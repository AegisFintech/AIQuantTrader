"""Deterministic bar-by-bar backtest engine."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from aiquanttrader.backtest.fills import FillConfig, simulate_fill
from aiquanttrader.backtest.position import Position, PositionSizer
from aiquanttrader.backtest.strategies.base import Signal, Strategy


@dataclass(frozen=True)
class BreakEvenConfig:
    """Dynamic break-even management for open simulated positions."""

    enabled: bool = False
    rr_ratio: float = 1.0
    extra_points: float = 10.0


@dataclass
class BacktestConfig:
    """Configuration for one deterministic backtest run."""

    symbol: str = "XAUUSD"
    fill_config: FillConfig = field(default_factory=FillConfig)
    sizer: PositionSizer = field(
        default_factory=lambda: PositionSizer(
            risk_per_trade_fraction=0.001,
            daily_loss_cap_fraction=0.01,
            max_lot_per_trade=5.0,
            max_positions_per_symbol=2,
        )
    )
    initial_equity: float = 10000.0
    magic: int = 20260522
    point_value: float = 1.0  # Cash value of a one-unit price move at one lot.
    min_seconds_between_trades: int = 0
    loss_streak_pause_count: int = 0
    max_recent_drawdown_fraction: float = 0.0
    break_even: BreakEvenConfig = field(default_factory=BreakEvenConfig)


@dataclass
class BacktestResult:
    """Result payload returned by :class:`Backtester`."""

    config: BacktestConfig
    strategy_name: str
    bars: int
    start_time: int
    end_time: int
    initial_equity: float
    final_equity: float
    trades: list[dict]
    equity_curve: list[tuple[int, float]]
    open_positions_at_end: list[Position]
    rejected_signals: int


@dataclass
class _OpenRecord:
    position: Position
    strategy: str
    comment: str
    entry_bar_idx: int


class Backtester:
    """Run deterministic bar-by-bar simulations over OHLCV bars."""

    def __init__(self, config: BacktestConfig):
        self.config = config

    def run(self, *, strategy: Strategy, bars: list[dict]) -> BacktestResult:
        """Run ``strategy`` over ``bars`` and return a deterministic result."""

        normalized_bars = [_normalize_bar(bar) for bar in bars]
        strategy_name = _strategy_name(strategy)
        if not normalized_bars:
            initial = float(self.config.initial_equity)
            return BacktestResult(
                config=self.config,
                strategy_name=strategy_name,
                bars=0,
                start_time=0,
                end_time=0,
                initial_equity=initial,
                final_equity=initial,
                trades=[],
                equity_curve=[],
                open_positions_at_end=[],
                rejected_signals=0,
            )

        open_records: list[_OpenRecord] = []
        trades: list[dict] = []
        equity_curve: list[tuple[int, float]] = []
        realized_pnl = 0.0
        closed_pnl_by_day: dict[str, float] = {}
        rejected_signals = 0
        history: list[dict] = []
        last_trade_time_by_symbol: dict[str, int] = {}
        current_loss_streak = 0

        for idx, bar in enumerate(normalized_bars):
            history.append(bar)
            now_epoch = int(bar["time"])
            open_records = [self._mark_record(record, bar) for record in open_records]
            equity = self._equity(realized_pnl, open_records)
            day_key = _day_key(now_epoch)
            signal = strategy.on_bar(
                idx=idx,
                bar=bar,
                history=history,
                open_positions=[record.position for record in open_records],
                equity=equity,
                day_closed_pnl=closed_pnl_by_day.get(day_key, 0.0),
            )

            if signal.action.upper() in {"BUY", "SELL"}:
                symbol_key = self.config.symbol.upper()
                min_seconds = int(self.config.min_seconds_between_trades)
                last_trade_time = last_trade_time_by_symbol.get(symbol_key)
                if (
                    min_seconds > 0
                    and last_trade_time is not None
                    and now_epoch - last_trade_time < min_seconds
                ):
                    rejected_signals += 1
                elif self._recovery_pause_active(
                    equity=equity,
                    day_closed_pnl=closed_pnl_by_day.get(day_key, 0.0),
                    loss_streak=current_loss_streak,
                ):
                    rejected_signals += 1
                else:
                    opened = self._open_position(
                        signal=signal,
                        strategy=strategy,
                        bar=bar,
                        idx=idx,
                        open_records=open_records,
                        equity=equity,
                        day_closed_pnl=closed_pnl_by_day.get(day_key, 0.0),
                    )
                    if opened is None:
                        rejected_signals += 1
                    else:
                        open_records.append(opened)
                        last_trade_time_by_symbol[symbol_key] = now_epoch

            survivors: list[_OpenRecord] = []
            for record in open_records:
                exit_price, exit_reason = self._exit_for_bar(record.position, bar)
                if exit_price is None:
                    survivors.append(
                        self._apply_break_even(
                            record=record,
                            current_price=float(bar["close"]),
                        )
                    )
                    continue
                trade, pnl = self._close_record(
                    record=record,
                    bar=bar,
                    idx=idx,
                    intended_exit_price=exit_price,
                    exit_reason=exit_reason,
                )
                trades.append(trade)
                realized_pnl += pnl
                if pnl < 0:
                    current_loss_streak += 1
                elif pnl > 0:
                    current_loss_streak = 0
                closed_pnl_by_day[day_key] = closed_pnl_by_day.get(day_key, 0.0) + pnl
            open_records = [self._mark_record(record, bar) for record in survivors]
            equity_curve.append((now_epoch, self._equity(realized_pnl, open_records)))

        last_bar = normalized_bars[-1]
        last_idx = len(normalized_bars) - 1
        last_day = _day_key(int(last_bar["time"]))
        for record in open_records:
            trade, pnl = self._close_record(
                record=record,
                bar=last_bar,
                idx=last_idx,
                intended_exit_price=float(last_bar["close"]),
                exit_reason="end_of_test",
            )
            trades.append(trade)
            realized_pnl += pnl
            if pnl < 0:
                current_loss_streak += 1
            elif pnl > 0:
                current_loss_streak = 0
            closed_pnl_by_day[last_day] = closed_pnl_by_day.get(last_day, 0.0) + pnl

        final_equity = float(self.config.initial_equity) + realized_pnl
        if equity_curve:
            equity_curve[-1] = (equity_curve[-1][0], final_equity)

        return BacktestResult(
            config=self.config,
            strategy_name=strategy_name,
            bars=len(normalized_bars),
            start_time=int(normalized_bars[0]["time"]),
            end_time=int(normalized_bars[-1]["time"]),
            initial_equity=float(self.config.initial_equity),
            final_equity=final_equity,
            trades=trades,
            equity_curve=equity_curve,
            open_positions_at_end=[],
            rejected_signals=rejected_signals,
        )

    def _recovery_pause_active(
        self,
        *,
        equity: float,
        day_closed_pnl: float,
        loss_streak: int,
    ) -> bool:
        loss_limit = int(self.config.loss_streak_pause_count)
        if loss_limit > 0 and int(loss_streak) >= loss_limit:
            return True

        drawdown_fraction = float(self.config.max_recent_drawdown_fraction)
        if drawdown_fraction > 0.0 and float(equity) > 0.0:
            return float(day_closed_pnl) <= -drawdown_fraction * float(equity)
        return False

    def _open_position(
        self,
        *,
        signal: Signal,
        strategy: Strategy,
        bar: dict,
        idx: int,
        open_records: list[_OpenRecord],
        equity: float,
        day_closed_pnl: float,
    ) -> _OpenRecord | None:
        action = signal.action.upper()
        sl_distance = _optional_distance(signal.sl_distance)
        tp_distance = _optional_distance(signal.tp_distance)
        if action not in {"BUY", "SELL"}:
            return None
        if sl_distance is None and not _allows_no_sl(strategy, signal):
            return None
        if (sl_distance is not None and sl_distance < 0) or (
            tp_distance is not None and tp_distance < 0
        ):
            return None

        sizing_distance = _sizing_distance(
            signal=signal,
            strategy=strategy,
            bar=bar,
            sl_distance=sl_distance,
            tp_distance=tp_distance,
        )
        volume = self.config.sizer.size(
            symbol=self.config.symbol,
            equity=equity,
            sl_distance=sizing_distance * float(self.config.point_value),
            open_positions=[record.position for record in open_records],
            today_closed_pnl=day_closed_pnl,
            smc_score=signal.smc_score or 0,
        )
        if volume <= 0:
            return None

        fill_price, _, commission_per_lot = simulate_fill(
            side=action,
            intended_price=float(bar["close"]),
            bar_high=float(bar["high"]),
            bar_low=float(bar["low"]),
            config=self.config.fill_config,
        )
        sl, tp = _protective_prices(
            side=action,
            entry_price=fill_price,
            sl_distance=sl_distance,
            tp_distance=tp_distance,
        )
        position = Position(
            symbol=self.config.symbol,
            side=action,
            volume=volume,
            entry_price=fill_price,
            entry_time=int(bar["time"]),
            sl=sl,
            tp=tp,
            magic=self.config.magic,
            open_commission=float(commission_per_lot) * volume,
        )
        return _OpenRecord(
            position=position,
            strategy=signal.strategy or _strategy_name(strategy),
            comment=signal.comment,
            entry_bar_idx=idx,
        )

    def _exit_for_bar(self, position: Position, bar: dict) -> tuple[float | None, str]:
        high = float(bar["high"])
        low = float(bar["low"])
        side = position.side.upper()
        if side == "BUY":
            sl_hit = position.sl > 0 and low <= position.sl
            tp_hit = position.tp > 0 and high >= position.tp
        elif side == "SELL":
            sl_hit = position.sl > 0 and high >= position.sl
            tp_hit = position.tp > 0 and low <= position.tp
        else:
            return None, ""

        if sl_hit:
            return position.sl, "sl"
        if tp_hit:
            return position.tp, "tp"
        return None, ""

    def _close_record(
        self,
        *,
        record: _OpenRecord,
        bar: dict,
        idx: int,
        intended_exit_price: float,
        exit_reason: str,
    ) -> tuple[dict, float]:
        position = record.position
        close_side = "SELL" if position.side.upper() == "BUY" else "BUY"
        exit_price, _, commission_per_lot = simulate_fill(
            side=close_side,
            intended_price=float(intended_exit_price),
            bar_high=float(bar["high"]),
            bar_low=float(bar["low"]),
            config=self.config.fill_config,
        )
        close_commission = float(commission_per_lot) * position.volume
        held_seconds = max(0, int(bar["time"]) - int(position.entry_time))
        swap = (
            -float(self.config.fill_config.swap_per_lot_per_day)
            * position.volume
            * held_seconds
            / 86400.0
        )
        price_pnl = _price_pnl(
            side=position.side,
            entry_price=position.entry_price,
            exit_price=exit_price,
            volume=position.volume,
            point_value=float(self.config.point_value),
        )
        pnl = price_pnl - position.open_commission - close_commission + swap
        trade = {
            "bar_idx": record.entry_bar_idx,
            "entry_bar_idx": record.entry_bar_idx,
            "exit_bar_idx": idx,
            "action": position.side.upper(),
            "symbol": position.symbol,
            "entry_time": position.entry_time,
            "exit_time": int(bar["time"]),
            "side": position.side.upper(),
            "volume": position.volume,
            "entry_price": position.entry_price,
            "exit_price": exit_price,
            "pnl": pnl,
            "sl": position.sl,
            "tp": position.tp,
            "strategy": record.strategy,
            "comment": record.comment,
            "magic": position.magic,
            "commission": position.open_commission + close_commission,
            "swap": swap,
            "exit_reason": exit_reason,
            "break_even_applied": position.break_even_applied,
        }
        return trade, pnl

    def _apply_break_even(
        self,
        *,
        record: _OpenRecord,
        current_price: float,
    ) -> _OpenRecord:
        config = self.config.break_even
        position = record.position
        if position.break_even_applied or not config.enabled:
            return record
        if position.sl <= 0.0 or position.tp <= 0.0:
            return record

        side = position.side.upper()
        if side == "BUY":
            direction = 1.0
        elif side == "SELL":
            direction = -1.0
        else:
            return record

        sl_distance = abs(position.entry_price - position.sl)
        if sl_distance <= 0.0:
            return record

        profit_points = direction * (float(current_price) - position.entry_price)
        threshold = sl_distance * float(config.rr_ratio)
        if profit_points < threshold:
            return record

        new_sl = position.entry_price + direction * float(config.extra_points)
        if side == "BUY" and new_sl <= position.sl:
            return record
        if side == "SELL" and new_sl >= position.sl:
            return record

        return replace(
            record,
            position=replace(position, sl=new_sl, break_even_applied=True),
        )

    def _mark_record(self, record: _OpenRecord, bar: dict) -> _OpenRecord:
        position = record.position
        price_pnl = _price_pnl(
            side=position.side,
            entry_price=position.entry_price,
            exit_price=float(bar["close"]),
            volume=position.volume,
            point_value=float(self.config.point_value),
        )
        held_seconds = max(0, int(bar["time"]) - int(position.entry_time))
        swap = (
            -float(self.config.fill_config.swap_per_lot_per_day)
            * position.volume
            * held_seconds
            / 86400.0
        )
        return replace(
            record,
            position=replace(
                position,
                open_swap_accrued=swap,
                current_pnl=price_pnl - position.open_commission + swap,
            ),
        )

    def _equity(self, realized_pnl: float, open_records: list[_OpenRecord]) -> float:
        return (
            float(self.config.initial_equity)
            + realized_pnl
            + sum(record.position.current_pnl for record in open_records)
        )


def _normalize_bar(raw: dict) -> dict:
    return {
        "time": _bar_epoch(raw.get("time", raw.get("ts", raw.get("ts_server")))),
        "open": float(raw["open"]),
        "high": float(raw["high"]),
        "low": float(raw["low"]),
        "close": float(raw["close"]),
        "volume": float(raw.get("volume", raw.get("tick_volume", 0.0)) or 0.0),
    }


def _bar_epoch(value: Any) -> int:
    if value is None or value == "":
        raise ValueError("bar time is required")
    try:
        return int(float(value))
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y.%m.%d %H:%M:%S"):
        try:
            return int(datetime.strptime(text, fmt).timestamp())
        except ValueError:
            continue
    raise ValueError(f"unsupported bar time: {value!r}")


def _day_key(epoch: int) -> str:
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d")


def _strategy_name(strategy: Strategy) -> str:
    return getattr(strategy, "name", "") or strategy.__class__.__name__


def _optional_distance(value: float | None) -> float | None:
    if value is None:
        return None
    return float(value)


def _allows_no_sl(strategy: Strategy, signal: Signal) -> bool:
    return _strategy_name(strategy) == "BuyAndHold" or signal.strategy == "BuyAndHold"


def _sizing_distance(
    *,
    signal: Signal,
    strategy: Strategy,
    bar: dict,
    sl_distance: float | None,
    tp_distance: float | None,
) -> float:
    if sl_distance is not None and sl_distance > 0:
        return sl_distance
    if tp_distance is not None and tp_distance > 0:
        return tp_distance
    risk_fraction = getattr(strategy, "risk_per_trade_fraction", None)
    if risk_fraction is not None and _allows_no_sl(strategy, signal):
        return max(float(bar["close"]), 0.000001)
    return 0.0


def _protective_prices(
    *,
    side: str,
    entry_price: float,
    sl_distance: float | None,
    tp_distance: float | None,
) -> tuple[float, float]:
    sl = 0.0
    tp = 0.0
    if side == "BUY":
        if sl_distance is not None and sl_distance > 0:
            sl = entry_price - sl_distance
        if tp_distance is not None and tp_distance > 0:
            tp = entry_price + tp_distance
    else:
        if sl_distance is not None and sl_distance > 0:
            sl = entry_price + sl_distance
        if tp_distance is not None and tp_distance > 0:
            tp = entry_price - tp_distance
    return sl, tp


def _price_pnl(
    *,
    side: str,
    entry_price: float,
    exit_price: float,
    volume: float,
    point_value: float,
) -> float:
    if side.upper() == "BUY":
        return (exit_price - entry_price) * volume * point_value
    if side.upper() == "SELL":
        return (entry_price - exit_price) * volume * point_value
    raise ValueError(f"position side must be BUY or SELL, got {side!r}")
