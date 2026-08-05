from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import test_retirement_collector as native_testkit
import test_retirement_final_state as legacy_testkit
from pydantic import ValidationError

import aiquanttrader.retirement.archive as archive_module
import aiquanttrader.retirement.collector as collector_module
import aiquanttrader.retirement.final_state as final_state_module
import aiquanttrader.retirement.readiness as readiness_module
from aiquanttrader.retirement.cli import main as retirement_main
from aiquanttrader.retirement.collector import assemble_native_production_observation
from aiquanttrader.retirement.final_state import assemble_legacy_final_state
from aiquanttrader.retirement.models import (
    LegacyArchiveArtifactKind,
    LegacyArchiveCredentialScanPolicy,
    LegacyArchiveManifest,
    LegacyCapability,
    LegacyFinalState,
    NativeProductionObservation,
    RequiredNativeDrill,
    RetirementPolicy,
    RetirementReadinessObservation,
    RetirementReadinessReport,
)
from aiquanttrader.retirement.readiness import (
    assemble_retirement_readiness_observation,
    load_retirement_readiness_observation,
    verify_retirement_readiness_observation,
)

RETIREMENT_ID = "retirement-final-state-001"
LEGACY_BASE = datetime(2026, 1, 22, tzinfo=UTC)
REPORT_TIME = LEGACY_BASE + timedelta(minutes=10)
BROKER_TIME = REPORT_TIME + timedelta(seconds=30)
SERVICE_TIME = REPORT_TIME + timedelta(seconds=60)
ARCHIVE_CREATED = SERVICE_TIME + timedelta(seconds=20)
ARCHIVE_ASSEMBLED = ARCHIVE_CREATED + timedelta(seconds=20)
READINESS_OBSERVED = ARCHIVE_ASSEMBLED + timedelta(seconds=20)


@dataclass(frozen=True, slots=True)
class ReadinessEvidence:
    native_root: Path
    legacy_root: Path
    native: NativeProductionObservation
    archive: LegacyArchiveManifest
    final_state: LegacyFinalState
    observation: RetirementReadinessObservation
    policy: RetirementPolicy
    scan_policy: LegacyArchiveCredentialScanPolicy
    key_id: str
    public_key_sha256: str


def _ts(value: datetime) -> int:
    return int(value.timestamp() * 1_000_000_000)


def _policy() -> RetirementPolicy:
    scan_policy = legacy_testkit._scan_policy()
    return RetirementPolicy(
        policy_id="readiness-assembly-test",
        frozen_at_ns=native_testkit._timestamp_ns(native_testkit.BASE - timedelta(days=1)),
        minimum_native_production_observation_ns=1,
        maximum_native_operational_gap_ns=native_testkit._policy().maximum_native_operational_gap_ns,
        minimum_disabled_observation_ns=1,
        maximum_disabled_evidence_gap_ns=1,
        minimum_archive_retention_ns=int(timedelta(days=365).total_seconds() * 1_000_000_000),
        maximum_final_state_capture_skew_ns=int(
            timedelta(minutes=5).total_seconds() * 1_000_000_000
        ),
        maximum_final_state_age_ns=int(timedelta(hours=1).total_seconds() * 1_000_000_000),
        archive_credential_scan_policy_id=scan_policy.policy_id,
        archive_credential_scan_policy_sha256=scan_policy.sha256(),
        required_archive_artifacts=tuple(LegacyArchiveArtifactKind),
        required_disabled_capabilities=tuple(LegacyCapability),
        required_native_drills=tuple(RequiredNativeDrill),
    )


def _write_policy_files(tmp_path: Path, policy: RetirementPolicy) -> tuple[Path, Path]:
    scan = legacy_testkit._scan_policy()
    scan_path = (tmp_path / "readiness-scan-policy.toml").resolve()
    scan_path.write_text(
        "\n".join(
            (
                "schema_version = 1",
                f'policy_id = "{scan.policy_id}"',
                "recursive_archive_scan = true",
                "maximum_findings = 0",
                "required_detectors = ["
                + ",".join(f'"{item.value}"' for item in scan.required_detectors)
                + "]",
            )
        ),
        encoding="utf-8",
    )
    policy_path = (tmp_path / "readiness-policy.toml").resolve()
    policy_path.write_text(
        "\n".join(
            (
                "schema_version = 1",
                f'policy_id = "{policy.policy_id}"',
                f"frozen_at_ns = {policy.frozen_at_ns}",
                "minimum_native_production_observation_ns = 1",
                f"maximum_native_operational_gap_ns = {policy.maximum_native_operational_gap_ns}",
                f"minimum_disabled_observation_ns = {policy.minimum_disabled_observation_ns}",
                f"maximum_disabled_evidence_gap_ns = {policy.maximum_disabled_evidence_gap_ns}",
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


def _evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ReadinessEvidence:
    native_root, native_manifest = native_testkit._bundle(tmp_path)
    native_manifest = native_manifest.model_copy(update={"retirement_id": RETIREMENT_ID})
    (native_root / "production-manifest.json").write_bytes(
        native_manifest.canonical_bytes() + b"\n"
    )

    monkeypatch.setattr(legacy_testkit, "BASE", LEGACY_BASE)
    monkeypatch.setattr(legacy_testkit, "ARCHIVE_CREATED", ARCHIVE_CREATED)
    monkeypatch.setattr(legacy_testkit, "ASSEMBLED", ARCHIVE_ASSEMBLED)
    monkeypatch.setattr(
        legacy_testkit,
        "RETENTION_EXPIRES",
        ARCHIVE_ASSEMBLED + timedelta(days=400),
    )
    monkeypatch.setattr(archive_module, "time_ns", lambda: _ts(ARCHIVE_ASSEMBLED))
    legacy_root, archive = legacy_testkit._bundle(
        tmp_path,
        report=legacy_testkit._report(captured_ts_ns=_ts(REPORT_TIME)),
        broker=legacy_testkit._broker(captured_ts_ns=_ts(BROKER_TIME)),
        service=legacy_testkit._service(captured_ts_ns=_ts(SERVICE_TIME)),
    )

    observed_ts_ns = _ts(READINESS_OBSERVED)
    monkeypatch.setattr(archive_module, "time_ns", lambda: observed_ts_ns)
    monkeypatch.setattr(collector_module, "time_ns", lambda: observed_ts_ns)
    monkeypatch.setattr(final_state_module, "time_ns", lambda: observed_ts_ns)
    monkeypatch.setattr(readiness_module, "time_ns", lambda: observed_ts_ns)

    policy = _policy()
    scan_policy = legacy_testkit._scan_policy()
    key_id, public_key_sha256 = native_testkit._trust(native_root)
    native = assemble_native_production_observation(
        native_root,
        policy=policy,
        expected_key_id=key_id,
        expected_public_key_sha256=public_key_sha256,
    )
    final_state = assemble_legacy_final_state(
        legacy_root,
        archive,
        policy=policy,
        credential_scan_policy=scan_policy,
    )
    observation = assemble_retirement_readiness_observation(
        native_root,
        legacy_root,
        native,
        archive,
        final_state,
        policy=policy,
        credential_scan_policy=scan_policy,
        expected_key_id=key_id,
        expected_public_key_sha256=public_key_sha256,
    )
    return ReadinessEvidence(
        native_root=native_root,
        legacy_root=legacy_root,
        native=native,
        archive=archive,
        final_state=final_state,
        observation=observation,
        policy=policy,
        scan_policy=scan_policy,
        key_id=key_id,
        public_key_sha256=public_key_sha256,
    )


def _write_inputs(tmp_path: Path, evidence: ReadinessEvidence) -> tuple[Path, Path, Path]:
    native_path = (tmp_path / "native-observation.json").resolve()
    archive_path = (tmp_path / "archive-manifest.json").resolve()
    final_state_path = (tmp_path / "legacy-final-state.json").resolve()
    native_path.write_bytes(evidence.native.canonical_bytes() + b"\n")
    archive_path.write_bytes(evidence.archive.canonical_bytes() + b"\n")
    final_state_path.write_bytes(evidence.final_state.canonical_bytes() + b"\n")
    return native_path, archive_path, final_state_path


def _replay_args(
    evidence: ReadinessEvidence,
    observation_path: Path,
    policy_path: Path,
    scan_path: Path,
) -> list[str]:
    return [
        "--native-evidence-root",
        str(evidence.native_root),
        "--legacy-evidence-root",
        str(evidence.legacy_root),
        "--observation",
        str(observation_path),
        "--policy",
        str(policy_path),
        "--credential-scan-policy",
        str(scan_path),
        "--approval-key-id",
        evidence.key_id,
        "--approval-public-key-sha256",
        evidence.public_key_sha256,
    ]


def test_readiness_assembly_replays_real_bundles_and_evaluates_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evidence = _evidence(tmp_path, monkeypatch)
    assert evidence.native.retirement_id == RETIREMENT_ID
    assert evidence.observation.retirement_id == RETIREMENT_ID
    assert (
        verify_retirement_readiness_observation(
            evidence.native_root,
            evidence.legacy_root,
            evidence.observation,
            policy=evidence.policy,
            credential_scan_policy=evidence.scan_policy,
            expected_key_id=evidence.key_id,
            expected_public_key_sha256=evidence.public_key_sha256,
        )
        == evidence.observation
    )

    native_path, archive_path, final_state_path = _write_inputs(tmp_path, evidence)
    policy_path, scan_path = _write_policy_files(tmp_path, evidence.policy)
    observation_path = (tmp_path / "readiness-observation.json").resolve()
    assert (
        retirement_main(
            [
                "assemble-readiness",
                "--native-evidence-root",
                str(evidence.native_root),
                "--legacy-evidence-root",
                str(evidence.legacy_root),
                "--native-observation",
                str(native_path),
                "--archive-manifest",
                str(archive_path),
                "--final-state",
                str(final_state_path),
                "--policy",
                str(policy_path),
                "--credential-scan-policy",
                str(scan_path),
                "--approval-key-id",
                evidence.key_id,
                "--approval-public-key-sha256",
                evidence.public_key_sha256,
                "--output",
                str(observation_path),
            ]
        )
        == 0
    )
    assert load_retirement_readiness_observation(observation_path) == evidence.observation
    assert (
        retirement_main(
            ["verify-readiness", *_replay_args(evidence, observation_path, policy_path, scan_path)]
        )
        == 0
    )
    report_path = (tmp_path / "readiness-report.json").resolve()
    assert (
        retirement_main(
            [
                "evaluate-readiness",
                *_replay_args(evidence, observation_path, policy_path, scan_path),
                "--output",
                str(report_path),
            ]
        )
        == 0
    )
    report = RetirementReadinessReport.model_validate_json(report_path.read_bytes())
    assert report.awaiting_stop_approval
    assert report.observation_sha256 == evidence.observation.sha256()
    assert RETIREMENT_ID in capsys.readouterr().out


def test_readiness_cli_refuses_outputs_inside_evidence_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _evidence(tmp_path, monkeypatch)
    native_path, archive_path, final_state_path = _write_inputs(tmp_path, evidence)
    policy_path, scan_path = _write_policy_files(tmp_path, evidence.policy)
    forbidden = evidence.native_root / "readiness-observation.json"
    assert (
        retirement_main(
            [
                "assemble-readiness",
                "--native-evidence-root",
                str(evidence.native_root),
                "--legacy-evidence-root",
                str(evidence.legacy_root),
                "--native-observation",
                str(native_path),
                "--archive-manifest",
                str(archive_path),
                "--final-state",
                str(final_state_path),
                "--policy",
                str(policy_path),
                "--credential-scan-policy",
                str(scan_path),
                "--approval-key-id",
                evidence.key_id,
                "--approval-public-key-sha256",
                evidence.public_key_sha256,
                "--output",
                str(forbidden),
            ]
        )
        == 2
    )
    assert not forbidden.exists()


def test_readiness_evaluation_rejects_tampered_observation_and_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _evidence(tmp_path, monkeypatch)
    policy_path, scan_path = _write_policy_files(tmp_path, evidence.policy)
    tampered = evidence.observation.model_copy(
        update={"native": evidence.native.model_copy(update={"evidence_bundle_sha256": "f" * 64})}
    )
    observation_path = (tmp_path / "tampered-readiness.json").resolve()
    observation_path.write_bytes(tampered.canonical_bytes() + b"\n")
    report_path = (tmp_path / "tampered-report.json").resolve()
    assert (
        retirement_main(
            [
                "evaluate-readiness",
                *_replay_args(evidence, observation_path, policy_path, scan_path),
                "--output",
                str(report_path),
            ]
        )
        == 2
    )
    assert not report_path.exists()

    observation_path.write_bytes(evidence.observation.canonical_bytes() + b"\n")
    supporting_path = evidence.native_root / "raw/support/native_rollback.txt"
    supporting_path.write_bytes(b"changed retained evidence\n")
    assert (
        retirement_main(
            ["verify-readiness", *_replay_args(evidence, observation_path, policy_path, scan_path)]
        )
        == 2
    )


def test_readiness_rejects_cross_bundle_identity_future_and_noncanonical_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence = _evidence(tmp_path, monkeypatch)
    with pytest.raises(ValidationError, match="native identities"):
        RetirementReadinessObservation.model_validate(
            {
                **evidence.observation.model_dump(),
                "native": evidence.native.model_copy(update={"retirement_id": "different"}),
            }
        )

    monkeypatch.setattr(readiness_module, "time_ns", lambda: _ts(READINESS_OBSERVED) - 1)
    with pytest.raises(ValueError, match="dated after verification"):
        verify_retirement_readiness_observation(
            evidence.native_root,
            evidence.legacy_root,
            evidence.observation,
            policy=evidence.policy,
            credential_scan_policy=evidence.scan_policy,
            expected_key_id=evidence.key_id,
            expected_public_key_sha256=evidence.public_key_sha256,
        )

    monkeypatch.setattr(
        readiness_module,
        "time_ns",
        lambda: evidence.native.authorization_expires_ts_ns,
    )
    with pytest.raises(ValueError, match="authorization expired during"):
        verify_retirement_readiness_observation(
            evidence.native_root,
            evidence.legacy_root,
            evidence.observation,
            policy=evidence.policy,
            credential_scan_policy=evidence.scan_policy,
            expected_key_id=evidence.key_id,
            expected_public_key_sha256=evidence.public_key_sha256,
        )

    monkeypatch.setattr(
        readiness_module,
        "time_ns",
        lambda: (
            evidence.final_state.captured_ts_ns + evidence.policy.maximum_final_state_age_ns + 1
        ),
    )
    with pytest.raises(ValueError, match="stale after readiness replay"):
        verify_retirement_readiness_observation(
            evidence.native_root,
            evidence.legacy_root,
            evidence.observation,
            policy=evidence.policy,
            credential_scan_policy=evidence.scan_policy,
            expected_key_id=evidence.key_id,
            expected_public_key_sha256=evidence.public_key_sha256,
        )

    path = (tmp_path / "noncanonical-readiness.json").resolve()
    path.write_bytes(evidence.observation.canonical_bytes())
    with pytest.raises(ValueError, match="not canonical"):
        load_retirement_readiness_observation(path)

    canonical = (tmp_path / "canonical-readiness.json").resolve()
    canonical.write_bytes(evidence.observation.canonical_bytes() + b"\n")
    symlink = tmp_path / "readiness-link.json"
    symlink.symlink_to(canonical)
    with pytest.raises(ValueError, match="cannot open"):
        load_retirement_readiness_observation(symlink)
