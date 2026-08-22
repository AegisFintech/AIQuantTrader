from __future__ import annotations

from decimal import Decimal
from typing import Literal

import pytest
from pydantic import ValidationError

from aiquanttrader.backtest.kernel import KernelBookLevel, KernelMarketState, KernelTrade
from aiquanttrader.domain.market import AggressorSide
from aiquanttrader.features.market_structure import (
    CausalCandle,
    CausalMarketStructureEngine,
    CausalStructureState,
    DealingRangeZone,
    StructureDirection,
    StructureEngineConfig,
    TimeframeBarState,
    TimeframeStructure,
)

MINUTE_NS = 60_000_000_000


def _candle(
    index: int,
    *,
    open_: str,
    high: str,
    low: str,
    close: str,
    timeframe: Literal[60, 300, 900] = 60,
) -> CausalCandle:
    duration = timeframe * 1_000_000_000
    opened = index * duration
    return CausalCandle(
        timeframe_seconds=timeframe,
        open_ts_ns=opened,
        close_ts_ns=opened + duration,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
    )


def _market(
    sequence: int,
    price: str,
    *,
    offset_ns: int = 1,
    trades: tuple[KernelTrade, ...] = (),
) -> KernelMarketState:
    observed = (sequence + 1) * MINUTE_NS + offset_ns
    mid = Decimal(price)
    return KernelMarketState(
        exchange_ts_ns=observed,
        book_exchange_ts_ns=observed,
        observed_ts_ns=observed,
        sequence=sequence,
        bids=(KernelBookLevel(price=mid - Decimal("0.5"), size=Decimal("2")),),
        asks=(KernelBookLevel(price=mid + Decimal("0.5"), size=Decimal("1")),),
        trades=trades,
    )


def _structure(
    timeframe: Literal[60, 300, 900],
    direction: StructureDirection,
    *,
    zone: DealingRangeZone,
    bullish: bool,
) -> TimeframeStructure:
    return TimeframeStructure(
        timeframe_seconds=timeframe,
        closed_bars=20,
        last_closed_ts_ns=1,
        close=Decimal("100"),
        direction=direction,
        zone=zone,
        support=Decimal("99.95"),
        resistance=Decimal("100.05"),
        bullish_bos=bullish,
        bearish_bos=not bullish,
        bullish_choch=bullish,
        bearish_choch=not bullish,
        bullish_sweep=bullish,
        bearish_sweep=not bullish,
        bullish_fvg_lower=Decimal("99") if bullish else None,
        bearish_fvg_lower=Decimal("101") if not bullish else None,
    )


def test_candle_and_structure_state_validation_fail_closed() -> None:
    with pytest.raises(ValidationError, match="close must follow"):
        _candle(0, open_="100", high="101", low="99", close="100").model_copy(
            update={"close_ts_ns": 0}
        ).model_validate(
            {
                "timeframe_seconds": 60,
                "open_ts_ns": 1,
                "close_ts_ns": 1,
                "open": "100",
                "high": "101",
                "low": "99",
                "close": "100",
            }
        )
    with pytest.raises(ValidationError, match="contain open and close"):
        CausalCandle(
            timeframe_seconds=60,
            open_ts_ns=0,
            close_ts_ns=1,
            open=Decimal("100"),
            high=Decimal("99"),
            low=Decimal("98"),
            close=Decimal("100"),
        )
    with pytest.raises(ValidationError, match="ordered 1m, 5m, and 15m"):
        CausalStructureState(
            timeframes=(
                TimeframeBarState(timeframe_seconds=300),
                TimeframeBarState(timeframe_seconds=60),
                TimeframeBarState(timeframe_seconds=900),
            )
        )


def test_closed_bar_engine_accumulates_trades_truncates_and_restores_fail_closed() -> None:
    engine = CausalMarketStructureEngine(
        StructureEngineConfig(
            pivot_span=1,
            maximum_closed_bars=64,
            minimum_1m_bars=5,
            minimum_5m_bars=5,
            minimum_15m_bars=3,
        )
    )
    first_ts = MINUTE_NS + 1
    trades = (
        KernelTrade(
            exchange_ts_ns=first_ts,
            observed_ts_ns=first_ts,
            price=Decimal("101"),
            size=Decimal("2"),
            aggressor=AggressorSide.BUYER,
        ),
        KernelTrade(
            exchange_ts_ns=first_ts,
            observed_ts_ns=first_ts,
            price=Decimal("99"),
            size=Decimal("1"),
            aggressor=AggressorSide.SELLER,
        ),
        KernelTrade(
            exchange_ts_ns=first_ts,
            observed_ts_ns=first_ts,
            price=Decimal("100"),
            size=Decimal("3"),
            aggressor=AggressorSide.UNKNOWN,
        ),
    )
    engine.update(_market(0, "100", trades=trades))
    current = engine.state.timeframes[0].current
    assert current is not None
    assert current.high == Decimal("101")
    assert current.low == Decimal("99")
    assert current.volume == Decimal("6")
    assert current.signed_volume == Decimal("1")

    engine.update(_market(0, "102", offset_ns=2))
    current = engine.state.timeframes[0].current
    assert current is not None and current.observation_count == 2
    assert current.close == Decimal("102")

    latest = None
    for sequence in range(1, 80):
        latest = engine.update(_market(sequence, str(100 + sequence)))
    assert latest is not None and latest.ready
    assert latest.directional_bias is StructureDirection.BULLISH
    assert len(engine.state.timeframes[0].closed) == 64
    with pytest.raises(ValueError, match="strictly increasing"):
        engine.update(_market(79, "200"))

    restored = CausalMarketStructureEngine(engine.config, restored_state=engine.state)
    assert all(not timeframe.current_valid for timeframe in restored.state.timeframes)
    before = restored.state.revision
    restored.update(_market(80, "200", offset_ns=20_000_000_000))
    assert restored.state.revision == before


def test_analysis_detects_breaks_sweeps_gaps_order_blocks_and_atr() -> None:
    engine = CausalMarketStructureEngine(StructureEngineConfig(pivot_span=1))
    bullish = (
        _candle(0, open_="100", high="102", low="99", close="101"),
        _candle(1, open_="101", high="102", low="99", close="100"),
        _candle(2, open_="100", high="103", low="100", close="101"),
        _candle(3, open_="101", high="104", low="101", close="102"),
        _candle(4, open_="105", high="111", low="105", close="110"),
    )
    result = engine._analyze(60, bullish)
    assert result.direction is StructureDirection.BULLISH
    assert result.bullish_bos
    assert result.bullish_fvg_lower == Decimal("103")
    assert result.bullish_order_block_low == Decimal("99")
    assert result.atr_bps > 0

    bearish = (
        _candle(0, open_="110", high="111", low="108", close="109"),
        _candle(1, open_="109", high="111", low="108", close="110"),
        _candle(2, open_="110", high="110", low="107", close="109"),
        _candle(3, open_="109", high="109", low="106", close="108"),
        _candle(4, open_="105", high="105", low="99", close="100"),
    )
    result = engine._analyze(60, bearish)
    assert result.direction is StructureDirection.BEARISH
    assert result.bearish_bos
    assert result.bearish_fvg_upper == Decimal("107")
    assert result.bearish_order_block_high == Decimal("111")

    sweep = (*bullish[:-1], _candle(4, open_="100", high="105", low="98", close="100"))
    result = engine._analyze(60, sweep)
    assert result.bullish_sweep and result.bearish_sweep
    assert engine._analyze(300, ()).closed_bars == 0
    assert engine._atr_bps(()) == 0


def test_pivots_direction_zones_nearness_and_confluence_are_explicit() -> None:
    engine = CausalMarketStructureEngine(StructureEngineConfig(pivot_span=1))
    bars = tuple(
        _candle(
            index,
            open_="5",
            high=str(high),
            low=str(low),
            close="5",
        )
        for index, (high, low) in enumerate(((6, 4), (7, 3), (10, 1), (7, 3), (6, 4)))
    )
    highs, lows = engine._confirmed_pivots(bars)
    assert [item.high for item in highs] == [Decimal("10")]
    assert [item.low for item in lows] == [Decimal("1")]

    higher_highs = [
        _candle(0, open_="5", high="6", low="4", close="5"),
        _candle(1, open_="6", high="7", low="5", close="6"),
    ]
    higher_lows = [
        _candle(0, open_="4", high="5", low="3", close="4"),
        _candle(1, open_="5", high="6", low="4", close="5"),
    ]
    assert engine._direction((), higher_highs, higher_lows) is StructureDirection.BULLISH
    assert engine._direction((), list(reversed(higher_highs)), list(reversed(higher_lows))) is (
        StructureDirection.BEARISH
    )
    flat = (
        _candle(0, open_="5", high="6", low="4", close="5"),
        _candle(1, open_="5", high="6", low="4", close="5"),
        _candle(2, open_="5", high="6", low="4", close="5"),
    )
    assert engine._direction(flat, [], []) is StructureDirection.NEUTRAL
    assert engine._direction(
        (*flat[:2], flat[2].model_copy(update={"close": Decimal("5.5")})), [], []
    ) is (StructureDirection.BULLISH)
    assert engine._direction(
        (*flat[:2], flat[2].model_copy(update={"close": Decimal("4.5")})), [], []
    ) is (StructureDirection.BEARISH)

    assert engine._zone(Decimal("100"), None, Decimal("101")) is DealingRangeZone.UNKNOWN
    assert engine._zone(Decimal("100"), Decimal("101"), Decimal("100")) is DealingRangeZone.UNKNOWN
    assert engine._zone(Decimal("101"), Decimal("100"), Decimal("110")) is DealingRangeZone.DISCOUNT
    assert engine._zone(Decimal("109"), Decimal("100"), Decimal("110")) is DealingRangeZone.PREMIUM
    assert (
        engine._zone(Decimal("105"), Decimal("100"), Decimal("110")) is DealingRangeZone.EQUILIBRIUM
    )
    assert not engine._near(None, Decimal("100"))
    assert engine._near(Decimal("100"), Decimal("100.05"))

    bullish_one = _structure(
        60, StructureDirection.BULLISH, zone=DealingRangeZone.DISCOUNT, bullish=True
    )
    bullish_five = _structure(
        300, StructureDirection.BULLISH, zone=DealingRangeZone.DISCOUNT, bullish=True
    )
    bullish_fifteen = _structure(
        900, StructureDirection.BULLISH, zone=DealingRangeZone.DISCOUNT, bullish=True
    )
    score, reasons = engine._score(
        StructureDirection.BULLISH,
        bullish_one,
        bullish_five,
        bullish_fifteen,
    )
    assert score >= 14
    assert "1m_choch" in reasons and "1m_fvg" in reasons

    bearish_one = _structure(
        60, StructureDirection.BEARISH, zone=DealingRangeZone.PREMIUM, bullish=False
    )
    bearish_five = _structure(
        300, StructureDirection.BEARISH, zone=DealingRangeZone.PREMIUM, bullish=False
    )
    bearish_fifteen = _structure(
        900, StructureDirection.BEARISH, zone=DealingRangeZone.PREMIUM, bullish=False
    )
    score, reasons = engine._score(
        StructureDirection.BEARISH,
        bearish_one,
        bearish_five,
        bearish_fifteen,
    )
    assert score >= 14
    assert "5m_dealing_range" in reasons and "1m_fvg" in reasons
