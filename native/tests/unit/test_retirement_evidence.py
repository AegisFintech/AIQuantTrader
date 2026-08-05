from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from aiquanttrader_native.domain.base import canonical_sha256
from aiquanttrader_native.retirement.cli import main as retirement_main
from aiquanttrader_native.retirement.evidence import (
    evaluate_disabled_observation,
    evaluate_retirement_readiness,
    load_retirement_policy,
)
from aiquanttrader_native.retirement.models import (
    REQUIRED_ARCHIVE_ARTIFACTS,
    REQUIRED_DISABLED_CAPABILITIES,
    REQUIRED_NATIVE_DRILLS,
    CleanupAction,
    CleanupTargetKind,
    DisabledObservation,
    LegacyArchiveArtifact,
    LegacyArchiveArtifactKind,
    LegacyArchiveManifest,
    LegacyCapability,
    LegacyCapabilityObservation,
    LegacyCleanupManifest,
    LegacyCleanupTarget,
    LegacyFinalState,
    NativeProductionObservation,
    RequiredNativeDrill,
    RetirementActionApproval,
    RetirementActionScope,
    RetirementApprovalSignature,
    RetirementPolicy,
    RetirementReadinessObservation,
)

SHA = "a" * 64
COMMIT = "b" * 40


def _policy() -> RetirementPolicy:
    return RetirementPolicy(
        policy_id="retirement-policy-test",
        frozen_at_ns=10,
        minimum_native_production_observation_ns=100,
        minimum_disabled_observation_ns=100,
        minimum_archive_retention_ns=1_000,
        required_archive_artifacts=tuple(LegacyArchiveArtifactKind),
        required_disabled_capabilities=tuple(LegacyCapability),
        required_native_drills=tuple(RequiredNativeDrill),
    )


def _archive() -> LegacyArchiveManifest:
    artifacts = tuple(
        LegacyArchiveArtifact(
            kind=kind,
            relative_path=f"artifacts/{kind.value}.tar.zst",
            content_sha256=(
                "c" * 64 if kind is LegacyArchiveArtifactKind.FINAL_TRADE_REPORT else "d" * 64
            )
            if kind is not LegacyArchiveArtifactKind.BROKER_ACCOUNT_STATE
            else "e" * 64,
            byte_count=128,
            captured_ts_ns=190,
        )
        for kind in LegacyArchiveArtifactKind
    )
    return LegacyArchiveManifest(
        retirement_id="retirement-test-001",
        created_ts_ns=200,
        retention_expires_ts_ns=2_000,
        source_commit_sha=COMMIT,
        final_tag_commit_sha=COMMIT,
        artifacts=artifacts,
    )


def _readiness() -> RetirementReadinessObservation:
    return RetirementReadinessObservation(
        retirement_id="retirement-test-001",
        observed_ts_ns=400,
        native=NativeProductionObservation(
            deployment_id="native-production-001",
            admission_id="1" * 64,
            terminal_authorization_id="1" * 64,
            renewal_count=0,
            authorization_expires_ts_ns=500,
            production_approval_sha256="2" * 64,
            production_artifact_manifest_sha256="3" * 64,
            started_ts_ns=100,
            ended_ts_ns=300,
            critical_incidents=0,
            reconciliation_failures=0,
            risk_breaches=0,
            completed_drills=tuple(RequiredNativeDrill),
            evidence_bundle_sha256="4" * 64,
        ),
        archive=_archive(),
        legacy=LegacyFinalState(
            captured_ts_ns=350,
            open_managed_positions=0,
            open_unmanaged_positions=0,
            pending_orders=0,
            entry_pause_active=True,
            command_file_writer_count=0,
            final_trade_report_sha256="c" * 64,
            final_status_sha256="f" * 64,
            broker_account_state_sha256="e" * 64,
        ),
    )


def _disabled() -> DisabledObservation:
    return DisabledObservation(
        retirement_id="retirement-test-001",
        readiness_report_sha256="5" * 64,
        stop_approval_sha256="6" * 64,
        archive_manifest_sha256=_archive().sha256(),
        native_deployment_id="native-production-001",
        native_admission_id="1" * 64,
        started_ts_ns=500,
        ended_ts_ns=700,
        native_critical_incidents=0,
        native_reconciliation_failures=0,
        native_risk_breaches=0,
        legacy_broker_orders_after_stop=0,
        archive_reverified=True,
        legacy_credentials_quarantined=True,
        capabilities=tuple(
            LegacyCapabilityObservation(
                capability=capability,
                disabled=True,
                active_instance_count=0,
                evidence_sha256="7" * 64,
            )
            for capability in LegacyCapability
        ),
        evidence_bundle_sha256="8" * 64,
    )


def test_readiness_requires_bound_complete_archive_and_passes_only_to_approval() -> None:
    observation = _readiness()
    report = evaluate_retirement_readiness(
        observation=observation,
        policy=_policy(),
        generated_ts_ns=401,
    )

    assert report.awaiting_stop_approval
    assert all(gate.passed for gate in report.gates)
    assert report.archive_manifest_sha256 == observation.archive.sha256()
    assert report.source_commit_sha == COMMIT
    assert set(observation.archive.artifacts[index].kind for index in range(11)) == (
        REQUIRED_ARCHIVE_ARTIFACTS
    )

    unsafe = observation.model_copy(
        update={"legacy": observation.legacy.model_copy(update={"pending_orders": 1})}
    )
    failed = evaluate_retirement_readiness(
        observation=unsafe,
        policy=_policy(),
        generated_ts_ns=401,
    )
    assert not failed.awaiting_stop_approval
    assert not next(gate for gate in failed.gates if gate.gate.value == "flat_account").passed


def test_archive_rejects_traversal_duplicates_and_unbound_reports() -> None:
    archive = _archive()
    with pytest.raises(ValidationError, match="below artifacts"):
        LegacyArchiveArtifact(
            kind=LegacyArchiveArtifactKind.OPERATIONAL_LOGS,
            relative_path="../logs.tar.zst",
            content_sha256=SHA,
            byte_count=1,
            captured_ts_ns=1,
        )
    with pytest.raises(ValidationError, match="incomplete or duplicated"):
        LegacyArchiveManifest.model_validate(
            {
                **archive.model_dump(),
                "artifacts": (*archive.artifacts[:-1], archive.artifacts[0]),
            }
        )
    with pytest.raises(ValidationError, match="trade report"):
        RetirementReadinessObservation(
            **_readiness().model_dump(exclude={"legacy"}),
            legacy=_readiness().legacy.model_copy(update={"final_trade_report_sha256": SHA}),
        )


def test_disabled_window_requires_every_capability_and_stable_native_operation() -> None:
    observation = _disabled()
    report = evaluate_disabled_observation(
        observation=observation,
        policy=_policy(),
        generated_ts_ns=701,
    )

    assert report.awaiting_cleanup_approval
    assert all(gate.passed for gate in report.gates)
    assert set(item.capability for item in observation.capabilities) == (
        REQUIRED_DISABLED_CAPABILITIES
    )

    active = observation.capabilities[0].model_copy(
        update={"disabled": False, "active_instance_count": 1}
    )
    failed = evaluate_disabled_observation(
        observation=observation.model_copy(
            update={"capabilities": (active, *observation.capabilities[1:])}
        ),
        policy=_policy(),
        generated_ts_ns=701,
    )
    assert not failed.awaiting_cleanup_approval
    failed_names = {gate.gate.value for gate in failed.gates if not gate.passed}
    assert failed_names == {"all_capabilities_disabled", "zero_active_instances"}


def test_policy_loader_and_approval_scope_are_fail_closed(tmp_path: Path) -> None:
    policy_path = tmp_path / "policy.toml"
    policy_path.write_text(
        "\n".join(
            (
                "schema_version = 1",
                'policy_id = "retirement-policy-test"',
                "frozen_at_ns = 10",
                "minimum_native_production_observation_ns = 100",
                "minimum_disabled_observation_ns = 100",
                "minimum_archive_retention_ns = 1000",
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
    assert load_retirement_policy(policy_path) == _policy()
    assert set(_policy().required_native_drills) == REQUIRED_NATIVE_DRILLS

    now = datetime(2026, 8, 5, tzinfo=UTC)
    common = {
        "approval_id": "retirement-approval-001",
        "retirement_id": "retirement-test-001",
        "report_sha256": SHA,
        "native_deployment_id": "native-production-001",
        "native_admission_id": "1" * 64,
        "archive_manifest_sha256": "2" * 64,
        "source_commit_sha": COMMIT,
        "approver": "risk-owner",
        "approved_at": now,
        "expires_at": now + timedelta(hours=1),
    }
    RetirementActionApproval.model_validate(
        {"scope": RetirementActionScope.STOP_AND_OBSERVE, **common}
    )
    with pytest.raises(ValidationError, match="cleanup manifest"):
        RetirementActionApproval.model_validate(
            {"scope": RetirementActionScope.REMOVE_AND_CLEAN, **common}
        )
    with pytest.raises(ValidationError, match="24 hours"):
        RetirementActionApproval.model_validate(
            {
                "scope": RetirementActionScope.STOP_AND_OBSERVE,
                **common,
                "expires_at": now + timedelta(days=2),
            }
        )


def test_cleanup_manifest_rejects_broad_targets_globs_and_wrong_secret_actions() -> None:
    target = LegacyCleanupTarget(
        target_id="legacy-mt5-source",
        kind=CleanupTargetKind.REPOSITORY_PATH,
        locator="broker/mt5",
        action=CleanupAction.REMOVE,
        expected_state_sha256=SHA,
        rationale="MQL5 execution is retired after the disabled observation",
    )
    manifest = LegacyCleanupManifest(
        retirement_id="retirement-test-001",
        created_ts_ns=1,
        source_commit_sha=COMMIT,
        archive_manifest_sha256="1" * 64,
        disabled_observation_report_sha256="2" * 64,
        targets=(target,),
    )
    assert manifest.targets == (target,)

    for locator in ("/", "/root", "/etc", "/tmp", "/var", "/root/AIQuantTrader"):
        with pytest.raises(ValidationError, match="narrow absolute"):
            LegacyCleanupTarget(
                target_id="unsafe-host-path",
                kind=CleanupTargetKind.RUNTIME_PATH,
                locator=locator,
                action=CleanupAction.REMOVE,
                expected_state_sha256=SHA,
                rationale="unsafe test target",
            )
    for locator in ("scripts/mt5_*", "$RUNTIME_ROOT/wineprefix", "scripts;rm"):
        with pytest.raises(ValidationError, match="globs, variables, or shell"):
            LegacyCleanupTarget(
                target_id="unsafe-interpolation",
                kind=CleanupTargetKind.REPOSITORY_PATH,
                locator=locator,
                action=CleanupAction.REMOVE,
                expected_state_sha256=SHA,
                rationale="unsafe test target",
            )
    with pytest.raises(ValidationError, match="revoke"):
        LegacyCleanupTarget(
            target_id="mt5-password",
            kind=CleanupTargetKind.SECRET_REFERENCE,
            locator="MT5_PASSWORD",
            action=CleanupAction.REMOVE,
            expected_state_sha256=SHA,
            rationale="credential must be revoked",
        )


def test_retirement_evidence_cli_writes_reports_and_validates_cleanup(
    tmp_path: Path,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    policy = project_root / "configs" / "retirement" / "evidence-v1.toml"
    readiness_path = tmp_path / "readiness-observation.json"
    readiness_report = tmp_path / "readiness-report.json"
    readiness_path.write_bytes(_readiness().canonical_bytes())
    assert (
        retirement_main(
            [
                "evaluate-readiness",
                "--observation",
                str(readiness_path),
                "--policy",
                str(policy),
                "--output",
                str(readiness_report),
            ]
        )
        == 1
    )
    assert readiness_report.is_file()

    disabled_path = tmp_path / "disabled-observation.json"
    disabled_report = tmp_path / "disabled-report.json"
    disabled_path.write_bytes(_disabled().canonical_bytes())
    assert (
        retirement_main(
            [
                "evaluate-disabled",
                "--observation",
                str(disabled_path),
                "--policy",
                str(policy),
                "--output",
                str(disabled_report),
            ]
        )
        == 1
    )
    assert disabled_report.is_file()

    manifest = LegacyCleanupManifest(
        retirement_id="retirement-test-001",
        created_ts_ns=1,
        source_commit_sha=COMMIT,
        archive_manifest_sha256="1" * 64,
        disabled_observation_report_sha256="2" * 64,
        targets=(
            LegacyCleanupTarget(
                target_id="legacy-source",
                kind=CleanupTargetKind.REPOSITORY_PATH,
                locator="broker/mt5",
                action=CleanupAction.REMOVE,
                expected_state_sha256=SHA,
                rationale="approved legacy source removal",
            ),
        ),
    )
    manifest_path = tmp_path / "cleanup.json"
    canonical_path = tmp_path / "cleanup.canonical.json"
    manifest_path.write_bytes(manifest.canonical_bytes())
    assert (
        retirement_main(
            [
                "validate-cleanup-manifest",
                "--manifest",
                str(manifest_path),
                "--output",
                str(canonical_path),
            ]
        )
        == 0
    )
    assert canonical_path.read_bytes() == manifest.canonical_bytes() + b"\n"

    manifest_path.write_text("{}", encoding="utf-8")
    assert retirement_main(["validate-cleanup-manifest", "--manifest", str(manifest_path)]) == 2
    assert '"status": "error"' in capsys.readouterr().err


def test_retirement_contracts_reject_inconsistent_intervals_inventories_and_reports() -> None:
    archive = _archive()
    for archive_update, message in (
        ({"retention_expires_ts_ns": archive.created_ts_ns}, "retention"),
        ({"final_tag_commit_sha": "0" * 40}, "archived source commit"),
        (
            {
                "artifacts": (
                    *archive.artifacts[:-1],
                    archive.artifacts[-1].model_copy(
                        update={"relative_path": archive.artifacts[0].relative_path}
                    ),
                )
            },
            "paths must be unique",
        ),
        (
            {
                "artifacts": (
                    archive.artifacts[0].model_copy(
                        update={"captured_ts_ns": archive.created_ts_ns + 1}
                    ),
                    *archive.artifacts[1:],
                )
            },
            "captured after",
        ),
    ):
        with pytest.raises(ValidationError, match=message):
            LegacyArchiveManifest.model_validate({**archive.model_dump(), **archive_update})

    native = _readiness().native
    with pytest.raises(ValidationError, match="positive interval"):
        NativeProductionObservation.model_validate(
            {**native.model_dump(), "ended_ts_ns": native.started_ts_ns}
        )
    with pytest.raises(ValidationError, match="remain active"):
        NativeProductionObservation.model_validate(
            {**native.model_dump(), "authorization_expires_ts_ns": native.ended_ts_ns}
        )
    with pytest.raises(ValidationError, match="terminal renewal"):
        NativeProductionObservation.model_validate({**native.model_dump(), "renewal_count": 1})
    with pytest.raises(ValidationError, match="every retirement drill"):
        NativeProductionObservation.model_validate(
            {
                **native.model_dump(),
                "completed_drills": (
                    *tuple(RequiredNativeDrill)[:-1],
                    RequiredNativeDrill.NATIVE_ROLLBACK,
                ),
            }
        )

    readiness = _readiness()
    invalid_readiness = (
        (
            {"archive": readiness.archive.model_copy(update={"retirement_id": "different"})},
            "archive identities",
        ),
        ({"observed_ts_ns": readiness.native.ended_ts_ns - 1}, "native observation"),
        (
            {
                "archive": readiness.archive.model_copy(
                    update={"created_ts_ns": readiness.observed_ts_ns + 1}
                )
            },
            "archive creation",
        ),
        (
            {
                "legacy": readiness.legacy.model_copy(
                    update={"captured_ts_ns": readiness.observed_ts_ns + 1}
                )
            },
            "final legacy state",
        ),
        (
            {
                "legacy": readiness.legacy.model_copy(
                    update={"broker_account_state_sha256": "0" * 64}
                )
            },
            "broker account state",
        ),
    )
    for readiness_update, message in invalid_readiness:
        with pytest.raises(ValidationError, match=message):
            RetirementReadinessObservation.model_validate(
                {**readiness.model_dump(), **readiness_update}
            )

    policy = _policy()
    policy_fields = (
        ("required_archive_artifacts", tuple(LegacyArchiveArtifactKind)),
        ("required_disabled_capabilities", tuple(LegacyCapability)),
        ("required_native_drills", tuple(RequiredNativeDrill)),
    )
    for field, values in policy_fields:
        duplicated = (*values[:-1], values[0])
        with pytest.raises(ValidationError, match="must require"):
            RetirementPolicy.model_validate({**policy.model_dump(), field: duplicated})

    readiness_report = evaluate_retirement_readiness(
        observation=readiness,
        policy=policy,
        generated_ts_ns=401,
    )
    with pytest.raises(ValidationError, match="identity"):
        type(readiness_report).model_validate(
            {**readiness_report.model_dump(), "report_id": "0" * 64}
        )
    readiness_gates = {
        **readiness_report.model_dump(mode="json"),
        "gates": [gate.model_dump(mode="json") for gate in readiness_report.gates[:-1]],
    }
    readiness_identity = {
        key: value
        for key, value in readiness_gates.items()
        if key not in {"report_id", "awaiting_stop_approval"}
    }
    readiness_gates["report_id"] = canonical_sha256(readiness_identity)
    with pytest.raises(ValidationError, match="every gate"):
        type(readiness_report).model_validate(readiness_gates)
    with pytest.raises(ValidationError, match="verdict"):
        type(readiness_report).model_validate(
            {**readiness_report.model_dump(), "awaiting_stop_approval": False}
        )

    disabled = _disabled()
    with pytest.raises(ValidationError, match="positive interval"):
        DisabledObservation.model_validate(
            {**disabled.model_dump(), "ended_ts_ns": disabled.started_ts_ns}
        )
    with pytest.raises(ValidationError, match="every legacy capability"):
        DisabledObservation.model_validate(
            {
                **disabled.model_dump(),
                "capabilities": (*disabled.capabilities[:-1], disabled.capabilities[0]),
            }
        )
    disabled_report = evaluate_disabled_observation(
        observation=disabled,
        policy=policy,
        generated_ts_ns=701,
    )
    with pytest.raises(ValidationError, match="identity"):
        type(disabled_report).model_validate(
            {**disabled_report.model_dump(), "report_id": "0" * 64}
        )
    disabled_gates = {
        **disabled_report.model_dump(mode="json"),
        "gates": [gate.model_dump(mode="json") for gate in disabled_report.gates[:-1]],
    }
    disabled_identity = {
        key: value
        for key, value in disabled_gates.items()
        if key not in {"report_id", "awaiting_cleanup_approval"}
    }
    disabled_gates["report_id"] = canonical_sha256(disabled_identity)
    with pytest.raises(ValidationError, match="every gate"):
        type(disabled_report).model_validate(disabled_gates)
    with pytest.raises(ValidationError, match="verdict"):
        type(disabled_report).model_validate(
            {**disabled_report.model_dump(), "awaiting_cleanup_approval": False}
        )


def test_cleanup_approval_and_signature_edge_contracts_are_rejected() -> None:
    with pytest.raises(ValidationError, match="traverse"):
        LegacyCleanupTarget(
            target_id="traversal",
            kind=CleanupTargetKind.REPOSITORY_PATH,
            locator="scripts/../AGENTS.md",
            action=CleanupAction.REMOVE,
            expected_state_sha256=SHA,
            rationale="invalid traversal",
        )
    with pytest.raises(ValidationError, match="relative paths"):
        LegacyCleanupTarget(
            target_id="absolute-repository",
            kind=CleanupTargetKind.REPOSITORY_PATH,
            locator="/root/AIQuantTrader/broker/mt5",
            action=CleanupAction.REMOVE,
            expected_state_sha256=SHA,
            rationale="invalid absolute repository path",
        )
    with pytest.raises(ValidationError, match="single identifiers"):
        LegacyCleanupTarget(
            target_id="bad-package",
            kind=CleanupTargetKind.HOST_PACKAGE,
            locator="wine package",
            action=CleanupAction.REMOVE,
            expected_state_sha256=SHA,
            rationale="invalid package identifier",
        )
    with pytest.raises(ValidationError, match="only use the remove"):
        LegacyCleanupTarget(
            target_id="bad-package-action",
            kind=CleanupTargetKind.HOST_PACKAGE,
            locator="wine",
            action=CleanupAction.REVOKE,
            expected_state_sha256=SHA,
            rationale="invalid package action",
        )

    target = LegacyCleanupTarget(
        target_id="legacy-source",
        kind=CleanupTargetKind.REPOSITORY_PATH,
        locator="broker/mt5",
        action=CleanupAction.REMOVE,
        expected_state_sha256=SHA,
        rationale="retired source",
    )
    manifest_payload = {
        "retirement_id": "retirement-test-001",
        "created_ts_ns": 1,
        "source_commit_sha": COMMIT,
        "archive_manifest_sha256": "1" * 64,
        "disabled_observation_report_sha256": "2" * 64,
    }
    with pytest.raises(ValidationError, match="identities must be unique"):
        LegacyCleanupManifest.model_validate(
            {
                **manifest_payload,
                "targets": (target, target.model_copy(update={"locator": "scripts"})),
            }
        )
    with pytest.raises(ValidationError, match="locators must be unique"):
        LegacyCleanupManifest.model_validate(
            {
                **manifest_payload,
                "targets": (target, target.model_copy(update={"target_id": "another"})),
            }
        )

    now = datetime(2026, 8, 5, tzinfo=UTC)
    approval_payload = {
        "approval_id": "approval-edge",
        "retirement_id": "retirement-test-001",
        "scope": RetirementActionScope.STOP_AND_OBSERVE,
        "report_sha256": "1" * 64,
        "native_deployment_id": "native-production-001",
        "native_admission_id": "2" * 64,
        "archive_manifest_sha256": "3" * 64,
        "source_commit_sha": COMMIT,
        "approver": "risk-owner",
        "approved_at": now,
        "expires_at": now + timedelta(hours=1),
    }
    with pytest.raises(ValidationError, match="timezone-aware"):
        RetirementActionApproval.model_validate(
            {
                **approval_payload,
                "approved_at": now.replace(tzinfo=None),
                "expires_at": (now + timedelta(hours=1)).replace(tzinfo=None),
            }
        )
    with pytest.raises(ValidationError, match="follow approval"):
        RetirementActionApproval.model_validate({**approval_payload, "expires_at": now})
    with pytest.raises(ValidationError, match="cannot authorize"):
        RetirementActionApproval.model_validate(
            {**approval_payload, "cleanup_manifest_sha256": "4" * 64}
        )
    approval = RetirementActionApproval.model_validate(approval_payload)
    with pytest.raises(ValueError, match="timezone-aware"):
        approval.is_active(now.replace(tzinfo=None))

    with pytest.raises(ValidationError, match="canonical base64"):
        RetirementApprovalSignature(
            key_id="key",
            approval_sha256="1" * 64,
            signature_base64="!" * 88,
        )
    with pytest.raises(ValidationError, match="one canonical"):
        RetirementApprovalSignature(
            key_id="key",
            approval_sha256="1" * 64,
            signature_base64=base64.b64encode(b"x" * 65).decode("ascii"),
        )
