from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_core import to_jsonable_python

import aiquanttrader_native.governance.bundle as release_bundle_module
from aiquanttrader_native.backtest.models import CalibrationState
from aiquanttrader_native.config import load_config
from aiquanttrader_native.config.models import RiskLimits
from aiquanttrader_native.domain.base import canonical_sha256
from aiquanttrader_native.domain.data import DatasetManifest
from aiquanttrader_native.domain.governance import DeploymentApproval, PromotionStage
from aiquanttrader_native.features.models import MODEL_FEATURE_SCHEMA
from aiquanttrader_native.governance.bundle import (
    load_release_bundle_spec,
    prepare_release_bundle,
    release_behavior_configuration,
)
from aiquanttrader_native.governance.cli import main as governance_main
from aiquanttrader_native.governance.evidence import (
    evaluate_testnet_evidence,
    load_testnet_policy,
)
from aiquanttrader_native.governance.models import (
    CanaryEvidenceReport,
    CanaryGateResult,
    DeploymentArtifactManifest,
    DeploymentModelSelection,
    ReleaseArtifactSourcePaths,
    ReleaseBundleReceipt,
    ReleaseBundleSpec,
)
from aiquanttrader_native.governance.models import (
    TestnetDressRehearsalObservation as DressRehearsalObservation,
)
from aiquanttrader_native.governance.models import (
    TestnetDressRehearsalPolicy as DressRehearsalPolicy,
)
from aiquanttrader_native.governance.models import (
    TestnetLifecycleScenario as LifecycleScenario,
)
from aiquanttrader_native.governance.models import (
    TestnetScenarioResult as ScenarioResult,
)
from aiquanttrader_native.risk.authority import limits_sha
from aiquanttrader_native.shadow.models import ShadowEvidenceReport, ShadowGateResult

NOW = datetime(2026, 8, 4, 12, tzinfo=UTC)
COMMIT = "b" * 40
IMAGE = "sha256:" + "c" * 64
TESTNET_ACCOUNT = "0x" + "1" * 40
TESTNET_TRADING = "0x" + "2" * 40
TESTNET_CONTROL = "0x" + "3" * 40
MAINNET_ACCOUNT = "0x" + "4" * 40
MAINNET_TRADING = "0x" + "5" * 40
MAINNET_CONTROL = "0x" + "6" * 40


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _risk() -> RiskLimits:
    return RiskLimits(
        max_leverage=Decimal("1"),
        max_order_size_base=Decimal("0.002"),
        max_position_size_base=Decimal("0.01"),
        max_order_notional_usd=Decimal("100"),
        max_inventory_notional_usd=Decimal("500"),
        max_open_orders=2,
        max_orders_per_second=2,
    )


def _policy() -> DressRehearsalPolicy:
    return DressRehearsalPolicy(
        policy_id="testnet-v1",
        frozen_at_ns=1,
        minimum_observation_ns=10,
        minimum_orders=15,
        minimum_fills=4,
        minimum_cancel_all_confirmations=2,
        minimum_deadman_cancellations=1,
        required_scenarios=tuple(LifecycleScenario),
    )


def _observation(**updates: object) -> DressRehearsalObservation:
    values: dict[str, object] = {
        "rehearsal_id": "rehearsal-001",
        "started_ts_ns": 10,
        "ended_ts_ns": 30,
        "commit_sha": COMMIT,
        "image_digest": IMAGE,
        "dependency_lock_sha256": "1" * 64,
        "dataset_sha256": "2" * 64,
        "model_sha256": "3" * 64,
        "feature_schema_sha256": "4" * 64,
        "strategy_config_sha256": "5" * 64,
        "risk_policy_sha256": "6" * 64,
        "target_configuration_sha256": "7" * 64,
        "account_address": TESTNET_ACCOUNT,
        "trading_wallet_address": TESTNET_TRADING,
        "control_wallet_address": TESTNET_CONTROL,
        "orders": 15,
        "fills": 4,
        "unknown_outcomes": 1,
        "resolved_unknown_outcomes": 1,
        "reconciliation_failures": 0,
        "duplicate_venue_orders": 0,
        "risk_breaches": 0,
        "cancel_all_confirmations": 2,
        "deadman_cancellations": 1,
        "ending_position_base": Decimal("0"),
        "ending_open_orders": 0,
        "scenarios": tuple(
            ScenarioResult(
                scenario=scenario,
                passed=True,
                evidence_sha256=f"{index:x}" * 64,
            )
            for index, scenario in enumerate(LifecycleScenario, start=1)
        ),
        "evidence_bundle_sha256": "e" * 64,
    }
    values.update(updates)
    return DressRehearsalObservation.model_validate(values)


def _shadow_report(
    *,
    feature_sha256: str,
    strategy_sha256: str,
    passed: bool = True,
) -> ShadowEvidenceReport:
    gate_names = (
        "observation",
        "independent_decisions",
        "fills",
        "regimes",
        "availability",
        "ingress_latency_p99_ms",
        "cycle_latency_p99_ms",
        "operational_sample_completeness",
        "command_completeness",
        "calibrated_scenario",
        "positive_post_cost_pnl",
        "drawdown",
        "denial_fraction",
        "markout_coverage",
        "adverse_markout",
        "drift_evaluated",
        "feature_psi",
        "feature_mean_shift",
        "determinism",
        "sensitivity",
        "drills",
        "run_integrity",
        "flat_final_state",
    )
    gates = tuple(
        ShadowGateResult(
            gate=name,
            passed=passed if index == 0 else True,
            actual=str(passed if index == 0 else True).lower(),
            required="true",
        )
        for index, name in enumerate(gate_names)
    )
    payload = {
        "schema_version": 1,
        "run_id": "shadow-release-001",
        "run_manifest_sha256": "a" * 64,
        "generated_ts_ns": 10,
        "policy_id": "shadow-v1",
        "policy_sha256": "b" * 64,
        "image_identity": IMAGE,
        "code_identity": COMMIT,
        "config_fingerprint": "c" * 64,
        "feature_config_sha256": feature_sha256,
        "strategy_config_sha256": strategy_sha256,
        "engine_policy_sha256": "e" * 64,
        "scenario_id": "calibrated-baseline",
        "scenario_sha256": "f" * 64,
        "calibration_state": CalibrationState.CALIBRATED,
        "observation_ns": 100,
        "independent_decisions": 100,
        "approved_decisions": 50,
        "denied_decisions": 50,
        "commands": 50,
        "submit_commands": 50,
        "fills": 20,
        "markouts": 20,
        "regimes": (),
        "availability_fraction": Decimal("1"),
        "ingress_latency_p99_ms": Decimal("1"),
        "cycle_latency_p99_ms": Decimal("1"),
        "post_cost_pnl_usd": Decimal("1"),
        "maximum_drawdown_fraction": Decimal("0.001"),
        "mean_adverse_markout_bps": Decimal("0"),
        "maximum_feature_psi": Decimal("0.01"),
        "maximum_standardized_mean_shift": Decimal("0.01"),
        "determinism_report_id": "9" * 64,
        "sensitivity_scenarios": ("pessimistic",),
        "completed_drills": ("host_reboot",),
        "invalidating_events": (),
        "gates": gates,
    }
    return ShadowEvidenceReport.model_validate(
        {
            **payload,
            "report_id": canonical_sha256(to_jsonable_python(payload)),
            "awaiting_human_approval": passed,
        }
    )


def _canary_report(*, deployment_id: str, passed: bool = True) -> CanaryEvidenceReport:
    gate_names = (
        "observation",
        "orders",
        "fills",
        "maker_fills",
        "rejection_fraction",
        "unknown_outcomes",
        "reconciliation_failures",
        "fee_attribution",
        "funding_attribution",
        "positive_post_cost_pnl",
        "drawdown",
        "adverse_markout",
        "capital",
        "drills",
    )
    gates = tuple(
        CanaryGateResult(
            gate=name,
            passed=passed if index == 0 else True,
            actual=str(passed if index == 0 else True).lower(),
            required="true",
        )
        for index, name in enumerate(gate_names)
    )
    identity = {
        "schema_version": 1,
        "deployment_id": deployment_id,
        "admission_id": "7" * 64,
        "policy_id": "canary-v1",
        "policy_sha256": "8" * 64,
        "observation_sha256": "9" * 64,
        "generated_ts_ns": 50,
        "gates": gates,
    }
    return CanaryEvidenceReport.model_validate(
        {
            **identity,
            "report_id": canonical_sha256(to_jsonable_python(identity)),
            "awaiting_production_approval": passed,
        }
    )


def test_testnet_evidence_stops_at_canary_approval_boundary() -> None:
    observation = _observation()
    report = evaluate_testnet_evidence(
        observation=observation,
        policy=_policy(),
        generated_ts_ns=40,
    )

    assert report.awaiting_canary_approval
    assert report.observation_sha256 == observation.sha256()
    assert all(gate.passed for gate in report.gates)

    failed = evaluate_testnet_evidence(
        observation=_observation(
            resolved_unknown_outcomes=0,
            ending_open_orders=1,
            risk_breaches=1,
            scenarios=tuple(
                result.model_copy(update={"passed": False})
                if result.scenario is LifecycleScenario.PARTIAL_FILL_CANCEL
                else result
                for result in observation.scenarios
            ),
        ),
        policy=_policy(),
        generated_ts_ns=40,
    )
    assert not failed.awaiting_canary_approval
    assert {gate.gate for gate in failed.gates if not gate.passed} >= {
        "unknown_outcomes_resolved",
        "risk_breaches",
        "flat_final_state",
        "scenario_results",
    }


def test_testnet_contracts_reject_incomplete_or_ambiguous_evidence() -> None:
    with pytest.raises(ValidationError, match=r"at least 15|complete lifecycle matrix"):
        _policy().model_copy(
            update={"required_scenarios": tuple(LifecycleScenario)[:-1]}
        ).__class__.model_validate(
            {
                **_policy().model_dump(mode="json"),
                "required_scenarios": [item.value for item in tuple(LifecycleScenario)[:-1]],
            }
        )
    with pytest.raises(ValidationError, match="positive interval"):
        _observation(ended_ts_ns=10)
    with pytest.raises(ValidationError, match="fills cannot exceed"):
        _observation(fills=16)
    with pytest.raises(ValidationError, match="resolved unknown"):
        _observation(resolved_unknown_outcomes=2)
    with pytest.raises(ValidationError, match="wallets must differ"):
        _observation(control_wallet_address=TESTNET_TRADING)
    with pytest.raises(ValidationError, match="identities must be distinct"):
        _observation(trading_wallet_address=TESTNET_ACCOUNT)
    with pytest.raises(ValidationError, match="scenario results must be unique"):
        original = _observation().scenarios
        _observation(scenarios=(*original, original[0]))
    valid_report = evaluate_testnet_evidence(
        observation=_observation(),
        policy=_policy(),
        generated_ts_ns=40,
    )
    incomplete_report = valid_report.model_dump(mode="json")
    incomplete_report["gates"] = incomplete_report["gates"][:1]
    report_identity = {
        key: value
        for key, value in incomplete_report.items()
        if key not in {"report_id", "awaiting_canary_approval"}
    }
    incomplete_report["report_id"] = canonical_sha256(report_identity)
    with pytest.raises(ValidationError, match="complete gate set"):
        valid_report.__class__.model_validate(incomplete_report)


def test_release_contracts_reject_long_lived_or_ambiguous_authority(
    tmp_path: Path,
    project_root: Path,
    config_dir: Path,
) -> None:
    spec, _testnet_path = _release_inputs(tmp_path, project_root, config_dir)
    with pytest.raises(ValidationError, match="more than seven days"):
        ReleaseBundleSpec.model_validate(
            {
                **spec.model_dump(mode="python"),
                "expires_at": NOW + timedelta(days=8),
            }
        )
    with pytest.raises(ValidationError, match="vault and account"):
        ReleaseBundleSpec.model_validate(
            {
                **spec.model_dump(mode="python"),
                "vault_address": spec.account_address,
            }
        )
    with pytest.raises(ValidationError, match="identities must be distinct"):
        ReleaseBundleSpec.model_validate(
            {
                **spec.model_dump(mode="python"),
                "trading_wallet_address": spec.account_address,
            }
        )


def _release_inputs(
    tmp_path: Path,
    project_root: Path,
    config_dir: Path,
) -> tuple[ReleaseBundleSpec, Path]:
    dataset = DatasetManifest(
        dataset_id="a" * 64,
        normalized_manifest_sha256s=("b" * 64,),
        policy_sha256="c" * 64,
        gaps=(),
        created_at=NOW - timedelta(days=1),
    )
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_bytes(dataset.canonical_bytes())
    feature_path = tmp_path / "feature-schema.json"
    feature_path.write_bytes(MODEL_FEATURE_SCHEMA.canonical_bytes())
    model = DeploymentModelSelection(
        selection="none",
        strategy_id="order-flow-scalper-v1",
        feature_schema_sha256=MODEL_FEATURE_SCHEMA.sha256(),
    )
    model_path = tmp_path / "model-selection.json"
    model_path.write_bytes(model.canonical_bytes())
    strategy_path = config_dir / "strategies" / "order-flow-scalper-v1.toml"
    shadow_path = tmp_path / "shadow-evidence.json"
    feature_config_path = config_dir / "features" / "microstructure-v1.toml"
    shadow_path.write_bytes(
        _shadow_report(
            feature_sha256=_sha(feature_config_path.read_bytes()),
            strategy_sha256=_sha(strategy_path.read_bytes()),
        ).canonical_bytes()
    )
    testnet_path = tmp_path / "testnet-evidence.json"
    spec = ReleaseBundleSpec(
        deployment_id="canary-release-001",
        approval_id="canary-approval-001",
        stage=PromotionStage.APPROVED_CANARY,
        rollback_deployment_id="halted-release-000",
        commit_sha=COMMIT,
        image_digest=IMAGE,
        account_address=MAINNET_ACCOUNT,
        trading_wallet_address=MAINNET_TRADING,
        control_wallet_address=MAINNET_CONTROL,
        capital_limit_usd=Decimal("1000"),
        approver="risk-owner@example.invalid",
        approved_at=NOW,
        expires_at=NOW + timedelta(days=1),
        risk=_risk(),
        artifacts=ReleaseArtifactSourcePaths(
            dependency_lock=(project_root / "uv.lock").resolve(),
            dataset_manifest=dataset_path.resolve(),
            model_manifest=model_path.resolve(),
            feature_schema=feature_path.resolve(),
            strategy_config=strategy_path.resolve(),
            shadow_evidence=shadow_path.resolve(),
            testnet_evidence=testnet_path.resolve(),
        ),
    )
    bundle = load_config(config_dir, "canary", environ={})
    settings, behavior_payload, behavior_sha = release_behavior_configuration(bundle, spec)
    assert settings.execution.enabled
    assert settings.live_strategy.enabled
    assert settings.live_strategy.strategy_id == "order-flow-scalper-v1"
    observation = _observation(
        commit_sha=COMMIT,
        image_digest=IMAGE,
        dependency_lock_sha256=_sha((project_root / "uv.lock").read_bytes()),
        dataset_sha256=_sha(dataset_path.read_bytes()),
        model_sha256=_sha(model_path.read_bytes()),
        feature_schema_sha256=_sha(feature_path.read_bytes()),
        strategy_config_sha256=_sha(strategy_path.read_bytes()),
        risk_policy_sha256=limits_sha(spec.risk),
        target_configuration_sha256=behavior_sha,
    )
    testnet_path.write_bytes(
        evaluate_testnet_evidence(
            observation=observation,
            policy=_policy(),
            generated_ts_ns=40,
        ).canonical_bytes()
    )
    assert _sha(behavior_payload) == behavior_sha
    return spec, testnet_path


def test_release_bundle_is_atomic_unsigned_and_exact(
    tmp_path: Path,
    project_root: Path,
    config_dir: Path,
) -> None:
    spec, _testnet_path = _release_inputs(tmp_path, project_root, config_dir)
    bundle = load_config(config_dir, "canary", environ={})
    output = (tmp_path / "release-bundle").resolve()

    receipt = prepare_release_bundle(bundle=bundle, spec=spec, output_dir=output)

    assert receipt.awaiting_offline_signature
    assert len(receipt.files) == 11
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    for file in receipt.files:
        path = output / file.relative_path
        assert path.is_file()
        assert _sha(path.read_bytes()) == file.content_sha256
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    manifest = DeploymentArtifactManifest.model_validate_json(
        (output / "artifact-manifest.json").read_bytes()
    )
    approval = DeploymentApproval.model_validate_json(
        (output / "deployment-approval.unsigned.json").read_bytes()
    )
    persisted_receipt = ReleaseBundleReceipt.model_validate_json(
        (output / "release-bundle-receipt.json").read_bytes()
    )
    assert manifest.sha256() == approval.artifact_manifest_sha256
    assert persisted_receipt == receipt
    assert not (output / "deployment-approval.sig.json").exists()
    with pytest.raises(ValidationError, match="identities must be distinct"):
        DeploymentApproval.model_validate(
            {
                **approval.model_dump(mode="python"),
                "trading_wallet_address": approval.account_address,
            }
        )

    with pytest.raises(ValueError, match="already exists"):
        prepare_release_bundle(bundle=bundle, spec=spec, output_dir=output)


def test_production_bundle_requires_and_binds_passed_canary_evidence(
    tmp_path: Path,
    project_root: Path,
    config_dir: Path,
) -> None:
    canary_spec, testnet_path = _release_inputs(tmp_path, project_root, config_dir)
    canary_path = tmp_path / "canary-evidence.json"
    canary_path.write_bytes(
        _canary_report(deployment_id=canary_spec.deployment_id).canonical_bytes()
    )
    production_spec = ReleaseBundleSpec.model_validate(
        {
            **canary_spec.model_dump(mode="python", exclude={"artifacts"}),
            "deployment_id": "production-release-001",
            "approval_id": "production-approval-001",
            "stage": PromotionStage.PRODUCTION,
            "rollback_deployment_id": canary_spec.deployment_id,
            "prior_approval_id": canary_spec.approval_id,
            "artifacts": {
                **canary_spec.artifacts.model_dump(mode="python"),
                "canary_evidence": canary_path.resolve(),
            },
        }
    )
    bundle = load_config(config_dir, "production", environ={})
    _settings, _behavior_payload, behavior_sha = release_behavior_configuration(
        bundle, production_spec
    )
    sources = production_spec.artifacts
    observation = _observation(
        dependency_lock_sha256=_sha(sources.dependency_lock.read_bytes()),
        dataset_sha256=_sha(sources.dataset_manifest.read_bytes()),
        model_sha256=_sha(sources.model_manifest.read_bytes()),
        feature_schema_sha256=_sha(sources.feature_schema.read_bytes()),
        strategy_config_sha256=_sha(sources.strategy_config.read_bytes()),
        risk_policy_sha256=limits_sha(production_spec.risk),
        target_configuration_sha256=behavior_sha,
    )
    testnet_path.write_bytes(
        evaluate_testnet_evidence(
            observation=observation,
            policy=_policy(),
            generated_ts_ns=40,
        ).canonical_bytes()
    )

    output = (tmp_path / "production-release-bundle").resolve()
    receipt = prepare_release_bundle(
        bundle=bundle,
        spec=production_spec,
        output_dir=output,
    )
    approval = DeploymentApproval.model_validate_json(
        (output / "deployment-approval.unsigned.json").read_bytes()
    )

    assert receipt.stage is PromotionStage.PRODUCTION
    assert len(receipt.files) == 12
    assert approval.prior_approval_id == canary_spec.approval_id
    assert approval.canary_evidence_sha256 == _sha(canary_path.read_bytes())

    incomplete_canary_path = tmp_path / "incomplete-canary-evidence.json"
    incomplete_canary = _canary_report(deployment_id=canary_spec.deployment_id).model_dump(
        mode="json"
    )
    incomplete_canary["gates"] = incomplete_canary["gates"][:1]
    incomplete_identity = {
        key: value
        for key, value in incomplete_canary.items()
        if key not in {"report_id", "awaiting_production_approval"}
    }
    incomplete_canary["report_id"] = canonical_sha256(incomplete_identity)
    incomplete_canary_path.write_bytes(
        CanaryEvidenceReport.model_validate(incomplete_canary).canonical_bytes()
    )
    incomplete_spec = production_spec.model_copy(
        update={
            "artifacts": production_spec.artifacts.model_copy(
                update={"canary_evidence": incomplete_canary_path.resolve()}
            )
        }
    )
    with pytest.raises(ValueError, match="canary evidence gate set"):
        prepare_release_bundle(
            bundle=bundle,
            spec=incomplete_spec,
            output_dir=(tmp_path / "incomplete-production").resolve(),
        )

    failed_canary = tmp_path / "failed-canary-evidence.json"
    failed_canary.write_bytes(
        _canary_report(
            deployment_id=canary_spec.deployment_id,
            passed=False,
        ).canonical_bytes()
    )
    failed_spec = production_spec.model_copy(
        update={
            "artifacts": production_spec.artifacts.model_copy(
                update={"canary_evidence": failed_canary.resolve()}
            )
        }
    )
    with pytest.raises(ValueError, match="canary evidence has not reached"):
        prepare_release_bundle(
            bundle=bundle,
            spec=failed_spec,
            output_dir=(tmp_path / "failed-production").resolve(),
        )


def test_release_bundle_rejects_mismatched_or_unpassed_evidence(
    tmp_path: Path,
    project_root: Path,
    config_dir: Path,
) -> None:
    spec, testnet_path = _release_inputs(tmp_path, project_root, config_dir)
    bundle = load_config(config_dir, "canary", environ={})
    report = json.loads(testnet_path.read_text(encoding="utf-8"))
    report["commit_sha"] = "a" * 40
    identity = {
        key: value
        for key, value in report.items()
        if key not in {"report_id", "awaiting_canary_approval"}
    }
    report["report_id"] = canonical_sha256(identity)
    testnet_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(ValueError, match="testnet evidence mismatch: commit_sha"):
        prepare_release_bundle(
            bundle=bundle,
            spec=spec,
            output_dir=(tmp_path / "bad-release").resolve(),
        )
    assert not (tmp_path / "bad-release").exists()


def test_release_bundle_rejects_bad_sources_semantics_and_limits(
    tmp_path: Path,
    project_root: Path,
    config_dir: Path,
) -> None:
    spec, _testnet_path = _release_inputs(tmp_path, project_root, config_dir)
    bundle = load_config(config_dir, "canary", environ={})

    with pytest.raises(ValueError, match="must be absolute"):
        prepare_release_bundle(bundle=bundle, spec=spec, output_dir=Path("relative"))
    production = load_config(config_dir, "production", environ={})
    with pytest.raises(ValueError, match="stage does not match"):
        release_behavior_configuration(production, spec)

    invalid_spec = tmp_path / "invalid-release.toml"
    invalid_spec.write_bytes(b"\xffnot-toml")
    with pytest.raises(ValueError, match="not valid UTF-8 TOML"):
        load_release_bundle_spec(invalid_spec)

    missing_spec = spec.model_copy(
        update={
            "artifacts": spec.artifacts.model_copy(
                update={"dataset_manifest": (tmp_path / "missing.json").resolve()}
            )
        }
    )
    with pytest.raises(ValueError, match="cannot open release artifact"):
        prepare_release_bundle(
            bundle=bundle,
            spec=missing_spec,
            output_dir=(tmp_path / "missing-source").resolve(),
        )

    directory_spec = spec.model_copy(
        update={
            "artifacts": spec.artifacts.model_copy(update={"dataset_manifest": tmp_path.resolve()})
        }
    )
    with pytest.raises(ValueError, match="is not regular"):
        prepare_release_bundle(
            bundle=bundle,
            spec=directory_spec,
            output_dir=(tmp_path / "directory-source").resolve(),
        )

    empty_path = (tmp_path / "empty.json").resolve()
    empty_path.touch()
    empty_spec = spec.model_copy(
        update={"artifacts": spec.artifacts.model_copy(update={"dataset_manifest": empty_path})}
    )
    with pytest.raises(ValueError, match="size is invalid"):
        prepare_release_bundle(
            bundle=bundle,
            spec=empty_spec,
            output_dir=(tmp_path / "empty-source").resolve(),
        )

    mismatched_model = DeploymentModelSelection(
        selection="none",
        strategy_id="avellaneda-stoikov-v1",
        feature_schema_sha256=MODEL_FEATURE_SCHEMA.sha256(),
    )
    mismatched_model_path = (tmp_path / "mismatched-model.json").resolve()
    mismatched_model_path.write_bytes(mismatched_model.canonical_bytes())
    mismatched_spec = spec.model_copy(
        update={
            "artifacts": spec.artifacts.model_copy(update={"model_manifest": mismatched_model_path})
        }
    )
    with pytest.raises(ValueError, match="strategy and model selection"):
        prepare_release_bundle(
            bundle=bundle,
            spec=mismatched_spec,
            output_dir=(tmp_path / "mismatched-model").resolve(),
        )

    feature_mismatch = DeploymentModelSelection(
        selection="none",
        strategy_id="order-flow-scalper-v1",
        feature_schema_sha256="f" * 64,
    )
    feature_mismatch_path = (tmp_path / "feature-mismatch-model.json").resolve()
    feature_mismatch_path.write_bytes(feature_mismatch.canonical_bytes())
    feature_mismatch_spec = spec.model_copy(
        update={
            "artifacts": spec.artifacts.model_copy(update={"model_manifest": feature_mismatch_path})
        }
    )
    with pytest.raises(ValueError, match="feature schema and model selection"):
        prepare_release_bundle(
            bundle=bundle,
            spec=feature_mismatch_spec,
            output_dir=(tmp_path / "feature-mismatch").resolve(),
        )

    feature_evidence_mismatch_path = (tmp_path / "feature-evidence-mismatch.json").resolve()
    feature_evidence_mismatch_path.write_bytes(
        _shadow_report(
            feature_sha256="d" * 64,
            strategy_sha256=_sha(spec.artifacts.strategy_config.read_bytes()),
        ).canonical_bytes()
    )
    feature_evidence_mismatch_spec = spec.model_copy(
        update={
            "artifacts": spec.artifacts.model_copy(
                update={"shadow_evidence": feature_evidence_mismatch_path}
            )
        }
    )
    with pytest.raises(ValueError, match="shadow evidence does not bind"):
        prepare_release_bundle(
            bundle=bundle,
            spec=feature_evidence_mismatch_spec,
            output_dir=(tmp_path / "feature-evidence-mismatch").resolve(),
        )

    failed_shadow_path = (tmp_path / "failed-shadow.json").resolve()
    failed_shadow_path.write_bytes(
        _shadow_report(
            feature_sha256=_sha((config_dir / "features" / "microstructure-v1.toml").read_bytes()),
            strategy_sha256=_sha(spec.artifacts.strategy_config.read_bytes()),
            passed=False,
        ).canonical_bytes()
    )
    failed_shadow_spec = spec.model_copy(
        update={
            "artifacts": spec.artifacts.model_copy(update={"shadow_evidence": failed_shadow_path})
        }
    )
    with pytest.raises(ValueError, match="shadow evidence has not reached"):
        prepare_release_bundle(
            bundle=bundle,
            spec=failed_shadow_spec,
            output_dir=(tmp_path / "failed-shadow").resolve(),
        )

    incomplete_shadow_path = (tmp_path / "incomplete-shadow.json").resolve()
    incomplete_shadow = _shadow_report(
        feature_sha256=_sha((config_dir / "features" / "microstructure-v1.toml").read_bytes()),
        strategy_sha256=_sha(spec.artifacts.strategy_config.read_bytes()),
    ).model_dump(mode="json")
    incomplete_shadow["gates"] = incomplete_shadow["gates"][:1]
    incomplete_shadow_identity = {
        key: value
        for key, value in incomplete_shadow.items()
        if key not in {"report_id", "awaiting_human_approval"}
    }
    incomplete_shadow["report_id"] = canonical_sha256(incomplete_shadow_identity)
    incomplete_shadow_path.write_bytes(
        ShadowEvidenceReport.model_validate(incomplete_shadow).canonical_bytes()
    )
    incomplete_shadow_spec = spec.model_copy(
        update={
            "artifacts": spec.artifacts.model_copy(
                update={"shadow_evidence": incomplete_shadow_path}
            )
        }
    )
    with pytest.raises(ValueError, match="shadow evidence gate set"):
        prepare_release_bundle(
            bundle=bundle,
            spec=incomplete_shadow_spec,
            output_dir=(tmp_path / "incomplete-shadow").resolve(),
        )

    failed_testnet_path = (tmp_path / "failed-testnet.json").resolve()
    failed_testnet_path.write_bytes(
        evaluate_testnet_evidence(
            observation=_observation(orders=14, fills=4),
            policy=_policy(),
            generated_ts_ns=40,
        ).canonical_bytes()
    )
    failed_testnet_spec = spec.model_copy(
        update={
            "artifacts": spec.artifacts.model_copy(update={"testnet_evidence": failed_testnet_path})
        }
    )
    with pytest.raises(ValueError, match="testnet evidence has not reached"):
        prepare_release_bundle(
            bundle=bundle,
            spec=failed_testnet_spec,
            output_dir=(tmp_path / "failed-testnet").resolve(),
        )

    over_cap = spec.model_copy(update={"capital_limit_usd": Decimal("1000.01")})
    with pytest.raises(ValueError, match="capital exceeds"):
        prepare_release_bundle(
            bundle=bundle,
            spec=over_cap,
            output_dir=(tmp_path / "over-cap").resolve(),
        )
    under_inventory = spec.model_copy(update={"capital_limit_usd": Decimal("499")})
    with pytest.raises(ValueError, match="inventory limit exceeds"):
        prepare_release_bundle(
            bundle=bundle,
            spec=under_inventory,
            output_dir=(tmp_path / "under-inventory").resolve(),
        )


def test_release_bundle_cleans_partial_atomic_write(
    tmp_path: Path,
    project_root: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec, _testnet_path = _release_inputs(tmp_path, project_root, config_dir)
    bundle = load_config(config_dir, "canary", environ={})
    output = (tmp_path / "interrupted-release").resolve()
    original_write = release_bundle_module._write_new

    def interrupted_write(path: Path, payload: bytes) -> None:
        if path.name == "artifact-manifest.json":
            raise OSError("simulated interrupted release write")
        original_write(path, payload)

    monkeypatch.setattr(release_bundle_module, "_write_new", interrupted_write)
    with pytest.raises(OSError, match="simulated interrupted"):
        prepare_release_bundle(bundle=bundle, spec=spec, output_dir=output)

    assert not output.exists()
    assert not tuple(tmp_path.glob(".interrupted-release.*.tmp"))


def test_release_file_write_removes_partial_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "partial-file"

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="simulated fsync failure"):
        release_bundle_module._write_new(output, b"release")
    assert not output.exists()


def test_release_cli_renders_behavior_and_evaluates_testnet(
    tmp_path: Path,
    project_root: Path,
    config_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec, _testnet_path = _release_inputs(tmp_path, project_root, config_dir)
    spec_path = tmp_path / "release.toml"
    # TOML is the operator interface; use a deterministic writer-shaped fixture.
    spec_path.write_text(
        "\n".join(
            (
                "schema_version = 1",
                'deployment_id = "canary-release-001"',
                'approval_id = "canary-approval-001"',
                'stage = "approved_canary"',
                'rollback_deployment_id = "halted-release-000"',
                f'commit_sha = "{COMMIT}"',
                f'image_digest = "{IMAGE}"',
                f'account_address = "{MAINNET_ACCOUNT}"',
                f'trading_wallet_address = "{MAINNET_TRADING}"',
                f'control_wallet_address = "{MAINNET_CONTROL}"',
                'capital_limit_usd = "1000"',
                'approver = "risk-owner@example.invalid"',
                "approved_at = 2026-08-04T12:00:00Z",
                "expires_at = 2026-08-05T12:00:00Z",
                "[risk]",
                *(
                    f'{key} = "{value}"'
                    if isinstance(value, str)
                    else f"{key} = {str(value).lower()}"
                    for key, value in spec.risk.model_dump(mode="json").items()
                ),
                "[artifacts]",
                *(
                    f'{key} = "{value}"'
                    for key, value in spec.artifacts.model_dump().items()
                    if value is not None
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    loaded = load_release_bundle_spec(spec_path)
    assert loaded.deployment_id == spec.deployment_id
    behavior_path = tmp_path / "behavior.json"
    common = [
        "--config-dir",
        str(config_dir),
        "--environment",
        "canary",
        "--spec",
        str(spec_path),
    ]
    assert governance_main(["release-fingerprint", *common, "--output", str(behavior_path)]) == 0
    assert behavior_path.is_file()
    assert "awaiting_testnet_rehearsal" in capsys.readouterr().out
    prepared = (tmp_path / "cli-release-bundle").resolve()
    assert governance_main(["prepare-release", *common, "--output-dir", str(prepared)]) == 0
    assert (prepared / "deployment-approval.unsigned.json").is_file()
    assert json.loads(capsys.readouterr().out)["awaiting_offline_signature"]

    observation = _observation()
    observation_path = tmp_path / "observation.json"
    policy_path = tmp_path / "policy.toml"
    output_path = tmp_path / "testnet-report.json"
    observation_path.write_bytes(observation.canonical_bytes())
    checked_policy = config_dir / "production" / "testnet-dress-rehearsal-v1.toml"
    policy_path.write_bytes(checked_policy.read_bytes())
    assert load_testnet_policy(policy_path).policy_id == "btc-final-testnet-rehearsal-v1"
    assert (
        governance_main(
            [
                "evaluate-testnet",
                "--observation",
                str(observation_path),
                "--policy",
                str(policy_path),
                "--output",
                str(output_path),
            ]
        )
        == 1
    )
    assert output_path.is_file()
    assert not json.loads(capsys.readouterr().out)["awaiting_canary_approval"]
