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

PERIODS_PER_YEAR = {
    "M1": 252 * 23 * 60,
    "M5": 252 * 23 * 12,
    "M15": 252 * 23 * 4,
    "H1": 252 * 23,
    "H4": 252 * 6,
    "D1": 252,
}


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
    max_consecutive_losses: int = 0
    recovery_factor: float = 0.0
    tail_ratio: float = 0.0
    pnl_skewness: float = 0.0
    pnl_kurtosis: float = 0.0
    monthly_pnl: dict = None  # type: ignore[assignment]


def compute_metrics(
    result: BacktestResult, *, timeframe: str = "M1"
) -> MetricsReport:
    """Compute all standard metrics from a backtest result.

    The *timeframe* parameter controls annualization (default M1).
    Accepted values: M1, M5, M15, H1, H4, D1.
    """

    periods = PERIODS_PER_YEAR.get(timeframe, M1_PERIODS_PER_YEAR)
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
        sharpe_ratio=sharpe_ratio(equity_curve, periods_per_year=periods),
        sortino_ratio=sortino_ratio(equity_curve, periods_per_year=periods),
        calmar_ratio=calmar_ratio(
            equity_curve, initial_equity=initial, final_equity=final,
            periods_per_year=periods,
        ),
        avg_holding_time_seconds=_avg_holding_time(trades),
        final_equity=final,
        max_consecutive_losses=_max_consecutive_losses(pnls),
        recovery_factor=_recovery_factor(sum(pnls), drawdown_abs),
        tail_ratio=_tail_ratio(pnls),
        pnl_skewness=_skewness(pnls),
        pnl_kurtosis=_kurtosis(pnls),
        monthly_pnl=_monthly_pnl(trades),
    )


def sharpe_ratio(
    equity_curve: list[tuple[int, float]],
    *,
    risk_free_rate: float = 0.0,
    periods_per_year: int = M1_PERIODS_PER_YEAR,
) -> float:
    """Return annualized Sharpe ratio."""

    if len(equity_curve) < 2:
        return 0.0
    returns: list[float] = []
    for (_, previous), (_, current) in zip(equity_curve, equity_curve[1:]):
        if previous == 0:
            continue
        returns.append((current / previous) - 1.0)
    if not returns:
        return 0.0
    period_rf = float(risk_free_rate) / periods_per_year
    excess = [value - period_rf for value in returns]
    avg_excess = mean(excess)
    std = pstdev(excess)
    if std == 0:
        if avg_excess > 0:
            return math.inf
        if avg_excess < 0:
            return -math.inf
        return 0.0
    return avg_excess / std * math.sqrt(periods_per_year)


def sortino_ratio(
    equity_curve: list[tuple[int, float]],
    *,
    risk_free_rate: float = 0.0,
    periods_per_year: int = M1_PERIODS_PER_YEAR,
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
    period_rf = float(risk_free_rate) / periods_per_year
    excess = [r - period_rf for r in returns]
    avg_excess = mean(excess)
    downside = [r for r in excess if r < 0]
    if not downside:
        return math.inf if avg_excess > 0 else 0.0
    downside_std = math.sqrt(sum(r ** 2 for r in downside) / len(downside))
    if downside_std == 0:
        return math.inf if avg_excess > 0 else 0.0
    return avg_excess / downside_std * math.sqrt(periods_per_year)


def calmar_ratio(
    equity_curve: list[tuple[int, float]],
    *,
    initial_equity: float,
    final_equity: float,
    periods_per_year: int = M1_PERIODS_PER_YEAR,
) -> float:
    """Return Calmar ratio: annualized return divided by max drawdown percentage."""

    if len(equity_curve) < 2 or initial_equity <= 0:
        return 0.0
    _, drawdown_pct = max_drawdown(equity_curve)
    if drawdown_pct == 0:
        return math.inf if final_equity > initial_equity else 0.0
    n_bars = len(equity_curve)
    years = n_bars / periods_per_year
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


def _max_consecutive_losses(pnls: list[float]) -> int:
    longest = 0
    current = 0
    for pnl in pnls:
        if pnl < 0:
            current += 1
            if current > longest:
                longest = current
        else:
            current = 0
    return longest


def _recovery_factor(total_pnl: float, max_dd: float) -> float:
    if max_dd == 0:
        return math.inf if total_pnl > 0 else 0.0
    return total_pnl / max_dd


def _tail_ratio(pnls: list[float]) -> float:
    if len(pnls) < 20:
        return 0.0
    sorted_pnls = sorted(pnls)
    n = len(sorted_pnls)
    idx_95 = int(n * 0.95)
    idx_05 = int(n * 0.05)
    upper = sorted_pnls[min(idx_95, n - 1)]
    lower = sorted_pnls[idx_05]
    if lower == 0:
        return math.inf if upper > 0 else 0.0
    return abs(upper / lower)


def _skewness(pnls: list[float]) -> float:
    if len(pnls) < 3:
        return 0.0
    n = len(pnls)
    mu = sum(pnls) / n
    m2 = sum((x - mu) ** 2 for x in pnls) / n
    m3 = sum((x - mu) ** 3 for x in pnls) / n
    if m2 == 0:
        return 0.0
    return m3 / (m2 ** 1.5)


def _kurtosis(pnls: list[float]) -> float:
    if len(pnls) < 4:
        return 0.0
    n = len(pnls)
    mu = sum(pnls) / n
    m2 = sum((x - mu) ** 2 for x in pnls) / n
    m4 = sum((x - mu) ** 4 for x in pnls) / n
    if m2 == 0:
        return 0.0
    return (m4 / (m2 ** 2)) - 3.0


def _monthly_pnl(trades: list[Any]) -> dict:
    from datetime import datetime, timezone

    buckets: dict[str, float] = {}
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        exit_time = trade.get("exit_time")
        if exit_time is None:
            continue
        try:
            dt = datetime.fromtimestamp(int(exit_time), tz=timezone.utc)
            key = dt.strftime("%Y-%m")
        except (ValueError, OSError):
            continue
        buckets[key] = buckets.get(key, 0.0) + _trade_pnl(trade)
    return buckets
