#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiquanttrader.hft import HFTConfig, backtest_hft
from aiquanttrader.backtesting import BacktestConfig, backtest_trend_martingale


def json_default(value: Any) -> Any:
    if isinstance(value, (np.floating, np.integer)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def load_ohlcv(path: Path) -> pd.DataFrame:
    return pd.read_csv(
        path,
        sep="\t",
        header=None,
        names=["time", "open", "high", "low", "close", "tick_volume"],
    )


def compact_stats(stats: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "total_return",
        "max_drawdown",
        "win_rate",
        "num_trades",
        "avg_trade",
        "avg_win",
        "avg_loss",
        "exit_reasons",
        "step_distribution",
    ]
    return {key: stats[key] for key in keys if key in stats}


def verdict(stats: dict[str, Any], min_trades: int) -> str:
    trades = int(stats.get("num_trades") or 0)
    expectancy = float(stats.get("avg_trade", 0.0))
    total_return = float(stats.get("total_return", 0.0))
    if trades < min_trades:
        return "insufficient_trades"
    if expectancy > 0.0 or ("avg_trade" not in stats and total_return > 0.0):
        return "candidate_positive_after_costs"
    return "negative_after_costs"


def main() -> None:
    parser = argparse.ArgumentParser(description="Report local strategy performance on OHLCV history.")
    parser.add_argument("--data", default="data/XAUUSD1.csv", help="Tab-separated OHLCV history file")
    parser.add_argument("--tail", type=int, default=0, help="Only use the last N rows")
    parser.add_argument("--min-trades", type=int, default=30, help="Minimum trades required for a usable sample")
    args = parser.parse_args()

    data_path = Path(args.data)
    df = load_ohlcv(data_path)
    if args.tail > 0:
        df = df.tail(args.tail).reset_index(drop=True)

    hft_stats = backtest_hft(df, HFTConfig(debug=False))
    martingale_stats = backtest_trend_martingale(df, BacktestConfig(debug=False))
    no_martingale_stats = backtest_trend_martingale(df, BacktestConfig(debug=False, multiplier=1.0, adx_threshold=35.0))

    report = {
        "data": str(data_path),
        "rows": len(df),
        "time_start": str(df["time"].iloc[0]) if len(df) else None,
        "time_end": str(df["time"].iloc[-1]) if len(df) else None,
        "strategies": {
            "hft_default": {
                "stats": compact_stats(hft_stats),
                "verdict": verdict(hft_stats, args.min_trades),
            },
            "trend_martingale_default": {
                "stats": compact_stats(martingale_stats),
                "verdict": verdict(martingale_stats, args.min_trades),
            },
            "trend_no_martingale_adx35": {
                "stats": compact_stats(no_martingale_stats),
                "verdict": verdict(no_martingale_stats, args.min_trades),
            },
        },
    }
    print(json.dumps(report, indent=2, default=json_default))


if __name__ == "__main__":
    main()
