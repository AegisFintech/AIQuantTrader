"""Bounded synchronous pre-trade risk authority."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from decimal import Decimal

from aiquanttrader_native.config.models import ExecutionConfig, RiskLimits
from aiquanttrader_native.domain.base import canonical_json_bytes
from aiquanttrader_native.domain.execution import (
    OrderIntent,
    RiskDecision,
    RiskReason,
    RiskSnapshot,
    RiskState,
)
from aiquanttrader_native.domain.market import OrderSide
from aiquanttrader_native.risk.kill_switch import KillSwitchStore


class ApprovalError(ValueError):
    """Raised when an execution approval is invalid, expired, or already consumed."""


class RiskAuthority:
    """Evaluate every exposure-changing order and issue single-use approvals."""

    def __init__(
        self,
        limits: RiskLimits,
        execution: ExecutionConfig,
        *,
        kill_switch: KillSwitchStore,
        inflight_count: Callable[[], int],
        clock_ns: Callable[[], int] = time.time_ns,
        signing_key: bytes | None = None,
    ) -> None:
        self._limits = limits
        self._execution = execution
        self._kill_switch = kill_switch
        self._inflight_count = inflight_count
        self._clock_ns = clock_ns
        self._key = secrets.token_bytes(32) if signing_key is None else signing_key
        if len(self._key) < 32:
            raise ValueError("risk signing key must contain at least 32 bytes")
        self._limits_sha256 = limits_sha(limits)
        self._submissions_ns: deque[int] = deque()
        self._consumed: set[str] = set()
        self._lock = threading.RLock()

    def evaluate(self, intent: OrderIntent, snapshot: RiskSnapshot) -> RiskDecision:
        now = self._clock_ns()
        state, state_reasons = self._state(snapshot, now)
        reasons: list[RiskReason] = []
        reference_price = intent.limit_price or snapshot.mark_price
        notional = intent.quantity_base * reference_price
        signed_quantity = intent.quantity_base * (
            Decimal("1") if intent.side is OrderSide.BUY else Decimal("-1")
        )
        reducing = abs(snapshot.position_base + signed_quantity) < abs(snapshot.position_base)

        if intent.created_ts_ns > now:
            reasons.append(RiskReason.INVALID_TIMESTAMP)
        elif now - intent.created_ts_ns > self._execution.unknown_order_timeout_ms * 1_000_000:
            reasons.append(RiskReason.INTENT_TOO_OLD)
        if intent.quantity_base > self._limits.max_order_size_base:
            reasons.append(RiskReason.ORDER_SIZE_LIMIT)
        if notional > self._limits.max_order_notional_usd:
            reasons.append(RiskReason.ORDER_NOTIONAL_LIMIT)
        if intent.reduce_only and not reducing:
            reasons.append(RiskReason.NOT_POSITION_REDUCING)
        if self._inflight_count() >= self._execution.max_inflight_requests:
            reasons.append(RiskReason.INFLIGHT_REQUEST_LIMIT)

        worst_long = snapshot.position_base + snapshot.pending_buy_base
        worst_short = snapshot.position_base - snapshot.pending_sell_base
        if intent.side is OrderSide.BUY:
            worst_long += intent.quantity_base
        else:
            worst_short -= intent.quantity_base
        projected_base = max(abs(worst_long), abs(worst_short))
        projected_notional = projected_base * snapshot.mark_price

        if not intent.reduce_only:
            if projected_base > self._limits.max_position_size_base:
                reasons.append(RiskReason.POSITION_LIMIT)
            if projected_notional > self._limits.max_inventory_notional_usd:
                reasons.append(RiskReason.INVENTORY_LIMIT)
            if projected_notional / snapshot.account_equity_usd > self._limits.max_leverage:
                reasons.append(RiskReason.LEVERAGE_LIMIT)
            if snapshot.open_order_count >= self._limits.max_open_orders:
                reasons.append(RiskReason.OPEN_ORDER_LIMIT)
            if self._rate_limited(now):
                reasons.append(RiskReason.ORDER_RATE_LIMIT)

        if state in {RiskState.CANCEL_ONLY, RiskState.HALTED}:
            reasons.extend(state_reasons)
        elif state in {RiskState.REDUCE_ONLY, RiskState.FLATTENING}:
            if not intent.reduce_only:
                reasons.extend(state_reasons)
                reasons.append(RiskReason.REDUCE_ONLY_REQUIRED)
            elif not reducing:
                reasons.extend(state_reasons)
                reasons.append(RiskReason.NOT_POSITION_REDUCING)

        reasons = list(dict.fromkeys(reasons))
        allowed = not reasons
        if allowed:
            reasons = [RiskReason.APPROVED]
        decision_id = str(uuid.uuid4())
        issued = now
        expires = issued + self._execution.approval_ttl_ms * 1_000_000
        payload = {
            "decision_id": decision_id,
            "intent_sha256": intent.sha256(),
            "snapshot_sha256": snapshot.sha256(),
            "limits_sha256": self._limits_sha256,
            "state": state.value,
            "issued_ts_ns": issued,
            "expires_ts_ns": expires,
        }
        signature = self._sign(payload) if allowed else None
        return RiskDecision(
            decision_id=decision_id,
            intent_sha256=intent.sha256(),
            snapshot_sha256=snapshot.sha256(),
            limits_sha256=self._limits_sha256,
            state=state,
            allowed=allowed,
            reasons=tuple(reasons),
            issued_ts_ns=issued,
            expires_ts_ns=expires,
            approval_signature=signature,
        )

    def consume(self, decision: RiskDecision, intent: OrderIntent, snapshot: RiskSnapshot) -> None:
        now = self._clock_ns()
        if not decision.allowed or decision.approval_signature is None:
            raise ApprovalError("risk decision is not approved")
        if now > decision.expires_ts_ns:
            raise ApprovalError("risk approval has expired")
        current_state, _ = self._state(snapshot, now)
        if current_state is not decision.state:
            raise ApprovalError("risk state changed before dispatch")
        if self._inflight_count() >= self._execution.max_inflight_requests:
            raise ApprovalError("inflight request limit reached before dispatch")
        if (
            decision.intent_sha256 != intent.sha256()
            or decision.snapshot_sha256 != snapshot.sha256()
        ):
            raise ApprovalError("risk approval does not match intent and snapshot")
        if decision.limits_sha256 != self._limits_sha256:
            raise ApprovalError("risk approval was issued for different limits")
        payload = {
            "decision_id": decision.decision_id,
            "intent_sha256": decision.intent_sha256,
            "snapshot_sha256": decision.snapshot_sha256,
            "limits_sha256": decision.limits_sha256,
            "state": decision.state.value,
            "issued_ts_ns": decision.issued_ts_ns,
            "expires_ts_ns": decision.expires_ts_ns,
        }
        if not hmac.compare_digest(decision.approval_signature, self._sign(payload)):
            raise ApprovalError("risk approval signature is invalid")
        with self._lock:
            if decision.decision_id in self._consumed:
                raise ApprovalError("risk approval has already been consumed")
            if not intent.reduce_only and self._rate_limited(now):
                raise ApprovalError("order rate limit reached before dispatch")
            self._consumed.add(decision.decision_id)
            self._submissions_ns.append(now)

    def _state(self, snapshot: RiskSnapshot, now: int) -> tuple[RiskState, list[RiskReason]]:
        if self._kill_switch.read().active or snapshot.operator_kill:
            return RiskState.HALTED, [RiskReason.OPERATOR_KILL]
        if not snapshot.exchange_connected:
            return RiskState.CANCEL_ONLY, [RiskReason.EXCHANGE_DISCONNECTED]
        if not snapshot.reconciliation_complete:
            return RiskState.CANCEL_ONLY, [RiskReason.RECONCILIATION_INCOMPLETE]
        if any(
            timestamp > now
            for timestamp in (
                snapshot.snapshot_ts_ns,
                snapshot.public_data_ts_ns,
                snapshot.private_data_ts_ns,
            )
        ):
            return RiskState.CANCEL_ONLY, [RiskReason.INVALID_TIMESTAMP]
        stale_reasons: list[RiskReason] = []
        if now - snapshot.public_data_ts_ns > self._limits.public_data_stale_after_ms * 1_000_000:
            stale_reasons.append(RiskReason.PUBLIC_DATA_STALE)
        if now - snapshot.private_data_ts_ns > self._limits.private_data_stale_after_ms * 1_000_000:
            stale_reasons.append(RiskReason.PRIVATE_DATA_STALE)
        if stale_reasons:
            return RiskState.CANCEL_ONLY, stale_reasons
        if (
            snapshot.account_equity_usd <= 0
            or snapshot.day_start_equity_usd <= 0
            or snapshot.high_water_equity_usd <= 0
            or snapshot.leverage < 0
        ):
            return RiskState.HALTED, [RiskReason.INVALID_ACCOUNT_STATE]
        if snapshot.flatten_requested:
            return RiskState.FLATTENING, [RiskReason.FLATTEN_REQUESTED]
        economic_reasons: list[RiskReason] = []
        daily_loss = max(
            Decimal("0"),
            (snapshot.day_start_equity_usd - snapshot.account_equity_usd)
            / snapshot.day_start_equity_usd,
        )
        drawdown = max(
            Decimal("0"),
            (snapshot.high_water_equity_usd - snapshot.account_equity_usd)
            / snapshot.high_water_equity_usd,
        )
        if daily_loss >= self._limits.daily_loss_fraction:
            economic_reasons.append(RiskReason.DAILY_LOSS_LIMIT)
        if drawdown >= self._limits.max_drawdown_fraction:
            economic_reasons.append(RiskReason.DRAWDOWN_LIMIT)
        if snapshot.leverage > self._limits.max_leverage:
            economic_reasons.append(RiskReason.LEVERAGE_LIMIT)
        if economic_reasons:
            return RiskState.REDUCE_ONLY, economic_reasons
        return RiskState.ACTIVE, []

    def _rate_limited(self, now: int) -> bool:
        cutoff = now - 1_000_000_000
        with self._lock:
            while self._submissions_ns and self._submissions_ns[0] <= cutoff:
                self._submissions_ns.popleft()
            return len(self._submissions_ns) >= self._limits.max_orders_per_second

    def _sign(self, payload: Mapping[str, object]) -> str:
        return hmac.new(self._key, canonical_json_bytes(payload), hashlib.sha256).hexdigest()


def limits_sha(limits: RiskLimits) -> str:
    return hashlib.sha256(canonical_json_bytes(limits.model_dump(mode="json"))).hexdigest()
