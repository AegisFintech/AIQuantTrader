from __future__ import annotations

from datetime import date, datetime, time, timezone
from pathlib import Path

from finrobot.backtest import (
    BacktestConfig,
    DailyRiskSizer,
    FillConfig,
    XauAtrImpulseStrategy,
    XauGateParams,
    XauGatedParams,
    XauGatedStrategy,
)


ROOT = Path(__file__).resolve().parents[1]
DUCKDB_PATH = ROOT / "data" / "finrobot.duckdb"
XAU_SYMBOL = "XAUUSD"


def load_xau_bars(from_date: str, to_date: str) -> list[dict]:
    """Load non-null XAU M1 bars for a server-date range from DuckDB."""

    import duckdb

    if not DUCKDB_PATH.exists():
        return []

    start = _utc_epoch_start(from_date)
    end = _utc_epoch_end(to_date)
    con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
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
            "time": _server_epoch_as_local_wall_epoch(int(row[0])),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
        for row in rows
    ]


def build_xau_strategy() -> XauGatedStrategy:
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
        ),
    )


def build_xau_backtest_config() -> BacktestConfig:
    return BacktestConfig(
        symbol=XAU_SYMBOL,
        fill_config=FillConfig(spread_points=5.0, slippage_points=2.0),
        sizer=DailyRiskSizer(
            risk_per_trade_fraction=0.001,
            daily_loss_cap_fraction=0.01,
            max_lot_per_trade=0.10,
            max_positions_per_symbol=2,
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


def _utc_epoch_start(value: str) -> int:
    day = date.fromisoformat(value)
    return int(datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp())


def _utc_epoch_end(value: str) -> int:
    day = date.fromisoformat(value)
    return int(datetime.combine(day, time.max, tzinfo=timezone.utc).timestamp())


def _server_epoch_as_local_wall_epoch(epoch: int) -> int:
    server_wall = datetime.fromtimestamp(int(epoch), timezone.utc).replace(tzinfo=None)
    return int(server_wall.timestamp())
