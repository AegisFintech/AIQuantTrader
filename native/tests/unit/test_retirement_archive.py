from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

import aiquanttrader_native.retirement.archive as archive_module
from aiquanttrader_native.retirement.archive import (
    assemble_legacy_archive_manifest,
    load_legacy_archive_credential_scan_policy,
    load_legacy_archive_manifest,
    verify_legacy_archive_manifest,
)
from aiquanttrader_native.retirement.cli import main as retirement_main
from aiquanttrader_native.retirement.evidence import load_retirement_policy
from aiquanttrader_native.retirement.models import (
    LegacyArchiveArtifact,
    LegacyArchiveArtifactKind,
    LegacyArchiveControlArtifact,
    LegacyArchiveControlKind,
    LegacyArchiveCredentialScanCheck,
    LegacyArchiveCredentialScanEvidence,
    LegacyArchiveCredentialScanPolicy,
    LegacyArchiveEvidenceManifest,
    LegacyArchiveRestoreCheck,
    LegacyArchiveRestoreEvidence,
    LegacyCapability,
    LegacyCredentialDetector,
    LegacyFinalTagEvidence,
    RequiredNativeDrill,
    RetirementPolicy,
)

BASE = datetime(2026, 7, 1, tzinfo=UTC)
CREATED = BASE + timedelta(days=2)
ASSEMBLED = CREATED + timedelta(hours=1)
RETENTION_EXPIRES = ASSEMBLED + timedelta(days=400)
COMMIT = "1" * 40
TAG_OBJECT = "2" * 40


def _ts(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def _scan_policy() -> LegacyArchiveCredentialScanPolicy:
    return LegacyArchiveCredentialScanPolicy(
        policy_id="legacy-archive-scan-test",
        required_detectors=tuple(LegacyCredentialDetector),
    )


def _policy() -> RetirementPolicy:
    scan = _scan_policy()
    return RetirementPolicy(
        policy_id="legacy-archive-retirement-test",
        frozen_at_ns=_ts(BASE - timedelta(days=1)),
        minimum_native_production_observation_ns=1,
        maximum_native_operational_gap_ns=1,
        minimum_disabled_observation_ns=1,
        minimum_archive_retention_ns=int(timedelta(days=365).total_seconds() * 1_000_000_000),
        maximum_final_state_capture_skew_ns=1,
        maximum_final_state_age_ns=1,
        archive_credential_scan_policy_id=scan.policy_id,
        archive_credential_scan_policy_sha256=scan.sha256(),
        required_archive_artifacts=tuple(LegacyArchiveArtifactKind),
        required_disabled_capabilities=tuple(LegacyCapability),
        required_native_drills=tuple(RequiredNativeDrill),
    )


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(archive_module, "time_ns", lambda: _ts(ASSEMBLED))


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _artifact(root: Path, kind: LegacyArchiveArtifactKind, index: int) -> LegacyArchiveArtifact:
    relative_path = f"artifacts/{kind.value}.tar.zst"
    payload = f"retained legacy category {kind.value}\n".encode()
    _write(root / relative_path, payload)
    return LegacyArchiveArtifact(
        kind=kind,
        relative_path=relative_path,
        content_sha256=hashlib.sha256(payload).hexdigest(),
        byte_count=len(payload),
        captured_ts_ns=_ts(BASE + timedelta(minutes=index)),
    )


def _control(
    root: Path,
    kind: LegacyArchiveControlKind,
    relative_path: str,
    value: LegacyArchiveRestoreEvidence
    | LegacyArchiveCredentialScanEvidence
    | LegacyFinalTagEvidence,
) -> LegacyArchiveControlArtifact:
    payload = value.canonical_bytes() + b"\n"
    _write(root / relative_path, payload)
    return LegacyArchiveControlArtifact(
        kind=kind,
        relative_path=relative_path,
        content_sha256=hashlib.sha256(payload).hexdigest(),
        byte_count=len(payload),
        captured_ts_ns=value.reviewed_ts_ns
        if isinstance(value, (LegacyArchiveRestoreEvidence, LegacyArchiveCredentialScanEvidence))
        else value.captured_ts_ns,
    )


def _bundle(tmp_path: Path) -> tuple[Path, LegacyArchiveEvidenceManifest]:
    root = (tmp_path / "legacy-archive").resolve()
    root.mkdir(parents=True)
    artifacts = tuple(
        _artifact(root, kind, index)
        for index, kind in enumerate(LegacyArchiveArtifactKind, start=1)
    )
    last_capture = max(item.captured_ts_ns for item in artifacts)
    restore = LegacyArchiveRestoreEvidence(
        retirement_id="legacy-retirement-001",
        started_ts_ns=last_capture + 1,
        ended_ts_ns=last_capture + 2,
        reviewed_ts_ns=last_capture + 3,
        reviewer="independent-restore-reviewer",
        checks=tuple(
            LegacyArchiveRestoreCheck(
                kind=item.kind,
                source_sha256=item.content_sha256,
                source_byte_count=item.byte_count,
                restored_sha256=item.content_sha256,
                restored_byte_count=item.byte_count,
            )
            for item in artifacts
        ),
    )
    scan_policy = _scan_policy()
    credential_scan = LegacyArchiveCredentialScanEvidence(
        retirement_id="legacy-retirement-001",
        started_ts_ns=last_capture + 4,
        ended_ts_ns=last_capture + 5,
        reviewed_ts_ns=last_capture + 6,
        reviewer="independent-security-reviewer",
        scanner_name="approved-offline-scanner",
        scanner_version="1.0.0",
        policy_id=scan_policy.policy_id,
        policy_sha256=scan_policy.sha256(),
        checks=tuple(
            LegacyArchiveCredentialScanCheck(
                kind=item.kind,
                artifact_sha256=item.content_sha256,
            )
            for item in artifacts
        ),
    )
    final_tag = LegacyFinalTagEvidence(
        retirement_id="legacy-retirement-001",
        captured_ts_ns=last_capture + 7,
        source_commit_sha=COMMIT,
        final_tag_commit_sha=COMMIT,
        tag_object_sha=TAG_OBJECT,
        verification_output_sha256="3" * 64,
        reviewer="independent-release-reviewer",
    )
    controls = (
        _control(
            root,
            LegacyArchiveControlKind.RESTORE_EVIDENCE,
            "controls/restore-evidence.json",
            restore,
        ),
        _control(
            root,
            LegacyArchiveControlKind.CREDENTIAL_SCAN_EVIDENCE,
            "controls/credential-scan-evidence.json",
            credential_scan,
        ),
        _control(
            root,
            LegacyArchiveControlKind.FINAL_TAG_EVIDENCE,
            "controls/final-tag-evidence.json",
            final_tag,
        ),
    )
    evidence = LegacyArchiveEvidenceManifest(
        retirement_id="legacy-retirement-001",
        created_ts_ns=_ts(CREATED),
        retention_expires_ts_ns=_ts(RETENTION_EXPIRES),
        source_commit_sha=COMMIT,
        final_tag_commit_sha=COMMIT,
        artifacts=artifacts,
        controls=controls,
    )
    _write(root / "legacy-archive-evidence.json", evidence.canonical_bytes() + b"\n")
    return root, evidence


def _rewrite_evidence_binding(root: Path, relative_path: str) -> None:
    evidence_path = root / "legacy-archive-evidence.json"
    evidence = LegacyArchiveEvidenceManifest.model_validate_json(evidence_path.read_bytes())
    payload = (root / relative_path).read_bytes()
    update = {
        "content_sha256": hashlib.sha256(payload).hexdigest(),
        "byte_count": len(payload),
    }
    artifacts = tuple(
        item.model_copy(update=update) if item.relative_path == relative_path else item
        for item in evidence.artifacts
    )
    controls = tuple(
        item.model_copy(update=update) if item.relative_path == relative_path else item
        for item in evidence.controls
    )
    changed = evidence.model_copy(update={"artifacts": artifacts, "controls": controls})
    evidence_path.write_bytes(changed.canonical_bytes() + b"\n")


def _replace_control(
    root: Path,
    relative_path: str,
    value: LegacyArchiveRestoreEvidence
    | LegacyArchiveCredentialScanEvidence
    | LegacyFinalTagEvidence,
) -> None:
    (root / relative_path).write_bytes(value.canonical_bytes() + b"\n")
    _rewrite_evidence_binding(root, relative_path)


def _write_policy_files(tmp_path: Path) -> tuple[Path, Path]:
    scan = _scan_policy()
    scan_path = (tmp_path / "scan-policy.toml").resolve()
    scan_path.write_text(
        "\n".join(
            (
                "schema_version = 1",
                f'policy_id = "{scan.policy_id}"',
                "recursive_archive_scan = true",
                "maximum_findings = 0",
                "required_detectors = ["
                + ",".join(f'"{item.value}"' for item in LegacyCredentialDetector)
                + "]",
            )
        ),
        encoding="utf-8",
    )
    policy = _policy()
    policy_path = (tmp_path / "retirement-policy.toml").resolve()
    policy_path.write_text(
        "\n".join(
            (
                "schema_version = 1",
                f'policy_id = "{policy.policy_id}"',
                f"frozen_at_ns = {policy.frozen_at_ns}",
                "minimum_native_production_observation_ns = 1",
                "maximum_native_operational_gap_ns = 1",
                "minimum_disabled_observation_ns = 1",
                f"minimum_archive_retention_ns = {policy.minimum_archive_retention_ns}",
                "maximum_final_state_capture_skew_ns = 1",
                "maximum_final_state_age_ns = 1",
                f'archive_credential_scan_policy_id = "{scan.policy_id}"',
                f'archive_credential_scan_policy_sha256 = "{scan.sha256()}"',
                "required_archive_artifacts = ["
                + ",".join(f'"{item.value}"' for item in LegacyArchiveArtifactKind)
                + "]",
                "required_disabled_capabilities = ["
                + ",".join(f'"{item.value}"' for item in LegacyCapability)
                + "]",
                "required_native_drills = ["
                + ",".join(f'"{item.value}"' for item in RequiredNativeDrill)
                + "]",
            )
        ),
        encoding="utf-8",
    )
    return policy_path, scan_path


def test_archive_assembler_verifies_restore_scan_tag_and_cli(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, evidence = _bundle(tmp_path)
    manifest = assemble_legacy_archive_manifest(
        root,
        policy=_policy(),
        credential_scan_policy=_scan_policy(),
    )
    assert manifest.retirement_id == evidence.retirement_id
    assert manifest.source_commit_sha == COMMIT
    assert manifest.final_tag_commit_sha == COMMIT
    assert manifest.assembled_ts_ns == _ts(ASSEMBLED)
    assert manifest.contains_credentials is False
    assert manifest.restore_test_passed is True
    assert len(manifest.artifacts) == len(LegacyArchiveArtifactKind)
    assert (
        verify_legacy_archive_manifest(
            root,
            manifest,
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
        )
        == manifest
    )

    policy_path, scan_path = _write_policy_files(tmp_path)
    assert load_legacy_archive_credential_scan_policy(scan_path) == _scan_policy()
    output = (tmp_path / "legacy-archive-manifest.json").resolve()
    assert (
        retirement_main(
            [
                "assemble-archive",
                "--evidence-root",
                str(root),
                "--policy",
                str(policy_path),
                "--credential-scan-policy",
                str(scan_path),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert load_legacy_archive_manifest(output) == manifest
    assert (
        retirement_main(
            [
                "verify-archive",
                "--evidence-root",
                str(root),
                "--manifest",
                str(output),
                "--policy",
                str(policy_path),
                "--credential-scan-policy",
                str(scan_path),
            ]
        )
        == 0
    )
    assert manifest.evidence_bundle_sha256 in capsys.readouterr().out


def test_archive_assembler_rejects_artifact_tampering(tmp_path: Path) -> None:
    root, _evidence = _bundle(tmp_path)
    target = root / "artifacts/final_trade_report.tar.zst"
    target.write_bytes(b"tampered archive category\n")
    with pytest.raises(ValueError, match="digest or size differs"):
        assemble_legacy_archive_manifest(
            root,
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
        )


@pytest.mark.parametrize("mutation", ("restore", "scan", "tag"))
def test_archive_assembler_rejects_control_mismatch(
    tmp_path: Path,
    mutation: str,
) -> None:
    root, evidence = _bundle(tmp_path)
    changed: (
        LegacyArchiveRestoreEvidence | LegacyArchiveCredentialScanEvidence | LegacyFinalTagEvidence
    )
    if mutation == "restore":
        relative = "controls/restore-evidence.json"
        restore = LegacyArchiveRestoreEvidence.model_validate_json((root / relative).read_bytes())
        first = restore.checks[0]
        different = "f" * 64
        changed = restore.model_copy(
            update={
                "checks": (
                    first.model_copy(
                        update={"source_sha256": different, "restored_sha256": different}
                    ),
                    *restore.checks[1:],
                )
            }
        )
        expected = "restore differs from category"
    elif mutation == "scan":
        relative = "controls/credential-scan-evidence.json"
        scan = LegacyArchiveCredentialScanEvidence.model_validate_json(
            (root / relative).read_bytes()
        )
        changed = scan.model_copy(
            update={
                "checks": (
                    scan.checks[0].model_copy(update={"artifact_sha256": "f" * 64}),
                    *scan.checks[1:],
                )
            }
        )
        expected = "scan differs from category"
    else:
        relative = "controls/final-tag-evidence.json"
        tag = LegacyFinalTagEvidence.model_validate_json((root / relative).read_bytes())
        changed = tag.model_copy(
            update={"source_commit_sha": "f" * 40, "final_tag_commit_sha": "f" * 40}
        )
        expected = "final tag evidence differs"
    _replace_control(root, relative, changed)
    with pytest.raises(ValueError, match=expected):
        assemble_legacy_archive_manifest(
            root,
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
        )
    assert evidence.retirement_id == "legacy-retirement-001"


def test_archive_assembler_requires_externally_frozen_scan_policy(tmp_path: Path) -> None:
    root, _evidence = _bundle(tmp_path)
    changed_scan = _scan_policy().model_copy(update={"policy_id": "different-scan-policy"})
    with pytest.raises(ValueError, match="identity differs"):
        assemble_legacy_archive_manifest(
            root,
            policy=_policy(),
            credential_scan_policy=changed_scan,
        )
    with pytest.raises(ValueError, match="hash differs"):
        assemble_legacy_archive_manifest(
            root,
            policy=_policy().model_copy(update={"archive_credential_scan_policy_sha256": "f" * 64}),
            credential_scan_policy=_scan_policy(),
        )


@pytest.mark.parametrize("mutation", ("extra", "symlink", "writable", "directory"))
def test_archive_inventory_is_exact_and_immutable(
    tmp_path: Path,
    mutation: str,
) -> None:
    root, _evidence = _bundle(tmp_path)
    if mutation == "extra":
        _write(root / "controls/unbound.txt", b"extra\n")
        expected = "hard resource bounds"
    elif mutation == "symlink":
        (root / "controls/unbound-link").symlink_to(root / "controls/final-tag-evidence.json")
        expected = "cannot contain symlinks"
    elif mutation == "writable":
        target = root / "controls/final-tag-evidence.json"
        target.chmod(target.stat().st_mode | 0o020)
        expected = "group/world writable"
    else:
        (root / "unbound-directory").mkdir()
        expected = "inventory is not exact"
    with pytest.raises(ValueError, match=expected):
        assemble_legacy_archive_manifest(
            root,
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
        )


@pytest.mark.parametrize("mutation", ("directory", "symlink-directory"))
def test_archive_final_inventory_recheck_detects_late_tree_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    root, _evidence = _bundle(tmp_path)
    original = archive_module._verify_final_tag_evidence

    def mutate_after_verification(
        evidence: LegacyArchiveEvidenceManifest,
        controls: dict[LegacyArchiveControlKind, LegacyArchiveControlArtifact],
        final_tag: LegacyFinalTagEvidence,
    ) -> None:
        original(evidence, controls, final_tag)
        if mutation == "directory":
            (root / "late-directory").mkdir()
        else:
            (root / "late-directory-link").symlink_to(root / "controls", target_is_directory=True)

    monkeypatch.setattr(archive_module, "_verify_final_tag_evidence", mutate_after_verification)
    with pytest.raises(ValueError, match="changed during assembly"):
        assemble_legacy_archive_manifest(
            root,
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
        )


def test_archive_verification_rejects_changed_future_and_expired_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, _evidence = _bundle(tmp_path)
    manifest = assemble_legacy_archive_manifest(
        root,
        policy=_policy(),
        credential_scan_policy=_scan_policy(),
    )
    with pytest.raises(ValueError, match="does not match its evidence bundle"):
        verify_legacy_archive_manifest(
            root,
            manifest.model_copy(update={"evidence_bundle_sha256": "f" * 64}),
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
        )
    with pytest.raises(ValueError, match="dated after verification"):
        verify_legacy_archive_manifest(
            root,
            manifest.model_copy(update={"assembled_ts_ns": _ts(ASSEMBLED + timedelta(seconds=1))}),
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
        )
    monkeypatch.setattr(
        archive_module,
        "time_ns",
        lambda: manifest.retention_expires_ts_ns - _policy().minimum_archive_retention_ns + 1,
    )
    with pytest.raises(ValueError, match="insufficient retention at verification"):
        verify_legacy_archive_manifest(
            root,
            manifest,
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
        )


def test_archive_loader_and_controls_require_canonical_bounded_paths(tmp_path: Path) -> None:
    root, _evidence = _bundle(tmp_path)
    output = (tmp_path / "manifest.json").resolve()
    manifest = assemble_legacy_archive_manifest(
        root,
        policy=_policy(),
        credential_scan_policy=_scan_policy(),
    )
    output.write_bytes(manifest.canonical_bytes())
    with pytest.raises(ValueError, match="not canonical JSON"):
        load_legacy_archive_manifest(output)
    with pytest.raises(ValidationError, match="below controls"):
        LegacyArchiveControlArtifact(
            kind=LegacyArchiveControlKind.FINAL_TAG_EVIDENCE,
            relative_path="../tag.json",
            content_sha256="f" * 64,
            byte_count=1,
            captured_ts_ns=1,
        )
    with pytest.raises(ValueError, match="must be absolute"):
        assemble_legacy_archive_manifest(
            Path("relative-archive"),
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
        )


def test_archive_contracts_reject_incomplete_restore_scan_and_tag() -> None:
    detector_policy = _scan_policy()
    with pytest.raises(ValidationError, match="at least 5"):
        LegacyArchiveCredentialScanPolicy(
            policy_id="incomplete",
            required_detectors=tuple(LegacyCredentialDetector)[:-1],
        )
    with pytest.raises(ValidationError, match="restored legacy archive bytes differ"):
        LegacyArchiveRestoreCheck(
            kind=LegacyArchiveArtifactKind.FINAL_TRADE_REPORT,
            source_sha256="1" * 64,
            source_byte_count=1,
            restored_sha256="2" * 64,
            restored_byte_count=1,
        )
    with pytest.raises(ValidationError, match="does not resolve"):
        LegacyFinalTagEvidence(
            retirement_id="retirement-001",
            captured_ts_ns=1,
            source_commit_sha="1" * 40,
            final_tag_commit_sha="2" * 40,
            tag_object_sha="3" * 40,
            verification_output_sha256="4" * 64,
            reviewer="reviewer",
        )
    assert detector_policy.maximum_findings == 0


def test_archive_policy_loader_rejects_symlink_and_empty_file(tmp_path: Path) -> None:
    _policy_path, scan_path = _write_policy_files(tmp_path)
    alias = (tmp_path / "scan-policy-alias.toml").resolve()
    alias.symlink_to(scan_path)
    with pytest.raises(ValueError, match="policy path is invalid"):
        load_legacy_archive_credential_scan_policy(alias)
    empty = (tmp_path / "empty-scan-policy.toml").resolve()
    empty.touch()
    with pytest.raises(ValueError, match="policy path is invalid"):
        load_legacy_archive_credential_scan_policy(empty)


def test_checked_in_retirement_policy_pins_the_scan_policy() -> None:
    config_root = Path(__file__).parents[2] / "configs/retirement"
    scan = load_legacy_archive_credential_scan_policy(
        config_root / "archive-credential-scan-v1.toml"
    )
    policy = load_retirement_policy(config_root / "evidence-v1.toml")
    assert policy.archive_credential_scan_policy_id == scan.policy_id
    assert policy.archive_credential_scan_policy_sha256 == scan.sha256()


def test_archive_assembler_rejects_future_evidence_and_short_retention(tmp_path: Path) -> None:
    root, evidence = _bundle(tmp_path)
    future = evidence.model_copy(update={"created_ts_ns": _ts(ASSEMBLED + timedelta(seconds=1))})
    (root / "legacy-archive-evidence.json").write_bytes(future.canonical_bytes() + b"\n")
    with pytest.raises(ValueError, match="dated after assembly"):
        assemble_legacy_archive_manifest(
            root,
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
        )

    root, _evidence = _bundle(tmp_path / "retention")
    with pytest.raises(ValueError, match="insufficient remaining retention"):
        assemble_legacy_archive_manifest(
            root,
            policy=_policy().model_copy(
                update={
                    "minimum_archive_retention_ns": int(
                        timedelta(days=401).total_seconds() * 1_000_000_000
                    )
                }
            ),
            credential_scan_policy=_scan_policy(),
        )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("restore-identity", "restore retirement identity"),
        ("restore-timing", "restore timing"),
        ("restore-invalidated", "restore contains invalidating"),
        ("scan-identity", "scan retirement identity"),
        ("scan-policy", "different frozen policy"),
        ("scan-timing", "scan timing"),
        ("tag-timing", "tag evidence timing"),
    ),
)
def test_archive_assembler_rejects_invalid_control_lineage_and_timing(
    tmp_path: Path,
    mutation: str,
    expected: str,
) -> None:
    root, evidence = _bundle(tmp_path)
    changed: (
        LegacyArchiveRestoreEvidence | LegacyArchiveCredentialScanEvidence | LegacyFinalTagEvidence
    )
    if mutation.startswith("restore"):
        relative = "controls/restore-evidence.json"
        restore = LegacyArchiveRestoreEvidence.model_validate_json((root / relative).read_bytes())
        if mutation == "restore-identity":
            changed = restore.model_copy(update={"retirement_id": "different-retirement"})
        elif mutation == "restore-timing":
            changed = restore.model_copy(update={"started_ts_ns": 0})
        else:
            changed = restore.model_copy(update={"invalidating_events": ("restore-failure",)})
    elif mutation.startswith("scan"):
        relative = "controls/credential-scan-evidence.json"
        scan = LegacyArchiveCredentialScanEvidence.model_validate_json(
            (root / relative).read_bytes()
        )
        if mutation == "scan-identity":
            changed = scan.model_copy(update={"retirement_id": "different-retirement"})
        elif mutation == "scan-policy":
            changed = scan.model_copy(update={"policy_id": "different-policy"})
        else:
            changed = scan.model_copy(update={"started_ts_ns": 0})
    else:
        relative = "controls/final-tag-evidence.json"
        tag = LegacyFinalTagEvidence.model_validate_json((root / relative).read_bytes())
        changed = tag.model_copy(update={"captured_ts_ns": evidence.created_ts_ns + 1})
    _replace_control(root, relative, changed)
    with pytest.raises(ValueError, match=expected):
        assemble_legacy_archive_manifest(
            root,
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
        )


def test_archive_root_and_control_canonicalization_are_fail_closed(tmp_path: Path) -> None:
    root, _evidence = _bundle(tmp_path)
    alias = (tmp_path / "archive-alias").resolve()
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(ValueError, match="non-symlink directory"):
        assemble_legacy_archive_manifest(
            alias,
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
        )
    regular = (tmp_path / "archive-file").resolve()
    regular.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ValueError, match="non-symlink directory"):
        assemble_legacy_archive_manifest(
            regular,
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
        )

    relative = "controls/final-tag-evidence.json"
    path = root / relative
    path.write_bytes(path.read_bytes() + b"\n")
    _rewrite_evidence_binding(root, relative)
    with pytest.raises(ValueError, match="not canonical JSON"):
        assemble_legacy_archive_manifest(
            root,
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
        )
