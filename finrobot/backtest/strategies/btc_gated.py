"""BTC PDA/SMC gate wrapper for MT5 bridge parity backtests."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from finrobot.backtest.position import Position
from finrobot.backtest.strategies._xau_state import XauRollingFeatureState
from finrobot.backtest.strategies.base import Signal, Strategy
from finrobot.backtest.strategies.btc_gates import btc_cost_filter_rejects
from finrobot.backtest.strategies.xau_gates import XauGateParams


@dataclass(frozen=True)
class BtcGatedParams:
    """BTC-specific gate thresholds from the live MT5 bridge EA."""

    pda_long_ceiling: float = 0.45
    pda_short_floor: float = 0.55
    min_smc_score: int = 2
    enable_pda_gate: bool = True
    enable_smc_gate: bool = True
    enable_direction_gate: bool = True
    htf_trend: int = 0
    enable_cost_filter: bool = False
    min_bars_between_signals: int = 1
    gate_params: XauGateParams = field(default_factory=XauGateParams)


@dataclass(frozen=True)
class _DefaultBtcStateParams:
    atr_period: int = 14


class BtcGatedStrategy(Strategy):
    """Compose BTC direction, PDA, SMC, and cost gates over an inner strategy."""

    name = "BtcGated"

    def __init__(
        self,
        inner: Strategy,
        params: BtcGatedParams | None = None,
        **kwargs: Any,
    ):
        if params is None:
            params = BtcGatedParams(**kwargs)
        elif kwargs:
            params = replace(params, **kwargs)
        self.params = params
        self._inner = inner
        self._state_params = getattr(inner, "params", _DefaultBtcStateParams())
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
        """Return the inner signal only when the BTC gates pass."""

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
        pda_value = float(feature["pda"])
        smc_score = int(
            feature["smc_long_score"]
            if action == "BUY"
            else feature["smc_short_score"]
        )

        # Mirrors MQL5 lines 861-870 of FinRobotBridgeEA.mq5.
        if self.params.enable_direction_gate and self._wrong_htf_trend(action):
            return Signal(
                action="HOLD",
                strategy=self.name,
                comment="btc_direction_reject",
            )

        # MQL5 reports BTC PDA failures through btc_direction_reject.
        if self.params.enable_pda_gate:
            if action == "BUY" and pda_value > self.params.pda_long_ceiling:
                return Signal(
                    action="HOLD",
                    strategy=self.name,
                    comment="btc_direction_reject",
                )
            if action == "SELL" and pda_value < self.params.pda_short_floor:
                return Signal(
                    action="HOLD",
                    strategy=self.name,
                    comment="btc_direction_reject",
                )

        # Mirrors MQL5 lines 883-889 of FinRobotBridgeEA.mq5.
        if self.params.enable_smc_gate and smc_score < self.params.min_smc_score:
            return Signal(action="HOLD", strategy=self.name, comment="smc_reject")

        if self.params.enable_cost_filter:
            rejected, detail = btc_cost_filter_rejects(
                _bar_spread(bar),
                feature.get("atr"),
                inner_signal.tp_distance,
            )
            if rejected:
                return Signal(
                    action="HOLD",
                    strategy=self.name,
                    comment=detail or "btc_cost_reject",
                )

        if self._within_min_interval(idx=idx):
            return Signal(
                action="HOLD",
                strategy=self.name,
                comment="min_interval_reject",
            )

        self._last_signal_bar_idx = idx
        return replace(inner_signal, strategy=self.name, smc_score=smc_score)

    def _wrong_htf_trend(self, action: str) -> bool:
        htf_trend = int(self.params.htf_trend)
        if action == "BUY":
            return htf_trend <= 0
        if action == "SELL":
            return htf_trend >= 0
        return True

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
            self._features.append(
                self._state.update(replay_idx, history[replay_idx])
            )
            self._last_idx = replay_idx
        return self._features[idx]

    def _within_min_interval(self, *, idx: int) -> bool:
        min_bars = int(self.params.min_bars_between_signals)
        return (
            min_bars > 0
            and self._last_signal_bar_idx is not None
            and idx - self._last_signal_bar_idx < min_bars
        )

    def _reset(self) -> None:
        self._state = XauRollingFeatureState(
            self._state_params,
            gate_params=self.params.gate_params,
        )
        self._features: list[dict] = []
        self._last_idx = -1
        self._last_signal_bar_idx: int | None = None


def _bar_spread(bar: dict) -> float | None:
    value = bar.get("spread", bar.get("spread_points"))
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
