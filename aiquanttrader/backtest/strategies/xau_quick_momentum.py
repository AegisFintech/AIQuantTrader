"""XAU QuickMomentum_EMA_cross strategy ported from the MT5 bridge EA."""

from __future__ import annotations

from dataclasses import dataclass, replace

from aiquanttrader.backtest.position import Position
from aiquanttrader.backtest.strategies._xau_state import build_xau_feature_state
from aiquanttrader.backtest.strategies.base import Signal, Strategy


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
        *,
        timeframe: str = "M5",
        **kwargs: float | int,
    ):
        if params is None:
            params = XauQuickMomentumParams(**kwargs)
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
        """Return BUY/SELL/HOLD for the current bar.

        The rolling state follows the selected runtime profile timeframe while
        receiving M1 warehouse bars. Signal booleans mirror the EA conditions,
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
        self._state = build_xau_feature_state(
            self.params,
            timeframe=self.timeframe,
        )
        self._features: list[dict] = []
        self._last_idx = -1
