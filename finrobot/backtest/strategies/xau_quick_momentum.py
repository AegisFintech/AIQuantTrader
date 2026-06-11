"""XAU QuickMomentum_EMA_cross strategy ported from the MT5 bridge EA."""

from __future__ import annotations

from dataclasses import dataclass, replace

from finrobot.backtest.position import Position
from finrobot.backtest.strategies.base import Signal, Strategy


@dataclass(frozen=True)
class XauQuickMomentumParams:
    """Parameters for the M2.3a QuickMomentum_EMA_cross slice."""

    fast: int = 9
    slow: int = 21
    trend: int = 50
    rsi_period: int = 14
    atr_period: int = 14
    stop_atr_mult: float = 1.2
    tp_atr_mult: float = 1.8
    min_stop_floor: float = 2.0
    min_stop_pct: float = 0.00045


class XauQuickMomentumStrategy(Strategy):
    """Emit XAU QuickMomentum signals for the deterministic backtester."""

    name = "XauQuickMomentum"

    def __init__(
        self,
        params: XauQuickMomentumParams | None = None,
        **kwargs: float | int,
    ):
        if params is None:
            params = XauQuickMomentumParams(**kwargs)
        elif kwargs:
            params = replace(params, **kwargs)
        self.params = params
        self._reset()

    def on_bar(
        self,
        *,
        idx: int,
        bar: dict,
        history: list[dict],
        open_positions: list[Position],
        equity: float,
        day_closed_pnl: float,
    ) -> Signal:
        """Return BUY/SELL/HOLD for the current bar.

        Indicator inputs mirror MQL5 lines 751-777 of
        ``FinRobotBridgeEA.mq5``. Signal booleans mirror lines 807-808,
        after the XAU weak-signal filter in lines 832-835.
        """

        feature = self._feature_for(idx=idx, history=history)
        atr = feature["atr"]
        current = feature["current"]
        if atr is None:
            return Signal(action="HOLD", strategy=self.name)

        if feature["quick_momentum_long"]:
            sl_distance, tp_distance = self._distances(current=current, atr=atr)
            return Signal(
                action="BUY",
                sl_distance=sl_distance,
                tp_distance=tp_distance,
                strategy=self.name,
                comment="XauQuickMomentum_EMA_cross",
            )
        if feature["quick_momentum_short"]:
            sl_distance, tp_distance = self._distances(current=current, atr=atr)
            return Signal(
                action="SELL",
                sl_distance=sl_distance,
                tp_distance=tp_distance,
                strategy=self.name,
                comment="XauQuickMomentum_EMA_cross",
            )
        return Signal(action="HOLD", strategy=self.name)

    def _feature_for(self, *, idx: int, history: list[dict]) -> dict:
        if idx == 0 and self._last_idx >= 0:
            self._reset()
        if idx <= self._last_idx:
            return self._features[idx]

        if idx != self._last_idx + 1:
            self._reset()
            start = 0
        else:
            start = idx

        for replay_idx in range(start, idx + 1):
            self._features.append(self._state.update(replay_idx, history[replay_idx]))
            self._last_idx = replay_idx
        return self._features[idx]

    def _distances(self, *, current: float, atr: float) -> tuple[float, float]:
        params = self.params
        # MQL5 lines 699-701 and 892-893: max(ATR stop, XAU min stop).
        min_stop = max(current * params.min_stop_pct, params.min_stop_floor)
        sl_distance = max(atr * params.stop_atr_mult, min_stop)
        tp_distance = sl_distance * params.tp_atr_mult
        return sl_distance, tp_distance

    def _reset(self) -> None:
        self._state = _RollingXauState(self.params)
        self._features: list[dict] = []
        self._last_idx = -1


class _RollingXauState:
    """O(1) rolling indicator state for normal sequential backtester calls."""

    def __init__(self, params: XauQuickMomentumParams):
        self.params = params
        self.ema_fast = _RollingEma(params.fast)
        self.ema_slow = _RollingEma(params.slow)
        self.ema_trend = _RollingEma(params.trend)
        self.rsi = _RollingRsi(params.rsi_period)
        self.atr = _RollingAtr(params.atr_period)
        self.macd_fast = _RollingEma(12)
        self.macd_slow = _RollingEma(26)
        self.macd_signal = _RollingEma(9)
        self.closes: list[float] = []

    def update(self, idx: int, bar: dict) -> dict:
        close = float(bar["close"])
        previous = self.closes[-1] if self.closes else None
        close_three_bars_ago = self.closes[-3] if len(self.closes) >= 3 else None
        previous_ema_fast = self.ema_fast.value
        previous_ema_slow = self.ema_slow.value

        ema_fast = self.ema_fast.update(close)
        ema_slow = self.ema_slow.update(close)
        ema_trend = self.ema_trend.update(close)
        rsi_value = self.rsi.update(close)
        atr_value = self.atr.update(bar)

        macd_fast = self.macd_fast.update(close)
        macd_slow = self.macd_slow.update(close)
        macd_main = None
        macd_signal = None
        macd_hist = None
        if macd_fast is not None and macd_slow is not None:
            macd_main = macd_fast - macd_slow
            macd_signal = self.macd_signal.update(macd_main)
            if macd_signal is not None:
                macd_hist = macd_main - macd_signal

        momentum3 = None
        if close_three_bars_ago not in (None, 0.0):
            momentum3 = (close - close_three_bars_ago) / close_three_bars_ago

        bullish_cross = False
        bearish_cross = False
        quick_long = False
        quick_short = False
        if idx >= 3:
            bullish_cross = _rolling_bullish_cross(
                previous_ema_fast=previous_ema_fast,
                previous_ema_slow=previous_ema_slow,
                ema_fast=ema_fast,
                ema_slow=ema_slow,
            )
            bearish_cross = _rolling_bearish_cross(
                previous_ema_fast=previous_ema_fast,
                previous_ema_slow=previous_ema_slow,
                ema_fast=ema_fast,
                ema_slow=ema_slow,
            )
            quick_long = _rolling_quick_momentum_long(
                current=close,
                previous=previous,
                ema_fast=ema_fast,
                previous_ema_fast=previous_ema_fast,
                ema_slow=ema_slow,
                rsi_value=rsi_value,
            )
            quick_short = _rolling_quick_momentum_short(
                current=close,
                previous=previous,
                ema_fast=ema_fast,
                previous_ema_fast=previous_ema_fast,
                ema_slow=ema_slow,
                rsi_value=rsi_value,
            )

        self.closes.append(close)
        if idx < 3:
            return {
                "ema_fast": None,
                "ema_slow": None,
                "ema_trend": None,
                "rsi": None,
                "macd_main": None,
                "macd_signal": None,
                "macd_hist": None,
                "atr": None,
                "current": close,
                "previous": previous,
                "momentum3": None,
                "bullish_cross": False,
                "bearish_cross": False,
                "quick_momentum_long": False,
                "quick_momentum_short": False,
            }
        return {
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "ema_trend": ema_trend,
            "rsi": rsi_value,
            "macd_main": macd_main,
            "macd_signal": macd_signal,
            "macd_hist": macd_hist,
            "atr": atr_value,
            "current": close,
            "previous": previous,
            "momentum3": momentum3,
            "bullish_cross": bullish_cross,
            "bearish_cross": bearish_cross,
            "quick_momentum_long": quick_long,
            "quick_momentum_short": quick_short,
        }


class _RollingEma:
    def __init__(self, period: int, *, alpha: float | None = None):
        self.period = _positive_period(period)
        self.alpha = 2.0 / (self.period + 1.0) if alpha is None else float(alpha)
        self.value: float | None = None
        self._sum = 0.0
        self._count = 0

    def update(self, value: float) -> float | None:
        value = float(value)
        if self.value is None:
            self._sum += value
            self._count += 1
            if self._count == self.period:
                self.value = self._sum / self.period
            return self.value
        self.value = self.value + (value - self.value) * self.alpha
        self._count += 1
        return self.value


class _RollingRsi:
    def __init__(self, period: int):
        self.period = _positive_period(period)
        self.value: float | None = None
        self._previous: float | None = None
        self._changes = 0
        self._gain_sum = 0.0
        self._loss_sum = 0.0
        self._avg_gain: float | None = None
        self._avg_loss: float | None = None

    def update(self, close: float) -> float | None:
        close = float(close)
        if self._previous is None:
            self._previous = close
            return None

        change = close - self._previous
        self._previous = close
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        if self._changes < self.period:
            self._changes += 1
            self._gain_sum += gain
            self._loss_sum += loss
            if self._changes == self.period:
                self._avg_gain = self._gain_sum / self.period
                self._avg_loss = self._loss_sum / self.period
                self.value = _rsi_value(self._avg_gain, self._avg_loss)
            return self.value

        assert self._avg_gain is not None
        assert self._avg_loss is not None
        self._avg_gain = ((self._avg_gain * (self.period - 1)) + gain) / self.period
        self._avg_loss = ((self._avg_loss * (self.period - 1)) + loss) / self.period
        self.value = _rsi_value(self._avg_gain, self._avg_loss)
        return self.value


class _RollingAtr:
    def __init__(self, period: int):
        self.period = _positive_period(period)
        self.value: float | None = None
        self._previous_close: float | None = None
        self._seed: list[float] = []

    def update(self, bar: dict) -> float | None:
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        if self._previous_close is None:
            self._previous_close = close
            return None

        true_range = max(
            high - low,
            abs(high - self._previous_close),
            abs(low - self._previous_close),
        )
        self._previous_close = close
        if self.value is None:
            self._seed.append(true_range)
            if len(self._seed) == self.period:
                self.value = sum(self._seed) / self.period
            return self.value
        self.value = self.value + (true_range - self.value) / self.period
        return self.value


def _rolling_bullish_cross(
    *,
    previous_ema_fast: float | None,
    previous_ema_slow: float | None,
    ema_fast: float | None,
    ema_slow: float | None,
) -> bool:
    if None in (previous_ema_fast, previous_ema_slow, ema_fast, ema_slow):
        return False
    # MQL5: emaFast[1] <= emaSlow[1] && emaFast[0] > emaSlow[0]
    return bool(previous_ema_fast <= previous_ema_slow and ema_fast > ema_slow)


def _rolling_bearish_cross(
    *,
    previous_ema_fast: float | None,
    previous_ema_slow: float | None,
    ema_fast: float | None,
    ema_slow: float | None,
) -> bool:
    if None in (previous_ema_fast, previous_ema_slow, ema_fast, ema_slow):
        return False
    # MQL5: emaFast[1] >= emaSlow[1] && emaFast[0] < emaSlow[0]
    return bool(previous_ema_fast >= previous_ema_slow and ema_fast < ema_slow)


def _rolling_quick_momentum_long(
    *,
    current: float,
    previous: float | None,
    ema_fast: float | None,
    previous_ema_fast: float | None,
    ema_slow: float | None,
    rsi_value: float | None,
) -> bool:
    if (
        previous is None
        or ema_fast is None
        or previous_ema_fast is None
        or ema_slow is None
        or rsi_value is None
    ):
        return False
    # MQL5: emaFast[0] > emaSlow[0]
    if not ema_fast > ema_slow:
        return False
    # MQL5: previous <= emaFast[1]
    if not previous <= previous_ema_fast:
        return False
    # MQL5: current > emaFast[0]
    if not current > ema_fast:
        return False
    # MQL5: rsi[0] >= 42 && rsi[0] < 68
    return 42.0 <= rsi_value < 68.0


def _rolling_quick_momentum_short(
    *,
    current: float,
    previous: float | None,
    ema_fast: float | None,
    previous_ema_fast: float | None,
    ema_slow: float | None,
    rsi_value: float | None,
) -> bool:
    if (
        previous is None
        or ema_fast is None
        or previous_ema_fast is None
        or ema_slow is None
        or rsi_value is None
    ):
        return False
    # MQL5: emaFast[0] < emaSlow[0]
    if not ema_fast < ema_slow:
        return False
    # MQL5: previous >= emaFast[1]
    if not previous >= previous_ema_fast:
        return False
    # MQL5: current < emaFast[0]
    if not current < ema_fast:
        return False
    # MQL5: rsi[0] <= 58 && rsi[0] > 32
    return 32.0 < rsi_value <= 58.0


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    relative_strength = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def _positive_period(period: int) -> int:
    period = int(period)
    if period <= 0:
        raise ValueError("period must be positive")
    return period
