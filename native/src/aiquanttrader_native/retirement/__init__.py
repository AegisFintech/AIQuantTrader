"""Fail-closed evidence contracts for retiring the legacy MT5 deployment."""

from aiquanttrader_native.retirement.archive import (
    assemble_legacy_archive_manifest,
    verify_legacy_archive_manifest,
)
from aiquanttrader_native.retirement.collector import (
    assemble_native_production_observation,
    verify_native_production_observation,
)
from aiquanttrader_native.retirement.disabled import (
    assemble_disabled_observation,
    verify_disabled_observation,
)
from aiquanttrader_native.retirement.evidence import (
    evaluate_disabled_observation,
    evaluate_retirement_readiness,
    load_retirement_policy,
)
from aiquanttrader_native.retirement.final_state import (
    assemble_legacy_final_state,
    verify_legacy_final_state,
)
from aiquanttrader_native.retirement.models import (
    DisabledObservation,
    DisabledObservationReport,
    RetirementActionApproval,
    RetirementActionScope,
    RetirementReadinessObservation,
    RetirementReadinessReport,
)
from aiquanttrader_native.retirement.readiness import (
    assemble_retirement_readiness_observation,
    verify_retirement_readiness_observation,
)

__all__ = [
    "DisabledObservation",
    "DisabledObservationReport",
    "RetirementActionApproval",
    "RetirementActionScope",
    "RetirementReadinessObservation",
    "RetirementReadinessReport",
    "assemble_disabled_observation",
    "assemble_legacy_archive_manifest",
    "assemble_legacy_final_state",
    "assemble_native_production_observation",
    "assemble_retirement_readiness_observation",
    "evaluate_disabled_observation",
    "evaluate_retirement_readiness",
    "load_retirement_policy",
    "verify_disabled_observation",
    "verify_legacy_archive_manifest",
    "verify_legacy_final_state",
    "verify_native_production_observation",
    "verify_retirement_readiness_observation",
]
