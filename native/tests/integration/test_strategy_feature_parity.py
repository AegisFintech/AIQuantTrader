from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from hftbacktest import BUY_EVENT, DEPTH_EVENT, SELL_EVENT, TRADE_EVENT, event_dtype
from hftbacktest.data.validation import correct_event_order
from nautilus_trader.model import Price, Quantity
from nautilus_trader.model.data import QuoteTick, TradeTick
from nautilus_trader.model.enums import AggressorSide as NautilusAggressorSide
from nautilus_trader.model.identifiers import InstrumentId, TradeId
from pydantic import ValidationError

from aiquanttrader_native.backtest.kernel import hft_market_states, nautilus_market_states
from aiquanttrader_native.domain.market import OrderSide
from aiquanttrader_native.features.engine import replay_features
from aiquanttrader_native.features.models import FeatureEngineConfig, VolatilityRegime
from aiquanttrader_native.strategies.common import StrategyInput, replay_strategy
from aiquanttrader_native.strategies.config import (
    load_market_maker_config,
    load_scalper_config,
)
from aiquanttrader_native.strategies.market_maker import (
    AvellanedaStoikovConfig,
    AvellanedaStoikovKernel,
    MarketMakerMemory,
)
from aiquanttrader_native.strategies.scalper import (
    OrderFlowScalperConfig,
    OrderFlowScalperKernel,
    ScalperEntryStyle,
    ScalperMemory,
)


def hft_events() -> np.ndarray[Any, np.dtype[Any]]:
    rows = [
        (DEPTH_EVENT | BUY_EVENT, 1_000, 1_100, 100.0, 5.0, 0, 0, 0.0),
        (DEPTH_EVENT | SELL_EVENT, 1_000, 1_100, 101.0, 5.0, 0, 0, 0.0),
        (TRADE_EVENT | SELL_EVENT, 2_000, 2_100, 100.0, 3.0, 0, 0, 0.0),
        (DEPTH_EVENT | BUY_EVENT, 2_000, 2_100, 100.0, 2.0, 0, 0, 0.0),
        (DEPTH_EVENT | SELL_EVENT, 2_000, 2_100, 101.0, 5.0, 0, 0, 0.0),
        (TRADE_EVENT | BUY_EVENT, 3_000, 3_100, 102.0, 4.0, 0, 0, 0.0),
        (DEPTH_EVENT | BUY_EVENT, 3_000, 3_100, 101.0, 3.0, 0, 0, 0.0),
        (DEPTH_EVENT | SELL_EVENT, 3_000, 3_100, 101.0, 0.0, 0, 0, 0.0),
        (DEPTH_EVENT | SELL_EVENT, 3_000, 3_100, 102.0, 2.0, 0, 0, 0.0),
    ]
    raw = np.asarray(rows, dtype=event_dtype)
    return cast(
        np.ndarray[Any, np.dtype[Any]],
        correct_event_order(
            raw,
            np.argsort(raw["exch_ts"], kind="mergesort"),
            np.argsort(raw["local_ts"], kind="mergesort"),
        ),
    )


def nautilus_events() -> list[QuoteTick | TradeTick]:
    instrument = InstrumentId.from_str("BTC-USD-PERP.HYPERLIQUID")
    return [
        QuoteTick(
            instrument_id=instrument,
            bid_price=Price.from_str("100"),
            ask_price=Price.from_str("101"),
            bid_size=Quantity.from_str("5"),
            ask_size=Quantity.from_str("5"),
            ts_event=1_000,
            ts_init=1_100,
        ),
        TradeTick(
            instrument_id=instrument,
            price=Price.from_str("100"),
            size=Quantity.from_str("3"),
            aggressor_side=NautilusAggressorSide.SELLER,
            trade_id=TradeId("trade-1"),
            ts_event=2_000,
            ts_init=2_100,
        ),
        QuoteTick(
            instrument_id=instrument,
            bid_price=Price.from_str("100"),
            ask_price=Price.from_str("101"),
            bid_size=Quantity.from_str("2"),
            ask_size=Quantity.from_str("5"),
            ts_event=2_000,
            ts_init=2_100,
        ),
        TradeTick(
            instrument_id=instrument,
            price=Price.from_str("102"),
            size=Quantity.from_str("4"),
            aggressor_side=NautilusAggressorSide.BUYER,
            trade_id=TradeId("trade-2"),
            ts_event=3_000,
            ts_init=3_100,
        ),
        QuoteTick(
            instrument_id=instrument,
            bid_price=Price.from_str("101"),
            ask_price=Price.from_str("102"),
            bid_size=Quantity.from_str("3"),
            ask_size=Quantity.from_str("2"),
            ts_event=3_000,
            ts_init=3_100,
        ),
    ]


def feature_config() -> FeatureEngineConfig:
    return FeatureEngineConfig(
        depth_levels=1,
        flow_window_ns=10_000,
        volatility_window_ns=10_000,
        spread_window_ns=10_000,
        markout_horizon_ns=500,
        warmup_samples=2,
        maximum_input_age_ns=500,
        low_volatility_bps=Decimal("1"),
        high_volatility_bps=Decimal("1000"),
        fill_model_calibrated=True,
        fill_model_id="synthetic-calibrated",
    )


def parity_snapshots() -> tuple[Any, ...]:
    hft = replay_features(hft_market_states(hft_events(), depth_levels=1), config=feature_config())
    nautilus = replay_features(
        nautilus_market_states(nautilus_events(), depth_levels=1),
        config=feature_config(),
    )
    assert hft == nautilus
    return hft


def test_production_strategy_decisions_match_hft_and_nautilus_representations() -> None:
    hft_snapshots = parity_snapshots()
    nautilus_snapshots = replay_features(
        nautilus_market_states(nautilus_events(), depth_levels=1), config=feature_config()
    )
    hft_inputs = tuple(StrategyInput(features=item) for item in hft_snapshots)
    nautilus_inputs = tuple(StrategyInput(features=item) for item in nautilus_snapshots)

    maker = AvellanedaStoikovKernel(
        AvellanedaStoikovConfig(
            require_calibrated_fill_model=True,
            maximum_quote_spread_bps=Decimal("200"),
            minimum_fill_probability=Decimal("0"),
            minimum_quote_lifetime_ns=0,
            quote_hysteresis_ticks=0,
        )
    )
    maker_hft = replay_strategy(kernel=maker, initial_memory=MarketMakerMemory(), states=hft_inputs)
    maker_nautilus = replay_strategy(
        kernel=maker, initial_memory=MarketMakerMemory(), states=nautilus_inputs
    )
    assert maker_hft == maker_nautilus
    assert len(maker_hft.decisions[1].submit) == 2

    scalper = OrderFlowScalperKernel(
        OrderFlowScalperConfig(
            safety_margin_bps=Decimal("0.1"),
            signal_threshold_bps=Decimal("0.1"),
            cooldown_ns=0,
        )
    )
    cheap_hft = tuple(
        StrategyInput(
            features=item,
            estimated_taker_fee_bps=Decimal("0"),
            estimated_slippage_bps=Decimal("0"),
        )
        for item in hft_snapshots
    )
    cheap_nautilus = tuple(
        StrategyInput(
            features=item,
            estimated_taker_fee_bps=Decimal("0"),
            estimated_slippage_bps=Decimal("0"),
        )
        for item in nautilus_snapshots
    )
    assert replay_strategy(
        kernel=scalper, initial_memory=ScalperMemory(), states=cheap_hft
    ) == replay_strategy(kernel=scalper, initial_memory=ScalperMemory(), states=cheap_nautilus)


def test_market_maker_inventory_limits_calibration_and_cancel_controls() -> None:
    snapshot = parity_snapshots()[1]
    uncalibrated = snapshot.model_copy(
        update={"fill_model_calibrated": False, "fill_model_id": "heuristic"}
    )
    default_kernel = AvellanedaStoikovKernel(AvellanedaStoikovConfig())
    assert (
        default_kernel.decide(
            StrategyInput(features=uncalibrated), MarketMakerMemory()
        ).decision.submit
        == ()
    )

    kernel = AvellanedaStoikovKernel(
        AvellanedaStoikovConfig(
            require_calibrated_fill_model=False,
            maximum_quote_spread_bps=Decimal("200"),
            minimum_fill_probability=Decimal("0"),
            minimum_quote_lifetime_ns=0,
            quote_hysteresis_ticks=0,
        )
    )
    first = kernel.decide(StrategyInput(features=snapshot), MarketMakerMemory())
    assert [intent.side for intent in first.decision.submit] == [OrderSide.BUY, OrderSide.SELL]
    capped = kernel.decide(
        StrategyInput(features=snapshot),
        MarketMakerMemory(inventory_base=Decimal("0.05")),
    )
    assert [intent.side for intent in capped.decision.submit] == [OrderSide.SELL]

    unbound_forecast = StrategyInput(features=snapshot, fill_forecast_bid=Decimal("0.5"))
    assert kernel.decide(unbound_forecast, MarketMakerMemory()).decision.submit == ()
    forecast_kernel = AvellanedaStoikovKernel(
        kernel.config.model_copy(update={"minimum_fill_probability": Decimal("0.05")})
    )
    bound_low_bid = unbound_forecast.model_copy(
        update={
            "fill_forecast_bid": Decimal("0"),
            "fill_forecast_ask": Decimal("0.5"),
            "model_artifact_sha256": "a" * 64,
        }
    )
    assert [
        intent.side
        for intent in forecast_kernel.decide(bound_low_bid, MarketMakerMemory()).decision.submit
    ] == [OrderSide.SELL]

    wide = snapshot.model_copy(update={"spread_bps": Decimal("300")})
    canceled = kernel.decide(StrategyInput(features=wide), first.memory)
    assert set(canceled.decision.cancel_intent_ids) == {
        first.memory.active_bid_intent_id,
        first.memory.active_ask_intent_id,
    }
    assert canceled.memory.active_bid_intent_id is None


def test_scalper_requires_post_cost_edge_and_reduces_inventory() -> None:
    sell_snapshot = parity_snapshots()[1]
    kernel = OrderFlowScalperKernel(
        OrderFlowScalperConfig(
            safety_margin_bps=Decimal("0.1"),
            signal_threshold_bps=Decimal("0.1"),
            maximum_spread_bps=Decimal("200"),
            cooldown_ns=0,
        )
    )
    expensive = kernel.decide(StrategyInput(features=sell_snapshot), ScalperMemory())
    assert expensive.decision.submit == ()

    cheap = StrategyInput(
        features=sell_snapshot,
        estimated_taker_fee_bps=Decimal("0"),
        estimated_slippage_bps=Decimal("0"),
    )
    reducing = kernel.decide(cheap, ScalperMemory(inventory_base=Decimal("0.002")))
    intent = reducing.decision.submit[0]
    assert intent.side is OrderSide.SELL
    assert intent.reduce_only
    assert intent.quantity_base == Decimal("0.001")

    forecast_without_identity = cheap.model_copy(update={"movement_forecast_bps": Decimal("2")})
    assert kernel.decide(forecast_without_identity, ScalperMemory()).decision.submit == ()

    high_vol = sell_snapshot.model_copy(update={"volatility_regime": VolatilityRegime.HIGH})
    assert (
        kernel.decide(
            StrategyInput(
                features=high_vol,
                estimated_taker_fee_bps=Decimal("0"),
                estimated_slippage_bps=Decimal("0"),
            ),
            ScalperMemory(),
        ).decision.submit
        == ()
    )

    passive = OrderFlowScalperKernel(
        OrderFlowScalperConfig(
            entry_style=ScalperEntryStyle.PASSIVE,
            safety_margin_bps=Decimal("0.1"),
            signal_threshold_bps=Decimal("0.1"),
            maximum_spread_bps=Decimal("200"),
            cooldown_ns=0,
        )
    ).decide(cheap, ScalperMemory())
    assert passive.decision.submit[0].post_only


def test_checked_in_strategy_configs_are_strict(project_root: Path) -> None:
    maker = load_market_maker_config(
        project_root / "configs" / "strategies" / "avellaneda-stoikov-v1.toml"
    )
    scalper = load_scalper_config(
        project_root / "configs" / "strategies" / "order-flow-scalper-v1.toml"
    )
    assert maker.require_calibrated_fill_model
    assert scalper.entry_style is ScalperEntryStyle.TAKER

    with pytest.raises(ValidationError):
        AvellanedaStoikovConfig.model_validate({"unknown": True})
