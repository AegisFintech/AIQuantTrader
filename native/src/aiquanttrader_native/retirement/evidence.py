"""Deterministic Phase 10 retirement evidence evaluation."""

from __future__ import annotations

import time
import tomllib
from pathlib import Path

from aiquanttrader_native.domain.base import canonical_sha256
from aiquanttrader_native.retirement.models import (
    REQUIRED_ARCHIVE_ARTIFACTS,
    REQUIRED_DISABLED_CAPABILITIES,
    DisabledGateResult,
    DisabledObservation,
    DisabledObservationGate,
    DisabledObservationReport,
    RetirementGateResult,
    RetirementPolicy,
    RetirementReadinessGate,
    RetirementReadinessObservation,
    RetirementReadinessReport,
)


def load_retirement_policy(path: Path) -> RetirementPolicy:
    """Load a bounded, frozen TOML retirement policy."""

    resolved = path.resolve(strict=True)
    size = resolved.stat().st_size
    if not resolved.is_file() or size <= 0 or size > 1_048_576:
        raise ValueError("retirement policy path is invalid")
    with resolved.open("rb") as handle:
        return RetirementPolicy.model_validate(tomllib.load(handle))


def evaluate_retirement_readiness(
    *,
    observation: RetirementReadinessObservation,
    policy: RetirementPolicy,
    generated_ts_ns: int | None = None,
) -> RetirementReadinessReport:
    """Evaluate whether evidence may advance only to stop-approval review."""

    native_duration = observation.native.ended_ts_ns - observation.native.started_ts_ns
    remaining_retention = observation.archive.retention_expires_ts_ns - observation.observed_ts_ns
    archive_kinds = {artifact.kind for artifact in observation.archive.artifacts}
    flat_account = (
        observation.legacy.open_managed_positions == 0
        and observation.legacy.open_unmanaged_positions == 0
        and observation.legacy.pending_orders == 0
    )
    native_clean = (
        observation.native.critical_incidents == 0
        and observation.native.reconciliation_failures == 0
        and observation.native.risk_breaches == 0
    )
    gates = (
        _readiness_gate(
            RetirementReadinessGate.POLICY_FROZEN,
            policy.frozen_at_ns <= observation.native.started_ts_ns,
            policy.frozen_at_ns,
            f"<= {observation.native.started_ts_ns}",
        ),
        _readiness_gate(
            RetirementReadinessGate.NATIVE_OBSERVATION,
            native_duration >= policy.minimum_native_production_observation_ns,
            native_duration,
            policy.minimum_native_production_observation_ns,
        ),
        _readiness_gate(
            RetirementReadinessGate.NATIVE_CLEAN,
            native_clean,
            (
                f"critical={observation.native.critical_incidents},"
                f"reconciliation={observation.native.reconciliation_failures},"
                f"risk={observation.native.risk_breaches}"
            ),
            "critical=0,reconciliation=0,risk=0",
        ),
        _readiness_gate(
            RetirementReadinessGate.NATIVE_DRILLS,
            set(observation.native.completed_drills) == set(policy.required_native_drills),
            sorted(item.value for item in observation.native.completed_drills),
            sorted(item.value for item in policy.required_native_drills),
        ),
        _readiness_gate(
            RetirementReadinessGate.ARCHIVE_INVENTORY,
            archive_kinds == set(policy.required_archive_artifacts) == REQUIRED_ARCHIVE_ARTIFACTS,
            sorted(item.value for item in archive_kinds),
            sorted(item.value for item in REQUIRED_ARCHIVE_ARTIFACTS),
        ),
        _readiness_gate(
            RetirementReadinessGate.ARCHIVE_RESTORE,
            observation.archive.restore_test_passed,
            observation.archive.restore_test_passed,
            True,
        ),
        _readiness_gate(
            RetirementReadinessGate.ARCHIVE_RETENTION,
            remaining_retention >= policy.minimum_archive_retention_ns,
            remaining_retention,
            policy.minimum_archive_retention_ns,
        ),
        _readiness_gate(
            RetirementReadinessGate.ARCHIVE_NO_CREDENTIALS,
            observation.archive.contains_credentials is False,
            observation.archive.contains_credentials,
            False,
        ),
        _readiness_gate(
            RetirementReadinessGate.FINAL_TAG,
            observation.archive.final_tag_name == "mt5-final"
            and observation.archive.final_tag_commit_sha == observation.archive.source_commit_sha,
            (f"{observation.archive.final_tag_name}@{observation.archive.final_tag_commit_sha}"),
            f"mt5-final@{observation.archive.source_commit_sha}",
        ),
        _readiness_gate(
            RetirementReadinessGate.DEMO_ACCOUNT,
            observation.legacy.account_mode == "demo",
            observation.legacy.account_mode,
            "demo",
        ),
        _readiness_gate(
            RetirementReadinessGate.ENTRY_PAUSE,
            observation.legacy.entry_pause_active,
            observation.legacy.entry_pause_active,
            True,
        ),
        _readiness_gate(
            RetirementReadinessGate.FLAT_ACCOUNT,
            flat_account,
            (
                f"managed={observation.legacy.open_managed_positions},"
                f"unmanaged={observation.legacy.open_unmanaged_positions},"
                f"orders={observation.legacy.pending_orders}"
            ),
            "managed=0,unmanaged=0,orders=0",
        ),
        _readiness_gate(
            RetirementReadinessGate.NO_COMMAND_WRITERS,
            observation.legacy.command_file_writer_count == 0,
            observation.legacy.command_file_writer_count,
            0,
        ),
    )
    generated = time.time_ns() if generated_ts_ns is None else generated_ts_ns
    payload = {
        "schema_version": 1,
        "retirement_id": observation.retirement_id,
        "policy_id": policy.policy_id,
        "policy_sha256": policy.sha256(),
        "observation_sha256": observation.sha256(),
        "generated_ts_ns": generated,
        "native_deployment_id": observation.native.deployment_id,
        "native_admission_id": observation.native.admission_id,
        "archive_manifest_sha256": observation.archive.sha256(),
        "source_commit_sha": observation.archive.source_commit_sha,
        "final_tag_name": observation.archive.final_tag_name,
        "gates": [gate.model_dump(mode="json") for gate in gates],
    }
    return RetirementReadinessReport.model_validate(
        {
            "report_id": canonical_sha256(payload),
            "awaiting_stop_approval": all(gate.passed for gate in gates),
            **payload,
        }
    )


def evaluate_disabled_observation(
    *,
    observation: DisabledObservation,
    policy: RetirementPolicy,
    generated_ts_ns: int | None = None,
) -> DisabledObservationReport:
    """Evaluate the reversible disabled window before cleanup approval review."""

    duration = observation.ended_ts_ns - observation.started_ts_ns
    capabilities = {item.capability: item for item in observation.capabilities}
    exact_capabilities = (
        set(capabilities)
        == set(policy.required_disabled_capabilities)
        == REQUIRED_DISABLED_CAPABILITIES
    )
    all_disabled = exact_capabilities and all(item.disabled for item in capabilities.values())
    zero_active = exact_capabilities and all(
        item.active_instance_count == 0 for item in capabilities.values()
    )
    native_stable = (
        observation.native_critical_incidents == 0
        and observation.native_reconciliation_failures == 0
        and observation.native_risk_breaches == 0
    )
    gates = (
        _disabled_gate(
            DisabledObservationGate.POLICY_FROZEN,
            policy.frozen_at_ns <= observation.started_ts_ns,
            policy.frozen_at_ns,
            f"<= {observation.started_ts_ns}",
        ),
        _disabled_gate(
            DisabledObservationGate.OBSERVATION_WINDOW,
            duration >= policy.minimum_disabled_observation_ns,
            duration,
            policy.minimum_disabled_observation_ns,
        ),
        _disabled_gate(
            DisabledObservationGate.ALL_CAPABILITIES_DISABLED,
            all_disabled,
            sorted(item.capability.value for item in observation.capabilities if item.disabled),
            sorted(item.value for item in REQUIRED_DISABLED_CAPABILITIES),
        ),
        _disabled_gate(
            DisabledObservationGate.ZERO_ACTIVE_INSTANCES,
            zero_active,
            sum(item.active_instance_count for item in observation.capabilities),
            0,
        ),
        _disabled_gate(
            DisabledObservationGate.NO_LEGACY_ORDERS,
            observation.legacy_broker_orders_after_stop == 0,
            observation.legacy_broker_orders_after_stop,
            0,
        ),
        _disabled_gate(
            DisabledObservationGate.NATIVE_STABLE,
            native_stable,
            (
                f"critical={observation.native_critical_incidents},"
                f"reconciliation={observation.native_reconciliation_failures},"
                f"risk={observation.native_risk_breaches}"
            ),
            "critical=0,reconciliation=0,risk=0",
        ),
        _disabled_gate(
            DisabledObservationGate.ARCHIVE_REVERIFIED,
            observation.archive_reverified,
            observation.archive_reverified,
            True,
        ),
        _disabled_gate(
            DisabledObservationGate.CREDENTIALS_QUARANTINED,
            observation.legacy_credentials_quarantined,
            observation.legacy_credentials_quarantined,
            True,
        ),
    )
    generated = time.time_ns() if generated_ts_ns is None else generated_ts_ns
    payload = {
        "schema_version": 1,
        "retirement_id": observation.retirement_id,
        "policy_id": policy.policy_id,
        "policy_sha256": policy.sha256(),
        "observation_sha256": observation.sha256(),
        "generated_ts_ns": generated,
        "readiness_report_sha256": observation.readiness_report_sha256,
        "stop_approval_sha256": observation.stop_approval_sha256,
        "archive_manifest_sha256": observation.archive_manifest_sha256,
        "native_deployment_id": observation.native_deployment_id,
        "native_admission_id": observation.native_admission_id,
        "gates": [gate.model_dump(mode="json") for gate in gates],
    }
    return DisabledObservationReport.model_validate(
        {
            "report_id": canonical_sha256(payload),
            "awaiting_cleanup_approval": all(gate.passed for gate in gates),
            **payload,
        }
    )


def _readiness_gate(
    gate: RetirementReadinessGate, passed: bool, actual: object, required: object
) -> RetirementGateResult:
    return RetirementGateResult(
        gate=gate,
        passed=passed,
        actual=str(actual),
        required=str(required),
    )


def _disabled_gate(
    gate: DisabledObservationGate, passed: bool, actual: object, required: object
) -> DisabledGateResult:
    return DisabledGateResult(
        gate=gate,
        passed=passed,
        actual=str(actual),
        required=str(required),
    )
