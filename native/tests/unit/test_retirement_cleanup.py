from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

import aiquanttrader_native.retirement.cleanup as cleanup_module
from aiquanttrader_native.domain.base import canonical_sha256
from aiquanttrader_native.retirement.archive import (
    load_legacy_archive_credential_scan_policy,
)
from aiquanttrader_native.retirement.cleanup import (
    assemble_cleanup_manifest,
    verify_cleanup_manifest,
)
from aiquanttrader_native.retirement.cli import main as retirement_main
from aiquanttrader_native.retirement.evidence import load_retirement_policy
from aiquanttrader_native.retirement.models import (
    CleanupAction,
    CleanupCredentialScanCheck,
    CleanupCredentialScanEvidence,
    CleanupEvidenceArtifact,
    CleanupEvidenceControl,
    CleanupEvidenceControlKind,
    CleanupEvidenceManifest,
    CleanupHostState,
    CleanupInventoryAuditEvidence,
    CleanupInventoryScope,
    CleanupPathInventoryEntry,
    CleanupPathInventoryEvidence,
    CleanupPathObjectType,
    CleanupPathState,
    CleanupScopeCheck,
    CleanupSecretState,
    CleanupTargetEvidence,
    CleanupTargetKind,
    DisabledGateResult,
    DisabledObservationGate,
    DisabledObservationReport,
    LegacyArchiveArtifact,
    LegacyArchiveArtifactKind,
    LegacyArchiveManifest,
)

NATIVE_ROOT = Path(__file__).parents[2]
POLICY_PATH = NATIVE_ROOT / "configs/retirement/evidence-v1.toml"
SCAN_POLICY_PATH = NATIVE_ROOT / "configs/retirement/archive-credential-scan-v1.toml"
COMMIT = "b" * 40


def _write(path: Path, payload: bytes) -> tuple[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest(), len(payload)


def _archive(now_ns: int) -> LegacyArchiveManifest:
    scan_policy = load_legacy_archive_credential_scan_policy(SCAN_POLICY_PATH)
    return LegacyArchiveManifest(
        retirement_id="retirement-cleanup-test",
        created_ts_ns=now_ns - 20_000_000_000,
        assembled_ts_ns=now_ns - 20_000_000_000,
        retention_expires_ts_ns=now_ns + 400 * 86_400_000_000_000,
        source_commit_sha=COMMIT,
        final_tag_commit_sha=COMMIT,
        credential_scan_policy_id=scan_policy.policy_id,
        credential_scan_policy_sha256=scan_policy.sha256(),
        evidence_manifest_sha256="1" * 64,
        evidence_bundle_sha256="2" * 64,
        restore_evidence_sha256="3" * 64,
        final_tag_evidence_sha256="4" * 64,
        artifacts=tuple(
            LegacyArchiveArtifact(
                kind=kind,
                relative_path=f"artifacts/{kind.value}.tar.zst",
                content_sha256=f"{index:x}" * 64,
                byte_count=128,
                captured_ts_ns=now_ns - 30_000_000_000,
            )
            for index, kind in enumerate(LegacyArchiveArtifactKind, start=1)
        ),
    )


def _disabled_report(now_ns: int, archive: LegacyArchiveManifest) -> DisabledObservationReport:
    policy = load_retirement_policy(POLICY_PATH)
    gates = tuple(
        DisabledGateResult(gate=gate, passed=True, actual="passed", required="passed")
        for gate in DisabledObservationGate
    )
    payload = {
        "schema_version": 2,
        "retirement_id": archive.retirement_id,
        "policy_id": policy.policy_id,
        "policy_sha256": policy.sha256(),
        "observation_sha256": "5" * 64,
        "generated_ts_ns": now_ns - 10_000_000_000,
        "readiness_report_sha256": "6" * 64,
        "stop_approval_sha256": "7" * 64,
        "archive_manifest_sha256": archive.sha256(),
        "native_deployment_id": "native-production-test",
        "native_admission_id": "8" * 64,
        "gates": [item.model_dump(mode="json") for item in gates],
    }
    return DisabledObservationReport.model_validate(
        {
            **payload,
            "report_id": canonical_sha256(payload),
            "awaiting_cleanup_approval": True,
        }
    )


def _bundle(
    root: Path,
    now_ns: int,
    archive: LegacyArchiveManifest,
    report: DisabledObservationReport,
) -> None:
    policy = load_retirement_policy(POLICY_PATH)
    scan_policy = load_legacy_archive_credential_scan_policy(SCAN_POLICY_PATH)
    path_inventory = CleanupPathInventoryEvidence(
        kind=CleanupTargetKind.REPOSITORY_PATH,
        locator="broker/mt5",
        source_commit_sha=COMMIT,
        captured_ts_ns=now_ns - 8_000_000_000,
        entries=(
            CleanupPathInventoryEntry(
                relative_path=".",
                object_type=CleanupPathObjectType.DIRECTORY,
                state_sha256="9" * 64,
                byte_count=0,
                mode="0755",
            ),
        ),
    )
    inventory_path = root / "raw/repository/broker-mt5-inventory.json"
    inventory_file_sha, inventory_size = _write(
        inventory_path,
        path_inventory.canonical_bytes() + b"\n",
    )
    scope_path = root / "raw/inventory/tracked-and-host-scope.json"
    scope_sha, scope_size = _write(scope_path, b'{"complete":true}\n')

    target = CleanupTargetEvidence(
        retirement_id=archive.retirement_id,
        target_id="legacy-mt5-source",
        action=CleanupAction.REMOVE,
        rationale="MQL5 execution is retired after the disabled observation",
        collected_by="cleanup-operator",
        reviewed_by="cleanup-reviewer",
        state=CleanupPathState(
            kind=CleanupTargetKind.REPOSITORY_PATH,
            locator="broker/mt5",
            object_type=CleanupPathObjectType.DIRECTORY,
            inventory_sha256=path_inventory.state_sha256(),
            entry_count=len(path_inventory.entries),
            total_bytes=path_inventory.total_bytes,
            captured_ts_ns=now_ns - 8_000_000_000,
            raw_artifact_id="broker-mt5-inventory",
        ),
    )
    target_payload = target.canonical_bytes() + b"\n"
    target_sha, target_size = _write(
        root / "controls/targets/legacy-mt5-source.json", target_payload
    )

    scopes = tuple(
        CleanupScopeCheck(
            scope=scope,
            present=scope is CleanupInventoryScope.MQL5_SOURCE,
            target_ids=("legacy-mt5-source",) if scope is CleanupInventoryScope.MQL5_SOURCE else (),
            evidence_artifact_ids=("complete-scope-inventory",),
        )
        for scope in CleanupInventoryScope
    )
    audit = CleanupInventoryAuditEvidence(
        retirement_id=archive.retirement_id,
        source_commit_sha=COMMIT,
        observed_ts_ns=now_ns - 9_000_000_000,
        reviewed_ts_ns=now_ns - 7_000_000_000,
        collected_by="cleanup-operator",
        reviewed_by="cleanup-reviewer",
        scopes=scopes,
    )
    audit_payload = audit.canonical_bytes() + b"\n"
    audit_sha, audit_size = _write(root / "controls/inventory-audit.json", audit_payload)

    artifacts = (
        CleanupEvidenceArtifact(
            artifact_id="broker-mt5-inventory",
            relative_path="raw/repository/broker-mt5-inventory.json",
            content_sha256=inventory_file_sha,
            byte_count=inventory_size,
            captured_ts_ns=now_ns - 8_000_000_000,
        ),
        CleanupEvidenceArtifact(
            artifact_id="complete-scope-inventory",
            relative_path="raw/inventory/tracked-and-host-scope.json",
            content_sha256=scope_sha,
            byte_count=scope_size,
            captured_ts_ns=now_ns - 9_000_000_000,
        ),
    )
    prescan_controls = (
        CleanupEvidenceControl(
            kind=CleanupEvidenceControlKind.INVENTORY_AUDIT,
            reference_id="complete-inventory",
            relative_path="controls/inventory-audit.json",
            content_sha256=audit_sha,
            byte_count=audit_size,
            captured_ts_ns=now_ns - 7_000_000_000,
        ),
        CleanupEvidenceControl(
            kind=CleanupEvidenceControlKind.TARGET_STATE,
            reference_id=target.target_id,
            relative_path="controls/targets/legacy-mt5-source.json",
            content_sha256=target_sha,
            byte_count=target_size,
            captured_ts_ns=now_ns - 7_000_000_000,
        ),
    )
    scan = CleanupCredentialScanEvidence(
        retirement_id=archive.retirement_id,
        started_ts_ns=now_ns - 6_000_000_000,
        ended_ts_ns=now_ns - 5_000_000_000,
        reviewed_ts_ns=now_ns - 4_000_000_000,
        reviewer="security-reviewer",
        scanner_name="gitleaks-and-private-key-scan",
        scanner_version="1.0.0",
        policy_id=scan_policy.policy_id,
        policy_sha256=scan_policy.sha256(),
        checks=tuple(
            CleanupCredentialScanCheck(
                relative_path=item.relative_path,
                content_sha256=item.content_sha256,
            )
            for item in (*artifacts, *prescan_controls)
        ),
    )
    scan_payload = scan.canonical_bytes() + b"\n"
    scan_sha, scan_size = _write(root / "controls/credential-scan.json", scan_payload)
    controls = (
        *prescan_controls,
        CleanupEvidenceControl(
            kind=CleanupEvidenceControlKind.CREDENTIAL_SCAN,
            reference_id="credential-scan",
            relative_path="controls/credential-scan.json",
            content_sha256=scan_sha,
            byte_count=scan_size,
            captured_ts_ns=now_ns - 4_000_000_000,
        ),
    )
    manifest = CleanupEvidenceManifest(
        retirement_id=archive.retirement_id,
        policy_id=policy.policy_id,
        policy_sha256=policy.sha256(),
        created_ts_ns=now_ns - 2_000_000_000,
        source_commit_sha=COMMIT,
        archive_manifest_sha256=archive.sha256(),
        disabled_observation_report_sha256=report.sha256(),
        credential_scan_policy_id=scan_policy.policy_id,
        credential_scan_policy_sha256=scan_policy.sha256(),
        artifacts=artifacts,
        controls=controls,
    )
    _write(root / "cleanup-evidence.json", manifest.canonical_bytes() + b"\n")


def _bundle_controls(
    root: Path,
) -> tuple[
    CleanupEvidenceManifest,
    CleanupInventoryAuditEvidence,
    CleanupCredentialScanEvidence,
    list[CleanupEvidenceControl],
    tuple[CleanupTargetEvidence, ...],
]:
    manifest = CleanupEvidenceManifest.model_validate_json(
        (root / "cleanup-evidence.json").read_bytes()
    )
    inventory_binding = next(
        item
        for item in manifest.controls
        if item.kind is CleanupEvidenceControlKind.INVENTORY_AUDIT
    )
    scan_binding = next(
        item
        for item in manifest.controls
        if item.kind is CleanupEvidenceControlKind.CREDENTIAL_SCAN
    )
    target_bindings = sorted(
        (
            item
            for item in manifest.controls
            if item.kind is CleanupEvidenceControlKind.TARGET_STATE
        ),
        key=lambda item: item.reference_id,
    )
    audit = CleanupInventoryAuditEvidence.model_validate_json(
        (root / inventory_binding.relative_path).read_bytes()
    )
    scan = CleanupCredentialScanEvidence.model_validate_json(
        (root / scan_binding.relative_path).read_bytes()
    )
    targets = tuple(
        CleanupTargetEvidence.model_validate_json((root / item.relative_path).read_bytes())
        for item in target_bindings
    )
    return manifest, audit, scan, target_bindings, targets


def test_cleanup_manifest_is_assembled_from_exact_replayable_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_ns = time.time_ns()
    archive = _archive(now_ns)
    report = _disabled_report(now_ns, archive)
    root = tmp_path / "cleanup-evidence"
    _bundle(root, now_ns, archive, report)
    monkeypatch.setattr("aiquanttrader_native.retirement.cleanup.time_ns", lambda: now_ns)

    manifest = assemble_cleanup_manifest(
        root,
        report,
        archive,
        policy=load_retirement_policy(POLICY_PATH),
        credential_scan_policy=load_legacy_archive_credential_scan_policy(SCAN_POLICY_PATH),
    )

    assert manifest.schema_version == 2
    assert manifest.source_commit_sha == COMMIT
    assert manifest.disabled_observation_report_sha256 == report.sha256()
    assert [item.target_id for item in manifest.targets] == ["legacy-mt5-source"]
    path_inventory = CleanupPathInventoryEvidence.model_validate_json(
        (root / "raw/repository/broker-mt5-inventory.json").read_bytes()
    )
    assert manifest.targets[0].expected_state_sha256 == path_inventory.state_sha256()
    assert (
        verify_cleanup_manifest(
            root,
            manifest,
            report,
            archive,
            policy=load_retirement_policy(POLICY_PATH),
            credential_scan_policy=load_legacy_archive_credential_scan_policy(SCAN_POLICY_PATH),
        )
        == manifest
    )


def test_cleanup_replay_rejects_tampering_unscanned_files_and_failed_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_ns = time.time_ns()
    archive = _archive(now_ns)
    report = _disabled_report(now_ns, archive)
    root = tmp_path / "cleanup-evidence"
    _bundle(root, now_ns, archive, report)
    monkeypatch.setattr("aiquanttrader_native.retirement.cleanup.time_ns", lambda: now_ns)
    policy = load_retirement_policy(POLICY_PATH)
    scan_policy = load_legacy_archive_credential_scan_policy(SCAN_POLICY_PATH)

    failed = report.model_copy(update={"awaiting_cleanup_approval": False})
    with pytest.raises(ValueError, match="does not permit cleanup review"):
        assemble_cleanup_manifest(
            root,
            failed,
            archive,
            policy=policy,
            credential_scan_policy=scan_policy,
        )

    (root / "raw/unbound.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="inventory is not exact"):
        assemble_cleanup_manifest(
            root,
            report,
            archive,
            policy=policy,
            credential_scan_policy=scan_policy,
        )
    (root / "raw/unbound.json").unlink()

    (root / "raw/repository/broker-mt5-inventory.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="digest or size differs"):
        assemble_cleanup_manifest(
            root,
            report,
            archive,
            policy=policy,
            credential_scan_policy=scan_policy,
        )


def test_cleanup_cli_assembles_and_independently_replays_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_ns = time.time_ns()
    archive = _archive(now_ns)
    report = _disabled_report(now_ns, archive)
    root = tmp_path / "cleanup-evidence"
    _bundle(root, now_ns, archive, report)
    archive_path = tmp_path / "legacy-archive-manifest.json"
    report_path = tmp_path / "disabled-report.json"
    output_path = tmp_path / "cleanup-manifest.json"
    archive_path.write_bytes(archive.canonical_bytes() + b"\n")
    report_path.write_bytes(report.canonical_bytes() + b"\n")
    monkeypatch.setattr("aiquanttrader_native.retirement.cleanup.time_ns", lambda: now_ns)
    monkeypatch.setattr(
        "aiquanttrader_native.retirement.cli._replay_cleanup_sources",
        lambda *args, **kwargs: (report, archive),
    )
    dummy = str(tmp_path / "source-placeholder.json")
    common = [
        "--evidence-root",
        str(root),
        "--disabled-evidence-root",
        str(root),
        "--native-evidence-root",
        str(root),
        "--legacy-evidence-root",
        str(root),
        "--readiness-observation",
        dummy,
        "--readiness-report",
        dummy,
        "--native-observation",
        dummy,
        "--disabled-observation",
        dummy,
        "--disabled-report",
        str(report_path),
        "--archive-manifest",
        str(archive_path),
        "--stop-approval",
        dummy,
        "--stop-signature",
        dummy,
        "--stop-public-key",
        dummy,
        "--policy",
        str(POLICY_PATH),
        "--credential-scan-policy",
        str(SCAN_POLICY_PATH),
        "--native-approval-key-id",
        "native-approval-test",
        "--native-approval-public-key-sha256",
        "a" * 64,
        "--stop-approval-key-id",
        "stop-approval-test",
        "--stop-approval-public-key-sha256",
        "b" * 64,
    ]

    assert (
        retirement_main(["assemble-cleanup-manifest", *common, "--output", str(output_path)]) == 0
    )
    assert (
        retirement_main(["verify-cleanup-manifest", *common, "--manifest", str(output_path)]) == 0
    )


def test_cleanup_state_requires_independent_review_and_safe_exact_target() -> None:
    with pytest.raises(ValidationError, match="independent reviewer"):
        CleanupTargetEvidence(
            retirement_id="retirement-cleanup-test",
            target_id="legacy-runtime",
            action=CleanupAction.REMOVE,
            rationale="retired runtime",
            collected_by="same-person",
            reviewed_by="same-person",
            state=CleanupPathState(
                kind=CleanupTargetKind.RUNTIME_PATH,
                locator="/root/AIQuantTrader/.runtime/wineprefix",
                object_type=CleanupPathObjectType.DIRECTORY,
                inventory_sha256="a" * 64,
                entry_count=1,
                total_bytes=1,
                captured_ts_ns=1,
                raw_artifact_id="runtime-inventory",
            ),
        )


def test_cleanup_host_secret_scope_and_scan_contracts_fail_closed() -> None:
    path_entry = CleanupPathInventoryEntry(
        relative_path=".",
        object_type=CleanupPathObjectType.DIRECTORY,
        state_sha256="9" * 64,
        byte_count=0,
        mode="0755",
    )
    path_inventory = CleanupPathInventoryEvidence(
        kind=CleanupTargetKind.REPOSITORY_PATH,
        locator="broker/mt5",
        source_commit_sha=COMMIT,
        captured_ts_ns=10,
        entries=(path_entry,),
    )
    assert path_inventory.total_bytes == 0
    assert (
        path_inventory.state_sha256()
        == path_inventory.model_copy(update={"captured_ts_ns": 11}).state_sha256()
    )
    with pytest.raises(ValidationError, match="relative without traversal"):
        CleanupPathInventoryEntry(
            relative_path="../escape",
            object_type=CleanupPathObjectType.FILE,
            state_sha256="9" * 64,
            byte_count=1,
            mode="0644",
        )
    with pytest.raises(ValidationError, match="unique, ordered, and rooted"):
        CleanupPathInventoryEvidence(
            kind=CleanupTargetKind.REPOSITORY_PATH,
            locator="broker/mt5",
            source_commit_sha=COMMIT,
            captured_ts_ns=10,
            entries=(
                path_entry.model_copy(update={"relative_path": "z"}),
                path_entry.model_copy(update={"relative_path": "a"}),
            ),
        )
    with pytest.raises(ValidationError, match="bind a source commit"):
        CleanupPathInventoryEvidence(
            kind=CleanupTargetKind.RUNTIME_PATH,
            locator="/root/AIQuantTrader/.runtime/wineprefix",
            source_commit_sha=COMMIT,
            captured_ts_ns=10,
            entries=(path_entry,),
        )

    host = CleanupHostState(
        kind=CleanupTargetKind.HOST_PACKAGE,
        locator="wine64",
        installed_version="11.10",
        configuration_sha256="a" * 64,
        ownership_sha256="b" * 64,
        captured_ts_ns=10,
        raw_artifact_ids=("package-state", "package-ownership"),
    )
    secret = CleanupSecretState(
        locator="MT5_PASSWORD",
        provider="broker-vault",
        provider_record_id_sha256="c" * 64,
        provider_state_sha256="d" * 64,
        active_sessions_sha256="e" * 64,
        captured_ts_ns=10,
        raw_artifact_ids=("provider-state", "session-state"),
    )
    host_target = CleanupTargetEvidence(
        retirement_id="retirement-cleanup-test",
        target_id="wine-package",
        action=CleanupAction.REMOVE,
        rationale="project-owned legacy package",
        collected_by="operator",
        reviewed_by="reviewer",
        state=host,
    )
    secret_target = CleanupTargetEvidence(
        retirement_id="retirement-cleanup-test",
        target_id="mt5-password",
        action=CleanupAction.REVOKE,
        rationale="revoke the provider record and broker sessions",
        collected_by="operator",
        reviewed_by="reviewer",
        state=secret,
    )
    assert host_target.cleanup_target().expected_state_sha256 == host.expected_state_sha256()
    assert secret_target.cleanup_target().expected_state_sha256 == secret.expected_state_sha256()
    assert host.artifact_ids() == ("package-state", "package-ownership")
    assert secret.artifact_ids() == ("provider-state", "session-state")

    for state_type, payload, message in (
        (
            CleanupHostState,
            {
                **host.model_dump(),
                "raw_artifact_ids": ("same", "same"),
            },
            "distinct state and ownership",
        ),
        (
            CleanupSecretState,
            {
                **secret.model_dump(),
                "raw_artifact_ids": ("same", "same"),
            },
            "distinct provider and session",
        ),
    ):
        with pytest.raises(ValidationError, match=message):
            state_type.model_validate(payload)

    with pytest.raises(ValidationError, match="presence"):
        CleanupScopeCheck(
            scope=CleanupInventoryScope.HOST_DEPENDENCIES,
            present=False,
            target_ids=("wine-package",),
            evidence_artifact_ids=("scope",),
        )
    with pytest.raises(ValidationError, match="target identities"):
        CleanupScopeCheck(
            scope=CleanupInventoryScope.HOST_DEPENDENCIES,
            present=True,
            target_ids=("wine-package", "wine-package"),
            evidence_artifact_ids=("scope",),
        )
    with pytest.raises(ValidationError, match="evidence identities"):
        CleanupScopeCheck(
            scope=CleanupInventoryScope.HOST_DEPENDENCIES,
            present=True,
            target_ids=("wine-package",),
            evidence_artifact_ids=("scope", "scope"),
        )

    for model, payload, message in (
        (
            CleanupEvidenceArtifact,
            {
                "artifact_id": "unsafe",
                "relative_path": "../unsafe",
                "content_sha256": "a" * 64,
                "byte_count": 1,
                "captured_ts_ns": 1,
            },
            "below raw",
        ),
        (
            CleanupEvidenceControl,
            {
                "kind": CleanupEvidenceControlKind.TARGET_STATE,
                "reference_id": "unsafe",
                "relative_path": "raw/not-a-control.json",
                "content_sha256": "a" * 64,
                "byte_count": 1,
                "captured_ts_ns": 1,
            },
            "below controls",
        ),
        (
            CleanupCredentialScanCheck,
            {
                "relative_path": "elsewhere/not-scanned.json",
                "content_sha256": "a" * 64,
            },
            "raw/ or controls",
        ),
    ):
        with pytest.raises(ValidationError, match=message):
            model.model_validate(payload)


def test_cleanup_low_level_reader_and_root_guards(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be absolute"):
        cleanup_module._validated_root(Path("relative"))

    regular_file = tmp_path / "regular-file"
    regular_file.write_text("data", encoding="utf-8")
    with pytest.raises(ValueError, match="directory"):
        cleanup_module._validated_root(regular_file)

    symlink = tmp_path / "evidence-link"
    symlink.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="non-symlink"):
        cleanup_module._validated_root(symlink)

    with pytest.raises(ValueError, match="cannot open"):
        cleanup_module._read_regular(tmp_path / "missing", maximum_bytes=10)
    empty = tmp_path / "empty"
    empty.touch()
    with pytest.raises(ValueError, match="size is invalid"):
        cleanup_module._read_regular(empty, maximum_bytes=10)
    with pytest.raises(ValueError, match="regular file"):
        cleanup_module._read_regular(tmp_path, maximum_bytes=10)
    with pytest.raises(ValueError, match="cannot open"):
        cleanup_module._hash_regular(tmp_path / "missing-hash")
    with pytest.raises(ValueError, match="regular file"):
        cleanup_module._hash_regular(tmp_path)
    with pytest.raises(ValueError, match="exactly one"):
        cleanup_module._single_control((), CleanupEvidenceControlKind.INVENTORY_AUDIT)


def test_cleanup_audit_scan_and_manifest_inventories_are_exact() -> None:
    absent_scopes = tuple(
        CleanupScopeCheck(
            scope=scope,
            present=False,
            evidence_artifact_ids=("scope-evidence",),
        )
        for scope in CleanupInventoryScope
    )
    audit_payload = {
        "retirement_id": "retirement-cleanup-test",
        "source_commit_sha": COMMIT,
        "observed_ts_ns": 10,
        "reviewed_ts_ns": 11,
        "collected_by": "operator",
        "reviewed_by": "reviewer",
        "scopes": absent_scopes,
    }
    for audit_updates, message in (
        ({"reviewed_ts_ns": 9}, "cannot predate"),
        ({"reviewed_by": "operator"}, "independent reviewer"),
        (
            {"scopes": (*absent_scopes[:-1], absent_scopes[0])},
            "every scope exactly once",
        ),
        (
            {
                "scopes": (
                    absent_scopes[0].model_copy(
                        update={"present": True, "target_ids": ("duplicate",)}
                    ),
                    absent_scopes[1].model_copy(
                        update={"present": True, "target_ids": ("duplicate",)}
                    ),
                    *absent_scopes[2:],
                )
            },
            "exactly one inventory scope",
        ),
        (
            {"invalidating_events": ("duplicate", "duplicate")},
            "invalidating events must be unique",
        ),
        ({"invalidating_events": ("unsafe",)}, "contains invalidating events"),
    ):
        with pytest.raises(ValidationError, match=message):
            CleanupInventoryAuditEvidence.model_validate({**audit_payload, **audit_updates})

    checks = (
        CleanupCredentialScanCheck(relative_path="raw/a.json", content_sha256="a" * 64),
        CleanupCredentialScanCheck(relative_path="controls/a.json", content_sha256="b" * 64),
    )
    scan_payload = {
        "retirement_id": "retirement-cleanup-test",
        "started_ts_ns": 10,
        "ended_ts_ns": 11,
        "reviewed_ts_ns": 12,
        "reviewer": "security-reviewer",
        "scanner_name": "scanner",
        "scanner_version": "1",
        "policy_id": "scan-policy",
        "policy_sha256": "c" * 64,
        "checks": checks,
    }
    for scan_updates, message in (
        ({"ended_ts_ns": 10}, "interval"),
        ({"checks": (checks[0], checks[0])}, "duplicate checks"),
        ({"findings": ("credential",)}, "contains findings"),
    ):
        with pytest.raises(ValidationError, match=message):
            CleanupCredentialScanEvidence.model_validate({**scan_payload, **scan_updates})

    artifacts = (
        CleanupEvidenceArtifact(
            artifact_id="raw-a",
            relative_path="raw/a.json",
            content_sha256="a" * 64,
            byte_count=1,
            captured_ts_ns=10,
        ),
        CleanupEvidenceArtifact(
            artifact_id="raw-b",
            relative_path="raw/b.json",
            content_sha256="b" * 64,
            byte_count=1,
            captured_ts_ns=10,
        ),
    )
    controls = (
        CleanupEvidenceControl(
            kind=CleanupEvidenceControlKind.INVENTORY_AUDIT,
            reference_id="inventory",
            relative_path="controls/inventory.json",
            content_sha256="c" * 64,
            byte_count=1,
            captured_ts_ns=10,
        ),
        CleanupEvidenceControl(
            kind=CleanupEvidenceControlKind.TARGET_STATE,
            reference_id="target",
            relative_path="controls/target.json",
            content_sha256="d" * 64,
            byte_count=1,
            captured_ts_ns=10,
        ),
        CleanupEvidenceControl(
            kind=CleanupEvidenceControlKind.CREDENTIAL_SCAN,
            reference_id="scan",
            relative_path="controls/scan.json",
            content_sha256="e" * 64,
            byte_count=1,
            captured_ts_ns=10,
        ),
    )
    manifest_payload = {
        "retirement_id": "retirement-cleanup-test",
        "policy_id": "retirement-policy",
        "policy_sha256": "f" * 64,
        "created_ts_ns": 11,
        "source_commit_sha": COMMIT,
        "archive_manifest_sha256": "1" * 64,
        "disabled_observation_report_sha256": "2" * 64,
        "credential_scan_policy_id": "scan-policy",
        "credential_scan_policy_sha256": "3" * 64,
        "artifacts": artifacts,
        "controls": controls,
    }
    invalid_cases = (
        (
            {"artifacts": (artifacts[0], artifacts[1].model_copy(update={"artifact_id": "raw-a"}))},
            "artifact identities",
        ),
        (
            {
                "controls": (
                    controls[0],
                    controls[1],
                    controls[2].model_copy(
                        update={
                            "kind": CleanupEvidenceControlKind.TARGET_STATE,
                            "reference_id": "target",
                        }
                    ),
                )
            },
            "control references",
        ),
        (
            {
                "artifacts": (
                    artifacts[0],
                    artifacts[1].model_copy(update={"relative_path": "raw/a.json"}),
                )
            },
            "paths must be unique",
        ),
        (
            {
                "controls": (
                    controls[0].model_copy(
                        update={"kind": CleanupEvidenceControlKind.TARGET_STATE}
                    ),
                    controls[1],
                    controls[2],
                )
            },
            "one inventory audit",
        ),
        (
            {
                "controls": (
                    controls[0],
                    controls[1],
                    controls[2].model_copy(
                        update={"kind": CleanupEvidenceControlKind.TARGET_STATE}
                    ),
                )
            },
            "one credential scan",
        ),
        (
            {
                "controls": (
                    controls[0],
                    controls[1].model_copy(
                        update={"kind": CleanupEvidenceControlKind.INVENTORY_AUDIT}
                    ),
                    controls[2],
                )
            },
            "one inventory audit",
        ),
        (
            {
                "artifacts": (
                    artifacts[0].model_copy(update={"captured_ts_ns": 12}),
                    artifacts[1],
                )
            },
            "cannot be captured after",
        ),
    )
    for manifest_updates, message in invalid_cases:
        with pytest.raises(ValidationError, match=message):
            CleanupEvidenceManifest.model_validate({**manifest_payload, **manifest_updates})


def test_cleanup_lineage_scope_and_scan_replay_reject_every_mismatch(tmp_path: Path) -> None:
    now_ns = time.time_ns()
    archive = _archive(now_ns)
    report = _disabled_report(now_ns, archive)
    policy = load_retirement_policy(POLICY_PATH)
    scan_policy = load_legacy_archive_credential_scan_policy(SCAN_POLICY_PATH)
    root = tmp_path / "cleanup-evidence"
    _bundle(root, now_ns, archive, report)
    manifest, audit, scan, target_bindings, targets = _bundle_controls(root)
    path_inventory = CleanupPathInventoryEvidence.model_validate_json(
        (root / "raw/repository/broker-mt5-inventory.json").read_bytes()
    )
    path_inventories = {targets[0].target_id: path_inventory}

    def assert_lineage_error(
        message: str,
        *,
        selected_manifest: CleanupEvidenceManifest = manifest,
        selected_bindings: list[CleanupEvidenceControl] = target_bindings,
        selected_ts_ns: int = now_ns,
    ) -> None:
        with pytest.raises(ValueError, match=message):
            cleanup_module._verify_lineage(
                selected_manifest,
                audit,
                scan,
                selected_bindings,
                targets,
                report,
                archive,
                policy,
                scan_policy,
                selected_ts_ns,
            )

    assert_lineage_error(
        "retirement identity",
        selected_manifest=manifest.model_copy(update={"retirement_id": "different"}),
    )
    assert_lineage_error(
        "retirement policy",
        selected_manifest=manifest.model_copy(update={"policy_id": "different"}),
    )
    assert_lineage_error(
        "credential scan policy",
        selected_manifest=manifest.model_copy(update={"credential_scan_policy_id": "different"}),
    )
    assert_lineage_error(
        "lineage",
        selected_manifest=manifest.model_copy(update={"source_commit_sha": "0" * 40}),
    )
    assert_lineage_error(
        "postdates",
        selected_manifest=manifest.model_copy(update={"created_ts_ns": report.generated_ts_ns - 1}),
    )
    assert_lineage_error(
        "bound identities",
        selected_bindings=[target_bindings[0].model_copy(update={"reference_id": "different"})],
    )

    assert_lineage_error(
        "remaining archive retention",
        selected_ts_ns=(archive.retention_expires_ts_ns - policy.minimum_archive_retention_ns + 1),
    )

    empty_target_scope = tuple(
        scope.model_copy(update={"present": False, "target_ids": ()}) if scope.target_ids else scope
        for scope in audit.scopes
    )
    with pytest.raises(ValueError, match="cover every target"):
        cleanup_module._verify_scope_and_raw_evidence(
            manifest,
            audit.model_copy(update={"scopes": empty_target_scope}),
            targets,
            path_inventories,
        )
    unbound_scope = (
        audit.scopes[0].model_copy(update={"evidence_artifact_ids": ("unbound",)}),
        *audit.scopes[1:],
    )
    with pytest.raises(ValueError, match="scope audit references unbound"):
        cleanup_module._verify_scope_and_raw_evidence(
            manifest,
            audit.model_copy(update={"scopes": unbound_scope}),
            targets,
            path_inventories,
        )
    missing_state = targets[0].model_copy(
        update={"state": targets[0].state.model_copy(update={"raw_artifact_id": "unbound"})}
    )
    with pytest.raises(ValueError, match="target references unbound"):
        cleanup_module._verify_scope_and_raw_evidence(
            manifest, audit, (missing_state,), path_inventories
        )
    changed_state = targets[0].model_copy(
        update={"state": targets[0].state.model_copy(update={"inventory_sha256": "0" * 64})}
    )
    with pytest.raises(ValueError, match="path inventory state"):
        cleanup_module._verify_scope_and_raw_evidence(
            manifest, audit, (changed_state,), path_inventories
        )
    with pytest.raises(ValueError, match="unreferenced raw artifacts"):
        cleanup_module._verify_scope_and_raw_evidence(
            manifest.model_copy(
                update={
                    "artifacts": (
                        *manifest.artifacts,
                        manifest.artifacts[0].model_copy(
                            update={"artifact_id": "extra-unreferenced"}
                        ),
                    )
                }
            ),
            audit,
            targets,
            path_inventories,
        )

    scan_binding = next(
        item
        for item in manifest.controls
        if item.kind is CleanupEvidenceControlKind.CREDENTIAL_SCAN
    )
    with pytest.raises(ValueError, match="exact evidence inventory"):
        cleanup_module._verify_credential_scan(
            manifest,
            scan_binding,
            scan.model_copy(update={"checks": scan.checks[:-1]}),
            scan_policy,
        )
    with pytest.raises(ValueError, match="zero-finding policy-bound"):
        cleanup_module._verify_credential_scan(
            manifest,
            scan_binding,
            scan.model_copy(update={"policy_id": "different"}),
            scan_policy,
        )

    host_state = CleanupHostState(
        kind=CleanupTargetKind.HOST_PACKAGE,
        locator="wine64",
        installed_version="11.10",
        configuration_sha256="a" * 64,
        ownership_sha256="b" * 64,
        captured_ts_ns=now_ns - 1,
        raw_artifact_ids=("host-configuration", "host-ownership"),
    )
    secret_state = CleanupSecretState(
        locator="MT5_PASSWORD",
        provider="broker-vault",
        provider_record_id_sha256="c" * 64,
        provider_state_sha256="d" * 64,
        active_sessions_sha256="e" * 64,
        captured_ts_ns=now_ns - 1,
        raw_artifact_ids=("provider-state", "active-sessions"),
    )
    host_target = CleanupTargetEvidence(
        retirement_id=archive.retirement_id,
        target_id="wine-package",
        action=CleanupAction.REMOVE,
        rationale="project-owned package",
        collected_by="operator",
        reviewed_by="reviewer",
        state=host_state,
    )
    secret_target = CleanupTargetEvidence(
        retirement_id=archive.retirement_id,
        target_id="mt5-password",
        action=CleanupAction.REVOKE,
        rationale="provider record and sessions",
        collected_by="operator",
        reviewed_by="reviewer",
        state=secret_state,
    )
    typed_artifacts = tuple(
        CleanupEvidenceArtifact(
            artifact_id=artifact_id,
            relative_path=f"raw/typed/{artifact_id}.json",
            content_sha256=content_sha256,
            byte_count=1,
            captured_ts_ns=now_ns - 1,
        )
        for artifact_id, content_sha256 in (
            ("host-configuration", "a" * 64),
            ("host-ownership", "b" * 64),
            ("provider-state", "d" * 64),
            ("active-sessions", "e" * 64),
        )
    )
    typed_scopes = tuple(
        CleanupScopeCheck(
            scope=scope,
            present=scope
            in {
                CleanupInventoryScope.HOST_DEPENDENCIES,
                CleanupInventoryScope.CREDENTIALS_AND_SESSIONS,
            },
            target_ids=("wine-package",)
            if scope is CleanupInventoryScope.HOST_DEPENDENCIES
            else ("mt5-password",)
            if scope is CleanupInventoryScope.CREDENTIALS_AND_SESSIONS
            else (),
            evidence_artifact_ids=("host-configuration",),
        )
        for scope in CleanupInventoryScope
    )
    typed_audit = audit.model_copy(update={"scopes": typed_scopes})
    typed_manifest = manifest.model_copy(update={"artifacts": typed_artifacts})
    cleanup_module._verify_scope_and_raw_evidence(
        typed_manifest, typed_audit, (host_target, secret_target), {}
    )
    bad_host = host_target.model_copy(
        update={"state": host_state.model_copy(update={"configuration_sha256": "0" * 64})}
    )
    with pytest.raises(ValueError, match="host state hashes"):
        cleanup_module._verify_scope_and_raw_evidence(
            typed_manifest, typed_audit, (bad_host, secret_target), {}
        )
    bad_secret = secret_target.model_copy(
        update={"state": secret_state.model_copy(update={"provider_state_sha256": "0" * 64})}
    )
    with pytest.raises(ValueError, match="secret state hashes"):
        cleanup_module._verify_scope_and_raw_evidence(
            typed_manifest, typed_audit, (host_target, bad_secret), {}
        )


def test_cleanup_loader_replay_and_filesystem_tamper_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_ns = time.time_ns()
    archive = _archive(now_ns)
    report = _disabled_report(now_ns, archive)
    policy = load_retirement_policy(POLICY_PATH)
    scan_policy = load_legacy_archive_credential_scan_policy(SCAN_POLICY_PATH)
    root = tmp_path / "cleanup-evidence"
    _bundle(root, now_ns, archive, report)
    monkeypatch.setattr("aiquanttrader_native.retirement.cleanup.time_ns", lambda: now_ns)
    manifest = assemble_cleanup_manifest(
        root,
        report,
        archive,
        policy=policy,
        credential_scan_policy=scan_policy,
    )
    manifest_path = tmp_path / "cleanup-manifest.json"
    report_path = tmp_path / "disabled-report.json"
    manifest_path.write_bytes(manifest.canonical_bytes() + b"\n")
    report_path.write_bytes(report.canonical_bytes() + b"\n")
    assert cleanup_module.load_cleanup_manifest(manifest_path) == manifest
    assert cleanup_module.load_disabled_observation_report(report_path) == report
    manifest_path.write_bytes(manifest.canonical_bytes())
    report_path.write_bytes(report.canonical_bytes())
    with pytest.raises(ValueError, match="not canonical JSON"):
        cleanup_module.load_cleanup_manifest(manifest_path)
    with pytest.raises(ValueError, match="not canonical JSON"):
        cleanup_module.load_disabled_observation_report(report_path)

    with pytest.raises(ValueError, match="dated after verification"):
        verify_cleanup_manifest(
            root,
            manifest.model_copy(update={"created_ts_ns": now_ns + 1}),
            report,
            archive,
            policy=policy,
            credential_scan_policy=scan_policy,
        )
    with pytest.raises(ValueError, match="does not match its evidence root"):
        verify_cleanup_manifest(
            root,
            manifest.model_copy(update={"evidence_bundle_sha256": "0" * 64}),
            report,
            archive,
            policy=policy,
            credential_scan_policy=scan_policy,
        )

    evidence_manifest = CleanupEvidenceManifest.model_validate_json(
        (root / "cleanup-evidence.json").read_bytes()
    )
    future_manifest = evidence_manifest.model_copy(update={"created_ts_ns": now_ns + 1})
    (root / "cleanup-evidence.json").write_bytes(future_manifest.canonical_bytes() + b"\n")
    with pytest.raises(ValueError, match="dated after assembly"):
        assemble_cleanup_manifest(
            root,
            report,
            archive,
            policy=policy,
            credential_scan_policy=scan_policy,
        )
    (root / "cleanup-evidence.json").write_bytes(evidence_manifest.canonical_bytes() + b"\n")

    target_binding = next(
        item
        for item in evidence_manifest.controls
        if item.kind is CleanupEvidenceControlKind.TARGET_STATE
    )
    with pytest.raises(ValueError, match="control hash differs"):
        cleanup_module._load_bound_control(
            root,
            target_binding.model_copy(update={"content_sha256": "0" * 64}),
            CleanupTargetEvidence,
        )
    target_path = root / target_binding.relative_path
    canonical_target = target_path.read_bytes()
    target_path.write_bytes(canonical_target.rstrip(b"\n"))
    with pytest.raises(ValueError, match="not canonical JSON"):
        cleanup_module._load_control(target_path, CleanupTargetEvidence)
    target_path.write_bytes(canonical_target)

    symlink = root / "raw/unbound-link"
    symlink.symlink_to(root / "raw/repository/broker-mt5-inventory.json")
    with pytest.raises(ValueError, match="cannot contain symlinks"):
        assemble_cleanup_manifest(
            root,
            report,
            archive,
            policy=policy,
            credential_scan_policy=scan_policy,
        )
    symlink.unlink()

    raw_path = root / "raw/repository/broker-mt5-inventory.json"
    raw_path.chmod(0o666)
    with pytest.raises(ValueError, match="group/world writable"):
        assemble_cleanup_manifest(
            root,
            report,
            archive,
            policy=policy,
            credential_scan_policy=scan_policy,
        )
    raw_path.chmod(0o644)

    monkeypatch.setattr(cleanup_module, "MAX_BUNDLE_FILES", 0)
    with pytest.raises(ValueError, match="resource bounds"):
        assemble_cleanup_manifest(
            root,
            report,
            archive,
            policy=policy,
            credential_scan_policy=scan_policy,
        )

    with pytest.raises(ValidationError, match="narrow absolute"):
        CleanupTargetEvidence(
            retirement_id="retirement-cleanup-test",
            target_id="legacy-runtime",
            action=CleanupAction.REMOVE,
            rationale="unsafe broad target",
            collected_by="operator",
            reviewed_by="reviewer",
            state=CleanupPathState(
                kind=CleanupTargetKind.RUNTIME_PATH,
                locator="/root",
                object_type=CleanupPathObjectType.DIRECTORY,
                inventory_sha256="a" * 64,
                entry_count=1,
                total_bytes=1,
                captured_ts_ns=1,
                raw_artifact_id="runtime-inventory",
            ),
        )
