"""Versioned contracts for deterministic paper execution and evidence."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from aiquanttrader.backtest.kernel import KernelDecision, StrategyAction
from aiquanttrader.backtest.models import CalibrationState
from aiquanttrader.domain.base import DomainModel, canonical_sha256
from aiquanttrader.domain.execution import (
    OrderIntent,
    RiskDecision,
    RiskReason,
    RiskState,
)
from aiquanttrader.domain.market import OrderSide
from aiquanttrader.features.market_structure import SmartMoneySnapshot
from aiquanttrader.features.models import VolatilityRegime
from aiquanttrader.paper.llm_models import LlmConfirmation

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
PositiveDecimal = Annotated[Decimal, Field(gt=0)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]


class PaperOrderState(StrEnum):
    PENDING_ACTIVATION = "pending_activation"
    RESTING = "resting"
    PARTIALLY_FILLED = "partially_filled"
    PENDING_CANCEL = "pending_cancel"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


TERMINAL_PAPER_ORDER_STATES = frozenset(
    {PaperOrderState.FILLED, PaperOrderState.CANCELED, PaperOrderState.REJECTED}
)


class PaperOrder(DomainModel):
    schema_version: Literal[1] = 1
    paper_order_id: Identifier
    intent: OrderIntent
    state: PaperOrderState
    accepted_ts_ns: int = Field(ge=0)
    effective_ts_ns: int = Field(ge=0)
    updated_ts_ns: int = Field(ge=0)
    filled_quantity_base: NonNegativeDecimal = Decimal("0")
    queue_ahead_base: NonNegativeDecimal = Decimal("0")
    cancel_effective_ts_ns: int | None = Field(default=None, ge=0)
    rejection_reason: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        if self.effective_ts_ns < self.accepted_ts_ns:
            raise ValueError("paper order cannot activate before acceptance")
        if self.updated_ts_ns < self.accepted_ts_ns:
            raise ValueError("paper order update cannot precede acceptance")
        if self.filled_quantity_base > self.intent.quantity_base:
            raise ValueError("paper order fills cannot exceed requested quantity")
        if self.state is PaperOrderState.FILLED and (
            self.filled_quantity_base != self.intent.quantity_base
        ):
            raise ValueError("filled paper order must have its full requested quantity")
        if self.state is PaperOrderState.REJECTED and self.rejection_reason is None:
            raise ValueError("rejected paper order requires a reason")
        if self.state is PaperOrderState.PENDING_CANCEL and self.cancel_effective_ts_ns is None:
            raise ValueError("pending paper cancel requires an effective time")
        return self

    @property
    def remaining_quantity_base(self) -> Decimal:
        return self.intent.quantity_base - self.filled_quantity_base


class PaperFill(DomainModel):
    schema_version: Literal[1] = 1
    fill_id: Identifier
    paper_order_id: Identifier
    intent_id: Identifier
    strategy_id: Identifier
    side: OrderSide
    quantity_base: PositiveDecimal
    price: PositiveDecimal
    fee_usd: Decimal
    maker: bool
    fill_ts_ns: int = Field(ge=0)
    decision_latency_ns: int = Field(ge=0)
    scenario_id: Identifier
    scenario_sha256: Sha256


class PaperAccountState(DomainModel):
    schema_version: Literal[1] = 1
    cash_usd: Decimal
    position_base: Decimal = Decimal("0")
    average_entry_price: PositiveDecimal | None = None
    mark_price: PositiveDecimal
    equity_usd: Decimal
    day_start_equity_usd: PositiveDecimal
    high_water_equity_usd: PositiveDecimal
    realized_trading_pnl_usd: Decimal = Decimal("0")
    fees_usd: Decimal = Decimal("0")
    funding_pnl_usd: Decimal = Decimal("0")
    last_funding_settlement_ns: int | None = Field(default=None, ge=0)
    utc_day: int = Field(ge=0)
    updated_ts_ns: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_account(self) -> Self:
        if self.position_base == 0 and self.average_entry_price is not None:
            raise ValueError("flat account cannot retain an average entry price")
        if self.position_base != 0 and self.average_entry_price is None:
            raise ValueError("non-flat account requires an average entry price")
        expected = self.cash_usd + self.position_base * self.mark_price
        if abs(expected - self.equity_usd) > Decimal("0.00000001"):
            raise ValueError("paper account equity does not reconcile to cash plus marked position")
        if self.high_water_equity_usd < self.equity_usd:
            raise ValueError("paper account high-water equity cannot trail current equity")
        return self

    @property
    def net_realized_pnl_usd(self) -> Decimal:
        return self.realized_trading_pnl_usd - self.fees_usd + self.funding_pnl_usd


class PaperDecisionRecord(DomainModel):
    schema_version: Literal[1] = 1
    record_id: Identifier
    sequence: int = Field(ge=0)
    decision_ts_ns: int = Field(ge=0)
    feature_snapshot_sha256: Sha256
    strategy_id: Identifier
    intent: OrderIntent
    risk_decision: RiskDecision
    independent: bool


class PaperForecastDiagnostics(DomainModel):
    """Bounded reactive-ensemble diagnostics captured at one causal decision."""

    schema_version: Literal[1] = 1
    training_samples: int = Field(ge=0)
    ready: bool
    directional_accuracy: Annotated[Decimal, Field(ge=0, le=1)]
    mean_absolute_error_bps: NonNegativeDecimal
    latest_prediction_bps: Decimal


class PaperStrategyEvaluation(DomainModel):
    """One strategy outcome, including blocked actions that emitted no intent."""

    schema_version: Literal[1] = 1
    evaluation_id: Sha256
    run_id: Identifier
    sequence: int = Field(ge=0)
    evaluated_ts_ns: int = Field(ge=0)
    feature_snapshot_sha256: Sha256
    strategy_id: Identifier
    feature_ready: bool
    structure_ready: bool
    feed_connected: bool
    risk_state: RiskState
    risk_reasons: tuple[RiskReason, ...] = ()
    decision: KernelDecision
    forecast: PaperForecastDiagnostics | None = None

    @model_validator(mode="after")
    def validate_risk_reasons(self) -> Self:
        if len(set(self.risk_reasons)) != len(self.risk_reasons):
            raise ValueError("paper strategy evaluation risk reasons must be unique")
        return self


class PaperStrategyActionCount(DomainModel):
    action: StrategyAction
    reason: Annotated[str, Field(min_length=1, max_length=256)]
    count: int = Field(gt=0)


class PaperStrategyEvaluationSummary(DomainModel):
    """Compact gate distribution and terminal model state for one paper run."""

    schema_version: Literal[1] = 1
    run_id: Identifier
    evaluations: int = Field(ge=0)
    feature_ready_evaluations: int = Field(ge=0)
    structure_ready_evaluations: int = Field(ge=0)
    feed_connected_evaluations: int = Field(ge=0)
    first_evaluated_ts_ns: int | None = Field(default=None, ge=0)
    last_evaluated_ts_ns: int | None = Field(default=None, ge=0)
    action_counts: tuple[PaperStrategyActionCount, ...] = ()
    latest_forecast: PaperForecastDiagnostics | None = None

    @model_validator(mode="after")
    def validate_counts_and_window(self) -> Self:
        bounded = (
            self.feature_ready_evaluations,
            self.structure_ready_evaluations,
            self.feed_connected_evaluations,
        )
        if any(value > self.evaluations for value in bounded):
            raise ValueError("paper strategy readiness count exceeds total evaluations")
        if sum(item.count for item in self.action_counts) != self.evaluations:
            raise ValueError("paper strategy action counts do not match total evaluations")
        if self.evaluations == 0:
            if self.first_evaluated_ts_ns is not None or self.last_evaluated_ts_ns is not None:
                raise ValueError("empty paper strategy summary cannot claim a time window")
        elif self.first_evaluated_ts_ns is None or self.last_evaluated_ts_ns is None:
            raise ValueError("non-empty paper strategy summary requires a time window")
        elif self.last_evaluated_ts_ns < self.first_evaluated_ts_ns:
            raise ValueError("paper strategy summary time window is reversed")
        return self


class PaperFeedBlockReason(StrEnum):
    NONE = "none"
    SOCKET_DISCONNECTED = "socket_disconnected"
    PUBLIC_FRAME_MISSING = "public_frame_missing"
    PUBLIC_FRAME_CLOCK_REGRESSION = "public_frame_clock_regression"
    PUBLIC_FRAME_STALE = "public_frame_stale"
    ASSET_CONTEXT_MISSING = "asset_context_missing"
    ASSET_CONTEXT_CLOCK_REGRESSION = "asset_context_clock_regression"
    ASSET_CONTEXT_STALE = "asset_context_stale"
    BBO_MISSING = "bbo_missing"
    BBO_CLOCK_REGRESSION = "bbo_clock_regression"
    BBO_STALE = "bbo_stale"


class PaperL2DepthState(StrEnum):
    MISSING = "missing"
    CLOCK_REGRESSION = "clock_regression"
    STALE = "stale"
    FRESH = "fresh"


class PaperFeedFreshness(DomainModel):
    """Executable-feed verdict plus independent full-depth validity evidence."""

    schema_version: Literal[2] = 2
    checked_ts_ns: int = Field(ge=0)
    stale_after_ms: int = Field(gt=0)
    depth_stale_after_ms: int = Field(gt=0)
    socket_connected: bool
    public_frame_age_ms: int | None = None
    asset_context_age_ms: int | None = None
    bbo_age_ms: int | None = None
    l2_depth_age_ms: int | None = None
    public_frame_fresh: bool
    asset_context_fresh: bool
    bbo_fresh: bool
    l2_depth_fresh: bool
    l2_depth_state: PaperL2DepthState
    ready: bool
    blocking_reason: PaperFeedBlockReason

    @staticmethod
    def _fresh(age_ms: int | None, stale_after_ms: int) -> bool:
        return age_ms is not None and 0 <= age_ms <= stale_after_ms

    @classmethod
    def _block_reason(
        cls,
        *,
        socket_connected: bool,
        frame_age_ms: int | None,
        context_age_ms: int | None,
        bbo_age_ms: int | None,
        stale_after_ms: int,
    ) -> PaperFeedBlockReason:
        if not socket_connected:
            return PaperFeedBlockReason.SOCKET_DISCONNECTED
        if frame_age_ms is None:
            return PaperFeedBlockReason.PUBLIC_FRAME_MISSING
        if frame_age_ms < 0:
            return PaperFeedBlockReason.PUBLIC_FRAME_CLOCK_REGRESSION
        if not cls._fresh(frame_age_ms, stale_after_ms):
            return PaperFeedBlockReason.PUBLIC_FRAME_STALE
        if context_age_ms is None:
            return PaperFeedBlockReason.ASSET_CONTEXT_MISSING
        if context_age_ms < 0:
            return PaperFeedBlockReason.ASSET_CONTEXT_CLOCK_REGRESSION
        if not cls._fresh(context_age_ms, stale_after_ms):
            return PaperFeedBlockReason.ASSET_CONTEXT_STALE
        if bbo_age_ms is None:
            return PaperFeedBlockReason.BBO_MISSING
        if bbo_age_ms < 0:
            return PaperFeedBlockReason.BBO_CLOCK_REGRESSION
        if not cls._fresh(bbo_age_ms, stale_after_ms):
            return PaperFeedBlockReason.BBO_STALE
        return PaperFeedBlockReason.NONE

    @classmethod
    def _depth_state(cls, age_ms: int | None, depth_stale_after_ms: int) -> PaperL2DepthState:
        if age_ms is None:
            return PaperL2DepthState.MISSING
        if age_ms < 0:
            return PaperL2DepthState.CLOCK_REGRESSION
        if not cls._fresh(age_ms, depth_stale_after_ms):
            return PaperL2DepthState.STALE
        return PaperL2DepthState.FRESH

    @classmethod
    def from_observations(
        cls,
        *,
        checked_ts_ns: int,
        stale_after_ms: int,
        depth_stale_after_ms: int,
        socket_connected: bool,
        last_public_frame_wall_ns: int | None,
        last_asset_context_wall_ns: int | None,
        last_bbo_wall_ns: int | None,
        last_l2_depth_wall_ns: int | None,
    ) -> Self:
        if stale_after_ms <= 0 or depth_stale_after_ms <= 0:
            raise ValueError("paper feed and depth stale thresholds must be positive")

        def age_ms(observed_ns: int | None) -> int | None:
            if observed_ns is None:
                return None
            return (checked_ts_ns - observed_ns) // 1_000_000

        frame_age = age_ms(last_public_frame_wall_ns)
        context_age = age_ms(last_asset_context_wall_ns)
        bbo_age = age_ms(last_bbo_wall_ns)
        depth_age = age_ms(last_l2_depth_wall_ns)
        frame_fresh = cls._fresh(frame_age, stale_after_ms)
        context_fresh = cls._fresh(context_age, stale_after_ms)
        bbo_fresh = cls._fresh(bbo_age, stale_after_ms)
        depth_state = cls._depth_state(depth_age, depth_stale_after_ms)
        reason = cls._block_reason(
            socket_connected=socket_connected,
            frame_age_ms=frame_age,
            context_age_ms=context_age,
            bbo_age_ms=bbo_age,
            stale_after_ms=stale_after_ms,
        )
        return cls(
            checked_ts_ns=checked_ts_ns,
            stale_after_ms=stale_after_ms,
            depth_stale_after_ms=depth_stale_after_ms,
            socket_connected=socket_connected,
            public_frame_age_ms=frame_age,
            asset_context_age_ms=context_age,
            bbo_age_ms=bbo_age,
            l2_depth_age_ms=depth_age,
            public_frame_fresh=frame_fresh,
            asset_context_fresh=context_fresh,
            bbo_fresh=bbo_fresh,
            l2_depth_fresh=depth_state is PaperL2DepthState.FRESH,
            l2_depth_state=depth_state,
            ready=reason is PaperFeedBlockReason.NONE,
            blocking_reason=reason,
        )

    @model_validator(mode="after")
    def validate_verdict(self) -> Self:
        expected_freshness = (
            self._fresh(self.public_frame_age_ms, self.stale_after_ms),
            self._fresh(self.asset_context_age_ms, self.stale_after_ms),
            self._fresh(self.bbo_age_ms, self.stale_after_ms),
            self._fresh(self.l2_depth_age_ms, self.depth_stale_after_ms),
        )
        actual_freshness = (
            self.public_frame_fresh,
            self.asset_context_fresh,
            self.bbo_fresh,
            self.l2_depth_fresh,
        )
        if actual_freshness != expected_freshness:
            raise ValueError("paper feed component freshness does not match its age")
        expected_depth_state = type(self)._depth_state(
            self.l2_depth_age_ms, self.depth_stale_after_ms
        )
        if self.l2_depth_state is not expected_depth_state:
            raise ValueError("paper L2 depth state does not match its age")
        expected_reason = type(self)._block_reason(
            socket_connected=self.socket_connected,
            frame_age_ms=self.public_frame_age_ms,
            context_age_ms=self.asset_context_age_ms,
            bbo_age_ms=self.bbo_age_ms,
            stale_after_ms=self.stale_after_ms,
        )
        if self.ready != (expected_reason is PaperFeedBlockReason.NONE):
            raise ValueError("paper feed readiness does not match its blocking reason")
        if self.blocking_reason is not expected_reason:
            raise ValueError("paper feed readiness does not match its blocking reason")
        return self


class PaperCommandKind(StrEnum):
    SUBMIT = "submit"
    CANCEL = "cancel"
    CANCEL_ALL = "cancel_all"


class PaperExecutionCommand(DomainModel):
    """Exact approved command captured before the non-exchange execution sink."""

    schema_version: Literal[1] = 1
    command_id: Identifier
    sequence: int = Field(ge=0)
    command_ts_ns: int = Field(ge=0)
    kind: PaperCommandKind
    intent_id: Identifier
    strategy_id: Identifier
    intent: OrderIntent | None = None
    risk_decision_id: Identifier | None = None
    feature_snapshot_sha256: Sha256 | None = None
    source_sequence: int | None = Field(default=None, ge=1)
    sink: Literal["counterfactual_only"] = "counterfactual_only"

    @model_validator(mode="after")
    def validate_command(self) -> Self:
        if self.kind is PaperCommandKind.SUBMIT:
            if self.intent is None or self.risk_decision_id is None:
                raise ValueError("submit command requires its exact intent and risk approval")
            if self.intent.intent_id != self.intent_id:
                raise ValueError("submit command intent identity does not match")
        elif self.intent is not None or self.risk_decision_id is not None:
            raise ValueError("cancel commands cannot claim a submit intent or risk approval")
        return self


class PaperRunManifest(DomainModel):
    schema_version: Literal[1] = 1
    run_id: Identifier
    environment: Identifier
    started_ts_ns: int = Field(ge=0)
    code_identity: Annotated[str, Field(min_length=1, max_length=128)]
    config_fingerprint: Sha256
    feature_config_sha256: Sha256
    strategy_config_sha256: Sha256
    scenario_id: Identifier
    scenario_sha256: Sha256
    evidence_policy_sha256: Sha256
    strategy_id: Identifier
    credential_capability: Literal["none"] = "none"
    image_identity: Annotated[str, Field(min_length=1, max_length=256)] | None = None
    source_start_sequence: int = Field(default=0, ge=0)


class PaperEngineCheckpoint(DomainModel):
    schema_version: Literal[1] = 1
    run_id: Identifier
    sequence: int = Field(ge=0)
    checkpoint_ts_ns: int = Field(ge=0)
    strategy_id: Identifier
    strategy_memory_json: Annotated[str, Field(min_length=2, max_length=65_536)]
    last_independent_decision_ts_ns: int | None = Field(default=None, ge=0)
    funding_rate: Decimal = Decimal("0")
    next_funding_settlement_ns: int | None = Field(default=None, ge=0)
    source_sequence: int | None = Field(default=None, ge=1)


class PaperEvidencePolicy(DomainModel):
    schema_version: Literal[1] = 1
    policy_id: Identifier
    frozen_at_ns: int = Field(ge=0)
    minimum_observation_ns: int = Field(gt=0)
    minimum_independent_decisions: int = Field(gt=0)
    decision_independence_ns: int = Field(gt=0)
    minimum_fills: int = Field(gt=0)
    minimum_regimes: int = Field(default=3, ge=1, le=3)
    maximum_drawdown_fraction: Annotated[Decimal, Field(gt=0, le=1)]
    maximum_denial_fraction: Annotated[Decimal, Field(ge=0, le=1)]
    maximum_adverse_markout_bps: Annotated[Decimal, Field(ge=0)]
    drift_baseline_samples: int = Field(default=1_000, ge=20)
    drift_window_samples: int = Field(default=1_000, ge=20)
    drift_evaluation_interval_samples: int = Field(default=100, ge=1)
    maximum_feature_psi: Annotated[Decimal, Field(gt=0)] = Decimal("0.2")
    maximum_standardized_mean_shift: Annotated[Decimal, Field(gt=0)] = Decimal("1")
    require_positive_post_cost_pnl: bool = True
    required_sensitivity_scenarios: tuple[Identifier, ...] = Field(min_length=1)
    required_drills: tuple[
        Literal[
            "restart",
            "stale_data",
            "daily_loss",
            "drawdown",
            "operator_kill",
            "observability",
        ],
        ...,
    ] = (
        "restart",
        "stale_data",
        "daily_loss",
        "drawdown",
        "operator_kill",
        "observability",
    )

    @model_validator(mode="after")
    def unique_requirements(self) -> Self:
        if len(set(self.required_sensitivity_scenarios)) != len(
            self.required_sensitivity_scenarios
        ):
            raise ValueError("required sensitivity scenarios must be unique")
        if len(set(self.required_drills)) != len(self.required_drills):
            raise ValueError("required paper drills must be unique")
        return self


class PaperGateResult(DomainModel):
    gate: Identifier
    passed: bool
    actual: str
    required: str


class PaperEvidenceReport(DomainModel):
    schema_version: Literal[1] = 1
    report_id: Sha256
    run_id: Identifier
    run_manifest_sha256: Sha256
    generated_ts_ns: int = Field(ge=0)
    policy_id: Identifier
    policy_sha256: Sha256
    code_identity: Annotated[str, Field(min_length=1, max_length=128)]
    config_fingerprint: Sha256
    feature_config_sha256: Sha256
    strategy_config_sha256: Sha256
    strategy_id: Identifier
    scenario_id: Identifier
    scenario_sha256: Sha256
    calibration_state: CalibrationState
    observation_started_ts_ns: int = Field(ge=0)
    observation_ended_ts_ns: int = Field(ge=0)
    observation_ns: int = Field(ge=0)
    independent_decisions: int = Field(ge=0)
    approved_decisions: int = Field(ge=0)
    denied_decisions: int = Field(ge=0)
    fills: int = Field(ge=0)
    markouts: int = Field(ge=0)
    ending_position_base: Decimal
    open_orders: int = Field(ge=0)
    regimes: tuple[VolatilityRegime, ...]
    post_cost_pnl_usd: Decimal
    maximum_drawdown_fraction: Annotated[Decimal, Field(ge=0, le=1)]
    mean_adverse_markout_bps: Decimal
    drift_evaluated: bool
    maximum_feature_psi: NonNegativeDecimal
    maximum_standardized_mean_shift: NonNegativeDecimal
    sensitivity_scenarios: tuple[Identifier, ...]
    completed_drills: tuple[Identifier, ...]
    invalidating_events: tuple[Identifier, ...]
    gates: tuple[PaperGateResult, ...]
    promotion_eligible: bool

    @model_validator(mode="after")
    def validate_report_identity_and_verdict(self) -> Self:
        identity = {
            "run_id": self.run_id,
            "run_manifest_sha256": self.run_manifest_sha256,
            "generated_ts_ns": self.generated_ts_ns,
            "policy_sha256": self.policy_sha256,
            "scenario_sha256": self.scenario_sha256,
            "observation_started_ts_ns": self.observation_started_ts_ns,
            "observation_ended_ts_ns": self.observation_ended_ts_ns,
            "observation_ns": self.observation_ns,
            "independent_decisions": self.independent_decisions,
            "approved_decisions": self.approved_decisions,
            "denied_decisions": self.denied_decisions,
            "fills": self.fills,
            "markouts": self.markouts,
            "ending_position_base": str(self.ending_position_base),
            "open_orders": self.open_orders,
            "regimes": [regime.value for regime in self.regimes],
            "post_cost_pnl_usd": str(self.post_cost_pnl_usd),
            "maximum_drawdown_fraction": str(self.maximum_drawdown_fraction),
            "mean_adverse_markout_bps": str(self.mean_adverse_markout_bps),
            "drift_evaluated": self.drift_evaluated,
            "maximum_feature_psi": str(self.maximum_feature_psi),
            "maximum_standardized_mean_shift": str(self.maximum_standardized_mean_shift),
            "sensitivity_scenarios": list(self.sensitivity_scenarios),
            "completed_drills": list(self.completed_drills),
            "invalidating_events": list(self.invalidating_events),
            "gates": [gate.model_dump(mode="json") for gate in self.gates],
        }
        if self.observation_ended_ts_ns < self.observation_started_ts_ns:
            raise ValueError("paper evidence observation window is reversed")
        if self.observation_ended_ts_ns - self.observation_started_ts_ns != self.observation_ns:
            raise ValueError("paper evidence observation duration does not match its window")
        if canonical_sha256(identity) != self.report_id:
            raise ValueError("paper evidence report identity does not match its contents")
        if self.promotion_eligible != all(gate.passed for gate in self.gates):
            raise ValueError("paper promotion verdict does not match its gates")
        return self


class PaperRuntimeStatus(DomainModel):
    schema_version: Literal[3] = 3
    status: Literal["starting", "warming", "ready", "degraded", "stopped", "failed"]
    run_id: Identifier
    environment: Identifier
    heartbeat_ts_ns: int = Field(ge=0)
    last_public_data_ts_ns: int | None = Field(default=None, ge=0)
    feed_connected: bool
    feed_freshness: PaperFeedFreshness
    feature_ready: bool
    operator_kill: bool
    scenario_id: Identifier
    scenario_sha256: Sha256
    calibration_state: CalibrationState
    strategy_id: Identifier
    config_fingerprint: Sha256
    account: PaperAccountState | None = None
    open_orders: int = Field(ge=0)
    decisions: int = Field(ge=0)
    fills: int = Field(ge=0)
    strategy_decision: KernelDecision | None = None
    market_structure: SmartMoneySnapshot | None = None
    llm_confirmation_enabled: bool = False
    latest_llm_confirmation: LlmConfirmation | None = None
    llm_last_error_code: Identifier | None = None
    last_error_code: Identifier | None = None

    @model_validator(mode="after")
    def validate_feed_projection(self) -> Self:
        if self.feed_connected != self.feed_freshness.ready:
            raise ValueError("paper feed projection does not match freshness evidence")
        if self.heartbeat_ts_ns != self.feed_freshness.checked_ts_ns:
            raise ValueError("paper heartbeat and feed freshness timestamps must match")
        return self


class PaperMarkout(DomainModel):
    schema_version: Literal[1] = 1
    fill_id: Identifier
    horizon_ns: int = Field(gt=0)
    observed_ts_ns: int = Field(ge=0)
    mark_price: PositiveDecimal
    signed_markout_bps: Decimal
