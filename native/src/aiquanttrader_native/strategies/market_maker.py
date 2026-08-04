"""Inventory-aware Avellaneda-Stoikov passive quoting kernel."""

from __future__ import annotations

import math
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from aiquanttrader_native.backtest.kernel import KernelDecision
from aiquanttrader_native.domain.base import DomainModel
from aiquanttrader_native.domain.execution import OrderIntent, OrderKind, TimeInForce
from aiquanttrader_native.domain.market import OrderSide
from aiquanttrader_native.strategies.common import StrategyInput, StrategyTransition


class AvellanedaStoikovConfig(DomainModel):
    schema_version: Literal[1] = 1
    strategy_id: Literal["avellaneda-stoikov-v1"] = "avellaneda-stoikov-v1"
    risk_aversion_per_usd: Annotated[Decimal, Field(gt=0)] = Decimal("0.00001")
    arrival_intensity_per_usd: Annotated[Decimal, Field(gt=0)] = Decimal("1")
    horizon_seconds: Annotated[Decimal, Field(gt=0)] = Decimal("1")
    tick_size: Annotated[Decimal, Field(gt=0)] = Decimal("1")
    order_quantity_base: Annotated[Decimal, Field(gt=0)] = Decimal("0.001")
    max_abs_inventory_base: Annotated[Decimal, Field(gt=0)] = Decimal("0.05")
    minimum_half_spread_ticks: int = Field(default=1, ge=1)
    maximum_quote_spread_bps: Annotated[Decimal, Field(gt=0)] = Decimal("25")
    minimum_quote_lifetime_ns: int = Field(default=500_000_000, ge=0)
    quote_hysteresis_ticks: int = Field(default=1, ge=0)
    funding_skew_multiplier: Annotated[Decimal, Field(ge=0)] = Decimal("1")
    adverse_selection_multiplier: Annotated[Decimal, Field(ge=0)] = Decimal("0.25")
    minimum_fill_probability: Annotated[Decimal, Field(ge=0, le=1)] = Decimal("0.05")
    require_calibrated_fill_model: bool = True
    require_model_identity_when_forecasted: bool = True


class MarketMakerMemory(DomainModel):
    inventory_base: Decimal = Decimal("0")
    active_bid_intent_id: str | None = None
    active_ask_intent_id: str | None = None
    active_bid_price: Decimal | None = None
    active_ask_price: Decimal | None = None
    last_quote_ts_ns: int | None = Field(default=None, ge=0)
    quote_revision: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def quote_state_is_complete(self) -> Self:
        bid_fields = (self.active_bid_intent_id, self.active_bid_price)
        ask_fields = (self.active_ask_intent_id, self.active_ask_price)
        if any(value is None for value in bid_fields) != all(value is None for value in bid_fields):
            raise ValueError("active bid identity and price must be set together")
        if any(value is None for value in ask_fields) != all(value is None for value in ask_fields):
            raise ValueError("active ask identity and price must be set together")
        return self

    def with_inventory(self, inventory_base: Decimal) -> MarketMakerMemory:
        return self.model_copy(update={"inventory_base": inventory_base})


class AvellanedaStoikovKernel:
    """Pure quote calculator; fills update memory through ``with_inventory``."""

    def __init__(self, config: AvellanedaStoikovConfig) -> None:
        self.config = config

    def decide(
        self, state: StrategyInput, memory: MarketMakerMemory
    ) -> StrategyTransition[MarketMakerMemory]:
        features = state.features
        has_forecast = (
            state.movement_forecast_bps != 0
            or state.spread_expansion_forecast_bps != 0
            or state.fill_forecast_bid is not None
            or state.fill_forecast_ask is not None
        )
        unsafe = (
            not features.ready
            or features.spread_bps > self.config.maximum_quote_spread_bps
            or (self.config.require_calibrated_fill_model and not features.fill_model_calibrated)
            or (
                self.config.require_model_identity_when_forecasted
                and has_forecast
                and state.model_artifact_sha256 is None
            )
        )
        if unsafe:
            return self._cancel_all(memory)
        if (
            memory.last_quote_ts_ns is not None
            and features.receive_ts_ns - memory.last_quote_ts_ns
            < self.config.minimum_quote_lifetime_ns
        ):
            return StrategyTransition(memory=memory, decision=KernelDecision())

        mid = features.midprice
        sigma_price = mid * features.realized_volatility
        gamma = self.config.risk_aversion_per_usd
        horizon = self.config.horizon_seconds
        intensity = self.config.arrival_intensity_per_usd
        reservation = mid - memory.inventory_base * gamma * sigma_price * sigma_price * horizon
        reservation -= state.funding_rate * mid * self.config.funding_skew_multiplier
        reservation -= (
            features.adverse_selection_bps
            * mid
            / Decimal("10000")
            * self.config.adverse_selection_multiplier
        )
        reservation += state.movement_forecast_bps * mid / Decimal("10000")

        risk_half_spread = gamma * sigma_price * sigma_price * horizon / Decimal("2")
        liquidity_half_spread = Decimal(str(math.log1p(float(gamma / intensity)) / float(gamma)))
        forecast_half_spread = state.spread_expansion_forecast_bps * mid / Decimal("20000")
        minimum_half_spread = self.config.tick_size * self.config.minimum_half_spread_ticks
        half_spread = max(
            minimum_half_spread,
            risk_half_spread + liquidity_half_spread + forecast_half_spread,
        )
        bid_price = self._round_down(min(reservation - half_spread, features.best_bid))
        ask_price = self._round_up(max(reservation + half_spread, features.best_ask))
        if bid_price <= 0 or bid_price >= ask_price:
            return self._cancel_all(memory)

        bid_fill_probability = (
            features.fill_probability_bid
            if state.fill_forecast_bid is None
            else state.fill_forecast_bid
        )
        ask_fill_probability = (
            features.fill_probability_ask
            if state.fill_forecast_ask is None
            else state.fill_forecast_ask
        )
        bid_allowed = (
            memory.inventory_base + self.config.order_quantity_base
            <= self.config.max_abs_inventory_base
            and bid_fill_probability >= self.config.minimum_fill_probability
        )
        ask_allowed = (
            memory.inventory_base - self.config.order_quantity_base
            >= -self.config.max_abs_inventory_base
            and ask_fill_probability >= self.config.minimum_fill_probability
        )
        if self._within_hysteresis(
            memory, bid_price, ask_price, bid_allowed=bid_allowed, ask_allowed=ask_allowed
        ):
            return StrategyTransition(memory=memory, decision=KernelDecision())

        revision = memory.quote_revision + 1
        bid_id = f"as-bid-{features.receive_ts_ns}-{revision}" if bid_allowed else None
        ask_id = f"as-ask-{features.receive_ts_ns}-{revision}" if ask_allowed else None
        cancellations = tuple(
            intent_id
            for intent_id in (memory.active_bid_intent_id, memory.active_ask_intent_id)
            if intent_id is not None
        )
        quantity = self.config.order_quantity_base
        orders: list[OrderIntent] = []
        if bid_id is not None:
            orders.append(
                OrderIntent(
                    intent_id=bid_id,
                    strategy_id=self.config.strategy_id,
                    side=OrderSide.BUY,
                    kind=OrderKind.LIMIT,
                    quantity_base=quantity,
                    limit_price=bid_price,
                    time_in_force=TimeInForce.GTC,
                    post_only=True,
                    created_ts_ns=features.receive_ts_ns,
                    rationale="Avellaneda-Stoikov inventory-aware passive bid",
                )
            )
        if ask_id is not None:
            orders.append(
                OrderIntent(
                    intent_id=ask_id,
                    strategy_id=self.config.strategy_id,
                    side=OrderSide.SELL,
                    kind=OrderKind.LIMIT,
                    quantity_base=quantity,
                    limit_price=ask_price,
                    time_in_force=TimeInForce.GTC,
                    post_only=True,
                    created_ts_ns=features.receive_ts_ns,
                    rationale="Avellaneda-Stoikov inventory-aware passive ask",
                )
            )
        next_memory = MarketMakerMemory(
            inventory_base=memory.inventory_base,
            active_bid_intent_id=bid_id,
            active_ask_intent_id=ask_id,
            active_bid_price=bid_price if bid_allowed else None,
            active_ask_price=ask_price if ask_allowed else None,
            last_quote_ts_ns=features.receive_ts_ns,
            quote_revision=revision,
        )
        return StrategyTransition(
            memory=next_memory,
            decision=KernelDecision(submit=tuple(orders), cancel_intent_ids=cancellations),
        )

    def _cancel_all(self, memory: MarketMakerMemory) -> StrategyTransition[MarketMakerMemory]:
        cancellations = tuple(
            intent_id
            for intent_id in (memory.active_bid_intent_id, memory.active_ask_intent_id)
            if intent_id is not None
        )
        if not cancellations:
            return StrategyTransition(memory=memory, decision=KernelDecision())
        cleared = MarketMakerMemory(
            inventory_base=memory.inventory_base,
            last_quote_ts_ns=memory.last_quote_ts_ns,
            quote_revision=memory.quote_revision,
        )
        return StrategyTransition(
            memory=cleared,
            decision=KernelDecision(cancel_intent_ids=cancellations),
        )

    def _within_hysteresis(
        self,
        memory: MarketMakerMemory,
        bid_price: Decimal,
        ask_price: Decimal,
        *,
        bid_allowed: bool,
        ask_allowed: bool,
    ) -> bool:
        if (memory.active_bid_price is not None) != bid_allowed or (
            memory.active_ask_price is not None
        ) != ask_allowed:
            return False
        tolerance = self.config.tick_size * self.config.quote_hysteresis_ticks
        bid_same = not bid_allowed or (
            memory.active_bid_price is not None
            and abs(memory.active_bid_price - bid_price) <= tolerance
        )
        ask_same = not ask_allowed or (
            memory.active_ask_price is not None
            and abs(memory.active_ask_price - ask_price) <= tolerance
        )
        return bid_same and ask_same

    def _round_down(self, value: Decimal) -> Decimal:
        ticks = (value / self.config.tick_size).to_integral_value(rounding=ROUND_FLOOR)
        return ticks * self.config.tick_size

    def _round_up(self, value: Decimal) -> Decimal:
        ticks = (value / self.config.tick_size).to_integral_value(rounding=ROUND_CEILING)
        return ticks * self.config.tick_size
