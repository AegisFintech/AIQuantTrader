#!/usr/bin/env python3
"""Run a Phase 3 M2.1 backtest from a TSV price file."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finrobot.backtest import (  # noqa: E402
    Backtester,
    BacktestConfig,
    BuyAndHold,
    PositionSizer,
    compute_metrics,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a deterministic FinRobot backtest.")
    parser.add_argument("--strategy", choices=("BuyAndHold",), default="BuyAndHold")
    parser.add_argument("--data-path", type=Path, default=ROOT / "data" / "XAUUSD1.csv")
    parser.add_argument("--initial-equity", type=float, default=10000.0)
    parser.add_argument("--risk-per-trade", type=float, default=0.001)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "state" / "research" / "experiments" / _default_run_id(),
    )
    parser.add_argument("--json", action="store_true", help="print MetricsReport as JSON")
    args = parser.parse_args(argv)

    try:
        from finrobot.prices import load_tsv_bars

        bars = list(load_tsv_bars(args.data_path))
        strategy = _build_strategy(args.strategy, risk_per_trade=args.risk_per_trade)
        config = BacktestConfig(
            initial_equity=args.initial_equity,
            sizer=PositionSizer(
                risk_per_trade_fraction=args.risk_per_trade,
                daily_loss_cap_fraction=0.01,
                max_lot_per_trade=5.0,
                max_positions_per_symbol=2,
            ),
        )
        result = Backtester(config).run(strategy=strategy, bars=bars)
        report = compute_metrics(result)
        if args.json:
            print(json.dumps(asdict(report), indent=2, sort_keys=True))
        else:
            _print_summary(result=result, report=report, data_path=args.data_path)
        return 0
    except Exception as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1


def _build_strategy(name: str, *, risk_per_trade: float) -> BuyAndHold:
    if name == "BuyAndHold":
        return BuyAndHold(risk_per_trade_fraction=risk_per_trade)
    raise ValueError(f"unsupported strategy: {name}")


def _print_summary(*, result, report, data_path: Path) -> None:
    print("Backtest summary")
    print(f"  strategy: {result.strategy_name}")
    print(f"  data: {display_path(data_path)}")
    print(f"  bars: {result.bars}")
    print(f"  initial_equity: {result.initial_equity:.2f}")
    print(f"  final_equity: {result.final_equity:.2f}")
    print(f"  total_pnl: {report.total_pnl:.2f}")
    print(f"  n_trades: {report.n_trades}")
    print(f"  win_rate: {report.win_rate:.4f}")
    print(f"  profit_factor: {report.profit_factor}")
    print(f"  max_drawdown: {report.max_drawdown:.2f}")
    print(f"  max_drawdown_pct: {report.max_drawdown_pct:.4f}")
    print(f"  sharpe_ratio: {report.sharpe_ratio}")
    print(f"  rejected_signals: {result.rejected_signals}")


def display_path(path: Path) -> str:
    """Return a repo-relative path when possible."""

    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def _default_run_id() -> str:
    return f"{int(time.time())}-backtest.json"


if __name__ == "__main__":
    raise SystemExit(main())
