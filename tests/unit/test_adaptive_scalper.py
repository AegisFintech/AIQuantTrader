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
from aiquanttrader.strategies.adaptive_scalper import (
    AdaptiveForecastState,
    AdaptiveScalperConfig,
    AdaptiveScalperKernel,
    AdaptiveScalperMemory,
    PendingForecast,
)
from aiquanttrader.strategies.common import StrategyInput

SECOND_NS = 1_000_000_000


def _feature(
    price: Decimal = Decimal("100"), *, observed_ts_ns: int = 2 * SECOND_NS
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
        atr_bps=Decimal("2"),
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


def _test_config(**updates: object) -> AdaptiveScalperConfig:
    values: dict[str, object] = {
        "maximum_spread_bps": Decimal("200"),
        "minimum_book_imbalance": Decimal("0"),
        "minimum_trade_flow_imbalance": Decimal("0"),
        "minimum_microprice_edge_bps": Decimal("0"),
        "minimum_forecast_edge_bps": Decimal("0.01"),
        "minimum_net_edge_bps": Decimal("0.01"),
        "safety_margin_bps": Decimal("0.01"),
        "forecast_horizon_ns": SECOND_NS,
        "forecast_minimum_samples": 100,
        "forecast_recent_window": 50,
        "forecast_minimum_directional_accuracy": Decimal("0.50"),
        "forecast_maximum_mae_bps": Decimal("100"),
        "cooldown_ns": 0,
        "reject_high_volatility": False,
    }
    values.update(updates)
    return AdaptiveScalperConfig.model_validate(values)


def _trained_memory(
    kernel: AdaptiveScalperKernel,
) -> tuple[AdaptiveScalperMemory, MicrostructureSnapshot]:
    memory = AdaptiveScalperMemory()
    feature = _feature()
    for index in range(130):
        price = Decimal("100") + Decimal(index) / Decimal("100")
        observed = (index + 2) * SECOND_NS
        feature = _feature(price, observed_ts_ns=observed)
        transition = kernel.decide(StrategyInput(features=feature), memory)
        memory = transition.memory
    return memory, feature


def _ready_memory(prediction_bps: Decimal = Decimal("20")) -> AdaptiveScalperMemory:
    return AdaptiveScalperMemory(
        forecast=AdaptiveForecastState(
            weights=(prediction_bps,) + (Decimal("0"),) * 7,
            training_samples=100,
            recent_direction_hits=(True,) * 50,
            recent_absolute_errors_bps=(Decimal("1"),) * 50,
            latest_prediction_bps=prediction_bps,
        )
    )


def test_online_forecast_resolves_only_after_causal_horizon() -> None:
    config = _test_config()
    kernel = AdaptiveScalperKernel(config)
    first = kernel.decide(
        StrategyInput(features=_feature(observed_ts_ns=2 * SECOND_NS)), AdaptiveScalperMemory()
    )
    assert first.memory.forecast.training_samples == 0
    assert len(first.memory.forecast.pending) == 1

    early = kernel.decide(
        StrategyInput(features=_feature(Decimal("101"), observed_ts_ns=2 * SECOND_NS + 1)),
        first.memory,
    )
    assert early.memory.forecast.training_samples == 0
    assert len(early.memory.forecast.pending) == 1
    resolved = kernel.decide(
        StrategyInput(features=_feature(Decimal("101"), observed_ts_ns=3 * SECOND_NS)),
        early.memory,
    )
    assert resolved.memory.forecast.training_samples == 1
    assert resolved.memory.forecast.weights != (Decimal("0"),) * 8


def test_adaptive_scalper_learns_then_places_expiring_maker_entry() -> None:
    config = _test_config()
    kernel = AdaptiveScalperKernel(config)
    memory, feature = _trained_memory(kernel)
    assert memory.forecast.ready(config)
    assert memory.forecast.directional_accuracy >= Decimal("0.50")
    assert memory.forecast.latest_prediction_bps > 0
    assert len(memory.model_dump_json()) < 65_536

    structure = _structure(feature.receive_ts_ns)
    entered = kernel.decide(
        StrategyInput(
            features=feature,
            market_structure=structure,
            estimated_maker_fee_bps=Decimal("0"),
            estimated_taker_fee_bps=Decimal("0"),
            estimated_slippage_bps=Decimal("0"),
        ),
        memory,
    )
    assert entered.decision.action is StrategyAction.ENTER_LONG
    intent = entered.decision.submit[0]
    assert intent.kind.value == "limit"
    assert intent.post_only
    assert intent.limit_price == feature.best_bid
    assert entered.memory.pending_entry_intent_id == intent.intent_id

    waiting = kernel.decide(
        StrategyInput(
            features=feature.model_copy(
                update={
                    "receive_ts_ns": feature.receive_ts_ns + SECOND_NS,
                    "computed_ts_ns": feature.receive_ts_ns + SECOND_NS,
                }
            ),
            market_structure=structure,
        ),
        entered.memory,
    )
    assert waiting.decision.reason == "maker_entry_resting"
    expired_ts = feature.receive_ts_ns + config.maker_entry_ttl_ns
    expired = kernel.decide(
        StrategyInput(
            features=feature.model_copy(
                update={"receive_ts_ns": expired_ts, "computed_ts_ns": expired_ts}
            ),
            market_structure=structure,
        ),
        waiting.memory,
    )
    assert expired.decision.cancel_intent_ids == (intent.intent_id,)
    assert expired.memory.pending_entry_intent_id is None


def test_adaptive_scalper_quality_gate_and_bounded_exits() -> None:
    config = _test_config()
    kernel = AdaptiveScalperKernel(config)
    feature = _feature()
    poor = AdaptiveForecastState(
        training_samples=100,
        recent_direction_hits=(False,) * 50,
        recent_absolute_errors_bps=(Decimal("1"),) * 50,
        latest_prediction_bps=Decimal("20"),
    )
    blocked = kernel.decide(
        StrategyInput(features=feature, market_structure=_structure(feature.receive_ts_ns)),
        AdaptiveScalperMemory(forecast=poor),
    )
    assert blocked.decision.action is StrategyAction.BLOCKED_MODEL
    assert blocked.decision.reason == "forecast_directional_accuracy_below_gate"

    opened = AdaptiveScalperMemory(
        inventory_base=Decimal("0.001"),
        average_entry_price=Decimal("100"),
        position_opened_ts_ns=feature.receive_ts_ns,
        forecast=poor.model_copy(update={"latest_prediction_bps": Decimal("0")}),
    )
    stop_ts = feature.receive_ts_ns + 10 * SECOND_NS
    stopped = kernel.decide(
        StrategyInput(
            features=_feature(Decimal("99.90"), observed_ts_ns=stop_ts),
            market_structure=_structure(stop_ts),
            position_average_entry_price=Decimal("100"),
            position_opened_ts_ns=feature.receive_ts_ns,
        ),
        opened,
    )
    assert stopped.decision.action is StrategyAction.EXIT_STOP_LOSS
    assert stopped.decision.submit[0].reduce_only

    limit_ts = feature.receive_ts_ns + config.hard_holding_limit_ns
    limited = kernel.decide(
        StrategyInput(
            features=_feature(Decimal("100.05"), observed_ts_ns=limit_ts),
            market_structure=_structure(limit_ts),
            position_average_entry_price=Decimal("100"),
            position_opened_ts_ns=feature.receive_ts_ns,
        ),
        opened,
    )
    assert limited.decision.reason == "three_minute_hard_limit"


@pytest.mark.parametrize(
    ("config_updates", "feature_updates", "memory_updates", "structure_updates", "reason"),
    [
        ({"forecast_maximum_mae_bps": Decimal("0.5")}, {}, {}, {}, "forecast_error_above_gate"),
        ({"maximum_spread_bps": Decimal("1")}, {}, {}, {}, "spread_above_maker_entry_limit"),
        (
            {"reject_high_volatility": True},
            {"volatility_regime": VolatilityRegime.HIGH},
            {},
            {},
            "high_volatility_regime",
        ),
        (
            {"cooldown_ns": 10 * SECOND_NS},
            {},
            {"last_flat_ts_ns": 1},
            {},
            "post_exit_cooldown",
        ),
        (
            {},
            {},
            {"last_entry_structure_revision": 10},
            {},
            "one_entry_attempt_per_closed_bar_revision",
        ),
        (
            {"maximum_entry_attempts_per_day": 1},
            {},
            {"entry_attempt_day": 0, "entry_attempts_today": 1},
            {},
            "daily_entry_attempt_cap_reached",
        ),
        (
            {},
            {},
            {},
            {"fifteen_minute": _timeframe(900, StructureDirection.NEUTRAL)},
            "15m_direction_neutral",
        ),
        (
            {},
            {},
            {},
            {"five_minute": _timeframe(300, StructureDirection.BEARISH)},
            "5m_direction_not_aligned",
        ),
        (
            {},
            {},
            {},
            {
                "one_minute": _timeframe(60, StructureDirection.NEUTRAL),
            },
            "1m_structure_trigger_absent",
        ),
        ({}, {}, {}, {"long_confluence": 1}, "multi_timeframe_confluence_below_threshold"),
        (
            {"minimum_book_imbalance": Decimal("0.9")},
            {},
            {},
            {},
            "book_imbalance_not_aligned",
        ),
        (
            {"minimum_trade_flow_imbalance": Decimal("0.8")},
            {},
            {},
            {},
            "trade_flow_not_aligned",
        ),
        (
            {"minimum_microprice_edge_bps": Decimal("30")},
            {},
            {},
            {},
            "microprice_not_aligned",
        ),
        (
            {"minimum_forecast_edge_bps": Decimal("25")},
            {},
            {},
            {},
            "forecast_does_not_clear_maker_taker_cost",
        ),
    ],
)
def test_adaptive_scalper_entry_gates_are_explicit(
    config_updates: dict[str, object],
    feature_updates: dict[str, object],
    memory_updates: dict[str, object],
    structure_updates: dict[str, object],
    reason: str,
) -> None:
    config = _test_config(**config_updates)
    kernel = AdaptiveScalperKernel(config)
    feature = _feature().model_copy(update=feature_updates)
    memory = _ready_memory().model_copy(update=memory_updates)
    structure = _structure(feature.receive_ts_ns).model_copy(update=structure_updates)

    transition = kernel.decide(
        StrategyInput(
            features=feature,
            market_structure=structure,
            estimated_maker_fee_bps=Decimal("0"),
            estimated_taker_fee_bps=Decimal("0"),
            estimated_slippage_bps=Decimal("0"),
        ),
        memory,
    )

    assert transition.decision.reason == reason
    assert transition.decision.submit == ()


def test_adaptive_scalper_short_entry_and_profit_protection_paths() -> None:
    config = _test_config()
    kernel = AdaptiveScalperKernel(config)
    feature = _feature().model_copy(
        update={
            "book_imbalance": Decimal("-0.5"),
            "trade_flow_imbalance": Decimal("-0.5"),
            "microprice": Decimal("99.8"),
        }
    )
    short_entry = kernel.decide(
        StrategyInput(
            features=feature,
            market_structure=_structure(feature.receive_ts_ns, bullish=False),
            estimated_maker_fee_bps=Decimal("0"),
            estimated_taker_fee_bps=Decimal("0"),
            estimated_slippage_bps=Decimal("0"),
        ),
        _ready_memory(Decimal("-20")),
    )
    assert short_entry.decision.action is StrategyAction.ENTER_SHORT
    assert short_entry.decision.submit[0].side.value == "sell"
    assert short_entry.decision.submit[0].limit_price == feature.best_ask

    opened = AdaptiveScalperMemory(
        inventory_base=Decimal("0.001"),
        average_entry_price=Decimal("100"),
        position_opened_ts_ns=feature.receive_ts_ns,
        peak_favorable_bps=Decimal("20"),
        forecast=_ready_memory().forecast,
    )
    protected_ts = feature.receive_ts_ns + 20 * SECOND_NS
    protected = kernel.decide(
        StrategyInput(
            features=_feature(Decimal("100.10"), observed_ts_ns=protected_ts),
            market_structure=_structure(protected_ts),
        ),
        opened,
    )
    assert protected.decision.action is StrategyAction.EXIT_TAKE_PROFIT
    assert protected.decision.reason == "trailing_profit_protection"

    take_profit = kernel.decide(
        StrategyInput(
            features=_feature(Decimal("100.20"), observed_ts_ns=protected_ts),
            market_structure=_structure(protected_ts),
        ),
        opened.model_copy(update={"peak_favorable_bps": Decimal("0")}),
    )
    assert take_profit.decision.reason == "take_profit_reached"


def test_adaptive_scalper_position_reversal_hold_and_retry_paths() -> None:
    config = _test_config()
    kernel = AdaptiveScalperKernel(config)
    observed = 20 * SECOND_NS
    context_missing = kernel.decide(
        StrategyInput(features=_feature(observed_ts_ns=observed)),
        AdaptiveScalperMemory(inventory_base=Decimal("0.001")),
    )
    assert context_missing.decision.reason == "awaiting_confirmed_position_context"

    opened = AdaptiveScalperMemory(
        inventory_base=Decimal("0.001"),
        average_entry_price=Decimal("100"),
        position_opened_ts_ns=observed,
        forecast=_ready_memory().forecast,
    )
    holding = kernel.decide(
        StrategyInput(
            features=_feature(Decimal("100.01"), observed_ts_ns=observed + SECOND_NS),
            market_structure=_structure(observed + SECOND_NS),
        ),
        opened,
    )
    assert holding.decision.action is StrategyAction.HOLD
    assert holding.decision.reason == "position_within_exit_bounds"

    reversal_feature = _feature(Decimal("100"), observed_ts_ns=observed + 2 * SECOND_NS).model_copy(
        update={"trade_flow_imbalance": Decimal("-0.5")}
    )
    reversal = kernel.decide(
        StrategyInput(
            features=reversal_feature, market_structure=_structure(observed, bullish=False)
        ),
        opened.model_copy(update={"forecast": _ready_memory(Decimal("-10")).forecast}),
    )
    assert reversal.decision.reason == "forecast_and_flow_reversal"

    retry = kernel.decide(
        StrategyInput(
            features=_feature(Decimal("99.90"), observed_ts_ns=observed + 3 * SECOND_NS),
            market_structure=_structure(observed + 3 * SECOND_NS),
        ),
        opened.model_copy(update={"last_order_ts_ns": observed + 3 * SECOND_NS}),
    )
    assert retry.decision.action is StrategyAction.HOLD
    assert retry.decision.reason == "exit_order_pending_activation"


def test_adaptive_scalper_models_fail_closed() -> None:
    with pytest.raises(ValidationError, match="recent window"):
        AdaptiveScalperConfig(forecast_minimum_samples=100, forecast_recent_window=101)
    with pytest.raises(ValidationError, match="five minutes"):
        AdaptiveScalperConfig(
            soft_holding_limit_ns=300 * SECOND_NS,
            hard_holding_limit_ns=301 * SECOND_NS,
        )
    with pytest.raises(ValidationError, match="checkpoint-safe"):
        AdaptiveScalperConfig(
            forecast_horizon_ns=300 * SECOND_NS,
            forecast_sample_interval_ns=SECOND_NS,
        )
    with pytest.raises(ValidationError, match="sample interval"):
        AdaptiveScalperConfig(
            forecast_horizon_ns=SECOND_NS,
            forecast_sample_interval_ns=2 * SECOND_NS,
        )
    for values, message in (
        ({"break_even_trigger_bps": Decimal("18")}, "break-even trigger"),
        ({"break_even_offset_bps": Decimal("11")}, "break-even offset"),
        ({"trailing_trigger_bps": Decimal("18")}, "trailing trigger"),
        ({"trailing_giveback_bps": Decimal("14")}, "trailing giveback"),
    ):
        with pytest.raises(ValidationError, match=message):
            AdaptiveScalperConfig.model_validate(values)
    with pytest.raises(ValidationError, match="supplied together"):
        AdaptiveScalperMemory(pending_entry_intent_id="entry")
    with pytest.raises(ValidationError, match="unexpected width"):
        PendingForecast(
            observed_ts_ns=1,
            midprice=Decimal("1"),
            vector=(Decimal("1"),),
            prediction_bps=Decimal("0"),
        )
    with pytest.raises(ValidationError, match="hard bound"):
        AdaptiveForecastState(
            recent_direction_hits=(True,) * 2_049,
            recent_absolute_errors_bps=(Decimal("1"),) * 2_049,
        )
    with pytest.raises(ValidationError, match="aligned"):
        AdaptiveForecastState(
            recent_direction_hits=(True,),
            recent_absolute_errors_bps=(),
        )

    memory = AdaptiveScalperMemory()
    with pytest.raises(ValueError, match="average entry price"):
        memory.synchronize_position(Decimal("1"), None, 1)
    opened = memory.synchronize_position(Decimal("1"), Decimal("100"), 1)
    flat = opened.synchronize_position(Decimal("0"), None, 2)
    assert flat.last_flat_ts_ns == 2
