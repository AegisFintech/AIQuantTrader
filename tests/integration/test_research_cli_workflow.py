from __future__ import annotations

import json
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
from aiquanttrader.backtest.scenarios import load_scenario
from aiquanttrader.domain.base import canonical_sha256
from aiquanttrader.features.models import (
    MODEL_FEATURE_SCHEMA,
    FeatureDatasetManifest,
    FeatureEngineConfig,
    VolatilityRegime,
)
from aiquanttrader.features.storage import write_feature_dataset
from aiquanttrader.market_data.io import sha256_file
from aiquanttrader.research.cli import main
from aiquanttrader.research.models import (
    CausalTrainingMatrix,
    ForecastEconomicPolicy,
    ForecastMatrixManifest,
    ForecastRegimePolicy,
    ForecastTarget,
    NoSignalControlReport,
    RandomizedLabelControlPolicy,
    ResearchControlPolicy,
    SearchPolicy,
    SearchTrial,
)
from aiquanttrader.strategies.config import load_scalper_config

SOURCE_DATASET_SHA256 = "a" * 64
SECOND_NS = 1_000_000_000


def validation_plan() -> ValidationPlan:
    return ValidationPlan(
        policy_sha256="b" * 64,
        dataset_sha256=SOURCE_DATASET_SHA256,
        label_horizon_ns=5,
        folds=(
            WalkForwardFold(
                fold=0,
                train=TimeWindow(role=WindowRole.TRAIN, start_ts_ns=0, end_ts_ns=200),
                purge=TimeWindow(role=WindowRole.PURGE, start_ts_ns=200, end_ts_ns=220),
                validation=TimeWindow(role=WindowRole.VALIDATION, start_ts_ns=220, end_ts_ns=320),
                embargo=TimeWindow(role=WindowRole.EMBARGO, start_ts_ns=320, end_ts_ns=340),
                test=TimeWindow(
                    role=WindowRole.WALK_FORWARD_TEST,
                    start_ts_ns=340,
                    end_ts_ns=440,
                ),
            ),
        ),
        final_holdout=TimeWindow(
            role=WindowRole.FINAL_HOLDOUT,
            start_ts_ns=440,
            end_ts_ns=600,
        ),
    )


def write_matrix(
    path: Path,
    *,
    schema_hash: str | None = None,
    source_feature_dataset_sha256: str = "b" * 64,
) -> Path:
    rows = 60
    features = np.zeros((rows, len(MODEL_FEATURE_SCHEMA.features)), dtype=np.float64)
    features[:, 0] = np.arange(rows, dtype=np.float64) / 10
    features[:, MODEL_FEATURE_SCHEMA.names.index("realized_volatility")] = np.arange(rows) % 3
    labels = features[:, 0] * 2
    timestamps = np.arange(rows, dtype=np.int64) * 10
    volatility_regimes = np.asarray(
        [
            (VolatilityRegime.LOW, VolatilityRegime.NORMAL, VolatilityRegime.HIGH)[index % 3].value
            for index in range(rows)
        ]
    )
    np.savez(
        path,
        features=features,
        labels=labels,
        sample_ts_ns=timestamps,
        label_end_ts_ns=timestamps + 5,
        volatility_regimes=volatility_regimes,
        feature_schema_sha256=(schema_hash or MODEL_FEATURE_SCHEMA.sha256()),
        source_dataset_sha256=SOURCE_DATASET_SHA256,
    )
    matrix = CausalTrainingMatrix(
        features=features,
        labels=labels,
        sample_ts_ns=timestamps,
        label_end_ts_ns=timestamps + 5,
        volatility_regimes=volatility_regimes,
        feature_schema=MODEL_FEATURE_SCHEMA,
        source_dataset_sha256=SOURCE_DATASET_SHA256,
    )
    manifest = ForecastMatrixManifest.create(
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        horizon_ns=5,
        sample_interval_ns=10,
        maximum_label_delay_ns=0,
        source_feature_dataset_sha256=source_feature_dataset_sha256,
        source_dataset_sha256=SOURCE_DATASET_SHA256,
        feature_schema_sha256=schema_hash or MODEL_FEATURE_SCHEMA.sha256(),
        causal_matrix_sha256=matrix.sha256(),
        file_sha256=sha256_file(path),
        source_row_count=rows,
        ready_row_count=rows,
        candidate_row_count=rows,
        row_count=rows,
        low_volatility_row_count=20,
        normal_volatility_row_count=20,
        high_volatility_row_count=20,
        dropped_label_gap_count=0,
        dropped_tail_count=0,
        first_sample_ts_ns=int(timestamps[0]),
        last_sample_ts_ns=int(timestamps[-1]),
        first_label_end_ts_ns=int(timestamps[0] + 5),
        last_label_end_ts_ns=int(timestamps[-1] + 5),
    )
    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    manifest_path.write_text(manifest.model_dump_json(), encoding="utf-8")
    return manifest_path


def write_feature_manifest(path: Path) -> FeatureDatasetManifest:
    identity = {
        "source_dataset_sha256": SOURCE_DATASET_SHA256,
        "feature_schema_sha256": MODEL_FEATURE_SCHEMA.sha256(),
        "feature_config_sha256": "c" * 64,
        "relative_path": "features/BTC.parquet",
        "file_sha256": "d" * 64,
        "row_count": 1_000,
        "stale_trade_exclusion_count": 0,
        "stale_book_exclusion_count": 0,
        "first_receive_ts_ns": 1,
        "last_receive_ts_ns": 2,
    }
    manifest = FeatureDatasetManifest.model_validate(
        {"feature_dataset_id": canonical_sha256(identity), **identity}
    )
    path.write_bytes(manifest.canonical_bytes() + b"\n")
    return manifest


def write_control_policy(path: Path) -> ResearchControlPolicy:
    policy = ResearchControlPolicy(
        policy_id="cli-test-controls",
        randomized_label=RandomizedLabelControlPolicy(
            repetitions=3,
            base_seed=11,
            minimum_median_mse_multiple_of_selected_model=1.0,
            minimum_worst_mse_multiple_of_training_mean=0.5,
        ),
        forecast_regime=ForecastRegimePolicy(minimum_rows_per_slice=2),
        forecast_economic=ForecastEconomicPolicy(
            minimum_expected_edge_bps=0.0,
            minimum_trades=2,
            minimum_trades_per_regime=1,
            minimum_profit_factor=1.01,
            require_calibrated_scenario=False,
        ),
    )
    path.write_bytes(policy.canonical_bytes() + b"\n")
    return policy


def write_zero_cost_scenario(path: Path) -> None:
    path.write_text(
        """schema_version = 1
scenario_id = "zero-cost-test"
calibration_state = "uncalibrated"
tick_size = "1.0"
lot_size = "0.00001"
entry_latency_ns = 0
response_latency_ns = 0
feed_latency_offset_ns = 0
maker_fee_bps = "0"
taker_fee_bps = "0"
queue_model = "risk_adverse"
queue_power = "2"
allow_partial_fills = true
book_liquidity_multiplier = "1.0"
trade_flow_multiplier = "1.0"
taker_slippage_bps = "0"
funding_rate_multiplier = "1.0"
""",
        encoding="utf-8",
    )


def test_build_matrix_cli_binds_immutable_features_and_explicit_label_policy(
    tmp_path: Path,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    states = tuple(
        KernelMarketState(
            exchange_ts_ns=(sequence + 1) * SECOND_NS,
            book_exchange_ts_ns=(sequence + 1) * SECOND_NS,
            observed_ts_ns=(sequence + 1) * SECOND_NS + 100,
            sequence=sequence,
            bids=(KernelBookLevel(price=Decimal(100 + sequence), size=Decimal("2")),),
            asks=(KernelBookLevel(price=Decimal(101 + sequence), size=Decimal("1")),),
        )
        for sequence in range(7)
    )
    feature_manifest_path, feature_manifest = write_feature_dataset(
        states,
        config=FeatureEngineConfig(
            depth_levels=1,
            flow_window_ns=10 * SECOND_NS,
            volatility_window_ns=10 * SECOND_NS,
            spread_window_ns=10 * SECOND_NS,
            markout_horizon_ns=SECOND_NS,
            warmup_samples=2,
            maximum_input_age_ns=1_000,
            low_volatility_bps=Decimal("1"),
            high_volatility_bps=Decimal("1000"),
        ),
        source_dataset_sha256=SOURCE_DATASET_SHA256,
        output_root=tmp_path,
        relative_path="features/BTC.parquet",
    )

    assert (
        main(
            [
                "build-matrix",
                "--features",
                str(tmp_path / feature_manifest.relative_path),
                "--feature-manifest",
                str(feature_manifest_path),
                "--output-root",
                str(tmp_path),
                "--relative-path",
                "matrices/next-mid.npz",
                "--target",
                "next_mid_return_bps",
                "--horizon-ns",
                str(2 * SECOND_NS),
                "--sample-interval-ns",
                str(SECOND_NS),
                "--maximum-label-delay-ns",
                "0",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["rows"] == 4
    assert result["dropped_tail"] == 2
    assert (tmp_path / "matrices/next-mid.npz").is_file()
    assert Path(result["manifest"]).is_file()

    no_signal_path = tmp_path / "controls/no-signal.json"
    assert (
        main(
            [
                "run-no-signal-control",
                "--features",
                str(tmp_path / feature_manifest.relative_path),
                "--feature-manifest",
                str(feature_manifest_path),
                "--strategy-config",
                str(project_root / "configs/strategies/order-flow-scalper-v1.toml"),
                "--scenario",
                str(project_root / "configs/backtest/baseline.toml"),
                "--output",
                str(no_signal_path),
            ]
        )
        == 0
    )
    control_output = json.loads(capsys.readouterr().out)
    report = NoSignalControlReport.model_validate_json(no_signal_path.read_bytes())
    assert control_output["passed"] is True
    assert report.feature_dataset_sha256 == feature_manifest.feature_dataset_id
    assert report.observation_count == len(states)
    assert 0 < report.ready_observation_count <= report.observation_count
    assert report.decision_count == 0

    feature_path = tmp_path / feature_manifest.relative_path
    feature_path.write_bytes(feature_path.read_bytes() + b"tamper")
    assert (
        main(
            [
                "run-no-signal-control",
                "--features",
                str(feature_path),
                "--feature-manifest",
                str(feature_manifest_path),
                "--strategy-config",
                str(project_root / "configs/strategies/order-flow-scalper-v1.toml"),
                "--scenario",
                str(project_root / "configs/backtest/baseline.toml"),
                "--output",
                str(tmp_path / "controls/rejected-no-signal.json"),
            ]
        )
        == 2
    )
    assert "immutable manifest" in capsys.readouterr().err


def test_run_search_writes_reproducible_native_artifact_and_validates_it(
    tmp_path: Path,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    matrix_path = tmp_path / "matrix.npz"
    plan_path = tmp_path / "validation-plan.json"
    policy_path = tmp_path / "search-policy.json"
    control_policy_path = tmp_path / "control-policy.json"
    receipt_path = tmp_path / "search-receipt.json"
    no_signal_path = tmp_path / "no-signal.json"
    artifacts = tmp_path / "artifacts"
    feature_manifest_path = tmp_path / "feature.manifest.json"
    feature_manifest = write_feature_manifest(feature_manifest_path)
    matrix_manifest_path = write_matrix(
        matrix_path,
        source_feature_dataset_sha256=feature_manifest.feature_dataset_id,
    )
    strategy_path = project_root / "configs/strategies/order-flow-scalper-v1.toml"
    scenario_path = tmp_path / "zero-cost-scenario.toml"
    write_zero_cost_scenario(scenario_path)
    plan_path.write_text(validation_plan().model_dump_json(), encoding="utf-8")
    policy_path.write_text(
        SearchPolicy(
            policy_id="cli-lightgbm-test",
            trials=(
                SearchTrial(
                    trial_id="short",
                    parameters={"num_boost_round": 2, "min_data_in_leaf": 2},
                ),
                SearchTrial(
                    trial_id="long",
                    parameters={"num_boost_round": 5, "min_data_in_leaf": 2},
                ),
            ),
        ).model_dump_json(),
        encoding="utf-8",
    )
    control_policy = write_control_policy(control_policy_path)
    no_signal_path.write_text(
        NoSignalControlReport(
            control_id="neutral-alpha-order-flow-v1",
            feature_dataset_sha256=feature_manifest.feature_dataset_id,
            feature_file_sha256=feature_manifest.file_sha256,
            feature_schema_sha256=MODEL_FEATURE_SCHEMA.sha256(),
            strategy_configuration_sha256=load_scalper_config(strategy_path).sha256(),
            scenario_sha256=load_scenario(scenario_path).sha256(),
            observation_count=1_000,
            ready_observation_count=999,
            decision_count=0,
            first_receive_ts_ns=1,
            last_receive_ts_ns=2,
        ).model_dump_json(),
        encoding="utf-8",
    )

    search_args = [
        "run-search",
        "--matrix",
        str(matrix_path),
        "--matrix-manifest",
        str(matrix_manifest_path),
        "--validation-plan",
        str(plan_path),
        "--fold",
        "0",
        "--policy",
        str(policy_path),
        "--engine",
        "lightgbm",
        "--target",
        "next_mid_return_bps",
        "--artifact-root",
        str(artifacts),
        "--artifact-path",
        "models/challenger.txt",
        "--dependency-lock",
        str(project_root / "uv.lock"),
        "--created-at",
        "2026-08-04T00:00:00+00:00",
        "--control-policy",
        str(control_policy_path),
        "--no-signal-report",
        str(no_signal_path),
        "--no-signal-feature-manifest",
        str(feature_manifest_path),
        "--no-signal-strategy-config",
        str(strategy_path),
        "--no-signal-scenario",
        str(scenario_path),
        "--output",
        str(receipt_path),
    ]
    result = main(search_args)
    assert result == 0
    assert capsys.readouterr().out == ""
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["test_rows"] == 10
    assert receipt["walk_forward_test_mse"] < receipt["zero_prediction_test_mse"]
    assert receipt["walk_forward_test_mse"] < receipt["training_mean_test_mse"]
    assert receipt["forecast_robustness_passed"] is True
    assert receipt["forecast_robustness"]["policy"] == control_policy.model_dump(mode="json")
    assert receipt["forecast_economic_performance_passed"] is True
    assert receipt["forecast_economic_passed"] is True
    assert receipt["negative_controls_passed"] is True
    assert receipt["negative_controls"]["randomized_seeds"] == [11, 12, 13]
    manifest_path = Path(receipt["model_manifest"])
    assert manifest_path.is_file()
    assert (artifacts / "models" / "challenger.txt").is_file()

    mismatched_report = no_signal_path.with_name("mismatched-no-signal.json")
    report = NoSignalControlReport.model_validate_json(no_signal_path.read_bytes())
    mismatched_report.write_text(
        report.model_copy(update={"feature_dataset_sha256": "f" * 64}).model_dump_json(),
        encoding="utf-8",
    )
    mismatched_args = list(search_args)
    mismatched_args[mismatched_args.index(str(no_signal_path))] = str(mismatched_report)
    assert main(mismatched_args) == 2
    assert "feature dataset does not match" in capsys.readouterr().err

    assert (
        main(
            [
                "validate-model",
                "--artifact-root",
                str(artifacts),
                "--manifest",
                str(manifest_path),
                "--target",
                "next_mid_return_bps",
            ]
        )
        == 0
    )
    validation = json.loads(capsys.readouterr().out)
    assert validation["status"] == "valid"
    assert validation["engine"] == "lightgbm"


def test_research_cli_rejects_schema_mismatch_and_partial_champion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad_matrix = tmp_path / "bad-matrix.npz"
    bad_matrix_manifest = write_matrix(bad_matrix, schema_hash="f" * 64)
    assert (
        main(
            [
                "run-search",
                "--matrix",
                str(bad_matrix),
                "--matrix-manifest",
                str(bad_matrix_manifest),
                "--validation-plan",
                str(tmp_path / "missing-plan.json"),
                "--fold",
                "0",
                "--policy",
                str(tmp_path / "missing-policy.json"),
                "--engine",
                "lightgbm",
                "--target",
                "next_mid_return_bps",
                "--artifact-root",
                str(tmp_path),
                "--artifact-path",
                "model.txt",
                "--dependency-lock",
                str(tmp_path / "missing.lock"),
                "--created-at",
                "2026-08-04T00:00:00+00:00",
                "--control-policy",
                str(tmp_path / "missing-control-policy.json"),
                "--no-signal-report",
                str(tmp_path / "missing-no-signal.json"),
                "--no-signal-feature-manifest",
                str(tmp_path / "missing-feature-manifest.json"),
                "--no-signal-strategy-config",
                str(tmp_path / "missing-strategy.toml"),
                "--no-signal-scenario",
                str(tmp_path / "missing-scenario.toml"),
            ]
        )
        == 2
    )
    assert "feature schema mismatch" in capsys.readouterr().err

    assert (
        main(
            [
                "evaluate",
                "--challenger-id",
                "challenger",
                "--challenger-metrics",
                str(tmp_path / "missing.json"),
                "--champion-id",
                "champion",
                "--policy",
                str(tmp_path / "missing-policy.json"),
                "--negative-controls",
                str(tmp_path / "missing-controls.json"),
            ]
        )
        == 2
    )
    assert "supplied together" in capsys.readouterr().err


def test_run_search_rejects_matrix_horizon_outside_frozen_validation_plan(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    matrix_path = tmp_path / "matrix.npz"
    matrix_manifest_path = write_matrix(matrix_path)
    plan_path = tmp_path / "validation-plan.json"
    plan_path.write_text(
        validation_plan().model_copy(update={"label_horizon_ns": 6}).model_dump_json(),
        encoding="utf-8",
    )

    assert (
        main(
            [
                "run-search",
                "--matrix",
                str(matrix_path),
                "--matrix-manifest",
                str(matrix_manifest_path),
                "--validation-plan",
                str(plan_path),
                "--fold",
                "0",
                "--policy",
                str(tmp_path / "unused-policy.json"),
                "--engine",
                "lightgbm",
                "--target",
                "next_mid_return_bps",
                "--artifact-root",
                str(tmp_path),
                "--artifact-path",
                "unused.txt",
                "--dependency-lock",
                str(tmp_path / "unused.lock"),
                "--created-at",
                "2026-08-20T00:00:00+00:00",
                "--control-policy",
                str(tmp_path / "unused-control-policy.json"),
                "--no-signal-report",
                str(tmp_path / "unused-no-signal.json"),
                "--no-signal-feature-manifest",
                str(tmp_path / "unused-feature-manifest.json"),
                "--no-signal-strategy-config",
                str(tmp_path / "unused-strategy.toml"),
                "--no-signal-scenario",
                str(tmp_path / "unused-scenario.toml"),
            ]
        )
        == 2
    )
    assert "horizon does not match" in capsys.readouterr().err
