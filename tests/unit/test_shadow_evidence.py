from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from aiquanttrader.config import load_config
from aiquanttrader.domain.base import canonical_sha256
from aiquanttrader.features.models import VolatilityRegime
from aiquanttrader.paper.journal import PaperJournalStatistics
from aiquanttrader.paper.models import PaperRunManifest
from aiquanttrader.shadow.audit import ShadowAuditStatistics
from aiquanttrader.shadow.config import ShadowArtifacts, load_shadow_artifacts
from aiquanttrader.shadow.evidence import evaluate_shadow_evidence
from aiquanttrader.shadow.models import (
    ShadowDeterminismReport,
    ShadowEvidencePolicy,
)

IMAGE = "sha256:" + "a" * 64


def _statistics(*, invalidating: tuple[str, ...] = ()) -> PaperJournalStatistics:
    return PaperJournalStatistics(
        started_ts_ns=1_000,
        ended_ts_ns=2_000,
        independent_decisions=1,
        approved_decisions=1,
        denied_decisions=0,
        fills=1,
        markouts=1,
        ending_position_base=Decimal("0"),
        open_orders=0,
        regimes=(VolatilityRegime.NORMAL,),
        ending_equity_usd=Decimal("1001"),
        starting_equity_usd=Decimal("1000"),
        maximum_drawdown_fraction=Decimal("0"),
        mean_signed_markout_bps=Decimal("1"),
        drift_evaluated=True,
        maximum_feature_psi=Decimal("0.01"),
        maximum_standardized_mean_shift=Decimal("0.01"),
        completed_drills=(),
        invalidating_events=invalidating,
        commands=1,
        submit_commands=1,
        feature_samples=1,
    )


def _audit(*, invalidating: tuple[str, ...] = ()) -> ShadowAuditStatistics:
    return ShadowAuditStatistics(
        cycle_samples=1,
        health_samples=1,
        availability_fraction=Decimal("1"),
        ingress_latency_p99_ms=Decimal("1"),
        cycle_latency_p99_ms=Decimal("1"),
        completed_drills=(),
        invalidating_events=invalidating,
    )


def _policy(required_scenario: str) -> ShadowEvidencePolicy:
    return ShadowEvidencePolicy(
        policy_id="shadow-test-evidence-v1",
        frozen_at_ns=1,
        minimum_observation_ns=1,
        minimum_independent_decisions=1,
        minimum_fills=1,
        minimum_regimes=1,
        minimum_availability_fraction=Decimal("0.9"),
        maximum_ingress_latency_p99_ms=Decimal("10"),
        maximum_cycle_latency_p99_ms=Decimal("10"),
        maximum_drawdown_fraction=Decimal("0.1"),
        maximum_denial_fraction=Decimal("0.5"),
        maximum_adverse_markout_bps=Decimal("5"),
        maximum_feature_psi=Decimal("0.2"),
        maximum_standardized_mean_shift=Decimal("1"),
        minimum_determinism_decisions=1,
        require_calibrated_scenario=False,
        required_sensitivity_scenarios=(required_scenario,),
        required_drills=(),
    )


def _manifest(
    run_id: str, configured: ShadowArtifacts, scenario_id: str, scenario_sha: str
) -> PaperRunManifest:
    return PaperRunManifest(
        run_id=run_id,
        environment="shadow",
        started_ts_ns=1_000,
        code_identity="test-commit",
        image_identity=IMAGE,
        config_fingerprint="1" * 64,
        feature_config_sha256=configured.paper.feature_config_sha256,
        strategy_config_sha256=configured.paper.strategy_config_sha256,
        scenario_id=scenario_id,
        scenario_sha256=scenario_sha,
        evidence_policy_sha256=configured.paper.evidence_policy_sha256,
        strategy_id=configured.paper.strategy_config.strategy_id,
    )


def _determinism(manifest: PaperRunManifest, replay_suffix: str) -> ShadowDeterminismReport:
    payload = {
        "schema_version": 1,
        "source_run_id": manifest.run_id,
        "replay_run_id": f"replay-{replay_suffix}",
        "source_manifest_sha256": manifest.sha256(),
        "replay_manifest_sha256": "9" * 64,
        "compared_decisions": 1,
        "decision_mismatches": 0,
        "compared_commands": 1,
        "command_mismatches": 0,
        "generated_ts_ns": 3_000,
    }
    return ShadowDeterminismReport.model_validate(
        {"report_id": canonical_sha256(payload), **payload}
    )


def test_shadow_evidence_stops_at_human_approval_and_binds_sensitivity(
    config_dir: Path,
) -> None:
    loaded = load_shadow_artifacts(
        config_dir,
        load_config(config_dir, "shadow", environ={}),
    )
    pessimistic = loaded.paper.sensitivity_scenarios[0]
    policy = _policy(pessimistic.scenario_id)
    common = replace(
        loaded,
        evidence_policy=policy,
        evidence_policy_sha256=policy.sha256(),
    )

    sensitivity_artifacts = replace(
        common,
        paper=replace(common.paper, scenario=pessimistic),
    )
    sensitivity_manifest = _manifest(
        "shadow-sensitivity",
        sensitivity_artifacts,
        pessimistic.scenario_id,
        pessimistic.sha256(),
    )
    sensitivity = evaluate_shadow_evidence(
        manifest=sensitivity_manifest,
        statistics=_statistics(),
        audit=_audit(),
        artifacts=sensitivity_artifacts,
        determinism=_determinism(sensitivity_manifest, "sensitivity"),
        generated_ts_ns=4_000,
    )
    assert not sensitivity.awaiting_human_approval
    assert [gate.gate for gate in sensitivity.gates if not gate.passed] == ["sensitivity"]

    baseline = common.paper.scenario
    manifest = _manifest(
        "shadow-baseline",
        common,
        baseline.scenario_id,
        baseline.sha256(),
    )
    report = evaluate_shadow_evidence(
        manifest=manifest,
        statistics=_statistics(),
        audit=_audit(),
        artifacts=common,
        determinism=_determinism(manifest, "baseline"),
        sensitivity_reports=(sensitivity,),
        generated_ts_ns=5_000,
    )
    assert report.awaiting_human_approval
    assert all(gate.passed for gate in report.gates)
    assert report.sensitivity_scenarios == (pessimistic.scenario_id,)
    assert len(report.report_id) == 64


def test_shadow_evidence_rejects_integrity_determinism_and_command_gaps(
    config_dir: Path,
) -> None:
    loaded = load_shadow_artifacts(
        config_dir,
        load_config(config_dir, "shadow", environ={}),
    )
    pessimistic = loaded.paper.sensitivity_scenarios[0]
    policy = _policy(pessimistic.scenario_id)
    artifacts = replace(
        loaded,
        evidence_policy=policy,
        evidence_policy_sha256=policy.sha256(),
    )
    scenario = artifacts.paper.scenario
    manifest = _manifest(
        "shadow-bad",
        artifacts,
        scenario.scenario_id,
        scenario.sha256(),
    )
    stats = replace(
        _statistics(invalidating=("service_failure",)),
        submit_commands=0,
        feature_samples=2,
    )
    report = evaluate_shadow_evidence(
        manifest=manifest,
        statistics=stats,
        audit=_audit(invalidating=("clock_failure",)),
        artifacts=artifacts,
        determinism=None,
        generated_ts_ns=5_000,
    )
    failed = {gate.gate for gate in report.gates if not gate.passed}
    assert {
        "operational_sample_completeness",
        "command_completeness",
        "determinism",
        "sensitivity",
        "run_integrity",
    } <= failed
    assert not report.awaiting_human_approval
