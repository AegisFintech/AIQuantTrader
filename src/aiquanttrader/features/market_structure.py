"""Causal multi-timeframe candles, support/resistance, and SMC state."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from aiquanttrader.backtest.kernel import KernelMarketState
from aiquanttrader.domain.base import DomainModel
from aiquanttrader.domain.market import AggressorSide

NANOSECONDS_PER_SECOND = 1_000_000_000
TIMEFRAMES_SECONDS: tuple[Literal[60], Literal[300], Literal[900]] = (60, 300, 900)


class StructureDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class DealingRangeZone(StrEnum):
    PREMIUM = "premium"
    DISCOUNT = "discount"
    EQUILIBRIUM = "equilibrium"
    UNKNOWN = "unknown"


class CausalCandle(DomainModel):
    """A locally observed candle; only completed candles drive signals."""

    timeframe_seconds: Literal[60, 300, 900]
    open_ts_ns: int = Field(ge=0)
    close_ts_ns: int = Field(gt=0)
    open: Annotated[Decimal, Field(gt=0)]
    high: Annotated[Decimal, Field(gt=0)]
    low: Annotated[Decimal, Field(gt=0)]
    close: Annotated[Decimal, Field(gt=0)]
    volume: Annotated[Decimal, Field(ge=0)] = Decimal("0")
    signed_volume: Decimal = Decimal("0")
    observation_count: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_ohlc(self) -> Self:
        if self.close_ts_ns <= self.open_ts_ns:
            raise ValueError("candle close must follow its open")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("candle high/low must contain open and close")
        if self.low > self.high:
            raise ValueError("candle low cannot exceed high")
        return self


class TimeframeStructure(DomainModel):
    timeframe_seconds: Literal[60, 300, 900]
    closed_bars: int = Field(ge=0)
    last_closed_ts_ns: int | None = Field(default=None, ge=0)
    close: Decimal | None = Field(default=None, gt=0)
    atr_bps: Annotated[Decimal, Field(ge=0)] = Decimal("0")
    direction: StructureDirection = StructureDirection.NEUTRAL
    zone: DealingRangeZone = DealingRangeZone.UNKNOWN
    support: Decimal | None = Field(default=None, gt=0)
    resistance: Decimal | None = Field(default=None, gt=0)
    bullish_bos: bool = False
    bearish_bos: bool = False
    bullish_choch: bool = False
    bearish_choch: bool = False
    bullish_sweep: bool = False
    bearish_sweep: bool = False
    bullish_fvg_lower: Decimal | None = Field(default=None, gt=0)
    bullish_fvg_upper: Decimal | None = Field(default=None, gt=0)
    bearish_fvg_lower: Decimal | None = Field(default=None, gt=0)
    bearish_fvg_upper: Decimal | None = Field(default=None, gt=0)
    bullish_order_block_low: Decimal | None = Field(default=None, gt=0)
    bullish_order_block_high: Decimal | None = Field(default=None, gt=0)
    bearish_order_block_low: Decimal | None = Field(default=None, gt=0)
    bearish_order_block_high: Decimal | None = Field(default=None, gt=0)


class SmartMoneySnapshot(DomainModel):
    schema_version: Literal[1] = 1
    observed_ts_ns: int = Field(ge=0)
    revision: int = Field(ge=0)
    ready: bool
    one_minute: TimeframeStructure
    five_minute: TimeframeStructure
    fifteen_minute: TimeframeStructure
    long_confluence: int = Field(ge=0, le=20)
    short_confluence: int = Field(ge=0, le=20)
    long_reasons: tuple[str, ...] = ()
    short_reasons: tuple[str, ...] = ()

    @property
    def directional_bias(self) -> StructureDirection:
        return self.fifteen_minute.direction


class StructureEngineConfig(DomainModel):
    schema_version: Literal[1] = 1
    pivot_span: int = Field(default=2, ge=1, le=5)
    maximum_closed_bars: int = Field(default=256, ge=64, le=2_048)
    minimum_1m_bars: int = Field(default=8, ge=5, le=100)
    minimum_5m_bars: int = Field(default=6, ge=5, le=100)
    minimum_15m_bars: int = Field(default=4, ge=3, le=100)
    near_level_bps: Annotated[Decimal, Field(gt=0, le=100)] = Decimal("12")
    equilibrium_band_fraction: Annotated[Decimal, Field(ge=0, le=Decimal("0.25"))] = Decimal("0.05")


class TimeframeBarState(DomainModel):
    timeframe_seconds: Literal[60, 300, 900]
    current: CausalCandle | None = None
    current_valid: bool = True
    closed: tuple[CausalCandle, ...] = ()


class CausalStructureState(DomainModel):
    schema_version: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    last_observed_ts_ns: int | None = Field(default=None, ge=0)
    timeframes: tuple[TimeframeBarState, ...] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_timeframes(self) -> Self:
        values = tuple(item.timeframe_seconds for item in self.timeframes)
        if values != TIMEFRAMES_SECONDS:
            raise ValueError("structure state requires ordered 1m, 5m, and 15m timeframes")
        return self


class CausalMarketStructureEngine:
    """Build non-repainting SMC features from locally observed, closed bars."""

    def __init__(
        self,
        config: StructureEngineConfig | None = None,
        *,
        restored_state: CausalStructureState | None = None,
    ) -> None:
        self.config = config or StructureEngineConfig()
        if restored_state is None:
            self._state = CausalStructureState(
                timeframes=tuple(
                    TimeframeBarState(timeframe_seconds=value) for value in TIMEFRAMES_SECONDS
                )
            )
        else:
            # A crash can truncate the current candle. Preserve completed bars and
            # quarantine every partial candle until its bucket rolls over.
            self._state = restored_state.model_copy(
                update={
                    "timeframes": tuple(
                        item.model_copy(update={"current_valid": False})
                        for item in restored_state.timeframes
                    )
                }
            )

    @property
    def state(self) -> CausalStructureState:
        return self._state

    @property
    def ready(self) -> bool:
        by_timeframe = {item.timeframe_seconds: item for item in self._state.timeframes}
        return (
            len(by_timeframe[60].closed) >= self.config.minimum_1m_bars
            and len(by_timeframe[300].closed) >= self.config.minimum_5m_bars
            and len(by_timeframe[900].closed) >= self.config.minimum_15m_bars
        )

    def update(self, market: KernelMarketState) -> SmartMoneySnapshot:
        previous_ts = self._state.last_observed_ts_ns
        if previous_ts is not None and market.observed_ts_ns <= previous_ts:
            raise ValueError("market-structure observations must be strictly increasing")
        mid = (market.bids[0].price + market.asks[0].price) / Decimal("2")
        prices = [mid, *(trade.price for trade in market.trades)]
        volume = sum((trade.size for trade in market.trades), Decimal("0"))
        signed_volume = sum(
            (
                trade.size
                if trade.aggressor is AggressorSide.BUYER
                else -trade.size
                if trade.aggressor is AggressorSide.SELLER
                else Decimal("0")
                for trade in market.trades
            ),
            Decimal("0"),
        )
        next_states: list[TimeframeBarState] = []
        closed_any = False
        for timeframe in self._state.timeframes:
            updated, closed = self._update_timeframe(
                timeframe,
                observed_ts_ns=market.observed_ts_ns,
                prices=prices,
                volume=volume,
                signed_volume=signed_volume,
            )
            next_states.append(updated)
            closed_any = closed_any or closed
        revision = self._state.revision + (1 if closed_any else 0)
        self._state = CausalStructureState(
            revision=revision,
            last_observed_ts_ns=market.observed_ts_ns,
            timeframes=tuple(next_states),
        )
        return self.snapshot(market.observed_ts_ns)

    def snapshot(self, observed_ts_ns: int) -> SmartMoneySnapshot:
        by_timeframe = {item.timeframe_seconds: item.closed for item in self._state.timeframes}
        one = self._analyze(60, by_timeframe[60])
        five = self._analyze(300, by_timeframe[300])
        fifteen = self._analyze(900, by_timeframe[900])
        long_score, long_reasons = self._score(StructureDirection.BULLISH, one, five, fifteen)
        short_score, short_reasons = self._score(StructureDirection.BEARISH, one, five, fifteen)
        return SmartMoneySnapshot(
            observed_ts_ns=observed_ts_ns,
            revision=self._state.revision,
            ready=self.ready,
            one_minute=one,
            five_minute=five,
            fifteen_minute=fifteen,
            long_confluence=long_score,
            short_confluence=short_score,
            long_reasons=long_reasons,
            short_reasons=short_reasons,
        )

    def _update_timeframe(
        self,
        state: TimeframeBarState,
        *,
        observed_ts_ns: int,
        prices: list[Decimal],
        volume: Decimal,
        signed_volume: Decimal,
    ) -> tuple[TimeframeBarState, bool]:
        duration = state.timeframe_seconds * NANOSECONDS_PER_SECOND
        bucket = observed_ts_ns // duration * duration
        current = state.current
        closed = list(state.closed)
        closed_bar = False
        observation_high = max(prices)
        observation_low = min(prices)
        observation_close = prices[-1]
        if current is None or current.open_ts_ns != bucket:
            if current is not None and state.current_valid:
                closed.append(current)
                closed = closed[-self.config.maximum_closed_bars :]
                closed_bar = True
            current = CausalCandle(
                timeframe_seconds=state.timeframe_seconds,
                open_ts_ns=bucket,
                close_ts_ns=bucket + duration,
                open=prices[0],
                high=observation_high,
                low=observation_low,
                close=observation_close,
                volume=volume,
                signed_volume=signed_volume,
            )
            current_valid = True
        else:
            current = current.model_copy(
                update={
                    "high": max(current.high, observation_high),
                    "low": min(current.low, observation_low),
                    "close": observation_close,
                    "volume": current.volume + volume,
                    "signed_volume": current.signed_volume + signed_volume,
                    "observation_count": current.observation_count + 1,
                }
            )
            current_valid = state.current_valid
        return (
            TimeframeBarState(
                timeframe_seconds=state.timeframe_seconds,
                current=current,
                current_valid=current_valid,
                closed=tuple(closed),
            ),
            closed_bar,
        )

    def _analyze(
        self, timeframe_seconds: Literal[60, 300, 900], bars: tuple[CausalCandle, ...]
    ) -> TimeframeStructure:
        if not bars:
            return TimeframeStructure(timeframe_seconds=timeframe_seconds, closed_bars=0)
        latest = bars[-1]
        prior = bars[:-1]
        swing_highs, swing_lows = self._confirmed_pivots(prior)
        resistance = (
            swing_highs[-1].high
            if swing_highs
            else max((bar.high for bar in prior[-10:]), default=None)
        )
        support = (
            swing_lows[-1].low
            if swing_lows
            else min((bar.low for bar in prior[-10:]), default=None)
        )
        direction = self._direction(prior, swing_highs, swing_lows)
        bullish_bos = resistance is not None and latest.close > resistance
        bearish_bos = support is not None and latest.close < support
        bullish_sweep = support is not None and latest.low < support <= latest.close
        bearish_sweep = resistance is not None and latest.high > resistance >= latest.close
        bullish_choch = bullish_bos and direction is StructureDirection.BEARISH
        bearish_choch = bearish_bos and direction is StructureDirection.BULLISH
        zone = self._zone(latest.close, support, resistance)
        atr_bps = self._atr_bps(bars)

        bullish_fvg_lower: Decimal | None = None
        bullish_fvg_upper: Decimal | None = None
        bearish_fvg_lower: Decimal | None = None
        bearish_fvg_upper: Decimal | None = None
        if len(bars) >= 3:
            first = bars[-3]
            if latest.low > first.high:
                bullish_fvg_lower, bullish_fvg_upper = first.high, latest.low
            if latest.high < first.low:
                bearish_fvg_lower, bearish_fvg_upper = latest.high, first.low

        bullish_ob_low: Decimal | None = None
        bullish_ob_high: Decimal | None = None
        bearish_ob_low: Decimal | None = None
        bearish_ob_high: Decimal | None = None
        if bullish_bos:
            opposite = next((bar for bar in reversed(prior[-8:]) if bar.close < bar.open), None)
            if opposite is not None:
                bullish_ob_low, bullish_ob_high = opposite.low, opposite.high
        if bearish_bos:
            opposite = next((bar for bar in reversed(prior[-8:]) if bar.close > bar.open), None)
            if opposite is not None:
                bearish_ob_low, bearish_ob_high = opposite.low, opposite.high

        return TimeframeStructure(
            timeframe_seconds=timeframe_seconds,
            closed_bars=len(bars),
            last_closed_ts_ns=latest.close_ts_ns,
            close=latest.close,
            atr_bps=atr_bps,
            direction=direction,
            zone=zone,
            support=support,
            resistance=resistance,
            bullish_bos=bullish_bos,
            bearish_bos=bearish_bos,
            bullish_choch=bullish_choch,
            bearish_choch=bearish_choch,
            bullish_sweep=bullish_sweep,
            bearish_sweep=bearish_sweep,
            bullish_fvg_lower=bullish_fvg_lower,
            bullish_fvg_upper=bullish_fvg_upper,
            bearish_fvg_lower=bearish_fvg_lower,
            bearish_fvg_upper=bearish_fvg_upper,
            bullish_order_block_low=bullish_ob_low,
            bullish_order_block_high=bullish_ob_high,
            bearish_order_block_low=bearish_ob_low,
            bearish_order_block_high=bearish_ob_high,
        )

    def _confirmed_pivots(
        self, bars: tuple[CausalCandle, ...]
    ) -> tuple[list[CausalCandle], list[CausalCandle]]:
        span = self.config.pivot_span
        highs: list[CausalCandle] = []
        lows: list[CausalCandle] = []
        for index in range(span, len(bars) - span):
            candidate = bars[index]
            neighbors = bars[index - span : index] + bars[index + 1 : index + span + 1]
            if all(candidate.high > bar.high for bar in neighbors):
                highs.append(candidate)
            if all(candidate.low < bar.low for bar in neighbors):
                lows.append(candidate)
        return highs, lows

    @staticmethod
    def _direction(
        bars: tuple[CausalCandle, ...],
        swing_highs: list[CausalCandle],
        swing_lows: list[CausalCandle],
    ) -> StructureDirection:
        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            higher = (
                swing_highs[-1].high > swing_highs[-2].high
                and swing_lows[-1].low > swing_lows[-2].low
            )
            lower = (
                swing_highs[-1].high < swing_highs[-2].high
                and swing_lows[-1].low < swing_lows[-2].low
            )
            if higher:
                return StructureDirection.BULLISH
            if lower:
                return StructureDirection.BEARISH
        if len(bars) >= 3:
            if bars[-1].close > bars[-3].close:
                return StructureDirection.BULLISH
            if bars[-1].close < bars[-3].close:
                return StructureDirection.BEARISH
        return StructureDirection.NEUTRAL

    def _zone(
        self, close: Decimal, support: Decimal | None, resistance: Decimal | None
    ) -> DealingRangeZone:
        if support is None or resistance is None or resistance <= support:
            return DealingRangeZone.UNKNOWN
        equilibrium = (support + resistance) / Decimal("2")
        band = (resistance - support) * self.config.equilibrium_band_fraction
        if close < equilibrium - band:
            return DealingRangeZone.DISCOUNT
        if close > equilibrium + band:
            return DealingRangeZone.PREMIUM
        return DealingRangeZone.EQUILIBRIUM

    @staticmethod
    def _atr_bps(bars: tuple[CausalCandle, ...]) -> Decimal:
        window = bars[-15:]
        if not window:
            return Decimal("0")
        ranges: list[Decimal] = []
        previous_close: Decimal | None = None
        for bar in window:
            true_range = bar.high - bar.low
            if previous_close is not None:
                true_range = max(
                    true_range,
                    abs(bar.high - previous_close),
                    abs(bar.low - previous_close),
                )
            ranges.append(true_range)
            previous_close = bar.close
        average_range = sum(ranges, Decimal("0")) / Decimal(len(ranges))
        return average_range / window[-1].close * Decimal("10000")

    def _near(self, price: Decimal | None, level: Decimal | None) -> bool:
        if price is None or level is None:
            return False
        return abs(price - level) / price * Decimal("10000") <= self.config.near_level_bps

    def _score(
        self,
        side: StructureDirection,
        one: TimeframeStructure,
        five: TimeframeStructure,
        fifteen: TimeframeStructure,
    ) -> tuple[int, tuple[str, ...]]:
        bullish = side is StructureDirection.BULLISH
        score = 0
        reasons: list[str] = []

        def add(points: int, reason: str) -> None:
            nonlocal score
            score += points
            reasons.append(reason)

        if fifteen.direction is side:
            add(3, "15m_bias")
        if five.direction is side:
            add(1, "5m_structure")
        if five.bullish_sweep if bullish else five.bearish_sweep:
            add(2, "5m_liquidity_sweep")
        if five.bullish_bos if bullish else five.bearish_bos:
            add(1, "5m_bos")
        desired_zone = DealingRangeZone.DISCOUNT if bullish else DealingRangeZone.PREMIUM
        if five.zone is desired_zone:
            add(1, "5m_dealing_range")
        level = five.support if bullish else five.resistance
        if self._near(five.close, level):
            add(1, "5m_support_resistance")
        if one.bullish_sweep if bullish else one.bearish_sweep:
            add(2, "1m_liquidity_sweep")
        if one.bullish_choch if bullish else one.bearish_choch:
            add(2, "1m_choch")
        elif one.bullish_bos if bullish else one.bearish_bos:
            add(1, "1m_bos")
        if one.direction is side:
            add(1, "1m_momentum")
        has_fvg = (
            one.bullish_fvg_lower is not None if bullish else one.bearish_fvg_lower is not None
        )
        if has_fvg:
            add(1, "1m_fvg")
        return min(score, 20), tuple(reasons)
