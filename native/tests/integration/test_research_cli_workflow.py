from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from aiquanttrader_native.backtest.models import (
    TimeWindow,
    ValidationPlan,
    WalkForwardFold,
    WindowRole,
)
from aiquanttrader_native.features.models import MODEL_FEATURE_SCHEMA
from aiquanttrader_native.research.cli import main
from aiquanttrader_native.research.models import (
    NoSignalControlReport,
    SearchPolicy,
    SearchTrial,
)

SOURCE_DATASET_SHA256 = "a" * 64


def validation_plan() -> ValidationPlan:
    return ValidationPlan(
        policy_sha256="b" * 64,
        dataset_sha256=SOURCE_DATASET_SHA256,
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


def write_matrix(path: Path, *, schema_hash: str | None = None) -> None:
    rows = 60
    features = np.zeros((rows, len(MODEL_FEATURE_SCHEMA.features)), dtype=np.float64)
    features[:, 0] = np.arange(rows, dtype=np.float64) / 10
    labels = features[:, 0] * 2
    timestamps = np.arange(rows, dtype=np.int64) * 10
    np.savez(
        path,
        features=features,
        labels=labels,
        sample_ts_ns=timestamps,
        label_end_ts_ns=timestamps + 5,
        feature_schema_sha256=(schema_hash or MODEL_FEATURE_SCHEMA.sha256()),
        source_dataset_sha256=SOURCE_DATASET_SHA256,
    )


def test_run_search_writes_reproducible_native_artifact_and_validates_it(
    tmp_path: Path,
    project_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    matrix_path = tmp_path / "matrix.npz"
    plan_path = tmp_path / "validation-plan.json"
    policy_path = tmp_path / "search-policy.json"
    receipt_path = tmp_path / "search-receipt.json"
    no_signal_path = tmp_path / "no-signal.json"
    artifacts = tmp_path / "artifacts"
    write_matrix(matrix_path)
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
    no_signal_path.write_text(
        NoSignalControlReport(
            feature_dataset_sha256="c" * 64,
            strategy_configuration_sha256="d" * 64,
            scenario_sha256="e" * 64,
            observation_count=1_000,
            decision_count=0,
        ).model_dump_json(),
        encoding="utf-8",
    )

    result = main(
        [
            "run-search",
            "--matrix",
            str(matrix_path),
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
            "--randomized-label-minimum-mse",
            "0",
            "--randomized-seed",
            "11",
            "--no-signal-report",
            str(no_signal_path),
            "--output",
            str(receipt_path),
        ]
    )
    assert result == 0
    assert capsys.readouterr().out == ""
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["test_rows"] == 10
    assert receipt["negative_controls_passed"] is True
    manifest_path = Path(receipt["model_manifest"])
    assert manifest_path.is_file()
    assert (artifacts / "models" / "challenger.txt").is_file()

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
    write_matrix(bad_matrix, schema_hash="f" * 64)
    assert (
        main(
            [
                "run-search",
                "--matrix",
                str(bad_matrix),
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
                "--randomized-label-minimum-mse",
                "0",
                "--no-signal-report",
                str(tmp_path / "missing-no-signal.json"),
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
