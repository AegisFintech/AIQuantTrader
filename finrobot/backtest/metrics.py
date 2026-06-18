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
    max_drawdown_duration_bars: int
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    avg_holding_time_seconds: float
    final_equity: float


def compute_metrics(result: BacktestResult) -> MetricsReport:
    """Compute all standard metrics from a backtest result."""

    trades = list(getattr(result, "trades", []))
    pnls = [_trade_pnl(trade) for trade in trades]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    equity_curve = list(getattr(result, "equity_curve", []))
    drawdown_abs, drawdown_pct = max_drawdown(equity_curve)
    initial = float(getattr(result, "initial_equity", 0.0))
    final = float(getattr(result, "final_equity", 0.0))
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
        max_drawdown_duration_bars=max_drawdown_duration(equity_curve),
        sharpe_ratio=sharpe_ratio(equity_curve),
        sortino_ratio=sortino_ratio(equity_curve),
        calmar_ratio=calmar_ratio(equity_curve, initial_equity=initial, final_equity=final),
        avg_holding_time_seconds=_avg_holding_time(trades),
        final_equity=final,
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


def sortino_ratio(
    equity_curve: list[tuple[int, float]], *, risk_free_rate: float = 0.0
) -> float:
    """Return annualized Sortino ratio (penalises downside volatility only)."""

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
    excess = [r - period_rf for r in returns]
    avg_excess = mean(excess)
    downside = [r for r in excess if r < 0]
    if not downside:
        return math.inf if avg_excess > 0 else 0.0
    downside_std = math.sqrt(sum(r ** 2 for r in downside) / len(downside))
    if downside_std == 0:
        return math.inf if avg_excess > 0 else 0.0
    return avg_excess / downside_std * math.sqrt(M1_PERIODS_PER_YEAR)


def calmar_ratio(
    equity_curve: list[tuple[int, float]],
    *,
    initial_equity: float,
    final_equity: float,
) -> float:
    """Return Calmar ratio: annualized return divided by max drawdown percentage."""

    if len(equity_curve) < 2 or initial_equity <= 0:
        return 0.0
    _, drawdown_pct = max_drawdown(equity_curve)
    if drawdown_pct == 0:
        return math.inf if final_equity > initial_equity else 0.0
    n_bars = len(equity_curve)
    years = n_bars / M1_PERIODS_PER_YEAR
    if years <= 0:
        return 0.0
    ratio = final_equity / initial_equity
    if ratio <= 0:
        return 0.0
    try:
        annualized_return = ratio ** (1.0 / years) - 1.0
    except (OverflowError, ZeroDivisionError):
        return 0.0
    return annualized_return / drawdown_pct


def max_drawdown_duration(equity_curve: list[tuple[int, float]]) -> int:
    """Return the longest streak of bars spent below a prior equity peak."""

    if not equity_curve:
        return 0
    peak = float(equity_curve[0][1])
    current_streak = 0
    longest = 0
    for _, equity in equity_curve:
        value = float(equity)
        if value >= peak:
            peak = value
            current_streak = 0
        else:
            current_streak += 1
            if current_streak > longest:
                longest = current_streak
    return longest


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
