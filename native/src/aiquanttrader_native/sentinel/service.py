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

from aiquanttrader_native.acceptance.audit import OperationalEvidenceLog
from aiquanttrader_native.acceptance.models import OperationalEventKind
from aiquanttrader_native.config.loader import ConfigBundle
from aiquanttrader_native.domain.execution import TradingHeartbeat
from aiquanttrader_native.execution.heartbeat import read_heartbeat
from aiquanttrader_native.execution.secrets import PrivateKey
from aiquanttrader_native.governance.models import (
    DeploymentAdmissionRecord,
    VerifiedDeploymentAdmission,
)
from aiquanttrader_native.sentinel.metrics import SentinelMetrics


class ControlClient(Protocol):
    def schedule_cancel(self, deadline_ms: int | None) -> None: ...

    def cancel_all(self) -> int: ...


class AdmissionGuard(Protocol):
    def active_record(self) -> DeploymentAdmissionRecord | None: ...


class HyperliquidControlClient:
    """SDK wrapper deliberately exposing no order-placement method."""

    def __init__(
        self,
        *,
        private_key: PrivateKey,
        base_url: str,
        account_address: str,
        timeout_seconds: int,
        vault_address: str | None = None,
    ) -> None:
        wallet = Account.from_key(private_key.reveal())
        normalized_url = base_url.rstrip("/")
        self._execution_account_address = vault_address or account_address
        self._info = Info(normalized_url, skip_ws=True, timeout=timeout_seconds)
        self._exchange = Exchange(
            wallet,
            base_url=normalized_url,
            account_address=account_address,
            vault_address=vault_address,
            timeout=timeout_seconds,
        )

    def schedule_cancel(self, deadline_ms: int | None) -> None:
        self._ensure_ok(self._exchange.schedule_cancel(deadline_ms), "scheduleCancel")

    def cancel_all(self) -> int:
        orders = self._info.open_orders(self._execution_account_address)
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
        admission: VerifiedDeploymentAdmission | None = None,
        admission_guard: AdmissionGuard | None = None,
        operational_log: OperationalEvidenceLog | None = None,
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
        self._admission = admission
        self._admission_guard = admission_guard
        self._operational_log = operational_log
        self._clock_ns = clock_ns
        self._last_renew_ns = 0
        self._last_emergency_cancel_ns: int | None = None
        self._last_health: bool | None = None
        self._last_deadman_schedule_success: bool | None = None
        self._last_emergency_cancel_success: bool | None = None

    def step(self) -> bool:
        """Perform one monitoring iteration and return whether the node is healthy."""

        now_ns = self._clock_ns()
        heartbeat = self._read_heartbeat()
        healthy, age_ns, admission_active = self._classify(heartbeat, now_ns)
        self._metrics.deployment_admission_active.set(1 if admission_active else 0)
        self._metrics.heartbeat_age_seconds.set(age_ns / 1_000_000_000)
        self._metrics.trading_node_healthy.set(1 if healthy else 0)
        if not healthy:
            repeat_ns = self._settings.sentinel.deadman_renew_interval_ms * 1_000_000
            if (
                self._last_emergency_cancel_ns is None
                or now_ns - self._last_emergency_cancel_ns >= repeat_ns
            ):
                last_error: Exception | None = None
                canceled_orders: int | None = None
                for _attempt in range(self._settings.sentinel.cancel_retry_count):
                    try:
                        canceled_orders = self._client.cancel_all()
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = exc
                if last_error is not None:
                    if self._last_emergency_cancel_success is not False:
                        self._record_operational(
                            kind=OperationalEventKind.SENTINEL_EMERGENCY_CANCEL,
                            success=False,
                            detail=(
                                "emergency cancel-all exhausted retries: "
                                f"{type(last_error).__name__}"
                            ),
                        )
                    self._last_emergency_cancel_success = False
                    self._metrics.emergency_cancels.labels(result="error").inc()
                    self._metrics.errors.labels(operation="cancel_all").inc()
                    raise last_error
                self._metrics.emergency_cancels.labels(result="success").inc()
                if self._last_emergency_cancel_success is not True:
                    self._record_operational(
                        kind=OperationalEventKind.SENTINEL_EMERGENCY_CANCEL,
                        success=True,
                        detail="control wallet confirmed emergency cancel-all",
                        order_count=canceled_orders,
                    )
                self._last_emergency_cancel_success = True
                self._last_emergency_cancel_ns = now_ns
            self._record_health_transition(healthy=False)
            return False

        self._last_emergency_cancel_ns = None
        self._last_emergency_cancel_success = None
        renew_interval_ns = self._settings.sentinel.deadman_renew_interval_ms * 1_000_000
        if now_ns - self._last_renew_ns >= renew_interval_ns:
            deadline_ms = (now_ns // 1_000_000) + self._settings.sentinel.deadman_timeout_ms
            try:
                self._client.schedule_cancel(deadline_ms)
            except Exception as exc:
                if self._last_deadman_schedule_success is not False:
                    self._record_operational(
                        kind=OperationalEventKind.DEADMAN_SCHEDULE,
                        success=False,
                        detail=f"dead-man schedule failed: {type(exc).__name__}",
                    )
                self._last_deadman_schedule_success = False
                self._metrics.errors.labels(operation="schedule_cancel").inc()
                raise
            if self._last_deadman_schedule_success is not True:
                self._record_operational(
                    kind=OperationalEventKind.DEADMAN_SCHEDULE,
                    success=True,
                    detail=f"exchange dead-man deadline scheduled for {deadline_ms}ms",
                )
            self._last_deadman_schedule_success = True
            self._last_renew_ns = now_ns
            self._metrics.deadman_deadline_seconds.set(deadline_ms / 1_000)
            self._metrics.deadman_renewals.inc()
        self._record_health_transition(healthy=True)
        return True

    def _read_heartbeat(self) -> TradingHeartbeat | None:
        try:
            return read_heartbeat(self._heartbeat_path)
        except (OSError, ValueError):
            return None

    def _classify(self, heartbeat: TradingHeartbeat | None, now_ns: int) -> tuple[bool, int, bool]:
        stale_ns = self._settings.sentinel.heartbeat_stale_after_ms * 1_000_000
        authorization = (
            None if self._admission_guard is None else self._admission_guard.active_record()
        )
        admission_healthy = self._admission_guard is None or authorization is not None
        if heartbeat is None:
            return False, stale_ns + 1, admission_healthy
        age_ns = max(0, now_ns - heartbeat.heartbeat_ts_ns)
        expected_account = self._settings.exchange.account_address
        expected_expiry = (
            None
            if self._admission is None
            else self._admission.approval.expires_at
            if authorization is None
            else authorization.expires_at
        )
        expected_expiry_ns = (
            None if expected_expiry is None else int(expected_expiry.timestamp() * 1_000_000_000)
        )
        identity_healthy = self._admission is None or (
            heartbeat.deployment_id == self._admission.approval.deployment_id
            and heartbeat.approval_id == self._admission.approval.approval_id
            and heartbeat.admission_id == self._admission.admission_id
            and heartbeat.approval_expires_ts_ns == expected_expiry_ns
        )
        healthy = (
            age_ns <= stale_ns
            and admission_healthy
            and identity_healthy
            and heartbeat.environment == self._settings.environment
            and heartbeat.account_address.lower() == str(expected_account).lower()
            and heartbeat.config_fingerprint == self._fingerprint
            and heartbeat.execution_healthy
            and heartbeat.reconciliation_complete
            and not heartbeat.operator_kill
        )
        return healthy, age_ns, admission_healthy

    def _record_operational(
        self,
        *,
        kind: OperationalEventKind,
        success: bool,
        detail: str,
        order_count: int | None = None,
    ) -> None:
        if self._operational_log is None:
            return
        self._operational_log.append(
            kind=kind,
            event_ts_ns=self._clock_ns(),
            success=success,
            detail=detail,
            order_count=order_count,
        )

    def _record_health_transition(self, *, healthy: bool) -> None:
        if healthy is self._last_health:
            return
        self._record_operational(
            kind=OperationalEventKind.HEARTBEAT_STATE,
            success=healthy,
            detail="trading heartbeat classified healthy"
            if healthy
            else "trading heartbeat classified unhealthy",
        )
        self._last_health = healthy
