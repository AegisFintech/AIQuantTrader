"""XAU PDA/SMC gate wrapper for MT5 bridge parity backtests."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from aiquanttrader.backtest.position import Position
from aiquanttrader.backtest.strategies._xau_state import build_xau_feature_state
from aiquanttrader.backtest.strategies.base import Signal, Strategy
from aiquanttrader.backtest.strategies.xau_gates import XauGateParams


@dataclass(frozen=True)
class XauGatedParams:
    """XAU-specific gate thresholds from the live MT5 bridge EA."""

    pda_long_ceiling: float = 0.40
    pda_short_floor: float = 0.60
    min_smc_score: int = 4
    enable_smc_gate: bool = True
    enable_pda_gate: bool = True
    enable_adx_gate: bool = True
    enable_macd_histogram_alignment: bool = False
    enable_trend_slope_alignment: bool = False
    min_trend_slope_atr_multiplier: float = 0.04
    enable_higher_timeframe_trend_alignment: bool = False
    higher_trend_timeframe: str = "M15"
    higher_trend_ema_period: int = 50
    adx_min_threshold: float = 20.0
    gate_params: XauGateParams = field(default_factory=XauGateParams)
    min_bars_between_signals: int = 0
    min_seconds_between_trades: int = 0
    max_same_direction_positions: int = 2
    blackout_enabled: bool = False
    max_atr_regime_multiplier: float = 0.0


@dataclass(frozen=True)
class _DefaultXauStateParams:
    atr_period: int = 14


class XauGatedStrategy(Strategy):
    """Compose XAU PDA and SMC gates over an inner XAU strategy."""

    name = "XauGated"

    def __init__(
        self,
        inner: Strategy,
        gate_params: XauGatedParams | None = None,
        **kwargs: Any,
    ):
        if gate_params is None:
            gate_params = XauGatedParams(**kwargs)
        elif kwargs:
            gate_params = replace(gate_params, **kwargs)
        self.params = gate_params
        self._inner = inner
        self._state_params = getattr(inner, "params", _DefaultXauStateParams())
        self.timeframe = str(getattr(inner, "timeframe", "M5"))
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
        """Return the inner signal only when the XAU gates pass.

        The inner XAU strategy and gate state use the same profile timeframe.
        """

        if idx == 0 and self._last_idx >= 0:
            self._reset()

        higher_trend = self._higher_trend.update(bar)

        inner_signal = self._inner.on_bar(
            idx=idx,
            bar=bar,
            history=history,
            open_positions=open_positions,
            equity=equity,
            day_closed_pnl=day_closed_pnl,
        )
        action = inner_signal.action.upper()
        if action == "HOLD":
            return Signal(action="HOLD", strategy=self.name)
        if action not in {"BUY", "SELL"}:
            return Signal(action="HOLD", strategy=self.name)

        max_same_direction = max(1, int(self.params.max_same_direction_positions))
        same_direction = sum(
            1 for position in open_positions if position.side.upper() == action
        )
        if same_direction >= max_same_direction:
            return Signal(action="HOLD", strategy=self.name, comment="same_side_max")

        feature = self._feature_for(idx=idx, history=history)
        if self.params.blackout_enabled and _truthy(bar.get("blackout")):
            return Signal(action="HOLD", strategy=self.name, comment="blackout_reject")
        if self._atr_regime_too_hot(feature):
            return Signal(action="HOLD", strategy=self.name, comment="atr_regime_reject")

        trigger_price = getattr(self._inner, "_last_trigger_price", None)
        gate_price = float(trigger_price) if trigger_price is not None else feature["current"]
        feature = {
            **feature,
            **self._state.gate_features_for_price(
                price=gate_price,
                atr_value=feature.get("atr"),
            ),
        }
        pda_value = float(feature["pda"])
        smc_score = int(
            feature["smc_long_score"]
            if action == "BUY"
            else feature["smc_short_score"]
        )
        if self.params.enable_adx_gate:
            adx_value = feature.get("adx")
            if adx_value is None or float(adx_value) < self.params.adx_min_threshold:
                return Signal(action="HOLD", strategy=self.name, comment="adx_regime_reject")

        if self.params.enable_macd_histogram_alignment:
            macd_hist = feature.get("macd_hist")
            previous_macd_hist = (
                self._features[-2].get("macd_hist")
                if len(self._features) >= 2
                else None
            )
            if not _macd_histogram_aligned(
                action=action,
                macd_hist=macd_hist,
                previous_macd_hist=previous_macd_hist,
            ):
                return Signal(action="HOLD", strategy=self.name, comment="direction_reject")

        if self.params.enable_trend_slope_alignment:
            ema_trend = feature.get("ema_trend")
            previous_ema_trend = (
                self._features[-2].get("ema_trend")
                if len(self._features) >= 2
                else None
            )
            atr_value = feature.get("atr")
            if not _trend_slope_aligned(
                action=action,
                current=feature.get("current"),
                ema_trend=ema_trend,
                previous_ema_trend=previous_ema_trend,
                atr_value=atr_value,
                min_slope_atr_multiplier=(
                    self.params.min_trend_slope_atr_multiplier
                ),
            ):
                return Signal(action="HOLD", strategy=self.name, comment="direction_reject")

        if self.params.enable_higher_timeframe_trend_alignment:
            if not _higher_timeframe_trend_aligned(
                action=action,
                current=feature.get("current"),
                ema_trend=higher_trend.get("ema_trend"),
                previous_ema_trend=higher_trend.get("previous_ema_trend"),
            ):
                return Signal(action="HOLD", strategy=self.name, comment="direction_reject")

        if self.params.enable_pda_gate:
            if action == "BUY" and pda_value > self.params.pda_long_ceiling:
                return Signal(
                    action="HOLD",
                    strategy=self.name,
                    comment="xau_pda_reject",
                )
            if action == "SELL" and pda_value < self.params.pda_short_floor:
                return Signal(
                    action="HOLD",
                    strategy=self.name,
                    comment="xau_pda_reject",
                )

        if self.params.enable_smc_gate:
            if smc_score < self.params.min_smc_score:
                return Signal(action="HOLD", strategy=self.name, comment="smc_reject")

        if self._within_min_interval(idx=idx, bar=bar):
            return Signal(
                action="HOLD",
                strategy=self.name,
                comment="min_interval_reject",
            )

        self._last_signal_bar_idx = idx
        self._last_signal_time = _numeric_epoch(bar.get("time"))
        return replace(inner_signal, strategy=self.name, smc_score=smc_score)

    def _feature_for(self, *, idx: int, history: list[dict]) -> dict:
        if idx == 0 and self._last_idx >= 0:
            self._reset()
        if idx <= self._last_idx:
            return self._features[idx]

        start = self._last_idx + 1

        for replay_idx in range(start, idx + 1):
            self._features.append(
                self._state.update(replay_idx, history[replay_idx])
            )
            self._last_idx = replay_idx
        return self._features[idx]

    def _within_min_interval(self, *, idx: int, bar: dict) -> bool:
        min_bars = int(self.params.min_bars_between_signals)
        if min_bars > 0 and self._last_signal_bar_idx is not None:
            if idx - self._last_signal_bar_idx < min_bars:
                return True

        min_seconds = int(self.params.min_seconds_between_trades)
        current_time = _numeric_epoch(bar.get("time"))
        if (
            min_seconds > 0
            and current_time is not None
            and self._last_signal_time is not None
        ):
            return current_time - self._last_signal_time < min_seconds
        return False

    def _atr_regime_too_hot(self, feature: dict) -> bool:
        multiplier = float(self.params.max_atr_regime_multiplier)
        if multiplier <= 0.0:
            return False
        current_atr = feature.get("atr")
        if current_atr is None:
            return False
        # The regime uses only the latest 50 valid ATR observations. Walking
        # the entire accumulated feature history on every signal made the
        # higher-frequency profile lab quadratic without changing the result.
        window: list[float] = []
        for item in reversed(self._features[:-1]):
            value = item.get("atr")
            if value is None or float(value) <= 0.0:
                continue
            window.append(float(value))
            if len(window) >= 50:
                break
        if len(window) < 10:
            return False
        average_atr = sum(window) / len(window)
        return average_atr > 0.0 and float(current_atr) > average_atr * multiplier

    def _reset(self) -> None:
        self._state = build_xau_feature_state(
            self._state_params,
            timeframe=self.timeframe,
            gate_params=self.params.gate_params,
            eager_gate_features=False,
        )
        self._features: list[dict] = []
        self._last_idx = -1
        self._last_signal_bar_idx: int | None = None
        self._last_signal_time: int | None = None
        self._higher_trend = _ClosedTimeframeEmaState(
            timeframe=self.params.higher_trend_timeframe,
            period=self.params.higher_trend_ema_period,
        )


def _numeric_epoch(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _macd_histogram_aligned(
    *,
    action: str,
    macd_hist: Any,
    previous_macd_hist: Any,
) -> bool:
    if macd_hist is None or previous_macd_hist is None:
        return False
    if action == "BUY":
        return float(macd_hist) > 0.0 and float(macd_hist) > float(previous_macd_hist)
    return float(macd_hist) < 0.0 and float(macd_hist) < float(previous_macd_hist)


def _trend_slope_aligned(
    *,
    action: str,
    current: Any,
    ema_trend: Any,
    previous_ema_trend: Any,
    atr_value: Any,
    min_slope_atr_multiplier: float,
) -> bool:
    if None in {current, ema_trend, previous_ema_trend, atr_value}:
        return False
    minimum = max(0.0, float(atr_value) * float(min_slope_atr_multiplier))
    slope = float(ema_trend) - float(previous_ema_trend)
    if action == "BUY":
        return float(current) > float(ema_trend) and slope >= minimum
    return float(current) < float(ema_trend) and -slope >= minimum


def _higher_timeframe_trend_aligned(
    *,
    action: str,
    current: Any,
    ema_trend: Any,
    previous_ema_trend: Any,
) -> bool:
    if None in {current, ema_trend, previous_ema_trend}:
        return False
    if action == "BUY":
        return float(current) > float(ema_trend)
    return float(current) < float(ema_trend)


class _ClosedTimeframeEmaState:
    def __init__(self, *, timeframe: str, period: int):
        normalized = str(timeframe).upper().replace("PERIOD_", "")
        minutes = {"M1": 1, "M5": 5, "M15": 15}.get(normalized)
        if minutes is None:
            raise ValueError(f"unsupported higher trend timeframe: {timeframe!r}")
        self.bucket_seconds = minutes * 60
        self.period = max(1, int(period))
        self.alpha = 2.0 / (self.period + 1.0)
        self.bucket: int | None = None
        self.forming_close: float | None = None
        self.seed: list[float] = []
        self.ema: float | None = None
        self.previous_ema: float | None = None

    def update(self, bar: dict) -> dict[str, float | None]:
        epoch = _numeric_epoch(bar.get("time"))
        if epoch is None:
            raise ValueError("bar time is required for higher timeframe trend")
        bucket = epoch - (epoch % self.bucket_seconds)
        if self.bucket is None:
            self.bucket = bucket
        elif bucket != self.bucket:
            if self.forming_close is not None:
                self._commit(self.forming_close)
            self.bucket = bucket
        self.forming_close = float(bar["close"])
        return {
            "ema_trend": self.ema,
            "previous_ema_trend": self.previous_ema,
        }

    def _commit(self, close: float) -> None:
        if self.ema is None:
            self.seed.append(float(close))
            if len(self.seed) == self.period:
                self.ema = sum(self.seed) / self.period
            return
        self.previous_ema = self.ema
        self.ema = self.ema + (float(close) - self.ema) * self.alpha


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on"}
