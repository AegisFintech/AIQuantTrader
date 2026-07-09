"""ML signal model pipeline using LightGBM.

Walk-forward retraining with expanding window. Produces probability
outputs used as signal confidence for position sizing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class ModelResult:
    """Result of a single walk-forward fold."""

    fold: int
    train_size: int
    test_size: int
    accuracy: float
    auc: float
    precision: float
    recall: float
    feature_importance: dict[str, float]
    predictions: np.ndarray
    probabilities: np.ndarray
    test_indices: np.ndarray


@dataclass
class WalkForwardModelResult:
    """Aggregate results across all walk-forward folds."""

    folds: list[ModelResult]
    mean_auc: float
    mean_accuracy: float
    all_predictions: np.ndarray
    all_probabilities: np.ndarray
    all_test_indices: np.ndarray
    feature_importance_agg: dict[str, float]
    model: Any = None  # final trained model


def train_walkforward(
    features: pd.DataFrame,
    target: pd.Series,
    *,
    n_folds: int = 5,
    min_train_size: int = 5000,
    purge_bars: int = 60,
    embargo_bars: int = 12,
    params: dict | None = None,
) -> WalkForwardModelResult:
    """Train LightGBM with purged walk-forward cross-validation.

    Uses expanding window: each fold's training set includes all data
    from the start up to the fold boundary (minus purge/embargo).
    """
    import lightgbm as lgb
    from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score

    if params is None:
        params = _default_params()

    valid_mask = features.notna().all(axis=1) & target.notna()
    X = features.loc[valid_mask].values
    y = target.loc[valid_mask].values
    indices = np.where(valid_mask)[0]

    n_samples = len(X)
    fold_size = (n_samples - min_train_size) // n_folds

    if fold_size < 100:
        raise ValueError(
            f"Insufficient data: {n_samples} samples, need at least "
            f"{min_train_size + 100 * n_folds} for {n_folds} folds"
        )

    folds: list[ModelResult] = []
    all_preds = []
    all_probs = []
    all_idxs = []
    importance_accum: dict[str, float] = {}
    feature_names = features.columns.tolist()

    final_model = None

    for fold_idx in range(n_folds):
        test_start = min_train_size + fold_idx * fold_size
        test_end = test_start + fold_size if fold_idx < n_folds - 1 else n_samples

        train_end = test_start - purge_bars
        test_start_actual = test_start + embargo_bars

        if train_end <= 0 or test_start_actual >= test_end:
            continue

        X_train, y_train = X[:train_end], y[:train_end]
        X_test, y_test = X[test_start_actual:test_end], y[test_start_actual:test_end]
        test_idx = indices[test_start_actual:test_end]

        if len(X_train) < 100 or len(X_test) < 50:
            continue

        dtrain = lgb.Dataset(X_train, label=y_train, feature_name=feature_names)
        dval = lgb.Dataset(X_test, label=y_test, feature_name=feature_names, reference=dtrain)

        model = lgb.train(
            params,
            dtrain,
            num_boost_round=500,
            valid_sets=[dval],
            callbacks=[lgb.early_stopping(50, verbose=False), lgb.log_evaluation(0)],
        )
        final_model = model

        probs = model.predict(X_test)
        preds = (probs >= 0.5).astype(int)

        auc = roc_auc_score(y_test, probs) if len(np.unique(y_test)) > 1 else 0.5
        acc = accuracy_score(y_test, preds)
        prec = precision_score(y_test, preds, zero_division=0)
        rec = recall_score(y_test, preds, zero_division=0)

        imp = dict(zip(feature_names, model.feature_importance(importance_type="gain")))
        for k, v in imp.items():
            importance_accum[k] = importance_accum.get(k, 0) + v

        fold_result = ModelResult(
            fold=fold_idx,
            train_size=len(X_train),
            test_size=len(X_test),
            accuracy=acc,
            auc=auc,
            precision=prec,
            recall=rec,
            feature_importance=imp,
            predictions=preds,
            probabilities=probs,
            test_indices=test_idx,
        )
        folds.append(fold_result)
        all_preds.append(preds)
        all_probs.append(probs)
        all_idxs.append(test_idx)

    if not folds:
        raise ValueError("No valid folds produced — insufficient data")

    n_folds_actual = len(folds)
    for k in importance_accum:
        importance_accum[k] /= n_folds_actual

    return WalkForwardModelResult(
        folds=folds,
        mean_auc=float(np.mean([f.auc for f in folds])),
        mean_accuracy=float(np.mean([f.accuracy for f in folds])),
        all_predictions=np.concatenate(all_preds),
        all_probabilities=np.concatenate(all_probs),
        all_test_indices=np.concatenate(all_idxs),
        feature_importance_agg=importance_accum,
        model=final_model,
    )


def predict_signal(
    model: Any,
    features: pd.DataFrame,
    *,
    threshold_long: float = 0.60,
    threshold_short: float = 0.40,
) -> pd.Series:
    """Generate signals from a trained model.

    Returns: Series with values in {"BUY", "SELL", "HOLD"}.
    BUY when P(up) > threshold_long, SELL when P(up) < threshold_short.
    """
    valid_mask = features.notna().all(axis=1)
    result = pd.Series("HOLD", index=features.index, dtype="object")

    if valid_mask.sum() == 0:
        return result

    X = features.loc[valid_mask].values
    probs = model.predict(X)

    signals = np.where(probs > threshold_long, "BUY",
              np.where(probs < threshold_short, "SELL", "HOLD"))
    result.loc[valid_mask] = signals
    return result


def signal_confidence(model: Any, features: pd.DataFrame) -> pd.Series:
    """Return raw model confidence: P(up) for each bar.

    Values near 0.5 = uncertain, near 0 or 1 = confident.
    """
    valid_mask = features.notna().all(axis=1)
    result = pd.Series(0.5, index=features.index)

    if valid_mask.sum() == 0:
        return result

    X = features.loc[valid_mask].values
    result.loc[valid_mask] = model.predict(X)
    return result


def _default_params() -> dict:
    return {
        "objective": "binary",
        "metric": "auc",
        "learning_rate": 0.05,
        "num_leaves": 31,
        "max_depth": 6,
        "min_child_samples": 50,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "reg_alpha": 0.1,
        "reg_lambda": 1.0,
        "verbose": -1,
        "seed": 42,
    }
