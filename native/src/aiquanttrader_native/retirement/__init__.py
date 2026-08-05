"""Fail-closed evidence contracts for retiring the legacy MT5 deployment."""

from aiquanttrader_native.retirement.archive import (
    assemble_legacy_archive_manifest,
    verify_legacy_archive_manifest,
)
from aiquanttrader_native.retirement.collector import (
    assemble_native_production_observation,
    verify_native_production_observation,
)
from aiquanttrader_native.retirement.evidence import (
    evaluate_disabled_observation,
    evaluate_retirement_readiness,
    load_retirement_policy,
)
from aiquanttrader_native.retirement.models import (
    DisabledObservation,
    DisabledObservationReport,
    RetirementActionApproval,
    RetirementActionScope,
    RetirementReadinessObservation,
    RetirementReadinessReport,
)

__all__ = [
    "DisabledObservation",
    "DisabledObservationReport",
    "RetirementActionApproval",
    "RetirementActionScope",
    "RetirementReadinessObservation",
    "RetirementReadinessReport",
    "assemble_legacy_archive_manifest",
    "assemble_native_production_observation",
    "evaluate_disabled_observation",
    "evaluate_retirement_readiness",
    "load_retirement_policy",
    "verify_legacy_archive_manifest",
    "verify_native_production_observation",
]
