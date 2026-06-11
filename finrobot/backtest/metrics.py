"""Standard metrics for backtest results."""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, pstdev
from typing import TYPE_CHECKING, Any

from finrobot.backtest.position import Position

if TYPE_CHECKING:
    from finrobot.backtest.engine import BacktestResult

M1_PERIODS_PER_YEAR = 252 * 24 * 60


@dataclass(frozen=True)
class MetricsReport:
    """Summary statistics for one backtest result."""

    n_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    expectancy: float
    total_pnl: float
    max_drawdown: float
    max_drawdown_pct: float
    sharpe_ratio: float
    avg_holding_time_seconds: float
    final_equity: float


def compute_metrics(result: BacktestResult) -> MetricsReport:
    """Compute all standard metrics from a backtest result."""

    trades = list(getattr(result, "trades", []))
    pnls = [_trade_pnl(trade) for trade in trades]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    drawdown_abs, drawdown_pct = max_drawdown(list(getattr(result, "equity_curve", [])))
    return MetricsReport(
        n_trades=len(trades),
        win_rate=win_rate(trades),
        avg_win=mean(wins) if wins else 0.0,
        avg_loss=mean(losses) if losses else 0.0,
        profit_factor=profit_factor(trades),
        expectancy=expectancy(trades),
        total_pnl=sum(pnls),
        max_drawdown=drawdown_abs,
        max_drawdown_pct=drawdown_pct,
        sharpe_ratio=sharpe_ratio(list(getattr(result, "equity_curve", []))),
        avg_holding_time_seconds=_avg_holding_time(trades),
        final_equity=float(getattr(result, "final_equity", 0.0)),
    )


def sharpe_ratio(
    equity_curve: list[tuple[int, float]], *, risk_free_rate: float = 0.0
) -> float:
    """Return annualized Sharpe ratio for an M1 equity curve."""

    if len(equity_curve) < 2:
        return 0.0
    returns: list[float] = []
    for (_, previous), (_, current) in zip(equity_curve, equity_curve[1:]):
        if previous == 0:
            continue
        returns.append((current / previous) - 1.0)
    if not returns:
        return 0.0
    period_rf = float(risk_free_rate) / M1_PERIODS_PER_YEAR
    excess = [value - period_rf for value in returns]
    avg_excess = mean(excess)
    std = pstdev(excess)
    if std == 0:
        if avg_excess > 0:
            return math.inf
        if avg_excess < 0:
            return -math.inf
        return 0.0
    return avg_excess / std * math.sqrt(M1_PERIODS_PER_YEAR)


def max_drawdown(equity_curve: list[tuple[int, float]]) -> tuple[float, float]:
    """Return absolute and percentage peak-to-trough drawdown."""

    if not equity_curve:
        return 0.0, 0.0
    peak = float(equity_curve[0][1])
    worst_abs = 0.0
    worst_pct = 0.0
    for _, equity in equity_curve:
        value = float(equity)
        if value > peak:
            peak = value
        drawdown = peak - value
        drawdown_pct = drawdown / peak if peak > 0 else 0.0
        if drawdown > worst_abs:
            worst_abs = drawdown
            worst_pct = drawdown_pct
    return worst_abs, worst_pct


def win_rate(trades: list[dict]) -> float:
    """Return fraction of trades with positive PnL."""

    if not trades:
        return 0.0
    wins = sum(1 for trade in trades if _trade_pnl(trade) > 0)
    return wins / len(trades)


def profit_factor(trades: list[dict]) -> float:
    """Return gross profit divided by absolute gross loss."""

    wins = [_trade_pnl(trade) for trade in trades if _trade_pnl(trade) > 0]
    losses = [_trade_pnl(trade) for trade in trades if _trade_pnl(trade) < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    if gross_loss == 0:
        return math.inf if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def expectancy(trades: list[dict]) -> float:
    """Return average PnL per trade."""

    if not trades:
        return 0.0
    return sum(_trade_pnl(trade) for trade in trades) / len(trades)


def _trade_pnl(trade: Any) -> float:
    if isinstance(trade, Position):
        return float(trade.current_pnl)
    if isinstance(trade, dict):
        return float(trade.get("pnl", trade.get("profit", 0.0)) or 0.0)
    return float(getattr(trade, "pnl", getattr(trade, "current_pnl", 0.0)) or 0.0)


def _avg_holding_time(trades: list[Any]) -> float:
    durations: list[float] = []
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        entry_time = trade.get("entry_time")
        exit_time = trade.get("exit_time")
        if entry_time is None or exit_time is None:
            continue
        durations.append(max(0.0, float(exit_time) - float(entry_time)))
    return mean(durations) if durations else 0.0
