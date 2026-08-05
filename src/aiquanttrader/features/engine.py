"""Bounded-memory incremental BTC microstructure feature engine."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from aiquanttrader.backtest.kernel import KernelMarketState
from aiquanttrader.domain.market import AggressorSide
from aiquanttrader.features.models import (
    MODEL_FEATURE_SCHEMA,
    FeatureEngineConfig,
    InventoryState,
    MicrostructureSnapshot,
    VolatilityRegime,
)

EMPTY_INVENTORY = InventoryState()


def _decimal(value: float) -> Decimal:
    if not math.isfinite(value):
        raise ValueError("feature calculation produced a non-finite value")
    return Decimal(str(value))


def _imbalance(bid: float, ask: float) -> float:
    total = bid + ask
    return 0.0 if total <= 0 else (bid - ask) / total


def _sigmoid(value: float) -> float:
    if value >= 0:
        inverse = math.exp(-value)
        return 1.0 / (1.0 + inverse)
    exponential = math.exp(value)
    return exponential / (1.0 + exponential)


@dataclass(frozen=True, slots=True)
class _TradeMarkout:
    observed_ts_ns: int
    side: float
    price: float


class IncrementalFeatureEngine:
    """Causal O(depth + new trades) update path with bounded time windows."""

    def __init__(self, config: FeatureEngineConfig) -> None:
        self.config = config
        self._flow: deque[tuple[int, float, float, float]] = deque()
        self._midpoints: deque[tuple[int, float]] = deque()
        self._returns: deque[tuple[int, float, float]] = deque()
        self._spreads: deque[tuple[int, float]] = deque()
        self._pending_markouts: deque[_TradeMarkout] = deque()
        self._markouts: deque[tuple[int, float]] = deque()
        self._last_receive_ts_ns: int | None = None
        self._last_mid: float | None = None
        self._last_spread_bps: float | None = None
        self._samples = 0

    @property
    def sample_count(self) -> int:
        return self._samples

    @property
    def ready(self) -> bool:
        return self._samples >= self.config.warmup_samples

    def update(
        self,
        market: KernelMarketState,
        *,
        inventory: InventoryState = EMPTY_INVENTORY,
        computed_ts_ns: int | None = None,
    ) -> MicrostructureSnapshot:
        receive_ts_ns = market.observed_ts_ns
        if self._last_receive_ts_ns is not None and receive_ts_ns <= self._last_receive_ts_ns:
            raise ValueError("feature inputs must be strictly increasing by receive time")
        computation_time = receive_ts_ns if computed_ts_ns is None else computed_ts_ns
        if computation_time < receive_ts_ns:
            raise ValueError("feature computation time cannot precede receipt time")
        trade_event_times = tuple(trade.exchange_ts_ns for trade in market.trades)
        maximum_event_ts = market.exchange_ts_ns
        oldest_required_input_ts = min((market.book_exchange_ts_ns, *trade_event_times))
        max_input_age_ns = receive_ts_ns - oldest_required_input_ts
        if max_input_age_ns > self.config.maximum_input_age_ns:
            raise ValueError("feature input exceeds maximum allowed age")

        bids = market.bids[: self.config.depth_levels]
        asks = market.asks[: self.config.depth_levels]
        bid_prices = np.asarray([float(level.price) for level in bids], dtype=np.float64)
        ask_prices = np.asarray([float(level.price) for level in asks], dtype=np.float64)
        bid_sizes = np.asarray([float(level.size) for level in bids], dtype=np.float64)
        ask_sizes = np.asarray([float(level.size) for level in asks], dtype=np.float64)
        if not all(
            np.all(np.isfinite(values)) for values in (bid_prices, ask_prices, bid_sizes, ask_sizes)
        ):
            raise ValueError("book contains non-finite values")

        best_bid = bid_prices[0]
        best_ask = ask_prices[0]
        mid = (best_bid + best_ask) / 2.0
        spread_bps = (best_ask - best_bid) / mid * 10_000.0
        best_bid_size = bid_sizes[0]
        best_ask_size = ask_sizes[0]
        book_imbalance = _imbalance(float(bid_sizes.sum()), float(ask_sizes.sum()))
        queue_imbalance = _imbalance(best_bid_size, best_ask_size)
        microprice = (best_ask * best_bid_size + best_bid * best_ask_size) / (
            best_bid_size + best_ask_size
        )

        paired = min(len(bids), len(asks))
        paired_bid_sizes = bid_sizes[:paired]
        paired_ask_sizes = ask_sizes[:paired]
        paired_total = float((paired_bid_sizes + paired_ask_sizes).sum())
        vamp = float(
            (ask_prices[:paired] * paired_bid_sizes + bid_prices[:paired] * paired_ask_sizes).sum()
            / paired_total
        )
        weighted_bid = float(np.average(bid_prices, weights=bid_sizes))
        weighted_ask = float(np.average(ask_prices, weights=ask_sizes))
        weighted_mid = (weighted_bid + weighted_ask) / 2.0

        for trade in market.trades:
            size = float(trade.size)
            sign = (
                1.0
                if trade.aggressor is AggressorSide.BUYER
                else -1.0
                if trade.aggressor is AggressorSide.SELLER
                else 0.0
            )
            buy = size if sign > 0 else 0.0
            sell = size if sign < 0 else 0.0
            self._flow.append((receive_ts_ns, buy, sell, sign * size))
            if sign:
                self._pending_markouts.append(
                    _TradeMarkout(receive_ts_ns, sign, float(trade.price))
                )
        self._expire(self._flow, receive_ts_ns - self.config.flow_window_ns)

        buy_pressure = sum(item[1] for item in self._flow)
        sell_pressure = sum(item[2] for item in self._flow)
        signed_volume = sum(item[3] for item in self._flow)
        total_aggressive = buy_pressure + sell_pressure
        flow_imbalance = 0.0 if total_aggressive == 0 else signed_volume / total_aggressive
        aggressor_ratio = 0.5 if total_aggressive == 0 else buy_pressure / total_aggressive

        mid_return_bps = 0.0
        if self._last_mid is not None:
            log_return = math.log(mid / self._last_mid)
            mid_return_bps = log_return * 10_000.0
            self._returns.append((receive_ts_ns, log_return, abs(mid - self._last_mid) / mid))
        self._midpoints.append((receive_ts_ns, mid))
        self._spreads.append((receive_ts_ns, spread_bps))
        self._expire(self._returns, receive_ts_ns - self.config.volatility_window_ns)
        self._expire(self._midpoints, receive_ts_ns - self.config.volatility_window_ns)
        self._expire(self._spreads, receive_ts_ns - self.config.spread_window_ns)

        realized_volatility = math.sqrt(sum(item[1] ** 2 for item in self._returns))
        atr_bps = (
            0.0
            if not self._returns
            else sum(item[2] for item in self._returns) / len(self._returns) * 10_000.0
        )
        spread_values = np.asarray([item[1] for item in self._spreads], dtype=np.float64)
        spread_mean = float(spread_values.mean())
        spread_std = float(spread_values.std())
        spread_zscore = 0.0 if spread_std <= 1e-12 else (spread_bps - spread_mean) / spread_std
        spread_change = 0.0 if self._last_spread_bps is None else spread_bps - self._last_spread_bps

        while (
            self._pending_markouts
            and receive_ts_ns - self._pending_markouts[0].observed_ts_ns
            >= self.config.markout_horizon_ns
        ):
            pending = self._pending_markouts.popleft()
            markout = pending.side * (mid - pending.price) / pending.price * 10_000.0
            self._markouts.append((receive_ts_ns, markout))
        self._expire(self._markouts, receive_ts_ns - self.config.volatility_window_ns)
        adverse_selection = (
            0.0
            if not self._markouts
            else sum(item[1] for item in self._markouts) / len(self._markouts)
        )

        self._samples += 1
        ready = self._samples >= self.config.warmup_samples
        realized_bps = realized_volatility * 10_000.0
        regime = self._regime(realized_bps, ready)
        inventory_drift = inventory.confirmed_base - inventory.target_base
        inventory_risk = min(
            1.0,
            abs(float(inventory_drift / self.config.inventory_limit_base)),
        )
        fill_bid = _sigmoid(-1.25 * queue_imbalance - flow_imbalance - 0.04 * spread_bps)
        fill_ask = _sigmoid(1.25 * queue_imbalance + flow_imbalance - 0.04 * spread_bps)
        snapshot = MicrostructureSnapshot(
            feature_schema_sha256=MODEL_FEATURE_SCHEMA.sha256(),
            sequence=market.sequence,
            event_ts_ns=maximum_event_ts,
            receive_ts_ns=receive_ts_ns,
            computed_ts_ns=computation_time,
            max_input_age_ns=max_input_age_ns,
            ready=ready,
            warmup_count=self._samples,
            best_bid=_decimal(best_bid),
            best_ask=_decimal(best_ask),
            midprice=_decimal(mid),
            book_imbalance=_decimal(book_imbalance),
            microprice=_decimal(microprice),
            vamp=_decimal(vamp),
            weighted_midprice=_decimal(weighted_mid),
            queue_imbalance=_decimal(queue_imbalance),
            depth_imbalance=_decimal(book_imbalance),
            trade_flow_imbalance=_decimal(flow_imbalance),
            buy_pressure=_decimal(buy_pressure),
            sell_pressure=_decimal(sell_pressure),
            aggressor_ratio=_decimal(aggressor_ratio),
            volume_delta=_decimal(signed_volume),
            signed_volume=_decimal(signed_volume),
            realized_volatility=_decimal(realized_volatility),
            volatility_regime=regime,
            atr_bps=_decimal(atr_bps),
            spread_bps=_decimal(spread_bps),
            spread_change_bps=_decimal(spread_change),
            spread_zscore=_decimal(spread_zscore),
            mid_return_bps=_decimal(mid_return_bps),
            inventory_base=inventory.confirmed_base,
            target_inventory_base=inventory.target_base,
            inventory_drift_base=inventory_drift,
            inventory_risk=_decimal(inventory_risk),
            liquidation_distance_bps=inventory.liquidation_distance_bps,
            margin_utilization=inventory.margin_utilization,
            fill_probability_bid=_decimal(fill_bid),
            fill_probability_ask=_decimal(fill_ask),
            queue_ahead_bid=_decimal(best_bid_size),
            queue_ahead_ask=_decimal(best_ask_size),
            adverse_selection_bps=_decimal(adverse_selection),
            fill_model_id=self.config.fill_model_id,
            fill_model_calibrated=self.config.fill_model_calibrated,
        )
        self._last_receive_ts_ns = receive_ts_ns
        self._last_mid = mid
        self._last_spread_bps = spread_bps
        return snapshot

    @staticmethod
    def _expire(window: deque[tuple], cutoff_ns: int) -> None:  # type: ignore[type-arg]
        while window and int(window[0][0]) < cutoff_ns:
            window.popleft()

    def _regime(self, realized_bps: float, ready: bool) -> VolatilityRegime:
        if not ready:
            return VolatilityRegime.WARMUP
        if realized_bps < float(self.config.low_volatility_bps):
            return VolatilityRegime.LOW
        if realized_bps > float(self.config.high_volatility_bps):
            return VolatilityRegime.HIGH
        return VolatilityRegime.NORMAL


def replay_features(
    states: Iterable[KernelMarketState],
    *,
    config: FeatureEngineConfig,
    inventory: InventoryState = EMPTY_INVENTORY,
) -> tuple[MicrostructureSnapshot, ...]:
    engine = IncrementalFeatureEngine(config)
    return tuple(engine.update(state, inventory=inventory) for state in states)
