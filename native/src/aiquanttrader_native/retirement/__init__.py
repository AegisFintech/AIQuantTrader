"""Fail-closed evidence contracts for retiring the legacy MT5 deployment."""

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
    "evaluate_disabled_observation",
    "evaluate_retirement_readiness",
    "load_retirement_policy",
]
