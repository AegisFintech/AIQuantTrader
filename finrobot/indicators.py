from __future__ import annotations

import pandas as pd
import numpy as np

from finrobot.strategies.smart_money import enrich_smart_money
from finrobot.strategies.harmonics import enrich_harmonics


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gains = delta.where(delta > 0, 0.0)
    losses = -delta.where(delta < 0, 0.0)
    rs = gains.rolling(period).mean() / losses.rolling(period).mean()
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=True).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    
    up_move = high.diff()
    down_move = low.diff() * -1
    
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0)
    
    tr = atr(df, period)
    
    plus_di = 100 * (pd.Series(plus_dm, index=df.index).ewm(span=period).mean() / tr)
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).ewm(span=period).mean() / tr)
    
    dx = 100 * (abs(plus_di - minus_di) / (plus_di + minus_di))
    adx_val = dx.ewm(span=period).mean()
    
    return pd.DataFrame({"ADX": adx_val, "+DI": plus_di, "-DI": minus_di})


def ichimoku(df: pd.DataFrame) -> pd.DataFrame:
    tenkan_period = 9
    kijun_period = 26
    senkou_b_period = 52
    
    tenkan_sen = (df["high"].rolling(window=tenkan_period).max() + df["low"].rolling(window=tenkan_period).min()) / 2
    kijun_sen = (df["high"].rolling(window=kijun_period).max() + df["low"].rolling(window=kijun_period).min()) / 2
    
    senkou_span_a = ((tenkan_sen + kijun_sen) / 2).shift(kijun_period)
    senkou_span_b = ((df["high"].rolling(window=senkou_b_period).max() + df["low"].rolling(window=senkou_b_period).min()) / 2).shift(kijun_period)
    
    chikou_span = df["close"].shift(-kijun_period)
    
    return pd.DataFrame({
        "Tenkan_Sen": tenkan_sen,
        "Kijun_Sen": kijun_sen,
        "Senkou_Span_A": senkou_span_a,
        "Senkou_Span_B": senkou_span_b,
        "Chikou_Span": chikou_span
    })


def rsi_divergence(price: pd.Series, rsi: pd.Series, lookback: int = 30) -> pd.Series:
    # Rolling min/max of the prior `lookback` bars (shift(1) excludes the current bar)
    price_rolling_min = price.rolling(lookback).min().shift(1)
    price_rolling_max = price.rolling(lookback).max().shift(1)
    rsi_rolling_min = rsi.rolling(lookback).min().shift(1)
    rsi_rolling_max = rsi.rolling(lookback).max().shift(1)

    # Bullish: price makes new low but RSI does not
    bullish = ((price < price_rolling_min) & (rsi > rsi_rolling_min)).astype(int)

    # Bearish: price makes new high but RSI does not
    bearish = ((price > price_rolling_max) & (rsi < rsi_rolling_max)).astype(int)

    return bullish - bearish


def enrich_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    
    # Existing Indicators
    df["EMA_200"] = df["close"].ewm(span=200, adjust=True).mean()
    df["EMA_50"] = df["close"].ewm(span=50, adjust=True).mean()
    df["SMA_20"] = df["close"].rolling(window=20).mean()
    df["STD_20"] = df["close"].rolling(window=20).std()
    df["Upper_Band"] = df["SMA_20"] + (df["STD_20"] * 2)
    df["Lower_Band"] = df["SMA_20"] - (df["STD_20"] * 2)
    df["Middle_Band"] = df["SMA_20"]
    df["RSI_14"] = rsi(df["close"])
    df["MACD"] = df["close"].ewm(span=12, adjust=True).mean() - df["close"].ewm(span=26, adjust=True).mean()
    df["Signal_Line"] = df["MACD"].ewm(span=9, adjust=True).mean()
    df["MACD_Histogram"] = df["MACD"] - df["Signal_Line"]
    df["Volume_SMA_20"] = df["volume"].rolling(window=20).mean()
    low14 = df["low"].rolling(window=14).min()
    df["%K"] = 100 * ((df["close"] - low14) / (df["high"].rolling(window=14).max() - low14))
    df["%D"] = df["%K"].rolling(window=3).mean()
    df["Support"] = df["low"].rolling(window=20).min()
    df["Resistance"] = df["high"].rolling(window=20).max()
    
    # New Advanced Indicators
    df["ATR_14"] = atr(df)
    adx_data = adx(df)
    df["ADX"] = adx_data["ADX"]
    df["PLUS_DI"] = adx_data["+DI"]
    df["MINUS_DI"] = adx_data["-DI"]
    
    ichimoku_data = ichimoku(df)
    df["Ichimoku_Tenkan"] = ichimoku_data["Tenkan_Sen"]
    df["Ichimoku_Kijun"] = ichimoku_data["Kijun_Sen"]
    df["Ichimoku_Senkou_A"] = ichimoku_data["Senkou_Span_A"]
    df["Ichimoku_Senkou_B"] = ichimoku_data["Senkou_Span_B"]
    df["Ichimoku_Chikou"] = ichimoku_data["Chikou_Span"]
    
    df["RSI_Divergence"] = rsi_divergence(df["close"], df["RSI_14"])
    
    # Smart Money Concepts
    df = enrich_smart_money(df)
    
    # Harmonics & Fibonacci
    df = enrich_harmonics(df)
    
    # Master Entry Score (0-100)
    df["Entry_Score"] = 50
    
    # Trend Strength
    df.loc[df["ADX"] > 25, "Entry_Score"] += 10
    df.loc[df["ADX"] > 40, "Entry_Score"] += 15
    
    # Ichimoku Alignment
    df.loc[df["close"] > df["Ichimoku_Senkou_A"], "Entry_Score"] += 10
    df.loc[df["close"] > df["Ichimoku_Kijun"], "Entry_Score"] += 5
    
    # Smart Money Confirmation
    df.loc[df["Break_Of_Structure"] == 1, "Entry_Score"] += 15
    df.loc[df["Bullish_OB"] == 1, "Entry_Score"] += 10
    df.loc[df["Liquidity_Sweep"] == 1, "Entry_Score"] += 8
    
    # Fibonacci Confluence
    df.loc[abs(df["close"] - df["Fib_618"]) < df["ATR_14"], "Entry_Score"] += 10
    df.loc[abs(df["close"] - df["Fib_500"]) < df["ATR_14"], "Entry_Score"] += 7
    
    # Neutral checks
    df.loc[df["RSI_Divergence"] == -1, "Entry_Score"] -= 20
    df.loc[df["Change_Of_Character"] == -1, "Entry_Score"] -= 25
    
    # Clamp score
    df["Entry_Score"] = df["Entry_Score"].clip(0, 100)
    
    return df
