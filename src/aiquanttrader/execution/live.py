"""Causal live market normalization, strategy coordination, and equity baselines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas, TradeTick
from nautilus_trader.model.enums import AggressorSide as NautilusAggressorSide
from nautilus_trader.model.enums import OrderSide as NautilusOrderSide
from nautilus_trader.model.identifiers import InstrumentId, Venue
from pydantic import Field, model_validator

from aiquanttrader.backtest.kernel import (
    KernelBookLevel,
    KernelMarketState,
    KernelTrade,
)
from aiquanttrader.domain.base import DomainModel
from aiquanttrader.domain.execution import RiskSnapshot
from aiquanttrader.domain.market import AggressorSide, OrderSide
from aiquanttrader.execution.artifacts import LiveStrategyArtifacts
from aiquanttrader.features.engine import IncrementalFeatureEngine
from aiquanttrader.features.market_structure import (
    CausalMarketStructureEngine,
    SmartMoneySnapshot,
)
from aiquanttrader.features.models import InventoryState, MicrostructureSnapshot
from aiquanttrader.market_data.io import atomic_replace_bytes
from aiquanttrader.strategies.common import StrategyInput, StrategyTransition
from aiquanttrader.strategies.market_maker import (
    AvellanedaStoikovConfig,
    AvellanedaStoikovKernel,
    MarketMakerMemory,
)
from aiquanttrader.strategies.scalper import (
    OrderFlowScalperConfig,
    OrderFlowScalperKernel,
    ScalperMemory,
)
from aiquanttrader.strategies.smart_money_scalper import (
    SmartMoneyScalperConfig,
    SmartMoneyScalperKernel,
    SmartMoneyScalperMemory,
)

INSTRUMENT_ID = "BTC-USD-PERP.HYPERLIQUID"


class NautilusMarketStateAssembler:
    """Convert managed Nautilus L2 books and trades to the shared causal kernel state."""

    def __init__(self, *, depth_levels: int, max_pending_trades: int = 10_000) -> None:
        if not 1 <= depth_levels <= 10:
            raise ValueError("live Nautilus depth must be in [1, 10]")
        if max_pending_trades < 1:
            raise ValueError("live pending-trade bound must be positive")
        self._depth_levels = depth_levels
        self._max_pending_trades = max_pending_trades
        self._pending_trades: list[KernelTrade] = []
        self._sequence = 0
        self._last_observed_ts_ns: int | None = None

    def observe_trade(self, tick: TradeTick) -> None:
        if str(tick.instrument_id) != INSTRUMENT_ID:
            raise ValueError("live trade belongs to an unexpected instrument")
        if len(self._pending_trades) >= self._max_pending_trades:
            raise ValueError("live trade buffer exceeded its hard bound")
        aggressor = {
            NautilusAggressorSide.BUYER: AggressorSide.BUYER,
            NautilusAggressorSide.SELLER: AggressorSide.SELLER,
        }.get(tick.aggressor_side, AggressorSide.UNKNOWN)
        self._pending_trades.append(
            KernelTrade(
                exchange_ts_ns=int(tick.ts_event),
                observed_ts_ns=int(tick.ts_init),
                price=Decimal(str(tick.price)),
                size=Decimal(str(tick.size)),
                aggressor=aggressor,
            )
        )

    def observe_book(
        self,
        book: OrderBook,
        deltas: OrderBookDeltas,
    ) -> KernelMarketState:
        if str(deltas.instrument_id) != INSTRUMENT_ID:
            raise ValueError("live order book belongs to an unexpected instrument")
        observed = int(deltas.ts_init)
        if self._last_observed_ts_ns is not None and observed <= self._last_observed_ts_ns:
            raise ValueError("live Nautilus receive timestamps must be strictly increasing")
        bids = self._levels(book.bids(), reverse=True)
        asks = self._levels(book.asks(), reverse=False)
        if not bids or not asks:
            raise ValueError("live managed order book must contain both sides")
        trades = tuple(self._pending_trades)
        exchange_ts = max((int(deltas.ts_event), *(trade.exchange_ts_ns for trade in trades)))
        state = KernelMarketState(
            exchange_ts_ns=exchange_ts,
            book_exchange_ts_ns=int(deltas.ts_event),
            observed_ts_ns=observed,
            sequence=self._sequence,
            bids=bids,
            asks=asks,
            trades=trades,
        )
        self._pending_trades.clear()
        self._sequence += 1
        self._last_observed_ts_ns = observed
        return state

    def _levels(self, levels: list[Any], *, reverse: bool) -> tuple[KernelBookLevel, ...]:
        normalized: list[KernelBookLevel] = []
        for level in levels[: self._depth_levels]:
            price = Decimal(str(level.price))
            size = sum((order.size.as_decimal() for order in level.orders()), Decimal("0"))
            if size > 0:
                normalized.append(KernelBookLevel(price=price, size=size))
        return tuple(sorted(normalized, key=lambda item: item.price, reverse=reverse))


LiveMemory = MarketMakerMemory | ScalperMemory | SmartMoneyScalperMemory
LiveTransition = (
    StrategyTransition[MarketMakerMemory]
    | StrategyTransition[ScalperMemory]
    | StrategyTransition[SmartMoneyScalperMemory]
)


@dataclass(frozen=True, slots=True)
class LiveStrategyCycle:
    features: MicrostructureSnapshot
    market_structure: SmartMoneySnapshot
    transition: LiveTransition


class LiveDecisionPipeline:
    """Run the same feature and alpha kernels as research without execution access."""

    def __init__(self, artifacts: LiveStrategyArtifacts) -> None:
        self.artifacts = artifacts
        self.market = NautilusMarketStateAssembler(
            depth_levels=artifacts.feature_config.depth_levels
        )
        self._features = IncrementalFeatureEngine(artifacts.feature_config)
        self._structure = CausalMarketStructureEngine()
        strategy = artifacts.strategy_config
        self._kernel: AvellanedaStoikovKernel | OrderFlowScalperKernel | SmartMoneyScalperKernel
        if isinstance(strategy, AvellanedaStoikovConfig):
            self._kernel = AvellanedaStoikovKernel(strategy)
            self._memory: LiveMemory = MarketMakerMemory()
        elif isinstance(strategy, OrderFlowScalperConfig):
            self._kernel = OrderFlowScalperKernel(strategy)
            self._memory = ScalperMemory()
        elif isinstance(strategy, SmartMoneyScalperConfig):
            self._kernel = SmartMoneyScalperKernel(strategy)
            self._memory = SmartMoneyScalperMemory()
        else:  # pragma: no cover - guarded by the artifact union
            raise TypeError("unsupported live strategy configuration")

    @property
    def memory(self) -> LiveMemory:
        return self._memory

    def decide(
        self,
        market: KernelMarketState,
        *,
        position_base: Decimal,
        margin_utilization: Decimal,
        funding_rate: Decimal,
        estimated_taker_fee_bps: Decimal,
        estimated_slippage_bps: Decimal,
        position_average_entry_price: Decimal | None = None,
        position_opened_ts_ns: int | None = None,
    ) -> LiveStrategyCycle:
        if isinstance(self._memory, SmartMoneyScalperMemory):
            memory: LiveMemory = self._memory.synchronize_position(
                position_base,
                position_average_entry_price,
                market.observed_ts_ns,
            )
        else:
            memory = self._memory.with_inventory(position_base)
        features = self._features.update(
            market,
            inventory=InventoryState(
                confirmed_base=position_base,
                margin_utilization=max(Decimal("0"), min(Decimal("1"), margin_utilization)),
            ),
        )
        structure = self._structure.update(market)
        strategy_input = StrategyInput(
            features=features,
            funding_rate=funding_rate,
            estimated_taker_fee_bps=estimated_taker_fee_bps,
            estimated_slippage_bps=estimated_slippage_bps,
            market_structure=structure,
            position_average_entry_price=position_average_entry_price,
            position_opened_ts_ns=position_opened_ts_ns,
        )
        if isinstance(memory, MarketMakerMemory) and isinstance(
            self._kernel, AvellanedaStoikovKernel
        ):
            transition: LiveTransition = self._kernel.decide(strategy_input, memory)
        elif isinstance(memory, ScalperMemory) and isinstance(  # noqa: SIM114
            self._kernel, OrderFlowScalperKernel
        ):
            transition = self._kernel.decide(strategy_input, memory)
        elif isinstance(memory, SmartMoneyScalperMemory) and isinstance(
            self._kernel, SmartMoneyScalperKernel
        ):
            transition = self._kernel.decide(strategy_input, memory)
        else:  # pragma: no cover - constructor fixes the pair
            raise TypeError("live strategy kernel and memory types diverged")
        return LiveStrategyCycle(
            features=features,
            market_structure=structure,
            transition=transition,
        )

    def commit(
        self,
        cycle: LiveStrategyCycle,
        *,
        dispatched_intent_ids: set[str],
        dispatched_cancel_ids: set[str],
    ) -> None:
        """Commit only commands durably handed to Nautilus; denied intents remain absent."""

        current_memory = self._memory
        if isinstance(current_memory, SmartMoneyScalperMemory):
            target = cycle.transition.memory
            if not isinstance(target, SmartMoneyScalperMemory):
                raise TypeError("smart-money transition returned incompatible memory")
            prior_smart = current_memory.synchronize_position(
                target.inventory_base,
                target.average_entry_price,
                cycle.features.receive_ts_ns,
            )
            self._memory = target if dispatched_intent_ids else prior_smart
            return
        prior_classic = current_memory.with_inventory(cycle.transition.memory.inventory_base)
        if isinstance(prior_classic, ScalperMemory):
            target_memory = cycle.transition.memory
            if not isinstance(target_memory, ScalperMemory):
                raise TypeError("scalper transition returned incompatible memory")
            self._memory = target_memory if dispatched_intent_ids else prior_classic
            return
        if not isinstance(prior_classic, MarketMakerMemory):  # pragma: no cover
            raise TypeError("unsupported live strategy memory")
        bid_id = prior_classic.active_bid_intent_id
        bid_price = prior_classic.active_bid_price
        ask_id = prior_classic.active_ask_intent_id
        ask_price = prior_classic.active_ask_price
        # A cancel dispatch is not a cancel outcome. Keep quote identity until the
        # authoritative terminal event invokes ``release_intent``.
        submitted = {
            intent.intent_id: intent
            for intent in cycle.transition.decision.submit
            if intent.intent_id in dispatched_intent_ids
        }
        for intent in submitted.values():
            if intent.side is OrderSide.BUY:
                bid_id, bid_price = intent.intent_id, intent.limit_price
            else:
                ask_id, ask_price = intent.intent_id, intent.limit_price
        changed = bool(dispatched_intent_ids)
        target = cycle.transition.memory
        if not isinstance(target, MarketMakerMemory):
            raise TypeError("market-maker transition returned incompatible memory")
        self._memory = MarketMakerMemory(
            inventory_base=target.inventory_base,
            active_bid_intent_id=bid_id,
            active_bid_price=bid_price,
            active_ask_intent_id=ask_id,
            active_ask_price=ask_price,
            last_quote_ts_ns=(
                target.last_quote_ts_ns if changed else prior_classic.last_quote_ts_ns
            ),
            quote_revision=target.quote_revision if changed else prior_classic.quote_revision,
        )

    def release_intent(self, intent_id: str) -> None:
        """Forget a passive quote only after an authoritative terminal event."""

        memory = self._memory
        if not isinstance(memory, MarketMakerMemory):
            return
        bid_matches = memory.active_bid_intent_id == intent_id
        ask_matches = memory.active_ask_intent_id == intent_id
        if not bid_matches and not ask_matches:
            return
        self._memory = MarketMakerMemory(
            inventory_base=memory.inventory_base,
            active_bid_intent_id=None if bid_matches else memory.active_bid_intent_id,
            active_bid_price=None if bid_matches else memory.active_bid_price,
            active_ask_intent_id=None if ask_matches else memory.active_ask_intent_id,
            active_ask_price=None if ask_matches else memory.active_ask_price,
            last_quote_ts_ns=memory.last_quote_ts_ns,
            quote_revision=memory.quote_revision,
        )


class EquityBaseline(DomainModel):
    schema_version: Literal[1] = 1
    account_address: str = Field(pattern=r"^0x[0-9a-f]{40}$")
    broker_day_utc: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    day_start_equity_usd: Decimal = Field(gt=0)
    high_water_equity_usd: Decimal = Field(gt=0)
    updated_ts_ns: int = Field(ge=0)

    @model_validator(mode="after")
    def high_water_covers_day_start(self) -> EquityBaseline:
        if self.high_water_equity_usd < self.day_start_equity_usd:
            raise ValueError("equity high-water mark cannot be below the day-start equity")
        return self


class EquityBaselineStore:
    """Durably preserve daily loss and drawdown baselines across process restarts."""

    def __init__(self, path: Path, *, account_address: str) -> None:
        if not path.is_absolute():
            raise ValueError("equity baseline path must be absolute")
        self.path = path
        self.account_address = account_address.lower()
        self._state: EquityBaseline | None = None
        if path.exists():
            self._state = EquityBaseline.model_validate_json(path.read_bytes())
            if self._state.account_address != self.account_address:
                raise ValueError("equity baseline belongs to a different execution account")

    def observe(self, equity_usd: Decimal, *, now_ns: int) -> EquityBaseline:
        if equity_usd <= 0:
            raise ValueError("live account equity must be positive")
        day = datetime.fromtimestamp(now_ns / 1_000_000_000, tz=UTC).date().isoformat()
        prior = self._state
        if prior is not None and day < prior.broker_day_utc:
            raise ValueError("system clock moved behind the persisted equity broker day")
        day_start = (
            equity_usd
            if prior is None or day != prior.broker_day_utc
            else prior.day_start_equity_usd
        )
        prior_high_water = Decimal("0") if prior is None else prior.high_water_equity_usd
        high_water = max(equity_usd, day_start, prior_high_water)
        state = EquityBaseline(
            account_address=self.account_address,
            broker_day_utc=day,
            day_start_equity_usd=day_start,
            high_water_equity_usd=high_water,
            updated_ts_ns=now_ns,
        )
        if prior is None or (
            prior.broker_day_utc != state.broker_day_utc
            or prior.day_start_equity_usd != state.day_start_equity_usd
            or prior.high_water_equity_usd != state.high_water_equity_usd
        ):
            atomic_replace_bytes(self.path, state.canonical_bytes() + b"\n", mode=0o600)
        self._state = state
        return state


@dataclass(frozen=True, slots=True)
class LiveAccountState:
    equity_usd: Decimal
    position_base: Decimal
    pending_buy_base: Decimal
    pending_sell_base: Decimal
    open_order_count: int
    average_entry_price: Decimal | None = None
    position_opened_ts_ns: int | None = None


def read_live_account_state(portfolio: Any, cache: Any) -> LiveAccountState:
    """Read one authoritative BTC account/exposure view from Nautilus facades."""

    instrument_id = InstrumentId.from_str(INSTRUMENT_ID)
    venue = Venue("HYPERLIQUID")
    account = portfolio.account(venue)
    if account is None:
        raise ValueError("reconciled Hyperliquid account is unavailable")
    equities = portfolio.equity(venue)
    base_currency = account.base_currency
    if base_currency is not None and base_currency in equities:
        equity_money = equities[base_currency]
    elif len(equities) == 1:
        equity_money = next(iter(equities.values()))
    else:
        raise ValueError("live account equity is missing or ambiguous")
    equity = equity_money.as_decimal()
    positions = cache.positions_open(instrument_id=instrument_id)
    position = sum((item.signed_decimal_qty() for item in positions), Decimal("0"))
    average_entry_price: Decimal | None = None
    position_opened_ts_ns: int | None = None
    if position != 0 and len(positions) == 1:
        raw_average = getattr(positions[0], "avg_px_open", None)
        if raw_average is not None:
            average_entry_price = (
                raw_average.as_decimal()
                if hasattr(raw_average, "as_decimal")
                else Decimal(str(raw_average))
            )
            if average_entry_price <= 0:
                raise ValueError("live position average entry price must be positive")
        raw_opened = getattr(positions[0], "ts_opened", None)
        if raw_opened is not None:
            position_opened_ts_ns = int(raw_opened)
            if position_opened_ts_ns < 0:
                raise ValueError("live position open timestamp cannot be negative")
    orders = list(cache.orders_open(instrument_id=instrument_id))
    pending_buy = sum(
        (order.leaves_qty.as_decimal() for order in orders if order.side is NautilusOrderSide.BUY),
        Decimal("0"),
    )
    pending_sell = sum(
        (order.leaves_qty.as_decimal() for order in orders if order.side is NautilusOrderSide.SELL),
        Decimal("0"),
    )
    return LiveAccountState(
        equity_usd=equity,
        position_base=position,
        pending_buy_base=pending_buy,
        pending_sell_base=pending_sell,
        open_order_count=len(orders),
        average_entry_price=average_entry_price,
        position_opened_ts_ns=position_opened_ts_ns,
    )


def build_live_risk_snapshot(
    *,
    now_ns: int,
    public_data_ts_ns: int,
    mark_price: Decimal,
    account: LiveAccountState,
    baseline: EquityBaseline,
    exchange_connected: bool,
    reconciliation_complete: bool,
    deployment_approved: bool = True,
) -> RiskSnapshot:
    leverage = abs(account.position_base * mark_price) / account.equity_usd
    return RiskSnapshot(
        snapshot_ts_ns=now_ns,
        public_data_ts_ns=min(public_data_ts_ns, now_ns),
        private_data_ts_ns=now_ns if exchange_connected else 0,
        mark_price=mark_price,
        position_base=account.position_base,
        pending_buy_base=account.pending_buy_base,
        pending_sell_base=account.pending_sell_base,
        account_equity_usd=account.equity_usd,
        day_start_equity_usd=baseline.day_start_equity_usd,
        high_water_equity_usd=baseline.high_water_equity_usd,
        leverage=leverage,
        open_order_count=account.open_order_count,
        exchange_connected=exchange_connected,
        reconciliation_complete=reconciliation_complete,
        deployment_approved=deployment_approved,
    )
