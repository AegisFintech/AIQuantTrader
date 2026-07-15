"""Private rolling XAU indicator state shared by backtest strategies."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from finrobot.backtest.strategies.xau_gates import (
    XauGateParams,
    pda,
    smc_long_score,
    smc_short_score,
)


class XauRollingFeatureState:
    """O(1) rolling indicator state for normal sequential backtester calls."""

    def __init__(
        self,
        params: Any,
        gate_params: XauGateParams | None = None,
        *,
        eager_gate_features: bool = True,
    ):
        self.params = params
        self.gate_params = gate_params
        self.eager_gate_features = bool(eager_gate_features)
        self.ema_fast = _RollingEma(getattr(params, "fast", 9))
        self.ema_slow = _RollingEma(getattr(params, "slow", 21))
        self.ema_trend = _RollingEma(getattr(params, "trend", 50))
        self.rsi = _RollingRsi(getattr(params, "rsi_period", 14))
        self.atr = _RollingAtr(params.atr_period)
        self.adx = _RollingAdx(getattr(params, "adx_period", 14))
        self.macd_fast = _RollingEma(12)
        self.macd_slow = _RollingEma(26)
        self.macd_signal = _RollingEma(9)
        self.closes: list[float] = []
        self.bars: list[dict] = []

    def update(self, idx: int, bar: dict) -> dict:
        close = float(bar["close"])
        previous = self.closes[-1] if self.closes else None
        previous_bar = self.bars[-1] if self.bars else None
        close_three_bars_ago = self.closes[-3] if len(self.closes) >= 3 else None
        previous_ema_fast = self.ema_fast.value
        previous_ema_slow = self.ema_slow.value

        ema_fast = self.ema_fast.update(close)
        ema_slow = self.ema_slow.update(close)
        ema_trend = self.ema_trend.update(close)
        rsi_value = self.rsi.update(close)
        atr_value = self.atr.update(bar)
        adx_value = self.adx.update(bar)

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
                "adx": None,
                "current": close,
                "previous": previous,
                "previous_high": (
                    float(previous_bar["high"]) if previous_bar is not None else None
                ),
                "previous_low": (
                    float(previous_bar["low"]) if previous_bar is not None else None
                ),
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
            "adx": adx_value,
            "current": close,
            "previous": previous,
            "previous_high": (
                float(previous_bar["high"]) if previous_bar is not None else None
            ),
            "previous_low": (
                float(previous_bar["low"]) if previous_bar is not None else None
            ),
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
        if self.gate_params is None or not self.eager_gate_features:
            return feature

        return _with_gate_features(
            feature=feature,
            bars=self.bars,
            gate_params=self.gate_params,
            current=current,
            atr_value=atr_value,
        )

    def gate_features_for_price(
        self,
        *,
        price: float,
        atr_value: float | None,
    ) -> dict:
        if self.gate_params is None:
            return {}
        return _with_gate_features(
            feature={},
            bars=self.bars,
            gate_params=self.gate_params,
            current=float(price),
            atr_value=atr_value,
        )

    def preview_next(self, idx: int, bar: dict) -> dict:
        """Return features for ``bar`` as the next bar without mutating state."""

        close = float(bar["close"])
        previous = self.closes[-1] if self.closes else None
        close_three_bars_ago = self.closes[-3] if len(self.closes) >= 3 else None
        previous_ema_fast = self.ema_fast.value
        previous_ema_slow = self.ema_slow.value

        ema_fast = _preview_ema(self.ema_fast, close)
        ema_slow = _preview_ema(self.ema_slow, close)
        ema_trend = _preview_ema(self.ema_trend, close)
        rsi_value = _preview_rsi(self.rsi, close)
        atr_value = _preview_atr(self.atr, bar)

        macd_fast = _preview_ema(self.macd_fast, close)
        macd_slow = _preview_ema(self.macd_slow, close)
        macd_main = None
        macd_signal = None
        macd_hist = None
        if macd_fast is not None and macd_slow is not None:
            macd_main = macd_fast - macd_slow
            macd_signal = _preview_ema(self.macd_signal, macd_main)
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
                "adx": None,
                "current": close,
                "previous": previous,
                "momentum3": None,
                "bullish_cross": False,
                "bearish_cross": False,
                "quick_momentum_long": False,
                "quick_momentum_short": False,
            }
            return _with_gate_features(
                feature=feature,
                bars=_BarsWithCurrent(self.bars, bar),
                gate_params=self.gate_params,
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
            "adx": _preview_adx(self.adx, bar),
            "current": close,
            "previous": previous,
            "momentum3": momentum3,
            "bullish_cross": bullish_cross,
            "bearish_cross": bearish_cross,
            "quick_momentum_long": quick_long,
            "quick_momentum_short": quick_short,
        }
        return _with_gate_features(
            feature=feature,
            bars=_BarsWithCurrent(self.bars, bar),
            gate_params=self.gate_params,
            current=close,
            atr_value=atr_value,
        )


class XauM5RollingFeatureState:
    """Preview MQL5 ``PERIOD_M5`` indicators while receiving M1 bars."""

    def __init__(
        self,
        params: Any,
        gate_params: XauGateParams | None = None,
        *,
        eager_gate_features: bool = True,
    ):
        self.params = params
        self.gate_params = gate_params
        state_gate_params = gate_params if eager_gate_features else None
        self._state = XauRollingFeatureState(params, gate_params=state_gate_params)
        self._bucket_start: int | None = None
        self._forming_bar: dict | None = None
        self._forming_m1_count = 0

    def update(self, idx: int, bar: dict) -> dict:
        bucket_start = _m5_bucket_start(bar)
        if self._forming_bar is None:
            self._start_forming(bucket_start, bar)
        elif bucket_start != self._bucket_start:
            self._commit_forming()
            self._start_forming(bucket_start, bar)
        else:
            self._update_forming(bar)

        assert self._forming_bar is not None
        m5_idx = len(self._state.closes)
        feature = self._state.preview_next(m5_idx, self._forming_bar)
        previous_bar = self._state.bars[-1] if self._state.bars else None
        feature["previous_high"] = (
            float(previous_bar["high"]) if previous_bar is not None else None
        )
        feature["previous_low"] = (
            float(previous_bar["low"]) if previous_bar is not None else None
        )
        feature["m5_bar_idx"] = m5_idx
        feature["m5_bucket_start"] = self._bucket_start
        feature["m5_forming_m1_count"] = self._forming_m1_count
        return feature

    def gate_features_for_price(
        self,
        *,
        price: float,
        atr_value: float | None,
    ) -> dict:
        if self.gate_params is None or self._forming_bar is None:
            return {}
        return _with_gate_features(
            feature={},
            bars=_BarsWithCurrent(self._state.bars, self._forming_bar),
            gate_params=self.gate_params,
            current=float(price),
            atr_value=atr_value,
        )

    def _start_forming(self, bucket_start: int, bar: dict) -> None:
        self._bucket_start = bucket_start
        self._forming_bar = {
            "time": bucket_start,
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
            "volume": float(bar.get("volume", bar.get("tick_volume", 0.0)) or 0.0),
        }
        self._forming_m1_count = 1

    def _update_forming(self, bar: dict) -> None:
        assert self._forming_bar is not None
        self._forming_bar["high"] = max(
            float(self._forming_bar["high"]),
            float(bar["high"]),
        )
        self._forming_bar["low"] = min(
            float(self._forming_bar["low"]),
            float(bar["low"]),
        )
        self._forming_bar["close"] = float(bar["close"])
        self._forming_bar["volume"] = float(self._forming_bar.get("volume", 0.0)) + float(
            bar.get("volume", bar.get("tick_volume", 0.0)) or 0.0
        )
        self._forming_m1_count += 1

    def _commit_forming(self) -> None:
        assert self._forming_bar is not None
        m5_idx = len(self._state.closes)
        self._state.update(m5_idx, self._forming_bar)


def build_xau_feature_state(
    params: Any,
    *,
    timeframe: str,
    gate_params: XauGateParams | None = None,
    eager_gate_features: bool = True,
) -> XauRollingFeatureState | XauM5RollingFeatureState:
    """Build the rolling state that matches the EA profile timeframe."""

    normalized = str(timeframe or "").strip().upper().replace("PERIOD_", "")
    if normalized == "M1":
        return XauRollingFeatureState(
            params,
            gate_params=gate_params,
            eager_gate_features=eager_gate_features,
        )
    if normalized == "M5":
        return XauM5RollingFeatureState(
            params,
            gate_params=gate_params,
            eager_gate_features=eager_gate_features,
        )
    raise ValueError(f"unsupported XAU research timeframe: {timeframe!r}")


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


class _RollingAdx:
    """Wilder-smoothed ADX matching MT5 iADX behaviour."""

    def __init__(self, period: int = 14):
        self.period = _positive_period(period)
        self.value: float | None = None
        self._previous_high: float | None = None
        self._previous_low: float | None = None
        self._previous_close: float | None = None
        self._atr = _RollingAtr(period)
        self._plus_dm_ema = _RollingEma(period, alpha=1.0 / period)
        self._minus_dm_ema = _RollingEma(period, alpha=1.0 / period)
        self._dx_ema = _RollingEma(period, alpha=1.0 / period)

    def update(self, bar: dict) -> float | None:
        high = float(bar["high"])
        low = float(bar["low"])
        atr = self._atr.update(bar)

        if self._previous_high is None:
            self._previous_high = high
            self._previous_low = low
            self._previous_close = float(bar["close"])
            return None

        up_move = high - self._previous_high
        down_move = self._previous_low - low
        plus_dm = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0.0

        self._previous_high = high
        self._previous_low = low
        self._previous_close = float(bar["close"])

        plus_dm_smooth = self._plus_dm_ema.update(plus_dm)
        minus_dm_smooth = self._minus_dm_ema.update(minus_dm)

        if atr is None or plus_dm_smooth is None or minus_dm_smooth is None:
            return None

        atr_safe = atr if atr > 0 else 1e-10
        plus_di = 100.0 * plus_dm_smooth / atr_safe
        minus_di = 100.0 * minus_dm_smooth / atr_safe
        di_sum = plus_di + minus_di
        dx = 100.0 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0

        self.value = self._dx_ema.update(dx)
        return self.value


def _preview_adx(state: "_RollingAdx", bar: dict) -> float | None:
    return state.value


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


def _with_gate_features(
    *,
    feature: dict,
    bars: Any,
    gate_params: XauGateParams | None,
    current: float,
    atr_value: float | None,
) -> dict:
    if gate_params is None:
        return feature

    params = gate_params
    atr_for_gate = float(atr_value) if atr_value and atr_value > 0 else current * 0.0015
    pda_value = pda(bars, params.smc_lookback, current)
    long_score, long_components = smc_long_score(
        bars,
        atr_for_gate,
        current,
        params,
    )
    short_score, short_components = smc_short_score(
        bars,
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


def _preview_ema(state: _RollingEma, value: float) -> float | None:
    value = float(value)
    if state.value is None:
        if state._count + 1 == state.period:
            return (state._sum + value) / state.period
        return None
    return state.value + (value - state.value) * state.alpha


def _preview_rsi(state: _RollingRsi, close: float) -> float | None:
    close = float(close)
    if state._previous is None:
        return None

    change = close - state._previous
    gain = max(change, 0.0)
    loss = max(-change, 0.0)
    if state._changes < state.period:
        changes = state._changes + 1
        gain_sum = state._gain_sum + gain
        loss_sum = state._loss_sum + loss
        if changes == state.period:
            return _rsi_value(gain_sum / state.period, loss_sum / state.period)
        return state.value

    assert state._avg_gain is not None
    assert state._avg_loss is not None
    avg_gain = ((state._avg_gain * (state.period - 1)) + gain) / state.period
    avg_loss = ((state._avg_loss * (state.period - 1)) + loss) / state.period
    return _rsi_value(avg_gain, avg_loss)


def _preview_atr(state: _RollingAtr, bar: dict) -> float | None:
    high = float(bar["high"])
    low = float(bar["low"])
    close = float(bar["close"])
    if state._previous_close is None:
        return None

    true_range = max(
        high - low,
        abs(high - state._previous_close),
        abs(low - state._previous_close),
    )
    if state.value is None:
        seed = [*state._seed, true_range]
        if len(seed) == state.period:
            return sum(seed) / state.period
        return state.value
    return state.value + (true_range - state.value) / state.period


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


def _m5_bucket_start(bar: dict) -> int:
    epoch = _bar_epoch(bar.get("time", bar.get("ts", bar.get("ts_server"))))
    return epoch - (epoch % 300)


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


class _BarsWithCurrent:
    def __init__(self, bars: list[dict], current: dict):
        self._bars = bars
        self._current = current

    def __len__(self) -> int:
        return len(self._bars) + 1

    def __getitem__(self, idx: int) -> dict:
        normalized = int(idx)
        if normalized < 0:
            normalized += len(self)
        if normalized == len(self._bars):
            return self._current
        return self._bars[normalized]
