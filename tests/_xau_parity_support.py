from __future__ import annotations

import os
from datetime import date, datetime, time
from pathlib import Path
from datetime import timezone

from aiquanttrader.backtest import (
    BacktestConfig,
    DailyRiskSizer,
    FillConfig,
    XauAtrImpulseStrategy,
    XauGateParams,
    XauGatedParams,
    XauGatedStrategy,
)


ROOT = Path(__file__).resolve().parents[1]
DUCKDB_PATH = ROOT / "data" / "aiquanttrader.duckdb"
XAU_SYMBOL = "XAUUSD"


def load_xau_bars(from_date: str, to_date: str) -> list[dict]:
    """Load non-null XAU M1 bars for a server-date range from DuckDB."""

    import duckdb

    duckdb_path = _duckdb_path()
    if not duckdb_path.exists():
        return []

    start = _broker_wall_epoch_start(from_date)
    end = _broker_wall_epoch_end(to_date)
    con = duckdb.connect(str(duckdb_path), read_only=True)
    try:
        rows = con.execute(
            """
            SELECT ts_server, open, high, low, close, COALESCE(volume, 0.0)
            FROM prices
            WHERE symbol = ?
              AND ts_server >= ?
              AND ts_server <= ?
              AND open IS NOT NULL
              AND high IS NOT NULL
              AND low IS NOT NULL
              AND close IS NOT NULL
            ORDER BY ts_server ASC
            """,
            [XAU_SYMBOL, start, end],
        ).fetchall()
    finally:
        con.close()

    return [
        {
            "time": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
        for row in rows
    ]


def build_xau_strategy() -> XauGatedStrategy:
    # Parity fixture: mirrors EA v1.31 parameters (pre-v1.32 acks).
    # ADX gate disabled so replayed acks from the old EA are not filtered.
    return XauGatedStrategy(
        inner=XauAtrImpulseStrategy(),
        gate_params=XauGatedParams(
            gate_params=XauGateParams(
                smc_lookback=48,
                discount_threshold=0.38,
                premium_threshold=0.62,
                fvg_min_atr_mult=0.15,
                liquidity_sweep_atr_mult=0.10,
            ),
            pda_long_ceiling=0.40,
            pda_short_floor=0.60,
            min_smc_score=3,
            min_bars_between_signals=1,
            enable_adx_gate=False,
        ),
    )


def build_xau_backtest_config(*, max_positions_per_symbol: int = 2) -> BacktestConfig:
    return BacktestConfig(
        symbol=XAU_SYMBOL,
        fill_config=FillConfig(spread_points=5.0, slippage_points=2.0),
        sizer=DailyRiskSizer(
            risk_per_trade_fraction=0.001,
            daily_loss_cap_fraction=0.01,
            max_lot_per_trade=0.10,
            max_positions_per_symbol=max_positions_per_symbol,
            max_lot_per_symbol={XAU_SYMBOL: 0.10},
            high_confluence_lot_multiplier=3.0,
            high_confluence_score=5,
        ),
    )


def decisions_from_trades(trades: list[dict]) -> list[dict]:
    decisions = [
        {
            "bar_idx": int(trade["entry_bar_idx"]),
            "action": str(trade["side"]).upper(),
            "side": str(trade["side"]).upper(),
            "volume": float(trade["volume"]),
            "price": float(trade["entry_price"]),
        }
        for trade in trades
    ]
    return sorted(decisions, key=lambda decision: int(decision["bar_idx"]))


def _broker_wall_epoch_start(value: str) -> int:
    day = date.fromisoformat(value)
    return int(datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp())


def _duckdb_path() -> Path:
    value = os.getenv("AIQUANTTRADER_WAREHOUSE")
    if not value:
        return DUCKDB_PATH
    path = Path(value).expanduser()
    return path if path.is_absolute() else ROOT / path


def _broker_wall_epoch_end(value: str) -> int:
    day = date.fromisoformat(value)
    return int(datetime.combine(day, time.max, tzinfo=timezone.utc).timestamp())
