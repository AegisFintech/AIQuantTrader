"""Deterministic JSON Schema export for versioned native contracts."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from aiquanttrader_native.acceptance.models import (
    EvidenceArtifactBinding,
    OperationalEvidenceEvent,
    TestnetAcceptanceRunManifest,
    TestnetFinalVenueState,
    TestnetOperationalFacts,
    TestnetScenarioEvidence,
)
from aiquanttrader_native.backtest.models import (
    BacktestDatasetManifest,
    ExecutionScenario,
    ReplayResult,
    SelectionReceipt,
    ValidationPlan,
    ValidationPolicy,
)
from aiquanttrader_native.domain.data import (
    DatasetManifest,
    NormalizedSegmentManifest,
    RawSegmentManifest,
    RecorderState,
    TardisFileManifest,
)
from aiquanttrader_native.domain.execution import (
    ExecutionJournalEvent,
    OrderIntent,
    RiskDecision,
    RiskSnapshot,
    TradingHeartbeat,
)
from aiquanttrader_native.domain.features import FeatureSnapshot
from aiquanttrader_native.domain.governance import DeploymentApproval, ExperimentManifest
from aiquanttrader_native.domain.market import DataCapabilities, MarketEvent
from aiquanttrader_native.execution.live import EquityBaseline
from aiquanttrader_native.features.models import (
    FeatureDatasetManifest,
    FeatureEngineConfig,
    FeatureSchema,
    MicrostructureSnapshot,
)
from aiquanttrader_native.governance.models import (
    CanaryEvidencePolicy,
    CanaryEvidenceReport,
    CanaryObservation,
    DeploymentAdmissionRecord,
    DeploymentArtifactManifest,
    DeploymentAuthorizationRenewal,
    DeploymentModelSelection,
    DetachedApprovalSignature,
    ReleaseBundleReceipt,
    ReleaseBundleSpec,
    TestnetDressRehearsalObservation,
    TestnetDressRehearsalPolicy,
    TestnetDressRehearsalReport,
    VerifiedDeploymentAdmission,
    VerifiedDeploymentRenewal,
)
from aiquanttrader_native.paper.models import (
    PaperAccountState,
    PaperDecisionRecord,
    PaperEngineCheckpoint,
    PaperEvidencePolicy,
    PaperEvidenceReport,
    PaperExecutionCommand,
    PaperFill,
    PaperMarkout,
    PaperOrder,
    PaperRunManifest,
    PaperRuntimeStatus,
)
from aiquanttrader_native.research.models import (
    ChampionChallengerReport,
    DriftReport,
    ModelArtifactManifest,
    NegativeControlReport,
    NoSignalControlReport,
    PromotionMetrics,
    PromotionPolicy,
    ResearchExperimentManifest,
    SearchPolicy,
    SearchReceipt,
)
from aiquanttrader_native.retirement.models import (
    DisabledCredentialScanEvidence,
    DisabledEvidenceManifest,
    DisabledObservation,
    DisabledObservationReport,
    LegacyArchiveCredentialScanEvidence,
    LegacyArchiveCredentialScanPolicy,
    LegacyArchiveEvidenceManifest,
    LegacyArchiveManifest,
    LegacyArchiveRestoreEvidence,
    LegacyBrokerAccountStateEvidence,
    LegacyBrokerOrderAuditEvidence,
    LegacyCapabilityAuditEvidence,
    LegacyCleanupManifest,
    LegacyCredentialQuarantineEvidence,
    LegacyFinalState,
    LegacyFinalTagEvidence,
    LegacyFinalTradeReportEvidence,
    LegacyServiceConfigurationEvidence,
    LegacyStopExecutionEvidence,
    NativeDisabledWindowEvidence,
    NativeDrillEvidence,
    NativeProductionObservation,
    ProductionEvidenceManifest,
    ProductionIncidentRegister,
    RetirementActionApproval,
    RetirementApprovalSignature,
    RetirementPolicy,
    RetirementReadinessObservation,
    RetirementReadinessReport,
    VerifiedRetirementApproval,
)
from aiquanttrader_native.shadow.models import (
    ShadowDeterminismReport,
    ShadowEvidencePolicy,
    ShadowEvidenceReport,
    ShadowGatewayStatus,
    ShadowIngressEnvelope,
    ShadowRuntimeStatus,
)

SchemaFactory = Callable[[], dict[str, Any]]


def _model_schema(
    model: type[
        DatasetManifest
        | DeploymentApproval
        | ExperimentManifest
        | FeatureSnapshot
        | NormalizedSegmentManifest
        | RawSegmentManifest
        | RecorderState
        | TardisFileManifest
    ],
) -> dict[str, Any]:
    return model.model_json_schema()


SCHEMAS: dict[str, SchemaFactory] = {
    "acceptance.schema.json": lambda: TypeAdapter(
        EvidenceArtifactBinding
        | OperationalEvidenceEvent
        | TestnetAcceptanceRunManifest
        | TestnetFinalVenueState
        | TestnetOperationalFacts
        | TestnetScenarioEvidence
    ).json_schema(),
    "backtest.schema.json": lambda: TypeAdapter(
        BacktestDatasetManifest
        | ExecutionScenario
        | ReplayResult
        | SelectionReceipt
        | ValidationPlan
        | ValidationPolicy
    ).json_schema(),
    "data-capabilities.schema.json": DataCapabilities.model_json_schema,
    "deployment-approval.schema.json": lambda: _model_schema(DeploymentApproval),
    "experiment.schema.json": lambda: _model_schema(ExperimentManifest),
    "execution.schema.json": lambda: TypeAdapter(
        OrderIntent
        | RiskSnapshot
        | RiskDecision
        | ExecutionJournalEvent
        | TradingHeartbeat
        | EquityBaseline
    ).json_schema(),
    "features.schema.json": lambda: TypeAdapter(
        FeatureSnapshot
        | FeatureEngineConfig
        | FeatureSchema
        | MicrostructureSnapshot
        | FeatureDatasetManifest
    ).json_schema(),
    "governance.schema.json": lambda: TypeAdapter(
        DeploymentArtifactManifest
        | DeploymentAuthorizationRenewal
        | DetachedApprovalSignature
        | VerifiedDeploymentAdmission
        | VerifiedDeploymentRenewal
        | DeploymentAdmissionRecord
        | CanaryEvidencePolicy
        | CanaryObservation
        | CanaryEvidenceReport
        | TestnetDressRehearsalPolicy
        | TestnetDressRehearsalObservation
        | TestnetDressRehearsalReport
        | DeploymentModelSelection
        | ReleaseBundleSpec
        | ReleaseBundleReceipt
    ).json_schema(),
    "market-data.schema.json": lambda: TypeAdapter(MarketEvent).json_schema(),
    "dataset-manifest.schema.json": lambda: _model_schema(DatasetManifest),
    "normalized-segment-manifest.schema.json": lambda: _model_schema(NormalizedSegmentManifest),
    "paper.schema.json": lambda: TypeAdapter(
        PaperOrder
        | PaperFill
        | PaperAccountState
        | PaperDecisionRecord
        | PaperRunManifest
        | PaperEngineCheckpoint
        | PaperEvidencePolicy
        | PaperEvidenceReport
        | PaperRuntimeStatus
        | PaperMarkout
        | PaperExecutionCommand
    ).json_schema(),
    "raw-segment-manifest.schema.json": lambda: _model_schema(RawSegmentManifest),
    "recorder-state.schema.json": lambda: _model_schema(RecorderState),
    "research.schema.json": lambda: TypeAdapter(
        ModelArtifactManifest
        | SearchPolicy
        | SearchReceipt
        | NegativeControlReport
        | NoSignalControlReport
        | PromotionMetrics
        | PromotionPolicy
        | ChampionChallengerReport
        | DriftReport
        | ResearchExperimentManifest
    ).json_schema(),
    "retirement.schema.json": lambda: TypeAdapter(
        LegacyArchiveManifest
        | LegacyArchiveEvidenceManifest
        | LegacyArchiveRestoreEvidence
        | LegacyArchiveCredentialScanPolicy
        | LegacyArchiveCredentialScanEvidence
        | LegacyFinalTagEvidence
        | LegacyFinalTradeReportEvidence
        | LegacyBrokerAccountStateEvidence
        | LegacyServiceConfigurationEvidence
        | LegacyFinalState
        | LegacyCleanupManifest
        | ProductionEvidenceManifest
        | ProductionIncidentRegister
        | NativeDrillEvidence
        | NativeProductionObservation
        | RetirementPolicy
        | RetirementReadinessObservation
        | RetirementReadinessReport
        | DisabledObservation
        | DisabledObservationReport
        | DisabledEvidenceManifest
        | DisabledCredentialScanEvidence
        | LegacyStopExecutionEvidence
        | LegacyCapabilityAuditEvidence
        | LegacyBrokerOrderAuditEvidence
        | LegacyCredentialQuarantineEvidence
        | NativeDisabledWindowEvidence
        | RetirementActionApproval
        | RetirementApprovalSignature
        | VerifiedRetirementApproval
    ).json_schema(),
    "shadow.schema.json": lambda: TypeAdapter(
        ShadowIngressEnvelope
        | ShadowGatewayStatus
        | ShadowRuntimeStatus
        | ShadowEvidencePolicy
        | ShadowDeterminismReport
        | ShadowEvidenceReport
    ).json_schema(),
    "tardis-file-manifest.schema.json": lambda: _model_schema(TardisFileManifest),
}


def render_schemas() -> dict[str, str]:
    """Render every checked-in schema with stable formatting."""

    return {
        filename: json.dumps(factory(), indent=2, sort_keys=True, ensure_ascii=True) + "\n"
        for filename, factory in sorted(SCHEMAS.items())
    }


def export_schemas(output: Path, *, check: bool) -> tuple[Path, ...]:
    """Write schemas, or verify the checked-in files byte-for-byte."""

    rendered = render_schemas()
    expected_paths = tuple(output / name for name in rendered)
    if check:
        failures: list[str] = []
        for path, content in zip(expected_paths, rendered.values(), strict=True):
            if not path.is_file():
                failures.append(f"missing schema: {path}")
            elif path.read_text(encoding="utf-8") != content:
                failures.append(f"stale schema: {path}")
        unexpected = sorted(
            path for path in output.glob("*.schema.json") if path not in expected_paths
        )
        failures.extend(f"unexpected schema: {path}" for path in unexpected)
        if failures:
            raise ValueError("; ".join(failures))
        return expected_paths

    output.mkdir(parents=True, exist_ok=True)
    for path, content in zip(expected_paths, rendered.values(), strict=True):
        path.write_text(content, encoding="utf-8")
    return expected_paths
