"""Cost-aware short-horizon order-flow scalping kernel."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field

from aiquanttrader_native.backtest.kernel import KernelDecision
from aiquanttrader_native.domain.base import DomainModel
from aiquanttrader_native.domain.execution import OrderIntent, OrderKind, TimeInForce
from aiquanttrader_native.domain.market import OrderSide
from aiquanttrader_native.features.models import VolatilityRegime
from aiquanttrader_native.strategies.common import StrategyInput, StrategyTransition


class ScalperEntryStyle(StrEnum):
    TAKER = "taker"
    PASSIVE = "passive"


class OrderFlowScalperConfig(DomainModel):
    schema_version: Literal[1] = 1
    strategy_id: Literal["order-flow-scalper-v1"] = "order-flow-scalper-v1"
    entry_style: ScalperEntryStyle = ScalperEntryStyle.TAKER
    order_quantity_base: Annotated[Decimal, Field(gt=0)] = Decimal("0.001")
    max_abs_inventory_base: Annotated[Decimal, Field(gt=0)] = Decimal("0.01")
    imbalance_weight_bps: Annotated[Decimal, Field(ge=0)] = Decimal("3")
    flow_weight_bps: Annotated[Decimal, Field(ge=0)] = Decimal("4")
    momentum_weight: Annotated[Decimal, Field(ge=0)] = Decimal("0.5")
    safety_margin_bps: Annotated[Decimal, Field(gt=0)] = Decimal("1.5")
    maximum_spread_bps: Annotated[Decimal, Field(gt=0)] = Decimal("12")
    signal_threshold_bps: Annotated[Decimal, Field(gt=0)] = Decimal("2")
    cooldown_ns: int = Field(default=1_000_000_000, ge=0)
    reject_high_volatility: bool = True
    require_model_identity_when_forecasted: bool = True


class ScalperMemory(DomainModel):
    inventory_base: Decimal = Decimal("0")
    last_order_ts_ns: int | None = Field(default=None, ge=0)
    order_revision: int = Field(default=0, ge=0)

    def with_inventory(self, inventory_base: Decimal) -> ScalperMemory:
        return self.model_copy(update={"inventory_base": inventory_base})


class OrderFlowScalperKernel:
    def __init__(self, config: OrderFlowScalperConfig) -> None:
        self.config = config

    def decide(
        self, state: StrategyInput, memory: ScalperMemory
    ) -> StrategyTransition[ScalperMemory]:
        features = state.features
        if (
            not features.ready
            or features.spread_bps > self.config.maximum_spread_bps
            or (
                self.config.reject_high_volatility
                and features.volatility_regime is VolatilityRegime.HIGH
            )
            or (
                memory.last_order_ts_ns is not None
                and features.receive_ts_ns - memory.last_order_ts_ns < self.config.cooldown_ns
            )
        ):
            return StrategyTransition(memory=memory, decision=KernelDecision())
        if (
            state.movement_forecast_bps != 0
            and self.config.require_model_identity_when_forecasted
            and state.model_artifact_sha256 is None
        ):
            return StrategyTransition(memory=memory, decision=KernelDecision())

        expected_edge = (
            features.book_imbalance * self.config.imbalance_weight_bps
            + features.trade_flow_imbalance * self.config.flow_weight_bps
            + features.mid_return_bps * self.config.momentum_weight
            + state.movement_forecast_bps
        )
        required_edge = (
            state.estimated_taker_fee_bps
            + state.estimated_slippage_bps
            + self.config.safety_margin_bps
            if self.config.entry_style is ScalperEntryStyle.TAKER
            else self.config.safety_margin_bps
        )
        threshold = max(self.config.signal_threshold_bps, required_edge)
        if abs(expected_edge) < threshold:
            return StrategyTransition(memory=memory, decision=KernelDecision())

        side = OrderSide.BUY if expected_edge > 0 else OrderSide.SELL
        projected = memory.inventory_base + (
            self.config.order_quantity_base
            if side is OrderSide.BUY
            else -self.config.order_quantity_base
        )
        reducing = (memory.inventory_base > 0 and side is OrderSide.SELL) or (
            memory.inventory_base < 0 and side is OrderSide.BUY
        )
        if not reducing and abs(projected) > self.config.max_abs_inventory_base:
            return StrategyTransition(memory=memory, decision=KernelDecision())
        quantity = (
            min(abs(memory.inventory_base), self.config.order_quantity_base)
            if reducing
            else self.config.order_quantity_base
        )
        if quantity <= 0:
            return StrategyTransition(memory=memory, decision=KernelDecision())

        revision = memory.order_revision + 1
        intent_id = f"ofs-{side.value}-{features.receive_ts_ns}-{revision}"
        if self.config.entry_style is ScalperEntryStyle.TAKER:
            kind = OrderKind.MARKET
            limit_price = None
            time_in_force = TimeInForce.IOC
            post_only = False
        else:
            kind = OrderKind.LIMIT
            limit_price = features.best_bid if side is OrderSide.BUY else features.best_ask
            time_in_force = TimeInForce.GTC
            post_only = True
        intent = OrderIntent(
            intent_id=intent_id,
            strategy_id=self.config.strategy_id,
            side=side,
            kind=kind,
            quantity_base=quantity,
            limit_price=limit_price,
            time_in_force=time_in_force,
            post_only=post_only,
            reduce_only=reducing,
            created_ts_ns=features.receive_ts_ns,
            rationale=(f"post-cost order-flow edge={expected_edge}bps required={required_edge}bps"),
        )
        next_memory = ScalperMemory(
            inventory_base=memory.inventory_base,
            last_order_ts_ns=features.receive_ts_ns,
            order_revision=revision,
        )
        return StrategyTransition(
            memory=next_memory,
            decision=KernelDecision(submit=(intent,)),
        )
