from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from aiquanttrader_native.backtest.kernel import KernelBookLevel, KernelMarketState, KernelTrade
from aiquanttrader_native.domain.market import AggressorSide
from aiquanttrader_native.features.engine import IncrementalFeatureEngine, replay_features
from aiquanttrader_native.features.models import (
    MODEL_FEATURE_SCHEMA,
    FeatureEngineConfig,
    InventoryState,
    VolatilityRegime,
)
from aiquanttrader_native.features.storage import write_feature_dataset


def market_state(
    sequence: int,
    *,
    bid: str,
    ask: str,
    bid_size: str,
    ask_size: str,
    trades: tuple[KernelTrade, ...] = (),
    delay_ns: int = 100,
) -> KernelMarketState:
    exchange_ts = (sequence + 1) * 1_000
    return KernelMarketState(
        exchange_ts_ns=exchange_ts,
        book_exchange_ts_ns=exchange_ts,
        observed_ts_ns=exchange_ts + delay_ns,
        sequence=sequence,
        bids=(KernelBookLevel(price=Decimal(bid), size=Decimal(bid_size)),),
        asks=(KernelBookLevel(price=Decimal(ask), size=Decimal(ask_size)),),
        trades=trades,
    )


def trade(sequence: int, side: AggressorSide, price: str, size: str) -> KernelTrade:
    return KernelTrade(
        exchange_ts_ns=(sequence + 1) * 1_000,
        observed_ts_ns=(sequence + 1) * 1_000 + 100,
        price=Decimal(price),
        size=Decimal(size),
        aggressor=side,
    )


def config(**overrides: object) -> FeatureEngineConfig:
    values: dict[str, object] = {
        "depth_levels": 1,
        "flow_window_ns": 10_000,
        "volatility_window_ns": 10_000,
        "spread_window_ns": 10_000,
        "markout_horizon_ns": 500,
        "warmup_samples": 2,
        "maximum_input_age_ns": 500,
        "low_volatility_bps": Decimal("1"),
        "high_volatility_bps": Decimal("1000"),
        "inventory_limit_base": Decimal("0.1"),
    }
    values.update(overrides)
    return FeatureEngineConfig.model_validate(values)


def states() -> tuple[KernelMarketState, ...]:
    return (
        market_state(0, bid="100", ask="101", bid_size="5", ask_size="5"),
        market_state(
            1,
            bid="100",
            ask="101",
            bid_size="2",
            ask_size="5",
            trades=(trade(1, AggressorSide.SELLER, "100", "3"),),
        ),
        market_state(
            2,
            bid="101",
            ask="102",
            bid_size="3",
            ask_size="2",
            trades=(trade(2, AggressorSide.BUYER, "102", "4"),),
        ),
        market_state(3, bid="101", ask="102", bid_size="3", ask_size="2"),
    )


def test_incremental_features_are_causal_bounded_and_numerically_stable() -> None:
    inventory = InventoryState(
        confirmed_base=Decimal("0.05"),
        target_base=Decimal("0.01"),
        liquidation_distance_bps=Decimal("500"),
        margin_utilization=Decimal("0.2"),
    )
    snapshots = replay_features(states(), config=config(), inventory=inventory)

    assert not snapshots[0].ready
    assert snapshots[0].volatility_regime is VolatilityRegime.WARMUP
    assert snapshots[1].ready
    assert snapshots[1].trade_flow_imbalance == Decimal("-1.0")
    assert snapshots[2].trade_flow_imbalance == Decimal(str(1 / 7))
    assert snapshots[3].volume_delta == snapshots[2].volume_delta
    assert snapshots[2].book_imbalance == Decimal("0.2")
    assert snapshots[2].queue_imbalance == Decimal("0.2")
    assert snapshots[2].inventory_drift_base == Decimal("0.04")
    assert snapshots[2].inventory_risk == Decimal("0.4")
    assert snapshots[2].fill_model_calibrated is False
    assert snapshots[2].max_input_age_ns == 100
    assert snapshots[2].model_vector().shape == (len(MODEL_FEATURE_SCHEMA.features),)


def test_incremental_and_batch_paths_are_exactly_identical() -> None:
    batch = replay_features(states(), config=config())
    engine = IncrementalFeatureEngine(config())
    live = tuple(engine.update(item) for item in states())
    assert batch == live


def test_feature_engine_rejects_stale_non_monotonic_and_noncausal_computation() -> None:
    engine = IncrementalFeatureEngine(config(maximum_input_age_ns=50))
    with pytest.raises(ValueError, match="maximum allowed age"):
        engine.update(states()[0])

    engine = IncrementalFeatureEngine(config())
    engine.update(states()[0])
    with pytest.raises(ValueError, match="strictly increasing"):
        engine.update(states()[0])

    engine = IncrementalFeatureEngine(config())
    with pytest.raises(ValueError, match="cannot precede"):
        engine.update(states()[0], computed_ts_ns=1_000)

    engine = IncrementalFeatureEngine(config())
    fresh_trade_on_stale_book = states()[1].model_copy(
        update={"book_exchange_ts_ns": states()[0].book_exchange_ts_ns}
    )
    with pytest.raises(ValueError, match="maximum allowed age"):
        engine.update(fresh_trade_on_stale_book)


def test_feature_dataset_is_deterministic_and_manifest_bound(tmp_path: Path) -> None:
    first_path, first = write_feature_dataset(
        states(),
        config=config(),
        source_dataset_sha256="a" * 64,
        output_root=tmp_path,
        relative_path="features/BTC.parquet",
    )
    second_path, second = write_feature_dataset(
        states(),
        config=config(),
        source_dataset_sha256="a" * 64,
        output_root=tmp_path,
        relative_path="features/BTC.parquet",
    )
    assert first == second
    assert first_path == second_path
    assert pq.read_table(tmp_path / first.relative_path).num_rows == len(states())
    assert first.feature_schema_sha256 == MODEL_FEATURE_SCHEMA.sha256()

    with pytest.raises(ValueError, match=r"\.parquet"):
        write_feature_dataset(
            states(),
            config=config(),
            source_dataset_sha256="a" * 64,
            output_root=tmp_path,
            relative_path="features/BTC.csv",
        )
