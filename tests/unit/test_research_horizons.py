from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from aiquanttrader.backtest.models import ValidationPolicy
from aiquanttrader.research.horizons import _write_immutable, validation_policy_for_horizon
from aiquanttrader.research.models import ForecastTarget, HorizonFamilyPolicy


def validation_template() -> ValidationPolicy:
    return ValidationPolicy(
        policy_id="bounded-template",
        train_ns=1_000,
        purge_ns=30,
        validation_ns=100,
        embargo_ns=20,
        test_ns=100,
        step_ns=100,
        final_holdout_ns=200,
        label_horizon_ns=30,
        minimum_folds=1,
    )


def test_horizon_policy_is_predeclared_sorted_and_scalping_bounded() -> None:
    policy = HorizonFamilyPolicy(
        policy_id="bounded-family",
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        horizons_ns=(30, 60, 300_000_000_000),
        sample_interval_ns=10,
        maximum_label_delay_ns=2,
    )
    assert policy.horizons_ns == (30, 60, 300_000_000_000)

    with pytest.raises(ValidationError, match="strictly increasing"):
        HorizonFamilyPolicy(
            policy_id="unsorted",
            target=ForecastTarget.NEXT_MID_RETURN_BPS,
            horizons_ns=(60, 30),
            sample_interval_ns=10,
            maximum_label_delay_ns=0,
        )
    with pytest.raises(ValidationError, match="five-minute"):
        HorizonFamilyPolicy(
            policy_id="too-long",
            target=ForecastTarget.NEXT_MID_RETURN_BPS,
            horizons_ns=(30, 300_000_000_001),
            sample_interval_ns=10,
            maximum_label_delay_ns=0,
        )
    with pytest.raises(ValidationError, match="sample interval"):
        HorizonFamilyPolicy(
            policy_id="sparse-sampling",
            target=ForecastTarget.NEXT_MID_RETURN_BPS,
            horizons_ns=(30, 60),
            sample_interval_ns=31,
            maximum_label_delay_ns=0,
        )


def test_horizon_validation_policy_expands_purge_without_mutating_template() -> None:
    template = validation_template()
    short = validation_policy_for_horizon(template, 20)
    long = validation_policy_for_horizon(template, 60)

    assert template.label_horizon_ns == 30
    assert template.purge_ns == 30
    assert short.label_horizon_ns == 20
    assert short.purge_ns == 30
    assert long.label_horizon_ns == 60
    assert long.purge_ns == 60
    assert short.train_ns == long.train_ns == template.train_ns
    assert short.final_holdout_ns == long.final_holdout_ns == template.final_holdout_ns
    assert short.policy_id == "bounded-template.h20"
    assert long.policy_id == "bounded-template.h60"


def test_horizon_artifacts_are_idempotent_but_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "nested/artifact.json"
    _write_immutable(path, b"same\n")
    _write_immutable(path, b"same\n")
    assert path.read_bytes() == b"same\n"

    with pytest.raises(FileExistsError, match="immutable horizon-audit artifact differs"):
        _write_immutable(path, b"different\n")
