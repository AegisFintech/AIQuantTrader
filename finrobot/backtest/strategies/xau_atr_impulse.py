"""XAU ATR_impulse strategy ported from the MT5 bridge EA."""

from __future__ import annotations

from dataclasses import dataclass, replace

from finrobot.backtest.position import Position
from finrobot.backtest.strategies._xau_state import XauRollingFeatureState
from finrobot.backtest.strategies.base import Signal, Strategy


@dataclass(frozen=True)
class XauAtrImpulseParams:
    """Parameters for the M2.3b ATR_impulse slice."""

    rsi_period: int = 14
    atr_period: int = 14
    impulse_atr_mult: float = 0.12
    rsi_long_ceiling: float = 80.0
    rsi_short_floor: float = 20.0
    stop_atr_mult: float = 1.2
    tp_atr_mult: float = 1.8
    min_stop_floor: float = 2.0
    min_stop_pct: float = 0.00045


class XauAtrImpulseStrategy(Strategy):
    """Emit XAU ATR_impulse signals for the deterministic backtester."""

    name = "XauAtrImpulse"

    def __init__(
        self,
        params: XauAtrImpulseParams | None = None,
        **kwargs: float | int,
    ):
        if params is None:
            params = XauAtrImpulseParams(**kwargs)
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
        ``FinRobotBridgeEA.mq5``. Signal booleans mirror lines 803-804,
        with the XAU weak-signal filter in lines 832-835 leaving
        ``atrImpulseLong`` and ``atrImpulseShort`` intact.
        """

        feature = self._feature_for(idx=idx, history=history)
        atr = feature["atr"]
        rsi = feature["rsi"]
        current = feature["current"]
        previous = feature["previous"]
        if idx <= 0 or atr is None or rsi is None or previous is None:
            return Signal(action="HOLD", strategy=self.name)

        params = self.params
        previous_bar = history[idx - 1]
        # mirrors MQL5 lines 803-804 of FinRobotBridgeEA.mq5
        atr_impulse_long = (
            current > float(previous_bar["high"])
            and (current - previous) > atr * params.impulse_atr_mult
            and rsi < params.rsi_long_ceiling
        )
        atr_impulse_short = (
            current < float(previous_bar["low"])
            and (previous - current) > atr * params.impulse_atr_mult
            and rsi > params.rsi_short_floor
        )

        if atr_impulse_long:
            sl_distance, tp_distance = self._distances(current=current, atr=atr)
            return Signal(
                action="BUY",
                sl_distance=sl_distance,
                tp_distance=tp_distance,
                strategy=self.name,
                comment="ATR_impulse",
            )
        if atr_impulse_short:
            sl_distance, tp_distance = self._distances(current=current, atr=atr)
            return Signal(
                action="SELL",
                sl_distance=sl_distance,
                tp_distance=tp_distance,
                strategy=self.name,
                comment="ATR_impulse",
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
        self._state = XauRollingFeatureState(self.params)
        self._features: list[dict] = []
        self._last_idx = -1
