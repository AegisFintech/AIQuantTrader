"""Versioned contracts for isolated shadow ingress, operations, and evidence."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from aiquanttrader.backtest.models import CalibrationState
from aiquanttrader.domain.base import DomainModel, canonical_sha256
from aiquanttrader.domain.market import (
    BboEvent,
    FundingEvent,
    IndexPriceEvent,
    L2BookSnapshot,
    MarketEvent,
    MarkPriceEvent,
    OpenInterestEvent,
    TradeEvent,
)
from aiquanttrader.features.models import VolatilityRegime
from aiquanttrader.paper.models import PaperAccountState

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]

PUBLIC_EVENT_TYPES = (
    L2BookSnapshot,
    BboEvent,
    TradeEvent,
    FundingEvent,
    OpenInterestEvent,
    MarkPriceEvent,
    IndexPriceEvent,
)


class ShadowIngressEnvelope(DomainModel):
    schema_version: Literal[1] = 1
    channel: Annotated[str, Field(min_length=1, max_length=64)]
    events: tuple[MarketEvent, ...] = ()
    is_control: bool = False
    receive_ts_ns: int = Field(ge=0)
    written_ts_ns: int = Field(ge=0)

    @model_validator(mode="after")
    def public_events_only(self) -> Self:
        if self.is_control and self.events:
            raise ValueError("control ingress cannot contain market events")
        if any(not isinstance(event, PUBLIC_EVENT_TYPES) for event in self.events):
            raise ValueError("shadow ingress accepts public BTC events only")
        if self.written_ts_ns + 60_000_000_000 < self.receive_ts_ns:
            raise ValueError("shadow ingress write time is unreasonably before receive time")
        return self


class ShadowGatewayStatus(DomainModel):
    schema_version: Literal[1] = 1
    status: Literal["starting", "ready", "reconnecting", "stopped", "failed"]
    heartbeat_ts_ns: int = Field(ge=0)
    last_ingress_sequence: int = Field(ge=0)
    last_receive_ts_ns: int | None = Field(default=None, ge=0)
    raw_first: Literal[True] = True
    credential_capability: Literal["none"] = "none"
    last_error_code: Identifier | None = None


class ShadowRuntimeStatus(DomainModel):
    schema_version: Literal[1] = 1
    status: Literal["starting", "warming", "ready", "degraded", "stopped", "failed"]
    run_id: Identifier
    heartbeat_ts_ns: int = Field(ge=0)
    last_public_data_ts_ns: int | None = Field(default=None, ge=0)
    last_ingress_sequence: int = Field(ge=0)
    ingress_lag_ns: int | None = Field(default=None, ge=0)
    feed_connected: bool
    feature_ready: bool
    operator_kill: bool
    strategy_id: Identifier
    scenario_id: Identifier
    scenario_sha256: Sha256
    calibration_state: CalibrationState
    config_fingerprint: Sha256
    image_identity: Annotated[str, Field(min_length=1, max_length=256)]
    credential_capability: Literal["none"] = "none"
    ip_network_capability: Literal["none"] = "none"
    command_sink: Literal["counterfactual_only"] = "counterfactual_only"
    account: PaperAccountState | None = None
    open_orders: int = Field(ge=0)
    decisions: int = Field(ge=0)
    commands: int = Field(ge=0)
    fills: int = Field(ge=0)
    last_error_code: Identifier | None = None


ShadowDrill = Literal[
    "host_reboot",
    "disk_pressure",
    "clock_degradation",
    "recorder_failure",
    "observability_failure",
    "operator_kill",
]


class ShadowEvidencePolicy(DomainModel):
    schema_version: Literal[1] = 1
    policy_id: Identifier
    frozen_at_ns: int = Field(ge=0)
    minimum_observation_ns: int = Field(gt=0)
    minimum_independent_decisions: int = Field(gt=0)
    minimum_fills: int = Field(gt=0)
    minimum_regimes: int = Field(default=3, ge=1, le=3)
    minimum_availability_fraction: Annotated[Decimal, Field(gt=0, le=1)]
    maximum_ingress_latency_p99_ms: Annotated[Decimal, Field(gt=0)]
    maximum_cycle_latency_p99_ms: Annotated[Decimal, Field(gt=0)]
    maximum_drawdown_fraction: Annotated[Decimal, Field(gt=0, le=1)]
    maximum_denial_fraction: Annotated[Decimal, Field(ge=0, le=1)]
    maximum_adverse_markout_bps: Annotated[Decimal, Field(ge=0)]
    maximum_feature_psi: Annotated[Decimal, Field(gt=0)]
    maximum_standardized_mean_shift: Annotated[Decimal, Field(gt=0)]
    minimum_determinism_decisions: int = Field(gt=0)
    require_positive_post_cost_pnl: bool = True
    require_calibrated_scenario: bool = True
    required_sensitivity_scenarios: tuple[Identifier, ...] = Field(min_length=1)
    required_drills: tuple[ShadowDrill, ...]

    @model_validator(mode="after")
    def unique_requirements(self) -> Self:
        if len(set(self.required_sensitivity_scenarios)) != len(
            self.required_sensitivity_scenarios
        ):
            raise ValueError("shadow sensitivity scenarios must be unique")
        if len(set(self.required_drills)) != len(self.required_drills):
            raise ValueError("shadow drills must be unique")
        return self


class ShadowDeterminismReport(DomainModel):
    schema_version: Literal[1] = 1
    report_id: Sha256
    source_run_id: Identifier
    replay_run_id: Identifier
    source_manifest_sha256: Sha256
    replay_manifest_sha256: Sha256
    compared_decisions: int = Field(ge=0)
    decision_mismatches: int = Field(ge=0)
    compared_commands: int = Field(ge=0)
    command_mismatches: int = Field(ge=0)
    generated_ts_ns: int = Field(ge=0)

    @model_validator(mode="after")
    def identity_matches(self) -> Self:
        identity = self.model_dump(mode="json", exclude={"report_id"})
        if canonical_sha256(identity) != self.report_id:
            raise ValueError("shadow determinism report identity does not match")
        return self


class ShadowGateResult(DomainModel):
    gate: Identifier
    passed: bool
    actual: str
    required: str


class ShadowEvidenceReport(DomainModel):
    schema_version: Literal[1] = 1
    report_id: Sha256
    run_id: Identifier
    run_manifest_sha256: Sha256
    generated_ts_ns: int = Field(ge=0)
    policy_id: Identifier
    policy_sha256: Sha256
    image_identity: Annotated[str, Field(min_length=1, max_length=256)]
    code_identity: Annotated[str, Field(min_length=1, max_length=128)]
    config_fingerprint: Sha256
    feature_config_sha256: Sha256
    strategy_config_sha256: Sha256
    engine_policy_sha256: Sha256
    scenario_id: Identifier
    scenario_sha256: Sha256
    calibration_state: CalibrationState
    observation_ns: int = Field(ge=0)
    independent_decisions: int = Field(ge=0)
    approved_decisions: int = Field(ge=0)
    denied_decisions: int = Field(ge=0)
    commands: int = Field(ge=0)
    submit_commands: int = Field(ge=0)
    fills: int = Field(ge=0)
    markouts: int = Field(ge=0)
    regimes: tuple[VolatilityRegime, ...]
    availability_fraction: Annotated[Decimal, Field(ge=0, le=1)]
    ingress_latency_p99_ms: NonNegativeDecimal
    cycle_latency_p99_ms: NonNegativeDecimal
    post_cost_pnl_usd: Decimal
    maximum_drawdown_fraction: Annotated[Decimal, Field(ge=0, le=1)]
    mean_adverse_markout_bps: Decimal
    maximum_feature_psi: NonNegativeDecimal
    maximum_standardized_mean_shift: NonNegativeDecimal
    determinism_report_id: Sha256 | None = None
    sensitivity_scenarios: tuple[Identifier, ...]
    completed_drills: tuple[Identifier, ...]
    invalidating_events: tuple[Identifier, ...]
    gates: tuple[ShadowGateResult, ...]
    awaiting_human_approval: bool

    @model_validator(mode="after")
    def identity_and_verdict_match(self) -> Self:
        identity = self.model_dump(mode="json", exclude={"report_id", "awaiting_human_approval"})
        if canonical_sha256(identity) != self.report_id:
            raise ValueError("shadow evidence report identity does not match")
        if self.awaiting_human_approval != all(gate.passed for gate in self.gates):
            raise ValueError("shadow evidence verdict does not match its gates")
        return self
