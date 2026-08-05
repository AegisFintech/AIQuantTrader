"""Fail-closed evidence contracts for retiring the legacy MT5 deployment."""

from aiquanttrader.retirement.action_plan import (
    prepare_cleanup_action_plan,
    verify_cleanup_action_plan,
)
from aiquanttrader.retirement.archive import (
    assemble_legacy_archive_manifest,
    verify_legacy_archive_manifest,
)
from aiquanttrader.retirement.closeout import (
    assemble_cleanup_closeout,
    verify_cleanup_closeout,
)
from aiquanttrader.retirement.collector import (
    assemble_native_production_observation,
    verify_native_production_observation,
)
from aiquanttrader.retirement.disabled import (
    assemble_disabled_observation,
    verify_disabled_observation,
)
from aiquanttrader.retirement.evidence import (
    evaluate_disabled_observation,
    evaluate_retirement_readiness,
    load_retirement_policy,
)
from aiquanttrader.retirement.final_state import (
    assemble_legacy_final_state,
    verify_legacy_final_state,
)
from aiquanttrader.retirement.models import (
    CleanupActionPlan,
    CleanupCompletionReport,
    CleanupOperatorLedger,
    CleanupPreflightReceipt,
    DisabledObservation,
    DisabledObservationReport,
    RetirementActionApproval,
    RetirementActionScope,
    RetirementReadinessObservation,
    RetirementReadinessReport,
)
from aiquanttrader.retirement.outcome import (
    assemble_cleanup_completion,
    verify_cleanup_completion,
)
from aiquanttrader.retirement.preflight import (
    evaluate_cleanup_preflight,
    verify_cleanup_preflight,
)
from aiquanttrader.retirement.readiness import (
    assemble_retirement_readiness_observation,
    verify_retirement_readiness_observation,
)

__all__ = [
    "CleanupActionPlan",
    "CleanupCompletionReport",
    "CleanupOperatorLedger",
    "CleanupPreflightReceipt",
    "DisabledObservation",
    "DisabledObservationReport",
    "RetirementActionApproval",
    "RetirementActionScope",
    "RetirementReadinessObservation",
    "RetirementReadinessReport",
    "assemble_cleanup_closeout",
    "assemble_cleanup_completion",
    "assemble_disabled_observation",
    "assemble_legacy_archive_manifest",
    "assemble_legacy_final_state",
    "assemble_native_production_observation",
    "assemble_retirement_readiness_observation",
    "evaluate_cleanup_preflight",
    "evaluate_disabled_observation",
    "evaluate_retirement_readiness",
    "load_retirement_policy",
    "prepare_cleanup_action_plan",
    "verify_cleanup_action_plan",
    "verify_cleanup_closeout",
    "verify_cleanup_completion",
    "verify_cleanup_preflight",
    "verify_disabled_observation",
    "verify_legacy_archive_manifest",
    "verify_legacy_final_state",
    "verify_native_production_observation",
    "verify_retirement_readiness_observation",
]
