from __future__ import annotations

import pandas as pd
import numpy as np


def detect_order_blocks(df: pd.DataFrame, lookback: int = 10) -> pd.DataFrame:
    bullish_ob = pd.Series(0, index=df.index)
    bearish_ob = pd.Series(0, index=df.index)
    
    for i in range(lookback, len(df)):
        # Bullish Order Block: Last down candle before strong upward move
        if df["close"].iloc[i] > df["high"].iloc[i-2] and df["close"].iloc[i-1] < df["open"].iloc[i-1]:
            bullish_ob.iloc[i-1] = 1
            
        # Bearish Order Block: Last up candle before strong downward move
        if df["close"].iloc[i] < df["low"].iloc[i-2] and df["close"].iloc[i-1] > df["open"].iloc[i-1]:
            bearish_ob.iloc[i-1] = -1
    
    return pd.DataFrame({"Bullish_OB": bullish_ob, "Bearish_OB": bearish_ob})


def detect_liquidity_sweeps(df: pd.DataFrame, lookback: int = 20) -> pd.Series:
    sweeps = pd.Series(0, index=df.index)
    
    for i in range(lookback, len(df)):
        prev_high = df["high"].iloc[i-lookback:i-1].max()
        prev_low = df["low"].iloc[i-lookback:i-1].min()
        
        # Bullish sweep: take out low then close above
        if df["low"].iloc[i] < prev_low and df["close"].iloc[i] > prev_low:
            sweeps.iloc[i] = 1
            
        # Bearish sweep: take out high then close below
        if df["high"].iloc[i] > prev_high and df["close"].iloc[i] < prev_high:
            sweeps.iloc[i] = -1
    
    return sweeps


def detect_fair_value_gaps(df: pd.DataFrame) -> pd.Series:
    fvg = pd.Series(0, index=df.index)
    
    for i in range(2, len(df)):
        # Bullish FVG: Candle 3 low > Candle 1 high
        if df["low"].iloc[i] > df["high"].iloc[i-2]:
            fvg.iloc[i-1] = 1
            
        # Bearish FVG: Candle 3 high < Candle 1 low
        if df["high"].iloc[i] < df["low"].iloc[i-2]:
            fvg.iloc[i-1] = -1
    
    return fvg


def detect_change_of_character(df: pd.DataFrame, lookback: int = 30) -> pd.Series:
    choch = pd.Series(0, index=df.index)
    
    for i in range(lookback, len(df)):
        prev_low = df["low"].iloc[i-lookback:i].min()
        prev_high = df["high"].iloc[i-lookback:i].max()
        
        # Bearish CHoCH: Break previous low after making higher high
        if df["low"].iloc[i] < prev_low and df["high"].iloc[i-5:i].max() > df["high"].iloc[i-lookback:i-5].max():
            choch.iloc[i] = -1
            
        # Bullish CHoCH: Break previous high after making lower low
        if df["high"].iloc[i] > prev_high and df["low"].iloc[i-5:i].min() < df["low"].iloc[i-lookback:i-5].min():
            choch.iloc[i] = 1
    
    return choch


def detect_break_of_structure(df: pd.DataFrame, lookback: int = 15) -> pd.Series:
    bos = pd.Series(0, index=df.index)
    
    for i in range(lookback, len(df)):
        # Bullish BOS: Break recent higher high
        if df["high"].iloc[i] > df["high"].iloc[i-lookback:i-1].max():
            bos.iloc[i] = 1
            
        # Bearish BOS: Break recent lower low
        if df["low"].iloc[i] < df["low"].iloc[i-lookback:i-1].min():
            bos.iloc[i] = -1
    
    return bos


def enrich_smart_money(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    
    ob_data = detect_order_blocks(df)
    df["Bullish_OB"] = ob_data["Bullish_OB"]
    df["Bearish_OB"] = ob_data["Bearish_OB"]
    
    df["Liquidity_Sweep"] = detect_liquidity_sweeps(df)
    df["Fair_Value_Gap"] = detect_fair_value_gaps(df)
    df["Change_Of_Character"] = detect_change_of_character(df)
    df["Break_Of_Structure"] = detect_break_of_structure(df)
    
    return df
