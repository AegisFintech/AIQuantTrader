from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from aiquanttrader.backtest.kernel import StrategyAction
from aiquanttrader.features.market_structure import (
    DealingRangeZone,
    SmartMoneySnapshot,
    StructureDirection,
    TimeframeStructure,
)
from aiquanttrader.features.models import (
    MODEL_FEATURE_SCHEMA,
    MicrostructureSnapshot,
    VolatilityRegime,
)
from aiquanttrader.strategies.common import StrategyInput
from aiquanttrader.strategies.reactive_scalper import (
    HorizonForecastState,
    PendingForecast,
    ReactiveForecastState,
    ReactiveScalperConfig,
    ReactiveScalperKernel,
    ReactiveScalperMemory,
)

SECOND_NS = 1_000_000_000


def _feature(
    price: Decimal = Decimal("100"), *, observed_ts_ns: int = 10 * SECOND_NS
) -> MicrostructureSnapshot:
    return MicrostructureSnapshot(
        feature_schema_sha256=MODEL_FEATURE_SCHEMA.sha256(),
        sequence=1,
        event_ts_ns=observed_ts_ns,
        receive_ts_ns=observed_ts_ns,
        computed_ts_ns=observed_ts_ns,
        max_input_age_ns=0,
        ready=True,
        warmup_count=20,
        best_bid=price - Decimal("0.5"),
        best_ask=price + Decimal("0.5"),
        midprice=price,
        book_imbalance=Decimal("0.50"),
        microprice=price + Decimal("0.20"),
        vamp=price + Decimal("0.50"),
        weighted_midprice=price + Decimal("0.10"),
        queue_imbalance=Decimal("0.40"),
        depth_imbalance=Decimal("0.30"),
        trade_flow_imbalance=Decimal("0.50"),
        buy_pressure=Decimal("2"),
        sell_pressure=Decimal("1"),
        aggressor_ratio=Decimal("0.67"),
        volume_delta=Decimal("1"),
        signed_volume=Decimal("1"),
        realized_volatility=Decimal("2"),
        volatility_regime=VolatilityRegime.NORMAL,
        atr_bps=Decimal("10"),
        spread_bps=Decimal("100"),
        spread_change_bps=Decimal("0"),
        spread_zscore=Decimal("0"),
        mid_return_bps=Decimal("1"),
        inventory_base=Decimal("0"),
        target_inventory_base=Decimal("0"),
        inventory_drift_base=Decimal("0"),
        inventory_risk=Decimal("0"),
        liquidation_distance_bps=Decimal("1000"),
        margin_utilization=Decimal("0"),
        fill_probability_bid=Decimal("0.5"),
        fill_probability_ask=Decimal("0.5"),
        queue_ahead_bid=Decimal("1"),
        queue_ahead_ask=Decimal("1"),
        adverse_selection_bps=Decimal("0"),
        fill_model_id="test-model",
        fill_model_calibrated=True,
    )


def _timeframe(seconds: int, direction: StructureDirection) -> TimeframeStructure:
    return TimeframeStructure.model_validate(
        {
            "timeframe_seconds": seconds,
            "closed_bars": 20,
            "last_closed_ts_ns": 1,
            "close": "100",
            "direction": direction,
            "zone": DealingRangeZone.DISCOUNT,
            "support": "99",
            "resistance": "101",
            "bullish_bos": direction is StructureDirection.BULLISH,
            "bearish_bos": direction is StructureDirection.BEARISH,
        }
    )


def _structure(observed_ts_ns: int, *, bullish: bool = True) -> SmartMoneySnapshot:
    direction = StructureDirection.BULLISH if bullish else StructureDirection.BEARISH
    return SmartMoneySnapshot(
        observed_ts_ns=observed_ts_ns,
        revision=10,
        ready=True,
        one_minute=_timeframe(60, direction),
        five_minute=_timeframe(300, direction),
        fifteen_minute=_timeframe(900, direction),
        long_confluence=8 if bullish else 1,
        short_confluence=1 if bullish else 8,
        long_reasons=("15m_bias", "5m_structure", "1m_bos") if bullish else (),
        short_reasons=("15m_bias", "5m_structure", "1m_bos") if not bullish else (),
    )


def _test_config(**updates: object) -> ReactiveScalperConfig:
    values: dict[str, object] = {
        "maximum_spread_bps": Decimal("200"),
        "minimum_book_imbalance": Decimal("0"),
        "minimum_trade_flow_imbalance": Decimal("0"),
        "minimum_microprice_edge_bps": Decimal("0"),
        "minimum_intrabar_momentum_bps": Decimal("0"),
        "minimum_signal_persistence": 1,
        "minimum_forecast_edge_bps": Decimal("0.01"),
        "minimum_net_edge_bps": Decimal("0.01"),
        "safety_margin_bps": Decimal("0.01"),
        "forecast_horizons_ns": (SECOND_NS, 2 * SECOND_NS),
        "forecast_sample_interval_ns": SECOND_NS,
        "forecast_minimum_samples": 100,
        "forecast_recent_window": 50,
        "forecast_minimum_directional_accuracy": Decimal("0.50"),
        "forecast_maximum_mae_bps": Decimal("100"),
        "minimum_quality_horizons": 2,
        "minimum_aligned_forecasts": 2,
        "cooldown_ns": 0,
        "minimum_entry_interval_ns": 0,
        "high_volatility_confluence_bonus": 0,
    }
    values.update(updates)
    return ReactiveScalperConfig.model_validate(values)


def _horizon(
    horizon_ns: int,
    prediction_bps: Decimal,
    *,
    hits: bool = True,
    error_bps: Decimal = Decimal("1"),
) -> HorizonForecastState:
    return HorizonForecastState(
        horizon_ns=horizon_ns,
        weights=(prediction_bps,) + (Decimal("0"),) * 7,
        training_samples=100,
        recent_direction_hits=(hits,) * 50,
        recent_absolute_errors_bps=(error_bps,) * 50,
        latest_prediction_bps=prediction_bps,
    )


def _ready_forecast(
    first: Decimal = Decimal("20"), second: Decimal | None = None
) -> ReactiveForecastState:
    other = first if second is None else second
    return ReactiveForecastState(
        horizons=(
            _horizon(SECOND_NS, first),
            _horizon(2 * SECOND_NS, other),
        )
    )


def _ready_memory(
    first: Decimal = Decimal("20"), second: Decimal | None = None
) -> ReactiveScalperMemory:
    return ReactiveScalperMemory(forecast=_ready_forecast(first, second))


def _entry_input(
    feature: MicrostructureSnapshot,
    structure: SmartMoneySnapshot,
) -> StrategyInput:
    return StrategyInput(
        features=feature,
        market_structure=structure,
        estimated_maker_fee_bps=Decimal("0"),
        estimated_taker_fee_bps=Decimal("0"),
        estimated_slippage_bps=Decimal("0"),
    )


def test_multi_horizon_forecast_resolves_only_after_each_causal_horizon() -> None:
    kernel = ReactiveScalperKernel(_test_config())
    first = kernel.decide(
        StrategyInput(features=_feature(observed_ts_ns=10 * SECOND_NS)),
        ReactiveScalperMemory(),
    )
    assert len(first.memory.forecast.pending) == 1
    after_one = kernel.decide(
        StrategyInput(features=_feature(Decimal("101"), observed_ts_ns=11 * SECOND_NS)),
        first.memory,
    )
    assert [item.training_samples for item in after_one.memory.forecast.horizons] == [1, 0]
    after_two = kernel.decide(
        StrategyInput(features=_feature(Decimal("102"), observed_ts_ns=12 * SECOND_NS)),
        after_one.memory,
    )
    assert [item.training_samples for item in after_two.memory.forecast.horizons] == [2, 1]
    assert all(item.weights != (Decimal("0"),) * 8 for item in after_two.memory.forecast.horizons)


def test_reactive_entry_requires_persistence_then_expires_unfilled_maker() -> None:
    config = _test_config(minimum_signal_persistence=3)
    kernel = ReactiveScalperKernel(config)
    feature = _feature()
    structure = _structure(feature.receive_ts_ns)
    memory = _ready_memory()
    first = kernel.decide(_entry_input(feature, structure), memory)
    assert first.decision.reason == "intrabar_signal_persistence_warming"
    second_feature = feature.model_copy(
        update={
            "receive_ts_ns": feature.receive_ts_ns + SECOND_NS,
            "computed_ts_ns": feature.receive_ts_ns + SECOND_NS,
        }
    )
    second = kernel.decide(_entry_input(second_feature, structure), first.memory)
    third_feature = second_feature.model_copy(
        update={
            "receive_ts_ns": second_feature.receive_ts_ns + SECOND_NS,
            "computed_ts_ns": second_feature.receive_ts_ns + SECOND_NS,
        }
    )
    entered = kernel.decide(_entry_input(third_feature, structure), second.memory)
    assert entered.decision.action is StrategyAction.ENTER_LONG
    intent = entered.decision.submit[0]
    assert intent.post_only and intent.limit_price == third_feature.best_bid
    assert intent.intent_id.startswith("rsc3-entry-buy-")
    waiting = kernel.decide(
        StrategyInput(features=third_feature, market_structure=structure), entered.memory
    )
    assert waiting.decision.reason == "maker_entry_resting"
    expired_ts = third_feature.receive_ts_ns + config.maker_entry_ttl_ns
    expired_feature = third_feature.model_copy(
        update={"receive_ts_ns": expired_ts, "computed_ts_ns": expired_ts}
    )
    expired = kernel.decide(
        StrategyInput(features=expired_feature, market_structure=structure), waiting.memory
    )
    assert expired.decision.cancel_intent_ids == (intent.intent_id,)
    assert expired.memory.pending_entry_intent_id is None


def test_resting_maker_entry_cancels_when_order_flow_reverses() -> None:
    kernel = ReactiveScalperKernel(_test_config())
    feature = _feature()
    structure = _structure(feature.receive_ts_ns)
    entered = kernel.decide(
        _entry_input(feature, structure),
        _ready_memory(),
    )
    reverse_ts = feature.receive_ts_ns + SECOND_NS
    reversed_feature = feature.model_copy(
        update={
            "receive_ts_ns": reverse_ts,
            "computed_ts_ns": reverse_ts,
            "book_imbalance": Decimal("-0.5"),
            "trade_flow_imbalance": Decimal("-0.5"),
        }
    )
    canceled = kernel.decide(
        StrategyInput(features=reversed_feature, market_structure=structure),
        entered.memory,
    )
    assert canceled.decision.reason == "maker_entry_signal_invalidated"
    assert canceled.decision.cancel_intent_ids == (entered.decision.submit[0].intent_id,)


@pytest.mark.parametrize(
    ("config_updates", "feature_updates", "memory", "structure_updates", "reason"),
    [
        ({}, {"ready": False}, ReactiveScalperMemory(), {}, "causal_timeframes_warming"),
        ({}, {}, ReactiveScalperMemory(), {}, "causal_multi_horizon_forecast_warming"),
        (
            {},
            {},
            ReactiveScalperMemory(
                forecast=ReactiveForecastState(
                    horizons=(
                        _horizon(SECOND_NS, Decimal("20"), hits=False),
                        _horizon(2 * SECOND_NS, Decimal("20"), hits=False),
                    )
                )
            ),
            {},
            "forecast_directional_accuracy_below_gate",
        ),
        (
            {"forecast_maximum_mae_bps": Decimal("0.5")},
            {},
            _ready_memory(),
            {},
            "forecast_error_above_gate",
        ),
        (
            {"maximum_spread_bps": Decimal("1")},
            {},
            _ready_memory(),
            {},
            "spread_above_maker_entry_limit",
        ),
        (
            {"cooldown_ns": 10 * SECOND_NS},
            {},
            _ready_memory().model_copy(update={"last_flat_ts_ns": 1}),
            {},
            "post_exit_cooldown",
        ),
        (
            {"minimum_entry_interval_ns": 10 * SECOND_NS},
            {},
            _ready_memory().model_copy(update={"last_entry_attempt_ts_ns": 1}),
            {},
            "entry_attempt_interval_active",
        ),
        (
            {"maximum_entry_attempts_per_day": 1},
            {},
            _ready_memory().model_copy(update={"entry_attempt_day": 0, "entry_attempts_today": 1}),
            {},
            "daily_entry_attempt_cap_reached",
        ),
        (
            {},
            {},
            _ready_memory(),
            {"fifteen_minute": _timeframe(900, StructureDirection.NEUTRAL)},
            "15m_direction_neutral",
        ),
        (
            {},
            {},
            _ready_memory(),
            {"five_minute": _timeframe(300, StructureDirection.BEARISH)},
            "5m_direction_not_aligned",
        ),
        (
            {},
            {"mid_return_bps": Decimal("0")},
            _ready_memory(),
            {"one_minute": _timeframe(60, StructureDirection.NEUTRAL)},
            "intrabar_structure_trigger_absent",
        ),
        (
            {},
            {},
            _ready_memory(),
            {"long_confluence": 1},
            "multi_timeframe_confluence_below_threshold",
        ),
        (
            {"minimum_book_imbalance": Decimal("0.9")},
            {},
            _ready_memory(),
            {},
            "book_imbalance_not_aligned",
        ),
        (
            {"minimum_trade_flow_imbalance": Decimal("0.8")},
            {},
            _ready_memory(),
            {},
            "trade_flow_not_aligned",
        ),
        (
            {"minimum_microprice_edge_bps": Decimal("30")},
            {},
            _ready_memory(),
            {},
            "microprice_not_aligned",
        ),
        (
            {},
            {},
            _ready_memory(Decimal("20"), Decimal("-20")),
            {},
            "multi_horizon_forecasts_not_aligned",
        ),
        (
            {"minimum_forecast_edge_bps": Decimal("25")},
            {},
            _ready_memory(),
            {},
            "forecast_does_not_clear_dynamic_cost",
        ),
    ],
)
def test_reactive_entry_gates_are_explicit(
    config_updates: dict[str, object],
    feature_updates: dict[str, object],
    memory: ReactiveScalperMemory,
    structure_updates: dict[str, object],
    reason: str,
) -> None:
    config = _test_config(**config_updates)
    feature = _feature().model_copy(update=feature_updates)
    structure = _structure(feature.receive_ts_ns).model_copy(update=structure_updates)
    transition = ReactiveScalperKernel(config).decide(_entry_input(feature, structure), memory)
    assert transition.decision.reason == reason
    assert transition.decision.submit == ()


def test_short_high_volatility_entry_halves_size_and_raises_cost_hurdle() -> None:
    config = _test_config(high_volatility_confluence_bonus=1)
    feature = _feature().model_copy(
        update={
            "book_imbalance": Decimal("-0.5"),
            "trade_flow_imbalance": Decimal("-0.5"),
            "microprice": Decimal("99.8"),
            "mid_return_bps": Decimal("-1"),
            "volatility_regime": VolatilityRegime.HIGH,
        }
    )
    entered = ReactiveScalperKernel(config).decide(
        StrategyInput(
            features=feature,
            market_structure=_structure(feature.receive_ts_ns, bullish=False),
            estimated_maker_fee_bps=Decimal("1"),
            estimated_taker_fee_bps=Decimal("3"),
            estimated_slippage_bps=Decimal("1"),
        ),
        _ready_memory(Decimal("-20")),
    )
    assert entered.decision.action is StrategyAction.ENTER_SHORT
    assert entered.decision.required_edge_bps == Decimal("3.8375")
    assert entered.decision.submit[0].quantity_base == Decimal("0.00050")
    assert entered.decision.submit[0].limit_price == feature.best_ask


def _opened_memory(*, now: int = 10 * SECOND_NS) -> ReactiveScalperMemory:
    return ReactiveScalperMemory(
        inventory_base=Decimal("0.001"),
        average_entry_price=Decimal("100"),
        position_opened_ts_ns=now,
        active_stop_loss_bps=Decimal("8"),
        active_take_profit_bps=Decimal("16"),
        forecast=_ready_forecast(),
    )


def test_position_stop_reversal_time_limit_and_hold_paths() -> None:
    kernel = ReactiveScalperKernel(_test_config())
    opened = _opened_memory()
    stopped = kernel.decide(
        StrategyInput(
            features=_feature(Decimal("99.90"), observed_ts_ns=20 * SECOND_NS),
            market_structure=_structure(20 * SECOND_NS),
        ),
        opened,
    )
    assert stopped.decision.action is StrategyAction.EXIT_STOP_LOSS
    assert stopped.decision.submit[0].reduce_only

    reversed_memory = opened.model_copy(update={"forecast": _ready_forecast(Decimal("-10"))})
    reversal_feature = _feature(observed_ts_ns=20 * SECOND_NS).model_copy(
        update={"trade_flow_imbalance": Decimal("-0.5")}
    )
    reversed_exit = kernel.decide(
        StrategyInput(features=reversal_feature, market_structure=_structure(20 * SECOND_NS)),
        reversed_memory,
    )
    assert reversed_exit.decision.reason == "forecast_and_flow_reversal"

    held = kernel.decide(
        StrategyInput(
            features=_feature(Decimal("100.05"), observed_ts_ns=20 * SECOND_NS),
            market_structure=_structure(20 * SECOND_NS),
        ),
        opened,
    )
    assert held.decision.reason == "position_within_exit_bounds"
    hard_ts = opened.position_opened_ts_ns + kernel.config.hard_holding_limit_ns  # type: ignore[operator]
    hard_exit = kernel.decide(
        StrategyInput(
            features=_feature(Decimal("100.05"), observed_ts_ns=hard_ts),
            market_structure=_structure(hard_ts),
        ),
        opened,
    )
    assert hard_exit.decision.reason == "two_minute_hard_limit"


def test_maker_take_profit_cancels_then_falls_back_to_taker() -> None:
    config = _test_config()
    kernel = ReactiveScalperKernel(config)
    opened = _opened_memory()
    target_ts = 20 * SECOND_NS
    submitted = kernel.decide(
        StrategyInput(
            features=_feature(Decimal("100.20"), observed_ts_ns=target_ts),
            market_structure=_structure(target_ts),
        ),
        opened,
    )
    assert submitted.decision.reason == "maker_take_profit_submitted"
    maker = submitted.decision.submit[0]
    assert maker.post_only and maker.reduce_only and maker.kind.value == "limit"
    before_ttl = target_ts + config.maker_exit_ttl_ns - 1
    resting = kernel.decide(
        StrategyInput(
            features=_feature(Decimal("100.20"), observed_ts_ns=before_ttl),
            market_structure=_structure(before_ttl),
        ),
        submitted.memory,
    )
    assert resting.decision.reason == "maker_take_profit_resting"
    cancel_ts = target_ts + config.maker_exit_ttl_ns
    canceling = kernel.decide(
        StrategyInput(
            features=_feature(Decimal("100.20"), observed_ts_ns=cancel_ts),
            market_structure=_structure(cancel_ts),
        ),
        resting.memory,
    )
    assert canceling.decision.cancel_intent_ids == (maker.intent_id,)
    assert canceling.decision.reason == "maker_exit_cancel_requested"
    fallback_ts = cancel_ts + config.maker_exit_cancel_grace_ns
    fallback = kernel.decide(
        StrategyInput(
            features=_feature(Decimal("100.19"), observed_ts_ns=fallback_ts),
            market_structure=_structure(fallback_ts),
        ),
        canceling.memory,
    )
    assert fallback.decision.reason == "maker_take_profit_ttl_expired"
    assert fallback.decision.submit[0].kind.value == "market"


def test_position_synchronization_promotes_planned_risk_and_clears_exit_state() -> None:
    memory = ReactiveScalperMemory(
        planned_stop_loss_bps=Decimal("7"), planned_take_profit_bps=Decimal("14")
    )
    with pytest.raises(ValueError, match="average entry price"):
        memory.synchronize_position(Decimal("1"), None, 1)
    opened = memory.synchronize_position(Decimal("1"), Decimal("100"), 1)
    assert opened.active_stop_loss_bps == Decimal("7")
    assert opened.active_take_profit_bps == Decimal("14")
    flat = opened.synchronize_position(Decimal("0"), None, 2)
    assert flat.last_flat_ts_ns == 2
    assert flat.active_stop_loss_bps is None


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"forecast_minimum_samples": 100, "forecast_recent_window": 101}, "recent window"),
        (
            {"soft_holding_limit_ns": 300 * SECOND_NS, "hard_holding_limit_ns": 301 * SECOND_NS},
            "five minutes",
        ),
        ({"forecast_horizons_ns": (2 * SECOND_NS, SECOND_NS)}, "strictly increasing"),
        (
            {
                "forecast_horizons_ns": (SECOND_NS,),
                "forecast_sample_interval_ns": SECOND_NS,
                "minimum_quality_horizons": 2,
            },
            "quality horizons",
        ),
        (
            {"minimum_quality_horizons": 1, "minimum_aligned_forecasts": 2},
            "aligned forecast",
        ),
        ({"minimum_stop_loss_bps": Decimal("14")}, "minimum stop loss"),
        ({"minimum_take_profit_bps": Decimal("24")}, "minimum take profit"),
        ({"break_even_lock_fraction": Decimal("0.65")}, "break-even lock"),
        ({"trailing_giveback_fraction": Decimal("0.80")}, "trailing giveback"),
    ],
)
def test_reactive_config_fails_closed(values: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        ReactiveScalperConfig.model_validate(values)


def test_reactive_state_models_reject_inconsistent_checkpoint_data() -> None:
    with pytest.raises(ValidationError, match="complete metadata"):
        ReactiveScalperMemory(pending_entry_intent_id="entry")
    with pytest.raises(ValidationError, match="complete metadata"):
        ReactiveScalperMemory(pending_exit_intent_id="exit", pending_exit_created_ts_ns=1)
    with pytest.raises(ValidationError, match="counters require a side"):
        ReactiveScalperMemory(confirmation_count=1)
    with pytest.raises(ValidationError, match="unexpected width"):
        PendingForecast(
            observed_ts_ns=1,
            midprice=Decimal("1"),
            vector=(Decimal("1"),),
            predictions_bps=(Decimal("0"),),
        )
    with pytest.raises(ValidationError, match="hard bound"):
        HorizonForecastState(
            horizon_ns=1,
            recent_direction_hits=(True,) * 2_049,
            recent_absolute_errors_bps=(Decimal("1"),) * 2_049,
        )
    with pytest.raises(ValidationError, match="remain aligned"):
        HorizonForecastState(
            horizon_ns=1,
            recent_direction_hits=(True,),
            recent_absolute_errors_bps=(),
        )


def test_reactive_checkpoint_memory_remains_below_journal_contract() -> None:
    value = Decimal("0.1234567890123456789012345678")
    predictions = (value, value, value, value)
    pending = tuple(
        PendingForecast(
            observed_ts_ns=index,
            midprice=Decimal("80000.12345678901234567890123"),
            vector=(value,) * 8,
            predictions_bps=predictions,
        )
        for index in range(1, 37)
    )
    horizons = tuple(
        HorizonForecastState(
            horizon_ns=horizon,
            weights=(value,) * 8,
            training_samples=1_000,
            recent_direction_hits=(True,) * 128,
            recent_absolute_errors_bps=(value,) * 128,
            latest_prediction_bps=value,
        )
        for horizon in (
            30 * SECOND_NS,
            60 * SECOND_NS,
            120 * SECOND_NS,
            180 * SECOND_NS,
        )
    )
    memory = ReactiveScalperMemory(
        forecast=ReactiveForecastState(
            horizons=horizons,
            pending=pending,
            last_sample_ts_ns=36,
        )
    )
    assert len(memory.model_dump_json()) < 65_536
