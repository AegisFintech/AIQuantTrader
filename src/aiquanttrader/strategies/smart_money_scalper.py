"""Causal 15m/5m/1m smart-money scalper with a bounded position lifecycle."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from aiquanttrader.backtest.kernel import KernelDecision, StrategyAction
from aiquanttrader.domain.base import DomainModel
from aiquanttrader.domain.execution import OrderIntent, OrderKind, TimeInForce
from aiquanttrader.domain.market import OrderSide
from aiquanttrader.features.market_structure import StructureDirection
from aiquanttrader.features.models import VolatilityRegime
from aiquanttrader.strategies.common import StrategyInput, StrategyTransition

ONE_SECOND_NS = 1_000_000_000


class SmartMoneyScalperConfig(DomainModel):
    schema_version: Literal[1] = 1
    strategy_id: Literal["smart-money-scalper-v1"] = "smart-money-scalper-v1"
    order_quantity_base: Annotated[Decimal, Field(gt=0)] = Decimal("0.001")
    maximum_spread_bps: Annotated[Decimal, Field(gt=0)] = Decimal("5")
    minimum_confluence_score: int = Field(default=5, ge=3, le=15)
    minimum_directional_edge_bps: Annotated[Decimal, Field(gt=0)] = Decimal("1.5")
    structure_point_value_bps: Annotated[Decimal, Field(gt=0)] = Decimal("1.8")
    imbalance_weight_bps: Annotated[Decimal, Field(ge=0)] = Decimal("2.5")
    flow_weight_bps: Annotated[Decimal, Field(ge=0)] = Decimal("3.5")
    momentum_weight: Annotated[Decimal, Field(ge=0)] = Decimal("0.4")
    safety_margin_bps: Annotated[Decimal, Field(gt=0)] = Decimal("1.5")
    stop_loss_bps: Annotated[Decimal, Field(gt=0)] = Decimal("12")
    take_profit_bps: Annotated[Decimal, Field(gt=0)] = Decimal("20")
    break_even_trigger_bps: Annotated[Decimal, Field(gt=0)] = Decimal("13")
    break_even_offset_bps: Annotated[Decimal, Field(ge=0)] = Decimal("10")
    soft_holding_limit_ns: int = Field(default=90 * ONE_SECOND_NS, ge=10 * ONE_SECOND_NS)
    hard_holding_limit_ns: int = Field(default=300 * ONE_SECOND_NS, ge=30 * ONE_SECOND_NS)
    cooldown_ns: int = Field(default=20 * ONE_SECOND_NS, ge=0)
    exit_retry_ns: int = Field(
        default=2 * ONE_SECOND_NS,
        ge=250_000_000,
        le=10 * ONE_SECOND_NS,
    )
    reject_high_volatility: bool = True

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        if self.soft_holding_limit_ns >= self.hard_holding_limit_ns:
            raise ValueError("soft holding limit must be below the hard holding limit")
        if self.hard_holding_limit_ns > 300 * ONE_SECOND_NS:
            raise ValueError("smart-money scalper hard holding limit cannot exceed five minutes")
        if self.break_even_trigger_bps >= self.take_profit_bps:
            raise ValueError("break-even trigger must precede take profit")
        if self.break_even_offset_bps >= self.break_even_trigger_bps:
            raise ValueError("break-even offset must be below its trigger")
        return self


class SmartMoneyScalperMemory(DomainModel):
    inventory_base: Decimal = Decimal("0")
    average_entry_price: Decimal | None = Field(default=None, gt=0)
    position_opened_ts_ns: int | None = Field(default=None, ge=0)
    last_flat_ts_ns: int | None = Field(default=None, ge=0)
    last_order_ts_ns: int | None = Field(default=None, ge=0)
    last_entry_structure_revision: int | None = Field(default=None, ge=0)
    peak_favorable_bps: Annotated[Decimal, Field(ge=0)] = Decimal("0")
    order_revision: int = Field(default=0, ge=0)

    def synchronize_position(
        self,
        inventory_base: Decimal,
        average_entry_price: Decimal | None,
        observed_ts_ns: int,
    ) -> SmartMoneyScalperMemory:
        if inventory_base == 0:
            transitioned_to_flat = self.inventory_base != 0
            return self.model_copy(
                update={
                    "inventory_base": Decimal("0"),
                    "average_entry_price": None,
                    "position_opened_ts_ns": None,
                    "last_flat_ts_ns": (
                        observed_ts_ns if transitioned_to_flat else self.last_flat_ts_ns
                    ),
                    "peak_favorable_bps": Decimal("0"),
                }
            )
        if average_entry_price is None or average_entry_price <= 0:
            raise ValueError("non-flat strategy inventory requires an average entry price")
        opened = (
            observed_ts_ns
            if self.inventory_base == 0 or self.position_opened_ts_ns is None
            else self.position_opened_ts_ns
        )
        return self.model_copy(
            update={
                "inventory_base": inventory_base,
                "average_entry_price": average_entry_price,
                "position_opened_ts_ns": opened,
            }
        )


class SmartMoneyScalperKernel:
    def __init__(self, config: SmartMoneyScalperConfig) -> None:
        self.config = config

    def decide(
        self, state: StrategyInput, memory: SmartMoneyScalperMemory
    ) -> StrategyTransition[SmartMoneyScalperMemory]:
        features = state.features
        if memory.inventory_base != 0:
            return self._manage_position(state, memory)
        structure = state.market_structure
        if not features.ready or structure is None or not structure.ready:
            return self._transition(memory, StrategyAction.WARMUP, "causal_timeframes_warming")
        if features.spread_bps > self.config.maximum_spread_bps:
            return self._transition(
                memory, StrategyAction.BLOCKED_SPREAD, "spread_above_entry_limit"
            )
        if (
            self.config.reject_high_volatility
            and features.volatility_regime is VolatilityRegime.HIGH
        ):
            return self._transition(
                memory, StrategyAction.BLOCKED_VOLATILITY, "high_volatility_regime"
            )
        if (
            memory.last_flat_ts_ns is not None
            and features.receive_ts_ns - memory.last_flat_ts_ns < self.config.cooldown_ns
        ):
            return self._transition(memory, StrategyAction.BLOCKED_COOLDOWN, "post_exit_cooldown")
        if memory.last_entry_structure_revision == structure.revision:
            return self._transition(
                memory,
                StrategyAction.BLOCKED_COOLDOWN,
                "one_entry_attempt_per_closed_bar_revision",
            )

        long_edge = self._directional_edge(state, bullish=True)
        short_edge = self._directional_edge(state, bullish=False)
        if structure.fifteen_minute.direction is StructureDirection.BULLISH:
            side = OrderSide.BUY
            score = structure.long_confluence
            expected_edge = long_edge
            reasons = structure.long_reasons
        elif structure.fifteen_minute.direction is StructureDirection.BEARISH:
            side = OrderSide.SELL
            score = structure.short_confluence
            expected_edge = short_edge
            reasons = structure.short_reasons
        else:
            return self._transition(
                memory, StrategyAction.BLOCKED_CONFLUENCE, "15m_direction_neutral"
            )
        if score < self.config.minimum_confluence_score:
            return self._transition(
                memory,
                StrategyAction.BLOCKED_CONFLUENCE,
                "multi_timeframe_confluence_below_threshold",
                expected_edge=expected_edge,
                confluence=score,
            )
        required_edge = (
            Decimal("2") * (state.estimated_taker_fee_bps + state.estimated_slippage_bps)
            + self.config.safety_margin_bps
        )
        required_directional = max(self.config.minimum_directional_edge_bps, required_edge)
        if expected_edge < required_directional:
            return self._transition(
                memory,
                StrategyAction.BLOCKED_COST,
                "expected_move_does_not_clear_entry_cost",
                expected_edge=expected_edge,
                required_edge=required_directional,
                confluence=score,
            )

        revision = memory.order_revision + 1
        intent = OrderIntent(
            intent_id=f"sms-{side.value}-{features.receive_ts_ns}-{revision}",
            strategy_id=self.config.strategy_id,
            side=side,
            kind=OrderKind.MARKET,
            quantity_base=self.config.order_quantity_base,
            time_in_force=TimeInForce.IOC,
            post_only=False,
            reduce_only=False,
            created_ts_ns=features.receive_ts_ns,
            rationale=(
                f"15m/5m/1m causal SMC edge={expected_edge}bps "
                f"required={required_directional}bps score={score} reasons={','.join(reasons)}"
            ),
        )
        action = StrategyAction.ENTER_LONG if side is OrderSide.BUY else StrategyAction.ENTER_SHORT
        stop, target = self._exit_prices(features.midprice, side)
        next_memory = memory.model_copy(
            update={
                "last_order_ts_ns": features.receive_ts_ns,
                "last_entry_structure_revision": structure.revision,
                "order_revision": revision,
            }
        )
        return StrategyTransition(
            memory=next_memory,
            decision=KernelDecision(
                submit=(intent,),
                action=action,
                reason="multi_timeframe_smc_entry",
                expected_edge_bps=expected_edge,
                required_edge_bps=required_directional,
                confluence_score=score,
                reference_price=features.midprice,
                stop_price=stop,
                target_price=target,
            ),
        )

    def _manage_position(
        self, state: StrategyInput, memory: SmartMoneyScalperMemory
    ) -> StrategyTransition[SmartMoneyScalperMemory]:
        features = state.features
        entry = memory.average_entry_price or state.position_average_entry_price
        opened = memory.position_opened_ts_ns or state.position_opened_ts_ns
        if entry is None or opened is None:
            return self._transition(
                memory, StrategyAction.HOLD, "awaiting_confirmed_position_context"
            )
        long_position = memory.inventory_base > 0
        direction = Decimal("1") if long_position else Decimal("-1")
        pnl_bps = direction * (features.midprice - entry) / entry * Decimal("10000")
        peak = max(memory.peak_favorable_bps, pnl_bps)
        age_ns = max(0, features.receive_ts_ns - opened)
        updated = memory.model_copy(update={"peak_favorable_bps": peak})
        stop_threshold = -self.config.stop_loss_bps
        if peak >= self.config.break_even_trigger_bps:
            stop_threshold = self.config.break_even_offset_bps

        action: StrategyAction | None = None
        reason = "position_within_exit_bounds"
        if pnl_bps >= self.config.take_profit_bps:
            action = StrategyAction.EXIT_TAKE_PROFIT
            reason = "take_profit_reached"
        elif pnl_bps <= stop_threshold:
            action = StrategyAction.EXIT_STOP_LOSS
            reason = "break_even_stop_reached" if stop_threshold >= 0 else "hard_stop_reached"
        elif age_ns >= self.config.hard_holding_limit_ns:
            action = StrategyAction.EXIT_TIME_LIMIT
            reason = "five_minute_hard_limit"
        elif age_ns >= self.config.soft_holding_limit_ns and pnl_bps <= Decimal("2"):
            action = StrategyAction.EXIT_TIME_LIMIT
            reason = "ninety_second_no_progress_exit"
        elif self._opposite_confirmation(state, long_position=long_position):
            action = StrategyAction.EXIT_OPPOSITE_FLOW
            reason = "opposite_1m_structure_and_order_flow"

        stop, target = self._exit_prices(
            entry,
            OrderSide.BUY if long_position else OrderSide.SELL,
            stop_threshold=stop_threshold,
        )
        age_seconds = Decimal(age_ns) / Decimal(ONE_SECOND_NS)
        if action is None:
            return StrategyTransition(
                memory=updated,
                decision=KernelDecision(
                    action=StrategyAction.HOLD,
                    reason=reason,
                    expected_edge_bps=pnl_bps,
                    reference_price=features.midprice,
                    stop_price=stop,
                    target_price=target,
                    position_age_seconds=age_seconds,
                ),
            )

        if (
            memory.last_order_ts_ns is not None
            and features.receive_ts_ns - memory.last_order_ts_ns < self.config.exit_retry_ns
        ):
            return StrategyTransition(
                memory=updated,
                decision=KernelDecision(
                    action=StrategyAction.HOLD,
                    reason="exit_order_pending_activation",
                    expected_edge_bps=pnl_bps,
                    reference_price=features.midprice,
                    stop_price=stop,
                    target_price=target,
                    position_age_seconds=age_seconds,
                ),
            )

        side = OrderSide.SELL if long_position else OrderSide.BUY
        revision = memory.order_revision + 1
        intent = OrderIntent(
            intent_id=f"sms-exit-{side.value}-{features.receive_ts_ns}-{revision}",
            strategy_id=self.config.strategy_id,
            side=side,
            kind=OrderKind.MARKET,
            quantity_base=abs(memory.inventory_base),
            time_in_force=TimeInForce.IOC,
            post_only=False,
            reduce_only=True,
            created_ts_ns=features.receive_ts_ns,
            rationale=f"{reason}; gross_move={pnl_bps}bps age={age_seconds}s",
        )
        next_memory = updated.model_copy(
            update={"last_order_ts_ns": features.receive_ts_ns, "order_revision": revision}
        )
        return StrategyTransition(
            memory=next_memory,
            decision=KernelDecision(
                submit=(intent,),
                action=action,
                reason=reason,
                expected_edge_bps=pnl_bps,
                confluence_score=self._active_confluence(state, long_position),
                reference_price=features.midprice,
                stop_price=stop,
                target_price=target,
                position_age_seconds=age_seconds,
            ),
        )

    def _directional_edge(self, state: StrategyInput, *, bullish: bool) -> Decimal:
        features = state.features
        structure = state.market_structure
        assert structure is not None
        sign = Decimal("1") if bullish else Decimal("-1")
        score = structure.long_confluence if bullish else structure.short_confluence
        micro_edge = sign * (
            features.book_imbalance * self.config.imbalance_weight_bps
            + features.trade_flow_imbalance * self.config.flow_weight_bps
            + features.mid_return_bps * self.config.momentum_weight
        )
        return Decimal(score) * self.config.structure_point_value_bps + micro_edge

    @staticmethod
    def _active_confluence(state: StrategyInput, long_position: bool) -> int:
        structure = state.market_structure
        if structure is None:
            return 0
        return structure.long_confluence if long_position else structure.short_confluence

    @staticmethod
    def _opposite_confirmation(state: StrategyInput, *, long_position: bool) -> bool:
        structure = state.market_structure
        if structure is None or not structure.ready:
            return False
        one = structure.one_minute
        if long_position:
            structural = one.bearish_bos or one.bearish_choch or one.bearish_sweep
            return structural and state.features.trade_flow_imbalance < Decimal("-0.20")
        structural = one.bullish_bos or one.bullish_choch or one.bullish_sweep
        return structural and state.features.trade_flow_imbalance > Decimal("0.20")

    def _exit_prices(
        self,
        entry: Decimal,
        side: OrderSide,
        *,
        stop_threshold: Decimal | None = None,
    ) -> tuple[Decimal, Decimal]:
        direction = Decimal("1") if side is OrderSide.BUY else Decimal("-1")
        stop_bps = -self.config.stop_loss_bps if stop_threshold is None else stop_threshold
        stop = entry * (Decimal("1") + direction * stop_bps / Decimal("10000"))
        target = entry * (Decimal("1") + direction * self.config.take_profit_bps / Decimal("10000"))
        return stop, target

    @staticmethod
    def _transition(
        memory: SmartMoneyScalperMemory,
        action: StrategyAction,
        reason: str,
        *,
        expected_edge: Decimal = Decimal("0"),
        required_edge: Decimal = Decimal("0"),
        confluence: int = 0,
    ) -> StrategyTransition[SmartMoneyScalperMemory]:
        return StrategyTransition(
            memory=memory,
            decision=KernelDecision(
                action=action,
                reason=reason,
                expected_edge_bps=expected_edge,
                required_edge_bps=required_edge,
                confluence_score=confluence,
            ),
        )
