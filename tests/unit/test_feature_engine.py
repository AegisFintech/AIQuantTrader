from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError

from aiquanttrader.backtest.kernel import KernelBookLevel, KernelMarketState, KernelTrade
from aiquanttrader.domain.base import canonical_sha256
from aiquanttrader.domain.market import AggressorSide
from aiquanttrader.features.engine import IncrementalFeatureEngine, replay_features
from aiquanttrader.features.models import (
    MODEL_FEATURE_SCHEMA,
    FeatureDatasetManifest,
    FeatureEngineConfig,
    InventoryState,
    VolatilityRegime,
)
from aiquanttrader.features.storage import write_feature_dataset


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
        iter(states()),
        config=config(),
        source_dataset_sha256="a" * 64,
        output_root=tmp_path,
        relative_path="features/BTC.parquet",
    )
    second_path, second = write_feature_dataset(
        (state for state in states()),
        config=config(),
        source_dataset_sha256="a" * 64,
        output_root=tmp_path,
        relative_path="features/BTC.parquet",
    )
    assert first == second
    assert first_path == second_path
    assert pq.read_table(tmp_path / first.relative_path).num_rows == len(states())
    assert first.feature_schema_sha256 == MODEL_FEATURE_SCHEMA.sha256()
    assert first.schema_version == 2
    assert first.stale_trade_exclusion_count == 0
    assert first.stale_book_exclusion_count == 0

    with pytest.raises(ValueError, match=r"\.parquet"):
        write_feature_dataset(
            states(),
            config=config(),
            source_dataset_sha256="a" * 64,
            output_root=tmp_path,
            relative_path="features/BTC.csv",
        )


def test_feature_dataset_records_live_parity_stale_input_exclusions(tmp_path: Path) -> None:
    stale_trade = KernelTrade(
        exchange_ts_ns=100,
        observed_ts_ns=100,
        price=Decimal("100"),
        size=Decimal("1"),
        aggressor=AggressorSide.SELLER,
    )
    stale_book = market_state(
        0,
        bid="100",
        ask="101",
        bid_size="1",
        ask_size="1",
        trades=(stale_trade,),
        delay_ns=1_000,
    )
    fresh_with_stale_trade = market_state(
        1,
        bid="100",
        ask="101",
        bid_size="1",
        ask_size="1",
        trades=(stale_trade,),
    )

    _, manifest = write_feature_dataset(
        (stale_book, fresh_with_stale_trade),
        config=config(maximum_input_age_ns=500),
        source_dataset_sha256="a" * 64,
        output_root=tmp_path,
        relative_path="features/stale-inputs.parquet",
    )

    assert manifest.row_count == 1
    assert manifest.stale_book_exclusion_count == 1
    assert manifest.stale_trade_exclusion_count == 2


def test_feature_dataset_rejects_empty_and_immutable_rewrites(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no snapshots"):
        write_feature_dataset(
            (),
            config=config(),
            source_dataset_sha256="a" * 64,
            output_root=tmp_path,
            relative_path="features/empty.parquet",
        )
    assert not tuple((tmp_path / "features").glob("*.partial"))

    write_feature_dataset(
        states(),
        config=config(),
        source_dataset_sha256="a" * 64,
        output_root=tmp_path,
        relative_path="features/immutable.parquet",
    )
    with pytest.raises(FileExistsError, match="immutable feature dataset differs"):
        write_feature_dataset(
            states()[:-1],
            config=config(),
            source_dataset_sha256="a" * 64,
            output_root=tmp_path,
            relative_path="features/immutable.parquet",
        )


def test_feature_manifest_v1_remains_readable_but_cannot_claim_v2_exclusions() -> None:
    identity = {
        "source_dataset_sha256": "a" * 64,
        "feature_schema_sha256": "b" * 64,
        "feature_config_sha256": "c" * 64,
        "relative_path": "features/legacy.parquet",
        "file_sha256": "d" * 64,
        "row_count": 1,
        "first_receive_ts_ns": 1,
        "last_receive_ts_ns": 1,
    }
    values = {"schema_version": 1, "feature_dataset_id": canonical_sha256(identity), **identity}

    legacy = FeatureDatasetManifest.model_validate(values)

    assert legacy.schema_version == 1
    with pytest.raises(ValidationError, match="cannot declare stale-input exclusions"):
        FeatureDatasetManifest.model_validate({**values, "stale_trade_exclusion_count": 1})
