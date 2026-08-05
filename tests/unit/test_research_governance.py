from __future__ import annotations

import numpy as np
import pytest

from aiquanttrader.features.models import MODEL_FEATURE_SCHEMA
from aiquanttrader.research.drift import calculate_drift
from aiquanttrader.research.governance import evaluate_challenger
from aiquanttrader.research.metrics import ResearchMetrics
from aiquanttrader.research.models import (
    NegativeControlReport,
    PromotionMetrics,
    PromotionPolicy,
)


def metrics(**updates: float | int) -> PromotionMetrics:
    values: dict[str, float | int] = {
        "post_cost_pnl_usd": 1_500.0,
        "maximum_drawdown_usd": 200.0,
        "tail_loss_99_usd": 100.0,
        "maximum_abs_inventory_base": 0.01,
        "fill_count": 2_000,
        "maker_ratio": 0.85,
        "adverse_selection_bps": 0.5,
        "decision_latency_p99_ms": 2.0,
        "fold_consistency": 0.9,
        "drift_psi_max": 0.05,
        "operational_failure_count": 0,
    }
    values.update(updates)
    return PromotionMetrics.model_validate(values)


def policy() -> PromotionPolicy:
    return PromotionPolicy(
        policy_id="strict-test-policy",
        minimum_post_cost_pnl_usd=100.0,
        maximum_drawdown_usd=500.0,
        maximum_tail_loss_99_usd=200.0,
        maximum_abs_inventory_base=0.02,
        minimum_fill_count=1_000,
        minimum_maker_ratio=0.7,
        maximum_adverse_selection_bps=2.0,
        maximum_decision_latency_p99_ms=5.0,
        minimum_fold_consistency=0.8,
        maximum_drift_psi=0.2,
        minimum_champion_improvement_usd=100.0,
    )


def controls(*, no_signal_decision_count: int = 0) -> NegativeControlReport:
    return NegativeControlReport(
        randomized_label_score=10.0,
        randomized_label_minimum_mse=1.0,
        no_signal_decision_count=no_signal_decision_count,
        no_signal_report_sha256="f" * 64,
        randomized_seed=7,
    )


def test_challenger_must_pass_every_absolute_relative_and_negative_control_gate() -> None:
    report = evaluate_challenger(
        challenger_experiment_id="challenger-1",
        challenger=metrics(),
        champion_experiment_id="champion-1",
        champion=metrics(post_cost_pnl_usd=1_300.0),
        policy=policy(),
        negative_controls=controls(),
    )
    assert report.passed
    assert report.maximum_automation_stage.value == "awaiting_approval"
    assert {gate.gate for gate in report.gates} >= {
        "post_cost_pnl",
        "champion_improvement",
        "negative_controls",
    }

    failed = evaluate_challenger(
        challenger_experiment_id="challenger-2",
        challenger=metrics(
            maximum_drawdown_usd=800.0,
            operational_failure_count=1,
        ),
        champion_experiment_id="champion-1",
        champion=metrics(post_cost_pnl_usd=1_490.0),
        policy=policy(),
        negative_controls=controls(no_signal_decision_count=1),
    )
    failures = {gate.gate for gate in failed.gates if not gate.passed}
    assert not failed.passed
    assert failures == {
        "maximum_drawdown",
        "operational_failures",
        "champion_improvement",
        "negative_controls",
    }


def test_governance_rejects_partial_champion_identity() -> None:
    with pytest.raises(ValueError, match="supplied together"):
        evaluate_challenger(
            challenger_experiment_id="challenger",
            challenger=metrics(),
            champion_experiment_id="missing-metrics",
            champion=None,
            policy=policy(),
            negative_controls=controls(),
        )


def test_drift_report_is_deterministic_and_detects_distribution_shift() -> None:
    rng = np.random.default_rng(17)
    baseline = rng.normal(size=(2_000, len(MODEL_FEATURE_SCHEMA.features))).astype(np.float64)
    stable = baseline.copy()
    stable_report = calculate_drift(
        baseline,
        stable,
        feature_schema=MODEL_FEATURE_SCHEMA,
    )
    assert not stable_report.drifted
    assert stable_report.maximum_psi == pytest.approx(0.0)

    shifted = stable.copy()
    shifted[:, 0] += 4.0
    shifted_report = calculate_drift(
        baseline,
        shifted,
        feature_schema=MODEL_FEATURE_SCHEMA,
    )
    assert shifted_report.drifted
    assert shifted_report.maximum_psi > shifted_report.psi_threshold
    assert shifted_report.features[0].feature_name == MODEL_FEATURE_SCHEMA.names[0]


def test_drift_and_metrics_fail_closed_without_unbounded_labels() -> None:
    columns = len(MODEL_FEATURE_SCHEMA.features)
    with pytest.raises(ValueError, match="invalid shape"):
        calculate_drift(
            np.ones((2, columns - 1)),
            np.ones((2, columns)),
            feature_schema=MODEL_FEATURE_SCHEMA,
        )
    with pytest.raises(ValueError, match="thresholds"):
        calculate_drift(
            np.ones((2, columns)),
            np.ones((2, columns)),
            feature_schema=MODEL_FEATURE_SCHEMA,
            bins=1,
        )

    research_metrics = ResearchMetrics()
    research_metrics.experiments.labels(stage="draft", result="passed").inc()
    research_metrics.promotion_gate.labels(strategy="market-maker", gate="drawdown").set(1)
    names = {sample.name for sample in research_metrics.registry.collect()}
    assert "aqt_research_experiments" in names
    assert "aqt_research_promotion_gate_passed" in names
