from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from aiquanttrader.backtest.kernel import KernelBookLevel, KernelMarketState
from aiquanttrader.backtest.models import (
    TimeWindow,
    ValidationPlan,
    WalkForwardFold,
    WindowRole,
)
from aiquanttrader.features.models import FeatureEngineConfig
from aiquanttrader.features.storage import write_feature_dataset
from aiquanttrader.research.matrix import (
    build_forecast_matrix,
    load_forecast_matrix,
    require_development_matrix_plan,
)
from aiquanttrader.research.models import ForecastMatrixManifest, ForecastTarget

SECOND_NS = 1_000_000_000


def states(seconds: tuple[int, ...] = tuple(range(1, 10))) -> tuple[KernelMarketState, ...]:
    return tuple(
        KernelMarketState(
            exchange_ts_ns=second * SECOND_NS,
            book_exchange_ts_ns=second * SECOND_NS,
            observed_ts_ns=second * SECOND_NS + 100,
            sequence=sequence,
            bids=(
                KernelBookLevel(
                    price=Decimal(100 + sequence),
                    size=Decimal("2"),
                ),
            ),
            asks=(
                KernelBookLevel(
                    price=Decimal(101 + sequence),
                    size=Decimal("1"),
                ),
            ),
        )
        for sequence, second in enumerate(seconds)
    )


def feature_config() -> FeatureEngineConfig:
    return FeatureEngineConfig(
        depth_levels=1,
        flow_window_ns=10 * SECOND_NS,
        volatility_window_ns=10 * SECOND_NS,
        spread_window_ns=10 * SECOND_NS,
        markout_horizon_ns=SECOND_NS,
        warmup_samples=2,
        maximum_input_age_ns=1_000,
        low_volatility_bps=Decimal("1"),
        high_volatility_bps=Decimal("1000"),
    )


def development_plan(*, dataset_sha256: str, cutoff_ts_ns: int, horizon_ns: int) -> ValidationPlan:
    train_end = cutoff_ts_ns - horizon_ns - 3
    return ValidationPlan(
        policy_sha256="f" * 64,
        dataset_sha256=dataset_sha256,
        label_horizon_ns=horizon_ns,
        folds=(
            WalkForwardFold(
                fold=0,
                train=TimeWindow(role=WindowRole.TRAIN, start_ts_ns=0, end_ts_ns=train_end),
                purge=TimeWindow(
                    role=WindowRole.PURGE,
                    start_ts_ns=train_end,
                    end_ts_ns=train_end + horizon_ns,
                ),
                validation=TimeWindow(
                    role=WindowRole.VALIDATION,
                    start_ts_ns=train_end + horizon_ns,
                    end_ts_ns=train_end + horizon_ns + 1,
                ),
                embargo=TimeWindow(
                    role=WindowRole.EMBARGO,
                    start_ts_ns=train_end + horizon_ns + 1,
                    end_ts_ns=train_end + horizon_ns + 2,
                ),
                test=TimeWindow(
                    role=WindowRole.WALK_FORWARD_TEST,
                    start_ts_ns=train_end + horizon_ns + 2,
                    end_ts_ns=cutoff_ts_ns,
                ),
            ),
        ),
        final_holdout=TimeWindow(
            role=WindowRole.FINAL_HOLDOUT,
            start_ts_ns=cutoff_ts_ns,
            end_ts_ns=cutoff_ts_ns + 1,
        ),
    )


def test_forecast_matrix_is_deterministic_causal_and_manifest_bound(tmp_path: Path) -> None:
    feature_manifest_path, feature_manifest = write_feature_dataset(
        states(),
        config=feature_config(),
        source_dataset_sha256="a" * 64,
        output_root=tmp_path,
        relative_path="features/BTC.parquet",
    )
    plan = development_plan(
        dataset_sha256="a" * 64,
        cutoff_ts_ns=9 * SECOND_NS + 100,
        horizon_ns=2 * SECOND_NS,
    )
    first_path, first = build_forecast_matrix(
        feature_path=tmp_path / feature_manifest.relative_path,
        feature_manifest_path=feature_manifest_path,
        output_root=tmp_path,
        relative_path="matrices/next-mid-30s.npz",
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        horizon_ns=2 * SECOND_NS,
        sample_interval_ns=SECOND_NS,
        maximum_label_delay_ns=0,
        validation_plan=plan,
    )
    second_path, second = build_forecast_matrix(
        feature_path=tmp_path / feature_manifest.relative_path,
        feature_manifest_path=feature_manifest_path,
        output_root=tmp_path,
        relative_path="matrices/next-mid-30s.npz",
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        horizon_ns=2 * SECOND_NS,
        sample_interval_ns=SECOND_NS,
        maximum_label_delay_ns=0,
        validation_plan=plan,
    )

    assert first_path == second_path
    assert first == second
    assert first.schema_version == 3
    assert first.partition_role == "development"
    assert first.ready_row_count == 8
    assert first.candidate_row_count == 8
    assert first.row_count == 5
    assert (
        first.low_volatility_row_count
        + first.normal_volatility_row_count
        + first.high_volatility_row_count
        == first.row_count
    )
    assert first.dropped_label_gap_count == 0
    assert first.dropped_tail_count == 1
    assert first.excluded_holdout_candidate_count == 2
    assert first.validation_plan_sha256 == plan.sha256()
    assert first.development_cutoff_ts_ns == plan.final_holdout.start_ts_ns

    matrix, loaded_manifest = load_forecast_matrix(
        tmp_path / "matrices/next-mid-30s.npz", first_path
    )
    assert loaded_manifest == first
    assert len(matrix.labels) == 5
    assert np.all(matrix.labels > 0)
    assert np.all(matrix.label_end_ts_ns - matrix.sample_ts_ns == 2 * SECOND_NS)
    assert set(matrix.volatility_regimes) <= {"low", "normal", "high"}
    assert np.all(matrix.sample_ts_ns < plan.final_holdout.start_ts_ns)
    assert np.all(matrix.label_end_ts_ns < plan.final_holdout.start_ts_ns)
    assert matrix.sha256() == first.causal_matrix_sha256

    manifest_values = first.model_dump(mode="python", exclude={"schema_version", "matrix_id"})
    wrong_horizon = ForecastMatrixManifest.create(
        **(manifest_values | {"horizon_ns": 3 * SECOND_NS})
    )
    wrong_horizon_path = tmp_path / "matrices/wrong-horizon.manifest.json"
    wrong_horizon_path.write_text(wrong_horizon.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="horizon/delay policy"):
        load_forecast_matrix(tmp_path / "matrices/next-mid-30s.npz", wrong_horizon_path)

    wrong_interval = ForecastMatrixManifest.create(
        **(manifest_values | {"sample_interval_ns": 2 * SECOND_NS})
    )
    wrong_interval_path = tmp_path / "matrices/wrong-interval.manifest.json"
    wrong_interval_path.write_text(wrong_interval.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="interval policy"):
        load_forecast_matrix(tmp_path / "matrices/next-mid-30s.npz", wrong_interval_path)


def test_forecast_matrix_rejects_tampered_source_and_output(tmp_path: Path) -> None:
    feature_manifest_path, feature_manifest = write_feature_dataset(
        states(),
        config=feature_config(),
        source_dataset_sha256="b" * 64,
        output_root=tmp_path,
        relative_path="features/BTC.parquet",
    )
    feature_path = tmp_path / feature_manifest.relative_path
    plan = development_plan(
        dataset_sha256="b" * 64,
        cutoff_ts_ns=9 * SECOND_NS + 100,
        horizon_ns=2 * SECOND_NS,
    )
    tampered_feature = tmp_path / "features/tampered.parquet"
    tampered_feature.write_bytes(feature_path.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="does not match"):
        build_forecast_matrix(
            feature_path=tampered_feature,
            feature_manifest_path=feature_manifest_path,
            output_root=tmp_path,
            relative_path="matrices/rejected.npz",
            target=ForecastTarget.NEXT_MID_RETURN_BPS,
            horizon_ns=2 * SECOND_NS,
            sample_interval_ns=SECOND_NS,
            maximum_label_delay_ns=0,
            validation_plan=plan,
        )

    matrix_manifest_path, _ = build_forecast_matrix(
        feature_path=feature_path,
        feature_manifest_path=feature_manifest_path,
        output_root=tmp_path,
        relative_path="matrices/valid.npz",
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        horizon_ns=2 * SECOND_NS,
        sample_interval_ns=SECOND_NS,
        maximum_label_delay_ns=0,
        validation_plan=plan,
    )
    matrix_path = tmp_path / "matrices/valid.npz"
    matrix_path.write_bytes(matrix_path.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="does not match"):
        load_forecast_matrix(matrix_path, matrix_manifest_path)


def test_forecast_matrix_requires_exact_validation_plan_binding(tmp_path: Path) -> None:
    feature_manifest_path, feature_manifest = write_feature_dataset(
        states(),
        config=feature_config(),
        source_dataset_sha256="d" * 64,
        output_root=tmp_path,
        relative_path="features/BTC.parquet",
    )
    plan = development_plan(
        dataset_sha256="d" * 64,
        cutoff_ts_ns=9 * SECOND_NS + 100,
        horizon_ns=2 * SECOND_NS,
    )
    _, manifest = build_forecast_matrix(
        feature_path=tmp_path / feature_manifest.relative_path,
        feature_manifest_path=feature_manifest_path,
        output_root=tmp_path,
        relative_path="matrices/development.npz",
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        horizon_ns=2 * SECOND_NS,
        sample_interval_ns=SECOND_NS,
        maximum_label_delay_ns=0,
        validation_plan=plan,
    )

    require_development_matrix_plan(manifest, plan)
    with pytest.raises(ValueError, match="does not bind"):
        require_development_matrix_plan(
            manifest,
            plan.model_copy(update={"policy_sha256": "e" * 64}),
        )
    with pytest.raises(ValueError, match="source does not match"):
        require_development_matrix_plan(
            manifest,
            plan.model_copy(update={"dataset_sha256": "e" * 64}),
        )
    with pytest.raises(ValueError, match="horizon does not match"):
        require_development_matrix_plan(
            manifest,
            plan.model_copy(update={"label_horizon_ns": 3 * SECOND_NS}),
        )

    outside_plan = development_plan(
        dataset_sha256="d" * 64,
        cutoff_ts_ns=10 * SECOND_NS + 100,
        horizon_ns=2 * SECOND_NS,
    )
    with pytest.raises(ValueError, match="outside the feature dataset"):
        build_forecast_matrix(
            feature_path=tmp_path / feature_manifest.relative_path,
            feature_manifest_path=feature_manifest_path,
            output_root=tmp_path,
            relative_path="matrices/rejected-boundary.npz",
            target=ForecastTarget.NEXT_MID_RETURN_BPS,
            horizon_ns=2 * SECOND_NS,
            sample_interval_ns=SECOND_NS,
            maximum_label_delay_ns=0,
            validation_plan=outside_plan,
        )
    with pytest.raises(ValueError, match="does not match the feature source"):
        build_forecast_matrix(
            feature_path=tmp_path / feature_manifest.relative_path,
            feature_manifest_path=feature_manifest_path,
            output_root=tmp_path,
            relative_path="matrices/rejected-source.npz",
            target=ForecastTarget.NEXT_MID_RETURN_BPS,
            horizon_ns=2 * SECOND_NS,
            sample_interval_ns=SECOND_NS,
            maximum_label_delay_ns=0,
            validation_plan=plan.model_copy(update={"dataset_sha256": "e" * 64}),
        )
    with pytest.raises(ValueError, match="horizon does not match"):
        build_forecast_matrix(
            feature_path=tmp_path / feature_manifest.relative_path,
            feature_manifest_path=feature_manifest_path,
            output_root=tmp_path,
            relative_path="matrices/rejected-horizon.npz",
            target=ForecastTarget.NEXT_MID_RETURN_BPS,
            horizon_ns=2 * SECOND_NS,
            sample_interval_ns=SECOND_NS,
            maximum_label_delay_ns=0,
            validation_plan=plan.model_copy(update={"label_horizon_ns": 3 * SECOND_NS}),
        )


def test_forecast_matrix_counts_labels_rejected_across_data_gaps(tmp_path: Path) -> None:
    feature_manifest_path, feature_manifest = write_feature_dataset(
        states((1, 2, 3, 10, 11, 12, 13, 14, 15, 16)),
        config=feature_config(),
        source_dataset_sha256="c" * 64,
        output_root=tmp_path,
        relative_path="features/gapped.parquet",
    )
    plan = development_plan(
        dataset_sha256="c" * 64,
        cutoff_ts_ns=16 * SECOND_NS + 100,
        horizon_ns=2 * SECOND_NS,
    )
    _, manifest = build_forecast_matrix(
        feature_path=tmp_path / feature_manifest.relative_path,
        feature_manifest_path=feature_manifest_path,
        output_root=tmp_path,
        relative_path="matrices/gapped.npz",
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        horizon_ns=2 * SECOND_NS,
        sample_interval_ns=SECOND_NS,
        maximum_label_delay_ns=0,
        validation_plan=plan,
    )

    assert manifest.candidate_row_count == 9
    assert manifest.row_count == 4
    assert manifest.dropped_label_gap_count == 2
    assert manifest.dropped_tail_count == 1
    assert manifest.excluded_holdout_candidate_count == 2


def test_forecast_matrix_manifest_rejects_corrupt_identity_counts_and_windows() -> None:
    values: dict[str, object] = {
        "partition_role": "development",
        "validation_plan_sha256": "6" * 64,
        "development_cutoff_ts_ns": 300,
        "target": ForecastTarget.NEXT_MID_RETURN_BPS,
        "horizon_ns": 30,
        "sample_interval_ns": 1,
        "maximum_label_delay_ns": 2,
        "source_feature_dataset_sha256": "1" * 64,
        "source_dataset_sha256": "2" * 64,
        "feature_schema_sha256": "3" * 64,
        "causal_matrix_sha256": "4" * 64,
        "file_sha256": "5" * 64,
        "source_row_count": 10,
        "ready_row_count": 8,
        "candidate_row_count": 6,
        "row_count": 4,
        "low_volatility_row_count": 1,
        "normal_volatility_row_count": 1,
        "high_volatility_row_count": 2,
        "dropped_label_gap_count": 1,
        "dropped_tail_count": 1,
        "excluded_holdout_candidate_count": 0,
        "first_sample_ts_ns": 100,
        "last_sample_ts_ns": 200,
        "first_label_end_ts_ns": 130,
        "last_label_end_ts_ns": 230,
    }
    valid = ForecastMatrixManifest.create(**values)
    with pytest.raises(ValueError, match="canonical lineage"):
        ForecastMatrixManifest.model_validate({"matrix_id": "0" * 64, **values})

    corruptions = (
        ({"ready_row_count": 11}, "ready rows exceed"),
        ({"candidate_row_count": 9}, "candidates exceed"),
        ({"row_count": 3}, "accounting does not balance"),
        ({"high_volatility_row_count": 1}, "regime accounting does not balance"),
        ({"excluded_holdout_candidate_count": 1}, "candidate accounting does not balance"),
        ({"last_sample_ts_ns": 100}, "sample window is not increasing"),
        ({"first_label_end_ts_ns": 100}, "first label is not causal"),
        ({"last_label_end_ts_ns": 200}, "last label is not causal"),
        (
            {"last_sample_ts_ns": 300, "last_label_end_ts_ns": 310},
            "samples reach the final holdout",
        ),
        ({"last_label_end_ts_ns": 300}, "labels reach the final holdout"),
        (
            {"first_label_end_ts_ns": 240, "last_label_end_ts_ns": 230},
            "label window is reversed",
        ),
    )
    for updates, message in corruptions:
        with pytest.raises(ValueError, match=message):
            ForecastMatrixManifest.create(**(values | updates))

    assert valid.row_count == 4
