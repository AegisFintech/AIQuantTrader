"""Credential-free assembly of the retained final legacy archive."""

from __future__ import annotations

import hashlib
import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from time import time_ns

from aiquanttrader.domain.base import DomainModel, canonical_sha256
from aiquanttrader.retirement.models import (
    LegacyArchiveArtifact,
    LegacyArchiveArtifactKind,
    LegacyArchiveControlArtifact,
    LegacyArchiveControlKind,
    LegacyArchiveCredentialScanEvidence,
    LegacyArchiveCredentialScanPolicy,
    LegacyArchiveEvidenceManifest,
    LegacyArchiveManifest,
    LegacyArchiveRestoreEvidence,
    LegacyFinalTagEvidence,
    RetirementPolicy,
)

MANIFEST_NAME = "legacy-archive-evidence.json"
MAX_MANIFEST_BYTES = 1_048_576
MAX_CONTROL_BYTES = 16_777_216
MAX_BUNDLE_FILES = 15
MAX_BUNDLE_BYTES = 13_194_139_533_312


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


def load_legacy_archive_credential_scan_policy(
    path: Path,
) -> LegacyArchiveCredentialScanPolicy:
    if path.is_symlink():
        raise ValueError("legacy archive credential scan policy path is invalid")
    resolved = path.resolve(strict=True)
    metadata = resolved.stat()
    if not resolved.is_file() or metadata.st_size <= 0 or metadata.st_size > MAX_MANIFEST_BYTES:
        raise ValueError("legacy archive credential scan policy path is invalid")
    with resolved.open("rb") as handle:
        return LegacyArchiveCredentialScanPolicy.model_validate(tomllib.load(handle))


def assemble_legacy_archive_manifest(
    root: Path,
    *,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
) -> LegacyArchiveManifest:
    """Verify one immutable final archive without broker, Git, or action capability."""

    return _assemble_legacy_archive_manifest(
        root,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        assembled_ts_ns=time_ns(),
    )


def _assemble_legacy_archive_manifest(
    root: Path,
    *,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
    assembled_ts_ns: int,
) -> LegacyArchiveManifest:
    evidence_root = _validated_root(root)
    evidence = _load_control(
        evidence_root / MANIFEST_NAME,
        LegacyArchiveEvidenceManifest,
        maximum_bytes=MAX_MANIFEST_BYTES,
    )
    if evidence.created_ts_ns > assembled_ts_ns:
        raise ValueError("legacy archive evidence is dated after assembly")
    _verify_policy_binding(policy, credential_scan_policy)
    if evidence.retention_expires_ts_ns - assembled_ts_ns < policy.minimum_archive_retention_ns:
        raise ValueError("legacy archive has insufficient remaining retention")

    bindings: dict[str, LegacyArchiveArtifact | LegacyArchiveControlArtifact] = {
        item.relative_path: item for item in evidence.artifacts
    }
    bindings.update({item.relative_path: item for item in evidence.controls})
    inventory = _validate_inventory(evidence_root, bindings)
    inventory_by_path = {item.relative_path: item for item in inventory}
    expected_manifest_sha256 = hashlib.sha256(evidence.canonical_bytes() + b"\n").hexdigest()
    if inventory_by_path[MANIFEST_NAME].content_sha256 != expected_manifest_sha256:
        raise ValueError("legacy archive evidence manifest changed while loading")

    artifacts = {item.kind: item for item in evidence.artifacts}
    controls = {item.kind: item for item in evidence.controls}
    restore = _load_bound_control(
        evidence_root,
        controls[LegacyArchiveControlKind.RESTORE_EVIDENCE],
        LegacyArchiveRestoreEvidence,
    )
    credential_scan = _load_bound_control(
        evidence_root,
        controls[LegacyArchiveControlKind.CREDENTIAL_SCAN_EVIDENCE],
        LegacyArchiveCredentialScanEvidence,
    )
    final_tag = _load_bound_control(
        evidence_root,
        controls[LegacyArchiveControlKind.FINAL_TAG_EVIDENCE],
        LegacyFinalTagEvidence,
    )
    _verify_restore_evidence(evidence, artifacts, controls, restore)
    _verify_credential_scan_evidence(
        evidence,
        artifacts,
        controls,
        credential_scan,
        credential_scan_policy,
    )
    _verify_final_tag_evidence(evidence, controls, final_tag)
    _assert_inventory_unchanged(evidence_root, inventory)

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
    return LegacyArchiveManifest(
        retirement_id=evidence.retirement_id,
        created_ts_ns=evidence.created_ts_ns,
        assembled_ts_ns=assembled_ts_ns,
        retention_expires_ts_ns=evidence.retention_expires_ts_ns,
        source_commit_sha=evidence.source_commit_sha,
        final_tag_name=evidence.final_tag_name,
        final_tag_commit_sha=evidence.final_tag_commit_sha,
        credential_scan_policy_id=credential_scan_policy.policy_id,
        credential_scan_policy_sha256=credential_scan_policy.sha256(),
        evidence_manifest_sha256=evidence.sha256(),
        evidence_bundle_sha256=bundle_sha256,
        restore_evidence_sha256=restore.sha256(),
        final_tag_evidence_sha256=final_tag.sha256(),
        artifacts=evidence.artifacts,
    )


def verify_legacy_archive_manifest(
    root: Path,
    manifest: LegacyArchiveManifest,
    *,
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
) -> LegacyArchiveManifest:
    verified_ts_ns = time_ns()
    if manifest.assembled_ts_ns > verified_ts_ns:
        raise ValueError("legacy archive manifest is dated after verification")
    if manifest.retention_expires_ts_ns - verified_ts_ns < policy.minimum_archive_retention_ns:
        raise ValueError("legacy archive has insufficient retention at verification")
    assembled = _assemble_legacy_archive_manifest(
        root,
        policy=policy,
        credential_scan_policy=credential_scan_policy,
        assembled_ts_ns=manifest.assembled_ts_ns,
    )
    if assembled != manifest:
        raise ValueError("legacy archive manifest does not match its evidence bundle")
    return assembled


def load_legacy_archive_manifest(path: Path) -> LegacyArchiveManifest:
    payload = _read_regular(path, maximum_bytes=MAX_MANIFEST_BYTES)
    manifest = LegacyArchiveManifest.model_validate_json(payload)
    if payload != manifest.canonical_bytes() + b"\n":
        raise ValueError("legacy archive manifest is not canonical JSON")
    return manifest


def _verify_policy_binding(
    policy: RetirementPolicy,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
) -> None:
    if policy.archive_credential_scan_policy_id != credential_scan_policy.policy_id:
        raise ValueError("legacy credential scan policy identity differs from retirement policy")
    if policy.archive_credential_scan_policy_sha256 != credential_scan_policy.sha256():
        raise ValueError("legacy credential scan policy hash differs from retirement policy")


def _verify_restore_evidence(
    evidence: LegacyArchiveEvidenceManifest,
    artifacts: dict[LegacyArchiveArtifactKind, LegacyArchiveArtifact],
    controls: dict[LegacyArchiveControlKind, LegacyArchiveControlArtifact],
    restore: LegacyArchiveRestoreEvidence,
) -> None:
    control = controls[LegacyArchiveControlKind.RESTORE_EVIDENCE]
    if restore.retirement_id != evidence.retirement_id:
        raise ValueError("legacy archive restore retirement identity differs")
    if (
        restore.started_ts_ns < max(item.captured_ts_ns for item in artifacts.values())
        or restore.reviewed_ts_ns > evidence.created_ts_ns
        or control.captured_ts_ns < restore.reviewed_ts_ns
    ):
        raise ValueError("legacy archive restore timing is inconsistent")
    if not restore.passed:
        raise ValueError("legacy archive restore contains invalidating events")
    checks = {item.kind: item for item in restore.checks}
    for kind, artifact in artifacts.items():
        check = checks[kind]
        if (
            check.source_sha256 != artifact.content_sha256
            or check.source_byte_count != artifact.byte_count
        ):
            raise ValueError(f"legacy archive restore differs from category: {kind.value}")


def _verify_credential_scan_evidence(
    evidence: LegacyArchiveEvidenceManifest,
    artifacts: dict[LegacyArchiveArtifactKind, LegacyArchiveArtifact],
    controls: dict[LegacyArchiveControlKind, LegacyArchiveControlArtifact],
    credential_scan: LegacyArchiveCredentialScanEvidence,
    credential_scan_policy: LegacyArchiveCredentialScanPolicy,
) -> None:
    control = controls[LegacyArchiveControlKind.CREDENTIAL_SCAN_EVIDENCE]
    if credential_scan.retirement_id != evidence.retirement_id:
        raise ValueError("legacy archive credential scan retirement identity differs")
    if (
        credential_scan.policy_id != credential_scan_policy.policy_id
        or credential_scan.policy_sha256 != credential_scan_policy.sha256()
    ):
        raise ValueError("legacy archive credential scan used a different frozen policy")
    if (
        credential_scan.started_ts_ns < max(item.captured_ts_ns for item in artifacts.values())
        or credential_scan.reviewed_ts_ns > evidence.created_ts_ns
        or control.captured_ts_ns < credential_scan.reviewed_ts_ns
    ):
        raise ValueError("legacy archive credential scan timing is inconsistent")
    checks = {item.kind: item for item in credential_scan.checks}
    for kind, artifact in artifacts.items():
        if checks[kind].artifact_sha256 != artifact.content_sha256:
            raise ValueError(f"legacy credential scan differs from category: {kind.value}")


def _verify_final_tag_evidence(
    evidence: LegacyArchiveEvidenceManifest,
    controls: dict[LegacyArchiveControlKind, LegacyArchiveControlArtifact],
    final_tag: LegacyFinalTagEvidence,
) -> None:
    control = controls[LegacyArchiveControlKind.FINAL_TAG_EVIDENCE]
    if (
        final_tag.retirement_id != evidence.retirement_id
        or final_tag.source_commit_sha != evidence.source_commit_sha
        or final_tag.final_tag_name != evidence.final_tag_name
        or final_tag.final_tag_commit_sha != evidence.final_tag_commit_sha
    ):
        raise ValueError("retained final tag evidence differs from archive identity")
    if (
        final_tag.captured_ts_ns > evidence.created_ts_ns
        or control.captured_ts_ns < final_tag.captured_ts_ns
    ):
        raise ValueError("retained final tag evidence timing is inconsistent")


def _validated_root(root: Path) -> Path:
    if not root.is_absolute():
        raise ValueError("legacy archive evidence root must be absolute")
    if root.is_symlink():
        raise ValueError("legacy archive evidence root must be a non-symlink directory")
    resolved = root.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("legacy archive evidence root must be a non-symlink directory")
    return resolved


def _validate_inventory(
    root: Path,
    bindings: dict[str, LegacyArchiveArtifact | LegacyArchiveControlArtifact],
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
            raise ValueError(f"legacy archive evidence cannot contain symlinks: {path.name}")
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            actual_directories.add(relative)
            continue
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"legacy archive evidence contains a non-regular file: {path.name}")
        if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ValueError(f"legacy archive evidence is group/world writable: {path.name}")
        actual[relative] = path
        total_bytes += metadata.st_size
    if len(actual) > MAX_BUNDLE_FILES or total_bytes > MAX_BUNDLE_BYTES:
        raise ValueError("legacy archive evidence exceeds its hard resource bounds")
    if set(actual) != expected or actual_directories != expected_directories:
        raise ValueError("legacy archive evidence inventory is not exact")

    inventory: list[InventoryEntry] = []
    for relative_path, path in sorted(actual.items()):
        digest, identity = _hash_regular(path)
        binding = bindings.get(relative_path)
        if binding is not None and (
            binding.content_sha256 != digest or binding.byte_count != identity.byte_count
        ):
            raise ValueError(f"legacy archive evidence digest or size differs: {relative_path}")
        inventory.append(
            InventoryEntry(
                relative_path=relative_path,
                content_sha256=digest,
                byte_count=identity.byte_count,
                identity=identity,
            )
        )
    return tuple(inventory)


def _assert_inventory_unchanged(root: Path, inventory: tuple[InventoryEntry, ...]) -> None:
    expected = {item.relative_path: item.identity for item in inventory}
    expected_directories = {
        parent.as_posix()
        for value in expected
        for parent in PurePosixPath(value).parents
        if parent.as_posix() != "."
    }
    actual_paths: dict[str, Path] = {}
    actual_directories: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("legacy archive evidence changed during assembly")
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            actual_directories.add(relative)
        else:
            actual_paths[relative] = path
    if set(actual_paths) != set(expected) or actual_directories != expected_directories:
        raise ValueError("legacy archive evidence inventory changed during assembly")
    for relative_path, path in actual_paths.items():
        if _identity(path.stat()) != expected[relative_path]:
            raise ValueError("legacy archive evidence changed during assembly")


def _load_bound_control[ModelT: DomainModel](
    root: Path,
    binding: LegacyArchiveControlArtifact,
    model: type[ModelT],
) -> ModelT:
    value = _load_control(root / binding.relative_path, model, maximum_bytes=MAX_CONTROL_BYTES)
    if hashlib.sha256(value.canonical_bytes() + b"\n").hexdigest() != binding.content_sha256:
        raise ValueError(f"legacy archive control binding differs: {binding.kind.value}")
    return value


def _load_control[ModelT: DomainModel](
    path: Path,
    model: type[ModelT],
    *,
    maximum_bytes: int,
) -> ModelT:
    payload = _read_regular(path, maximum_bytes=maximum_bytes)
    value = model.model_validate_json(payload)
    if payload != value.canonical_bytes() + b"\n":
        raise ValueError(f"legacy archive control is not canonical JSON: {path.name}")
    return value


def _read_regular(path: Path, *, maximum_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot open legacy archive evidence: {path.name}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"legacy archive evidence is not regular: {path.name}")
        if metadata.st_size <= 0 or metadata.st_size > maximum_bytes:
            raise ValueError(f"legacy archive evidence size is invalid: {path.name}")
        payload = bytearray()
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 1_048_576))
            if not chunk:
                break
            payload.extend(chunk)
            remaining -= len(chunk)
        final = os.fstat(descriptor)
        if len(payload) != metadata.st_size or _identity(final) != _identity(metadata):
            raise ValueError(f"legacy archive evidence changed while read: {path.name}")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _hash_regular(path: Path) -> tuple[str, FileIdentity]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot hash legacy archive evidence: {path.name}") from exc
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode) or initial.st_size <= 0:
            raise ValueError(f"legacy archive evidence is not a non-empty file: {path.name}")
        digest = hashlib.sha256()
        observed = 0
        while True:
            chunk = os.read(descriptor, 1_048_576)
            if not chunk:
                break
            digest.update(chunk)
            observed += len(chunk)
        final = os.fstat(descriptor)
        if observed != initial.st_size or _identity(final) != _identity(initial):
            raise ValueError(f"legacy archive evidence changed while hashed: {path.name}")
        return digest.hexdigest(), _identity(initial)
    finally:
        os.close(descriptor)


def _identity(metadata: os.stat_result) -> FileIdentity:
    return FileIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        byte_count=metadata.st_size,
        modified_ts_ns=metadata.st_mtime_ns,
        changed_ts_ns=metadata.st_ctime_ns,
    )
