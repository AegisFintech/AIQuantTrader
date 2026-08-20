"""Causal, maker-first BTC scalper with bounded online forecast adaptation."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from aiquanttrader.backtest.kernel import KernelDecision, StrategyAction
from aiquanttrader.domain.base import DomainModel
from aiquanttrader.domain.execution import OrderIntent, OrderKind, TimeInForce
from aiquanttrader.domain.market import OrderSide
from aiquanttrader.features.market_structure import StructureDirection
from aiquanttrader.features.models import MicrostructureSnapshot, VolatilityRegime
from aiquanttrader.strategies.common import StrategyInput, StrategyTransition

ONE_SECOND_NS = 1_000_000_000
ONE_DAY_NS = 86_400 * ONE_SECOND_NS
FORECAST_FEATURE_COUNT = 8


def _zeros() -> tuple[Decimal, ...]:
    return (Decimal("0"),) * FORECAST_FEATURE_COUNT


class AdaptiveScalperConfig(DomainModel):
    """Immutable economics, signal, lifecycle, and online-learning policy."""

    schema_version: Literal[1] = 1
    strategy_id: Literal["smart-money-scalper-v2"] = "smart-money-scalper-v2"
    order_quantity_base: Annotated[Decimal, Field(gt=0)] = Decimal("0.001")
    maximum_spread_bps: Annotated[Decimal, Field(gt=0)] = Decimal("1")
    minimum_confluence_score: int = Field(default=7, ge=3, le=20)
    minimum_book_imbalance: Annotated[Decimal, Field(ge=0, le=1)] = Decimal("0.20")
    minimum_trade_flow_imbalance: Annotated[Decimal, Field(ge=0, le=1)] = Decimal("0.15")
    minimum_microprice_edge_bps: Annotated[Decimal, Field(ge=0)] = Decimal("0.02")
    minimum_forecast_edge_bps: Annotated[Decimal, Field(gt=0)] = Decimal("8")
    minimum_net_edge_bps: Annotated[Decimal, Field(gt=0)] = Decimal("2.5")
    safety_margin_bps: Annotated[Decimal, Field(gt=0)] = Decimal("2.5")
    stop_loss_bps: Annotated[Decimal, Field(gt=0)] = Decimal("8")
    take_profit_bps: Annotated[Decimal, Field(gt=0)] = Decimal("18")
    break_even_trigger_bps: Annotated[Decimal, Field(gt=0)] = Decimal("11")
    break_even_offset_bps: Annotated[Decimal, Field(ge=0)] = Decimal("7")
    trailing_trigger_bps: Annotated[Decimal, Field(gt=0)] = Decimal("14")
    trailing_giveback_bps: Annotated[Decimal, Field(gt=0)] = Decimal("5")
    reversal_forecast_bps: Annotated[Decimal, Field(gt=0)] = Decimal("5")
    soft_holding_limit_ns: int = Field(default=60 * ONE_SECOND_NS, ge=10 * ONE_SECOND_NS)
    hard_holding_limit_ns: int = Field(default=180 * ONE_SECOND_NS, ge=30 * ONE_SECOND_NS)
    cooldown_ns: int = Field(default=60 * ONE_SECOND_NS, ge=0)
    maker_entry_ttl_ns: int = Field(
        default=3 * ONE_SECOND_NS,
        ge=250_000_000,
        le=10 * ONE_SECOND_NS,
    )
    exit_retry_ns: int = Field(
        default=2 * ONE_SECOND_NS,
        ge=250_000_000,
        le=10 * ONE_SECOND_NS,
    )
    maximum_entry_attempts_per_day: int = Field(default=48, ge=1, le=500)
    reject_high_volatility: bool = True
    forecast_horizon_ns: int = Field(
        default=30 * ONE_SECOND_NS,
        ge=ONE_SECOND_NS,
        le=300 * ONE_SECOND_NS,
    )
    forecast_sample_interval_ns: int = Field(
        default=ONE_SECOND_NS,
        ge=100_000_000,
        le=10 * ONE_SECOND_NS,
    )
    forecast_minimum_samples: int = Field(default=500, ge=100, le=1_000_000)
    forecast_recent_window: int = Field(default=256, ge=50, le=2_048)
    forecast_minimum_directional_accuracy: Annotated[
        Decimal, Field(ge=Decimal("0.50"), le=Decimal("0.90"))
    ] = Decimal("0.54")
    forecast_maximum_mae_bps: Annotated[Decimal, Field(gt=0)] = Decimal("12")
    forecast_learning_rate: Annotated[Decimal, Field(gt=0, le=Decimal("0.20"))] = Decimal("0.012")
    forecast_learning_rate_decay_samples: int = Field(default=5_000, ge=100)
    forecast_l2_penalty: Annotated[Decimal, Field(ge=0, le=Decimal("0.10"))] = Decimal("0.0005")
    forecast_huber_delta_bps: Annotated[Decimal, Field(gt=0)] = Decimal("8")
    forecast_prediction_clip_bps: Annotated[Decimal, Field(gt=0)] = Decimal("30")

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        if self.soft_holding_limit_ns >= self.hard_holding_limit_ns:
            raise ValueError("soft holding limit must be below the hard holding limit")
        if self.hard_holding_limit_ns > 300 * ONE_SECOND_NS:
            raise ValueError("adaptive scalper hard holding limit cannot exceed five minutes")
        if self.break_even_trigger_bps >= self.take_profit_bps:
            raise ValueError("break-even trigger must precede take profit")
        if self.break_even_offset_bps >= self.break_even_trigger_bps:
            raise ValueError("break-even offset must be below its trigger")
        if self.trailing_trigger_bps >= self.take_profit_bps:
            raise ValueError("trailing trigger must precede take profit")
        if self.trailing_giveback_bps >= self.trailing_trigger_bps:
            raise ValueError("trailing giveback must be below its trigger")
        if self.forecast_recent_window > self.forecast_minimum_samples:
            raise ValueError("forecast recent window cannot exceed minimum samples")
        if self.forecast_sample_interval_ns > self.forecast_horizon_ns:
            raise ValueError("forecast sample interval cannot exceed its horizon")
        if self.forecast_horizon_ns > 128 * self.forecast_sample_interval_ns:
            raise ValueError("forecast pending-label policy exceeds the checkpoint-safe bound")
        return self


class PendingForecast(DomainModel):
    observed_ts_ns: int = Field(ge=0)
    midprice: Annotated[Decimal, Field(gt=0)]
    vector: tuple[Decimal, ...]
    prediction_bps: Decimal

    @model_validator(mode="after")
    def validate_vector(self) -> Self:
        if len(self.vector) != FORECAST_FEATURE_COUNT:
            raise ValueError("pending forecast vector has an unexpected width")
        return self


class AdaptiveForecastState(DomainModel):
    """All online-learning state is explicit, bounded, and checkpointable."""

    weights: tuple[Decimal, ...] = Field(default_factory=_zeros)
    pending: tuple[PendingForecast, ...] = ()
    training_samples: int = Field(default=0, ge=0)
    recent_direction_hits: tuple[bool, ...] = ()
    recent_absolute_errors_bps: tuple[Annotated[Decimal, Field(ge=0)], ...] = ()
    latest_prediction_bps: Decimal = Decimal("0")

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if len(self.weights) != FORECAST_FEATURE_COUNT:
            raise ValueError("adaptive forecast weight vector has an unexpected width")
        if len(self.pending) > 4_096:
            raise ValueError("adaptive forecast pending-label buffer exceeds its hard bound")
        if len(self.recent_direction_hits) > 2_048:
            raise ValueError("adaptive forecast hit window exceeds its hard bound")
        if len(self.recent_absolute_errors_bps) > 2_048:
            raise ValueError("adaptive forecast error window exceeds its hard bound")
        if len(self.recent_direction_hits) != len(self.recent_absolute_errors_bps):
            raise ValueError("adaptive forecast diagnostic windows must remain aligned")
        return self

    def ready(self, config: AdaptiveScalperConfig) -> bool:
        return (
            self.training_samples >= config.forecast_minimum_samples
            and len(self.recent_direction_hits) >= config.forecast_recent_window
        )

    @property
    def directional_accuracy(self) -> Decimal:
        if not self.recent_direction_hits:
            return Decimal("0")
        return Decimal(sum(self.recent_direction_hits)) / Decimal(len(self.recent_direction_hits))

    @property
    def mean_absolute_error_bps(self) -> Decimal:
        if not self.recent_absolute_errors_bps:
            return Decimal("0")
        return sum(self.recent_absolute_errors_bps, Decimal("0")) / Decimal(
            len(self.recent_absolute_errors_bps)
        )


class AdaptiveScalperMemory(DomainModel):
    inventory_base: Decimal = Decimal("0")
    average_entry_price: Decimal | None = Field(default=None, gt=0)
    position_opened_ts_ns: int | None = Field(default=None, ge=0)
    last_flat_ts_ns: int | None = Field(default=None, ge=0)
    last_order_ts_ns: int | None = Field(default=None, ge=0)
    last_entry_structure_revision: int | None = Field(default=None, ge=0)
    pending_entry_intent_id: str | None = None
    pending_entry_created_ts_ns: int | None = Field(default=None, ge=0)
    peak_favorable_bps: Annotated[Decimal, Field(ge=0)] = Decimal("0")
    entry_attempt_day: int | None = Field(default=None, ge=0)
    entry_attempts_today: int = Field(default=0, ge=0)
    order_revision: int = Field(default=0, ge=0)
    forecast: AdaptiveForecastState = Field(default_factory=AdaptiveForecastState)

    @model_validator(mode="after")
    def validate_pending_entry(self) -> Self:
        if (self.pending_entry_intent_id is None) != (self.pending_entry_created_ts_ns is None):
            raise ValueError("pending entry identity and timestamp must be supplied together")
        return self

    def synchronize_position(
        self,
        inventory_base: Decimal,
        average_entry_price: Decimal | None,
        observed_ts_ns: int,
    ) -> AdaptiveScalperMemory:
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
            raise ValueError("non-flat adaptive inventory requires an average entry price")
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
                "pending_entry_intent_id": None,
                "pending_entry_created_ts_ns": None,
            }
        )


class AdaptiveScalperKernel:
    """Pure causal challenger; online changes are explicit strategy memory only."""

    def __init__(self, config: AdaptiveScalperConfig) -> None:
        self.config = config

    def decide(
        self, state: StrategyInput, memory: AdaptiveScalperMemory
    ) -> StrategyTransition[AdaptiveScalperMemory]:
        forecast = self._update_forecast(state.features, memory.forecast)
        memory = memory.model_copy(update={"forecast": forecast})
        if memory.inventory_base != 0:
            return self._manage_position(state, memory)
        if memory.pending_entry_intent_id is not None:
            return self._manage_pending_entry(state, memory)

        features = state.features
        structure = state.market_structure
        if not features.ready or structure is None or not structure.ready:
            return self._transition(memory, StrategyAction.WARMUP, "causal_timeframes_warming")
        if not forecast.ready(self.config):
            return self._transition(
                memory,
                StrategyAction.WARMUP,
                "causal_online_forecast_warming",
                expected_edge=abs(forecast.latest_prediction_bps),
            )
        if forecast.directional_accuracy < self.config.forecast_minimum_directional_accuracy:
            return self._transition(
                memory,
                StrategyAction.BLOCKED_MODEL,
                "forecast_directional_accuracy_below_gate",
                expected_edge=abs(forecast.latest_prediction_bps),
            )
        if forecast.mean_absolute_error_bps > self.config.forecast_maximum_mae_bps:
            return self._transition(
                memory,
                StrategyAction.BLOCKED_MODEL,
                "forecast_error_above_gate",
                expected_edge=abs(forecast.latest_prediction_bps),
            )
        if features.spread_bps > self.config.maximum_spread_bps:
            return self._transition(
                memory, StrategyAction.BLOCKED_SPREAD, "spread_above_maker_entry_limit"
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

        day = features.receive_ts_ns // ONE_DAY_NS
        attempts = memory.entry_attempts_today if memory.entry_attempt_day == day else 0
        if attempts >= self.config.maximum_entry_attempts_per_day:
            return self._transition(
                memory.model_copy(
                    update={"entry_attempt_day": day, "entry_attempts_today": attempts}
                ),
                StrategyAction.BLOCKED_INVENTORY,
                "daily_entry_attempt_cap_reached",
            )

        direction = structure.fifteen_minute.direction
        if direction not in {StructureDirection.BULLISH, StructureDirection.BEARISH}:
            return self._transition(
                memory, StrategyAction.BLOCKED_CONFLUENCE, "15m_direction_neutral"
            )
        bullish = direction is StructureDirection.BULLISH
        sign = Decimal("1") if bullish else Decimal("-1")
        five = structure.five_minute
        one = structure.one_minute
        if five.direction is not direction:
            return self._transition(
                memory,
                StrategyAction.BLOCKED_CONFLUENCE,
                "5m_direction_not_aligned",
            )
        trigger = (
            one.bullish_sweep or one.bullish_choch or one.bullish_bos
            if bullish
            else one.bearish_sweep or one.bearish_choch or one.bearish_bos
        )
        if not trigger:
            return self._transition(
                memory,
                StrategyAction.BLOCKED_CONFLUENCE,
                "1m_structure_trigger_absent",
            )
        score = structure.long_confluence if bullish else structure.short_confluence
        reasons = structure.long_reasons if bullish else structure.short_reasons
        if score < self.config.minimum_confluence_score:
            return self._transition(
                memory,
                StrategyAction.BLOCKED_CONFLUENCE,
                "multi_timeframe_confluence_below_threshold",
                confluence=score,
            )
        if sign * features.book_imbalance < self.config.minimum_book_imbalance:
            return self._transition(
                memory,
                StrategyAction.BLOCKED_CONFLUENCE,
                "book_imbalance_not_aligned",
                confluence=score,
            )
        if sign * features.trade_flow_imbalance < self.config.minimum_trade_flow_imbalance:
            return self._transition(
                memory,
                StrategyAction.BLOCKED_CONFLUENCE,
                "trade_flow_not_aligned",
                confluence=score,
            )
        microprice_edge = (
            sign * (features.microprice - features.midprice) / features.midprice * Decimal("10000")
        )
        if microprice_edge < self.config.minimum_microprice_edge_bps:
            return self._transition(
                memory,
                StrategyAction.BLOCKED_CONFLUENCE,
                "microprice_not_aligned",
                confluence=score,
            )

        expected_edge = sign * forecast.latest_prediction_bps
        required_edge = (
            state.estimated_maker_fee_bps
            + state.estimated_taker_fee_bps
            + state.estimated_slippage_bps
            + self.config.safety_margin_bps
            + self.config.minimum_net_edge_bps
        )
        threshold = max(self.config.minimum_forecast_edge_bps, required_edge)
        if expected_edge < threshold:
            return self._transition(
                memory,
                StrategyAction.BLOCKED_COST,
                "forecast_does_not_clear_maker_taker_cost",
                expected_edge=expected_edge,
                required_edge=threshold,
                confluence=score,
            )

        side = OrderSide.BUY if bullish else OrderSide.SELL
        revision = memory.order_revision + 1
        intent_id = f"as2-{side.value}-{features.receive_ts_ns}-{revision}"
        limit_price = features.best_bid if side is OrderSide.BUY else features.best_ask
        intent = OrderIntent(
            intent_id=intent_id,
            strategy_id=self.config.strategy_id,
            side=side,
            kind=OrderKind.LIMIT,
            quantity_base=self.config.order_quantity_base,
            limit_price=limit_price,
            time_in_force=TimeInForce.GTC,
            post_only=True,
            reduce_only=False,
            created_ts_ns=features.receive_ts_ns,
            rationale=(
                f"adaptive causal SMC forecast={expected_edge}bps required={threshold}bps "
                f"accuracy={forecast.directional_accuracy} score={score} "
                f"reasons={','.join(reasons)}"
            ),
        )
        stop, target = self._exit_prices(features.midprice, side)
        next_memory = memory.model_copy(
            update={
                "last_order_ts_ns": features.receive_ts_ns,
                "last_entry_structure_revision": structure.revision,
                "pending_entry_intent_id": intent_id,
                "pending_entry_created_ts_ns": features.receive_ts_ns,
                "entry_attempt_day": day,
                "entry_attempts_today": attempts + 1,
                "order_revision": revision,
            }
        )
        return StrategyTransition(
            memory=next_memory,
            decision=KernelDecision(
                submit=(intent,),
                action=(StrategyAction.ENTER_LONG if bullish else StrategyAction.ENTER_SHORT),
                reason="adaptive_maker_smc_entry",
                expected_edge_bps=expected_edge,
                required_edge_bps=threshold,
                confluence_score=score,
                reference_price=features.midprice,
                stop_price=stop,
                target_price=target,
            ),
        )

    def _manage_pending_entry(
        self,
        state: StrategyInput,
        memory: AdaptiveScalperMemory,
    ) -> StrategyTransition[AdaptiveScalperMemory]:
        assert memory.pending_entry_intent_id is not None
        assert memory.pending_entry_created_ts_ns is not None
        age = state.features.receive_ts_ns - memory.pending_entry_created_ts_ns
        if age < self.config.maker_entry_ttl_ns:
            return self._transition(memory, StrategyAction.HOLD, "maker_entry_resting")
        intent_id = memory.pending_entry_intent_id
        next_memory = memory.model_copy(
            update={"pending_entry_intent_id": None, "pending_entry_created_ts_ns": None}
        )
        return StrategyTransition(
            memory=next_memory,
            decision=KernelDecision(
                cancel_intent_ids=(intent_id,),
                action=StrategyAction.BLOCKED_COOLDOWN,
                reason="maker_entry_ttl_expired",
                expected_edge_bps=abs(memory.forecast.latest_prediction_bps),
            ),
        )

    def _manage_position(
        self,
        state: StrategyInput,
        memory: AdaptiveScalperMemory,
    ) -> StrategyTransition[AdaptiveScalperMemory]:
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
        elif peak >= self.config.trailing_trigger_bps and pnl_bps <= max(
            self.config.break_even_offset_bps, peak - self.config.trailing_giveback_bps
        ):
            action = StrategyAction.EXIT_TAKE_PROFIT
            reason = "trailing_profit_protection"
        elif (
            direction * updated.forecast.latest_prediction_bps <= -self.config.reversal_forecast_bps
            and direction * features.trade_flow_imbalance
            <= -self.config.minimum_trade_flow_imbalance
        ):
            action = StrategyAction.EXIT_OPPOSITE_FLOW
            reason = "forecast_and_flow_reversal"
        elif self._opposite_confirmation(state, long_position=long_position):
            action = StrategyAction.EXIT_OPPOSITE_FLOW
            reason = "opposite_1m_structure_and_order_flow"
        elif age_ns >= self.config.hard_holding_limit_ns:
            action = StrategyAction.EXIT_TIME_LIMIT
            reason = "three_minute_hard_limit"
        elif (
            age_ns >= self.config.soft_holding_limit_ns
            and pnl_bps <= self.config.minimum_net_edge_bps
        ):
            action = StrategyAction.EXIT_TIME_LIMIT
            reason = "sixty_second_no_progress_exit"

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
            intent_id=f"as2-exit-{side.value}-{features.receive_ts_ns}-{revision}",
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

    def _update_forecast(
        self,
        features: MicrostructureSnapshot,
        state: AdaptiveForecastState,
    ) -> AdaptiveForecastState:
        if not features.ready:
            return state
        weights = list(state.weights)
        pending: list[PendingForecast] = []
        hits = list(state.recent_direction_hits)
        errors = list(state.recent_absolute_errors_bps)
        samples = state.training_samples
        for observation in state.pending:
            if (
                features.receive_ts_ns - observation.observed_ts_ns
                < self.config.forecast_horizon_ns
            ):
                pending.append(observation)
                continue
            label = (
                (features.midprice - observation.midprice) / observation.midprice * Decimal("10000")
            )
            error = observation.prediction_bps - label
            clipped = max(
                -self.config.forecast_huber_delta_bps,
                min(self.config.forecast_huber_delta_bps, error),
            )
            rate = self.config.forecast_learning_rate / (
                Decimal("1")
                + Decimal(samples) / Decimal(self.config.forecast_learning_rate_decay_samples)
            )
            for index, value in enumerate(observation.vector):
                gradient = clipped * value + self.config.forecast_l2_penalty * weights[index]
                weights[index] = self._clip(
                    weights[index] - rate * gradient,
                    -self.config.forecast_prediction_clip_bps,
                    self.config.forecast_prediction_clip_bps,
                )
            samples += 1
            hits.append(
                (observation.prediction_bps > 0 and label > 0)
                or (observation.prediction_bps < 0 and label < 0)
            )
            errors.append(abs(error))
        window = self.config.forecast_recent_window
        hits = hits[-window:]
        errors = errors[-window:]
        vector = self._forecast_vector(features)
        prediction = sum(
            (weight * value for weight, value in zip(weights, vector, strict=True)),
            Decimal("0"),
        )
        prediction = self._clip(
            prediction,
            -self.config.forecast_prediction_clip_bps,
            self.config.forecast_prediction_clip_bps,
        )
        if (
            not pending
            or features.receive_ts_ns - pending[-1].observed_ts_ns
            >= self.config.forecast_sample_interval_ns
        ):
            pending.append(
                PendingForecast(
                    observed_ts_ns=features.receive_ts_ns,
                    midprice=features.midprice,
                    vector=vector,
                    prediction_bps=prediction,
                )
            )
        if len(pending) > 4_096:
            raise ValueError("adaptive forecast could not resolve labels within its hard bound")
        return AdaptiveForecastState(
            weights=tuple(weights),
            pending=tuple(pending),
            training_samples=samples,
            recent_direction_hits=tuple(hits),
            recent_absolute_errors_bps=tuple(errors),
            latest_prediction_bps=prediction,
        )

    @classmethod
    def _forecast_vector(cls, features: MicrostructureSnapshot) -> tuple[Decimal, ...]:
        microprice_edge = (
            (features.microprice - features.midprice) / features.midprice * Decimal("10000")
        )
        vamp_edge = (features.vamp - features.midprice) / features.midprice * Decimal("10000")
        return (
            Decimal("1"),
            features.book_imbalance,
            features.queue_imbalance,
            features.depth_imbalance,
            features.trade_flow_imbalance,
            cls._clip(features.mid_return_bps / Decimal("5"), Decimal("-1"), Decimal("1")),
            cls._clip(microprice_edge / Decimal("0.25"), Decimal("-1"), Decimal("1")),
            cls._clip(vamp_edge / Decimal("2"), Decimal("-1"), Decimal("1")),
        )

    @staticmethod
    def _clip(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
        return max(lower, min(upper, value))

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
        memory: AdaptiveScalperMemory,
        action: StrategyAction,
        reason: str,
        *,
        expected_edge: Decimal = Decimal("0"),
        required_edge: Decimal = Decimal("0"),
        confluence: int = 0,
    ) -> StrategyTransition[AdaptiveScalperMemory]:
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
