from __future__ import annotations

import pandas as pd
import numpy as np


def fibonacci_retracement(high: float, low: float) -> dict:
    diff = high - low
    return {
        0.0: low,
        0.236: low + (diff * 0.236),
        0.382: low + (diff * 0.382),
        0.5: low + (diff * 0.5),
        0.618: low + (diff * 0.618),
        0.786: low + (diff * 0.786),
        1.0: high
    }


def fibonacci_extension(high: float, low: float, swing_high: float) -> dict:
    diff = high - low
    return {
        1.272: swing_high + (diff * 0.272),
        1.618: swing_high + (diff * 0.618),
        2.0: swing_high + diff,
        2.618: swing_high + (diff * 1.618),
        3.618: swing_high + (diff * 2.618)
    }


def gann_angle(price: float, bars: int, angle: int = 45) -> float:
    ratio = {
        15: 0.25,
        26.5: 0.5,
        45: 1.0,
        63.5: 2.0,
        75: 4.0,
        82.5: 8.0
    }
    return price + (price * ratio.get(angle, 1.0) * (bars / 100))


def calculate_fib_levels(df: pd.DataFrame, lookback: int = 50) -> pd.DataFrame:
    fib_382 = pd.Series(np.nan, index=df.index)
    fib_500 = pd.Series(np.nan, index=df.index)
    fib_618 = pd.Series(np.nan, index=df.index)
    
    for i in range(lookback, len(df)):
        window_high = df["high"].iloc[i-lookback:i].max()
        window_low = df["low"].iloc[i-lookback:i].min()
        
        levels = fibonacci_retracement(window_high, window_low)
        fib_382.iloc[i] = levels[0.382]
        fib_500.iloc[i] = levels[0.5]
        fib_618.iloc[i] = levels[0.618]
    
    return pd.DataFrame({
        "Fib_382": fib_382,
        "Fib_500": fib_500,
        "Fib_618": fib_618
    })


def enrich_harmonics(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    
    fib_data = calculate_fib_levels(df)
    df["Fib_382"] = fib_data["Fib_382"]
    df["Fib_500"] = fib_data["Fib_500"]
    df["Fib_618"] = fib_data["Fib_618"]
    
    # Price relative to fib levels
    def get_fib_pos(row):
        if pd.isna(row["Fib_382"]):
            return 0
        if row["close"] < row["Fib_382"]:
            return 1
        elif row["close"] < row["Fib_500"]:
            return 2
        elif row["close"] < row["Fib_618"]:
            return 3
        else:
            return 4
            
    df["Fib_Position"] = df.apply(get_fib_pos, axis=1)
    
    return df
