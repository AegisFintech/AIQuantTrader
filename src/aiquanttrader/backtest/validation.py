"""Leakage-resistant walk-forward planning and final-holdout authorization."""

from __future__ import annotations

import math
from collections.abc import Mapping

from aiquanttrader.backtest.models import (
    SelectionReceipt,
    TimeWindow,
    ValidationPlan,
    ValidationPolicy,
    WalkForwardFold,
    WindowRole,
)
from aiquanttrader.domain.base import canonical_sha256


def plan_walk_forward(
    *, dataset_sha256: str, start_ts_ns: int, end_ts_ns: int, policy: ValidationPolicy
) -> ValidationPlan:
    if start_ts_ns < 0 or end_ts_ns <= start_ts_ns:
        raise ValueError("invalid dataset time range")
    holdout_start = end_ts_ns - policy.final_holdout_ns
    if holdout_start <= start_ts_ns:
        raise ValueError("final holdout consumes the complete dataset")

    folds: list[WalkForwardFold] = []
    cursor = start_ts_ns
    while True:
        train_end = cursor + policy.train_ns
        purge_end = train_end + policy.purge_ns
        validation_end = purge_end + policy.validation_ns
        embargo_end = validation_end + policy.embargo_ns
        test_end = embargo_end + policy.test_ns
        if test_end > holdout_start:
            break
        folds.append(
            WalkForwardFold(
                fold=len(folds),
                train=TimeWindow(role=WindowRole.TRAIN, start_ts_ns=cursor, end_ts_ns=train_end),
                purge=TimeWindow(role=WindowRole.PURGE, start_ts_ns=train_end, end_ts_ns=purge_end),
                validation=TimeWindow(
                    role=WindowRole.VALIDATION,
                    start_ts_ns=purge_end,
                    end_ts_ns=validation_end,
                ),
                embargo=TimeWindow(
                    role=WindowRole.EMBARGO,
                    start_ts_ns=validation_end,
                    end_ts_ns=embargo_end,
                ),
                test=TimeWindow(
                    role=WindowRole.WALK_FORWARD_TEST,
                    start_ts_ns=embargo_end,
                    end_ts_ns=test_end,
                ),
            )
        )
        cursor += policy.step_ns
    if len(folds) < policy.minimum_folds:
        raise ValueError(
            f"dataset yields {len(folds)} folds; policy requires {policy.minimum_folds}"
        )
    return ValidationPlan(
        policy_sha256=policy.sha256(),
        dataset_sha256=dataset_sha256,
        label_horizon_ns=policy.label_horizon_ns,
        folds=tuple(folds),
        final_holdout=TimeWindow(
            role=WindowRole.FINAL_HOLDOUT,
            start_ts_ns=holdout_start,
            end_ts_ns=end_ts_ns,
        ),
    )


def select_candidate(
    *,
    validation_plan: ValidationPlan,
    validation_scores: Mapping[str, float],
    metric: str,
    maximize: bool = True,
) -> SelectionReceipt:
    """Freeze selection using validation-only scores before holdout access."""

    if not validation_scores:
        raise ValueError("candidate selection requires validation scores")
    if not metric:
        raise ValueError("selection metric cannot be empty")
    if any(not candidate_id for candidate_id in validation_scores):
        raise ValueError("candidate IDs cannot be empty")
    if any(not math.isfinite(score) for score in validation_scores.values()):
        raise ValueError("validation scores must be finite")
    ordered = sorted(validation_scores.items())
    selected = sorted(
        ordered,
        key=lambda item: ((-item[1] if maximize else item[1]), item[0]),
    )[0][0]
    payload = {
        "metric": metric,
        "maximize": maximize,
        "validation_scores": ordered,
    }
    return SelectionReceipt(
        selected_candidate_id=selected,
        candidate_set_sha256=canonical_sha256([item[0] for item in ordered]),
        validation_plan_sha256=validation_plan.sha256(),
        selection_metric=metric,
        selection_payload_sha256=canonical_sha256(payload),
    )


def authorize_holdout(
    *,
    validation_plan: ValidationPlan,
    receipt: SelectionReceipt,
    candidate_id: str,
) -> TimeWindow:
    if receipt.validation_plan_sha256 != validation_plan.sha256():
        raise ValueError("selection receipt does not bind this validation plan")
    if receipt.selected_candidate_id != candidate_id:
        raise ValueError("only the frozen selected candidate may access final holdout")
    return validation_plan.final_holdout
