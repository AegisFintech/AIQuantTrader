"""Short-lived, non-executing preflight for one approved cleanup manifest."""

from __future__ import annotations

import calendar
from datetime import UTC, datetime
from pathlib import Path
from time import time_ns

from aiquanttrader.domain.base import canonical_sha256
from aiquanttrader.retirement.approval import (
    ExpectedRetirementAction,
    RetirementApprovalPaths,
    verify_retirement_approval,
)
from aiquanttrader.retirement.cleanup import (
    MAX_CONTROL_BYTES,
    _read_regular,
    _replay_cleanup_evidence,
    verify_cleanup_manifest,
)
from aiquanttrader.retirement.models import (
    CleanupPreflightGate,
    CleanupPreflightGateResult,
    CleanupPreflightReceipt,
    CleanupPreflightTargetResult,
    DisabledObservationReport,
    LegacyArchiveCredentialScanPolicy,
    LegacyArchiveManifest,
    LegacyCleanupManifest,
    LegacyCleanupTarget,
    RetirementActionScope,
    RetirementPolicy,
)


def evaluate_cleanup_preflight(
    approved_evidence_root: Path,
    action_evidence_root: Path,
    cleanup_manifest: LegacyCleanupManifest,
    disabled_report: DisabledObservationReport,
    archive_manifest: LegacyArchiveManifest,
    *,
    cleanup_approval_paths: RetirementApprovalPaths,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    expected_cleanup_key_id: str,
    expected_cleanup_public_key_sha256: str,
) -> CleanupPreflightReceipt:
    """Evaluate current evidence without stop, revoke, package, or delete capability."""

    return _evaluate_cleanup_preflight(
        approved_evidence_root,
        action_evidence_root,
        cleanup_manifest,
        disabled_report,
        archive_manifest,
        cleanup_approval_paths=cleanup_approval_paths,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        expected_cleanup_key_id=expected_cleanup_key_id,
        expected_cleanup_public_key_sha256=expected_cleanup_public_key_sha256,
        evaluated_ts_ns=time_ns(),
    )


def verify_cleanup_preflight(
    approved_evidence_root: Path,
    action_evidence_root: Path,
    receipt: CleanupPreflightReceipt,
    cleanup_manifest: LegacyCleanupManifest,
    disabled_report: DisabledObservationReport,
    archive_manifest: LegacyArchiveManifest,
    *,
    cleanup_approval_paths: RetirementApprovalPaths,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    expected_cleanup_key_id: str,
    expected_cleanup_public_key_sha256: str,
) -> CleanupPreflightReceipt:
    """Replay a retained receipt while its approval and fresh-state window remain active."""

    verified_ts_ns = time_ns()
    if receipt.evaluated_ts_ns > verified_ts_ns:
        raise ValueError("cleanup preflight receipt is dated after verification")
    if verified_ts_ns >= receipt.valid_until_ts_ns:
        raise ValueError("cleanup preflight receipt has expired")
    replayed = _evaluate_cleanup_preflight(
        approved_evidence_root,
        action_evidence_root,
        cleanup_manifest,
        disabled_report,
        archive_manifest,
        cleanup_approval_paths=cleanup_approval_paths,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        expected_cleanup_key_id=expected_cleanup_key_id,
        expected_cleanup_public_key_sha256=expected_cleanup_public_key_sha256,
        evaluated_ts_ns=receipt.evaluated_ts_ns,
    )
    if replayed != receipt:
        raise ValueError("cleanup preflight receipt does not match source replay")
    return replayed


def load_cleanup_preflight_receipt(path: Path) -> CleanupPreflightReceipt:
    payload = _read_regular(path, maximum_bytes=MAX_CONTROL_BYTES)
    receipt = CleanupPreflightReceipt.model_validate_json(payload)
    if payload != receipt.canonical_bytes() + b"\n":
        raise ValueError("cleanup preflight receipt is not canonical JSON")
    return receipt


def _evaluate_cleanup_preflight(
    approved_evidence_root: Path,
    action_evidence_root: Path,
    cleanup_manifest: LegacyCleanupManifest,
    disabled_report: DisabledObservationReport,
    archive_manifest: LegacyArchiveManifest,
    *,
    cleanup_approval_paths: RetirementApprovalPaths,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    expected_cleanup_key_id: str,
    expected_cleanup_public_key_sha256: str,
    evaluated_ts_ns: int,
) -> CleanupPreflightReceipt:
    if evaluated_ts_ns < 0:
        raise ValueError("cleanup preflight evaluation timestamp cannot be negative")
    if not disabled_report.awaiting_cleanup_approval:
        raise ValueError("disabled observation report does not permit cleanup preflight")

    approved_manifest = verify_cleanup_manifest(
        approved_evidence_root,
        cleanup_manifest,
        disabled_report,
        archive_manifest,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
    )
    action_replay = _replay_cleanup_evidence(
        action_evidence_root,
        disabled_report,
        archive_manifest,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        assembled_ts_ns=evaluated_ts_ns,
    )

    approval = verify_retirement_approval(
        paths=cleanup_approval_paths,
        expected=ExpectedRetirementAction(
            retirement_id=approved_manifest.retirement_id,
            scope=RetirementActionScope.REMOVE_AND_CLEAN,
            report_sha256=disabled_report.sha256(),
            native_deployment_id=disabled_report.native_deployment_id,
            native_admission_id=disabled_report.native_admission_id,
            archive_manifest_sha256=archive_manifest.sha256(),
            source_commit_sha=approved_manifest.source_commit_sha,
            cleanup_manifest_sha256=approved_manifest.sha256(),
        ),
        expected_key_id=expected_cleanup_key_id,
        expected_public_key_sha256=expected_cleanup_public_key_sha256,
        now=_datetime_from_ns(evaluated_ts_ns),
    )
    approval_created_ts_ns = _datetime_to_ns(approval.approval.approved_at)
    approval_expires_ts_ns = _datetime_to_ns(approval.approval.expires_at)
    if approval_created_ts_ns < approved_manifest.created_ts_ns:
        raise ValueError("cleanup approval predates the approved cleanup manifest")

    timestamps = action_replay.evidence_timestamps_ns()
    capture_start_ts_ns = min(timestamps)
    capture_end_ts_ns = max(timestamps)
    if capture_start_ts_ns < approval_created_ts_ns:
        raise ValueError("action-time cleanup evidence predates cleanup approval")
    freshness_ns = policy.maximum_final_state_capture_skew_ns
    action_state_expires_ts_ns = capture_start_ts_ns + freshness_ns
    if evaluated_ts_ns >= action_state_expires_ts_ns:
        raise ValueError("action-time cleanup evidence is stale")

    approved_inventory = tuple(_target_inventory(item) for item in approved_manifest.targets)
    observed_inventory = tuple(
        _target_inventory(item) for item in action_replay.cleanup_manifest.targets
    )
    if observed_inventory != approved_inventory:
        raise ValueError("action-time cleanup target inventory differs from approval")

    observed_by_id = {item.target_id: item for item in action_replay.cleanup_manifest.targets}
    target_results = tuple(
        CleanupPreflightTargetResult(
            target_id=item.target_id,
            kind=item.kind,
            locator=item.locator,
            action=item.action,
            destination_locator=item.destination_locator,
            expected_state_sha256=item.expected_state_sha256,
            observed_state_sha256=observed_by_id[item.target_id].expected_state_sha256,
            state_matches=(
                observed_by_id[item.target_id].expected_state_sha256 == item.expected_state_sha256
            ),
        )
        for item in approved_manifest.targets
    )
    if not all(item.state_matches for item in target_results):
        raise ValueError("action-time cleanup target state differs from approval")

    valid_until_ts_ns = min(action_state_expires_ts_ns, approval_expires_ts_ns)

    gates = (
        _passed_gate(
            CleanupPreflightGate.POLICY_BOUND,
            actual=policy.sha256(),
            required=approved_manifest.policy_sha256,
        ),
        _passed_gate(
            CleanupPreflightGate.DISABLED_OBSERVATION_PASSED,
            actual="awaiting_cleanup_approval",
            required="all disabled-window gates passed",
        ),
        _passed_gate(
            CleanupPreflightGate.APPROVED_MANIFEST_REPLAYED,
            actual=approved_manifest.sha256(),
            required=cleanup_manifest.sha256(),
        ),
        _passed_gate(
            CleanupPreflightGate.ACTION_EVIDENCE_REPLAYED,
            actual=action_replay.cleanup_manifest.sha256(),
            required="exact inventory, lineage, review, and zero-finding scan",
        ),
        _passed_gate(
            CleanupPreflightGate.ACTION_EVIDENCE_FRESH,
            actual=f"{evaluated_ts_ns - capture_start_ts_ns}ns oldest evidence age",
            required=f"less than {freshness_ns}ns",
        ),
        _passed_gate(
            CleanupPreflightGate.TARGET_INVENTORY_EXACT,
            actual=f"{len(target_results)} exact targets",
            required=f"{len(approved_manifest.targets)} approved targets",
        ),
        _passed_gate(
            CleanupPreflightGate.TARGET_STATE_UNCHANGED,
            actual=f"{len(target_results)} matching state hashes",
            required="every approved target state hash matches",
        ),
        _passed_gate(
            CleanupPreflightGate.CLEANUP_APPROVAL_ACTIVE,
            actual=approval.verification_id,
            required="active remove_and_clean approval for the exact manifest",
        ),
    )
    payload = {
        "schema_version": 1,
        "retirement_id": approved_manifest.retirement_id,
        "evaluated_ts_ns": evaluated_ts_ns,
        "valid_until_ts_ns": valid_until_ts_ns,
        "action_capture_start_ts_ns": capture_start_ts_ns,
        "action_capture_end_ts_ns": capture_end_ts_ns,
        "action_state_expires_ts_ns": action_state_expires_ts_ns,
        "approval_expires_ts_ns": approval_expires_ts_ns,
        "policy_id": policy.policy_id,
        "policy_sha256": policy.sha256(),
        "disabled_observation_report_sha256": disabled_report.sha256(),
        "archive_manifest_sha256": archive_manifest.sha256(),
        "approved_cleanup_manifest_sha256": approved_manifest.sha256(),
        "action_snapshot_sha256": action_replay.cleanup_manifest.sha256(),
        "action_evidence_manifest_sha256": action_replay.evidence_manifest.sha256(),
        "action_evidence_bundle_sha256": action_replay.cleanup_manifest.evidence_bundle_sha256,
        "cleanup_approval_sha256": approval.approval.sha256(),
        "approval_verification_id": approval.verification_id,
        "approval_public_key_sha256": approval.public_key_sha256,
        "approval_signature_envelope_sha256": approval.signature_envelope_sha256,
        "native_deployment_id": disabled_report.native_deployment_id,
        "native_admission_id": disabled_report.native_admission_id,
        "source_commit_sha": approved_manifest.source_commit_sha,
        "final_tag_name": approved_manifest.final_tag_name,
        "execution_mode": "evidence_only",
        "operator_action_required": True,
        "targets": [item.model_dump(mode="json") for item in target_results],
        "gates": [item.model_dump(mode="json") for item in gates],
    }
    return CleanupPreflightReceipt.model_validate(
        {
            **payload,
            "receipt_id": canonical_sha256(payload),
            "ready_for_operator_action": True,
        }
    )


def _target_inventory(target: LegacyCleanupTarget) -> tuple[object, ...]:
    return (
        target.target_id,
        target.kind,
        target.locator,
        target.action,
        target.destination_locator,
        target.rationale,
    )


def _passed_gate(
    gate: CleanupPreflightGate,
    *,
    actual: str,
    required: str,
) -> CleanupPreflightGateResult:
    return CleanupPreflightGateResult(
        gate=gate,
        passed=True,
        actual=actual,
        required=required,
    )


def _datetime_from_ns(value: int) -> datetime:
    seconds, nanoseconds = divmod(value, 1_000_000_000)
    return datetime.fromtimestamp(seconds, UTC).replace(microsecond=nanoseconds // 1_000)


def _datetime_to_ns(value: datetime) -> int:
    instant = value.astimezone(UTC)
    return calendar.timegm(instant.utctimetuple()) * 1_000_000_000 + instant.microsecond * 1_000
