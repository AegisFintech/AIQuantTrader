"""Private rolling XAU indicator state shared by backtest strategies."""

from __future__ import annotations

from typing import Any

from finrobot.backtest.strategies.xau_gates import (
    XauGateParams,
    pda,
    smc_long_score,
    smc_short_score,
)


class XauRollingFeatureState:
    """O(1) rolling indicator state for normal sequential backtester calls."""

    def __init__(self, params: Any, gate_params: XauGateParams | None = None):
        self.params = params
        self.gate_params = gate_params
        self.ema_fast = _RollingEma(getattr(params, "fast", 9))
        self.ema_slow = _RollingEma(getattr(params, "slow", 21))
        self.ema_trend = _RollingEma(getattr(params, "trend", 50))
        self.rsi = _RollingRsi(getattr(params, "rsi_period", 14))
        self.atr = _RollingAtr(params.atr_period)
        self.macd_fast = _RollingEma(12)
        self.macd_slow = _RollingEma(26)
        self.macd_signal = _RollingEma(9)
        self.closes: list[float] = []
        self.bars: list[dict] = []

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
        self.bars.append(bar)
        if idx < 3:
            feature = {
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
            return self._with_gate_features(
                feature=feature,
                current=close,
                atr_value=atr_value,
            )
        feature = {
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
        return self._with_gate_features(
            feature=feature,
            current=close,
            atr_value=atr_value,
        )

    def _with_gate_features(
        self,
        *,
        feature: dict,
        current: float,
        atr_value: float | None,
    ) -> dict:
        if self.gate_params is None:
            return feature

        params = self.gate_params
        atr_for_gate = (
            float(atr_value) if atr_value and atr_value > 0 else current * 0.0015
        )
        pda_value = pda(self.bars, params.smc_lookback, current)
        long_score, long_components = smc_long_score(
            self.bars,
            atr_for_gate,
            current,
            params,
        )
        short_score, short_components = smc_short_score(
            self.bars,
            atr_for_gate,
            current,
            params,
        )
        return {
            **feature,
            "pda": pda_value,
            "smc_long_score": long_score,
            "smc_short_score": short_score,
            "smc_long_discount": long_components["discount"],
            "smc_long_deep_discount": long_components["deep_discount"],
            "smc_long_has_fvg": long_components["has_fvg"],
            "smc_long_reclaimed_order_block": long_components[
                "reclaimed_order_block"
            ],
            "smc_long_sweep": long_components["sweep"],
            "smc_long_structure": long_components["structure"],
            "smc_short_premium": short_components["premium"],
            "smc_short_deep_premium": short_components["deep_premium"],
            "smc_short_has_fvg": short_components["has_fvg"],
            "smc_short_rejected_order_block": short_components[
                "rejected_order_block"
            ],
            "smc_short_sweep": short_components["sweep"],
            "smc_short_structure": short_components["structure"],
        }


_RollingXauState = XauRollingFeatureState


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
