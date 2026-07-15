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
    adx_min_threshold: float = 20.0
    gate_params: XauGateParams = field(default_factory=XauGateParams)
    min_bars_between_signals: int = 0
    min_seconds_between_trades: int = 0
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
        previous_values = [
            float(item["atr"])
            for item in self._features[:-1]
            if item.get("atr") is not None and float(item["atr"]) > 0.0
        ]
        if len(previous_values) < 10:
            return False
        window = previous_values[-50:]
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


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "on"}
