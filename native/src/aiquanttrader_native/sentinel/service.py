"""Independent exchange dead-man renewal and emergency cancellation."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, cast

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils.signing import CancelRequest

from aiquanttrader_native.config.loader import ConfigBundle
from aiquanttrader_native.domain.execution import TradingHeartbeat
from aiquanttrader_native.execution.heartbeat import read_heartbeat
from aiquanttrader_native.execution.secrets import PrivateKey
from aiquanttrader_native.sentinel.metrics import SentinelMetrics


class ControlClient(Protocol):
    def schedule_cancel(self, deadline_ms: int | None) -> None: ...

    def cancel_all(self) -> int: ...


class HyperliquidControlClient:
    """SDK wrapper deliberately exposing no order-placement method."""

    def __init__(
        self,
        *,
        private_key: PrivateKey,
        base_url: str,
        account_address: str,
        timeout_seconds: int,
    ) -> None:
        wallet = Account.from_key(private_key.reveal())
        normalized_url = base_url.rstrip("/")
        self._account_address = account_address
        self._info = Info(normalized_url, skip_ws=True, timeout=timeout_seconds)
        self._exchange = Exchange(
            wallet,
            base_url=normalized_url,
            account_address=account_address,
            timeout=timeout_seconds,
        )

    def schedule_cancel(self, deadline_ms: int | None) -> None:
        self._ensure_ok(self._exchange.schedule_cancel(deadline_ms), "scheduleCancel")

    def cancel_all(self) -> int:
        orders = self._info.open_orders(self._account_address)
        if not isinstance(orders, list):
            raise RuntimeError("openOrders returned an unexpected response")
        cancels: list[CancelRequest] = []
        for order in orders:
            if not isinstance(order, dict):
                raise RuntimeError("openOrders contains a non-object entry")
            coin = order.get("coin")
            order_id = order.get("oid")
            if not isinstance(coin, str) or not isinstance(order_id, int):
                raise RuntimeError("openOrders entry is missing coin or numeric oid")
            cancels.append(cast(CancelRequest, {"coin": coin, "oid": order_id}))
        if cancels:
            self._ensure_ok(self._exchange.bulk_cancel(cancels), "cancel")
        return len(cancels)

    @staticmethod
    def _ensure_ok(response: Any, operation: str) -> None:
        if not isinstance(response, dict) or response.get("status") != "ok":
            raise RuntimeError(f"Hyperliquid {operation} failed without a successful response")


class SafetySentinel:
    """Renew the exchange timer only while an exact trading-node heartbeat is healthy."""

    def __init__(
        self,
        *,
        bundle: ConfigBundle,
        heartbeat_path: Path,
        client: ControlClient,
        metrics: SentinelMetrics,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        settings = bundle.settings
        if not settings.sentinel.enabled:
            raise ValueError("safety sentinel cannot run while disabled")
        if settings.exchange.account_address is None:
            raise ValueError("safety sentinel requires an account address")
        self._settings = settings
        self._fingerprint = bundle.fingerprint
        self._heartbeat_path = heartbeat_path
        self._client = client
        self._metrics = metrics
        self._clock_ns = clock_ns
        self._last_renew_ns = 0
        self._last_emergency_cancel_ns: int | None = None

    def step(self) -> bool:
        """Perform one monitoring iteration and return whether the node is healthy."""

        now_ns = self._clock_ns()
        heartbeat = self._read_heartbeat()
        healthy, age_ns = self._classify(heartbeat, now_ns)
        self._metrics.heartbeat_age_seconds.set(age_ns / 1_000_000_000)
        self._metrics.trading_node_healthy.set(1 if healthy else 0)
        if not healthy:
            repeat_ns = self._settings.sentinel.deadman_renew_interval_ms * 1_000_000
            if (
                self._last_emergency_cancel_ns is None
                or now_ns - self._last_emergency_cancel_ns >= repeat_ns
            ):
                last_error: Exception | None = None
                for _attempt in range(self._settings.sentinel.cancel_retry_count):
                    try:
                        self._client.cancel_all()
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = exc
                if last_error is not None:
                    self._metrics.emergency_cancels.labels(result="error").inc()
                    self._metrics.errors.labels(operation="cancel_all").inc()
                    raise last_error
                self._metrics.emergency_cancels.labels(result="success").inc()
                self._last_emergency_cancel_ns = now_ns
            return False

        self._last_emergency_cancel_ns = None
        renew_interval_ns = self._settings.sentinel.deadman_renew_interval_ms * 1_000_000
        if now_ns - self._last_renew_ns >= renew_interval_ns:
            deadline_ms = (now_ns // 1_000_000) + self._settings.sentinel.deadman_timeout_ms
            try:
                self._client.schedule_cancel(deadline_ms)
            except Exception:
                self._metrics.errors.labels(operation="schedule_cancel").inc()
                raise
            self._last_renew_ns = now_ns
            self._metrics.deadman_deadline_seconds.set(deadline_ms / 1_000)
            self._metrics.deadman_renewals.inc()
        return True

    def _read_heartbeat(self) -> TradingHeartbeat | None:
        try:
            return read_heartbeat(self._heartbeat_path)
        except (OSError, ValueError):
            return None

    def _classify(self, heartbeat: TradingHeartbeat | None, now_ns: int) -> tuple[bool, int]:
        stale_ns = self._settings.sentinel.heartbeat_stale_after_ms * 1_000_000
        if heartbeat is None:
            return False, stale_ns + 1
        age_ns = max(0, now_ns - heartbeat.heartbeat_ts_ns)
        expected_account = self._settings.exchange.account_address
        healthy = (
            age_ns <= stale_ns
            and heartbeat.environment == self._settings.environment
            and heartbeat.account_address.lower() == str(expected_account).lower()
            and heartbeat.config_fingerprint == self._fingerprint
            and heartbeat.execution_healthy
            and heartbeat.reconciliation_complete
            and not heartbeat.operator_kill
        )
        return healthy, age_ns
