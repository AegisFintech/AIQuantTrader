from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from aiquanttrader.backtest.kernel import KernelBookLevel, KernelMarketState
from aiquanttrader.features.models import FeatureEngineConfig
from aiquanttrader.features.storage import write_feature_dataset
from aiquanttrader.research.matrix import build_forecast_matrix, load_forecast_matrix
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


def test_forecast_matrix_is_deterministic_causal_and_manifest_bound(tmp_path: Path) -> None:
    feature_manifest_path, feature_manifest = write_feature_dataset(
        states(),
        config=feature_config(),
        source_dataset_sha256="a" * 64,
        output_root=tmp_path,
        relative_path="features/BTC.parquet",
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
    )

    assert first_path == second_path
    assert first == second
    assert first.ready_row_count == 8
    assert first.candidate_row_count == 8
    assert first.row_count == 6
    assert first.dropped_label_gap_count == 0
    assert first.dropped_tail_count == 2

    matrix, loaded_manifest = load_forecast_matrix(
        tmp_path / "matrices/next-mid-30s.npz", first_path
    )
    assert loaded_manifest == first
    assert len(matrix.labels) == 6
    assert np.all(matrix.labels > 0)
    assert np.all(matrix.label_end_ts_ns - matrix.sample_ts_ns == 2 * SECOND_NS)
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
    )
    matrix_path = tmp_path / "matrices/valid.npz"
    matrix_path.write_bytes(matrix_path.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="does not match"):
        load_forecast_matrix(matrix_path, matrix_manifest_path)


def test_forecast_matrix_counts_labels_rejected_across_data_gaps(tmp_path: Path) -> None:
    feature_manifest_path, feature_manifest = write_feature_dataset(
        states((1, 2, 3, 10, 11, 12, 13, 14, 15, 16)),
        config=feature_config(),
        source_dataset_sha256="c" * 64,
        output_root=tmp_path,
        relative_path="features/gapped.parquet",
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
    )

    assert manifest.candidate_row_count == 9
    assert manifest.row_count == 5
    assert manifest.dropped_label_gap_count == 2
    assert manifest.dropped_tail_count == 2


def test_forecast_matrix_manifest_rejects_corrupt_identity_counts_and_windows() -> None:
    values: dict[str, object] = {
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
        "dropped_label_gap_count": 1,
        "dropped_tail_count": 1,
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
        ({"last_sample_ts_ns": 100}, "sample window is not increasing"),
        ({"first_label_end_ts_ns": 100}, "first label is not causal"),
        ({"last_label_end_ts_ns": 200}, "last label is not causal"),
        (
            {"first_label_end_ts_ns": 240, "last_label_end_ts_ns": 230},
            "label window is reversed",
        ),
    )
    for updates, message in corruptions:
        with pytest.raises(ValueError, match=message):
            ForecastMatrixManifest.create(**(values | updates))

    assert valid.row_count == 4
