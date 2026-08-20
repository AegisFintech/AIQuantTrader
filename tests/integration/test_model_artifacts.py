from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest

from aiquanttrader.features.models import (
    MODEL_FEATURE_SCHEMA,
    FeatureDefinition,
    FeatureSchema,
    VolatilityRegime,
)
from aiquanttrader.research.artifacts import load_model_artifact, save_model_artifact
from aiquanttrader.research.model_adapters import _canonicalize_catboost_json, adapter_for
from aiquanttrader.research.models import (
    CausalTrainingMatrix,
    ForecastTarget,
    ModelEngine,
)


def matrix(rows: int = 48) -> CausalTrainingMatrix:
    rng = np.random.default_rng(7)
    features = rng.normal(size=(rows, len(MODEL_FEATURE_SCHEMA.features))).astype(np.float64)
    labels = (features[:, 0] * 2 - features[:, 1] + 0.1).astype(np.float64)
    timestamps = np.arange(rows, dtype=np.int64) * 1_000 + 1_000
    return CausalTrainingMatrix(
        features=features,
        labels=labels,
        sample_ts_ns=timestamps,
        label_end_ts_ns=timestamps + 100,
        volatility_regimes=np.asarray(
            [
                (VolatilityRegime.LOW, VolatilityRegime.NORMAL, VolatilityRegime.HIGH)[
                    index % 3
                ].value
                for index in range(rows)
            ]
        ),
        feature_schema=MODEL_FEATURE_SCHEMA,
        source_dataset_sha256="a" * 64,
    )


def classification_matrix(target: ForecastTarget, rows: int = 48) -> CausalTrainingMatrix:
    source = matrix(rows)
    labels = (
        np.arange(rows, dtype=np.float64) % 2
        if target is ForecastTarget.PASSIVE_FILL_PROBABILITY
        else np.arange(rows, dtype=np.float64) % 3
    )
    return CausalTrainingMatrix(
        features=source.features,
        labels=labels,
        sample_ts_ns=source.sample_ts_ns,
        label_end_ts_ns=source.label_end_ts_ns,
        volatility_regimes=source.volatility_regimes,
        feature_schema=source.feature_schema,
        source_dataset_sha256=source.source_dataset_sha256,
    )


@pytest.mark.parametrize(
    ("engine", "parameters", "suffix"),
    [
        (
            ModelEngine.LIGHTGBM,
            {"num_boost_round": 5, "num_leaves": 7, "min_data_in_leaf": 2},
            ".txt",
        ),
        (ModelEngine.XGBOOST, {"num_boost_round": 5, "max_depth": 2}, ".json"),
        (ModelEngine.CATBOOST, {"iterations": 5, "depth": 2}, ".json"),
    ],
)
def test_native_model_formats_round_trip_without_pickle(
    tmp_path: Path,
    engine: ModelEngine,
    parameters: dict[str, int | float | str | bool],
    suffix: str,
) -> None:
    training = matrix()
    adapter = adapter_for(engine)
    model = adapter.train(
        training,
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        parameters=parameters,
    )
    expected = adapter.predict(model, training.features[:5])
    manifest_path, manifest = save_model_artifact(
        model,
        artifact_root=tmp_path,
        relative_path=f"models/{engine.value}{suffix}",
        training_dataset_sha256=training.sha256(),
        training_window_sha256="b" * 64,
        dependency_lock_sha256="c" * 64,
        created_at=datetime(2026, 8, 4, tzinfo=UTC),
    )
    loaded = load_model_artifact(
        artifact_root=tmp_path,
        manifest_path=manifest_path,
        feature_schema=MODEL_FEATURE_SCHEMA,
        expected_target=ForecastTarget.NEXT_MID_RETURN_BPS,
    )
    actual = adapter_for(engine).predict(loaded, training.features[:5])
    np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-12)
    assert manifest.relative_path.endswith(suffix)
    assert manifest.artifact_sha256 != "0" * 64


def test_catboost_artifact_and_identity_are_reproducible_across_independent_fits(
    tmp_path: Path,
) -> None:
    training = matrix()
    adapter = adapter_for(ModelEngine.CATBOOST)
    manifests = []
    artifacts = []
    for run in range(2):
        model = adapter.train(
            training,
            target=ForecastTarget.NEXT_MID_RETURN_BPS,
            parameters={"iterations": 5, "depth": 2},
        )
        artifact_root = tmp_path / f"run-{run}"
        _, manifest = save_model_artifact(
            model,
            artifact_root=artifact_root,
            relative_path="models/fold-0.json",
            training_dataset_sha256=training.sha256(),
            training_window_sha256="b" * 64,
            dependency_lock_sha256="c" * 64,
            created_at=datetime(2026, 8, 20, tzinfo=UTC),
        )
        manifests.append(manifest)
        artifacts.append((artifact_root / manifest.relative_path).read_bytes())

    assert artifacts[0] == artifacts[1]
    assert manifests[0].artifact_sha256 == manifests[1].artifact_sha256
    assert manifests[0].model_id == manifests[1].model_id
    assert manifests[0].canonical_bytes() == manifests[1].canonical_bytes()


def test_catboost_canonicalization_removes_wall_clock_metadata(tmp_path: Path) -> None:
    training = matrix()
    model = adapter_for(ModelEngine.CATBOOST).train(
        training,
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        parameters={"iterations": 2, "depth": 2},
    )
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    model.native_model.save_model(first, format="json")
    original = first.read_bytes()
    metadata = json.loads(original)["model_info"]
    second.write_bytes(
        original.replace(metadata["model_guid"].encode("ascii"), b"1-2-3-4", 1).replace(
            metadata["train_finish_time"].encode("ascii"),
            b"2000-01-01T00:00:00Z",
            1,
        )
    )

    _canonicalize_catboost_json(first)
    _canonicalize_catboost_json(second)

    assert first.read_bytes() == second.read_bytes()
    metadata = json.loads(first.read_bytes())["model_info"]
    assert metadata["train_finish_time"] == "1970-01-01T00:00:00Z"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "missing required metadata"),
        (
            {"model_info": {"model_guid": "invalid", "train_finish_time": "2026-08-20T00:00:00Z"}},
            "invalid model GUID",
        ),
        (
            {"model_info": {"model_guid": "1-2-3-4", "train_finish_time": "invalid"}},
            "invalid training finish time",
        ),
        (
            {
                "model_info": {
                    "model_guid": "1-2-3-4",
                    "train_finish_time": "2026-08-20T00:00:00Z",
                },
                "duplicate": "1-2-3-4",
            },
            "model GUID is not unique",
        ),
        (
            {
                "model_info": {
                    "model_guid": "1-2-3-4",
                    "train_finish_time": "2026-08-20T00:00:00Z",
                },
                "duplicate": "2026-08-20T00:00:00Z",
            },
            "training finish time is not unique",
        ),
        (
            {
                "model_info": {
                    "model_guid": "1-2-3-4",
                    "train_finish_time": "2026-08-20T00:00:00Z",
                },
                "collision": "00000000-00000000-00000000-00000000",
            },
            "normalized model GUID is not unique",
        ),
    ],
)
def test_catboost_canonicalization_fails_closed_on_volatile_metadata(
    tmp_path: Path, payload: dict[str, object], message: str
) -> None:
    artifact = tmp_path / "model.json"
    artifact.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match=message):
        _canonicalize_catboost_json(artifact)


@pytest.mark.parametrize("engine", list(ModelEngine))
@pytest.mark.parametrize(
    "target",
    [ForecastTarget.PASSIVE_FILL_PROBABILITY, ForecastTarget.VOLATILITY_REGIME],
)
def test_every_engine_uses_target_appropriate_classification_probabilities(
    engine: ModelEngine, target: ForecastTarget
) -> None:
    parameters: dict[ModelEngine, dict[str, int | float | str | bool]] = {
        ModelEngine.LIGHTGBM: {"num_boost_round": 3, "min_data_in_leaf": 2},
        ModelEngine.XGBOOST: {"num_boost_round": 3, "max_depth": 2},
        ModelEngine.CATBOOST: {"iterations": 3, "depth": 2},
    }
    training = classification_matrix(target)
    adapter = adapter_for(engine)
    model = adapter.train(training, target=target, parameters=parameters[engine])
    predictions = adapter.predict(model, training.features)
    assert predictions.shape == (len(training.labels),)
    if target is ForecastTarget.PASSIVE_FILL_PROBABILITY:
        assert np.all((predictions >= 0) & (predictions <= 1))
    else:
        assert set(np.unique(predictions)) <= {0.0, 1.0, 2.0}


def test_model_loading_fails_closed_on_schema_hash_target_and_artifact_tamper(
    tmp_path: Path,
) -> None:
    training = matrix()
    adapter = adapter_for(ModelEngine.LIGHTGBM)
    model = adapter.train(
        training,
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        parameters={"num_boost_round": 2, "min_data_in_leaf": 2},
    )
    manifest_path, manifest = save_model_artifact(
        model,
        artifact_root=tmp_path,
        relative_path="models/reference.txt",
        training_dataset_sha256=training.sha256(),
        training_window_sha256="b" * 64,
        dependency_lock_sha256="c" * 64,
        created_at=datetime(2026, 8, 4, tzinfo=UTC),
    )
    wrong_schema = FeatureSchema(
        feature_set="wrong-v1",
        features=(*MODEL_FEATURE_SCHEMA.features, FeatureDefinition(name="unexpected")),
    )
    with pytest.raises(ValueError, match="schema mismatch"):
        load_model_artifact(
            artifact_root=tmp_path,
            manifest_path=manifest_path,
            feature_schema=wrong_schema,
        )
    with pytest.raises(ValueError, match="target mismatch"):
        load_model_artifact(
            artifact_root=tmp_path,
            manifest_path=manifest_path,
            feature_schema=MODEL_FEATURE_SCHEMA,
            expected_target=ForecastTarget.SPREAD_EXPANSION_BPS,
        )

    artifact = tmp_path / manifest.relative_path
    artifact.write_bytes(artifact.read_bytes() + b"tamper")
    with pytest.raises(ValueError, match="byte count mismatch"):
        load_model_artifact(
            artifact_root=tmp_path,
            manifest_path=manifest_path,
            feature_schema=MODEL_FEATURE_SCHEMA,
        )


def test_model_adapters_reject_unknown_unbounded_and_unsafe_paths(tmp_path: Path) -> None:
    training = matrix()
    adapter = adapter_for(ModelEngine.LIGHTGBM)
    with pytest.raises(ValueError, match="unsupported"):
        adapter.train(
            training,
            target=ForecastTarget.NEXT_MID_RETURN_BPS,
            parameters={"arbitrary_callback": "payload"},
        )
    with pytest.raises(ValueError, match="binary labels"):
        adapter.train(
            training,
            target=ForecastTarget.PASSIVE_FILL_PROBABILITY,
            parameters={"num_boost_round": 2},
        )
    with pytest.raises(ValueError, match=r"\[1, 1000\]"):
        adapter.train(
            training,
            target=ForecastTarget.NEXT_MID_RETURN_BPS,
            parameters={"num_boost_round": 10_000},
        )
    model = adapter.train(
        training,
        target=ForecastTarget.NEXT_MID_RETURN_BPS,
        parameters={"num_boost_round": 2, "min_data_in_leaf": 2},
    )
    with pytest.raises(ValueError, match=r"must end with \.txt"):
        save_model_artifact(
            model,
            artifact_root=tmp_path,
            relative_path="model.pkl",
            training_dataset_sha256=training.sha256(),
            training_window_sha256="b" * 64,
            dependency_lock_sha256="c" * 64,
            created_at=datetime(2026, 8, 4, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="safe and relative"):
        save_model_artifact(
            model,
            artifact_root=tmp_path,
            relative_path="../model.txt",
            training_dataset_sha256=training.sha256(),
            training_window_sha256="b" * 64,
            dependency_lock_sha256="c" * 64,
            created_at=datetime(2026, 8, 4, tzinfo=UTC),
        )
