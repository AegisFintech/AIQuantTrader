from __future__ import annotations

import base64
import hashlib
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa
from pydantic import ValidationError

import aiquanttrader_native.retirement.action_plan as action_plan_module
import aiquanttrader_native.retirement.cleanup as cleanup_module
import aiquanttrader_native.retirement.outcome as outcome_module
import aiquanttrader_native.retirement.preflight as preflight_module
from aiquanttrader_native.domain.base import canonical_sha256
from aiquanttrader_native.retirement.action_plan import (
    load_cleanup_action_plan,
    prepare_cleanup_action_plan,
    verify_cleanup_action_plan,
)
from aiquanttrader_native.retirement.approval import RetirementApprovalPaths
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
    CleanupActionEvidenceRequirement,
    CleanupActionOutcomeKind,
    CleanupActionPlan,
    CleanupActionStage,
    CleanupArchiveOnlyResult,
    CleanupCompletionReport,
    CleanupCredentialScanCheck,
    CleanupCredentialScanEvidence,
    CleanupEvidenceArtifact,
    CleanupEvidenceControl,
    CleanupEvidenceControlKind,
    CleanupEvidenceManifest,
    CleanupHostAbsenceEvidence,
    CleanupHostState,
    CleanupInventoryAuditEvidence,
    CleanupInventoryScope,
    CleanupNativeMigrationResult,
    CleanupOutcomeControl,
    CleanupOutcomeControlKind,
    CleanupOutcomeEvidenceManifest,
    CleanupOutcomeGate,
    CleanupPathAbsenceEvidence,
    CleanupPathInventoryEntry,
    CleanupPathInventoryEvidence,
    CleanupPathObjectType,
    CleanupPathState,
    CleanupPreflightGate,
    CleanupPreflightReceipt,
    CleanupPreflightTargetResult,
    CleanupRemovedHostResult,
    CleanupRemovedPathResult,
    CleanupRevokedSecretResult,
    CleanupScopeCheck,
    CleanupSecretState,
    CleanupTargetEvidence,
    CleanupTargetKind,
    CleanupTargetOutcomeEvidence,
    DisabledGateResult,
    DisabledObservationGate,
    DisabledObservationReport,
    LegacyArchiveArtifact,
    LegacyArchiveArtifactKind,
    LegacyArchiveManifest,
    LegacyCleanupManifest,
    LegacyCleanupTarget,
    RetirementActionApproval,
    RetirementActionScope,
    RetirementApprovalSignature,
)
from aiquanttrader_native.retirement.outcome import (
    assemble_cleanup_completion,
    load_cleanup_completion_report,
    verify_cleanup_completion,
)
from aiquanttrader_native.retirement.preflight import (
    evaluate_cleanup_preflight,
    load_cleanup_preflight_receipt,
    verify_cleanup_preflight,
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
    *,
    root_state_sha256: str = "9" * 64,
    rationale: str = "MQL5 execution is retired after the disabled observation",
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
                state_sha256=root_state_sha256,
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
        rationale=rationale,
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


def _cleanup_approval(
    root: Path,
    manifest: LegacyCleanupManifest,
    report: DisabledObservationReport,
    archive: LegacyArchiveManifest,
    approved_at: datetime,
) -> tuple[RetirementApprovalPaths, str, str, RetirementActionApproval]:
    root.mkdir(parents=True, exist_ok=True)
    approval = RetirementActionApproval(
        approval_id="cleanup-approval-test",
        retirement_id=manifest.retirement_id,
        scope=RetirementActionScope.REMOVE_AND_CLEAN,
        report_sha256=report.sha256(),
        native_deployment_id=report.native_deployment_id,
        native_admission_id=report.native_admission_id,
        archive_manifest_sha256=archive.sha256(),
        source_commit_sha=manifest.source_commit_sha,
        cleanup_manifest_sha256=manifest.sha256(),
        approver="offline-cleanup-approver",
        approved_at=approved_at,
        expires_at=approved_at + timedelta(hours=1),
    )
    key = ECC.generate(curve="Ed25519")
    key_id = "cleanup-approver-test"
    signature = RetirementApprovalSignature(
        key_id=key_id,
        approval_sha256=approval.sha256(),
        signature_base64=base64.b64encode(
            eddsa.new(key, "rfc8032").sign(approval.canonical_bytes())
        ).decode("ascii"),
    )
    approval_path = root / "cleanup-approval.json"
    signature_path = root / "cleanup-approval.sig.json"
    public_key_path = root / "cleanup-approver.pub"
    approval_path.write_bytes(approval.canonical_bytes() + b"\n")
    signature_path.write_bytes(signature.canonical_bytes() + b"\n")
    public_key_path.write_text(key.public_key().export_key(format="PEM"), encoding="ascii")
    public_key_sha256 = hashlib.sha256(
        key.public_key().export_key(format="DER", compress=False)
    ).hexdigest()
    return (
        RetirementApprovalPaths(approval_path, signature_path, public_key_path),
        key_id,
        public_key_sha256,
        approval,
    )


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

    assert manifest.schema_version == 3
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


def test_cleanup_preflight_requires_fresh_post_approval_unchanged_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluated_ts_ns = time.time_ns() // 1_000 * 1_000
    approved_bundle_ts_ns = evaluated_ts_ns - 120_000_000_000
    manifest_ts_ns = evaluated_ts_ns - 90_000_000_000
    archive = _archive(approved_bundle_ts_ns)
    report = _disabled_report(approved_bundle_ts_ns, archive)
    policy = load_retirement_policy(POLICY_PATH)
    scan_policy = load_legacy_archive_credential_scan_policy(SCAN_POLICY_PATH)
    approved_root = tmp_path / "approved-cleanup-evidence"
    action_root = tmp_path / "action-cleanup-evidence"
    _bundle(approved_root, approved_bundle_ts_ns, archive, report)
    _bundle(action_root, evaluated_ts_ns, archive, report)

    monkeypatch.setattr(cleanup_module, "time_ns", lambda: manifest_ts_ns)
    manifest = assemble_cleanup_manifest(
        approved_root,
        report,
        archive,
        policy=policy,
        credential_scan_policy=scan_policy,
    )
    approved_at = datetime.fromtimestamp(
        (evaluated_ts_ns - 60_000_000_000) / 1_000_000_000,
        UTC,
    )
    approval_paths, key_id, public_key_sha256, _ = _cleanup_approval(
        tmp_path,
        manifest,
        report,
        archive,
        approved_at,
    )
    monkeypatch.setattr(cleanup_module, "time_ns", lambda: evaluated_ts_ns)
    monkeypatch.setattr(preflight_module, "time_ns", lambda: evaluated_ts_ns)

    receipt = evaluate_cleanup_preflight(
        approved_root,
        action_root,
        manifest,
        report,
        archive,
        cleanup_approval_paths=approval_paths,
        policy=policy,
        credential_scan_policy=scan_policy,
        expected_cleanup_key_id=key_id,
        expected_cleanup_public_key_sha256=public_key_sha256,
    )

    assert receipt.ready_for_operator_action is True
    assert receipt.execution_mode == "evidence_only"
    assert receipt.operator_action_required is True
    assert receipt.approved_cleanup_manifest_sha256 == manifest.sha256()
    assert receipt.targets[0].state_matches is True
    assert {item.gate for item in receipt.gates} == set(CleanupPreflightGate)

    monkeypatch.setattr(preflight_module, "time_ns", lambda: evaluated_ts_ns - 1)
    with pytest.raises(ValueError, match="dated after verification"):
        verify_cleanup_preflight(
            approved_root,
            action_root,
            receipt,
            manifest,
            report,
            archive,
            cleanup_approval_paths=approval_paths,
            policy=policy,
            credential_scan_policy=scan_policy,
            expected_cleanup_key_id=key_id,
            expected_cleanup_public_key_sha256=public_key_sha256,
        )
    monkeypatch.setattr(preflight_module, "time_ns", lambda: evaluated_ts_ns)
    assert (
        verify_cleanup_preflight(
            approved_root,
            action_root,
            receipt,
            manifest,
            report,
            archive,
            cleanup_approval_paths=approval_paths,
            policy=policy,
            credential_scan_policy=scan_policy,
            expected_cleanup_key_id=key_id,
            expected_cleanup_public_key_sha256=public_key_sha256,
        )
        == receipt
    )
    receipt_path = tmp_path / "cleanup-preflight.json"
    receipt_path.write_bytes(receipt.canonical_bytes() + b"\n")
    assert load_cleanup_preflight_receipt(receipt_path) == receipt
    receipt_path.write_bytes(receipt.canonical_bytes())
    with pytest.raises(ValueError, match="not canonical JSON"):
        load_cleanup_preflight_receipt(receipt_path)

    with pytest.raises(ValueError, match="predates cleanup approval"):
        evaluate_cleanup_preflight(
            approved_root,
            approved_root,
            manifest,
            report,
            archive,
            cleanup_approval_paths=approval_paths,
            policy=policy,
            credential_scan_policy=scan_policy,
            expected_cleanup_key_id=key_id,
            expected_cleanup_public_key_sha256=public_key_sha256,
        )

    with pytest.raises(ValueError, match="does not permit cleanup preflight"):
        evaluate_cleanup_preflight(
            approved_root,
            action_root,
            manifest,
            report.model_copy(update={"awaiting_cleanup_approval": False}),
            archive,
            cleanup_approval_paths=approval_paths,
            policy=policy,
            credential_scan_policy=scan_policy,
            expected_cleanup_key_id=key_id,
            expected_cleanup_public_key_sha256=public_key_sha256,
        )

    with pytest.raises(ValueError, match="cannot be negative"):
        preflight_module._evaluate_cleanup_preflight(
            approved_root,
            action_root,
            manifest,
            report,
            archive,
            cleanup_approval_paths=approval_paths,
            policy=policy,
            credential_scan_policy=scan_policy,
            expected_cleanup_key_id=key_id,
            expected_cleanup_public_key_sha256=public_key_sha256,
            evaluated_ts_ns=-1,
        )

    early_paths, early_key_id, early_public_key_sha256, _ = _cleanup_approval(
        tmp_path / "early-approval",
        manifest,
        report,
        archive,
        datetime.fromtimestamp(
            (manifest_ts_ns - 10_000_000_000) / 1_000_000_000,
            UTC,
        ),
    )
    with pytest.raises(ValueError, match="predates the approved cleanup manifest"):
        evaluate_cleanup_preflight(
            approved_root,
            action_root,
            manifest,
            report,
            archive,
            cleanup_approval_paths=early_paths,
            policy=policy,
            credential_scan_policy=scan_policy,
            expected_cleanup_key_id=early_key_id,
            expected_cleanup_public_key_sha256=early_public_key_sha256,
        )

    inventory_drift_root = tmp_path / "inventory-drifted-action-evidence"
    _bundle(
        inventory_drift_root,
        evaluated_ts_ns,
        archive,
        report,
        rationale="different cleanup rationale",
    )
    with pytest.raises(ValueError, match="target inventory differs"):
        evaluate_cleanup_preflight(
            approved_root,
            inventory_drift_root,
            manifest,
            report,
            archive,
            cleanup_approval_paths=approval_paths,
            policy=policy,
            credential_scan_policy=scan_policy,
            expected_cleanup_key_id=key_id,
            expected_cleanup_public_key_sha256=public_key_sha256,
        )

    drift_root = tmp_path / "drifted-action-evidence"
    _bundle(
        drift_root,
        evaluated_ts_ns,
        archive,
        report,
        root_state_sha256="0" * 64,
    )
    with pytest.raises(ValueError, match="target state differs"):
        evaluate_cleanup_preflight(
            approved_root,
            drift_root,
            manifest,
            report,
            archive,
            cleanup_approval_paths=approval_paths,
            policy=policy,
            credential_scan_policy=scan_policy,
            expected_cleanup_key_id=key_id,
            expected_cleanup_public_key_sha256=public_key_sha256,
        )

    with pytest.raises(ValueError, match="is stale"):
        preflight_module._evaluate_cleanup_preflight(
            approved_root,
            action_root,
            manifest,
            report,
            archive,
            cleanup_approval_paths=approval_paths,
            policy=policy,
            credential_scan_policy=scan_policy,
            expected_cleanup_key_id=key_id,
            expected_cleanup_public_key_sha256=public_key_sha256,
            evaluated_ts_ns=(evaluated_ts_ns + policy.maximum_final_state_capture_skew_ns),
        )

    manifest_path = tmp_path / "approved-cleanup-manifest.json"
    cli_receipt_path = tmp_path / "cli-cleanup-preflight.json"
    manifest_path.write_bytes(manifest.canonical_bytes() + b"\n")
    monkeypatch.setattr(
        "aiquanttrader_native.retirement.cli._replay_cleanup_sources",
        lambda *args, **kwargs: (report, archive),
    )
    dummy = str(tmp_path / "source-placeholder.json")
    preflight_args = [
        "--evidence-root",
        str(approved_root),
        "--action-evidence-root",
        str(action_root),
        "--disabled-evidence-root",
        str(approved_root),
        "--native-evidence-root",
        str(approved_root),
        "--legacy-evidence-root",
        str(approved_root),
        "--readiness-observation",
        dummy,
        "--readiness-report",
        dummy,
        "--native-observation",
        dummy,
        "--archive-manifest",
        dummy,
        "--stop-approval",
        dummy,
        "--stop-signature",
        dummy,
        "--stop-public-key",
        dummy,
        "--disabled-observation",
        dummy,
        "--disabled-report",
        dummy,
        "--policy",
        str(POLICY_PATH),
        "--credential-scan-policy",
        str(SCAN_POLICY_PATH),
        "--native-approval-key-id",
        "native-test",
        "--native-approval-public-key-sha256",
        "a" * 64,
        "--stop-approval-key-id",
        "stop-test",
        "--stop-approval-public-key-sha256",
        "b" * 64,
        "--cleanup-manifest",
        str(manifest_path),
        "--cleanup-approval",
        str(approval_paths.approval_path),
        "--cleanup-signature",
        str(approval_paths.signature_path),
        "--cleanup-public-key",
        str(approval_paths.public_key_path),
        "--cleanup-approval-key-id",
        key_id,
        "--cleanup-approval-public-key-sha256",
        public_key_sha256,
    ]
    assert (
        retirement_main(
            [
                "prepare-cleanup-preflight",
                *preflight_args,
                "--output",
                str(cli_receipt_path),
            ]
        )
        == 0
    )
    assert (
        retirement_main(
            [
                "verify-cleanup-preflight",
                *preflight_args,
                "--preflight",
                str(cli_receipt_path),
            ]
        )
        == 0
    )

    action_plan_ts_ns = evaluated_ts_ns + 1
    action_plan_path = tmp_path / "cleanup-action-plan.json"
    monkeypatch.setattr(action_plan_module, "time_ns", lambda: action_plan_ts_ns)
    assert (
        retirement_main(
            [
                "prepare-cleanup-action-plan",
                *preflight_args,
                "--preflight",
                str(cli_receipt_path),
                "--output",
                str(action_plan_path),
            ]
        )
        == 0
    )
    plan = load_cleanup_action_plan(action_plan_path)
    assert CleanupActionPlan.model_validate_json(plan.canonical_bytes()) == plan
    assert plan.execution_mode == "evidence_only"
    assert plan.commands_included is False
    assert plan.operator_ledger_required is True
    assert plan.valid_until_ts_ns == receipt.valid_until_ts_ns
    assert len(plan.steps) == 1
    assert plan.steps[0].target_id == "legacy-mt5-source"
    assert plan.steps[0].stage is CleanupActionStage.REPOSITORY_RETIREMENT
    assert plan.steps[0].required_outcome is CleanupActionOutcomeKind.REMOVED_PATH
    assert plan.steps[0].evidence_requirements == (
        CleanupActionEvidenceRequirement.APPROVED_PRE_ACTION_STATE,
        CleanupActionEvidenceRequirement.ACTION_START_INSIDE_AUTHORITY,
        CleanupActionEvidenceRequirement.PATH_ABSENCE,
        CleanupActionEvidenceRequirement.RAW_EVIDENCE_AFTER_ACTION,
        CleanupActionEvidenceRequirement.INDEPENDENT_REVIEW,
        CleanupActionEvidenceRequirement.ZERO_FINDING_CREDENTIAL_SCAN,
    )
    with pytest.raises(ValidationError, match="plan identity does not match"):
        CleanupActionPlan.model_validate(
            {
                **plan.model_dump(mode="json"),
                "plan_id": "0" * 64,
            }
        )
    with pytest.raises(ValidationError, match="outside its preflight validity"):
        CleanupActionPlan.model_validate(
            {
                **plan.model_dump(mode="json"),
                "prepared_ts_ns": plan.valid_until_ts_ns,
            }
        )
    second_sequence = action_plan_module._build_step(2, manifest.targets[0])
    with pytest.raises(ValidationError, match="targets must be unique"):
        CleanupActionPlan.model_validate(
            {
                **plan.model_dump(mode="json"),
                "steps": (plan.steps[0], second_sequence),
            }
        )
    with pytest.raises(ValidationError, match="sequence must be contiguous"):
        CleanupActionPlan.model_validate(
            {
                **plan.model_dump(mode="json"),
                "steps": (second_sequence,),
            }
        )
    direct_plan = prepare_cleanup_action_plan(
        approved_root,
        action_root,
        receipt,
        manifest,
        report,
        archive,
        cleanup_approval_paths=approval_paths,
        policy=policy,
        credential_scan_policy=scan_policy,
        expected_cleanup_key_id=key_id,
        expected_cleanup_public_key_sha256=public_key_sha256,
    )
    assert direct_plan == plan
    assert (
        verify_cleanup_action_plan(
            approved_root,
            action_root,
            plan,
            receipt,
            manifest,
            report,
            archive,
            cleanup_approval_paths=approval_paths,
            policy=policy,
            credential_scan_policy=scan_policy,
            expected_cleanup_key_id=key_id,
            expected_cleanup_public_key_sha256=public_key_sha256,
        )
        == plan
    )
    monkeypatch.setattr(action_plan_module, "time_ns", lambda: action_plan_ts_ns - 1)
    with pytest.raises(ValueError, match="dated after verification"):
        verify_cleanup_action_plan(
            approved_root,
            action_root,
            plan,
            receipt,
            manifest,
            report,
            archive,
            cleanup_approval_paths=approval_paths,
            policy=policy,
            credential_scan_policy=scan_policy,
            expected_cleanup_key_id=key_id,
            expected_cleanup_public_key_sha256=public_key_sha256,
        )
    monkeypatch.setattr(action_plan_module, "time_ns", lambda: action_plan_ts_ns)
    forged_plan_payload = {
        **plan.model_dump(mode="json"),
        "native_deployment_id": "forged-native-deployment",
    }
    forged_plan_identity = {
        key: value for key, value in forged_plan_payload.items() if key != "plan_id"
    }
    forged_plan = CleanupActionPlan.model_validate(
        {
            **forged_plan_payload,
            "plan_id": canonical_sha256(forged_plan_identity),
        }
    )
    with pytest.raises(ValueError, match="does not match source replay"):
        verify_cleanup_action_plan(
            approved_root,
            action_root,
            forged_plan,
            receipt,
            manifest,
            report,
            archive,
            cleanup_approval_paths=approval_paths,
            policy=policy,
            credential_scan_policy=scan_policy,
            expected_cleanup_key_id=key_id,
            expected_cleanup_public_key_sha256=public_key_sha256,
        )
    with pytest.raises(ValueError, match="does not permit an action plan"):
        action_plan_module._build_cleanup_action_plan(
            receipt.model_copy(update={"ready_for_operator_action": False}),
            manifest,
            prepared_ts_ns=action_plan_ts_ns,
        )
    with pytest.raises(ValueError, match="outside the preflight window"):
        action_plan_module._build_cleanup_action_plan(
            receipt,
            manifest,
            prepared_ts_ns=receipt.evaluated_ts_ns - 1,
        )
    with pytest.raises(ValueError, match="differs from preflight authority"):
        action_plan_module._build_cleanup_action_plan(
            receipt.model_copy(update={"approved_cleanup_manifest_sha256": "0" * 64}),
            manifest,
            prepared_ts_ns=action_plan_ts_ns,
        )
    with pytest.raises(ValueError, match="differ from the verified preflight"):
        action_plan_module._build_cleanup_action_plan(
            receipt.model_copy(
                update={
                    "targets": (
                        receipt.targets[0].model_copy(update={"observed_state_sha256": "0" * 64}),
                    )
                }
            ),
            manifest,
            prepared_ts_ns=action_plan_ts_ns,
        )
    monkeypatch.setattr(action_plan_module, "time_ns", lambda: receipt.valid_until_ts_ns)
    with pytest.raises(ValueError, match="expired while preparing"):
        prepare_cleanup_action_plan(
            approved_root,
            action_root,
            receipt,
            manifest,
            report,
            archive,
            cleanup_approval_paths=approval_paths,
            policy=policy,
            credential_scan_policy=scan_policy,
            expected_cleanup_key_id=key_id,
            expected_cleanup_public_key_sha256=public_key_sha256,
        )
    monkeypatch.setattr(action_plan_module, "time_ns", lambda: action_plan_ts_ns)
    assert (
        retirement_main(
            [
                "verify-cleanup-action-plan",
                *preflight_args,
                "--preflight",
                str(cli_receipt_path),
                "--action-plan",
                str(action_plan_path),
            ]
        )
        == 0
    )
    action_plan_path.write_bytes(plan.canonical_bytes())
    with pytest.raises(ValueError, match="not canonical JSON"):
        load_cleanup_action_plan(action_plan_path)

    monkeypatch.setattr(
        preflight_module,
        "time_ns",
        lambda: receipt.valid_until_ts_ns,
    )
    with pytest.raises(ValueError, match="has expired"):
        verify_cleanup_preflight(
            approved_root,
            action_root,
            receipt,
            manifest,
            report,
            archive,
            cleanup_approval_paths=approval_paths,
            policy=policy,
            credential_scan_policy=scan_policy,
            expected_cleanup_key_id=key_id,
            expected_cleanup_public_key_sha256=public_key_sha256,
        )
    monkeypatch.setattr(action_plan_module, "time_ns", lambda: receipt.valid_until_ts_ns)
    with pytest.raises(ValueError, match="action plan has expired"):
        verify_cleanup_action_plan(
            approved_root,
            action_root,
            plan,
            receipt,
            manifest,
            report,
            archive,
            cleanup_approval_paths=approval_paths,
            policy=policy,
            credential_scan_policy=scan_policy,
            expected_cleanup_key_id=key_id,
            expected_cleanup_public_key_sha256=public_key_sha256,
        )


def test_cleanup_preflight_receipt_contract_rejects_forged_verdict_and_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluated_ts_ns = time.time_ns() // 1_000 * 1_000
    approved_bundle_ts_ns = evaluated_ts_ns - 120_000_000_000
    archive = _archive(approved_bundle_ts_ns)
    report = _disabled_report(approved_bundle_ts_ns, archive)
    policy = load_retirement_policy(POLICY_PATH)
    scan_policy = load_legacy_archive_credential_scan_policy(SCAN_POLICY_PATH)
    approved_root = tmp_path / "approved"
    action_root = tmp_path / "action"
    _bundle(approved_root, approved_bundle_ts_ns, archive, report)
    _bundle(action_root, evaluated_ts_ns, archive, report)
    monkeypatch.setattr(cleanup_module, "time_ns", lambda: evaluated_ts_ns - 90_000_000_000)
    manifest = assemble_cleanup_manifest(
        approved_root,
        report,
        archive,
        policy=policy,
        credential_scan_policy=scan_policy,
    )
    approval_paths, key_id, public_key_sha256, _ = _cleanup_approval(
        tmp_path,
        manifest,
        report,
        archive,
        datetime.fromtimestamp(
            (evaluated_ts_ns - 60_000_000_000) / 1_000_000_000,
            UTC,
        ),
    )
    monkeypatch.setattr(cleanup_module, "time_ns", lambda: evaluated_ts_ns)
    monkeypatch.setattr(preflight_module, "time_ns", lambda: evaluated_ts_ns)
    receipt = evaluate_cleanup_preflight(
        approved_root,
        action_root,
        manifest,
        report,
        archive,
        cleanup_approval_paths=approval_paths,
        policy=policy,
        credential_scan_policy=scan_policy,
        expected_cleanup_key_id=key_id,
        expected_cleanup_public_key_sha256=public_key_sha256,
    )
    payload = receipt.model_dump(mode="json")

    with pytest.raises(ValidationError, match="capture or validity interval"):
        CleanupPreflightReceipt.model_validate(
            {
                **payload,
                "action_capture_start_ts_ns": payload["evaluated_ts_ns"] + 1,
            }
        )
    with pytest.raises(ValidationError, match="earliest expiry"):
        CleanupPreflightReceipt.model_validate(
            {**payload, "valid_until_ts_ns": payload["valid_until_ts_ns"] + 1}
        )
    with pytest.raises(ValidationError, match="verdict"):
        CleanupPreflightReceipt.model_validate({**payload, "ready_for_operator_action": False})
    with pytest.raises(ValidationError, match="targets must be unique"):
        CleanupPreflightReceipt.model_validate(
            {**payload, "targets": [payload["targets"][0], payload["targets"][0]]}
        )
    with pytest.raises(ValidationError, match="every gate exactly once"):
        CleanupPreflightReceipt.model_validate({**payload, "gates": payload["gates"][:-1]})
    with pytest.raises(ValidationError, match="identity does not match"):
        CleanupPreflightReceipt.model_validate({**payload, "receipt_id": "0" * 64})

    forged = {**payload, "gates": [*payload["gates"]]}
    forged["gates"][0] = {**forged["gates"][0], "actual": "forged-current-state"}
    identity = {
        key: value
        for key, value in forged.items()
        if key not in {"receipt_id", "ready_for_operator_action"}
    }
    forged["receipt_id"] = canonical_sha256(identity)
    forged_receipt = CleanupPreflightReceipt.model_validate(forged)
    with pytest.raises(ValueError, match="does not match source replay"):
        verify_cleanup_preflight(
            approved_root,
            action_root,
            forged_receipt,
            manifest,
            report,
            archive,
            cleanup_approval_paths=approval_paths,
            policy=policy,
            credential_scan_policy=scan_policy,
            expected_cleanup_key_id=key_id,
            expected_cleanup_public_key_sha256=public_key_sha256,
        )


def _outcome_bundle(
    root: Path,
    *,
    receipt: CleanupPreflightReceipt,
    manifest: LegacyCleanupManifest,
    archive: LegacyArchiveManifest,
    report: DisabledObservationReport,
    action_started_ts_ns: int | None = None,
) -> int:
    policy = load_retirement_policy(POLICY_PATH)
    scan_policy = load_legacy_archive_credential_scan_policy(SCAN_POLICY_PATH)
    started_ts_ns = (
        receipt.evaluated_ts_ns + 1_000_000_000
        if action_started_ts_ns is None
        else action_started_ts_ns
    )
    completed_ts_ns = started_ts_ns + 1_000_000_000
    raw_ts_ns = completed_ts_ns + 1_000_000_000
    target_ts_ns = raw_ts_ns + 1_000_000_000
    scan_start_ts_ns = target_ts_ns + 1_000_000_000
    scan_end_ts_ns = scan_start_ts_ns + 1_000_000_000
    scan_reviewed_ts_ns = scan_end_ts_ns + 1_000_000_000
    created_ts_ns = scan_reviewed_ts_ns + 1_000_000_000

    absence = CleanupPathAbsenceEvidence(
        kind=CleanupTargetKind.REPOSITORY_PATH,
        locator=manifest.targets[0].locator,
        observed_commit_sha="c" * 40,
        captured_ts_ns=raw_ts_ns,
        observation_source="git-tree-audit",
    )
    raw_payload = absence.canonical_bytes() + b"\n"
    raw_sha, raw_size = _write(root / "raw/repository/legacy-mt5-absence.json", raw_payload)
    artifact = CleanupEvidenceArtifact(
        artifact_id="legacy-mt5-absence",
        relative_path="raw/repository/legacy-mt5-absence.json",
        content_sha256=raw_sha,
        byte_count=raw_size,
        captured_ts_ns=raw_ts_ns,
    )
    target = CleanupTargetOutcomeEvidence(
        retirement_id=manifest.retirement_id,
        target_id=manifest.targets[0].target_id,
        kind=CleanupTargetKind.REPOSITORY_PATH,
        locator=manifest.targets[0].locator,
        action=CleanupAction.REMOVE,
        pre_action_state_sha256=manifest.targets[0].expected_state_sha256,
        action_started_ts_ns=started_ts_ns,
        action_completed_ts_ns=completed_ts_ns,
        captured_ts_ns=target_ts_ns,
        collected_by="cleanup-operator",
        reviewed_by="cleanup-reviewer",
        result=CleanupRemovedPathResult(
            kind=CleanupTargetKind.REPOSITORY_PATH,
            locator=manifest.targets[0].locator,
            observed_commit_sha="c" * 40,
            raw_artifact_id=artifact.artifact_id,
        ),
    )
    target_sha, target_size = _write(
        root / "controls/targets/legacy-mt5-source.json",
        target.canonical_bytes() + b"\n",
    )
    target_control = CleanupOutcomeControl(
        kind=CleanupOutcomeControlKind.TARGET_OUTCOME,
        reference_id=target.target_id,
        relative_path="controls/targets/legacy-mt5-source.json",
        content_sha256=target_sha,
        byte_count=target_size,
        captured_ts_ns=target_ts_ns,
    )
    scan = CleanupCredentialScanEvidence(
        retirement_id=manifest.retirement_id,
        started_ts_ns=scan_start_ts_ns,
        ended_ts_ns=scan_end_ts_ns,
        reviewed_ts_ns=scan_reviewed_ts_ns,
        reviewer="security-reviewer",
        scanner_name="gitleaks-and-private-key-scan",
        scanner_version="1.0.0",
        policy_id=scan_policy.policy_id,
        policy_sha256=scan_policy.sha256(),
        checks=(
            CleanupCredentialScanCheck(
                relative_path=artifact.relative_path,
                content_sha256=artifact.content_sha256,
            ),
            CleanupCredentialScanCheck(
                relative_path=target_control.relative_path,
                content_sha256=target_control.content_sha256,
            ),
        ),
    )
    scan_sha, scan_size = _write(
        root / "controls/credential-scan.json",
        scan.canonical_bytes() + b"\n",
    )
    scan_control = CleanupOutcomeControl(
        kind=CleanupOutcomeControlKind.CREDENTIAL_SCAN,
        reference_id="credential-scan",
        relative_path="controls/credential-scan.json",
        content_sha256=scan_sha,
        byte_count=scan_size,
        captured_ts_ns=scan_reviewed_ts_ns,
    )
    outcome_manifest = CleanupOutcomeEvidenceManifest(
        retirement_id=manifest.retirement_id,
        created_ts_ns=created_ts_ns,
        policy_id=policy.policy_id,
        policy_sha256=policy.sha256(),
        credential_scan_policy_id=scan_policy.policy_id,
        credential_scan_policy_sha256=scan_policy.sha256(),
        source_commit_sha=manifest.source_commit_sha,
        archive_manifest_sha256=archive.sha256(),
        disabled_observation_report_sha256=report.sha256(),
        cleanup_manifest_sha256=manifest.sha256(),
        cleanup_preflight_receipt_sha256=receipt.sha256(),
        artifacts=(artifact,),
        controls=(target_control, scan_control),
    )
    _write(
        root / outcome_module.OUTCOME_MANIFEST_NAME,
        outcome_manifest.canonical_bytes() + b"\n",
    )
    return created_ts_ns + 1_000_000_000


def _completion_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[
    Path,
    Path,
    LegacyCleanupManifest,
    DisabledObservationReport,
    LegacyArchiveManifest,
    RetirementApprovalPaths,
    str,
    str,
    CleanupPreflightReceipt,
]:
    evaluated_ts_ns = time.time_ns() // 1_000 * 1_000
    approved_ts_ns = evaluated_ts_ns - 120_000_000_000
    archive = _archive(approved_ts_ns)
    report = _disabled_report(approved_ts_ns, archive)
    policy = load_retirement_policy(POLICY_PATH)
    scan_policy = load_legacy_archive_credential_scan_policy(SCAN_POLICY_PATH)
    approved_root = tmp_path / "approved-cleanup"
    action_root = tmp_path / "action-cleanup"
    _bundle(approved_root, approved_ts_ns, archive, report)
    _bundle(action_root, evaluated_ts_ns, archive, report)
    monkeypatch.setattr(cleanup_module, "time_ns", lambda: evaluated_ts_ns - 90_000_000_000)
    manifest = assemble_cleanup_manifest(
        approved_root,
        report,
        archive,
        policy=policy,
        credential_scan_policy=scan_policy,
    )
    approval_paths, key_id, public_key_sha256, _ = _cleanup_approval(
        tmp_path / "approval",
        manifest,
        report,
        archive,
        datetime.fromtimestamp((evaluated_ts_ns - 60_000_000_000) / 1_000_000_000, UTC),
    )
    monkeypatch.setattr(cleanup_module, "time_ns", lambda: evaluated_ts_ns)
    monkeypatch.setattr(preflight_module, "time_ns", lambda: evaluated_ts_ns)
    receipt = evaluate_cleanup_preflight(
        approved_root,
        action_root,
        manifest,
        report,
        archive,
        cleanup_approval_paths=approval_paths,
        policy=policy,
        credential_scan_policy=scan_policy,
        expected_cleanup_key_id=key_id,
        expected_cleanup_public_key_sha256=public_key_sha256,
    )
    return (
        approved_root,
        action_root,
        manifest,
        report,
        archive,
        approval_paths,
        key_id,
        public_key_sha256,
        receipt,
    )


def test_cleanup_completion_replays_exact_postconditions_after_receipt_expiry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        approved_root,
        action_root,
        manifest,
        report,
        archive,
        approval_paths,
        key_id,
        public_key_sha256,
        receipt,
    ) = _completion_sources(tmp_path, monkeypatch)
    outcome_root = tmp_path / "cleanup-outcome"
    generated_ts_ns = _outcome_bundle(
        outcome_root,
        receipt=receipt,
        manifest=manifest,
        archive=archive,
        report=report,
    )
    policy = load_retirement_policy(POLICY_PATH)
    scan_policy = load_legacy_archive_credential_scan_policy(SCAN_POLICY_PATH)
    monkeypatch.setattr(outcome_module, "time_ns", lambda: generated_ts_ns)

    completion = assemble_cleanup_completion(
        outcome_root,
        approved_root,
        action_root,
        receipt,
        manifest,
        report,
        archive,
        cleanup_approval_paths=approval_paths,
        policy=policy,
        credential_scan_policy=scan_policy,
        expected_cleanup_key_id=key_id,
        expected_cleanup_public_key_sha256=public_key_sha256,
    )

    assert completion.cleanup_complete is True
    assert completion.verification_mode == "evidence_only"
    assert completion.operator_actions_observed is True
    assert completion.targets[0].postcondition_met is True
    assert {item.gate for item in completion.gates} == set(CleanupOutcomeGate)

    report_path = tmp_path / "cleanup-completion.json"
    report_path.write_bytes(completion.canonical_bytes() + b"\n")
    assert load_cleanup_completion_report(report_path) == completion
    report_path.write_bytes(completion.canonical_bytes())
    with pytest.raises(ValueError, match="not canonical JSON"):
        load_cleanup_completion_report(report_path)
    report_path.write_bytes(completion.canonical_bytes() + b"\n")
    with pytest.raises(ValidationError, match="verdict"):
        CleanupCompletionReport.model_validate(
            {**completion.model_dump(mode="json"), "cleanup_complete": False}
        )
    monkeypatch.setattr(outcome_module, "time_ns", lambda: receipt.valid_until_ts_ns + 1)
    assert (
        verify_cleanup_completion(
            outcome_root,
            approved_root,
            action_root,
            completion,
            receipt,
            manifest,
            report,
            archive,
            cleanup_approval_paths=approval_paths,
            policy=policy,
            credential_scan_policy=scan_policy,
            expected_cleanup_key_id=key_id,
            expected_cleanup_public_key_sha256=public_key_sha256,
        )
        == completion
    )

    manifest_path = tmp_path / "cleanup-manifest.json"
    receipt_path = tmp_path / "cleanup-preflight.json"
    cli_report_path = tmp_path / "cli-cleanup-completion.json"
    manifest_path.write_bytes(manifest.canonical_bytes() + b"\n")
    receipt_path.write_bytes(receipt.canonical_bytes() + b"\n")
    monkeypatch.setattr(outcome_module, "time_ns", lambda: generated_ts_ns)
    monkeypatch.setattr(
        "aiquanttrader_native.retirement.cli._replay_cleanup_sources",
        lambda *args, **kwargs: (report, archive),
    )
    dummy = str(tmp_path / "source-placeholder.json")
    cli_args = [
        "--evidence-root",
        str(approved_root),
        "--action-evidence-root",
        str(action_root),
        "--outcome-evidence-root",
        str(outcome_root),
        "--preflight",
        str(receipt_path),
        "--cleanup-manifest",
        str(manifest_path),
        "--disabled-evidence-root",
        str(approved_root),
        "--native-evidence-root",
        str(approved_root),
        "--legacy-evidence-root",
        str(approved_root),
        "--readiness-observation",
        dummy,
        "--readiness-report",
        dummy,
        "--native-observation",
        dummy,
        "--archive-manifest",
        dummy,
        "--stop-approval",
        dummy,
        "--stop-signature",
        dummy,
        "--stop-public-key",
        dummy,
        "--disabled-observation",
        dummy,
        "--disabled-report",
        dummy,
        "--policy",
        str(POLICY_PATH),
        "--credential-scan-policy",
        str(SCAN_POLICY_PATH),
        "--native-approval-key-id",
        "native-test",
        "--native-approval-public-key-sha256",
        "a" * 64,
        "--stop-approval-key-id",
        "stop-test",
        "--stop-approval-public-key-sha256",
        "b" * 64,
        "--cleanup-approval",
        str(approval_paths.approval_path),
        "--cleanup-signature",
        str(approval_paths.signature_path),
        "--cleanup-public-key",
        str(approval_paths.public_key_path),
        "--cleanup-approval-key-id",
        key_id,
        "--cleanup-approval-public-key-sha256",
        public_key_sha256,
    ]
    assert (
        retirement_main(
            ["assemble-cleanup-completion", *cli_args, "--output", str(cli_report_path)]
        )
        == 0
    )
    assert (
        retirement_main(["verify-cleanup-completion", *cli_args, "--report", str(cli_report_path)])
        == 0
    )

    (outcome_root / "raw/repository/legacy-mt5-absence.json").write_text(
        '{"exists":true}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="digest or size differs"):
        verify_cleanup_completion(
            outcome_root,
            approved_root,
            action_root,
            completion,
            receipt,
            manifest,
            report,
            archive,
            cleanup_approval_paths=approval_paths,
            policy=policy,
            credential_scan_policy=scan_policy,
            expected_cleanup_key_id=key_id,
            expected_cleanup_public_key_sha256=public_key_sha256,
        )


def test_cleanup_completion_rejects_action_started_at_expiry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        approved_root,
        action_root,
        manifest,
        report,
        archive,
        approval_paths,
        key_id,
        public_key_sha256,
        receipt,
    ) = _completion_sources(tmp_path, monkeypatch)
    outcome_root = tmp_path / "late-cleanup-outcome"
    generated_ts_ns = _outcome_bundle(
        outcome_root,
        receipt=receipt,
        manifest=manifest,
        archive=archive,
        report=report,
        action_started_ts_ns=receipt.valid_until_ts_ns,
    )
    monkeypatch.setattr(outcome_module, "time_ns", lambda: generated_ts_ns)

    with pytest.raises(ValueError, match="did not start inside preflight validity"):
        assemble_cleanup_completion(
            outcome_root,
            approved_root,
            action_root,
            receipt,
            manifest,
            report,
            archive,
            cleanup_approval_paths=approval_paths,
            policy=load_retirement_policy(POLICY_PATH),
            credential_scan_policy=load_legacy_archive_credential_scan_policy(SCAN_POLICY_PATH),
            expected_cleanup_key_id=key_id,
            expected_cleanup_public_key_sha256=public_key_sha256,
        )


def test_cleanup_action_contract_requires_exact_native_destination() -> None:
    target = LegacyCleanupTarget(
        target_id="native-package-migration",
        kind=CleanupTargetKind.REPOSITORY_PATH,
        locator="native/src/aiquanttrader_native",
        action=CleanupAction.MIGRATE_NATIVE,
        destination_locator="src/aiquanttrader",
        expected_state_sha256="a" * 64,
        rationale="migrate the isolated native package after legacy removal",
    )
    assert target.destination_locator == "src/aiquanttrader"

    typed_targets = (
        LegacyCleanupTarget(
            target_id="repository-removal",
            kind=CleanupTargetKind.REPOSITORY_PATH,
            locator="broker/mt5",
            action=CleanupAction.REMOVE,
            expected_state_sha256="1" * 64,
            rationale="remove the retired MQL5 source",
        ),
        target,
        LegacyCleanupTarget(
            target_id="host-package-removal",
            kind=CleanupTargetKind.HOST_PACKAGE,
            locator="wine64",
            action=CleanupAction.REMOVE,
            expected_state_sha256="2" * 64,
            rationale="remove the project-owned Wine package",
        ),
        LegacyCleanupTarget(
            target_id="host-integration-removal",
            kind=CleanupTargetKind.HOST_INTEGRATION,
            locator="/etc/cron.d/aiquanttrader",
            action=CleanupAction.REMOVE,
            expected_state_sha256="3" * 64,
            rationale="remove the disabled legacy schedule",
        ),
        LegacyCleanupTarget(
            target_id="runtime-retirement",
            kind=CleanupTargetKind.RUNTIME_PATH,
            locator="/root/AIQuantTrader/.runtime/wineprefix",
            action=CleanupAction.RETAIN_ARCHIVE_ONLY,
            expected_state_sha256="4" * 64,
            rationale="retain the final archive instead of the operational runtime",
        ),
        LegacyCleanupTarget(
            target_id="credential-revocation",
            kind=CleanupTargetKind.SECRET_REFERENCE,
            locator="MT5_PASSWORD",
            action=CleanupAction.REVOKE,
            expected_state_sha256="5" * 64,
            rationale="revoke the legacy broker credential and sessions",
        ),
    )
    ordered = sorted(typed_targets, key=action_plan_module._stage_rank)
    steps = tuple(
        action_plan_module._build_step(index, item) for index, item in enumerate(ordered, start=1)
    )
    assert tuple(item.stage for item in steps) == (
        CleanupActionStage.CREDENTIAL_REVOCATION,
        CleanupActionStage.RUNTIME_RETIREMENT,
        CleanupActionStage.HOST_INTEGRATION_REMOVAL,
        CleanupActionStage.HOST_PACKAGE_REMOVAL,
        CleanupActionStage.NATIVE_REPOSITORY_MIGRATION,
        CleanupActionStage.REPOSITORY_RETIREMENT,
    )
    assert tuple(item.required_outcome for item in steps) == (
        CleanupActionOutcomeKind.REVOKED_SECRET,
        CleanupActionOutcomeKind.ARCHIVE_ONLY,
        CleanupActionOutcomeKind.REMOVED_HOST_DEPENDENCY,
        CleanupActionOutcomeKind.REMOVED_HOST_DEPENDENCY,
        CleanupActionOutcomeKind.NATIVE_MIGRATION,
        CleanupActionOutcomeKind.REMOVED_PATH,
    )
    forged_step = steps[0].model_dump(mode="json")
    with pytest.raises(ValidationError, match="stage does not match"):
        type(steps[0]).model_validate(
            {
                **forged_step,
                "stage": CleanupActionStage.REPOSITORY_RETIREMENT,
            }
        )
    with pytest.raises(ValidationError, match="outcome does not match"):
        type(steps[0]).model_validate(
            {
                **forged_step,
                "required_outcome": CleanupActionOutcomeKind.REMOVED_PATH,
            }
        )
    with pytest.raises(ValidationError, match="evidence requirements are not exact"):
        type(steps[0]).model_validate(
            {
                **forged_step,
                "evidence_requirements": steps[-1].evidence_requirements,
            }
        )
    with pytest.raises(ValidationError, match="step identity does not match"):
        type(steps[0]).model_validate({**forged_step, "step_id": "0" * 64})

    with pytest.raises(ValidationError, match="explicit destination"):
        LegacyCleanupTarget(
            target_id="missing-destination",
            kind=CleanupTargetKind.REPOSITORY_PATH,
            locator="native/src/aiquanttrader_native",
            action=CleanupAction.MIGRATE_NATIVE,
            expected_state_sha256="a" * 64,
            rationale="migrate the isolated native package after legacy removal",
        )
    with pytest.raises(ValidationError, match="only native migration"):
        LegacyCleanupTarget(
            target_id="unexpected-destination",
            kind=CleanupTargetKind.REPOSITORY_PATH,
            locator="broker/mt5",
            action=CleanupAction.REMOVE,
            destination_locator="src/aiquanttrader",
            expected_state_sha256="a" * 64,
            rationale="migrate the isolated native package after legacy removal",
        )
    with pytest.raises(ValidationError, match="distinct safe repository path"):
        LegacyCleanupTarget(
            target_id="unsafe-destination",
            kind=CleanupTargetKind.REPOSITORY_PATH,
            locator="native/src/aiquanttrader_native",
            action=CleanupAction.MIGRATE_NATIVE,
            destination_locator="../src/aiquanttrader",
            expected_state_sha256="a" * 64,
            rationale="unsafe destination must fail",
        )
    with pytest.raises(ValidationError, match="only secret references"):
        LegacyCleanupTarget(
            target_id="wrong-revoke-kind",
            kind=CleanupTargetKind.REPOSITORY_PATH,
            locator="broker/mt5",
            action=CleanupAction.REVOKE,
            expected_state_sha256="a" * 64,
            rationale="repository paths cannot be revoked",
        )
    with pytest.raises(ValidationError, match="host packages"):
        LegacyCleanupTarget(
            target_id="wrong-archive-kind",
            kind=CleanupTargetKind.HOST_PACKAGE,
            locator="wine64",
            action=CleanupAction.RETAIN_ARCHIVE_ONLY,
            expected_state_sha256="a" * 64,
            rationale="packages cannot be archive-only targets",
        )
    with pytest.raises(ValidationError, match="action and outcome result kind differ"):
        CleanupTargetOutcomeEvidence(
            retirement_id="retirement-cleanup-test",
            target_id="native-package-migration",
            kind=CleanupTargetKind.REPOSITORY_PATH,
            locator="native/src/aiquanttrader_native",
            action=CleanupAction.MIGRATE_NATIVE,
            destination_locator="src/aiquanttrader",
            pre_action_state_sha256="a" * 64,
            action_started_ts_ns=1,
            action_completed_ts_ns=2,
            captured_ts_ns=3,
            collected_by="operator",
            reviewed_by="reviewer",
            result=CleanupRemovedPathResult(
                kind=CleanupTargetKind.REPOSITORY_PATH,
                locator="native/src/aiquanttrader_native",
                observed_commit_sha="b" * 40,
                raw_artifact_id="source-proof",
            ),
        )
    result = CleanupNativeMigrationResult(
        locator="native/src/aiquanttrader_native",
        destination_locator="src/aiquanttrader",
        migration_commit_sha="b" * 40,
        destination_inventory_sha256="c" * 64,
        raw_artifact_ids=("source-proof", "destination-inventory"),
    )
    assert result.destination_exists is True

    overlapping = LegacyCleanupTarget(
        target_id="nested-source",
        kind=CleanupTargetKind.REPOSITORY_PATH,
        locator="native/src/aiquanttrader_native/retirement",
        action=CleanupAction.REMOVE,
        expected_state_sha256="d" * 64,
        rationale="overlapping removal must fail at manifest validation",
    )
    with pytest.raises(ValidationError, match="target paths cannot overlap"):
        LegacyCleanupManifest(
            retirement_id="retirement-cleanup-test",
            created_ts_ns=1,
            policy_id="retirement-policy",
            policy_sha256="e" * 64,
            source_commit_sha=COMMIT,
            archive_manifest_sha256="f" * 64,
            disabled_observation_report_sha256="1" * 64,
            evidence_manifest_sha256="2" * 64,
            credential_scan_sha256="3" * 64,
            evidence_bundle_sha256="4" * 64,
            targets=(target, overlapping),
        )


def test_cleanup_outcome_verifies_archive_host_migration_and_secret_postconditions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        _approved_root,
        _action_root,
        base_manifest,
        _report,
        archive,
        _approval_paths,
        _key_id,
        _public_key_sha256,
        base_receipt,
    ) = _completion_sources(tmp_path, monkeypatch)
    started = base_receipt.evaluated_ts_ns + 1_000_000_000
    completed = started + 1_000_000_000
    captured = completed + 1_000_000_000
    artifact_models: list[CleanupEvidenceArtifact] = []

    def write_artifact(artifact_id: str, relative_path: str, payload: bytes) -> None:
        sha256, size = _write(tmp_path / "variant-outcome" / relative_path, payload)
        artifact_models.append(
            CleanupEvidenceArtifact(
                artifact_id=artifact_id,
                relative_path=relative_path,
                content_sha256=sha256,
                byte_count=size,
                captured_ts_ns=completed,
            )
        )

    archive_absence = CleanupPathAbsenceEvidence(
        kind=CleanupTargetKind.RUNTIME_PATH,
        locator="/root/AIQuantTrader/.runtime/legacy-archive-only",
        captured_ts_ns=completed,
        observation_source="filesystem-audit",
    )
    write_artifact(
        "archive-absence",
        "raw/runtime/archive-absence.json",
        archive_absence.canonical_bytes() + b"\n",
    )
    host_proofs = tuple(
        CleanupHostAbsenceEvidence(
            kind=CleanupTargetKind.HOST_PACKAGE,
            locator="wine64",
            captured_ts_ns=completed,
            observation_source=source,
        )
        for source in ("dpkg-query", "executable-path-audit")
    )
    for index, proof in enumerate(host_proofs, start=1):
        write_artifact(
            f"host-absence-{index}",
            f"raw/host/host-absence-{index}.json",
            proof.canonical_bytes() + b"\n",
        )

    migration_absence = CleanupPathAbsenceEvidence(
        kind=CleanupTargetKind.REPOSITORY_PATH,
        locator="native/src/aiquanttrader_native",
        observed_commit_sha="d" * 40,
        captured_ts_ns=completed,
        observation_source="git-tree-audit",
    )
    write_artifact(
        "migration-source-absence",
        "raw/repository/migration-source-absence.json",
        migration_absence.canonical_bytes() + b"\n",
    )
    destination_inventory = CleanupPathInventoryEvidence(
        kind=CleanupTargetKind.REPOSITORY_PATH,
        locator="src/aiquanttrader",
        source_commit_sha="d" * 40,
        captured_ts_ns=completed,
        entries=(
            CleanupPathInventoryEntry(
                relative_path=".",
                object_type=CleanupPathObjectType.DIRECTORY,
                state_sha256="e" * 64,
                byte_count=0,
                mode="0755",
            ),
        ),
    )
    write_artifact(
        "migration-destination-inventory",
        "raw/repository/migration-destination-inventory.json",
        destination_inventory.canonical_bytes() + b"\n",
    )
    provider_payload = b'{"revoked":true}\n'
    sessions_payload = b'{"active_sessions":[]}\n'
    write_artifact("secret-provider", "raw/credentials/provider.json", provider_payload)
    write_artifact("secret-sessions", "raw/credentials/sessions.json", sessions_payload)

    secret_state = CleanupSecretState(
        locator="MT5_PASSWORD",
        provider="broker-vault",
        provider_record_id_sha256="5" * 64,
        provider_state_sha256="6" * 64,
        active_sessions_sha256="7" * 64,
        captured_ts_ns=1,
        raw_artifact_ids=("approved-provider", "approved-sessions"),
    )
    targets = (
        LegacyCleanupTarget(
            target_id="archive-only-runtime",
            kind=CleanupTargetKind.RUNTIME_PATH,
            locator=archive_absence.locator,
            action=CleanupAction.RETAIN_ARCHIVE_ONLY,
            expected_state_sha256="1" * 64,
            rationale="retain only the final credential-free archive",
        ),
        LegacyCleanupTarget(
            target_id="host-package-removal",
            kind=CleanupTargetKind.HOST_PACKAGE,
            locator="wine64",
            action=CleanupAction.REMOVE,
            expected_state_sha256="2" * 64,
            rationale="remove a project-owned package with no shared consumer",
        ),
        LegacyCleanupTarget(
            target_id="native-package-migration",
            kind=CleanupTargetKind.REPOSITORY_PATH,
            locator=migration_absence.locator,
            action=CleanupAction.MIGRATE_NATIVE,
            destination_locator=destination_inventory.locator,
            expected_state_sha256="3" * 64,
            rationale="perform the approved ADR 0008 package migration",
        ),
        LegacyCleanupTarget(
            target_id="secret-revocation",
            kind=CleanupTargetKind.SECRET_REFERENCE,
            locator=secret_state.locator,
            action=CleanupAction.REVOKE,
            expected_state_sha256=secret_state.expected_state_sha256(),
            rationale="revoke the exact legacy provider record and sessions",
        ),
    )
    cleanup_manifest = LegacyCleanupManifest.model_validate(
        {**base_manifest.model_dump(mode="json"), "targets": targets}
    )
    preflight_targets = tuple(
        CleanupPreflightTargetResult(
            target_id=item.target_id,
            kind=item.kind,
            locator=item.locator,
            action=item.action,
            destination_locator=item.destination_locator,
            expected_state_sha256=item.expected_state_sha256,
            observed_state_sha256=item.expected_state_sha256,
            state_matches=True,
        )
        for item in targets
    )
    receipt_payload = base_receipt.model_dump(
        mode="json",
        exclude={"receipt_id", "ready_for_operator_action"},
    )
    receipt_payload["targets"] = [item.model_dump(mode="json") for item in preflight_targets]
    preflight = CleanupPreflightReceipt.model_validate(
        {
            **receipt_payload,
            "receipt_id": canonical_sha256(receipt_payload),
            "ready_for_operator_action": True,
        }
    )
    artifact_by_id = {item.artifact_id: item for item in artifact_models}
    outcomes = (
        CleanupTargetOutcomeEvidence(
            retirement_id=cleanup_manifest.retirement_id,
            target_id=targets[0].target_id,
            kind=targets[0].kind,
            locator=targets[0].locator,
            action=targets[0].action,
            pre_action_state_sha256=targets[0].expected_state_sha256,
            action_started_ts_ns=started,
            action_completed_ts_ns=completed,
            captured_ts_ns=captured,
            collected_by="operator",
            reviewed_by="reviewer",
            result=CleanupArchiveOnlyResult(
                kind=CleanupTargetKind.RUNTIME_PATH,
                locator=targets[0].locator,
                archive_manifest_sha256=archive.sha256(),
                raw_artifact_id="archive-absence",
            ),
        ),
        CleanupTargetOutcomeEvidence(
            retirement_id=cleanup_manifest.retirement_id,
            target_id=targets[1].target_id,
            kind=targets[1].kind,
            locator=targets[1].locator,
            action=targets[1].action,
            pre_action_state_sha256=targets[1].expected_state_sha256,
            action_started_ts_ns=started,
            action_completed_ts_ns=completed,
            captured_ts_ns=captured,
            collected_by="operator",
            reviewed_by="reviewer",
            result=CleanupRemovedHostResult(
                kind=CleanupTargetKind.HOST_PACKAGE,
                locator=targets[1].locator,
                raw_artifact_ids=("host-absence-1", "host-absence-2"),
            ),
        ),
        CleanupTargetOutcomeEvidence(
            retirement_id=cleanup_manifest.retirement_id,
            target_id=targets[2].target_id,
            kind=targets[2].kind,
            locator=targets[2].locator,
            action=targets[2].action,
            destination_locator=targets[2].destination_locator,
            pre_action_state_sha256=targets[2].expected_state_sha256,
            action_started_ts_ns=started,
            action_completed_ts_ns=completed,
            captured_ts_ns=captured,
            collected_by="operator",
            reviewed_by="reviewer",
            result=CleanupNativeMigrationResult(
                locator=targets[2].locator,
                destination_locator=destination_inventory.locator,
                migration_commit_sha="d" * 40,
                destination_inventory_sha256=destination_inventory.state_sha256(),
                raw_artifact_ids=(
                    "migration-source-absence",
                    "migration-destination-inventory",
                ),
            ),
        ),
        CleanupTargetOutcomeEvidence(
            retirement_id=cleanup_manifest.retirement_id,
            target_id=targets[3].target_id,
            kind=targets[3].kind,
            locator=targets[3].locator,
            action=targets[3].action,
            pre_action_state_sha256=targets[3].expected_state_sha256,
            action_started_ts_ns=started,
            action_completed_ts_ns=completed,
            captured_ts_ns=captured,
            collected_by="operator",
            reviewed_by="reviewer",
            result=CleanupRevokedSecretResult(
                locator=secret_state.locator,
                provider=secret_state.provider,
                provider_record_id_sha256=secret_state.provider_record_id_sha256,
                provider_state_sha256=artifact_by_id["secret-provider"].content_sha256,
                active_sessions_sha256=artifact_by_id["secret-sessions"].content_sha256,
                raw_artifact_ids=("secret-provider", "secret-sessions"),
            ),
        ),
    )
    policy = load_retirement_policy(POLICY_PATH)
    scan_policy = load_legacy_archive_credential_scan_policy(SCAN_POLICY_PATH)
    outcome_controls = (
        *(
            CleanupOutcomeControl(
                kind=CleanupOutcomeControlKind.TARGET_OUTCOME,
                reference_id=item.target_id,
                relative_path=f"controls/targets/{item.target_id}.json",
                content_sha256=item.sha256(),
                byte_count=1,
                captured_ts_ns=captured,
            )
            for item in outcomes
        ),
        CleanupOutcomeControl(
            kind=CleanupOutcomeControlKind.CREDENTIAL_SCAN,
            reference_id="credential-scan",
            relative_path="controls/credential-scan.json",
            content_sha256="8" * 64,
            byte_count=1,
            captured_ts_ns=captured,
        ),
    )
    outcome_manifest = CleanupOutcomeEvidenceManifest(
        retirement_id=cleanup_manifest.retirement_id,
        created_ts_ns=captured + 1,
        policy_id=policy.policy_id,
        policy_sha256=policy.sha256(),
        credential_scan_policy_id=scan_policy.policy_id,
        credential_scan_policy_sha256=scan_policy.sha256(),
        source_commit_sha=cleanup_manifest.source_commit_sha,
        archive_manifest_sha256=archive.sha256(),
        disabled_observation_report_sha256="9" * 64,
        cleanup_manifest_sha256=cleanup_manifest.sha256(),
        cleanup_preflight_receipt_sha256=preflight.sha256(),
        artifacts=tuple(artifact_models),
        controls=outcome_controls,
    )
    approved_secret = CleanupTargetEvidence(
        retirement_id=cleanup_manifest.retirement_id,
        target_id=targets[3].target_id,
        action=CleanupAction.REVOKE,
        rationale=targets[3].rationale,
        collected_by="operator",
        reviewed_by="reviewer",
        state=secret_state,
    )

    outcome_module._verify_target_outcomes(
        tmp_path / "variant-outcome",
        outcome_manifest,
        outcomes,
        cleanup_manifest,
        preflight,
        (approved_secret,),
        archive,
    )
