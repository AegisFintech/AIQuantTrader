from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest
from pydantic import ValidationError

from aiquanttrader_native.backtest.models import (
    CalibrationState,
    ExecutionScenario,
    QueueModel,
    TimeWindow,
    ValidationPolicy,
    WindowRole,
)
from aiquanttrader_native.backtest.scenarios import load_scenario, load_validation_policy
from aiquanttrader_native.backtest.statistics import (
    deflated_selection_mean,
    moving_block_bootstrap_mean,
)
from aiquanttrader_native.backtest.validation import (
    authorize_holdout,
    plan_walk_forward,
    select_candidate,
)


def policy() -> ValidationPolicy:
    return ValidationPolicy(
        policy_id="test-v1",
        train_ns=100,
        purge_ns=10,
        validation_ns=20,
        embargo_ns=10,
        test_ns=20,
        step_ns=20,
        final_holdout_ns=50,
        label_horizon_ns=10,
        minimum_folds=3,
    )


def test_walk_forward_plan_is_disjoint_and_holdout_is_guarded() -> None:
    plan = plan_walk_forward(
        dataset_sha256="a" * 64,
        start_ts_ns=0,
        end_ts_ns=300,
        policy=policy(),
    )

    assert len(plan.folds) == 5
    assert plan.final_holdout.start_ts_ns == 250
    assert plan.final_holdout.role is WindowRole.FINAL_HOLDOUT
    assert all(
        left.test.end_ts_ns <= right.test.start_ts_ns for left, right in pairwise(plan.folds)
    )

    receipt = select_candidate(
        validation_plan=plan,
        validation_scores={"candidate-a": 1.5, "candidate-b": 2.0},
        metric="post_cost_expectancy",
    )
    assert receipt.selected_candidate_id == "candidate-b"
    assert (
        authorize_holdout(
            validation_plan=plan,
            receipt=receipt,
            candidate_id="candidate-b",
        )
        == plan.final_holdout
    )
    with pytest.raises(ValueError, match="only the frozen"):
        authorize_holdout(
            validation_plan=plan,
            receipt=receipt,
            candidate_id="candidate-a",
        )
    with pytest.raises(ValueError, match="does not bind"):
        authorize_holdout(
            validation_plan=plan.model_copy(update={"dataset_sha256": "b" * 64}),
            receipt=receipt,
            candidate_id="candidate-b",
        )


def test_validation_policy_rejects_leakage_and_insufficient_history() -> None:
    with pytest.raises(ValidationError, match="label horizon"):
        ValidationPolicy(**policy().model_dump(exclude={"purge_ns"}), purge_ns=9)

    with pytest.raises(ValidationError, match="test windows"):
        ValidationPolicy(**policy().model_dump(exclude={"step_ns"}), step_ns=19)

    with pytest.raises(ValueError, match="requires 3"):
        plan_walk_forward(
            dataset_sha256="a" * 64,
            start_ts_ns=0,
            end_ts_ns=190,
            policy=policy(),
        )
    with pytest.raises(ValueError, match="invalid dataset"):
        plan_walk_forward(
            dataset_sha256="a" * 64,
            start_ts_ns=10,
            end_ts_ns=10,
            policy=policy(),
        )
    with pytest.raises(ValueError, match="consumes"):
        plan_walk_forward(
            dataset_sha256="a" * 64,
            start_ts_ns=0,
            end_ts_ns=40,
            policy=policy(),
        )
    with pytest.raises(ValidationError, match="window end"):
        TimeWindow(role=WindowRole.TRAIN, start_ts_ns=10, end_ts_ns=10)


def test_checked_scenarios_are_strict_versioned_and_not_yet_promotable(
    config_dir: Path,
) -> None:
    baseline = load_scenario(config_dir / "backtest" / "baseline.toml")
    pessimistic = load_scenario(config_dir / "backtest" / "pessimistic.toml")
    checked_policy = load_validation_policy(config_dir / "backtest" / "validation-v1.toml")

    assert baseline.queue_model is QueueModel.LOG_PROBABILITY
    assert pessimistic.queue_model is QueueModel.RISK_ADVERSE
    assert pessimistic.entry_latency_ns > baseline.entry_latency_ns
    assert pessimistic.trade_flow_multiplier < baseline.trade_flow_multiplier
    assert checked_policy.purge_ns >= checked_policy.label_horizon_ns
    with pytest.raises(ValueError, match="not promotion eligible"):
        baseline.require_promotion_eligible()

    calibrated = ExecutionScenario(
        **baseline.model_dump(exclude={"calibration_state", "calibration_sha256"}, mode="python"),
        calibration_state=CalibrationState.CALIBRATED,
        calibration_sha256="f" * 64,
    )
    calibrated.require_promotion_eligible()
    with pytest.raises(ValidationError, match="calibration hash"):
        ExecutionScenario(
            **baseline.model_dump(
                exclude={"calibration_state", "calibration_sha256"}, mode="python"
            ),
            calibration_state=CalibrationState.CALIBRATED,
        )
    with pytest.raises(ValidationError, match="cannot claim"):
        ExecutionScenario(
            **baseline.model_dump(exclude={"calibration_sha256"}, mode="python"),
            calibration_sha256="e" * 64,
        )


def test_bootstrap_and_selection_penalty_are_deterministic() -> None:
    values = (1.0, -0.5, 2.0, 0.75, 1.25, -0.25)
    first = moving_block_bootstrap_mean(
        values, block_size=2, resamples=500, confidence=0.9, seed=42
    )
    second = moving_block_bootstrap_mean(
        values, block_size=2, resamples=500, confidence=0.9, seed=42
    )
    assert first == second
    assert first.lower <= first.estimate <= first.upper

    one = deflated_selection_mean(values, candidate_count=1)
    many = deflated_selection_mean(values, candidate_count=100)
    assert many.critical_z > one.critical_z
    assert many.deflated_lower_bound < one.deflated_lower_bound

    with pytest.raises(ValueError, match="at least two"):
        moving_block_bootstrap_mean((1.0,), block_size=1)
    with pytest.raises(ValueError, match="block size"):
        moving_block_bootstrap_mean(values, block_size=0)
    with pytest.raises(ValueError, match="100 resamples"):
        moving_block_bootstrap_mean(values, block_size=1, resamples=99)
    with pytest.raises(ValueError, match="confidence"):
        moving_block_bootstrap_mean(values, block_size=1, confidence=1)
    with pytest.raises(ValueError, match="finite"):
        moving_block_bootstrap_mean((1.0, float("nan")), block_size=1)
    with pytest.raises(ValueError, match="candidate count"):
        deflated_selection_mean(values, candidate_count=0)
    with pytest.raises(ValueError, match="family-wise"):
        deflated_selection_mean(values, candidate_count=1, family_wise_alpha=1)


def test_candidate_selection_rejects_ambiguous_or_nonfinite_inputs() -> None:
    plan = plan_walk_forward(
        dataset_sha256="a" * 64,
        start_ts_ns=0,
        end_ts_ns=300,
        policy=policy(),
    )
    with pytest.raises(ValueError, match="requires validation"):
        select_candidate(validation_plan=plan, validation_scores={}, metric="edge")
    with pytest.raises(ValueError, match="metric"):
        select_candidate(
            validation_plan=plan,
            validation_scores={"candidate": 1.0},
            metric="",
        )
    with pytest.raises(ValueError, match="IDs"):
        select_candidate(
            validation_plan=plan,
            validation_scores={"": 1.0},
            metric="edge",
        )
    with pytest.raises(ValueError, match="finite"):
        select_candidate(
            validation_plan=plan,
            validation_scores={"candidate": float("inf")},
            metric="edge",
        )
    receipt = select_candidate(
        validation_plan=plan,
        validation_scores={"candidate-a": 1.0, "candidate-b": 2.0},
        metric="loss",
        maximize=False,
    )
    assert receipt.selected_candidate_id == "candidate-a"
