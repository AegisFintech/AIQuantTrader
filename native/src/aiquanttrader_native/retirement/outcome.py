"""Exact, non-executing verification of operator-produced cleanup outcomes."""

from __future__ import annotations

import hashlib
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from time import time_ns

from aiquanttrader_native.domain.base import DomainModel, canonical_sha256
from aiquanttrader_native.retirement.approval import RetirementApprovalPaths
from aiquanttrader_native.retirement.cleanup import (
    MAX_BUNDLE_BYTES,
    MAX_BUNDLE_FILES,
    MAX_CONTROL_BYTES,
    InventoryEntry,
    _assert_inventory_unchanged,
    _hash_regular,
    _load_control,
    _read_regular,
    _replay_cleanup_evidence,
    _validated_root,
)
from aiquanttrader_native.retirement.models import (
    CleanupArchiveOnlyResult,
    CleanupCompletionReport,
    CleanupCredentialScanEvidence,
    CleanupEvidenceArtifact,
    CleanupHostAbsenceEvidence,
    CleanupNativeMigrationResult,
    CleanupOutcomeControl,
    CleanupOutcomeControlKind,
    CleanupOutcomeEvidenceManifest,
    CleanupOutcomeGate,
    CleanupOutcomeGateResult,
    CleanupOutcomeTargetResult,
    CleanupPathAbsenceEvidence,
    CleanupPathInventoryEvidence,
    CleanupPreflightReceipt,
    CleanupRemovedHostResult,
    CleanupRemovedPathResult,
    CleanupRevokedSecretResult,
    CleanupSecretState,
    CleanupTargetEvidence,
    CleanupTargetKind,
    CleanupTargetOutcomeEvidence,
    DisabledObservationReport,
    LegacyArchiveCredentialScanPolicy,
    LegacyArchiveManifest,
    LegacyCleanupManifest,
    RetirementPolicy,
)
from aiquanttrader_native.retirement.preflight import _evaluate_cleanup_preflight

OUTCOME_MANIFEST_NAME = "cleanup-outcome-evidence.json"


@dataclass(frozen=True, slots=True)
class CleanupOutcomeReplay:
    manifest: CleanupOutcomeEvidenceManifest
    credential_scan: CleanupCredentialScanEvidence
    targets: tuple[CleanupTargetOutcomeEvidence, ...]
    bundle_sha256: str


def assemble_cleanup_completion(
    outcome_evidence_root: Path,
    approved_evidence_root: Path,
    action_evidence_root: Path,
    preflight_receipt: CleanupPreflightReceipt,
    cleanup_manifest: LegacyCleanupManifest,
    disabled_report: DisabledObservationReport,
    archive_manifest: LegacyArchiveManifest,
    *,
    cleanup_approval_paths: RetirementApprovalPaths,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    expected_cleanup_key_id: str,
    expected_cleanup_public_key_sha256: str,
) -> CleanupCompletionReport:
    """Assemble an evidence verdict without process, package, credential, or file access."""

    return _assemble_cleanup_completion(
        outcome_evidence_root,
        approved_evidence_root,
        action_evidence_root,
        preflight_receipt,
        cleanup_manifest,
        disabled_report,
        archive_manifest,
        cleanup_approval_paths=cleanup_approval_paths,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        expected_cleanup_key_id=expected_cleanup_key_id,
        expected_cleanup_public_key_sha256=expected_cleanup_public_key_sha256,
        generated_ts_ns=time_ns(),
    )


def verify_cleanup_completion(
    outcome_evidence_root: Path,
    approved_evidence_root: Path,
    action_evidence_root: Path,
    report: CleanupCompletionReport,
    preflight_receipt: CleanupPreflightReceipt,
    cleanup_manifest: LegacyCleanupManifest,
    disabled_report: DisabledObservationReport,
    archive_manifest: LegacyArchiveManifest,
    *,
    cleanup_approval_paths: RetirementApprovalPaths,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    expected_cleanup_key_id: str,
    expected_cleanup_public_key_sha256: str,
) -> CleanupCompletionReport:
    """Replay a retained completion report even after its initiating receipt expires."""

    verified_ts_ns = time_ns()
    if report.generated_ts_ns > verified_ts_ns:
        raise ValueError("cleanup completion report is dated after verification")
    replayed = _assemble_cleanup_completion(
        outcome_evidence_root,
        approved_evidence_root,
        action_evidence_root,
        preflight_receipt,
        cleanup_manifest,
        disabled_report,
        archive_manifest,
        cleanup_approval_paths=cleanup_approval_paths,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        expected_cleanup_key_id=expected_cleanup_key_id,
        expected_cleanup_public_key_sha256=expected_cleanup_public_key_sha256,
        generated_ts_ns=report.generated_ts_ns,
    )
    if replayed != report:
        raise ValueError("cleanup completion report does not match source replay")
    return replayed


def load_cleanup_completion_report(path: Path) -> CleanupCompletionReport:
    payload = _read_regular(path, maximum_bytes=MAX_CONTROL_BYTES)
    report = CleanupCompletionReport.model_validate_json(payload)
    if payload != report.canonical_bytes() + b"\n":
        raise ValueError("cleanup completion report is not canonical JSON")
    return report


def _assemble_cleanup_completion(
    outcome_evidence_root: Path,
    approved_evidence_root: Path,
    action_evidence_root: Path,
    preflight_receipt: CleanupPreflightReceipt,
    cleanup_manifest: LegacyCleanupManifest,
    disabled_report: DisabledObservationReport,
    archive_manifest: LegacyArchiveManifest,
    *,
    cleanup_approval_paths: RetirementApprovalPaths,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    expected_cleanup_key_id: str,
    expected_cleanup_public_key_sha256: str,
    generated_ts_ns: int,
) -> CleanupCompletionReport:
    if generated_ts_ns < 0:
        raise ValueError("cleanup completion generation timestamp cannot be negative")
    preflight = _evaluate_cleanup_preflight(
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
        evaluated_ts_ns=preflight_receipt.evaluated_ts_ns,
    )
    if preflight != preflight_receipt or not preflight.ready_for_operator_action:
        raise ValueError("cleanup preflight receipt does not match its complete source replay")

    approved_replay = _replay_cleanup_evidence(
        approved_evidence_root,
        disabled_report,
        archive_manifest,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        assembled_ts_ns=cleanup_manifest.created_ts_ns,
    )
    if approved_replay.cleanup_manifest != cleanup_manifest:
        raise ValueError("approved cleanup manifest does not match source replay")

    outcome = _replay_outcome_evidence(
        outcome_evidence_root,
        preflight,
        cleanup_manifest,
        disabled_report,
        archive_manifest,
        approved_replay.target_evidence,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        generated_ts_ns=generated_ts_ns,
    )
    target_results = tuple(
        CleanupOutcomeTargetResult(
            target_id=item.target_id,
            kind=item.kind,
            locator=item.locator,
            action=item.action,
            destination_locator=item.destination_locator,
            pre_action_state_sha256=item.pre_action_state_sha256,
            postcondition_sha256=item.postcondition_sha256(),
            action_started_ts_ns=item.action_started_ts_ns,
            action_completed_ts_ns=item.action_completed_ts_ns,
            postcondition_met=True,
        )
        for item in outcome.targets
    )
    gates = (
        _passed_gate(
            CleanupOutcomeGate.PREFLIGHT_REPLAYED,
            preflight.sha256(),
            "exact historical preflight source replay",
        ),
        _passed_gate(
            CleanupOutcomeGate.ACTIONS_STARTED_WHILE_AUTHORIZED,
            f"{len(target_results)} target actions",
            "every action started inside the exclusive preflight window",
        ),
        _passed_gate(
            CleanupOutcomeGate.TARGET_INVENTORY_EXACT,
            f"{len(target_results)} exact targets",
            f"{len(cleanup_manifest.targets)} approved targets",
        ),
        _passed_gate(
            CleanupOutcomeGate.PRE_ACTION_STATE_BOUND,
            f"{len(target_results)} matching hashes",
            "every target binds its approved pre-action state",
        ),
        _passed_gate(
            CleanupOutcomeGate.POSTCONDITIONS_VERIFIED,
            f"{len(target_results)} typed postconditions",
            "every approved action has its exact typed outcome",
        ),
        _passed_gate(
            CleanupOutcomeGate.OUTCOME_EVIDENCE_EXACT,
            outcome.bundle_sha256,
            "immutable exact-inventory outcome bundle",
        ),
        _passed_gate(
            CleanupOutcomeGate.CREDENTIAL_SCAN_PASSED,
            outcome.credential_scan.sha256(),
            "complete policy-bound zero-finding scan",
        ),
        _passed_gate(
            CleanupOutcomeGate.ARCHIVE_RETENTION_ACTIVE,
            str(archive_manifest.retention_expires_ts_ns - generated_ts_ns),
            f"at least {policy.minimum_archive_retention_ns}ns remains",
        ),
        _passed_gate(
            CleanupOutcomeGate.INDEPENDENT_REVIEW_COMPLETE,
            f"{len(target_results)} independently reviewed outcomes",
            "collector and reviewer differ for every target",
        ),
    )
    payload = {
        "schema_version": 1,
        "retirement_id": cleanup_manifest.retirement_id,
        "generated_ts_ns": generated_ts_ns,
        "policy_id": policy.policy_id,
        "policy_sha256": policy.sha256(),
        "source_commit_sha": cleanup_manifest.source_commit_sha,
        "archive_manifest_sha256": archive_manifest.sha256(),
        "disabled_observation_report_sha256": disabled_report.sha256(),
        "cleanup_manifest_sha256": cleanup_manifest.sha256(),
        "cleanup_preflight_receipt_sha256": preflight.sha256(),
        "outcome_evidence_manifest_sha256": outcome.manifest.sha256(),
        "outcome_evidence_bundle_sha256": outcome.bundle_sha256,
        "credential_scan_sha256": outcome.credential_scan.sha256(),
        "native_deployment_id": disabled_report.native_deployment_id,
        "native_admission_id": disabled_report.native_admission_id,
        "verification_mode": "evidence_only",
        "operator_actions_observed": True,
        "targets": [item.model_dump(mode="json") for item in target_results],
        "gates": [item.model_dump(mode="json") for item in gates],
    }
    return CleanupCompletionReport.model_validate(
        {**payload, "report_id": canonical_sha256(payload), "cleanup_complete": True}
    )


def _replay_outcome_evidence(
    evidence_root: Path,
    preflight: CleanupPreflightReceipt,
    cleanup_manifest: LegacyCleanupManifest,
    disabled_report: DisabledObservationReport,
    archive_manifest: LegacyArchiveManifest,
    approved_target_evidence: tuple[CleanupTargetEvidence, ...],
    *,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    generated_ts_ns: int,
) -> CleanupOutcomeReplay:
    root = _validated_root(evidence_root)
    manifest = _load_outcome_manifest(root / OUTCOME_MANIFEST_NAME)
    if manifest.created_ts_ns > generated_ts_ns:
        raise ValueError("cleanup outcome evidence is dated after report generation")

    bindings: dict[str, CleanupEvidenceArtifact | CleanupOutcomeControl] = {
        item.relative_path: item for item in manifest.artifacts
    }
    bindings.update({item.relative_path: item for item in manifest.controls})
    inventory = _validate_outcome_inventory(root, bindings)
    by_path = {item.relative_path: item for item in inventory}
    expected_manifest_hash = hashlib.sha256(manifest.canonical_bytes() + b"\n").hexdigest()
    if by_path[OUTCOME_MANIFEST_NAME].content_sha256 != expected_manifest_hash:
        raise ValueError("cleanup outcome manifest changed while loading")

    scan_binding = _single_outcome_control(
        manifest.controls,
        CleanupOutcomeControlKind.CREDENTIAL_SCAN,
    )
    target_bindings = sorted(
        (
            item
            for item in manifest.controls
            if item.kind is CleanupOutcomeControlKind.TARGET_OUTCOME
        ),
        key=lambda item: item.reference_id,
    )
    scan = _load_bound_outcome_control(root, scan_binding, CleanupCredentialScanEvidence)
    targets = tuple(
        _load_bound_outcome_control(root, item, CleanupTargetOutcomeEvidence)
        for item in target_bindings
    )
    _verify_outcome_lineage(
        manifest,
        scan,
        target_bindings,
        targets,
        preflight,
        cleanup_manifest,
        disabled_report,
        archive_manifest,
        policy,
        credential_scan_policy,
        generated_ts_ns,
    )
    _verify_target_outcomes(
        root,
        manifest,
        targets,
        cleanup_manifest,
        preflight,
        approved_target_evidence,
        archive_manifest,
    )
    _verify_outcome_scan(manifest, scan_binding, scan, credential_scan_policy, targets)
    _assert_inventory_unchanged(root, inventory)
    bundle_sha256 = canonical_sha256(
        {
            "schema_version": 1,
            "files": [
                {
                    "relative_path": item.relative_path,
                    "content_sha256": item.content_sha256,
                    "byte_count": item.byte_count,
                }
                for item in inventory
            ],
        }
    )
    return CleanupOutcomeReplay(manifest, scan, targets, bundle_sha256)


def _verify_outcome_lineage(
    manifest: CleanupOutcomeEvidenceManifest,
    scan: CleanupCredentialScanEvidence,
    target_bindings: list[CleanupOutcomeControl],
    targets: tuple[CleanupTargetOutcomeEvidence, ...],
    preflight: CleanupPreflightReceipt,
    cleanup_manifest: LegacyCleanupManifest,
    disabled_report: DisabledObservationReport,
    archive_manifest: LegacyArchiveManifest,
    policy: RetirementPolicy,
    scan_policy: LegacyArchiveCredentialScanPolicy,
    generated_ts_ns: int,
) -> None:
    if (
        manifest.retirement_id != cleanup_manifest.retirement_id
        or manifest.retirement_id != disabled_report.retirement_id
        or manifest.retirement_id != archive_manifest.retirement_id
        or scan.retirement_id != manifest.retirement_id
        or any(item.retirement_id != manifest.retirement_id for item in targets)
    ):
        raise ValueError("cleanup outcome retirement identity differs")
    if (
        manifest.policy_id != policy.policy_id
        or manifest.policy_sha256 != policy.sha256()
        or manifest.credential_scan_policy_id != scan_policy.policy_id
        or manifest.credential_scan_policy_sha256 != scan_policy.sha256()
        or scan.policy_id != scan_policy.policy_id
        or scan.policy_sha256 != scan_policy.sha256()
    ):
        raise ValueError("cleanup outcome policy identity differs")
    if (
        manifest.source_commit_sha != cleanup_manifest.source_commit_sha
        or manifest.archive_manifest_sha256 != archive_manifest.sha256()
        or manifest.disabled_observation_report_sha256 != disabled_report.sha256()
        or manifest.cleanup_manifest_sha256 != cleanup_manifest.sha256()
        or manifest.cleanup_preflight_receipt_sha256 != preflight.sha256()
    ):
        raise ValueError("cleanup outcome source lineage differs")
    if manifest.created_ts_ns < preflight.evaluated_ts_ns:
        raise ValueError("cleanup outcome manifest predates preflight")
    if (
        archive_manifest.retention_expires_ts_ns - generated_ts_ns
        < policy.minimum_archive_retention_ns
    ):
        raise ValueError("cleanup outcome lacks required remaining archive retention")
    if scan.reviewed_ts_ns > manifest.created_ts_ns or any(
        item.captured_ts_ns > manifest.created_ts_ns for item in targets
    ):
        raise ValueError("cleanup outcome source evidence postdates its manifest")
    if [item.reference_id for item in target_bindings] != [item.target_id for item in targets]:
        raise ValueError("cleanup outcome controls differ from their bound identities")


def _verify_target_outcomes(
    root: Path,
    manifest: CleanupOutcomeEvidenceManifest,
    outcomes: tuple[CleanupTargetOutcomeEvidence, ...],
    cleanup_manifest: LegacyCleanupManifest,
    preflight: CleanupPreflightReceipt,
    approved_target_evidence: tuple[CleanupTargetEvidence, ...],
    archive_manifest: LegacyArchiveManifest,
) -> None:
    approved = {item.target_id: item for item in cleanup_manifest.targets}
    preflight_targets = {item.target_id: item for item in preflight.targets}
    approved_evidence = {item.target_id: item for item in approved_target_evidence}
    if [item.target_id for item in outcomes] != sorted(approved):
        raise ValueError("cleanup outcome target inventory differs from approval")

    artifacts = {item.artifact_id: item for item in manifest.artifacts}
    referenced_artifacts: set[str] = set()
    for outcome in outcomes:
        target = approved[outcome.target_id]
        preflight_target = preflight_targets.get(outcome.target_id)
        if (
            preflight_target is None
            or (outcome.kind, outcome.locator, outcome.action, outcome.destination_locator)
            != (target.kind, target.locator, target.action, target.destination_locator)
            or outcome.pre_action_state_sha256 != target.expected_state_sha256
            or preflight_target.expected_state_sha256 != target.expected_state_sha256
            or preflight_target.observed_state_sha256 != target.expected_state_sha256
        ):
            raise ValueError("cleanup outcome target or pre-action state differs from approval")
        if not (
            preflight.evaluated_ts_ns <= outcome.action_started_ts_ns < preflight.valid_until_ts_ns
        ):
            raise ValueError("cleanup outcome action did not start inside preflight validity")
        artifact_ids = outcome.artifact_ids()
        if any(item not in artifacts for item in artifact_ids):
            raise ValueError("cleanup outcome references unbound raw evidence")
        referenced_artifacts.update(artifact_ids)
        for artifact_id in artifact_ids:
            artifact = artifacts[artifact_id]
            if not (
                outcome.action_completed_ts_ns <= artifact.captured_ts_ns <= outcome.captured_ts_ns
            ):
                raise ValueError("cleanup outcome raw evidence capture interval is invalid")

        result = outcome.result
        if isinstance(result, CleanupRemovedPathResult | CleanupArchiveOnlyResult):
            _verify_path_absence(
                root,
                artifacts[result.raw_artifact_id],
                kind=result.kind,
                locator=result.locator,
                observed_commit_sha=result.observed_commit_sha,
            )
            if isinstance(result, CleanupArchiveOnlyResult) and (
                result.archive_manifest_sha256 != archive_manifest.sha256()
            ):
                raise ValueError("archive-only outcome does not retain the approved archive")
        elif isinstance(result, CleanupRemovedHostResult):
            proofs = tuple(
                _load_control(
                    root / artifacts[artifact_id].relative_path,
                    CleanupHostAbsenceEvidence,
                )
                for artifact_id in result.raw_artifact_ids
            )
            if (
                any(
                    (proof.kind, proof.locator, proof.captured_ts_ns)
                    != (result.kind, result.locator, artifacts[artifact_id].captured_ts_ns)
                    for proof, artifact_id in zip(proofs, result.raw_artifact_ids, strict=True)
                )
                or len({proof.observation_source for proof in proofs}) != 2
            ):
                raise ValueError("removed host proofs differ or are not independent")
        elif isinstance(result, CleanupNativeMigrationResult):
            _verify_path_absence(
                root,
                artifacts[result.raw_artifact_ids[0]],
                kind=result.kind,
                locator=result.locator,
                observed_commit_sha=result.migration_commit_sha,
            )
            destination_artifact = artifacts[result.raw_artifact_ids[1]]
            destination_inventory = _load_control(
                root / destination_artifact.relative_path,
                CleanupPathInventoryEvidence,
            )
            if (
                destination_inventory.kind is not result.kind
                or destination_inventory.locator != result.destination_locator
                or destination_inventory.source_commit_sha != result.migration_commit_sha
                or destination_inventory.state_sha256() != result.destination_inventory_sha256
                or destination_artifact.content_sha256
                != hashlib.sha256(destination_inventory.canonical_bytes() + b"\n").hexdigest()
                or destination_inventory.captured_ts_ns != destination_artifact.captured_ts_ns
            ):
                raise ValueError("native migration destination inventory differs")
        elif isinstance(result, CleanupRevokedSecretResult):
            source = approved_evidence[outcome.target_id]
            source_state = source.state
            if not isinstance(source_state, CleanupSecretState) or (
                result.provider,
                result.provider_record_id_sha256,
            ) != (source_state.provider, source_state.provider_record_id_sha256):
                raise ValueError("revoked secret outcome differs from approved provider record")
            raw_hashes = tuple(artifacts[item].content_sha256 for item in result.raw_artifact_ids)
            if raw_hashes != (result.provider_state_sha256, result.active_sessions_sha256):
                raise ValueError("revoked secret result hashes differ from raw evidence")
    if referenced_artifacts != set(artifacts):
        raise ValueError("cleanup outcome contains unreferenced raw evidence")


def _verify_path_absence(
    root: Path,
    artifact: CleanupEvidenceArtifact,
    *,
    kind: CleanupTargetKind,
    locator: str,
    observed_commit_sha: str | None,
) -> None:
    proof = _load_control(root / artifact.relative_path, CleanupPathAbsenceEvidence)
    if (proof.kind, proof.locator, proof.observed_commit_sha, proof.captured_ts_ns) != (
        kind,
        locator,
        observed_commit_sha,
        artifact.captured_ts_ns,
    ) or artifact.content_sha256 != hashlib.sha256(proof.canonical_bytes() + b"\n").hexdigest():
        raise ValueError("cleanup path absence proof differs from typed outcome")


def _verify_outcome_scan(
    manifest: CleanupOutcomeEvidenceManifest,
    scan_binding: CleanupOutcomeControl,
    scan: CleanupCredentialScanEvidence,
    scan_policy: LegacyArchiveCredentialScanPolicy,
    targets: tuple[CleanupTargetOutcomeEvidence, ...],
) -> None:
    expected = {item.relative_path: item.content_sha256 for item in manifest.artifacts}
    expected.update(
        {
            item.relative_path: item.content_sha256
            for item in manifest.controls
            if item.relative_path != scan_binding.relative_path
        }
    )
    actual = {item.relative_path: item.content_sha256 for item in scan.checks}
    if actual != expected:
        raise ValueError("cleanup outcome credential scan does not cover exact inventory")
    if (
        scan.policy_id != scan_policy.policy_id
        or scan.policy_sha256 != scan_policy.sha256()
        or scan.findings
    ):
        raise ValueError("cleanup outcome credential scan is not zero-finding and policy-bound")
    if scan.started_ts_ns < max(item.captured_ts_ns for item in targets):
        raise ValueError("cleanup outcome credential scan predates target evidence")


def _load_outcome_manifest(path: Path) -> CleanupOutcomeEvidenceManifest:
    payload = _read_regular(path, maximum_bytes=MAX_CONTROL_BYTES)
    manifest = CleanupOutcomeEvidenceManifest.model_validate_json(payload)
    if payload != manifest.canonical_bytes() + b"\n":
        raise ValueError("cleanup outcome manifest is not canonical JSON")
    return manifest


def _single_outcome_control(
    controls: tuple[CleanupOutcomeControl, ...],
    kind: CleanupOutcomeControlKind,
) -> CleanupOutcomeControl:
    matches = [item for item in controls if item.kind is kind]
    if len(matches) != 1:
        raise ValueError(f"cleanup outcome requires exactly one {kind.value} control")
    return matches[0]


def _load_bound_outcome_control[ModelT: DomainModel](
    root: Path,
    binding: CleanupOutcomeControl,
    model: type[ModelT],
) -> ModelT:
    value = _load_control(root / binding.relative_path, model)
    if hashlib.sha256(value.canonical_bytes() + b"\n").hexdigest() != binding.content_sha256:
        raise ValueError(f"cleanup outcome control hash differs: {binding.kind.value}")
    return value


def _validate_outcome_inventory(
    root: Path,
    bindings: dict[str, CleanupEvidenceArtifact | CleanupOutcomeControl],
) -> tuple[InventoryEntry, ...]:
    expected = {OUTCOME_MANIFEST_NAME, *bindings}
    expected_directories = {
        parent.as_posix()
        for value in expected
        for parent in PurePosixPath(value).parents
        if parent.as_posix() != "."
    }
    actual: dict[str, Path] = {}
    actual_directories: set[str] = set()
    total_bytes = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"cleanup outcome evidence cannot contain symlinks: {path.name}")
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            actual_directories.add(relative)
            continue
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"cleanup outcome contains a non-regular file: {path.name}")
        if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError(f"cleanup outcome evidence is group/world writable: {path.name}")
        actual[relative] = path
        total_bytes += metadata.st_size
    if len(actual) > MAX_BUNDLE_FILES or total_bytes > MAX_BUNDLE_BYTES:
        raise ValueError("cleanup outcome evidence exceeds hard resource bounds")
    if set(actual) != expected or actual_directories != expected_directories:
        raise ValueError("cleanup outcome evidence inventory is not exact")

    inventory: list[InventoryEntry] = []
    for relative_path, path in sorted(actual.items()):
        digest, identity = _hash_regular(path)
        binding = bindings.get(relative_path)
        if binding is not None and (
            binding.content_sha256 != digest or binding.byte_count != identity.byte_count
        ):
            raise ValueError(f"cleanup outcome digest or size differs: {relative_path}")
        inventory.append(
            InventoryEntry(
                relative_path=relative_path,
                content_sha256=digest,
                byte_count=identity.byte_count,
                identity=identity,
            )
        )
    return tuple(inventory)


def _passed_gate(gate: CleanupOutcomeGate, actual: str, required: str) -> CleanupOutcomeGateResult:
    return CleanupOutcomeGateResult(gate=gate, passed=True, actual=actual, required=required)
