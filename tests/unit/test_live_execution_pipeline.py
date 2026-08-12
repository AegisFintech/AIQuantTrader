from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import (
    BookOrder,
    OrderBookDelta,
    OrderBookDeltas,
    TradeTick,
)
from nautilus_trader.model.enums import (
    AggressorSide as NautilusAggressorSide,
)
from nautilus_trader.model.enums import (
    BookAction,
    BookType,
)
from nautilus_trader.model.enums import (
    OrderSide as NautilusOrderSide,
)
from nautilus_trader.model.identifiers import InstrumentId, TradeId
from nautilus_trader.model.objects import Price, Quantity

from aiquanttrader.backtest.kernel import KernelBookLevel, KernelMarketState
from aiquanttrader.config import load_config
from aiquanttrader.execution.artifacts import (
    LiveStrategyArtifacts,
    load_live_strategy_artifacts,
)
from aiquanttrader.execution.live import (
    EquityBaseline,
    EquityBaselineStore,
    LiveAccountState,
    LiveDecisionPipeline,
    NautilusMarketStateAssembler,
    build_live_risk_snapshot,
    read_live_account_state,
)
from aiquanttrader.features.models import FeatureEngineConfig
from aiquanttrader.strategies.market_maker import (
    AvellanedaStoikovConfig,
    MarketMakerMemory,
)
from aiquanttrader.strategies.scalper import OrderFlowScalperConfig, ScalperMemory

ACCOUNT = "0x" + "1" * 40
INSTRUMENT = InstrumentId.from_str("BTC-USD-PERP.HYPERLIQUID")


def _book() -> tuple[OrderBook, OrderBookDeltas]:
    deltas = OrderBookDeltas(
        INSTRUMENT,
        [
            OrderBookDelta(
                instrument_id=INSTRUMENT,
                action=BookAction.ADD,
                order=BookOrder(
                    NautilusOrderSide.BUY,
                    Price.from_str("100"),
                    Quantity.from_str("5"),
                    1,
                ),
                flags=0,
                sequence=1,
                ts_event=1_000,
                ts_init=1_200,
            ),
            OrderBookDelta(
                instrument_id=INSTRUMENT,
                action=BookAction.ADD,
                order=BookOrder(
                    NautilusOrderSide.SELL,
                    Price.from_str("101"),
                    Quantity.from_str("3"),
                    2,
                ),
                flags=128,
                sequence=2,
                ts_event=1_000,
                ts_init=1_200,
            ),
        ],
    )
    book = OrderBook(INSTRUMENT, BookType.L2_MBP)
    book.apply_deltas(deltas)
    return book, deltas


def _market(
    sequence: int,
    *,
    bid: str = "100",
    ask: str = "101",
    bid_size: str = "100",
    ask_size: str = "1",
) -> KernelMarketState:
    event_ts = 1_000_000_000 + sequence * 1_000
    return KernelMarketState(
        exchange_ts_ns=event_ts,
        book_exchange_ts_ns=event_ts,
        observed_ts_ns=event_ts + 100,
        sequence=sequence,
        bids=(KernelBookLevel(price=Decimal(bid), size=Decimal(bid_size)),),
        asks=(KernelBookLevel(price=Decimal(ask), size=Decimal(ask_size)),),
    )


def _feature_config(*, calibrated: bool = False) -> FeatureEngineConfig:
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
        inventory_limit_base=Decimal("0.1"),
        fill_model_calibrated=calibrated,
    )


def _artifacts(
    strategy: AvellanedaStoikovConfig | OrderFlowScalperConfig,
    *,
    calibrated: bool = False,
) -> LiveStrategyArtifacts:
    return LiveStrategyArtifacts(
        feature_config=_feature_config(calibrated=calibrated),
        strategy_config=strategy,
        feature_config_sha256="1" * 64,
        strategy_config_sha256="2" * 64,
        feature_config_path=Path("/features.toml"),
        strategy_config_path=Path("/strategy.toml"),
    )


def test_nautilus_live_assembler_preserves_l2_trade_causality() -> None:
    assembler = NautilusMarketStateAssembler(depth_levels=1)
    assembler.observe_trade(
        TradeTick(
            instrument_id=INSTRUMENT,
            price=Price.from_str("100"),
            size=Quantity.from_str("2"),
            aggressor_side=NautilusAggressorSide.SELLER,
            trade_id=TradeId("trade-1"),
            ts_event=900,
            ts_init=1_100,
        )
    )
    book, deltas = _book()

    state = assembler.observe_book(book, deltas)

    assert state.bids[0] == KernelBookLevel(price=Decimal("100"), size=Decimal("5"))
    assert state.asks[0] == KernelBookLevel(price=Decimal("101"), size=Decimal("3"))
    assert state.trades[0].aggressor.value == "seller"
    assert state.exchange_ts_ns == 1_000
    with pytest.raises(ValueError, match="strictly increasing"):
        assembler.observe_book(book, deltas)


def test_nautilus_live_assembler_fails_closed_on_trade_buffer_overflow() -> None:
    assembler = NautilusMarketStateAssembler(depth_levels=1, max_pending_trades=1)
    trade = TradeTick(
        instrument_id=INSTRUMENT,
        price=Price.from_str("100"),
        size=Quantity.from_str("2"),
        aggressor_side=NautilusAggressorSide.BUYER,
        trade_id=TradeId("trade-overflow"),
        ts_event=900,
        ts_init=1_100,
    )
    assembler.observe_trade(trade)
    with pytest.raises(ValueError, match="hard bound"):
        assembler.observe_trade(trade)


def test_scalper_pipeline_commits_only_dispatched_intents() -> None:
    strategy = OrderFlowScalperConfig(
        order_quantity_base=Decimal("0.001"),
        max_abs_inventory_base=Decimal("0.01"),
        imbalance_weight_bps=Decimal("20"),
        flow_weight_bps=Decimal("0"),
        momentum_weight=Decimal("0"),
        safety_margin_bps=Decimal("0.1"),
        maximum_spread_bps=Decimal("200"),
        signal_threshold_bps=Decimal("0.1"),
        cooldown_ns=0,
    )
    pipeline = LiveDecisionPipeline(_artifacts(strategy))
    first = pipeline.decide(
        _market(0),
        position_base=Decimal("0"),
        margin_utilization=Decimal("0"),
        funding_rate=Decimal("0"),
        estimated_taker_fee_bps=Decimal("0"),
        estimated_slippage_bps=Decimal("0"),
    )
    assert not first.transition.decision.submit
    second = pipeline.decide(
        _market(1),
        position_base=Decimal("0"),
        margin_utilization=Decimal("0"),
        funding_rate=Decimal("0"),
        estimated_taker_fee_bps=Decimal("0"),
        estimated_slippage_bps=Decimal("0"),
    )
    intent = second.transition.decision.submit[0]
    pipeline.commit(
        second,
        dispatched_intent_ids=set(),
        dispatched_cancel_ids=set(),
    )
    assert isinstance(pipeline.memory, ScalperMemory)
    assert pipeline.memory.last_order_ts_ns is None
    third = pipeline.decide(
        _market(2),
        position_base=Decimal("0"),
        margin_utilization=Decimal("0"),
        funding_rate=Decimal("0"),
        estimated_taker_fee_bps=Decimal("0"),
        estimated_slippage_bps=Decimal("0"),
    )
    accepted = third.transition.decision.submit[0]
    pipeline.commit(
        third,
        dispatched_intent_ids={accepted.intent_id},
        dispatched_cancel_ids=set(),
    )
    assert intent.side.value == "buy"
    assert isinstance(pipeline.memory, ScalperMemory)
    assert pipeline.memory.last_order_ts_ns == accepted.created_ts_ns


def test_market_maker_waits_for_authoritative_cancel_outcome() -> None:
    strategy = AvellanedaStoikovConfig(
        tick_size=Decimal("1"),
        order_quantity_base=Decimal("0.001"),
        max_abs_inventory_base=Decimal("0.01"),
        minimum_quote_lifetime_ns=0,
        quote_hysteresis_ticks=0,
        maximum_quote_spread_bps=Decimal("500"),
        minimum_fill_probability=Decimal("0"),
        require_calibrated_fill_model=True,
    )
    pipeline = LiveDecisionPipeline(_artifacts(strategy, calibrated=True))
    pipeline.decide(
        _market(0),
        position_base=Decimal("0"),
        margin_utilization=Decimal("0"),
        funding_rate=Decimal("0"),
        estimated_taker_fee_bps=Decimal("0"),
        estimated_slippage_bps=Decimal("0"),
    )
    quoted = pipeline.decide(
        _market(1),
        position_base=Decimal("0"),
        margin_utilization=Decimal("0"),
        funding_rate=Decimal("0"),
        estimated_taker_fee_bps=Decimal("0"),
        estimated_slippage_bps=Decimal("0"),
    )
    submitted = {intent.intent_id for intent in quoted.transition.decision.submit}
    pipeline.commit(quoted, dispatched_intent_ids=submitted, dispatched_cancel_ids=set())
    assert isinstance(pipeline.memory, MarketMakerMemory)
    active_bid = pipeline.memory.active_bid_intent_id
    assert active_bid is not None

    replacement = pipeline.decide(
        _market(2, bid="110", ask="111", bid_size="1", ask_size="100"),
        position_base=Decimal("0"),
        margin_utilization=Decimal("0"),
        funding_rate=Decimal("0"),
        estimated_taker_fee_bps=Decimal("0"),
        estimated_slippage_bps=Decimal("0"),
    )
    canceled = set(replacement.transition.decision.cancel_intent_ids)
    pipeline.commit(
        replacement,
        dispatched_intent_ids=set(),
        dispatched_cancel_ids=canceled,
    )
    assert pipeline.memory.active_bid_intent_id == active_bid
    pipeline.release_intent(active_bid)
    assert isinstance(pipeline.memory, MarketMakerMemory)
    assert pipeline.memory.active_bid_intent_id is None


def test_equity_baselines_survive_restart_and_reset_only_daily_loss(tmp_path: Path) -> None:
    path = (tmp_path / "equity.json").resolve()
    store = EquityBaselineStore(path, account_address=ACCOUNT)
    start = int(datetime(2026, 8, 5, 1, tzinfo=UTC).timestamp() * 1_000_000_000)
    assert store.observe(Decimal("100"), now_ns=start).day_start_equity_usd == 100
    assert store.observe(Decimal("120"), now_ns=start + 1).high_water_equity_usd == 120

    restarted = EquityBaselineStore(path, account_address=ACCOUNT)
    loss = restarted.observe(Decimal("90"), now_ns=start + 2)
    assert loss.day_start_equity_usd == 100
    assert loss.high_water_equity_usd == 120
    next_day = restarted.observe(
        Decimal("80"), now_ns=start + int(timedelta(days=1).total_seconds() * 1_000_000_000)
    )
    assert next_day.day_start_equity_usd == 80
    assert next_day.high_water_equity_usd == 120
    with pytest.raises(ValueError, match="different execution account"):
        EquityBaselineStore(path, account_address="0x" + "2" * 40)
    with pytest.raises(ValueError, match="absolute"):
        EquityBaselineStore(Path("relative.json"), account_address=ACCOUNT)
    with pytest.raises(ValueError, match="positive"):
        restarted.observe(Decimal("0"), now_ns=start + 3)
    with pytest.raises(ValueError, match="clock moved"):
        restarted.observe(Decimal("80"), now_ns=start)


def test_live_risk_snapshot_uses_reconciled_pending_exposure() -> None:
    snapshot = build_live_risk_snapshot(
        now_ns=2_000,
        public_data_ts_ns=1_900,
        mark_price=Decimal("100000"),
        account=LiveAccountState(
            equity_usd=Decimal("10000"),
            position_base=Decimal("0.001"),
            pending_buy_base=Decimal("0.002"),
            pending_sell_base=Decimal("0.003"),
            open_order_count=2,
        ),
        baseline=EquityBaseline(
            account_address=ACCOUNT,
            broker_day_utc="2026-08-05",
            day_start_equity_usd=Decimal("11000"),
            high_water_equity_usd=Decimal("12000"),
            updated_ts_ns=1_800,
        ),
        exchange_connected=True,
        reconciliation_complete=True,
    )
    assert snapshot.pending_buy_base == Decimal("0.002")
    assert snapshot.pending_sell_base == Decimal("0.003")
    assert snapshot.leverage == Decimal("0.01")
    assert snapshot.private_data_ts_ns == 2_000


def test_live_account_view_uses_portfolio_equity_and_order_leaves() -> None:
    portfolio = SimpleNamespace(
        account=lambda venue: SimpleNamespace(base_currency="USDC"),
        equity=lambda venue: {"USDC": SimpleNamespace(as_decimal=lambda: Decimal("10000"))},
    )
    cache = SimpleNamespace(
        positions_open=lambda **kwargs: [
            SimpleNamespace(
                signed_decimal_qty=lambda: Decimal("-0.002"),
                avg_px_open=Decimal("101000"),
                ts_opened=1_800_000_000_000_000_000,
            )
        ],
        orders_open=lambda **kwargs: [
            SimpleNamespace(
                side=NautilusOrderSide.BUY,
                leaves_qty=SimpleNamespace(as_decimal=lambda: Decimal("0.003")),
            ),
            SimpleNamespace(
                side=NautilusOrderSide.SELL,
                leaves_qty=SimpleNamespace(as_decimal=lambda: Decimal("0.004")),
            ),
        ],
    )

    state = read_live_account_state(portfolio, cache)

    assert state.equity_usd == 10000
    assert state.position_base == Decimal("-0.002")
    assert state.pending_buy_base == Decimal("0.003")
    assert state.pending_sell_base == Decimal("0.004")
    assert state.open_order_count == 2
    assert state.average_entry_price == Decimal("101000")
    assert state.position_opened_ts_ns == 1_800_000_000_000_000_000

    with pytest.raises(ValueError, match="account is unavailable"):
        read_live_account_state(SimpleNamespace(account=lambda venue: None), cache)
    ambiguous = SimpleNamespace(
        account=lambda venue: SimpleNamespace(base_currency=None),
        equity=lambda venue: {},
    )
    with pytest.raises(ValueError, match="missing or ambiguous"):
        read_live_account_state(ambiguous, cache)


def test_live_artifacts_are_strict_and_can_consume_approved_strategy_bytes(
    config_dir: Path,
    tmp_path: Path,
) -> None:
    bundle = load_config(
        config_dir,
        "testnet",
        environ={
            "AQT_NATIVE__EXCHANGE__ACCOUNT_ADDRESS": ACCOUNT,
            "AQT_NATIVE__EXCHANGE__TRADING_WALLET_SECRET_PATH": (
                "/run/secrets/testnet-trading-wallet"
            ),
            "AQT_NATIVE__EXCHANGE__CONTROL_WALLET_SECRET_PATH": (
                "/run/secrets/testnet-control-wallet"
            ),
            "AQT_NATIVE__EXECUTION__ENABLED": "true",
            "AQT_NATIVE__LIVE_STRATEGY__ENABLED": "true",
            "AQT_NATIVE__SENTINEL__ENABLED": "true",
        },
    )
    approved = tmp_path / "approved-strategy.toml"
    approved.write_bytes((config_dir / "strategies/order-flow-scalper-v1.toml").read_bytes())
    artifacts = load_live_strategy_artifacts(
        config_dir,
        bundle,
        approved_strategy_path=approved,
    )
    assert artifacts.strategy_config_path == approved.resolve()
    assert artifacts.strategy_config.strategy_id == "order-flow-scalper-v1"

    approved.write_text(
        approved.read_text().replace(
            'order_quantity_base = "0.001"',
            'order_quantity_base = "1"',
        )
    )
    with pytest.raises(ValueError, match="hard order-size limit"):
        load_live_strategy_artifacts(config_dir, bundle, approved_strategy_path=approved)

    approved.write_bytes((config_dir / "strategies/order-flow-scalper-v1.toml").read_bytes())
    approved.write_text(
        approved.read_text().replace(
            'max_abs_inventory_base = "0.01"',
            'max_abs_inventory_base = "0.1"',
        )
    )
    with pytest.raises(ValueError, match="hard position limit"):
        load_live_strategy_artifacts(config_dir, bundle, approved_strategy_path=approved)

    approved.write_text("not = [valid")
    with pytest.raises(ValueError, match="invalid live TOML"):
        load_live_strategy_artifacts(config_dir, bundle, approved_strategy_path=approved)

    approved.write_bytes(b"x" * 1_048_577)
    with pytest.raises(ValueError, match="bounded regular file"):
        load_live_strategy_artifacts(config_dir, bundle, approved_strategy_path=approved)

    disabled = load_config(config_dir, "testnet", environ={})
    with pytest.raises(ValueError, match="enabled execution and strategy"):
        load_live_strategy_artifacts(config_dir, disabled)

    market_maker_bundle = load_config(
        config_dir,
        "testnet",
        environ={
            "AQT_NATIVE__EXCHANGE__ACCOUNT_ADDRESS": ACCOUNT,
            "AQT_NATIVE__EXCHANGE__TRADING_WALLET_SECRET_PATH": (
                "/run/secrets/testnet-trading-wallet"
            ),
            "AQT_NATIVE__EXCHANGE__CONTROL_WALLET_SECRET_PATH": (
                "/run/secrets/testnet-control-wallet"
            ),
            "AQT_NATIVE__EXECUTION__ENABLED": "true",
            "AQT_NATIVE__LIVE_STRATEGY__ENABLED": "true",
            "AQT_NATIVE__LIVE_STRATEGY__STRATEGY_ID": "avellaneda-stoikov-v1",
            "AQT_NATIVE__LIVE_STRATEGY__STRATEGY_CONFIG_PATH": (
                "strategies/avellaneda-stoikov-v1.toml"
            ),
            "AQT_NATIVE__SENTINEL__ENABLED": "true",
            "AQT_NATIVE__RISK__MAX_POSITION_SIZE_BASE": "0.1",
            "AQT_NATIVE__RISK__MAX_OPEN_ORDERS": "1",
        },
    )
    with pytest.raises(ValueError, match="one bid and one ask"):
        load_live_strategy_artifacts(config_dir, market_maker_bundle)
