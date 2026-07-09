"""Feature engineering pipeline for ML-driven signal discovery.

All features are computed with strict look-ahead prevention: every feature
at index i uses only data from indices <= i-1 (shifted by 1 bar).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_features(df: pd.DataFrame, *, timeframe: str = "M5") -> pd.DataFrame:
    """Compute the full feature matrix from an OHLCV DataFrame.

    Input must have columns: time, open, high, low, close, volume.
    Returns a DataFrame with the same index, adding feature columns.
    All features are lagged by 1 bar to prevent look-ahead.
    """
    out = pd.DataFrame(index=df.index)

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"] if "volume" in df.columns else pd.Series(0, index=df.index)

    # --- Price returns at multiple horizons ---
    for horizon in [1, 5, 15, 60, 240]:
        out[f"ret_{horizon}"] = close.pct_change(horizon).shift(1)

    # --- Realized volatility (rolling std of returns) ---
    ret1 = close.pct_change()
    for window in [20, 60, 240]:
        out[f"rvol_{window}"] = ret1.rolling(window).std().shift(1)

    # --- Volatility ratio (short vs long) ---
    out["vol_ratio_20_60"] = (
        ret1.rolling(20).std() / ret1.rolling(60).std()
    ).shift(1)

    # --- Range / ATR ---
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr14 = tr.ewm(span=14, adjust=False).mean()
    out["range_atr_ratio"] = ((high - low) / atr14).shift(1)
    out["atr_14"] = atr14.shift(1)

    # --- Return autocorrelation ---
    out["autocorr_20"] = ret1.rolling(20).apply(
        lambda x: x.autocorr() if len(x) > 1 else 0, raw=False
    ).shift(1)

    # --- RSI ---
    delta = close.diff()
    gains = delta.where(delta > 0, 0.0)
    losses = -delta.where(delta < 0, 0.0)
    avg_gain = gains.ewm(span=14, adjust=False).mean()
    avg_loss = losses.ewm(span=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out["rsi_14"] = (100 - (100 / (1 + rs))).shift(1)

    # --- Bollinger %B ---
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    out["bb_pct_b"] = ((close - (sma20 - 2 * std20)) / (4 * std20)).shift(1)

    # --- MACD histogram slope ---
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    out["macd_hist_slope"] = hist.diff(3).shift(1)

    # --- ADX ---
    out["adx_14"] = _adx(high, low, close, period=14).shift(1)

    # --- Keltner channel position ---
    ema20 = close.ewm(span=20, adjust=False).mean()
    kelt_upper = ema20 + 2 * atr14
    kelt_lower = ema20 - 2 * atr14
    kelt_width = kelt_upper - kelt_lower
    out["keltner_pos"] = ((close - kelt_lower) / kelt_width.replace(0, np.nan)).shift(1)

    # --- Volume features ---
    vol_sma20 = volume.rolling(20).mean()
    out["volume_ratio"] = (volume / vol_sma20.replace(0, np.nan)).shift(1)

    # --- Spread z-score (if available) ---
    if "spread" in df.columns:
        spread = df["spread"]
        sp_mean = spread.rolling(100).mean()
        sp_std = spread.rolling(100).std()
        out["spread_zscore"] = ((spread - sp_mean) / sp_std.replace(0, np.nan)).shift(1)

    # --- Time-of-day features (cyclical encoding) ---
    if "time" in df.columns:
        timestamps = pd.to_datetime(df["time"], unit="s", utc=True)
        hour = timestamps.dt.hour + timestamps.dt.minute / 60.0
        out["hour_sin"] = np.sin(2 * np.pi * hour / 24)
        out["hour_cos"] = np.cos(2 * np.pi * hour / 24)
        out["day_of_week"] = timestamps.dt.dayofweek / 6.0

    # --- Multi-timeframe features (H1 approximation from M5) ---
    bars_per_h1 = {"M1": 60, "M5": 12, "M15": 4, "H1": 1}.get(timeframe, 12)
    if bars_per_h1 > 1:
        h1_close = close.rolling(bars_per_h1).apply(lambda x: x.iloc[-1], raw=False)
        ema_h1 = h1_close.ewm(span=20 * bars_per_h1, adjust=False).mean()
        out["h1_ema_slope"] = ema_h1.pct_change(bars_per_h1).shift(1)
        h4_bars = bars_per_h1 * 4
        out["h4_range_pos"] = (
            (close - low.rolling(h4_bars).min())
            / (high.rolling(h4_bars).max() - low.rolling(h4_bars).min()).replace(0, np.nan)
        ).shift(1)

    # --- Daily range position ---
    bars_per_day = {"M1": 1380, "M5": 276, "M15": 92, "H1": 23}.get(timeframe, 276)
    out["daily_range_pos"] = (
        (close - low.rolling(bars_per_day).min())
        / (high.rolling(bars_per_day).max() - low.rolling(bars_per_day).min()).replace(0, np.nan)
    ).shift(1)

    return out


def compute_target(df: pd.DataFrame, *, horizon: int = 5) -> pd.Series:
    """Compute binary target: 1 if close[i+horizon] > close[i], else 0."""
    future_ret = df["close"].pct_change(horizon).shift(-horizon)
    return (future_ret > 0).astype(int)


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
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
