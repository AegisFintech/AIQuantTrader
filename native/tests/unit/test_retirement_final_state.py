from __future__ import annotations

import hashlib
import io
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

import aiquanttrader_native.retirement.archive as archive_module
import aiquanttrader_native.retirement.final_state as final_state_module
from aiquanttrader_native.retirement.archive import assemble_legacy_archive_manifest
from aiquanttrader_native.retirement.cli import main as retirement_main
from aiquanttrader_native.retirement.final_state import (
    BROKER_ACCOUNT_EXPORT_MEMBER,
    BROKER_ACCOUNT_STATE_MEMBER,
    ENTRY_PAUSE_FLAG_MEMBER,
    FINAL_TRADE_REPORT_MEMBER,
    FINAL_TRADE_REPORT_SOURCE_MEMBER,
    SERVICE_CONFIGURATION_MEMBER,
    SERVICE_STATUS_MEMBER,
    FileIdentity,
    _assemble_legacy_final_state,
    _load_archive_fact,
    _read_regular,
    _read_tar_member,
    _sha256_regular,
    _verify_capture_lineage,
    assemble_legacy_final_state,
    load_legacy_final_state,
    verify_legacy_final_state,
)
from aiquanttrader_native.retirement.models import (
    LegacyAccountMode,
    LegacyArchiveArtifact,
    LegacyArchiveArtifactKind,
    LegacyArchiveControlArtifact,
    LegacyArchiveControlKind,
    LegacyArchiveCredentialScanCheck,
    LegacyArchiveCredentialScanEvidence,
    LegacyArchiveCredentialScanPolicy,
    LegacyArchiveEvidenceManifest,
    LegacyArchiveManifest,
    LegacyArchiveRestoreCheck,
    LegacyArchiveRestoreEvidence,
    LegacyBrokerAccountStateEvidence,
    LegacyBrokerOrderEvidence,
    LegacyBrokerPositionEvidence,
    LegacyCapability,
    LegacyCommandWriterCheck,
    LegacyCommandWriterSurface,
    LegacyCredentialDetector,
    LegacyFinalTagEvidence,
    LegacyFinalTradeReportEvidence,
    LegacyManagedPositionEvidence,
    LegacyServiceConfigurationEvidence,
    RequiredNativeDrill,
    RetirementPolicy,
)

BASE = datetime(2026, 8, 1, tzinfo=UTC)
REPORT_TIME = BASE + timedelta(minutes=10)
BROKER_TIME = REPORT_TIME + timedelta(seconds=30)
SERVICE_TIME = REPORT_TIME + timedelta(seconds=60)
ARCHIVE_CREATED = SERVICE_TIME + timedelta(seconds=20)
ASSEMBLED = ARCHIVE_CREATED + timedelta(seconds=20)
RETENTION_EXPIRES = ASSEMBLED + timedelta(days=400)
COMMIT = "1" * 40
TAG_OBJECT = "2" * 40
ACCOUNT = "3" * 64
SERVER = "4" * 64
REPORT_SOURCE = b"retained output from scripts/mt5_trade_report.py\n"
BROKER_SOURCE = b"retained independently exported broker account state\n"
STATUS_SOURCE = b'{"entry_pause":1,"positions":0,"server":"redacted"}\n'
ENTRY_PAUSE_SOURCE = b"paused\n"
STATUS = hashlib.sha256(STATUS_SOURCE).hexdigest()


def _ts(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def _scan_policy() -> LegacyArchiveCredentialScanPolicy:
    return LegacyArchiveCredentialScanPolicy(
        policy_id="final-state-scan-test",
        required_detectors=tuple(LegacyCredentialDetector),
    )


def _policy() -> RetirementPolicy:
    scan = _scan_policy()
    return RetirementPolicy(
        policy_id="final-state-retirement-test",
        frozen_at_ns=_ts(BASE - timedelta(days=1)),
        minimum_native_production_observation_ns=1,
        maximum_native_operational_gap_ns=1,
        minimum_disabled_observation_ns=1,
        maximum_disabled_evidence_gap_ns=1,
        minimum_archive_retention_ns=int(timedelta(days=365).total_seconds() * 1_000_000_000),
        maximum_final_state_capture_skew_ns=int(
            timedelta(minutes=5).total_seconds() * 1_000_000_000
        ),
        maximum_final_state_age_ns=int(timedelta(hours=1).total_seconds() * 1_000_000_000),
        archive_credential_scan_policy_id=scan.policy_id,
        archive_credential_scan_policy_sha256=scan.sha256(),
        required_archive_artifacts=tuple(LegacyArchiveArtifactKind),
        required_disabled_capabilities=tuple(LegacyCapability),
        required_native_drills=tuple(RequiredNativeDrill),
    )


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(archive_module, "time_ns", lambda: _ts(ASSEMBLED))
    monkeypatch.setattr(final_state_module, "time_ns", lambda: _ts(ASSEMBLED))


def _tar_payload(
    member_name: str,
    payload: bytes,
    *,
    extras: tuple[tuple[tarfile.TarInfo, bytes], ...] = (),
) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        primary = tarfile.TarInfo(member_name)
        primary.size = len(payload)
        primary.mode = 0o600
        primary.mtime = 0
        archive.addfile(primary, io.BytesIO(payload))
        for member, content in extras:
            member.size = len(content)
            member.mtime = 0
            archive.addfile(member, io.BytesIO(content) if member.isfile() else None)
    return output.getvalue()


def _extra(name: str, content: bytes) -> tuple[tarfile.TarInfo, bytes]:
    member = tarfile.TarInfo(name)
    member.mode = 0o600
    return member, content


def _writer_source(surface: LegacyCommandWriterSurface) -> bytes:
    return f"reviewed {surface.value} command-writer inventory\n".encode()


def _report(
    *,
    positions: tuple[LegacyManagedPositionEvidence, ...] = (),
    total_positions: int = 0,
    entry_pause: bool = True,
    account: str = ACCOUNT,
    server: str = SERVER,
    status: str = STATUS,
    captured_ts_ns: int = _ts(REPORT_TIME),
) -> LegacyFinalTradeReportEvidence:
    return LegacyFinalTradeReportEvidence(
        retirement_id="retirement-final-state-001",
        capture_id="trade-report-001",
        captured_ts_ns=captured_ts_ns,
        generated_by="mt5-trade-report-export",
        reviewed_by="risk-reviewer",
        source_report_sha256=hashlib.sha256(REPORT_SOURCE).hexdigest(),
        account_login_sha256=account,
        broker_server_sha256=server,
        mt5_reported_open_positions=total_positions,
        entry_pause_reported=entry_pause,
        final_status_sha256=status,
        managed_positions=positions,
    )


def _broker(
    *,
    positions: tuple[LegacyBrokerPositionEvidence, ...] = (),
    orders: tuple[LegacyBrokerOrderEvidence, ...] = (),
    account_mode: LegacyAccountMode = LegacyAccountMode.DEMO,
    account: str = ACCOUNT,
    server: str = SERVER,
    captured_ts_ns: int = _ts(BROKER_TIME),
) -> LegacyBrokerAccountStateEvidence:
    return LegacyBrokerAccountStateEvidence(
        retirement_id="retirement-final-state-001",
        capture_id="broker-account-001",
        captured_ts_ns=captured_ts_ns,
        captured_by="broker-export-operator",
        reviewed_by="independent-broker-reviewer",
        account_mode=account_mode,
        account_login_sha256=account,
        broker_server_sha256=server,
        source_export_sha256=hashlib.sha256(BROKER_SOURCE).hexdigest(),
        positions=positions,
        pending_orders=orders,
    )


def _service(
    *,
    entry_pause_file: bool = True,
    entry_pause_reported: bool = True,
    writer_surface: LegacyCommandWriterSurface | None = None,
    status: str = STATUS,
    captured_ts_ns: int = _ts(SERVICE_TIME),
) -> LegacyServiceConfigurationEvidence:
    return LegacyServiceConfigurationEvidence(
        retirement_id="retirement-final-state-001",
        capture_id="service-configuration-001",
        captured_ts_ns=captured_ts_ns,
        captured_by="host-inventory-operator",
        reviewed_by="independent-service-reviewer",
        final_status_sha256=status,
        entry_pause_file_present=entry_pause_file,
        entry_pause_file_sha256=(
            hashlib.sha256(ENTRY_PAUSE_SOURCE).hexdigest() if entry_pause_file else None
        ),
        ea_entry_pause_reported=entry_pause_reported,
        command_writer_checks=tuple(
            LegacyCommandWriterCheck(
                surface=surface,
                evidence_member=f"final-state/command-writers/{surface.value}.txt",
                evidence_sha256=hashlib.sha256(_writer_source(surface)).hexdigest(),
                active_writer_ids=("writer-001",) if surface is writer_surface else (),
            )
            for surface in LegacyCommandWriterSurface
        ),
    )


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


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
    captured_ts_ns = (
        value.reviewed_ts_ns
        if isinstance(value, (LegacyArchiveRestoreEvidence, LegacyArchiveCredentialScanEvidence))
        else value.captured_ts_ns
    )
    return LegacyArchiveControlArtifact(
        kind=kind,
        relative_path=relative_path,
        content_sha256=hashlib.sha256(payload).hexdigest(),
        byte_count=len(payload),
        captured_ts_ns=captured_ts_ns,
    )


def _bundle(
    tmp_path: Path,
    *,
    report: LegacyFinalTradeReportEvidence | None = None,
    broker: LegacyBrokerAccountStateEvidence | None = None,
    service: LegacyServiceConfigurationEvidence | None = None,
) -> tuple[Path, LegacyArchiveManifest]:
    report = report or _report()
    broker = broker or _broker()
    service = service or _service()
    root = (tmp_path / "legacy-archive").resolve()
    root.mkdir(parents=True)
    facts: dict[LegacyArchiveArtifactKind, tuple[str, bytes, int]] = {
        LegacyArchiveArtifactKind.FINAL_TRADE_REPORT: (
            "artifacts/final_trade_report.tar",
            _tar_payload(
                FINAL_TRADE_REPORT_MEMBER,
                report.canonical_bytes() + b"\n",
                extras=(_extra(FINAL_TRADE_REPORT_SOURCE_MEMBER, REPORT_SOURCE),),
            ),
            report.captured_ts_ns,
        ),
        LegacyArchiveArtifactKind.BROKER_ACCOUNT_STATE: (
            "artifacts/broker_account_state.tar",
            _tar_payload(
                BROKER_ACCOUNT_STATE_MEMBER,
                broker.canonical_bytes() + b"\n",
                extras=(_extra(BROKER_ACCOUNT_EXPORT_MEMBER, BROKER_SOURCE),),
            ),
            broker.captured_ts_ns,
        ),
        LegacyArchiveArtifactKind.SERVICE_CONFIGURATION: (
            "artifacts/service_configuration.tar",
            _tar_payload(
                SERVICE_CONFIGURATION_MEMBER,
                service.canonical_bytes() + b"\n",
                extras=(
                    _extra(SERVICE_STATUS_MEMBER, STATUS_SOURCE),
                    *(
                        (_extra(ENTRY_PAUSE_FLAG_MEMBER, ENTRY_PAUSE_SOURCE),)
                        if service.entry_pause_file_present
                        else ()
                    ),
                    *tuple(
                        _extra(check.evidence_member, _writer_source(check.surface))
                        for check in service.command_writer_checks
                    ),
                ),
            ),
            service.captured_ts_ns,
        ),
    }
    artifacts: list[LegacyArchiveArtifact] = []
    for index, kind in enumerate(LegacyArchiveArtifactKind, start=1):
        relative_path, payload, captured_ts_ns = facts.get(
            kind,
            (
                f"artifacts/{kind.value}.tar.zst",
                f"retained legacy category {kind.value}\n".encode(),
                _ts(BASE + timedelta(minutes=index)),
            ),
        )
        _write(root / relative_path, payload)
        artifacts.append(
            LegacyArchiveArtifact(
                kind=kind,
                relative_path=relative_path,
                content_sha256=hashlib.sha256(payload).hexdigest(),
                byte_count=len(payload),
                captured_ts_ns=captured_ts_ns,
            )
        )
    artifact_tuple = tuple(artifacts)
    last_capture = max(item.captured_ts_ns for item in artifact_tuple)
    restore = LegacyArchiveRestoreEvidence(
        retirement_id="retirement-final-state-001",
        started_ts_ns=last_capture + 1,
        ended_ts_ns=last_capture + 2,
        reviewed_ts_ns=last_capture + 3,
        reviewer="restore-reviewer",
        checks=tuple(
            LegacyArchiveRestoreCheck(
                kind=item.kind,
                source_sha256=item.content_sha256,
                source_byte_count=item.byte_count,
                restored_sha256=item.content_sha256,
                restored_byte_count=item.byte_count,
            )
            for item in artifact_tuple
        ),
    )
    scan_policy = _scan_policy()
    scan = LegacyArchiveCredentialScanEvidence(
        retirement_id="retirement-final-state-001",
        started_ts_ns=last_capture + 4,
        ended_ts_ns=last_capture + 5,
        reviewed_ts_ns=last_capture + 6,
        reviewer="security-reviewer",
        scanner_name="approved-offline-scanner",
        scanner_version="1.0.0",
        policy_id=scan_policy.policy_id,
        policy_sha256=scan_policy.sha256(),
        checks=tuple(
            LegacyArchiveCredentialScanCheck(
                kind=item.kind,
                artifact_sha256=item.content_sha256,
            )
            for item in artifact_tuple
        ),
    )
    tag = LegacyFinalTagEvidence(
        retirement_id="retirement-final-state-001",
        captured_ts_ns=last_capture + 7,
        source_commit_sha=COMMIT,
        final_tag_commit_sha=COMMIT,
        tag_object_sha=TAG_OBJECT,
        verification_output_sha256="7" * 64,
        reviewer="release-reviewer",
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
            scan,
        ),
        _control(
            root,
            LegacyArchiveControlKind.FINAL_TAG_EVIDENCE,
            "controls/final-tag-evidence.json",
            tag,
        ),
    )
    evidence = LegacyArchiveEvidenceManifest(
        retirement_id="retirement-final-state-001",
        created_ts_ns=_ts(ARCHIVE_CREATED),
        retention_expires_ts_ns=_ts(RETENTION_EXPIRES),
        source_commit_sha=COMMIT,
        final_tag_commit_sha=COMMIT,
        artifacts=artifact_tuple,
        controls=controls,
    )
    _write(root / "legacy-archive-evidence.json", evidence.canonical_bytes() + b"\n")
    archive = assemble_legacy_archive_manifest(
        root,
        policy=_policy(),
        credential_scan_policy=_scan_policy(),
    )
    return root, archive


def _policy_files(tmp_path: Path) -> tuple[Path, Path]:
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
                "maximum_disabled_evidence_gap_ns = 1",
                f"minimum_archive_retention_ns = {policy.minimum_archive_retention_ns}",
                "maximum_final_state_capture_skew_ns = "
                f"{policy.maximum_final_state_capture_skew_ns}",
                f"maximum_final_state_age_ns = {policy.maximum_final_state_age_ns}",
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


def test_final_state_assembly_reconciles_flat_demo_account_and_cli(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, archive = _bundle(tmp_path)
    final_state = assemble_legacy_final_state(
        root,
        archive,
        policy=_policy(),
        credential_scan_policy=_scan_policy(),
    )
    assert final_state.account_mode is LegacyAccountMode.DEMO
    assert final_state.open_managed_positions == 0
    assert final_state.open_unmanaged_positions == 0
    assert final_state.pending_orders == 0
    assert final_state.entry_pause_active
    assert final_state.command_file_writer_count == 0
    assert final_state.archive_manifest_sha256 == archive.sha256()
    assert (
        verify_legacy_final_state(
            root,
            archive,
            final_state,
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
        )
        == final_state
    )

    archive_path = (tmp_path / "archive-manifest.json").resolve()
    archive_path.write_bytes(archive.canonical_bytes() + b"\n")
    policy_path, scan_path = _policy_files(tmp_path)
    output = (tmp_path / "final-state.json").resolve()
    assert (
        retirement_main(
            [
                "assemble-final-state",
                "--evidence-root",
                str(root),
                "--archive-manifest",
                str(archive_path),
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
    assert load_legacy_final_state(output) == final_state
    assert (
        retirement_main(
            [
                "verify-final-state",
                "--evidence-root",
                str(root),
                "--archive-manifest",
                str(archive_path),
                "--final-state",
                str(output),
                "--policy",
                str(policy_path),
                "--credential-scan-policy",
                str(scan_path),
            ]
        )
        == 0
    )
    assert final_state.archive_bundle_sha256 in capsys.readouterr().out


def test_final_state_represents_nonready_facts_without_granting_authority(tmp_path: Path) -> None:
    managed_id = "8" * 64
    unmanaged_id = "9" * 64
    report = _report(
        positions=(LegacyManagedPositionEvidence(position_id_sha256=managed_id),),
        total_positions=2,
        entry_pause=False,
    )
    broker = _broker(
        account_mode=LegacyAccountMode.LIVE,
        positions=(
            LegacyBrokerPositionEvidence(position_id_sha256=managed_id, instrument_id="XAUUSD"),
            LegacyBrokerPositionEvidence(position_id_sha256=unmanaged_id, instrument_id="BTCUSD"),
        ),
        orders=(LegacyBrokerOrderEvidence(order_id_sha256="a" * 64, instrument_id="XAUUSD"),),
    )
    service = _service(
        entry_pause_file=False,
        entry_pause_reported=False,
        writer_surface=LegacyCommandWriterSurface.PROCESS_TABLE,
    )
    root, archive = _bundle(tmp_path, report=report, broker=broker, service=service)
    final_state = assemble_legacy_final_state(
        root,
        archive,
        policy=_policy(),
        credential_scan_policy=_scan_policy(),
    )
    assert final_state.account_mode is LegacyAccountMode.LIVE
    assert final_state.open_managed_positions == 1
    assert final_state.open_unmanaged_positions == 1
    assert final_state.pending_orders == 1
    assert not final_state.entry_pause_active
    assert final_state.command_file_writer_count == 1


def test_final_state_cli_refuses_to_mutate_the_evidence_root(tmp_path: Path) -> None:
    root, archive = _bundle(tmp_path)
    archive_path = (tmp_path / "archive-manifest.json").resolve()
    archive_path.write_bytes(archive.canonical_bytes() + b"\n")
    policy_path, scan_path = _policy_files(tmp_path)
    forbidden = root / "final-state.json"
    assert (
        retirement_main(
            [
                "assemble-final-state",
                "--evidence-root",
                str(root),
                "--archive-manifest",
                str(archive_path),
                "--policy",
                str(policy_path),
                "--credential-scan-policy",
                str(scan_path),
                "--output",
                str(forbidden),
            ]
        )
        == 2
    )
    assert not forbidden.exists()


def test_final_state_rejects_raw_source_hash_mismatch(tmp_path: Path) -> None:
    report = _report().model_copy(update={"source_report_sha256": "f" * 64})
    root, archive = _bundle(tmp_path, report=report)
    with pytest.raises(ValueError, match="source evidence hash differs"):
        assemble_legacy_final_state(
            root,
            archive,
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
        )


@pytest.mark.parametrize(
    ("report", "broker", "service", "expected"),
    (
        (_report(account="a" * 64), _broker(), _service(), "account identities differ"),
        (_report(total_positions=1), _broker(), _service(), "position totals differ"),
        (
            _report(
                positions=(LegacyManagedPositionEvidence(position_id_sha256="8" * 64),),
                total_positions=1,
            ),
            _broker(
                positions=(
                    LegacyBrokerPositionEvidence(
                        position_id_sha256="9" * 64,
                        instrument_id="XAUUSD",
                    ),
                )
            ),
            _service(),
            "absent from the broker snapshot",
        ),
        (_report(entry_pause=False), _broker(), _service(), "disagree on entry pause"),
        (_report(status="a" * 64), _broker(), _service(), "different status captures"),
        (
            _report().model_copy(update={"retirement_id": "different-retirement"}),
            _broker(),
            _service(),
            "retirement identities differ",
        ),
        (
            _report(
                positions=(LegacyManagedPositionEvidence(position_id_sha256="8" * 64),),
                total_positions=1,
            ),
            _broker(
                positions=(
                    LegacyBrokerPositionEvidence(
                        position_id_sha256="8" * 64,
                        instrument_id="BTCUSD",
                    ),
                )
            ),
            _service(),
            "position instruments differ",
        ),
    ),
)
def test_final_state_rejects_cross_source_disagreement(
    tmp_path: Path,
    report: LegacyFinalTradeReportEvidence,
    broker: LegacyBrokerAccountStateEvidence,
    service: LegacyServiceConfigurationEvidence,
    expected: str,
) -> None:
    root, archive = _bundle(tmp_path, report=report, broker=broker, service=service)
    with pytest.raises(ValueError, match=expected):
        assemble_legacy_final_state(
            root,
            archive,
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
        )


def test_final_state_rejects_capture_binding_skew_future_and_staleness(
    tmp_path: Path,
) -> None:
    stale_report = _report(captured_ts_ns=_ts(REPORT_TIME - timedelta(hours=2)))
    root, archive = _bundle(tmp_path / "stale", report=stale_report)
    with pytest.raises(ValueError, match="capture skew exceeds policy"):
        assemble_legacy_final_state(
            root,
            archive,
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
        )


def test_final_state_capture_lineage_enforces_bindings_future_and_age(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _report()
    broker = _broker()
    service = _service()
    _root, archive = _bundle(tmp_path, report=report, broker=broker, service=service)
    by_kind = {item.kind: item for item in archive.artifacts}
    report_artifact = by_kind[LegacyArchiveArtifactKind.FINAL_TRADE_REPORT]
    broker_artifact = by_kind[LegacyArchiveArtifactKind.BROKER_ACCOUNT_STATE]
    service_artifact = by_kind[LegacyArchiveArtifactKind.SERVICE_CONFIGURATION]

    with pytest.raises(ValueError, match="capture times differ"):
        _verify_capture_lineage(
            archive,
            report_artifact.model_copy(update={"captured_ts_ns": report.captured_ts_ns + 1}),
            broker_artifact,
            service_artifact,
            report,
            broker,
            service,
            policy=_policy(),
            assembled_ts_ns=_ts(ASSEMBLED),
        )
    with pytest.raises(ValueError, match="dated after assembly"):
        _verify_capture_lineage(
            archive,
            report_artifact,
            broker_artifact,
            service_artifact,
            report,
            broker,
            service,
            policy=_policy(),
            assembled_ts_ns=service.captured_ts_ns - 1,
        )
    with pytest.raises(ValueError, match="stale at assembly"):
        _verify_capture_lineage(
            archive,
            report_artifact,
            broker_artifact,
            service_artifact,
            report,
            broker,
            service,
            policy=_policy().model_copy(update={"maximum_final_state_age_ns": 1}),
            assembled_ts_ns=_ts(ASSEMBLED),
        )
    with pytest.raises(ValueError, match="cannot predate archive assembly"):
        _assemble_legacy_final_state(
            _root,
            archive,
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
            assembled_ts_ns=archive.assembled_ts_ns - 1,
        )

    root, archive = _bundle(tmp_path / "valid")
    final_state = assemble_legacy_final_state(
        root,
        archive,
        policy=_policy(),
        credential_scan_policy=_scan_policy(),
    )
    monkeypatch.setattr(
        final_state_module,
        "time_ns",
        lambda: final_state.captured_ts_ns + _policy().maximum_final_state_age_ns + 1,
    )
    with pytest.raises(ValueError, match="stale at verification"):
        verify_legacy_final_state(
            root,
            archive,
            final_state,
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
        )


def test_final_state_verification_and_loader_reject_tampering(tmp_path: Path) -> None:
    root, archive = _bundle(tmp_path)
    final_state = assemble_legacy_final_state(
        root,
        archive,
        policy=_policy(),
        credential_scan_policy=_scan_policy(),
    )
    with pytest.raises(ValueError, match="does not match"):
        verify_legacy_final_state(
            root,
            archive,
            final_state.model_copy(update={"pending_orders": 1}),
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
        )
    with pytest.raises(ValueError, match="dated after verification"):
        verify_legacy_final_state(
            root,
            archive,
            final_state.model_copy(update={"assembled_ts_ns": _ts(ASSEMBLED) + 1}),
            policy=_policy(),
            credential_scan_policy=_scan_policy(),
        )
    output = (tmp_path / "noncanonical.json").resolve()
    output.write_bytes(final_state.canonical_bytes())
    with pytest.raises(ValueError, match="not canonical JSON"):
        load_legacy_final_state(output)


@pytest.mark.parametrize("mutation", ("missing", "traversal", "symlink", "duplicate", "invalid"))
def test_tar_reader_rejects_unsafe_or_invalid_evidence(tmp_path: Path, mutation: str) -> None:
    path = (tmp_path / "fact.tar").resolve()
    if mutation == "missing":
        payload = _tar_payload("different.json", b"{}\n")
        expected = "member is missing"
    elif mutation == "traversal":
        payload = _tar_payload("../final-state.json", b"{}\n")
        expected = "path is unsafe"
    elif mutation == "symlink":
        link = tarfile.TarInfo("unsafe-link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        payload = _tar_payload("different.json", b"{}\n", extras=((link, b""),))
        expected = "non-regular member"
    elif mutation == "duplicate":
        duplicate = tarfile.TarInfo(FINAL_TRADE_REPORT_MEMBER)
        payload = _tar_payload(
            FINAL_TRADE_REPORT_MEMBER,
            b"{}\n",
            extras=((duplicate, b"{}\n"),),
        )
        expected = "path is unsafe"
    else:
        payload = b"not a tar archive\n"
        expected = "not a valid tar"
    path.write_bytes(payload)
    _digest, identity = _sha256_regular(path)
    with pytest.raises(ValueError, match=expected):
        _read_tar_member(path, FINAL_TRADE_REPORT_MEMBER, expected_identity=identity)


def test_tar_reader_enforces_identity_member_and_content_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = (tmp_path / "fact.tar").resolve()
    directory = tarfile.TarInfo("support")
    directory.type = tarfile.DIRTYPE
    payload = _tar_payload(
        FINAL_TRADE_REPORT_MEMBER,
        _report().canonical_bytes() + b"\n",
        extras=((directory, b""),),
    )
    path.write_bytes(payload)
    digest, identity = _sha256_regular(path)
    assert digest == hashlib.sha256(payload).hexdigest()
    assert _read_tar_member(path, FINAL_TRADE_REPORT_MEMBER, expected_identity=identity)

    wrong_identity = FileIdentity(
        device=identity.device,
        inode=identity.inode,
        byte_count=identity.byte_count + 1,
        modified_ts_ns=identity.modified_ts_ns,
        changed_ts_ns=identity.changed_ts_ns,
    )
    with pytest.raises(ValueError, match="changed before inspection"):
        _read_tar_member(path, FINAL_TRADE_REPORT_MEMBER, expected_identity=wrong_identity)
    monkeypatch.setattr(final_state_module, "MAX_TAR_MEMBERS", 0)
    with pytest.raises(ValueError, match="too many members"):
        _read_tar_member(path, FINAL_TRADE_REPORT_MEMBER, expected_identity=identity)
    monkeypatch.setattr(final_state_module, "MAX_TAR_MEMBERS", 100_000)
    monkeypatch.setattr(final_state_module, "MAX_TAR_CONTENT_BYTES", 0)
    with pytest.raises(ValueError, match="content exceeds"):
        _read_tar_member(path, FINAL_TRADE_REPORT_MEMBER, expected_identity=identity)


def test_final_state_file_readers_and_archive_fact_fail_closed(tmp_path: Path) -> None:
    missing = (tmp_path / "missing").resolve()
    with pytest.raises(ValueError, match="cannot open final legacy state evidence"):
        _read_regular(missing, maximum_bytes=10)
    with pytest.raises(ValueError, match="cannot hash final legacy state archive"):
        _sha256_regular(missing)

    empty = (tmp_path / "empty").resolve()
    empty.touch()
    with pytest.raises(ValueError, match="evidence size is invalid"):
        _read_regular(empty, maximum_bytes=10)
    with pytest.raises(ValueError, match="non-empty regular file"):
        _sha256_regular(empty)

    payload = _tar_payload(FINAL_TRADE_REPORT_MEMBER, _report().model_dump_json().encode())
    path = (tmp_path / "artifacts/noncanonical.tar").resolve()
    path.parent.mkdir()
    path.write_bytes(payload)
    artifact = LegacyArchiveArtifact(
        kind=LegacyArchiveArtifactKind.FINAL_TRADE_REPORT,
        relative_path="artifacts/noncanonical.tar",
        content_sha256=hashlib.sha256(payload).hexdigest(),
        byte_count=len(payload),
        captured_ts_ns=_ts(REPORT_TIME),
    )
    with pytest.raises(ValueError, match="not canonical JSON"):
        _load_archive_fact(
            tmp_path.resolve(),
            artifact,
            FINAL_TRADE_REPORT_MEMBER,
            LegacyFinalTradeReportEvidence,
        )
    with pytest.raises(ValueError, match="archive artifact changed"):
        _load_archive_fact(
            tmp_path.resolve(),
            artifact.model_copy(update={"content_sha256": "f" * 64}),
            FINAL_TRADE_REPORT_MEMBER,
            LegacyFinalTradeReportEvidence,
        )


def test_final_state_input_contracts_reject_duplicates_and_same_reviewer() -> None:
    position = LegacyManagedPositionEvidence(position_id_sha256="8" * 64)
    with pytest.raises(ValidationError, match="duplicate managed positions"):
        _report(positions=(position, position), total_positions=2)
    with pytest.raises(ValidationError, match="independent reviewer"):
        LegacyBrokerAccountStateEvidence(
            retirement_id="retirement-final-state-001",
            capture_id="broker-account-001",
            captured_ts_ns=1,
            captured_by="same-person",
            reviewed_by="same-person",
            account_mode=LegacyAccountMode.DEMO,
            account_login_sha256=ACCOUNT,
            broker_server_sha256=SERVER,
            source_export_sha256="6" * 64,
        )
    checks = tuple(
        LegacyCommandWriterCheck(
            surface=surface,
            evidence_member=f"final-state/command-writers/{surface.value}.txt",
            evidence_sha256="7" * 64,
        )
        for surface in LegacyCommandWriterSurface
    )
    with pytest.raises(ValidationError, match="every command writer surface"):
        LegacyServiceConfigurationEvidence(
            retirement_id="retirement-final-state-001",
            capture_id="service-configuration-001",
            captured_ts_ns=1,
            captured_by="capture-person",
            reviewed_by="review-person",
            final_status_sha256=STATUS,
            entry_pause_file_present=True,
            entry_pause_file_sha256=hashlib.sha256(ENTRY_PAUSE_SOURCE).hexdigest(),
            ea_entry_pause_reported=True,
            command_writer_checks=(*checks[:-1], checks[0]),
        )
