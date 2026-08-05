from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import test_retirement_readiness as readiness_testkit
from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa
from pydantic import ValidationError

import aiquanttrader.retirement.archive as archive_module
import aiquanttrader.retirement.collector as collector_module
import aiquanttrader.retirement.disabled as disabled_module
from aiquanttrader.retirement.approval import RetirementApprovalPaths
from aiquanttrader.retirement.cli import main as retirement_main
from aiquanttrader.retirement.disabled import (
    assemble_disabled_observation,
    load_disabled_observation,
    load_retirement_readiness_report,
    verify_disabled_observation,
)
from aiquanttrader.retirement.evidence import (
    evaluate_disabled_observation,
    evaluate_retirement_readiness,
)
from aiquanttrader.retirement.models import (
    DisabledCredentialScanCheck,
    DisabledCredentialScanEvidence,
    DisabledEvidenceArtifact,
    DisabledEvidenceArtifactKind,
    DisabledEvidenceControl,
    DisabledEvidenceControlKind,
    DisabledEvidenceManifest,
    DisabledObservation,
    LegacyBrokerOrderAuditEvidence,
    LegacyCapability,
    LegacyCapabilityAuditEvidence,
    LegacyCapabilitySample,
    LegacyCapabilityState,
    LegacyCredentialQuarantineCheck,
    LegacyCredentialQuarantineEvidence,
    LegacyPostStopBrokerOrder,
    LegacyStopActionEvidence,
    LegacyStopExecutionEvidence,
    NativeDisabledWindowEvidence,
    RequiredLegacyStopAction,
    RetirementActionApproval,
    RetirementActionScope,
    RetirementApprovalSignature,
    RetirementPolicy,
    RetirementReadinessReport,
)


@dataclass(frozen=True, slots=True)
class DisabledBundle:
    disabled_root: Path
    readiness: readiness_testkit.ReadinessEvidence
    readiness_report: RetirementReadinessReport
    readiness_observation_path: Path
    readiness_report_path: Path
    native_observation_path: Path
    archive_manifest_path: Path
    stop_approval_paths: RetirementApprovalPaths
    stop_key_id: str
    stop_public_key_sha256: str
    policy_path: Path
    scan_policy_path: Path
    policy: RetirementPolicy
    observation: DisabledObservation
    assembled_ts_ns: int


def _ts(value: datetime) -> int:
    return int(value.timestamp()) * 1_000_000_000 + value.microsecond * 1_000


def _dt(value: int) -> datetime:
    seconds, nanoseconds = divmod(value, 1_000_000_000)
    return datetime.fromtimestamp(seconds, UTC) + timedelta(microseconds=nanoseconds // 1_000)


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _artifact(
    root: Path,
    *,
    artifact_id: str,
    kind: DisabledEvidenceArtifactKind,
    relative_path: str,
    started_ts_ns: int,
    ended_ts_ns: int,
) -> DisabledEvidenceArtifact:
    payload = (root / relative_path).read_bytes()
    return DisabledEvidenceArtifact(
        artifact_id=artifact_id,
        kind=kind,
        relative_path=relative_path,
        content_sha256=hashlib.sha256(payload).hexdigest(),
        byte_count=len(payload),
        captured_start_ts_ns=started_ts_ns,
        captured_end_ts_ns=ended_ts_ns,
    )


def _control(
    root: Path,
    *,
    kind: DisabledEvidenceControlKind,
    relative_path: str,
    captured_ts_ns: int,
) -> DisabledEvidenceControl:
    payload = (root / relative_path).read_bytes()
    return DisabledEvidenceControl(
        kind=kind,
        relative_path=relative_path,
        content_sha256=hashlib.sha256(payload).hexdigest(),
        byte_count=len(payload),
        captured_ts_ns=captured_ts_ns,
    )


def _build_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    active_capability: bool = False,
    omit_middle_sample: bool = False,
    broker_complete: bool = True,
    post_stop_order: bool = False,
    credentials_safe: bool = True,
    native_complete: bool = True,
    native_critical_incidents: int = 0,
) -> DisabledBundle:
    policy = readiness_testkit._policy().model_copy(
        update={
            "minimum_disabled_observation_ns": _ts(datetime(1970, 1, 2, tzinfo=UTC)),
            "maximum_disabled_evidence_gap_ns": _ts(datetime(1970, 1, 1, 12, tzinfo=UTC)),
        }
    )
    monkeypatch.setattr(readiness_testkit, "_policy", lambda: policy)
    readiness = readiness_testkit._evidence(tmp_path / "readiness", monkeypatch)
    readiness_report = evaluate_retirement_readiness(
        observation=readiness.observation,
        policy=policy,
        generated_ts_ns=readiness.observation.observed_ts_ns + 1_000_000_000,
    )
    assert readiness_report.awaiting_stop_approval

    approval_key = ECC.generate(curve="Ed25519")
    stop_key_id = "retirement-stop-approver-001"
    approved_at = _dt(readiness_report.generated_ts_ns) + timedelta(seconds=1)
    stop_started = _ts(approved_at + timedelta(seconds=1))
    action_times = tuple(stop_started + index * 1_000_000_000 for index in range(1, 14))
    stop_ended = action_times[-1]
    approval = RetirementActionApproval(
        approval_id="retirement-stop-approval-001",
        retirement_id=readiness_report.retirement_id,
        scope=RetirementActionScope.STOP_AND_OBSERVE,
        report_sha256=readiness_report.sha256(),
        native_deployment_id=readiness_report.native_deployment_id,
        native_admission_id=readiness_report.native_admission_id,
        archive_manifest_sha256=readiness_report.archive_manifest_sha256,
        source_commit_sha=readiness_report.source_commit_sha,
        approver="independent-retirement-risk-owner",
        approved_at=approved_at,
        expires_at=approved_at + timedelta(hours=1),
    )
    signature_bytes = eddsa.new(approval_key, "rfc8032").sign(approval.canonical_bytes())
    signature = RetirementApprovalSignature(
        key_id=stop_key_id,
        approval_sha256=approval.sha256(),
        signature_base64=base64.b64encode(signature_bytes).decode("ascii"),
    )
    approval_path = (tmp_path / "trust/stop-approval.json").resolve()
    signature_path = (tmp_path / "trust/stop-approval.sig.json").resolve()
    public_key_path = (tmp_path / "trust/stop-approver.pub").resolve()
    public_key_bytes = approval_key.public_key().export_key(format="PEM").encode("ascii")
    _write(approval_path, approval.canonical_bytes())
    _write(signature_path, signature.canonical_bytes())
    _write(public_key_path, public_key_bytes)
    public_key_sha256 = hashlib.sha256(
        approval_key.public_key().export_key(format="DER", compress=False)
    ).hexdigest()

    disabled_root = (tmp_path / "disabled-evidence").resolve()
    disabled_root.mkdir(parents=True)
    stop_raw = "raw/stop/execution.txt"
    _write(disabled_root / stop_raw, b"retained reviewed stop command outputs\n")
    stop = LegacyStopExecutionEvidence(
        retirement_id=readiness_report.retirement_id,
        readiness_report_sha256=readiness_report.sha256(),
        stop_approval_sha256=approval.sha256(),
        started_ts_ns=stop_started,
        ended_ts_ns=stop_ended,
        operator="legacy-retirement-operator",
        reviewer="independent-stop-reviewer",
        actions=tuple(
            LegacyStopActionEvidence(
                action=action,
                completed_ts_ns=action_times[index],
                evidence_path=stop_raw,
            )
            for index, action in enumerate(RequiredLegacyStopAction)
        ),
    )
    _write(disabled_root / "controls/stop-execution.json", stop.canonical_bytes() + b"\n")

    observation_started = stop_ended
    observation_ended = observation_started + int(timedelta(days=1).total_seconds() * 1e9)
    sample_times = [
        observation_started,
        observation_started + int(timedelta(hours=12).total_seconds() * 1e9),
        observation_ended,
    ]
    if omit_middle_sample:
        sample_times.pop(1)
    samples: list[LegacyCapabilitySample] = []
    capability_artifacts: list[DisabledEvidenceArtifact] = []
    for sample_index, sampled_at in enumerate(sample_times):
        relative_path = f"raw/capabilities/sample-{sample_index}.json"
        _write(disabled_root / relative_path, f"capability sample {sample_index}\n".encode())
        states = tuple(
            LegacyCapabilityState(
                capability=capability,
                disabled=not (
                    active_capability
                    and sample_index == 1
                    and capability is LegacyCapability.PM2_MT5
                ),
                active_instance_count=int(
                    active_capability
                    and sample_index == 1
                    and capability is LegacyCapability.PM2_MT5
                ),
            )
            for capability in LegacyCapability
        )
        samples.append(
            LegacyCapabilitySample(
                observed_ts_ns=sampled_at,
                evidence_path=relative_path,
                states=states,
            )
        )
        capability_artifacts.append(
            _artifact(
                disabled_root,
                artifact_id=f"capability-sample-{sample_index}",
                kind=DisabledEvidenceArtifactKind.CAPABILITY_SNAPSHOT,
                relative_path=relative_path,
                started_ts_ns=sampled_at,
                ended_ts_ns=sampled_at,
            )
        )
    capability_reviewed = observation_ended + int(timedelta(minutes=1).total_seconds() * 1e9)
    capability_audit = LegacyCapabilityAuditEvidence(
        retirement_id=readiness_report.retirement_id,
        started_ts_ns=observation_started,
        ended_ts_ns=observation_ended,
        reviewed_ts_ns=capability_reviewed,
        collected_by="capability-evidence-collector",
        reviewed_by="independent-capability-reviewer",
        samples=tuple(samples),
    )
    _write(
        disabled_root / "controls/capability-audit.json",
        capability_audit.canonical_bytes() + b"\n",
    )

    mt5_stopped = action_times[
        list(RequiredLegacyStopAction).index(RequiredLegacyStopAction.STOP_MT5)
    ]
    broker_raw = "raw/broker/order-history.json"
    _write(disabled_root / broker_raw, b"retained complete post-stop broker order export\n")
    broker_captured = observation_ended + int(timedelta(minutes=2).total_seconds() * 1e9)
    broker_reviewed = broker_captured + int(timedelta(minutes=1).total_seconds() * 1e9)
    broker_audit = LegacyBrokerOrderAuditEvidence(
        retirement_id=readiness_report.retirement_id,
        queried_start_ts_ns=mt5_stopped,
        queried_end_ts_ns=observation_ended,
        captured_ts_ns=broker_captured,
        reviewed_ts_ns=broker_reviewed,
        captured_by="broker-history-exporter",
        reviewed_by="independent-broker-reviewer",
        account_login_sha256=readiness.observation.legacy.account_login_sha256,
        broker_server_sha256=readiness.observation.legacy.broker_server_sha256,
        coverage_complete=broker_complete,
        source_evidence_path=broker_raw,
        orders=(
            (
                LegacyPostStopBrokerOrder(
                    order_id_sha256="9" * 64,
                    instrument_id="XAUUSD",
                    created_ts_ns=observation_started + 1,
                ),
            )
            if post_stop_order
            else ()
        ),
    )
    _write(
        disabled_root / "controls/broker-order-audit.json",
        broker_audit.canonical_bytes() + b"\n",
    )

    credential_raw = "raw/credentials/quarantine-audit.txt"
    _write(disabled_root / credential_raw, b"retained credential access audit without secrets\n")
    quarantine_started = action_times[
        list(RequiredLegacyStopAction).index(RequiredLegacyStopAction.QUARANTINE_CREDENTIALS)
    ]
    credential_reviewed = observation_ended + int(timedelta(minutes=4).total_seconds() * 1e9)
    credential_evidence = LegacyCredentialQuarantineEvidence(
        retirement_id=readiness_report.retirement_id,
        started_ts_ns=quarantine_started,
        observed_through_ts_ns=observation_ended,
        reviewed_ts_ns=credential_reviewed,
        collected_by="credential-access-auditor",
        reviewed_by="independent-credential-reviewer",
        inventory_complete=credentials_safe,
        continuous_audit=credentials_safe,
        checks=tuple(
            LegacyCredentialQuarantineCheck(
                credential_id=credential_id,
                quarantined=credentials_safe,
                active_reader_count=0 if credentials_safe else 1,
                evidence_path=credential_raw,
            )
            for credential_id in ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER")
        ),
    )
    _write(
        disabled_root / "controls/credential-quarantine.json",
        credential_evidence.canonical_bytes() + b"\n",
    )

    native_raw = "raw/native/operational-audit.jsonl"
    _write(disabled_root / native_raw, b"retained native operational audit window\n")
    native_reviewed = observation_ended + int(timedelta(minutes=5).total_seconds() * 1e9)
    native_stability = NativeDisabledWindowEvidence(
        retirement_id=readiness_report.retirement_id,
        native_deployment_id=readiness_report.native_deployment_id,
        native_admission_id=readiness_report.native_admission_id,
        started_ts_ns=observation_started,
        ended_ts_ns=observation_ended,
        reviewed_ts_ns=native_reviewed,
        collected_by="native-operational-collector",
        reviewed_by="independent-native-reviewer",
        continuous_monitoring=native_complete,
        critical_incidents=native_critical_incidents,
        reconciliation_failures=0,
        risk_breaches=0,
        evidence_paths=(native_raw,),
    )
    _write(
        disabled_root / "controls/native-stability-audit.json",
        native_stability.canonical_bytes() + b"\n",
    )

    artifacts = (
        _artifact(
            disabled_root,
            artifact_id="stop-execution-output",
            kind=DisabledEvidenceArtifactKind.STOP_EXECUTION_OUTPUT,
            relative_path=stop_raw,
            started_ts_ns=stop_started,
            ended_ts_ns=stop_ended,
        ),
        *capability_artifacts,
        _artifact(
            disabled_root,
            artifact_id="broker-order-export",
            kind=DisabledEvidenceArtifactKind.BROKER_ORDER_EXPORT,
            relative_path=broker_raw,
            started_ts_ns=mt5_stopped,
            ended_ts_ns=observation_ended,
        ),
        _artifact(
            disabled_root,
            artifact_id="credential-quarantine-audit",
            kind=DisabledEvidenceArtifactKind.CREDENTIAL_QUARANTINE_AUDIT,
            relative_path=credential_raw,
            started_ts_ns=quarantine_started,
            ended_ts_ns=observation_ended,
        ),
        _artifact(
            disabled_root,
            artifact_id="native-operational-audit",
            kind=DisabledEvidenceArtifactKind.NATIVE_OPERATIONAL_AUDIT,
            relative_path=native_raw,
            started_ts_ns=observation_started,
            ended_ts_ns=observation_ended,
        ),
    )
    scan_started = observation_ended + int(timedelta(minutes=5).total_seconds() * 1e9)
    scan_ended = scan_started + int(timedelta(minutes=1).total_seconds() * 1e9)
    scan_reviewed = scan_ended + int(timedelta(minutes=1).total_seconds() * 1e9)
    credential_scan = DisabledCredentialScanEvidence(
        retirement_id=readiness_report.retirement_id,
        started_ts_ns=scan_started,
        ended_ts_ns=scan_ended,
        reviewed_ts_ns=scan_reviewed,
        reviewer="independent-disabled-credential-scan-reviewer",
        scanner_name="reviewed-recursive-credential-scanner",
        scanner_version="1.0.0",
        policy_id=readiness.scan_policy.policy_id,
        policy_sha256=readiness.scan_policy.sha256(),
        checks=tuple(
            DisabledCredentialScanCheck(
                artifact_id=artifact.artifact_id,
                artifact_sha256=artifact.content_sha256,
            )
            for artifact in artifacts
        ),
    )
    _write(
        disabled_root / "controls/credential-scan.json",
        credential_scan.canonical_bytes() + b"\n",
    )
    controls = (
        _control(
            disabled_root,
            kind=DisabledEvidenceControlKind.STOP_EXECUTION,
            relative_path="controls/stop-execution.json",
            captured_ts_ns=stop_ended,
        ),
        _control(
            disabled_root,
            kind=DisabledEvidenceControlKind.CAPABILITY_AUDIT,
            relative_path="controls/capability-audit.json",
            captured_ts_ns=capability_reviewed,
        ),
        _control(
            disabled_root,
            kind=DisabledEvidenceControlKind.BROKER_ORDER_AUDIT,
            relative_path="controls/broker-order-audit.json",
            captured_ts_ns=broker_reviewed,
        ),
        _control(
            disabled_root,
            kind=DisabledEvidenceControlKind.CREDENTIAL_QUARANTINE,
            relative_path="controls/credential-quarantine.json",
            captured_ts_ns=credential_reviewed,
        ),
        _control(
            disabled_root,
            kind=DisabledEvidenceControlKind.NATIVE_STABILITY_AUDIT,
            relative_path="controls/native-stability-audit.json",
            captured_ts_ns=native_reviewed,
        ),
        _control(
            disabled_root,
            kind=DisabledEvidenceControlKind.CREDENTIAL_SCAN,
            relative_path="controls/credential-scan.json",
            captured_ts_ns=scan_reviewed,
        ),
    )
    created = observation_ended + int(timedelta(minutes=8).total_seconds() * 1e9)
    manifest = DisabledEvidenceManifest(
        retirement_id=readiness_report.retirement_id,
        created_ts_ns=created,
        started_ts_ns=observation_started,
        ended_ts_ns=observation_ended,
        readiness_report_sha256=readiness_report.sha256(),
        stop_approval_sha256=approval.sha256(),
        archive_manifest_sha256=readiness.archive.sha256(),
        native_deployment_id=readiness_report.native_deployment_id,
        native_admission_id=readiness_report.native_admission_id,
        artifacts=artifacts,
        controls=controls,
    )
    _write(disabled_root / disabled_module.MANIFEST_NAME, manifest.canonical_bytes() + b"\n")

    readiness_observation_path = (tmp_path / "inputs/readiness-observation.json").resolve()
    readiness_report_path = (tmp_path / "inputs/readiness-report.json").resolve()
    native_observation_path = (tmp_path / "inputs/native-observation.json").resolve()
    archive_manifest_path = (tmp_path / "inputs/archive-manifest.json").resolve()
    _write(readiness_observation_path, readiness.observation.canonical_bytes() + b"\n")
    _write(readiness_report_path, readiness_report.canonical_bytes() + b"\n")
    _write(native_observation_path, readiness.native.canonical_bytes() + b"\n")
    _write(archive_manifest_path, readiness.archive.canonical_bytes() + b"\n")
    policy_path, scan_policy_path = readiness_testkit._write_policy_files(tmp_path, policy)

    assembled_ts_ns = created + int(timedelta(minutes=1).total_seconds() * 1e9)
    monkeypatch.setattr(disabled_module, "time_ns", lambda: assembled_ts_ns)
    monkeypatch.setattr(archive_module, "time_ns", lambda: assembled_ts_ns)
    monkeypatch.setattr(collector_module, "time_ns", lambda: assembled_ts_ns)
    stop_paths = RetirementApprovalPaths(
        approval_path=approval_path,
        signature_path=signature_path,
        public_key_path=public_key_path,
    )
    observation = assemble_disabled_observation(
        disabled_root,
        readiness.native_root,
        readiness.legacy_root,
        readiness.observation,
        readiness_report,
        readiness.native,
        readiness.archive,
        stop_approval_paths=stop_paths,
        policy=policy,
        credential_scan_policy=readiness.scan_policy,
        expected_native_key_id=readiness.key_id,
        expected_native_public_key_sha256=readiness.public_key_sha256,
        expected_stop_key_id=stop_key_id,
        expected_stop_public_key_sha256=public_key_sha256,
    )
    return DisabledBundle(
        disabled_root=disabled_root,
        readiness=readiness,
        readiness_report=readiness_report,
        readiness_observation_path=readiness_observation_path,
        readiness_report_path=readiness_report_path,
        native_observation_path=native_observation_path,
        archive_manifest_path=archive_manifest_path,
        stop_approval_paths=stop_paths,
        stop_key_id=stop_key_id,
        stop_public_key_sha256=public_key_sha256,
        policy_path=policy_path,
        scan_policy_path=scan_policy_path,
        policy=policy,
        observation=observation,
        assembled_ts_ns=assembled_ts_ns,
    )


def _verify(bundle: DisabledBundle) -> DisabledObservation:
    return verify_disabled_observation(
        bundle.disabled_root,
        bundle.readiness.native_root,
        bundle.readiness.legacy_root,
        bundle.observation,
        bundle.readiness.observation,
        bundle.readiness_report,
        bundle.readiness.native,
        bundle.readiness.archive,
        stop_approval_paths=bundle.stop_approval_paths,
        policy=bundle.policy,
        credential_scan_policy=bundle.readiness.scan_policy,
        expected_native_key_id=bundle.readiness.key_id,
        expected_native_public_key_sha256=bundle.readiness.public_key_sha256,
        expected_stop_key_id=bundle.stop_key_id,
        expected_stop_public_key_sha256=bundle.stop_public_key_sha256,
    )


def _cli_args(bundle: DisabledBundle) -> list[str]:
    return [
        "--disabled-evidence-root",
        str(bundle.disabled_root),
        "--native-evidence-root",
        str(bundle.readiness.native_root),
        "--legacy-evidence-root",
        str(bundle.readiness.legacy_root),
        "--readiness-observation",
        str(bundle.readiness_observation_path),
        "--readiness-report",
        str(bundle.readiness_report_path),
        "--native-observation",
        str(bundle.native_observation_path),
        "--archive-manifest",
        str(bundle.archive_manifest_path),
        "--stop-approval",
        str(bundle.stop_approval_paths.approval_path),
        "--stop-signature",
        str(bundle.stop_approval_paths.signature_path),
        "--stop-public-key",
        str(bundle.stop_approval_paths.public_key_path),
        "--policy",
        str(bundle.policy_path),
        "--credential-scan-policy",
        str(bundle.scan_policy_path),
        "--native-approval-key-id",
        bundle.readiness.key_id,
        "--native-approval-public-key-sha256",
        bundle.readiness.public_key_sha256,
        "--stop-approval-key-id",
        bundle.stop_key_id,
        "--stop-approval-public-key-sha256",
        bundle.stop_public_key_sha256,
    ]


def test_disabled_evidence_assembles_replays_and_evaluates_only_to_cleanup_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _build_bundle(tmp_path, monkeypatch)

    assert _verify(bundle) == bundle.observation
    report = evaluate_disabled_observation(
        observation=bundle.observation,
        policy=bundle.policy,
        generated_ts_ns=bundle.assembled_ts_ns,
    )
    assert report.awaiting_cleanup_approval
    assert all(gate.passed for gate in report.gates)
    assert bundle.observation.capability_sample_count == 3
    assert bundle.observation.maximum_capability_gap_ns == int(
        timedelta(hours=12).total_seconds() * 1e9
    )

    observation_path = (tmp_path / "outputs/disabled-observation.json").resolve()
    report_path = (tmp_path / "outputs/disabled-report.json").resolve()
    assert (
        retirement_main(
            ["assemble-disabled", *_cli_args(bundle), "--output", str(observation_path)]
        )
        == 0
    )
    assert load_disabled_observation(observation_path) == bundle.observation
    assert load_retirement_readiness_report(bundle.readiness_report_path) == bundle.readiness_report
    assert (
        retirement_main(
            ["verify-disabled", *_cli_args(bundle), "--observation", str(observation_path)]
        )
        == 0
    )
    assert (
        retirement_main(
            [
                "evaluate-disabled",
                *_cli_args(bundle),
                "--observation",
                str(observation_path),
                "--output",
                str(report_path),
            ]
        )
        == 0
    )
    assert report_path.is_file()


@pytest.mark.parametrize(
    ("updates", "failed_gate"),
    (
        ({"active_capability": True}, "all_capabilities_disabled"),
        ({"omit_middle_sample": True}, "evidence_continuity"),
        ({"broker_complete": False}, "no_legacy_orders"),
        ({"post_stop_order": True}, "no_legacy_orders"),
        ({"credentials_safe": False}, "credentials_quarantined"),
        ({"native_complete": False}, "native_stable"),
        ({"native_critical_incidents": 1}, "native_stable"),
    ),
)
def test_disabled_evidence_preserves_failed_gate_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    updates: dict[str, object],
    failed_gate: str,
) -> None:
    bundle = _build_bundle(tmp_path, monkeypatch, **updates)  # type: ignore[arg-type]
    report = evaluate_disabled_observation(
        observation=bundle.observation,
        policy=bundle.policy,
        generated_ts_ns=bundle.assembled_ts_ns,
    )

    assert not report.awaiting_cleanup_approval
    assert failed_gate in {gate.gate.value for gate in report.gates if not gate.passed}


def test_disabled_replay_rejects_raw_tampering_and_unexpected_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _build_bundle(tmp_path, monkeypatch)
    raw = bundle.disabled_root / "raw/capabilities/sample-1.json"
    raw.write_bytes(b"tampered capability evidence\n")
    with pytest.raises(ValueError, match="digest or size differs"):
        _verify(bundle)

    original = b"capability sample 1\n"
    raw.write_bytes(original)
    unexpected = bundle.disabled_root / "raw/unexpected.txt"
    unexpected.write_bytes(b"unbound\n")
    with pytest.raises(ValueError, match="inventory is not exact"):
        _verify(bundle)


def test_disabled_contracts_reject_reordered_stop_and_unsafe_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _build_bundle(tmp_path, monkeypatch)
    stop = LegacyStopExecutionEvidence.model_validate_json(
        (bundle.disabled_root / "controls/stop-execution.json").read_bytes()
    )
    with pytest.raises(ValidationError, match="exact and ordered"):
        LegacyStopExecutionEvidence.model_validate(
            {**stop.model_dump(), "actions": tuple(reversed(stop.actions))}
        )
    with pytest.raises(ValidationError, match="below raw"):
        DisabledEvidenceArtifact(
            artifact_id="unsafe",
            kind=DisabledEvidenceArtifactKind.CAPABILITY_SNAPSHOT,
            relative_path="../escape.txt",
            content_sha256="1" * 64,
            byte_count=1,
            captured_start_ts_ns=1,
            captured_end_ts_ns=1,
        )
    with pytest.raises(ValidationError, match="independent reviewer"):
        LegacyCredentialQuarantineEvidence.model_validate(
            {
                "retirement_id": "retirement-test",
                "started_ts_ns": 1,
                "observed_through_ts_ns": 2,
                "reviewed_ts_ns": 2,
                "collected_by": "same-person",
                "reviewed_by": "same-person",
                "inventory_complete": True,
                "continuous_audit": True,
                "checks": [
                    {
                        "credential_id": "MT5_PASSWORD",
                        "quarantined": True,
                        "active_reader_count": 0,
                        "evidence_path": "raw/credentials.txt",
                    }
                ],
            }
        )


def test_disabled_output_must_be_new_absolute_and_outside_all_evidence_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = _build_bundle(tmp_path, monkeypatch)
    inside = bundle.disabled_root / "output.json"
    assert retirement_main(["assemble-disabled", *_cli_args(bundle), "--output", str(inside)]) == 2
    assert "outside the immutable evidence root" in capsys.readouterr().err

    output = (tmp_path / "disabled-output.json").resolve()
    output.write_text("existing", encoding="utf-8")
    assert retirement_main(["assemble-disabled", *_cli_args(bundle), "--output", str(output)]) == 2
    assert "already exists" in capsys.readouterr().err


def _loaded_bundle_controls(
    bundle: DisabledBundle,
) -> tuple[
    DisabledEvidenceManifest,
    LegacyStopExecutionEvidence,
    LegacyCapabilityAuditEvidence,
    LegacyBrokerOrderAuditEvidence,
    LegacyCredentialQuarantineEvidence,
    NativeDisabledWindowEvidence,
    DisabledCredentialScanEvidence,
]:
    root = bundle.disabled_root
    return (
        DisabledEvidenceManifest.model_validate_json(
            (root / disabled_module.MANIFEST_NAME).read_bytes()
        ),
        LegacyStopExecutionEvidence.model_validate_json(
            (root / "controls/stop-execution.json").read_bytes()
        ),
        LegacyCapabilityAuditEvidence.model_validate_json(
            (root / "controls/capability-audit.json").read_bytes()
        ),
        LegacyBrokerOrderAuditEvidence.model_validate_json(
            (root / "controls/broker-order-audit.json").read_bytes()
        ),
        LegacyCredentialQuarantineEvidence.model_validate_json(
            (root / "controls/credential-quarantine.json").read_bytes()
        ),
        NativeDisabledWindowEvidence.model_validate_json(
            (root / "controls/native-stability-audit.json").read_bytes()
        ),
        DisabledCredentialScanEvidence.model_validate_json(
            (root / "controls/credential-scan.json").read_bytes()
        ),
    )


def test_disabled_loaders_roots_and_replay_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _build_bundle(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="dated after verification"):
        monkeypatch.setattr(disabled_module, "time_ns", lambda: bundle.assembled_ts_ns - 1)
        _verify(bundle)
    monkeypatch.setattr(disabled_module, "time_ns", lambda: bundle.assembled_ts_ns)
    changed = bundle.observation.model_copy(update={"evidence_bundle_sha256": "f" * 64})
    with pytest.raises(ValueError, match="does not match its evidence roots"):
        verify_disabled_observation(
            bundle.disabled_root,
            bundle.readiness.native_root,
            bundle.readiness.legacy_root,
            changed,
            bundle.readiness.observation,
            bundle.readiness_report,
            bundle.readiness.native,
            bundle.readiness.archive,
            stop_approval_paths=bundle.stop_approval_paths,
            policy=bundle.policy,
            credential_scan_policy=bundle.readiness.scan_policy,
            expected_native_key_id=bundle.readiness.key_id,
            expected_native_public_key_sha256=bundle.readiness.public_key_sha256,
            expected_stop_key_id=bundle.stop_key_id,
            expected_stop_public_key_sha256=bundle.stop_public_key_sha256,
        )

    noncanonical = (tmp_path / "noncanonical-observation.json").resolve()
    noncanonical.write_text(bundle.observation.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="not canonical"):
        load_disabled_observation(noncanonical)
    noncanonical_report = (tmp_path / "noncanonical-report.json").resolve()
    noncanonical_report.write_text(bundle.readiness_report.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="not canonical"):
        load_retirement_readiness_report(noncanonical_report)

    with pytest.raises(ValueError, match="must be absolute"):
        disabled_module._validated_root(Path("relative"))
    alias = tmp_path / "disabled-alias"
    alias.symlink_to(bundle.disabled_root, target_is_directory=True)
    with pytest.raises(ValueError, match="non-symlink"):
        disabled_module._validated_root(alias)
    regular_file = (tmp_path / "regular.txt").resolve()
    regular_file.write_text("regular", encoding="utf-8")
    with pytest.raises(ValueError, match="non-symlink directory"):
        disabled_module._validated_root(regular_file)
    with pytest.raises(ValueError, match="cannot open"):
        disabled_module._read_regular(tmp_path / "missing", maximum_bytes=10)
    empty = (tmp_path / "empty").resolve()
    empty.touch()
    with pytest.raises(ValueError, match="size is invalid"):
        disabled_module._read_regular(empty, maximum_bytes=10)
    with pytest.raises(ValueError, match="regular file"):
        disabled_module._read_regular(tmp_path, maximum_bytes=10)
    with pytest.raises(ValueError, match="cannot open"):
        disabled_module._hash_regular(tmp_path / "missing-hash")
    with pytest.raises(ValueError, match="regular file"):
        disabled_module._hash_regular(tmp_path)


def test_disabled_cross_bundle_and_raw_reference_guards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = _build_bundle(tmp_path, monkeypatch)
    (
        manifest,
        stop,
        capabilities,
        broker,
        credentials,
        native_stability,
        credential_scan,
    ) = _loaded_bundle_controls(bundle)
    controls = {item.kind: item for item in manifest.controls}
    artifacts = {item.relative_path: item for item in manifest.artifacts}

    with pytest.raises(ValueError, match="retirement identities differ"):
        disabled_module._verify_manifest_lineage(
            manifest,
            stop.model_copy(update={"retirement_id": "different"}),
            capabilities,
            broker,
            credentials,
            native_stability,
            credential_scan,
            controls,
        )
    with pytest.raises(ValueError, match="audit identity differs"):
        disabled_module._verify_manifest_lineage(
            manifest,
            stop,
            capabilities,
            broker,
            credentials,
            native_stability.model_copy(update={"native_deployment_id": "different"}),
            credential_scan,
            controls,
        )
    with pytest.raises(ValueError, match="declared interval"):
        disabled_module._verify_manifest_lineage(
            manifest,
            stop.model_copy(update={"ended_ts_ns": stop.ended_ts_ns - 1}),
            capabilities,
            broker,
            credentials,
            native_stability,
            credential_scan,
            controls,
        )
    bad_controls = {
        **controls,
        DisabledEvidenceControlKind.CAPABILITY_AUDIT: controls[
            DisabledEvidenceControlKind.CAPABILITY_AUDIT
        ].model_copy(update={"captured_ts_ns": 0}),
    }
    with pytest.raises(ValueError, match="predates its review"):
        disabled_module._verify_manifest_lineage(
            manifest,
            stop,
            capabilities,
            broker,
            credentials,
            native_stability,
            credential_scan,
            bad_controls,
        )

    with pytest.raises(ValueError, match="different frozen policy"):
        disabled_module._verify_credential_scan(
            manifest,
            artifacts,
            credential_scan,
            bundle.readiness.scan_policy.model_copy(update={"policy_id": "different"}),
        )
    with pytest.raises(ValueError, match="before raw evidence collection ended"):
        disabled_module._verify_credential_scan(
            manifest,
            artifacts,
            credential_scan.model_copy(update={"started_ts_ns": 0}),
            bundle.readiness.scan_policy,
        )
    with pytest.raises(ValueError, match="review postdates"):
        disabled_module._verify_credential_scan(
            manifest,
            artifacts,
            credential_scan.model_copy(update={"reviewed_ts_ns": manifest.created_ts_ns + 1}),
            bundle.readiness.scan_policy,
        )
    with pytest.raises(ValueError, match="inventory is not exact"):
        disabled_module._verify_credential_scan(
            manifest,
            artifacts,
            credential_scan.model_copy(update={"checks": credential_scan.checks[:-1]}),
            bundle.readiness.scan_policy,
        )
    changed_check = credential_scan.checks[0].model_copy(update={"artifact_sha256": "f" * 64})
    with pytest.raises(ValueError, match="scan hash differs"):
        disabled_module._verify_credential_scan(
            manifest,
            artifacts,
            credential_scan.model_copy(
                update={"checks": (changed_check, *credential_scan.checks[1:])}
            ),
            bundle.readiness.scan_policy,
        )

    with pytest.raises(ValueError, match="passing retirement readiness"):
        disabled_module._verify_external_lineage(
            manifest,
            stop,
            bundle.readiness.observation,
            bundle.readiness_report.model_copy(update={"awaiting_stop_approval": False}),
            bundle.readiness.archive,
            bundle.policy,
        )
    with pytest.raises(ValueError, match="different frozen policy"):
        disabled_module._verify_external_lineage(
            manifest,
            stop,
            bundle.readiness.observation,
            bundle.readiness_report,
            bundle.readiness.archive,
            bundle.policy.model_copy(update={"policy_id": "different"}),
        )
    with pytest.raises(ValueError, match="observation differs"):
        disabled_module._verify_external_lineage(
            manifest,
            stop,
            bundle.readiness.observation.model_copy(
                update={"observed_ts_ns": bundle.readiness.observation.observed_ts_ns + 1}
            ),
            bundle.readiness_report,
            bundle.readiness.archive,
            bundle.policy,
        )
    with pytest.raises(ValueError, match="retained archive"):
        disabled_module._verify_external_lineage(
            manifest,
            stop,
            bundle.readiness.observation,
            bundle.readiness_report,
            bundle.readiness.archive.model_copy(update={"assembled_ts_ns": 0}),
            bundle.policy,
        )
    with pytest.raises(ValueError, match="readiness lineage"):
        disabled_module._verify_external_lineage(
            manifest.model_copy(update={"readiness_report_sha256": "f" * 64}),
            stop,
            bundle.readiness.observation,
            bundle.readiness_report,
            bundle.readiness.archive,
            bundle.policy,
        )
    with pytest.raises(ValueError, match="predates"):
        disabled_module._verify_external_lineage(
            manifest,
            stop.model_copy(update={"started_ts_ns": bundle.readiness_report.generated_ts_ns - 1}),
            bundle.readiness.observation,
            bundle.readiness_report,
            bundle.readiness.archive,
            bundle.policy,
        )

    with pytest.raises(ValueError, match="different stop approval"):
        disabled_module._verify_stop_approval(
            stop.model_copy(update={"stop_approval_sha256": "f" * 64}),
            bundle.readiness_report,
            bundle.stop_approval_paths,
            expected_key_id=bundle.stop_key_id,
            expected_public_key_sha256=bundle.stop_public_key_sha256,
        )
    with pytest.raises(ValueError, match="began before approval"):
        disabled_module._verify_stop_approval(
            stop.model_copy(update={"started_ts_ns": stop.started_ts_ns - 10_000_000_000}),
            bundle.readiness_report,
            bundle.stop_approval_paths,
            expected_key_id=bundle.stop_key_id,
            expected_public_key_sha256=bundle.stop_public_key_sha256,
        )

    with pytest.raises(ValueError, match="native disabled-window evidence"):
        disabled_module._verify_native_and_archive_lineage(
            manifest,
            bundle.readiness.observation,
            bundle.readiness_report,
            bundle.readiness.native.model_copy(update={"retirement_id": "different"}),
            bundle.readiness.archive,
            bundle.policy,
        )
    with pytest.raises(ValueError, match="reverified archive"):
        disabled_module._verify_native_and_archive_lineage(
            manifest,
            bundle.readiness.observation,
            bundle.readiness_report,
            bundle.readiness.native,
            bundle.readiness.archive.model_copy(update={"retirement_id": "different"}),
            bundle.policy,
        )

    with pytest.raises(ValueError, match="unbound raw evidence"):
        disabled_module._require_artifact(
            artifacts,
            "raw/missing",
            DisabledEvidenceArtifactKind.CAPABILITY_SNAPSHOT,
        )
    with pytest.raises(ValueError, match="category differs"):
        disabled_module._require_artifact(
            artifacts,
            capabilities.samples[0].evidence_path,
            DisabledEvidenceArtifactKind.BROKER_ORDER_EXPORT,
        )
    stop_artifact = artifacts[stop.actions[0].evidence_path]
    with pytest.raises(ValueError, match="stop action timestamp"):
        disabled_module._verify_raw_references(
            {
                **artifacts,
                stop_artifact.relative_path: stop_artifact.model_copy(
                    update={"captured_end_ts_ns": 0}
                ),
            },
            stop,
            capabilities,
            broker,
            credentials,
            native_stability,
        )
    capability_artifact = artifacts[capabilities.samples[0].evidence_path]
    with pytest.raises(ValueError, match="capability sample timestamp"):
        disabled_module._verify_raw_references(
            {
                **artifacts,
                capability_artifact.relative_path: capability_artifact.model_copy(
                    update={"captured_end_ts_ns": 0}
                ),
            },
            stop,
            capabilities,
            broker,
            credentials,
            native_stability,
        )
    broker_artifact = artifacts[broker.source_evidence_path]
    with pytest.raises(ValueError, match="broker export"):
        disabled_module._verify_raw_references(
            {
                **artifacts,
                broker_artifact.relative_path: broker_artifact.model_copy(
                    update={"captured_end_ts_ns": 0}
                ),
            },
            stop,
            capabilities,
            broker,
            credentials,
            native_stability,
        )
    credential_artifact = artifacts[credentials.checks[0].evidence_path]
    with pytest.raises(ValueError, match="credential audit"):
        disabled_module._verify_raw_references(
            {
                **artifacts,
                credential_artifact.relative_path: credential_artifact.model_copy(
                    update={"captured_end_ts_ns": 0}
                ),
            },
            stop,
            capabilities,
            broker,
            credentials,
            native_stability,
        )
    native_artifact = artifacts[native_stability.evidence_paths[0]]
    with pytest.raises(ValueError, match="native operational evidence"):
        disabled_module._verify_raw_references(
            {
                **artifacts,
                native_artifact.relative_path: native_artifact.model_copy(
                    update={"captured_end_ts_ns": 0}
                ),
            },
            stop,
            capabilities,
            broker,
            credentials,
            native_stability,
        )
    extra = next(iter(artifacts.values())).model_copy(
        update={"artifact_id": "extra", "relative_path": "raw/extra"}
    )
    with pytest.raises(ValueError, match="unreferenced"):
        disabled_module._verify_raw_references(
            {**artifacts, extra.relative_path: extra},
            stop,
            capabilities,
            broker,
            credentials,
            native_stability,
        )
