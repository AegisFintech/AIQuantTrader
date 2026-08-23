"""Full enabled XAU entry chain from the MT5 bridge EA."""

from __future__ import annotations

from dataclasses import dataclass, replace

from aiquanttrader.backtest.position import Position
from aiquanttrader.backtest.strategies._xau_state import build_xau_feature_state
from aiquanttrader.backtest.strategies.base import Signal, Strategy


@dataclass(frozen=True)
class XauLiveSignalParams:
    """Parameters used by the enabled XAU signal paths in the live EA."""

    fast: int = 9
    slow: int = 21
    trend: int = 50
    rsi_period: int = 14
    atr_period: int = 14
    impulse_atr_mult: float = 0.12
    momentum3_threshold: float = 0.0015
    stop_atr_mult: float = 1.2
    tp_atr_mult: float = 2.4
    min_stop_floor: float = 2.0
    min_stop_pct: float = 0.00045
    enable_atr_impulse: bool = True


class XauLiveSignalStrategy(Strategy):
    """Mirror the enabled XAU signal precedence in ``ManageAutoSymbol``.

    With ``DisableWeakStrategySignals=true`` and RSI reversion disabled, the
    live EA retains ATR impulse, quick EMA momentum, and three-bar momentum.
    The profile lab must evaluate all three paths before a profile is eligible
    for deployment.
    """

    name = "XauLiveSignals"

    def __init__(
        self,
        params: XauLiveSignalParams | None = None,
        *,
        timeframe: str = "M1",
        **kwargs: float | int | bool,
    ):
        if params is None:
            params = XauLiveSignalParams(**kwargs)
        elif kwargs:
            params = replace(params, **kwargs)
        self.params = params
        self.timeframe = str(timeframe).upper().replace("PERIOD_", "")
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
        """Return the live enabled BUY/SELL/HOLD decision for one closed bar."""

        self._last_trigger_price = None
        feature = self._feature_for(idx=idx, history=history)
        decision = _enabled_xau_decision(feature, self.params)
        if decision is None:
            return Signal(action="HOLD", strategy=self.name)

        action, reason = decision
        current = float(feature["current"])
        atr = float(feature["atr"])
        self._last_trigger_price = current
        sl_distance, tp_distance = self._distances(current=current, atr=atr)
        return Signal(
            action=action,
            sl_distance=sl_distance,
            tp_distance=tp_distance,
            strategy=self.name,
            comment=reason,
        )

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
        min_stop = max(current * self.params.min_stop_pct, self.params.min_stop_floor)
        sl_distance = max(atr * self.params.stop_atr_mult, min_stop)
        return sl_distance, sl_distance * self.params.tp_atr_mult

    def _reset(self) -> None:
        self._state = build_xau_feature_state(self.params, timeframe=self.timeframe)
        self._features: list[dict] = []
        self._last_idx = -1
        self._last_trigger_price: float | None = None


def _enabled_xau_decision(
    feature: dict,
    params: XauLiveSignalParams,
) -> tuple[str, str] | None:
    """Return the side/reason using the EA's enabled-signal precedence."""

    required = (
        "atr",
        "rsi",
        "previous",
        "previous_high",
        "previous_low",
        "ema_trend",
        "momentum3",
    )
    if any(feature.get(key) is None for key in required):
        return None

    current = float(feature["current"])
    previous = float(feature["previous"])
    previous_high = float(feature["previous_high"])
    previous_low = float(feature["previous_low"])
    atr = float(feature["atr"])
    rsi = float(feature["rsi"])
    ema_trend = float(feature["ema_trend"])
    momentum3 = float(feature["momentum3"])

    atr_long = bool(params.enable_atr_impulse) and (
        current > previous_high
        and current - previous > atr * params.impulse_atr_mult
        and rsi < 80.0
    )
    atr_short = bool(params.enable_atr_impulse) and (
        current < previous_low
        and previous - current > atr * params.impulse_atr_mult
        and rsi > 20.0
    )
    momentum_long = (
        momentum3 > params.momentum3_threshold
        and current > ema_trend
        and rsi < 70.0
    )
    momentum_short = (
        momentum3 < -params.momentum3_threshold
        and current < ema_trend
        and rsi > 30.0
    )
    quick_long = bool(feature.get("quick_momentum_long"))
    quick_short = bool(feature.get("quick_momentum_short"))

    # The EA checks the long branch before the short branch. Its reason label
    # classifies a quick-momentum-only setup as Momentum_trend.
    if quick_long or atr_long or momentum_long:
        return "BUY", "ATR_impulse" if atr_long else "Momentum_trend"
    if quick_short or atr_short or momentum_short:
        return "SELL", "ATR_impulse" if atr_short else "Momentum_trend"
    return None
