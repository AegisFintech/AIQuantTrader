"""Regime detection using Hidden Markov Models.

Identifies market regimes (trending, ranging, volatile) from price data.
The regime state drives strategy sleeve activation — different strategies
are optimal in different regimes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class RegimeModel:
    """Fitted HMM regime model with labeled states."""

    hmm: Any  # hmmlearn.hmm.GaussianHMM
    state_labels: dict[int, str]  # {0: "trending", 1: "ranging", 2: "volatile"}
    feature_columns: list[str]
    n_states: int = 3


def fit_regime_model(
    df: pd.DataFrame,
    *,
    n_states: int = 3,
    n_iter: int = 100,
    random_state: int = 42,
) -> RegimeModel:
    """Fit an HMM regime model on OHLCV data.

    Returns a RegimeModel with automatically labeled states based on
    the characteristics of each state's emission distribution.
    """
    from hmmlearn.hmm import GaussianHMM

    features = _compute_regime_features(df)
    feature_cols = features.columns.tolist()
    X = features.dropna().values

    model = GaussianHMM(
        n_components=n_states,
        covariance_type="full",
        n_iter=n_iter,
        random_state=random_state,
    )
    model.fit(X)

    state_labels = _label_states(model, feature_cols)

    return RegimeModel(
        hmm=model,
        state_labels=state_labels,
        feature_columns=feature_cols,
        n_states=n_states,
    )


def predict_regime(model: RegimeModel, df: pd.DataFrame) -> pd.Series:
    """Predict regime for each bar. Returns a Series of state labels.

    Bars with insufficient data for feature computation return "unknown".
    """
    features = _compute_regime_features(df)
    valid_mask = features.notna().all(axis=1)
    result = pd.Series("unknown", index=df.index, dtype="object")

    if valid_mask.sum() == 0:
        return result

    X = features.loc[valid_mask].values
    states = model.hmm.predict(X)
    labels = [model.state_labels.get(s, "unknown") for s in states]
    result.loc[valid_mask] = labels
    return result


def regime_stability_score(regimes: pd.Series, *, min_run: int = 12) -> float:
    """Fraction of bars within regime runs of at least `min_run` bars.

    Higher is better — frequent regime flips indicate an unstable model.
    """
    if len(regimes) == 0:
        return 0.0
    runs = (regimes != regimes.shift(1)).cumsum()
    run_lengths = regimes.groupby(runs).transform("count")
    return float((run_lengths >= min_run).mean())


def _compute_regime_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute features for regime classification."""
    close = df["close"]
    high = df["high"]
    low = df["low"]

    ret = close.pct_change()

    features = pd.DataFrame(index=df.index)

    # Realized volatility (20-bar)
    features["rvol_20"] = ret.rolling(20).std()

    # ADX (trend strength)
    features["adx"] = _fast_adx(high, low, close, period=14)

    # Return autocorrelation (mean-reverting vs trending)
    features["autocorr"] = ret.rolling(20).apply(
        lambda x: x.autocorr() if len(x) > 1 else 0, raw=False
    )

    # Range relative to ATR (compression vs expansion)
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=14, adjust=False).mean()
    features["range_atr"] = ((high - low) / atr.replace(0, np.nan))

    # Directional bias (signed EMA slope)
    ema20 = close.ewm(span=20, adjust=False).mean()
    features["ema_slope"] = ema20.pct_change(5)

    return features


def _fast_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    up = high.diff()
    down = -low.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=high.index).ewm(span=period, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=high.index).ewm(span=period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(span=period, adjust=False).mean()


def _label_states(model: Any, feature_cols: list[str]) -> dict[int, str]:
    """Auto-label HMM states based on emission means.

    Heuristic:
    - State with highest mean ADX and abs(ema_slope) → "trending"
    - State with highest mean rvol_20 → "volatile"
    - Remaining → "ranging"
    """
    means = model.means_  # shape (n_states, n_features)
    col_idx = {col: i for i, col in enumerate(feature_cols)}

    adx_idx = col_idx.get("adx", 1)
    rvol_idx = col_idx.get("rvol_20", 0)
    slope_idx = col_idx.get("ema_slope", 4)

    n_states = means.shape[0]
    labels: dict[int, str] = {}

    # Trending: highest ADX * |slope| product
    trend_scores = means[:, adx_idx] * np.abs(means[:, slope_idx])
    trending_state = int(np.argmax(trend_scores))
    labels[trending_state] = "trending"

    # Volatile: highest rvol among remaining
    remaining = [s for s in range(n_states) if s not in labels]
    if remaining:
        vol_scores = [(s, means[s, rvol_idx]) for s in remaining]
        volatile_state = max(vol_scores, key=lambda x: x[1])[0]
        labels[volatile_state] = "volatile"

    # Ranging: everything else
    for s in range(n_states):
        if s not in labels:
            labels[s] = "ranging"

    return labels
