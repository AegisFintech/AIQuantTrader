"""Evidence-derived assembly and replay of the exact legacy cleanup manifest."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from time import time_ns

from aiquanttrader_native.domain.base import DomainModel, canonical_sha256
from aiquanttrader_native.retirement.models import (
    CleanupCredentialScanEvidence,
    CleanupEvidenceArtifact,
    CleanupEvidenceControl,
    CleanupEvidenceControlKind,
    CleanupEvidenceManifest,
    CleanupHostState,
    CleanupInventoryAuditEvidence,
    CleanupPathInventoryEvidence,
    CleanupPathState,
    CleanupSecretState,
    CleanupTargetEvidence,
    CleanupTargetKind,
    DisabledObservationReport,
    LegacyArchiveCredentialScanPolicy,
    LegacyArchiveManifest,
    LegacyCleanupManifest,
    RetirementPolicy,
)

MANIFEST_NAME = "cleanup-evidence.json"
MAX_CONTROL_BYTES = 16_777_216
MAX_BUNDLE_FILES = 6_148
MAX_BUNDLE_BYTES = 1_099_511_627_776


@dataclass(frozen=True, slots=True)
class FileIdentity:
    device: int
    inode: int
    byte_count: int
    modified_ts_ns: int
    changed_ts_ns: int


@dataclass(frozen=True, slots=True)
class InventoryEntry:
    relative_path: str
    content_sha256: str
    byte_count: int
    identity: FileIdentity


@dataclass(frozen=True, slots=True)
class CleanupEvidenceReplay:
    """Verified cleanup evidence plus the typed state used to derive its manifest."""

    cleanup_manifest: LegacyCleanupManifest
    evidence_manifest: CleanupEvidenceManifest
    inventory_audit: CleanupInventoryAuditEvidence
    credential_scan: CleanupCredentialScanEvidence
    target_evidence: tuple[CleanupTargetEvidence, ...]

    def evidence_timestamps_ns(self) -> tuple[int, ...]:
        """Return every action-state capture/review boundary used by preflight."""

        return (
            self.evidence_manifest.created_ts_ns,
            self.inventory_audit.observed_ts_ns,
            self.inventory_audit.reviewed_ts_ns,
            self.credential_scan.started_ts_ns,
            self.credential_scan.ended_ts_ns,
            self.credential_scan.reviewed_ts_ns,
            *(item.captured_ts_ns for item in self.evidence_manifest.artifacts),
            *(item.captured_ts_ns for item in self.evidence_manifest.controls),
            *(item.state.captured_ts_ns for item in self.target_evidence),
        )


def assemble_cleanup_manifest(
    evidence_root: Path,
    disabled_report: DisabledObservationReport,
    archive_manifest: LegacyArchiveManifest,
    *,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
) -> LegacyCleanupManifest:
    """Assemble a cleanup manifest without stop, revoke, package, or delete capability."""

    return _assemble_cleanup_manifest(
        evidence_root,
        disabled_report,
        archive_manifest,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        assembled_ts_ns=time_ns(),
    )


def verify_cleanup_manifest(
    evidence_root: Path,
    cleanup_manifest: LegacyCleanupManifest,
    disabled_report: DisabledObservationReport,
    archive_manifest: LegacyArchiveManifest,
    *,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
) -> LegacyCleanupManifest:
    """Replay every cleanup evidence byte and compare the deterministic manifest."""

    verified_ts_ns = time_ns()
    if cleanup_manifest.created_ts_ns > verified_ts_ns:
        raise ValueError("cleanup manifest is dated after verification")
    assembled = _assemble_cleanup_manifest(
        evidence_root,
        disabled_report,
        archive_manifest,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        assembled_ts_ns=cleanup_manifest.created_ts_ns,
    )
    if assembled != cleanup_manifest:
        raise ValueError("cleanup manifest does not match its evidence root")
    return assembled


def load_cleanup_manifest(path: Path) -> LegacyCleanupManifest:
    payload = _read_regular(path, maximum_bytes=MAX_CONTROL_BYTES)
    manifest = LegacyCleanupManifest.model_validate_json(payload)
    if payload != manifest.canonical_bytes() + b"\n":
        raise ValueError("cleanup manifest is not canonical JSON")
    return manifest


def load_disabled_observation_report(path: Path) -> DisabledObservationReport:
    payload = _read_regular(path, maximum_bytes=MAX_CONTROL_BYTES)
    report = DisabledObservationReport.model_validate_json(payload)
    if payload != report.canonical_bytes() + b"\n":
        raise ValueError("disabled observation report is not canonical JSON")
    return report


def _assemble_cleanup_manifest(
    evidence_root: Path,
    disabled_report: DisabledObservationReport,
    archive_manifest: LegacyArchiveManifest,
    *,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    assembled_ts_ns: int,
) -> LegacyCleanupManifest:
    return _replay_cleanup_evidence(
        evidence_root,
        disabled_report,
        archive_manifest,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        assembled_ts_ns=assembled_ts_ns,
    ).cleanup_manifest


def _replay_cleanup_evidence(
    evidence_root: Path,
    disabled_report: DisabledObservationReport,
    archive_manifest: LegacyArchiveManifest,
    *,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    assembled_ts_ns: int,
) -> CleanupEvidenceReplay:
    root = _validated_root(evidence_root)
    evidence_manifest = _load_control(root / MANIFEST_NAME, CleanupEvidenceManifest)
    if evidence_manifest.created_ts_ns > assembled_ts_ns:
        raise ValueError("cleanup evidence is dated after assembly")

    bindings: dict[str, CleanupEvidenceArtifact | CleanupEvidenceControl] = {
        item.relative_path: item for item in evidence_manifest.artifacts
    }
    bindings.update({item.relative_path: item for item in evidence_manifest.controls})
    inventory = _validate_inventory(root, bindings)
    by_inventory_path = {item.relative_path: item for item in inventory}
    manifest_file_sha256 = hashlib.sha256(evidence_manifest.canonical_bytes() + b"\n").hexdigest()
    if by_inventory_path[MANIFEST_NAME].content_sha256 != manifest_file_sha256:
        raise ValueError("cleanup evidence manifest changed while loading")

    controls = evidence_manifest.controls
    inventory_binding = _single_control(controls, CleanupEvidenceControlKind.INVENTORY_AUDIT)
    scan_binding = _single_control(controls, CleanupEvidenceControlKind.CREDENTIAL_SCAN)
    target_bindings = sorted(
        (item for item in controls if item.kind is CleanupEvidenceControlKind.TARGET_STATE),
        key=lambda item: item.reference_id,
    )
    audit = _load_bound_control(root, inventory_binding, CleanupInventoryAuditEvidence)
    scan = _load_bound_control(root, scan_binding, CleanupCredentialScanEvidence)
    target_evidence = tuple(
        _load_bound_control(root, binding, CleanupTargetEvidence) for binding in target_bindings
    )

    _verify_lineage(
        evidence_manifest,
        audit,
        scan,
        target_bindings,
        target_evidence,
        disabled_report,
        archive_manifest,
        policy,
        credential_scan_policy,
        assembled_ts_ns,
    )
    path_inventories = _load_path_inventories(
        root,
        evidence_manifest,
        target_evidence,
    )
    _verify_scope_and_raw_evidence(
        evidence_manifest,
        audit,
        target_evidence,
        path_inventories,
    )
    _verify_credential_scan(
        evidence_manifest,
        scan_binding,
        scan,
        credential_scan_policy,
    )
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
    targets = tuple(item.cleanup_target() for item in target_evidence)
    cleanup_manifest = LegacyCleanupManifest(
        retirement_id=evidence_manifest.retirement_id,
        created_ts_ns=assembled_ts_ns,
        policy_id=policy.policy_id,
        policy_sha256=policy.sha256(),
        source_commit_sha=evidence_manifest.source_commit_sha,
        archive_manifest_sha256=archive_manifest.sha256(),
        disabled_observation_report_sha256=disabled_report.sha256(),
        evidence_manifest_sha256=evidence_manifest.sha256(),
        credential_scan_sha256=scan.sha256(),
        evidence_bundle_sha256=bundle_sha256,
        targets=targets,
    )
    return CleanupEvidenceReplay(
        cleanup_manifest=cleanup_manifest,
        evidence_manifest=evidence_manifest,
        inventory_audit=audit,
        credential_scan=scan,
        target_evidence=target_evidence,
    )


def _verify_lineage(
    manifest: CleanupEvidenceManifest,
    audit: CleanupInventoryAuditEvidence,
    scan: CleanupCredentialScanEvidence,
    target_bindings: list[CleanupEvidenceControl],
    target_evidence: tuple[CleanupTargetEvidence, ...],
    disabled_report: DisabledObservationReport,
    archive_manifest: LegacyArchiveManifest,
    policy: RetirementPolicy,
    scan_policy: LegacyArchiveCredentialScanPolicy,
    assembled_ts_ns: int,
) -> None:
    if not disabled_report.awaiting_cleanup_approval:
        raise ValueError("disabled observation report does not permit cleanup review")
    if (
        manifest.retirement_id != disabled_report.retirement_id
        or manifest.retirement_id != archive_manifest.retirement_id
        or audit.retirement_id != manifest.retirement_id
        or any(item.retirement_id != manifest.retirement_id for item in target_evidence)
        or scan.retirement_id != manifest.retirement_id
    ):
        raise ValueError("cleanup evidence retirement identity differs")
    if (
        manifest.policy_id != policy.policy_id
        or manifest.policy_sha256 != policy.sha256()
        or disabled_report.policy_id != policy.policy_id
        or disabled_report.policy_sha256 != policy.sha256()
    ):
        raise ValueError("cleanup evidence retirement policy differs")
    if (
        manifest.credential_scan_policy_id != scan_policy.policy_id
        or manifest.credential_scan_policy_sha256 != scan_policy.sha256()
        or scan.policy_id != scan_policy.policy_id
        or scan.policy_sha256 != scan_policy.sha256()
    ):
        raise ValueError("cleanup evidence credential scan policy differs")
    if (
        manifest.source_commit_sha != archive_manifest.source_commit_sha
        or audit.source_commit_sha != manifest.source_commit_sha
        or archive_manifest.final_tag_name != "mt5-final"
        or archive_manifest.final_tag_commit_sha != manifest.source_commit_sha
        or manifest.archive_manifest_sha256 != archive_manifest.sha256()
        or manifest.disabled_observation_report_sha256 != disabled_report.sha256()
        or disabled_report.archive_manifest_sha256 != archive_manifest.sha256()
    ):
        raise ValueError("cleanup evidence source or report lineage differs")
    if (
        archive_manifest.retention_expires_ts_ns - assembled_ts_ns
        < policy.minimum_archive_retention_ns
    ):
        raise ValueError("cleanup assembly lacks the required remaining archive retention")
    if (
        disabled_report.generated_ts_ns > manifest.created_ts_ns
        or audit.reviewed_ts_ns > manifest.created_ts_ns
        or scan.reviewed_ts_ns > manifest.created_ts_ns
        or any(item.state.captured_ts_ns > manifest.created_ts_ns for item in target_evidence)
    ):
        raise ValueError("cleanup source evidence postdates its manifest")
    if [item.reference_id for item in target_bindings] != [
        item.target_id for item in target_evidence
    ]:
        raise ValueError("cleanup target controls differ from their bound identities")


def _verify_scope_and_raw_evidence(
    manifest: CleanupEvidenceManifest,
    audit: CleanupInventoryAuditEvidence,
    targets: tuple[CleanupTargetEvidence, ...],
    path_inventories: dict[str, CleanupPathInventoryEvidence],
) -> None:
    target_ids = {item.target_id for item in targets}
    scoped_target_ids = {target_id for scope in audit.scopes for target_id in scope.target_ids}
    if scoped_target_ids != target_ids:
        raise ValueError("cleanup scope audit does not cover every target exactly once")
    artifacts = {item.artifact_id: item for item in manifest.artifacts}
    scope_artifact_ids = {
        artifact_id for scope in audit.scopes for artifact_id in scope.evidence_artifact_ids
    }
    if not scope_artifact_ids <= set(artifacts):
        raise ValueError("cleanup scope audit references unbound raw evidence")

    target_artifact_ids: set[str] = set()
    for target in targets:
        state_artifacts = target.state.artifact_ids()
        if any(item not in artifacts for item in state_artifacts):
            raise ValueError("cleanup target references unbound raw evidence")
        target_artifact_ids.update(state_artifacts)
        state = target.state
        artifact_hashes = tuple(artifacts[item].content_sha256 for item in state_artifacts)
        if isinstance(state, CleanupPathState):
            inventory = path_inventories.get(target.target_id)
            if inventory is None:
                raise ValueError("cleanup path target lacks a typed raw inventory")
            if (
                inventory.kind is not state.kind
                or inventory.locator != state.locator
                or inventory.captured_ts_ns != state.captured_ts_ns
                or inventory.state_sha256() != state.inventory_sha256
                or len(inventory.entries) != state.entry_count
                or inventory.total_bytes != state.total_bytes
                or (
                    state.kind is CleanupTargetKind.REPOSITORY_PATH
                    and inventory.source_commit_sha != manifest.source_commit_sha
                )
            ):
                raise ValueError("cleanup path inventory state differs from typed evidence")
        elif isinstance(state, CleanupHostState):
            if artifact_hashes != (state.configuration_sha256, state.ownership_sha256):
                raise ValueError("cleanup host state hashes differ from raw evidence")
        elif isinstance(state, CleanupSecretState) and artifact_hashes != (
            state.provider_state_sha256,
            state.active_sessions_sha256,
        ):
            raise ValueError("cleanup secret state hashes differ from raw evidence")
    if scope_artifact_ids | target_artifact_ids != set(artifacts):
        raise ValueError("cleanup evidence contains unreferenced raw artifacts")


def _load_path_inventories(
    root: Path,
    manifest: CleanupEvidenceManifest,
    targets: tuple[CleanupTargetEvidence, ...],
) -> dict[str, CleanupPathInventoryEvidence]:
    artifacts = {item.artifact_id: item for item in manifest.artifacts}
    inventories: dict[str, CleanupPathInventoryEvidence] = {}
    for target in targets:
        if not isinstance(target.state, CleanupPathState):
            continue
        artifact = artifacts.get(target.state.raw_artifact_id)
        if artifact is None:
            continue
        inventories[target.target_id] = _load_control(
            root / artifact.relative_path,
            CleanupPathInventoryEvidence,
        )
    return inventories


def _verify_credential_scan(
    manifest: CleanupEvidenceManifest,
    scan_binding: CleanupEvidenceControl,
    scan: CleanupCredentialScanEvidence,
    scan_policy: LegacyArchiveCredentialScanPolicy,
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
        raise ValueError("cleanup credential scan does not cover the exact evidence inventory")
    if (
        scan.policy_id != scan_policy.policy_id
        or scan.policy_sha256 != scan_policy.sha256()
        or scan.findings
    ):
        raise ValueError("cleanup credential scan is not a zero-finding policy-bound scan")


def _single_control(
    controls: tuple[CleanupEvidenceControl, ...], kind: CleanupEvidenceControlKind
) -> CleanupEvidenceControl:
    matches = [item for item in controls if item.kind is kind]
    if len(matches) != 1:
        raise ValueError(f"cleanup evidence requires exactly one {kind.value} control")
    return matches[0]


def _validated_root(root: Path) -> Path:
    if not root.is_absolute():
        raise ValueError("cleanup evidence root must be absolute")
    if root.is_symlink():
        raise ValueError("cleanup evidence root must be a non-symlink directory")
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("cleanup evidence root must be a non-symlink directory")
    return resolved


def _validate_inventory(
    root: Path,
    bindings: dict[str, CleanupEvidenceArtifact | CleanupEvidenceControl],
) -> tuple[InventoryEntry, ...]:
    expected = {MANIFEST_NAME, *bindings}
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
            raise ValueError(f"cleanup evidence cannot contain symlinks: {path.name}")
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            actual_directories.add(relative)
            continue
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"cleanup evidence contains a non-regular file: {path.name}")
        if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError(f"cleanup evidence is group/world writable: {path.name}")
        actual[relative] = path
        total_bytes += metadata.st_size
    if len(actual) > MAX_BUNDLE_FILES or total_bytes > MAX_BUNDLE_BYTES:
        raise ValueError("cleanup evidence exceeds its hard resource bounds")
    if set(actual) != expected or actual_directories != expected_directories:
        raise ValueError("cleanup evidence inventory is not exact")

    inventory: list[InventoryEntry] = []
    for relative_path, path in sorted(actual.items()):
        digest, identity = _hash_regular(path)
        binding = bindings.get(relative_path)
        if binding is not None and (
            binding.content_sha256 != digest or binding.byte_count != identity.byte_count
        ):
            raise ValueError(f"cleanup evidence digest or size differs: {relative_path}")
        inventory.append(
            InventoryEntry(
                relative_path=relative_path,
                content_sha256=digest,
                byte_count=identity.byte_count,
                identity=identity,
            )
        )
    return tuple(inventory)


def _load_bound_control[ModelT: DomainModel](
    root: Path,
    binding: CleanupEvidenceControl,
    model: type[ModelT],
) -> ModelT:
    value = _load_control(root / binding.relative_path, model)
    if hashlib.sha256(value.canonical_bytes() + b"\n").hexdigest() != binding.content_sha256:
        raise ValueError(f"cleanup control hash differs: {binding.kind.value}")
    return value


def _load_control[ModelT: DomainModel](path: Path, model: type[ModelT]) -> ModelT:
    payload = _read_regular(path, maximum_bytes=MAX_CONTROL_BYTES)
    value = model.model_validate_json(payload)
    if payload != value.canonical_bytes() + b"\n":
        raise ValueError(f"cleanup evidence control is not canonical JSON: {path.name}")
    return value


def _read_regular(path: Path, *, maximum_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot open cleanup evidence artifact: {path.name}") from exc
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode):
            raise ValueError("cleanup evidence artifact must be a regular file")
        if initial.st_size <= 0 or initial.st_size > maximum_bytes:
            raise ValueError("cleanup evidence artifact size is invalid")
        payload = bytearray()
        remaining = initial.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1_048_576))
            if not chunk:
                break
            payload.extend(chunk)
            remaining -= len(chunk)
        final = os.fstat(descriptor)
        if len(payload) != initial.st_size or _identity(final) != _identity(initial):
            raise ValueError("cleanup evidence artifact changed while read")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _hash_regular(path: Path) -> tuple[str, FileIdentity]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot open cleanup evidence artifact: {path.name}") from exc
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode):
            raise ValueError("cleanup evidence artifact must be a regular file")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1_048_576)
            if not chunk:
                break
            digest.update(chunk)
        final = os.fstat(descriptor)
        if _identity(final) != _identity(initial):
            raise ValueError("cleanup evidence artifact changed while hashing")
        return digest.hexdigest(), _file_identity(final)
    finally:
        os.close(descriptor)


def _assert_inventory_unchanged(root: Path, inventory: tuple[InventoryEntry, ...]) -> None:
    for item in inventory:
        digest, identity = _hash_regular(root / item.relative_path)
        if digest != item.content_sha256 or identity != item.identity:
            raise ValueError("cleanup evidence changed during verification")


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _file_identity(metadata: os.stat_result) -> FileIdentity:
    return FileIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        byte_count=metadata.st_size,
        modified_ts_ns=metadata.st_mtime_ns,
        changed_ts_ns=metadata.st_ctime_ns,
    )
