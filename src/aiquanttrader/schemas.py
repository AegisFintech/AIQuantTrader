"""Deterministic JSON Schema export for versioned native contracts."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from aiquanttrader.acceptance.models import (
    EvidenceArtifactBinding,
    OperationalEvidenceEvent,
    TestnetAcceptanceRunManifest,
    TestnetFinalVenueState,
    TestnetOperationalFacts,
    TestnetScenarioEvidence,
)
from aiquanttrader.backtest.models import (
    BacktestDatasetManifest,
    ExecutionScenario,
    ReplayResult,
    SelectionReceipt,
    ValidationPlan,
    ValidationPolicy,
)
from aiquanttrader.domain.data import (
    DatasetManifest,
    MarketDataRecorderMetricsSnapshot,
    MarketDataSoakPolicy,
    MarketDataSoakReport,
    NormalizedSegmentManifest,
    NormalizerState,
    RawSegmentManifest,
    RecorderState,
    TardisFileManifest,
)
from aiquanttrader.domain.execution import (
    ExecutionJournalEvent,
    OrderIntent,
    RiskDecision,
    RiskSnapshot,
    TradingHeartbeat,
)
from aiquanttrader.domain.features import FeatureSnapshot
from aiquanttrader.domain.governance import DeploymentApproval, ExperimentManifest
from aiquanttrader.domain.market import DataCapabilities, MarketEvent
from aiquanttrader.execution.live import EquityBaseline
from aiquanttrader.features.market_structure import (
    CausalCandle,
    CausalStructureState,
    SmartMoneySnapshot,
)
from aiquanttrader.features.models import (
    FeatureDatasetManifest,
    FeatureEngineConfig,
    FeatureSchema,
    MicrostructureSnapshot,
)
from aiquanttrader.governance.models import (
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
from aiquanttrader.paper.llm_models import (
    LlmAssessment,
    LlmConfirmation,
    LlmConfirmationRequest,
)
from aiquanttrader.paper.models import (
    PaperAccountState,
    PaperDecisionRecord,
    PaperEngineCheckpoint,
    PaperEvidencePolicy,
    PaperEvidenceReport,
    PaperExecutionCommand,
    PaperFill,
    PaperForecastDiagnostics,
    PaperMarkout,
    PaperOrder,
    PaperRunManifest,
    PaperRuntimeStatus,
    PaperStrategyActionCount,
    PaperStrategyEvaluation,
    PaperStrategyEvaluationSummary,
)
from aiquanttrader.research.models import (
    ChampionChallengerReport,
    DriftReport,
    ForecastEconomicReport,
    ForecastMatrixManifest,
    ForecastRobustnessReport,
    HorizonFamilyFeasibilityReport,
    HorizonFamilyPolicy,
    ModelArtifactManifest,
    NegativeControlReport,
    NoSignalControlReport,
    PromotionMetrics,
    PromotionPolicy,
    ResearchControlPolicy,
    ResearchExperimentManifest,
    SearchPolicy,
    SearchReceipt,
    TargetFeasibilityReport,
)
from aiquanttrader.research.readiness_models import (
    ResearchDataReadinessPolicy,
    ResearchDataReadinessReport,
    ResearchDataReadinessState,
)
from aiquanttrader.retirement.models import (
    CleanupActionPlan,
    CleanupCompletionReport,
    CleanupCredentialScanEvidence,
    CleanupEvidenceManifest,
    CleanupHostAbsenceEvidence,
    CleanupInventoryAuditEvidence,
    CleanupOperatorLedger,
    CleanupOutcomeEvidenceManifest,
    CleanupPathAbsenceEvidence,
    CleanupPreflightReceipt,
    CleanupTargetEvidence,
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
from aiquanttrader.shadow.models import (
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
        | NormalizerState
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
    "market-data-soak.schema.json": lambda: TypeAdapter(
        MarketDataSoakPolicy | MarketDataRecorderMetricsSnapshot | MarketDataSoakReport
    ).json_schema(),
    "dataset-manifest.schema.json": lambda: _model_schema(DatasetManifest),
    "normalized-segment-manifest.schema.json": lambda: _model_schema(NormalizedSegmentManifest),
    "normalizer-state.schema.json": lambda: _model_schema(NormalizerState),
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
        | PaperForecastDiagnostics
        | PaperStrategyEvaluation
        | PaperStrategyActionCount
        | PaperStrategyEvaluationSummary
        | PaperMarkout
        | PaperExecutionCommand
        | CausalCandle
        | CausalStructureState
        | SmartMoneySnapshot
        | LlmAssessment
        | LlmConfirmationRequest
        | LlmConfirmation
    ).json_schema(),
    "raw-segment-manifest.schema.json": lambda: _model_schema(RawSegmentManifest),
    "recorder-state.schema.json": lambda: _model_schema(RecorderState),
    "research.schema.json": lambda: TypeAdapter(
        ModelArtifactManifest
        | ForecastMatrixManifest
        | SearchPolicy
        | SearchReceipt
        | ResearchControlPolicy
        | ForecastRobustnessReport
        | ForecastEconomicReport
        | TargetFeasibilityReport
        | HorizonFamilyPolicy
        | HorizonFamilyFeasibilityReport
        | ResearchDataReadinessPolicy
        | ResearchDataReadinessReport
        | ResearchDataReadinessState
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
        | CleanupEvidenceManifest
        | CleanupInventoryAuditEvidence
        | CleanupTargetEvidence
        | CleanupCredentialScanEvidence
        | CleanupPreflightReceipt
        | CleanupActionPlan
        | CleanupOutcomeEvidenceManifest
        | CleanupCompletionReport
        | CleanupOperatorLedger
        | CleanupPathAbsenceEvidence
        | CleanupHostAbsenceEvidence
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
