"""CPU-only native-format adapters for the three approved tabular engines."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

from aiquanttrader.features.models import FeatureSchema
from aiquanttrader.research.models import (
    CausalTrainingMatrix,
    ForecastTarget,
    ModelEngine,
)


@dataclass(frozen=True, slots=True)
class TrainedModel:
    engine: ModelEngine
    target: ForecastTarget
    feature_schema: FeatureSchema
    parameters: dict[str, int | float | str | bool]
    native_model: Any


class ModelAdapter(Protocol):
    engine: ModelEngine

    def train(
        self,
        matrix: CausalTrainingMatrix,
        *,
        target: ForecastTarget,
        parameters: dict[str, int | float | str | bool],
    ) -> TrainedModel: ...

    def predict(
        self, model: TrainedModel, features: NDArray[np.float64]
    ) -> NDArray[np.float64]: ...

    def save(self, model: TrainedModel, path: Path) -> None: ...

    def load(
        self, path: Path, *, target: ForecastTarget, feature_schema: FeatureSchema
    ) -> TrainedModel: ...


def _validate_matrix(features: NDArray[np.float64], schema: FeatureSchema) -> NDArray[np.float64]:
    values = np.asarray(features, dtype=np.float64)
    if values.ndim != 2 or values.shape[1] != len(schema.features):
        raise ValueError("prediction matrix does not match feature schema")
    if not np.all(np.isfinite(values)):
        raise ValueError("prediction matrix contains non-finite values")
    return values


def _validate_target_labels(matrix: CausalTrainingMatrix, target: ForecastTarget) -> None:
    labels = matrix.labels
    if target is ForecastTarget.PASSIVE_FILL_PROBABILITY:
        if set(np.unique(labels)) != {0.0, 1.0}:
            raise ValueError("fill-probability training requires both binary labels 0 and 1")
    elif target is ForecastTarget.VOLATILITY_REGIME and set(np.unique(labels)) != {
        0.0,
        1.0,
        2.0,
    }:
        raise ValueError("volatility-regime training requires labels 0, 1, and 2")


def _validate_predictions(
    predictions: NDArray[np.float64], rows: int, target: ForecastTarget
) -> NDArray[np.float64]:
    values = np.asarray(predictions, dtype=np.float64)
    if target is ForecastTarget.VOLATILITY_REGIME:
        if values.shape != (rows, 3) or not np.all(np.isfinite(values)):
            raise ValueError("volatility model returned invalid class probabilities")
        if np.any(values < 0) or np.any(values > 1):
            raise ValueError("volatility model returned probabilities outside [0, 1]")
        if not np.allclose(values.sum(axis=1), 1.0, rtol=1e-6, atol=1e-6):
            raise ValueError("volatility model probabilities do not sum to one")
        return np.asarray(np.argmax(values, axis=1), dtype=np.float64)
    if values.shape != (rows,) or not np.all(np.isfinite(values)):
        raise ValueError("model returned invalid predictions")
    if target is ForecastTarget.PASSIVE_FILL_PROBABILITY and (
        np.any(values < 0) or np.any(values > 1)
    ):
        raise ValueError("fill model returned probabilities outside [0, 1]")
    return values


def _integer_parameter(
    parameters: dict[str, int | float | str | bool],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = parameters.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _float_parameter(
    parameters: dict[str, int | float | str | bool],
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    value = parameters.get(name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    converted = float(value)
    if not np.isfinite(converted) or not minimum <= converted <= maximum:
        raise ValueError(f"{name} must be in [{minimum}, {maximum}]")
    return converted


def _reject_unknown(
    parameters: dict[str, int | float | str | bool], allowed: frozenset[str]
) -> None:
    unknown = sorted(parameters.keys() - allowed)
    if unknown:
        raise ValueError(f"unsupported model parameters: {', '.join(unknown)}")


class LightGBMAdapter:
    engine = ModelEngine.LIGHTGBM
    _allowed = frozenset(
        {"num_boost_round", "learning_rate", "num_leaves", "min_data_in_leaf", "max_depth"}
    )

    def train(
        self,
        matrix: CausalTrainingMatrix,
        *,
        target: ForecastTarget,
        parameters: dict[str, int | float | str | bool],
    ) -> TrainedModel:
        import lightgbm as lgb

        _validate_target_labels(matrix, target)
        _reject_unknown(parameters, self._allowed)
        rounds = _integer_parameter(parameters, "num_boost_round", 50, minimum=1, maximum=1_000)
        objective = {
            ForecastTarget.PASSIVE_FILL_PROBABILITY: "binary",
            ForecastTarget.VOLATILITY_REGIME: "multiclass",
        }.get(target, "regression")
        metric = {
            ForecastTarget.PASSIVE_FILL_PROBABILITY: "binary_logloss",
            ForecastTarget.VOLATILITY_REGIME: "multi_logloss",
        }.get(target, "l2")
        params: dict[str, Any] = {
            "objective": objective,
            "metric": metric,
            "verbosity": -1,
            "seed": 0,
            "deterministic": True,
            "force_col_wise": True,
            "num_threads": 1,
            "learning_rate": _float_parameter(
                parameters, "learning_rate", 0.05, minimum=0.0001, maximum=1
            ),
            "num_leaves": _integer_parameter(parameters, "num_leaves", 15, minimum=2, maximum=255),
            "min_data_in_leaf": _integer_parameter(
                parameters, "min_data_in_leaf", 5, minimum=1, maximum=10_000
            ),
            "max_depth": _integer_parameter(parameters, "max_depth", -1, minimum=-1, maximum=32),
        }
        if target is ForecastTarget.VOLATILITY_REGIME:
            params["num_class"] = 3
        dataset = lgb.Dataset(
            matrix.features,
            label=matrix.labels,
            feature_name=list(matrix.feature_schema.names),
            free_raw_data=True,
        )
        booster = lgb.train(params, dataset, num_boost_round=rounds)
        return TrainedModel(self.engine, target, matrix.feature_schema, parameters, booster)

    def predict(self, model: TrainedModel, features: NDArray[np.float64]) -> NDArray[np.float64]:
        values = _validate_matrix(features, model.feature_schema)
        predictions = np.asarray(model.native_model.predict(values), dtype=np.float64)
        return _validate_predictions(predictions, len(values), model.target)

    def save(self, model: TrainedModel, path: Path) -> None:
        model.native_model.save_model(path)

    def load(
        self, path: Path, *, target: ForecastTarget, feature_schema: FeatureSchema
    ) -> TrainedModel:
        import lightgbm as lgb

        booster = lgb.Booster(model_file=path)
        if tuple(booster.feature_name()) != feature_schema.names:
            raise ValueError("LightGBM artifact feature names do not match schema")
        return TrainedModel(self.engine, target, feature_schema, {}, booster)


class XGBoostAdapter:
    engine = ModelEngine.XGBOOST
    _allowed = frozenset(
        {"num_boost_round", "learning_rate", "max_depth", "min_child_weight", "subsample"}
    )

    def train(
        self,
        matrix: CausalTrainingMatrix,
        *,
        target: ForecastTarget,
        parameters: dict[str, int | float | str | bool],
    ) -> TrainedModel:
        import xgboost as xgb

        _validate_target_labels(matrix, target)
        _reject_unknown(parameters, self._allowed)
        rounds = _integer_parameter(parameters, "num_boost_round", 50, minimum=1, maximum=1_000)
        objective = {
            ForecastTarget.PASSIVE_FILL_PROBABILITY: "binary:logistic",
            ForecastTarget.VOLATILITY_REGIME: "multi:softprob",
        }.get(target, "reg:squarederror")
        metric = {
            ForecastTarget.PASSIVE_FILL_PROBABILITY: "logloss",
            ForecastTarget.VOLATILITY_REGIME: "mlogloss",
        }.get(target, "rmse")
        params = {
            "objective": objective,
            "eval_metric": metric,
            "seed": 0,
            "nthread": 1,
            "tree_method": "hist",
            "learning_rate": _float_parameter(
                parameters, "learning_rate", 0.05, minimum=0.0001, maximum=1
            ),
            "max_depth": _integer_parameter(parameters, "max_depth", 4, minimum=1, maximum=32),
            "min_child_weight": _float_parameter(
                parameters, "min_child_weight", 1, minimum=0, maximum=10_000
            ),
            "subsample": _float_parameter(parameters, "subsample", 1, minimum=0.1, maximum=1),
        }
        if target is ForecastTarget.VOLATILITY_REGIME:
            params["num_class"] = 3
        data = xgb.DMatrix(
            matrix.features,
            label=matrix.labels,
            feature_names=list(matrix.feature_schema.names),
        )
        booster = xgb.train(params, data, num_boost_round=rounds)
        return TrainedModel(self.engine, target, matrix.feature_schema, parameters, booster)

    def predict(self, model: TrainedModel, features: NDArray[np.float64]) -> NDArray[np.float64]:
        import xgboost as xgb

        values = _validate_matrix(features, model.feature_schema)
        data = xgb.DMatrix(values, feature_names=list(model.feature_schema.names))
        predictions = np.asarray(model.native_model.predict(data), dtype=np.float64)
        return _validate_predictions(predictions, len(values), model.target)

    def save(self, model: TrainedModel, path: Path) -> None:
        model.native_model.save_model(path)

    def load(
        self, path: Path, *, target: ForecastTarget, feature_schema: FeatureSchema
    ) -> TrainedModel:
        import xgboost as xgb

        booster = xgb.Booster()
        booster.load_model(path)
        names = tuple(booster.feature_names or ())
        if names != feature_schema.names:
            raise ValueError("XGBoost artifact feature names do not match schema")
        return TrainedModel(self.engine, target, feature_schema, {}, booster)


class CatBoostAdapter:
    engine = ModelEngine.CATBOOST
    _allowed = frozenset({"iterations", "learning_rate", "depth", "l2_leaf_reg"})

    def train(
        self,
        matrix: CausalTrainingMatrix,
        *,
        target: ForecastTarget,
        parameters: dict[str, int | float | str | bool],
    ) -> TrainedModel:
        from catboost import CatBoostClassifier, CatBoostRegressor, Pool

        _validate_target_labels(matrix, target)
        _reject_unknown(parameters, self._allowed)
        common: dict[str, Any] = {
            "iterations": _integer_parameter(
                parameters, "iterations", 50, minimum=1, maximum=1_000
            ),
            "learning_rate": _float_parameter(
                parameters, "learning_rate", 0.05, minimum=0.0001, maximum=1
            ),
            "depth": _integer_parameter(parameters, "depth", 4, minimum=1, maximum=16),
            "l2_leaf_reg": _float_parameter(
                parameters, "l2_leaf_reg", 3, minimum=0, maximum=10_000
            ),
            "random_seed": 0,
            "thread_count": 1,
            "allow_writing_files": False,
            "verbose": False,
        }
        model = (
            CatBoostClassifier(
                **common,
                loss_function=(
                    "Logloss" if target is ForecastTarget.PASSIVE_FILL_PROBABILITY else "MultiClass"
                ),
            )
            if target
            in {
                ForecastTarget.PASSIVE_FILL_PROBABILITY,
                ForecastTarget.VOLATILITY_REGIME,
            }
            else CatBoostRegressor(**common, loss_function="RMSE")
        )
        pool = Pool(
            matrix.features,
            label=matrix.labels,
            feature_names=list(matrix.feature_schema.names),
        )
        model.fit(pool)
        return TrainedModel(self.engine, target, matrix.feature_schema, parameters, model)

    def predict(self, model: TrainedModel, features: NDArray[np.float64]) -> NDArray[np.float64]:
        values = _validate_matrix(features, model.feature_schema)
        if model.target in {
            ForecastTarget.PASSIVE_FILL_PROBABILITY,
            ForecastTarget.VOLATILITY_REGIME,
        }:
            probabilities = np.asarray(model.native_model.predict_proba(values), dtype=np.float64)
            predictions = (
                probabilities[:, 1]
                if model.target is ForecastTarget.PASSIVE_FILL_PROBABILITY
                else probabilities
            )
        else:
            predictions = np.asarray(model.native_model.predict(values), dtype=np.float64)
        return _validate_predictions(predictions, len(values), model.target)

    def save(self, model: TrainedModel, path: Path) -> None:
        model.native_model.save_model(path, format="cbm")

    def load(
        self, path: Path, *, target: ForecastTarget, feature_schema: FeatureSchema
    ) -> TrainedModel:
        from catboost import CatBoostClassifier, CatBoostRegressor

        model = (
            CatBoostClassifier()
            if target
            in {
                ForecastTarget.PASSIVE_FILL_PROBABILITY,
                ForecastTarget.VOLATILITY_REGIME,
            }
            else CatBoostRegressor()
        )
        model.load_model(path, format="cbm")
        if tuple(model.feature_names_) != feature_schema.names:
            raise ValueError("CatBoost artifact feature names do not match schema")
        return TrainedModel(self.engine, target, feature_schema, {}, model)


def adapter_for(engine: ModelEngine) -> ModelAdapter:
    if engine is ModelEngine.LIGHTGBM:
        return LightGBMAdapter()
    if engine is ModelEngine.XGBOOST:
        return XGBoostAdapter()
    if engine is ModelEngine.CATBOOST:
        return CatBoostAdapter()
    raise ValueError(f"unsupported model engine: {engine}")
