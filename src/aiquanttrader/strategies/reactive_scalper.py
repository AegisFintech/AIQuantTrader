"""Event-driven, cost-aware BTC scalper with bounded multi-horizon learning."""

from __future__ import annotations

from decimal import Decimal
from statistics import median
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
MAX_FORECAST_HORIZONS = 4
MAX_PENDING_PER_HORIZON = 512


def _zeros() -> tuple[Decimal, ...]:
    return (Decimal("0"),) * FORECAST_FEATURE_COUNT


class ReactiveScalperConfig(DomainModel):
    """Immutable economics, signal, lifecycle, and online-learning policy."""

    schema_version: Literal[1] = 1
    strategy_id: Literal["smart-money-scalper-v3"] = "smart-money-scalper-v3"
    order_quantity_base: Annotated[Decimal, Field(gt=0)] = Decimal("0.001")
    high_volatility_quantity_multiplier: Annotated[Decimal, Field(gt=0, le=1)] = Decimal("0.50")
    maximum_spread_bps: Annotated[Decimal, Field(gt=0)] = Decimal("1")
    minimum_confluence_score: int = Field(default=6, ge=3, le=20)
    high_volatility_confluence_bonus: int = Field(default=1, ge=0, le=5)
    minimum_book_imbalance: Annotated[Decimal, Field(ge=0, le=1)] = Decimal("0.12")
    minimum_trade_flow_imbalance: Annotated[Decimal, Field(ge=0, le=1)] = Decimal("0.10")
    minimum_microprice_edge_bps: Annotated[Decimal, Field(ge=0)] = Decimal("0.01")
    minimum_intrabar_momentum_bps: Annotated[Decimal, Field(ge=0)] = Decimal("0.02")
    minimum_signal_persistence: int = Field(default=2, ge=1, le=10)
    confirmation_max_gap_ns: int = Field(
        default=3 * ONE_SECOND_NS,
        ge=ONE_SECOND_NS,
        le=10 * ONE_SECOND_NS,
    )
    minimum_forecast_edge_bps: Annotated[Decimal, Field(gt=0)] = Decimal("6.5")
    minimum_net_edge_bps: Annotated[Decimal, Field(gt=0)] = Decimal("1.5")
    safety_margin_bps: Annotated[Decimal, Field(gt=0)] = Decimal("1")
    expected_maker_exit_fraction: Annotated[Decimal, Field(ge=0, le=1)] = Decimal("0.65")
    high_volatility_edge_multiplier: Annotated[Decimal, Field(ge=1, le=3)] = Decimal("1.25")
    minimum_stop_loss_bps: Annotated[Decimal, Field(gt=0)] = Decimal("6")
    maximum_stop_loss_bps: Annotated[Decimal, Field(gt=0)] = Decimal("14")
    stop_atr_multiplier: Annotated[Decimal, Field(gt=0, le=5)] = Decimal("0.75")
    minimum_take_profit_bps: Annotated[Decimal, Field(gt=0)] = Decimal("12")
    maximum_take_profit_bps: Annotated[Decimal, Field(gt=0)] = Decimal("24")
    minimum_reward_risk_ratio: Annotated[Decimal, Field(gt=1, le=5)] = Decimal("1.5")
    break_even_trigger_fraction: Annotated[Decimal, Field(gt=0, lt=1)] = Decimal("0.65")
    break_even_lock_fraction: Annotated[Decimal, Field(ge=0, lt=1)] = Decimal("0.20")
    trailing_trigger_fraction: Annotated[Decimal, Field(gt=0, lt=1)] = Decimal("0.80")
    trailing_giveback_fraction: Annotated[Decimal, Field(gt=0, lt=1)] = Decimal("0.25")
    reversal_forecast_bps: Annotated[Decimal, Field(gt=0)] = Decimal("4")
    soft_holding_limit_ns: int = Field(default=45 * ONE_SECOND_NS, ge=10 * ONE_SECOND_NS)
    hard_holding_limit_ns: int = Field(default=120 * ONE_SECOND_NS, ge=30 * ONE_SECOND_NS)
    cooldown_ns: int = Field(default=15 * ONE_SECOND_NS, ge=0)
    minimum_entry_interval_ns: int = Field(default=10 * ONE_SECOND_NS, ge=0)
    maker_entry_ttl_ns: int = Field(
        default=15 * ONE_SECOND_NS,
        ge=250_000_000,
        le=15 * ONE_SECOND_NS,
    )
    maker_exit_ttl_ns: int = Field(
        default=2 * ONE_SECOND_NS,
        ge=250_000_000,
        le=10 * ONE_SECOND_NS,
    )
    maker_exit_cancel_grace_ns: int = Field(
        default=ONE_SECOND_NS,
        ge=250_000_000,
        le=5 * ONE_SECOND_NS,
    )
    exit_retry_ns: int = Field(
        default=2 * ONE_SECOND_NS,
        ge=250_000_000,
        le=10 * ONE_SECOND_NS,
    )
    maximum_entry_attempts_per_day: int = Field(default=96, ge=1, le=500)
    forecast_horizons_ns: tuple[int, ...] = (
        30 * ONE_SECOND_NS,
        60 * ONE_SECOND_NS,
        120 * ONE_SECOND_NS,
        180 * ONE_SECOND_NS,
    )
    forecast_sample_interval_ns: int = Field(
        default=5 * ONE_SECOND_NS,
        ge=100_000_000,
        le=10 * ONE_SECOND_NS,
    )
    forecast_minimum_samples: int = Field(default=1_000, ge=100, le=1_000_000)
    forecast_recent_window: int = Field(default=128, ge=50, le=2_048)
    forecast_minimum_directional_accuracy: Annotated[
        Decimal, Field(ge=Decimal("0.50"), le=Decimal("0.90"))
    ] = Decimal("0.55")
    forecast_maximum_mae_bps: Annotated[Decimal, Field(gt=0)] = Decimal("10")
    minimum_quality_horizons: int = Field(default=2, ge=1, le=MAX_FORECAST_HORIZONS)
    minimum_aligned_forecasts: int = Field(default=2, ge=1, le=MAX_FORECAST_HORIZONS)
    forecast_learning_rate: Annotated[Decimal, Field(gt=0, le=Decimal("0.20"))] = Decimal("0.012")
    forecast_learning_rate_decay_samples: int = Field(default=10_000, ge=100)
    forecast_l2_penalty: Annotated[Decimal, Field(ge=0, le=Decimal("0.10"))] = Decimal("0.0005")
    forecast_huber_delta_bps: Annotated[Decimal, Field(gt=0)] = Decimal("10")
    forecast_prediction_clip_bps: Annotated[Decimal, Field(gt=0)] = Decimal("40")

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        if self.soft_holding_limit_ns >= self.hard_holding_limit_ns:
            raise ValueError("soft holding limit must be below the hard holding limit")
        if self.hard_holding_limit_ns > 300 * ONE_SECOND_NS:
            raise ValueError("reactive scalper hard holding limit cannot exceed five minutes")
        if self.minimum_stop_loss_bps >= self.maximum_stop_loss_bps:
            raise ValueError("minimum stop loss must be below maximum stop loss")
        if self.minimum_take_profit_bps >= self.maximum_take_profit_bps:
            raise ValueError("minimum take profit must be below maximum take profit")
        if self.break_even_lock_fraction >= self.break_even_trigger_fraction:
            raise ValueError("break-even lock must be below its trigger")
        if self.trailing_giveback_fraction >= self.trailing_trigger_fraction:
            raise ValueError("trailing giveback must be below its trigger")
        if self.forecast_recent_window > self.forecast_minimum_samples:
            raise ValueError("forecast recent window cannot exceed minimum samples")
        horizons = self.forecast_horizons_ns
        if not horizons or len(horizons) > MAX_FORECAST_HORIZONS:
            raise ValueError("forecast horizon count is outside the supported bound")
        if tuple(sorted(set(horizons))) != horizons:
            raise ValueError("forecast horizons must be unique and strictly increasing")
        if horizons[0] < self.forecast_sample_interval_ns:
            raise ValueError("forecast sample interval cannot exceed its shortest horizon")
        if horizons[-1] > 300 * ONE_SECOND_NS:
            raise ValueError("forecast horizons cannot exceed five minutes")
        if horizons[-1] > MAX_PENDING_PER_HORIZON * self.forecast_sample_interval_ns:
            raise ValueError("forecast pending-label policy exceeds the checkpoint-safe bound")
        if self.minimum_quality_horizons > len(horizons):
            raise ValueError("minimum quality horizons exceed configured horizons")
        if self.minimum_aligned_forecasts > self.minimum_quality_horizons:
            raise ValueError("aligned forecast requirement exceeds quality horizon requirement")
        return self


class PendingForecast(DomainModel):
    observed_ts_ns: int = Field(ge=0)
    midprice: Annotated[Decimal, Field(gt=0)]
    vector: tuple[Decimal, ...]
    predictions_bps: tuple[Decimal, ...]
    resolved_horizons: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_vector(self) -> Self:
        if len(self.vector) != FORECAST_FEATURE_COUNT:
            raise ValueError("pending forecast vector has an unexpected width")
        if not self.predictions_bps or len(self.predictions_bps) > MAX_FORECAST_HORIZONS:
            raise ValueError("pending forecast predictions have an unexpected width")
        if self.resolved_horizons > len(self.predictions_bps):
            raise ValueError("pending forecast resolved count exceeds its predictions")
        return self


class HorizonForecastState(DomainModel):
    """Bounded online model and diagnostics for one fixed causal horizon."""

    horizon_ns: int = Field(gt=0)
    weights: tuple[Decimal, ...] = Field(default_factory=_zeros)
    training_samples: int = Field(default=0, ge=0)
    recent_direction_hits: tuple[bool, ...] = ()
    recent_absolute_errors_bps: tuple[Annotated[Decimal, Field(ge=0)], ...] = ()
    latest_prediction_bps: Decimal = Decimal("0")

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if len(self.weights) != FORECAST_FEATURE_COUNT:
            raise ValueError("reactive forecast weight vector has an unexpected width")
        if len(self.recent_direction_hits) > 2_048:
            raise ValueError("reactive forecast hit window exceeds its hard bound")
        if len(self.recent_absolute_errors_bps) > 2_048:
            raise ValueError("reactive forecast error window exceeds its hard bound")
        if len(self.recent_direction_hits) != len(self.recent_absolute_errors_bps):
            raise ValueError("reactive forecast diagnostic windows must remain aligned")
        return self

    def ready(self, config: ReactiveScalperConfig) -> bool:
        return (
            self.training_samples >= config.forecast_minimum_samples
            and len(self.recent_direction_hits) >= config.forecast_recent_window
        )

    def quality_ready(self, config: ReactiveScalperConfig) -> bool:
        return (
            self.ready(config)
            and self.directional_accuracy >= config.forecast_minimum_directional_accuracy
            and self.mean_absolute_error_bps <= config.forecast_maximum_mae_bps
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


class ReactiveForecastState(DomainModel):
    """Checkpoint-safe fixed-horizon ensemble without self-selected horizons."""

    horizons: tuple[HorizonForecastState, ...] = ()
    pending: tuple[PendingForecast, ...] = ()
    last_sample_ts_ns: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_horizons(self) -> Self:
        values = tuple(item.horizon_ns for item in self.horizons)
        if len(values) > MAX_FORECAST_HORIZONS or tuple(sorted(set(values))) != values:
            raise ValueError("reactive forecast horizons must be bounded, unique, and ordered")
        if len(self.pending) > MAX_PENDING_PER_HORIZON:
            raise ValueError("reactive forecast pending-label buffer exceeds its hard bound")
        if any(len(item.predictions_bps) != len(values) for item in self.pending):
            raise ValueError("pending forecast width does not match the horizon ensemble")
        if self.pending and self.last_sample_ts_ns != self.pending[-1].observed_ts_ns:
            raise ValueError("forecast sample timestamp does not match the pending tail")
        return self

    def ready(self, config: ReactiveScalperConfig) -> bool:
        return len(self.quality_horizons(config)) >= config.minimum_quality_horizons

    def quality_horizons(self, config: ReactiveScalperConfig) -> tuple[HorizonForecastState, ...]:
        return tuple(item for item in self.horizons if item.quality_ready(config))

    @property
    def training_samples(self) -> int:
        return min((item.training_samples for item in self.horizons), default=0)

    @property
    def directional_accuracy(self) -> Decimal:
        ready = [item.directional_accuracy for item in self.horizons if item.recent_direction_hits]
        return min(ready, default=Decimal("0"))

    @property
    def mean_absolute_error_bps(self) -> Decimal:
        ready = [
            item.mean_absolute_error_bps
            for item in self.horizons
            if item.recent_absolute_errors_bps
        ]
        return max(ready, default=Decimal("0"))

    @property
    def latest_prediction_bps(self) -> Decimal:
        predictions = [item.latest_prediction_bps for item in self.horizons]
        return Decimal("0") if not predictions else Decimal(str(median(predictions)))


class ReactiveScalperMemory(DomainModel):
    inventory_base: Decimal = Decimal("0")
    average_entry_price: Decimal | None = Field(default=None, gt=0)
    position_opened_ts_ns: int | None = Field(default=None, ge=0)
    last_flat_ts_ns: int | None = Field(default=None, ge=0)
    last_order_ts_ns: int | None = Field(default=None, ge=0)
    last_entry_attempt_ts_ns: int | None = Field(default=None, ge=0)
    pending_entry_intent_id: str | None = None
    pending_entry_created_ts_ns: int | None = Field(default=None, ge=0)
    pending_entry_side: OrderSide | None = None
    pending_exit_intent_id: str | None = None
    pending_exit_created_ts_ns: int | None = Field(default=None, ge=0)
    pending_exit_cancel_ts_ns: int | None = Field(default=None, ge=0)
    pending_exit_action: StrategyAction | None = None
    pending_exit_reason: str | None = None
    confirmation_side: OrderSide | None = None
    confirmation_count: int = Field(default=0, ge=0, le=10)
    last_confirmation_ts_ns: int | None = Field(default=None, ge=0)
    planned_stop_loss_bps: Decimal | None = Field(default=None, gt=0)
    planned_take_profit_bps: Decimal | None = Field(default=None, gt=0)
    active_stop_loss_bps: Decimal | None = Field(default=None, gt=0)
    active_take_profit_bps: Decimal | None = Field(default=None, gt=0)
    peak_favorable_bps: Annotated[Decimal, Field(ge=0)] = Decimal("0")
    entry_attempt_day: int | None = Field(default=None, ge=0)
    entry_attempts_today: int = Field(default=0, ge=0)
    order_revision: int = Field(default=0, ge=0)
    forecast: ReactiveForecastState = Field(default_factory=ReactiveForecastState)

    @model_validator(mode="after")
    def validate_coupled_state(self) -> Self:
        entry_fields = (self.pending_entry_created_ts_ns, self.pending_entry_side)
        if self.pending_entry_intent_id is None and any(item is not None for item in entry_fields):
            raise ValueError("pending entry metadata requires an intent identity")
        if self.pending_entry_intent_id is not None and any(item is None for item in entry_fields):
            raise ValueError("pending entry identity requires complete metadata")
        exit_fields = (
            self.pending_exit_created_ts_ns,
            self.pending_exit_action,
            self.pending_exit_reason,
        )
        if self.pending_exit_intent_id is None and any(item is not None for item in exit_fields):
            raise ValueError("pending exit metadata requires an intent identity")
        if self.pending_exit_intent_id is not None and any(item is None for item in exit_fields):
            raise ValueError("pending exit identity requires complete metadata")
        if self.pending_exit_cancel_ts_ns is not None and self.pending_exit_intent_id is None:
            raise ValueError("exit cancel timestamp requires a pending exit")
        if self.confirmation_side is None and (
            self.confirmation_count != 0 or self.last_confirmation_ts_ns is not None
        ):
            raise ValueError("confirmation counters require a side")
        if self.confirmation_side is not None and (
            self.confirmation_count == 0 or self.last_confirmation_ts_ns is None
        ):
            raise ValueError("confirmation side requires counters and timestamp")
        return self

    def synchronize_position(
        self,
        inventory_base: Decimal,
        average_entry_price: Decimal | None,
        observed_ts_ns: int,
    ) -> ReactiveScalperMemory:
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
                    "pending_exit_intent_id": None,
                    "pending_exit_created_ts_ns": None,
                    "pending_exit_cancel_ts_ns": None,
                    "pending_exit_action": None,
                    "pending_exit_reason": None,
                    "active_stop_loss_bps": None,
                    "active_take_profit_bps": None,
                    "peak_favorable_bps": Decimal("0"),
                }
            )
        if average_entry_price is None or average_entry_price <= 0:
            raise ValueError("non-flat reactive inventory requires an average entry price")
        newly_opened = self.inventory_base == 0 or self.position_opened_ts_ns is None
        return self.model_copy(
            update={
                "inventory_base": inventory_base,
                "average_entry_price": average_entry_price,
                "position_opened_ts_ns": (
                    observed_ts_ns if newly_opened else self.position_opened_ts_ns
                ),
                "pending_entry_intent_id": None,
                "pending_entry_created_ts_ns": None,
                "pending_entry_side": None,
                "active_stop_loss_bps": (
                    self.planned_stop_loss_bps if newly_opened else self.active_stop_loss_bps
                ),
                "active_take_profit_bps": (
                    self.planned_take_profit_bps if newly_opened else self.active_take_profit_bps
                ),
            }
        )


class ReactiveScalperKernel:
    """Pure causal challenger; online changes are explicit strategy memory only."""

    def __init__(self, config: ReactiveScalperConfig) -> None:
        self.config = config

    def decide(
        self, state: StrategyInput, memory: ReactiveScalperMemory
    ) -> StrategyTransition[ReactiveScalperMemory]:
        forecast = self._update_forecast(state.features, memory.forecast)
        memory = memory.model_copy(update={"forecast": forecast})
        if memory.inventory_base != 0:
            return self._manage_position(state, memory)
        if memory.pending_entry_intent_id is not None:
            return self._manage_pending_entry(state, memory)
        return self._evaluate_entry(state, memory)

    def _evaluate_entry(
        self,
        state: StrategyInput,
        memory: ReactiveScalperMemory,
    ) -> StrategyTransition[ReactiveScalperMemory]:
        features = state.features
        structure = state.market_structure
        if not features.ready or structure is None or not structure.ready:
            return self._blocked(memory, StrategyAction.WARMUP, "causal_timeframes_warming")
        ready_horizons = tuple(item for item in memory.forecast.horizons if item.ready(self.config))
        if len(ready_horizons) < self.config.minimum_quality_horizons:
            return self._blocked(
                memory,
                StrategyAction.WARMUP,
                "causal_multi_horizon_forecast_warming",
                expected_edge=abs(memory.forecast.latest_prediction_bps),
            )
        quality_horizons = memory.forecast.quality_horizons(self.config)
        if len(quality_horizons) < self.config.minimum_quality_horizons:
            reason = (
                "forecast_directional_accuracy_below_gate"
                if sum(
                    item.directional_accuracy >= self.config.forecast_minimum_directional_accuracy
                    for item in ready_horizons
                )
                < self.config.minimum_quality_horizons
                else "forecast_error_above_gate"
            )
            return self._blocked(
                memory,
                StrategyAction.BLOCKED_MODEL,
                reason,
                expected_edge=abs(memory.forecast.latest_prediction_bps),
            )
        if features.spread_bps > self.config.maximum_spread_bps:
            return self._blocked(
                memory, StrategyAction.BLOCKED_SPREAD, "spread_above_maker_entry_limit"
            )
        if (
            memory.last_flat_ts_ns is not None
            and features.receive_ts_ns - memory.last_flat_ts_ns < self.config.cooldown_ns
        ):
            return self._blocked(memory, StrategyAction.BLOCKED_COOLDOWN, "post_exit_cooldown")
        if (
            memory.last_entry_attempt_ts_ns is not None
            and features.receive_ts_ns - memory.last_entry_attempt_ts_ns
            < self.config.minimum_entry_interval_ns
        ):
            return self._blocked(
                memory, StrategyAction.BLOCKED_COOLDOWN, "entry_attempt_interval_active"
            )

        day = features.receive_ts_ns // ONE_DAY_NS
        attempts = memory.entry_attempts_today if memory.entry_attempt_day == day else 0
        if attempts >= self.config.maximum_entry_attempts_per_day:
            return self._blocked(
                memory.model_copy(
                    update={"entry_attempt_day": day, "entry_attempts_today": attempts}
                ),
                StrategyAction.BLOCKED_INVENTORY,
                "daily_entry_attempt_cap_reached",
            )

        direction = structure.fifteen_minute.direction
        if direction not in {StructureDirection.BULLISH, StructureDirection.BEARISH}:
            return self._blocked(memory, StrategyAction.BLOCKED_CONFLUENCE, "15m_direction_neutral")
        if structure.five_minute.direction is not direction:
            return self._blocked(
                memory, StrategyAction.BLOCKED_CONFLUENCE, "5m_direction_not_aligned"
            )
        bullish = direction is StructureDirection.BULLISH
        side = OrderSide.BUY if bullish else OrderSide.SELL
        sign = Decimal("1") if bullish else Decimal("-1")
        score = structure.long_confluence if bullish else structure.short_confluence
        reasons = structure.long_reasons if bullish else structure.short_reasons
        score_required = self.config.minimum_confluence_score + (
            self.config.high_volatility_confluence_bonus
            if features.volatility_regime is VolatilityRegime.HIGH
            else 0
        )
        if score < score_required:
            return self._blocked(
                memory,
                StrategyAction.BLOCKED_CONFLUENCE,
                "multi_timeframe_confluence_below_threshold",
                confluence=score,
            )

        one = structure.one_minute
        closed_trigger = (
            one.bullish_sweep or one.bullish_choch or one.bullish_bos
            if bullish
            else one.bearish_sweep or one.bearish_choch or one.bearish_bos
        )
        momentum_trigger = (
            one.direction is direction
            and sign * features.mid_return_bps >= self.config.minimum_intrabar_momentum_bps
        )
        if not closed_trigger and not momentum_trigger:
            return self._blocked(
                memory,
                StrategyAction.BLOCKED_CONFLUENCE,
                "intrabar_structure_trigger_absent",
                confluence=score,
            )
        if sign * features.book_imbalance < self.config.minimum_book_imbalance:
            return self._blocked(
                memory,
                StrategyAction.BLOCKED_CONFLUENCE,
                "book_imbalance_not_aligned",
                confluence=score,
            )
        if sign * features.trade_flow_imbalance < self.config.minimum_trade_flow_imbalance:
            return self._blocked(
                memory,
                StrategyAction.BLOCKED_CONFLUENCE,
                "trade_flow_not_aligned",
                confluence=score,
            )
        microprice_edge = (
            sign * (features.microprice - features.midprice) / features.midprice * Decimal("10000")
        )
        if microprice_edge < self.config.minimum_microprice_edge_bps:
            return self._blocked(
                memory,
                StrategyAction.BLOCKED_CONFLUENCE,
                "microprice_not_aligned",
                confluence=score,
            )

        aligned = tuple(item for item in quality_horizons if sign * item.latest_prediction_bps > 0)
        if len(aligned) < self.config.minimum_aligned_forecasts:
            return self._blocked(
                memory,
                StrategyAction.BLOCKED_MODEL,
                "multi_horizon_forecasts_not_aligned",
                expected_edge=max(
                    (sign * item.latest_prediction_bps for item in quality_horizons),
                    default=Decimal("0"),
                ),
                confluence=score,
            )
        selected = max(aligned, key=lambda item: sign * item.latest_prediction_bps)
        expected_edge = sign * selected.latest_prediction_bps
        required_edge = self._required_edge(state, features.volatility_regime)
        threshold = max(self.config.minimum_forecast_edge_bps, required_edge)
        if expected_edge < threshold:
            return self._blocked(
                memory,
                StrategyAction.BLOCKED_COST,
                "forecast_does_not_clear_dynamic_cost",
                expected_edge=expected_edge,
                required_edge=threshold,
                confluence=score,
            )

        confirmed = self._confirm(memory, side, features.receive_ts_ns)
        if confirmed.confirmation_count < self.config.minimum_signal_persistence:
            return self._transition(
                confirmed,
                StrategyAction.BLOCKED_CONFLUENCE,
                "intrabar_signal_persistence_warming",
                expected_edge=expected_edge,
                required_edge=threshold,
                confluence=score,
            )

        stop_bps, target_bps = self._planned_exit_distances(features)
        quantity = self.config.order_quantity_base * (
            self.config.high_volatility_quantity_multiplier
            if features.volatility_regime is VolatilityRegime.HIGH
            else Decimal("1")
        )
        revision = confirmed.order_revision + 1
        intent_id = f"rsc3-entry-{side.value}-{features.receive_ts_ns}-{revision}"
        intent = OrderIntent(
            intent_id=intent_id,
            strategy_id=self.config.strategy_id,
            side=side,
            kind=OrderKind.LIMIT,
            quantity_base=quantity,
            limit_price=features.best_bid if side is OrderSide.BUY else features.best_ask,
            time_in_force=TimeInForce.GTC,
            post_only=True,
            reduce_only=False,
            created_ts_ns=features.receive_ts_ns,
            rationale=(
                f"reactive SMC horizon={selected.horizon_ns // ONE_SECOND_NS}s "
                f"forecast={expected_edge}bps required={threshold}bps "
                f"accuracy={selected.directional_accuracy} score={score} "
                f"persistence={confirmed.confirmation_count} reasons={','.join(reasons)}"
            ),
        )
        stop, target = self._exit_prices(features.midprice, side, stop_bps, target_bps)
        next_memory = self._reset_confirmation(confirmed).model_copy(
            update={
                "last_order_ts_ns": features.receive_ts_ns,
                "last_entry_attempt_ts_ns": features.receive_ts_ns,
                "pending_entry_intent_id": intent_id,
                "pending_entry_created_ts_ns": features.receive_ts_ns,
                "pending_entry_side": side,
                "planned_stop_loss_bps": stop_bps,
                "planned_take_profit_bps": target_bps,
                "entry_attempt_day": day,
                "entry_attempts_today": attempts + 1,
                "order_revision": revision,
            }
        )
        return StrategyTransition(
            memory=next_memory,
            decision=KernelDecision(
                submit=(intent,),
                action=StrategyAction.ENTER_LONG if bullish else StrategyAction.ENTER_SHORT,
                reason="reactive_maker_entry",
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
        memory: ReactiveScalperMemory,
    ) -> StrategyTransition[ReactiveScalperMemory]:
        assert memory.pending_entry_intent_id is not None
        assert memory.pending_entry_created_ts_ns is not None
        assert memory.pending_entry_side is not None
        direction = Decimal("1") if memory.pending_entry_side is OrderSide.BUY else Decimal("-1")
        invalidated = (
            direction * state.features.book_imbalance < -self.config.minimum_book_imbalance
            or direction * state.features.trade_flow_imbalance
            < -self.config.minimum_trade_flow_imbalance
        )
        age = state.features.receive_ts_ns - memory.pending_entry_created_ts_ns
        if age < self.config.maker_entry_ttl_ns and not invalidated:
            return self._transition(memory, StrategyAction.HOLD, "maker_entry_resting")
        intent_id = memory.pending_entry_intent_id
        next_memory = memory.model_copy(
            update={
                "pending_entry_intent_id": None,
                "pending_entry_created_ts_ns": None,
                "pending_entry_side": None,
                "planned_stop_loss_bps": None,
                "planned_take_profit_bps": None,
            }
        )
        return StrategyTransition(
            memory=next_memory,
            decision=KernelDecision(
                cancel_intent_ids=(intent_id,),
                action=StrategyAction.BLOCKED_COOLDOWN,
                reason=(
                    "maker_entry_signal_invalidated" if invalidated else "maker_entry_ttl_expired"
                ),
                expected_edge_bps=abs(memory.forecast.latest_prediction_bps),
            ),
        )

    def _manage_position(
        self,
        state: StrategyInput,
        memory: ReactiveScalperMemory,
    ) -> StrategyTransition[ReactiveScalperMemory]:
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
        stop_loss = memory.active_stop_loss_bps or self.config.minimum_stop_loss_bps
        take_profit = memory.active_take_profit_bps or self.config.minimum_take_profit_bps
        updated = memory.model_copy(update={"peak_favorable_bps": peak})
        stop_threshold = -stop_loss
        if peak >= take_profit * self.config.break_even_trigger_fraction:
            stop_threshold = take_profit * self.config.break_even_lock_fraction

        urgent_action: StrategyAction | None = None
        urgent_reason: str | None = None
        if pnl_bps <= stop_threshold:
            urgent_action = StrategyAction.EXIT_STOP_LOSS
            urgent_reason = (
                "break_even_stop_reached" if stop_threshold >= 0 else "hard_stop_reached"
            )
        elif peak >= take_profit * self.config.trailing_trigger_fraction and pnl_bps <= max(
            take_profit * self.config.break_even_lock_fraction,
            peak - take_profit * self.config.trailing_giveback_fraction,
        ):
            urgent_action = StrategyAction.EXIT_TAKE_PROFIT
            urgent_reason = "trailing_profit_protection"
        elif self._forecast_and_flow_reversed(updated.forecast, features, long_position):
            urgent_action = StrategyAction.EXIT_OPPOSITE_FLOW
            urgent_reason = "forecast_and_flow_reversal"
        elif self._opposite_confirmation(state, long_position=long_position):
            urgent_action = StrategyAction.EXIT_OPPOSITE_FLOW
            urgent_reason = "opposite_1m_structure_and_order_flow"
        elif age_ns >= self.config.hard_holding_limit_ns:
            urgent_action = StrategyAction.EXIT_TIME_LIMIT
            urgent_reason = "two_minute_hard_limit"
        elif (
            age_ns >= self.config.soft_holding_limit_ns
            and pnl_bps <= self.config.minimum_net_edge_bps
        ):
            urgent_action = StrategyAction.EXIT_TIME_LIMIT
            urgent_reason = "forty_five_second_no_progress_exit"

        if updated.pending_exit_intent_id is not None:
            return self._manage_pending_exit(
                state,
                updated,
                pnl_bps=pnl_bps,
                urgent_action=urgent_action,
                urgent_reason=urgent_reason,
                stop_threshold=stop_threshold,
                take_profit=take_profit,
            )

        if urgent_action is not None and urgent_reason is not None:
            return self._market_exit(
                state,
                updated,
                action=urgent_action,
                reason=urgent_reason,
                pnl_bps=pnl_bps,
                stop_threshold=stop_threshold,
                take_profit=take_profit,
            )
        if pnl_bps >= take_profit:
            return self._maker_take_profit(
                state,
                updated,
                pnl_bps=pnl_bps,
                stop_threshold=stop_threshold,
                take_profit=take_profit,
            )
        stop, target = self._exit_prices(
            entry,
            OrderSide.BUY if long_position else OrderSide.SELL,
            stop_loss,
            take_profit,
            stop_threshold=stop_threshold,
        )
        return StrategyTransition(
            memory=updated,
            decision=KernelDecision(
                action=StrategyAction.HOLD,
                reason="position_within_exit_bounds",
                expected_edge_bps=pnl_bps,
                confluence_score=self._active_confluence(state, long_position),
                reference_price=features.midprice,
                stop_price=stop,
                target_price=target,
                position_age_seconds=Decimal(age_ns) / Decimal(ONE_SECOND_NS),
            ),
        )

    def _maker_take_profit(
        self,
        state: StrategyInput,
        memory: ReactiveScalperMemory,
        *,
        pnl_bps: Decimal,
        stop_threshold: Decimal,
        take_profit: Decimal,
    ) -> StrategyTransition[ReactiveScalperMemory]:
        features = state.features
        long_position = memory.inventory_base > 0
        side = OrderSide.SELL if long_position else OrderSide.BUY
        revision = memory.order_revision + 1
        intent = OrderIntent(
            intent_id=f"rsc3-maker-exit-{side.value}-{features.receive_ts_ns}-{revision}",
            strategy_id=self.config.strategy_id,
            side=side,
            kind=OrderKind.LIMIT,
            quantity_base=abs(memory.inventory_base),
            limit_price=features.best_ask if long_position else features.best_bid,
            time_in_force=TimeInForce.GTC,
            post_only=True,
            reduce_only=True,
            created_ts_ns=features.receive_ts_ns,
            rationale=f"maker take profit; gross_move={pnl_bps}bps",
        )
        stop, target = self._position_exit_prices(memory, stop_threshold, take_profit)
        next_memory = memory.model_copy(
            update={
                "last_order_ts_ns": features.receive_ts_ns,
                "pending_exit_intent_id": intent.intent_id,
                "pending_exit_created_ts_ns": features.receive_ts_ns,
                "pending_exit_cancel_ts_ns": None,
                "pending_exit_action": StrategyAction.EXIT_TAKE_PROFIT,
                "pending_exit_reason": "take_profit_reached",
                "order_revision": revision,
            }
        )
        return StrategyTransition(
            memory=next_memory,
            decision=KernelDecision(
                submit=(intent,),
                action=StrategyAction.EXIT_TAKE_PROFIT,
                reason="maker_take_profit_submitted",
                expected_edge_bps=pnl_bps,
                confluence_score=self._active_confluence(state, long_position),
                reference_price=features.midprice,
                stop_price=stop,
                target_price=target,
                position_age_seconds=self._position_age_seconds(memory, features.receive_ts_ns),
            ),
        )

    def _manage_pending_exit(
        self,
        state: StrategyInput,
        memory: ReactiveScalperMemory,
        *,
        pnl_bps: Decimal,
        urgent_action: StrategyAction | None,
        urgent_reason: str | None,
        stop_threshold: Decimal,
        take_profit: Decimal,
    ) -> StrategyTransition[ReactiveScalperMemory]:
        assert memory.pending_exit_intent_id is not None
        assert memory.pending_exit_created_ts_ns is not None
        assert memory.pending_exit_action is not None
        assert memory.pending_exit_reason is not None
        now = state.features.receive_ts_ns
        age = now - memory.pending_exit_created_ts_ns
        should_cancel = urgent_action is not None or age >= self.config.maker_exit_ttl_ns
        if not should_cancel:
            return self._transition(
                memory,
                StrategyAction.HOLD,
                "maker_take_profit_resting",
                expected_edge=pnl_bps,
            )
        fallback_action = urgent_action or memory.pending_exit_action
        fallback_reason = urgent_reason or "maker_take_profit_ttl_expired"
        if memory.pending_exit_cancel_ts_ns is None:
            updated = memory.model_copy(
                update={
                    "pending_exit_cancel_ts_ns": now,
                    "pending_exit_action": fallback_action,
                    "pending_exit_reason": fallback_reason,
                }
            )
            return StrategyTransition(
                memory=updated,
                decision=KernelDecision(
                    cancel_intent_ids=(memory.pending_exit_intent_id,),
                    action=StrategyAction.HOLD,
                    reason="maker_exit_cancel_requested",
                    expected_edge_bps=pnl_bps,
                ),
            )
        if now - memory.pending_exit_cancel_ts_ns < self.config.maker_exit_cancel_grace_ns:
            return self._transition(
                memory,
                StrategyAction.HOLD,
                "maker_exit_cancel_grace",
                expected_edge=pnl_bps,
            )
        cleared = memory.model_copy(
            update={
                "pending_exit_intent_id": None,
                "pending_exit_created_ts_ns": None,
                "pending_exit_cancel_ts_ns": None,
                "pending_exit_action": None,
                "pending_exit_reason": None,
            }
        )
        return self._market_exit(
            state,
            cleared,
            action=fallback_action,
            reason=fallback_reason,
            pnl_bps=pnl_bps,
            stop_threshold=stop_threshold,
            take_profit=take_profit,
        )

    def _market_exit(
        self,
        state: StrategyInput,
        memory: ReactiveScalperMemory,
        *,
        action: StrategyAction,
        reason: str,
        pnl_bps: Decimal,
        stop_threshold: Decimal,
        take_profit: Decimal,
    ) -> StrategyTransition[ReactiveScalperMemory]:
        features = state.features
        if (
            memory.last_order_ts_ns is not None
            and features.receive_ts_ns - memory.last_order_ts_ns < self.config.exit_retry_ns
        ):
            return self._transition(
                memory,
                StrategyAction.HOLD,
                "exit_order_pending_activation",
                expected_edge=pnl_bps,
            )
        long_position = memory.inventory_base > 0
        side = OrderSide.SELL if long_position else OrderSide.BUY
        revision = memory.order_revision + 1
        intent = OrderIntent(
            intent_id=f"rsc3-market-exit-{side.value}-{features.receive_ts_ns}-{revision}",
            strategy_id=self.config.strategy_id,
            side=side,
            kind=OrderKind.MARKET,
            quantity_base=abs(memory.inventory_base),
            time_in_force=TimeInForce.IOC,
            post_only=False,
            reduce_only=True,
            created_ts_ns=features.receive_ts_ns,
            rationale=(
                f"{reason}; gross_move={pnl_bps}bps "
                f"age={self._position_age_seconds(memory, features.receive_ts_ns)}s"
            ),
        )
        stop, target = self._position_exit_prices(memory, stop_threshold, take_profit)
        next_memory = memory.model_copy(
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
                position_age_seconds=self._position_age_seconds(memory, features.receive_ts_ns),
            ),
        )

    def _update_forecast(
        self,
        features: MicrostructureSnapshot,
        state: ReactiveForecastState,
    ) -> ReactiveForecastState:
        if not features.ready:
            return state
        configured = self.config.forecast_horizons_ns
        restored = tuple(item.horizon_ns for item in state.horizons)
        if restored and restored != configured:
            raise ValueError("reactive forecast checkpoint horizons do not match configuration")
        states = list(state.horizons) or [
            HorizonForecastState(horizon_ns=horizon) for horizon in configured
        ]
        pending: list[PendingForecast] = []
        for observation in state.pending:
            resolved = observation.resolved_horizons
            age = features.receive_ts_ns - observation.observed_ts_ns
            while resolved < len(states) and age >= states[resolved].horizon_ns:
                states[resolved] = self._learn_horizon(
                    states[resolved],
                    observation,
                    prediction_bps=observation.predictions_bps[resolved],
                    label_bps=(
                        (features.midprice - observation.midprice)
                        / observation.midprice
                        * Decimal("10000")
                    ),
                )
                resolved += 1
            if resolved < len(states):
                pending.append(observation.model_copy(update={"resolved_horizons": resolved}))
        vector = self._forecast_vector(features)
        predictions = tuple(
            self._clip(
                sum(
                    (weight * value for weight, value in zip(item.weights, vector, strict=True)),
                    Decimal("0"),
                ),
                -self.config.forecast_prediction_clip_bps,
                self.config.forecast_prediction_clip_bps,
            )
            for item in states
        )
        states = [
            item.model_copy(update={"latest_prediction_bps": prediction})
            for item, prediction in zip(states, predictions, strict=True)
        ]
        should_sample = (
            state.last_sample_ts_ns is None
            or features.receive_ts_ns - state.last_sample_ts_ns
            >= self.config.forecast_sample_interval_ns
        )
        last_sample = state.last_sample_ts_ns
        if should_sample:
            pending.append(
                PendingForecast(
                    observed_ts_ns=features.receive_ts_ns,
                    midprice=features.midprice,
                    vector=vector,
                    predictions_bps=predictions,
                )
            )
            last_sample = features.receive_ts_ns
        if len(pending) > MAX_PENDING_PER_HORIZON:
            raise ValueError("reactive forecast could not resolve labels within its hard bound")
        return ReactiveForecastState(
            horizons=tuple(states),
            pending=tuple(pending),
            last_sample_ts_ns=last_sample,
        )

    def _learn_horizon(
        self,
        state: HorizonForecastState,
        observation: PendingForecast,
        *,
        prediction_bps: Decimal,
        label_bps: Decimal,
    ) -> HorizonForecastState:
        weights = list(state.weights)
        error = prediction_bps - label_bps
        clipped = self._clip(
            error,
            -self.config.forecast_huber_delta_bps,
            self.config.forecast_huber_delta_bps,
        )
        rate = self.config.forecast_learning_rate / (
            Decimal("1")
            + Decimal(state.training_samples)
            / Decimal(self.config.forecast_learning_rate_decay_samples)
        )
        for index, value in enumerate(observation.vector):
            gradient = clipped * value + self.config.forecast_l2_penalty * weights[index]
            weights[index] = self._clip(
                weights[index] - rate * gradient,
                -self.config.forecast_prediction_clip_bps,
                self.config.forecast_prediction_clip_bps,
            )
        window = self.config.forecast_recent_window
        hits = (
            *state.recent_direction_hits,
            (prediction_bps > 0 and label_bps > 0) or (prediction_bps < 0 and label_bps < 0),
        )[-window:]
        errors = (*state.recent_absolute_errors_bps, abs(error))[-window:]
        return state.model_copy(
            update={
                "weights": tuple(weights),
                "training_samples": state.training_samples + 1,
                "recent_direction_hits": hits,
                "recent_absolute_errors_bps": errors,
            }
        )

    def _required_edge(self, state: StrategyInput, regime: VolatilityRegime) -> Decimal:
        exit_fee = (
            self.config.expected_maker_exit_fraction * state.estimated_maker_fee_bps
            + (Decimal("1") - self.config.expected_maker_exit_fraction)
            * state.estimated_taker_fee_bps
        )
        exit_slippage = (
            Decimal("1") - self.config.expected_maker_exit_fraction
        ) * state.estimated_slippage_bps
        required = (
            state.estimated_maker_fee_bps
            + exit_fee
            + exit_slippage
            + self.config.safety_margin_bps
            + self.config.minimum_net_edge_bps
        )
        if regime is VolatilityRegime.HIGH:
            required *= self.config.high_volatility_edge_multiplier
        return required

    def _planned_exit_distances(self, features: MicrostructureSnapshot) -> tuple[Decimal, Decimal]:
        stop = self._clip(
            features.atr_bps * self.config.stop_atr_multiplier,
            self.config.minimum_stop_loss_bps,
            self.config.maximum_stop_loss_bps,
        )
        target = self._clip(
            max(self.config.minimum_take_profit_bps, stop * self.config.minimum_reward_risk_ratio),
            self.config.minimum_take_profit_bps,
            self.config.maximum_take_profit_bps,
        )
        return stop, target

    def _confirm(
        self, memory: ReactiveScalperMemory, side: OrderSide, observed_ts_ns: int
    ) -> ReactiveScalperMemory:
        continuous = (
            memory.confirmation_side is side
            and memory.last_confirmation_ts_ns is not None
            and observed_ts_ns - memory.last_confirmation_ts_ns
            <= self.config.confirmation_max_gap_ns
        )
        count = memory.confirmation_count + 1 if continuous else 1
        return memory.model_copy(
            update={
                "confirmation_side": side,
                "confirmation_count": min(count, self.config.minimum_signal_persistence),
                "last_confirmation_ts_ns": observed_ts_ns,
            }
        )

    @staticmethod
    def _reset_confirmation(memory: ReactiveScalperMemory) -> ReactiveScalperMemory:
        return memory.model_copy(
            update={
                "confirmation_side": None,
                "confirmation_count": 0,
                "last_confirmation_ts_ns": None,
            }
        )

    def _blocked(
        self,
        memory: ReactiveScalperMemory,
        action: StrategyAction,
        reason: str,
        *,
        expected_edge: Decimal = Decimal("0"),
        required_edge: Decimal = Decimal("0"),
        confluence: int = 0,
    ) -> StrategyTransition[ReactiveScalperMemory]:
        return self._transition(
            self._reset_confirmation(memory),
            action,
            reason,
            expected_edge=expected_edge,
            required_edge=required_edge,
            confluence=confluence,
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

    def _forecast_and_flow_reversed(
        self,
        forecast: ReactiveForecastState,
        features: MicrostructureSnapshot,
        long_position: bool,
    ) -> bool:
        direction = Decimal("1") if long_position else Decimal("-1")
        quality = forecast.quality_horizons(self.config)
        reversed_forecasts = sum(
            direction * item.latest_prediction_bps <= -self.config.reversal_forecast_bps
            for item in quality
        )
        return (
            reversed_forecasts >= self.config.minimum_aligned_forecasts
            and direction * features.trade_flow_imbalance
            <= -self.config.minimum_trade_flow_imbalance
        )

    @staticmethod
    def _active_confluence(state: StrategyInput, long_position: bool) -> int:
        structure = state.market_structure
        if structure is None:
            return 0
        return structure.long_confluence if long_position else structure.short_confluence

    def _opposite_confirmation(self, state: StrategyInput, *, long_position: bool) -> bool:
        structure = state.market_structure
        if structure is None or not structure.ready:
            return False
        one = structure.one_minute
        if long_position:
            structural = one.bearish_bos or one.bearish_choch or one.bearish_sweep
            return (
                structural
                and state.features.trade_flow_imbalance < -self.config.minimum_trade_flow_imbalance
            )
        structural = one.bullish_bos or one.bullish_choch or one.bullish_sweep
        return (
            structural
            and state.features.trade_flow_imbalance > self.config.minimum_trade_flow_imbalance
        )

    @staticmethod
    def _position_age_seconds(memory: ReactiveScalperMemory, now_ts_ns: int) -> Decimal:
        opened = memory.position_opened_ts_ns
        return (
            Decimal("0")
            if opened is None
            else Decimal(max(0, now_ts_ns - opened)) / Decimal(ONE_SECOND_NS)
        )

    def _position_exit_prices(
        self,
        memory: ReactiveScalperMemory,
        stop_threshold: Decimal,
        take_profit: Decimal,
    ) -> tuple[Decimal, Decimal]:
        assert memory.average_entry_price is not None
        side = OrderSide.BUY if memory.inventory_base > 0 else OrderSide.SELL
        stop_loss = memory.active_stop_loss_bps or self.config.minimum_stop_loss_bps
        return self._exit_prices(
            memory.average_entry_price,
            side,
            stop_loss,
            take_profit,
            stop_threshold=stop_threshold,
        )

    @staticmethod
    def _exit_prices(
        entry: Decimal,
        side: OrderSide,
        stop_loss_bps: Decimal,
        take_profit_bps: Decimal,
        *,
        stop_threshold: Decimal | None = None,
    ) -> tuple[Decimal, Decimal]:
        direction = Decimal("1") if side is OrderSide.BUY else Decimal("-1")
        stop_bps = -stop_loss_bps if stop_threshold is None else stop_threshold
        stop = entry * (Decimal("1") + direction * stop_bps / Decimal("10000"))
        target = entry * (Decimal("1") + direction * take_profit_bps / Decimal("10000"))
        return stop, target

    @staticmethod
    def _clip(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
        return max(lower, min(upper, value))

    @staticmethod
    def _transition(
        memory: ReactiveScalperMemory,
        action: StrategyAction,
        reason: str,
        *,
        expected_edge: Decimal = Decimal("0"),
        required_edge: Decimal = Decimal("0"),
        confluence: int = 0,
    ) -> StrategyTransition[ReactiveScalperMemory]:
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
