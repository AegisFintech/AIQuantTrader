"""Deterministic market-by-price paper exchange with explicit limitations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from aiquanttrader.backtest.kernel import KernelMarketState, KernelTrade
from aiquanttrader.backtest.models import ExecutionScenario, QueueModel
from aiquanttrader.domain.execution import OrderIntent, OrderKind, TimeInForce
from aiquanttrader.domain.market import AggressorSide, OrderSide
from aiquanttrader.paper.models import (
    TERMINAL_PAPER_ORDER_STATES,
    PaperAccountState,
    PaperFill,
    PaperOrder,
    PaperOrderState,
)

NANOSECONDS_PER_DAY = 86_400_000_000_000


def validate_paper_scenario(scenario: ExecutionScenario) -> None:
    """Reject assumptions this public MBP simulator cannot apply faithfully."""

    if scenario.queue_model is not QueueModel.RISK_ADVERSE:
        raise ValueError("paper simulation supports only the conservative risk_adverse queue model")
    if scenario.feed_latency_offset_ns != 0:
        raise ValueError(
            "live paper simulation requires zero synthetic feed latency; use retained replay"
        )


@dataclass(frozen=True, slots=True)
class SimulatorUpdate:
    orders: tuple[PaperOrder, ...]
    fills: tuple[PaperFill, ...]
    account: PaperAccountState


class PaperExchangeSimulator:
    """A causal simulator; market-by-price depth cannot claim private queue truth."""

    def __init__(
        self,
        scenario: ExecutionScenario,
        *,
        initial_equity_usd: Decimal,
        initial_mark_price: Decimal,
        started_ts_ns: int,
        restored_account: PaperAccountState | None = None,
        restored_orders: tuple[PaperOrder, ...] = (),
        identity_namespace: str = "paper",
    ) -> None:
        if initial_equity_usd <= 0 or initial_mark_price <= 0 or started_ts_ns < 0:
            raise ValueError("paper simulator requires positive capital, mark, and timestamp")
        validate_paper_scenario(scenario)
        self.scenario = scenario
        if not identity_namespace:
            raise ValueError("paper simulator identity namespace must be non-empty")
        self._identity_namespace = identity_namespace
        self._orders: dict[str, PaperOrder] = {}
        self._intent_ids: set[str] = set()
        for order in restored_orders:
            if order.paper_order_id in self._orders or order.intent.intent_id in self._intent_ids:
                raise ValueError("restored paper orders contain duplicate identities")
            self._orders[order.paper_order_id] = order
            self._intent_ids.add(order.intent.intent_id)
        self._account = restored_account or PaperAccountState(
            cash_usd=initial_equity_usd,
            mark_price=initial_mark_price,
            equity_usd=initial_equity_usd,
            day_start_equity_usd=initial_equity_usd,
            high_water_equity_usd=initial_equity_usd,
            utc_day=started_ts_ns // NANOSECONDS_PER_DAY,
            updated_ts_ns=started_ts_ns,
        )

    @property
    def account(self) -> PaperAccountState:
        return self._account

    @property
    def open_orders(self) -> tuple[PaperOrder, ...]:
        return tuple(
            sorted(
                (
                    order
                    for order in self._orders.values()
                    if order.state not in TERMINAL_PAPER_ORDER_STATES
                ),
                key=lambda order: (order.accepted_ts_ns, order.paper_order_id),
            )
        )

    @property
    def orders(self) -> tuple[PaperOrder, ...]:
        return tuple(sorted(self._orders.values(), key=lambda order: order.paper_order_id))

    def pending_exposure(self) -> tuple[Decimal, Decimal]:
        buys = sum(
            (
                order.remaining_quantity_base
                for order in self.open_orders
                if order.intent.side is OrderSide.BUY
            ),
            Decimal("0"),
        )
        sells = sum(
            (
                order.remaining_quantity_base
                for order in self.open_orders
                if order.intent.side is OrderSide.SELL
            ),
            Decimal("0"),
        )
        return buys, sells

    def submit(self, intent: OrderIntent, *, accepted_ts_ns: int) -> PaperOrder:
        if intent.intent_id in self._intent_ids:
            raise ValueError(f"duplicate paper intent: {intent.intent_id}")
        self._intent_ids.add(intent.intent_id)
        reason = self._instruction_error(intent)
        order_identity = f"{self._identity_namespace}:{intent.intent_id}"
        paper_order_id = f"paper-{hashlib.sha256(order_identity.encode()).hexdigest()[:24]}"
        effective = accepted_ts_ns + self.scenario.entry_latency_ns
        state = PaperOrderState.REJECTED if reason else PaperOrderState.PENDING_ACTIVATION
        order = PaperOrder(
            paper_order_id=paper_order_id,
            intent=intent,
            state=state,
            accepted_ts_ns=accepted_ts_ns,
            effective_ts_ns=effective,
            updated_ts_ns=accepted_ts_ns,
            rejection_reason=reason,
        )
        self._orders[paper_order_id] = order
        return order

    def request_cancel(self, intent_id: str, *, requested_ts_ns: int) -> PaperOrder | None:
        order = self._find_open_intent(intent_id)
        if order is None:
            return None
        effective = requested_ts_ns + self.scenario.response_latency_ns
        updated = order.model_copy(
            update={
                "state": PaperOrderState.PENDING_CANCEL,
                "cancel_effective_ts_ns": effective,
                "updated_ts_ns": requested_ts_ns,
            }
        )
        self._orders[order.paper_order_id] = updated
        return updated

    def request_cancel_all(self, *, requested_ts_ns: int) -> tuple[PaperOrder, ...]:
        updated: list[PaperOrder] = []
        for order in self.open_orders:
            result = self.request_cancel(order.intent.intent_id, requested_ts_ns=requested_ts_ns)
            if result is not None:
                updated.append(result)
        return tuple(updated)

    def elapse(self, now_ts_ns: int) -> tuple[PaperOrder, ...]:
        """Apply latency-bound cancellations when no new market frame is available."""

        changed: list[PaperOrder] = []
        for order in self.open_orders:
            if (
                order.state is PaperOrderState.PENDING_CANCEL
                and order.cancel_effective_ts_ns is not None
                and order.cancel_effective_ts_ns <= now_ts_ns
            ):
                updated = order.model_copy(
                    update={"state": PaperOrderState.CANCELED, "updated_ts_ns": now_ts_ns}
                )
                self._orders[order.paper_order_id] = updated
                changed.append(updated)
        return tuple(changed)

    def advance(
        self,
        market: KernelMarketState,
        *,
        mark_price: Decimal | None = None,
    ) -> SimulatorUpdate:
        now = market.observed_ts_ns
        changed: dict[str, PaperOrder] = {}
        fills: list[PaperFill] = []
        self._mark(mark_price or self._mid(market), now)

        for order in self.open_orders:
            if (
                order.state is PaperOrderState.PENDING_CANCEL
                and order.cancel_effective_ts_ns is not None
                and order.cancel_effective_ts_ns <= now
            ):
                canceled = order.model_copy(
                    update={"state": PaperOrderState.CANCELED, "updated_ts_ns": now}
                )
                self._store(canceled, changed)

        for order in self.open_orders:
            if order.state is not PaperOrderState.PENDING_ACTIVATION or order.effective_ts_ns > now:
                continue
            activated, new_fills = self._activate(order, market, now)
            self._store(activated, changed)
            fills.extend(new_fills)

        for trade in market.trades:
            self._match_trade(trade, now, changed, fills)

        self._mark(mark_price or self._mid(market), now)
        return SimulatorUpdate(tuple(changed.values()), tuple(fills), self._account)

    def settle_funding(
        self,
        *,
        funding_rate: Decimal,
        mark_price: Decimal,
        settlement_ts_ns: int,
    ) -> PaperAccountState:
        previous = self._account.last_funding_settlement_ns
        if previous is not None and settlement_ts_ns <= previous:
            raise ValueError("paper funding settlements must be strictly increasing")
        cashflow = -(
            self._account.position_base
            * mark_price
            * funding_rate
            * self.scenario.funding_rate_multiplier
        )
        account = self._account.model_copy(
            update={
                "cash_usd": self._account.cash_usd + cashflow,
                "funding_pnl_usd": self._account.funding_pnl_usd + cashflow,
                "last_funding_settlement_ns": settlement_ts_ns,
            }
        )
        self._account = account
        self._mark(mark_price, settlement_ts_ns)
        return self._account

    def _activate(
        self, order: PaperOrder, market: KernelMarketState, now: int
    ) -> tuple[PaperOrder, tuple[PaperFill, ...]]:
        intent = order.intent
        if intent.kind is OrderKind.MARKET:
            return self._execute_taker(order, market, now)
        assert intent.limit_price is not None
        crossing = (
            intent.side is OrderSide.BUY and intent.limit_price >= market.asks[0].price
        ) or (intent.side is OrderSide.SELL and intent.limit_price <= market.bids[0].price)
        if intent.post_only and crossing:
            return (
                order.model_copy(
                    update={
                        "state": PaperOrderState.REJECTED,
                        "updated_ts_ns": now,
                        "rejection_reason": "post_only_would_cross",
                    }
                ),
                (),
            )
        if intent.time_in_force is TimeInForce.IOC:
            if crossing:
                return self._execute_taker(order, market, now)
            return (
                order.model_copy(update={"state": PaperOrderState.CANCELED, "updated_ts_ns": now}),
                (),
            )
        queue = self._displayed_size(market, intent.side, intent.limit_price)
        queue *= self.scenario.book_liquidity_multiplier
        return (
            order.model_copy(
                update={
                    "state": PaperOrderState.RESTING,
                    "queue_ahead_base": queue,
                    "updated_ts_ns": now,
                }
            ),
            (),
        )

    def _execute_taker(
        self, order: PaperOrder, market: KernelMarketState, now: int
    ) -> tuple[PaperOrder, tuple[PaperFill, ...]]:
        levels = market.asks if order.intent.side is OrderSide.BUY else market.bids
        if order.intent.limit_price is not None:
            levels = tuple(
                level
                for level in levels
                if (
                    level.price <= order.intent.limit_price
                    if order.intent.side is OrderSide.BUY
                    else level.price >= order.intent.limit_price
                )
            )
        available = sum(
            (level.size * self.scenario.book_liquidity_multiplier for level in levels),
            Decimal("0"),
        )
        requested = order.remaining_quantity_base
        target = min(requested, available) if self.scenario.allow_partial_fills else requested
        if target <= 0 or (not self.scenario.allow_partial_fills and available < requested):
            return (
                order.model_copy(update={"state": PaperOrderState.CANCELED, "updated_ts_ns": now}),
                (),
            )
        direction = Decimal("1") if order.intent.side is OrderSide.BUY else Decimal("-1")
        remaining = target
        fills: list[PaperFill] = []
        for level in levels:
            quantity = min(
                remaining,
                level.size * self.scenario.book_liquidity_multiplier,
            )
            if quantity <= 0:
                continue
            raw_price = level.price * (
                Decimal("1") + direction * self.scenario.taker_slippage_bps / Decimal("10000")
            )
            price = self._round_adverse(raw_price, order.intent.side)
            if order.intent.limit_price is not None:
                price = (
                    min(price, order.intent.limit_price)
                    if order.intent.side is OrderSide.BUY
                    else max(price, order.intent.limit_price)
                )
            fill_order = order.model_copy(
                update={"filled_quantity_base": order.filled_quantity_base + target - remaining}
            )
            fills.append(
                self._fill(fill_order, quantity=quantity, price=price, maker=False, ts_ns=now)
            )
            remaining -= quantity
            if remaining == 0:
                break
        total_filled = order.filled_quantity_base + target - remaining
        state = (
            PaperOrderState.FILLED
            if total_filled == order.intent.quantity_base
            else PaperOrderState.CANCELED
        )
        return (
            order.model_copy(
                update={
                    "state": state,
                    "filled_quantity_base": total_filled,
                    "updated_ts_ns": now,
                }
            ),
            tuple(fills),
        )

    def _match_trade(
        self,
        trade: KernelTrade,
        now: int,
        changed: dict[str, PaperOrder],
        fills: list[PaperFill],
    ) -> None:
        available = trade.size * self.scenario.trade_flow_multiplier
        if available <= 0:
            return
        candidates = [
            order
            for order in self.open_orders
            if order.state
            in {
                PaperOrderState.RESTING,
                PaperOrderState.PARTIALLY_FILLED,
                PaperOrderState.PENDING_CANCEL,
            }
            and order.intent.limit_price is not None
            and order.effective_ts_ns <= trade.observed_ts_ns
            and self._trade_reaches_order(trade, order)
        ]
        candidates.sort(key=lambda order: (order.effective_ts_ns, order.paper_order_id))
        for order in candidates:
            if available <= 0:
                break
            queue_consumed = min(order.queue_ahead_base, available)
            available -= queue_consumed
            queue_remaining = order.queue_ahead_base - queue_consumed
            if available <= 0:
                updated = order.model_copy(
                    update={"queue_ahead_base": queue_remaining, "updated_ts_ns": now}
                )
                self._store(updated, changed)
                continue
            fill_quantity = min(order.remaining_quantity_base, available)
            if (
                not self.scenario.allow_partial_fills
                and fill_quantity < order.remaining_quantity_base
            ):
                continue
            assert order.intent.limit_price is not None
            fill = self._fill(
                order,
                quantity=fill_quantity,
                price=order.intent.limit_price,
                maker=True,
                ts_ns=now,
            )
            fills.append(fill)
            available -= fill_quantity
            total_filled = order.filled_quantity_base + fill_quantity
            if total_filled == order.intent.quantity_base:
                state = PaperOrderState.FILLED
                cancel_effective = None
            elif order.state is PaperOrderState.PENDING_CANCEL:
                state = PaperOrderState.PENDING_CANCEL
                cancel_effective = order.cancel_effective_ts_ns
            else:
                state = PaperOrderState.PARTIALLY_FILLED
                cancel_effective = None
            updated = order.model_copy(
                update={
                    "state": state,
                    "filled_quantity_base": total_filled,
                    "queue_ahead_base": queue_remaining,
                    "updated_ts_ns": now,
                    "cancel_effective_ts_ns": cancel_effective,
                }
            )
            self._store(updated, changed)

    def _fill(
        self,
        order: PaperOrder,
        *,
        quantity: Decimal,
        price: Decimal,
        maker: bool,
        ts_ns: int,
    ) -> PaperFill:
        fee_bps = self.scenario.maker_fee_bps if maker else self.scenario.taker_fee_bps
        fee = quantity * price * fee_bps / Decimal("10000")
        identity = (
            f"{self._identity_namespace}:{order.paper_order_id}:"
            f"{order.filled_quantity_base}:{quantity}:{price}:{ts_ns}"
        )
        fill = PaperFill(
            fill_id=f"fill-{hashlib.sha256(identity.encode()).hexdigest()[:32]}",
            paper_order_id=order.paper_order_id,
            intent_id=order.intent.intent_id,
            strategy_id=order.intent.strategy_id,
            side=order.intent.side,
            quantity_base=quantity,
            price=price,
            fee_usd=fee,
            maker=maker,
            fill_ts_ns=ts_ns,
            decision_latency_ns=max(0, ts_ns - order.intent.created_ts_ns),
            scenario_id=self.scenario.scenario_id,
            scenario_sha256=self.scenario.sha256(),
        )
        self._apply_fill(fill)
        return fill

    def _apply_fill(self, fill: PaperFill) -> None:
        account = self._account
        old_position = account.position_base
        signed = fill.quantity_base if fill.side is OrderSide.BUY else -fill.quantity_base
        new_position = old_position + signed
        cash_delta = -signed * fill.price - fill.fee_usd
        realized = account.realized_trading_pnl_usd
        average = account.average_entry_price

        if old_position == 0 or old_position * signed > 0:
            old_notional = abs(old_position) * (average or fill.price)
            total_quantity = abs(old_position) + abs(signed)
            average = (old_notional + abs(signed) * fill.price) / total_quantity
        else:
            assert average is not None
            closed = min(abs(old_position), abs(signed))
            direction = Decimal("1") if old_position > 0 else Decimal("-1")
            realized += direction * closed * (fill.price - average)
            if new_position == 0:
                average = None
            elif old_position * new_position < 0:
                average = fill.price

        self._account = account.model_copy(
            update={
                "cash_usd": account.cash_usd + cash_delta,
                "position_base": new_position,
                "average_entry_price": average,
                "realized_trading_pnl_usd": realized,
                "fees_usd": account.fees_usd + fill.fee_usd,
                "updated_ts_ns": fill.fill_ts_ns,
            }
        )

    def _mark(self, mark_price: Decimal, ts_ns: int) -> None:
        account = self._account
        equity = account.cash_usd + account.position_base * mark_price
        day = ts_ns // NANOSECONDS_PER_DAY
        day_start = (
            account.day_start_equity_usd
            if day == account.utc_day
            else max(equity, Decimal("0.00000001"))
        )
        high_water = max(account.high_water_equity_usd, equity, Decimal("0.00000001"))
        self._account = account.model_copy(
            update={
                "mark_price": mark_price,
                "equity_usd": equity,
                "day_start_equity_usd": day_start,
                "high_water_equity_usd": high_water,
                "utc_day": day,
                "updated_ts_ns": ts_ns,
            }
        )

    def _instruction_error(self, intent: OrderIntent) -> str | None:
        lot_units = intent.quantity_base / self.scenario.lot_size
        if lot_units != lot_units.to_integral_value():
            return "quantity_not_on_lot_size"
        if intent.limit_price is not None:
            tick_units = intent.limit_price / self.scenario.tick_size
            if tick_units != tick_units.to_integral_value():
                return "price_not_on_tick_size"
        if (
            intent.kind is OrderKind.LIMIT
            and intent.time_in_force is TimeInForce.GTC
            and not intent.post_only
        ):
            return "paper_gtc_limit_must_be_post_only"
        return None

    def _find_open_intent(self, intent_id: str) -> PaperOrder | None:
        return next(
            (order for order in self.open_orders if order.intent.intent_id == intent_id),
            None,
        )

    def _store(self, order: PaperOrder, changed: dict[str, PaperOrder]) -> None:
        self._orders[order.paper_order_id] = order
        changed[order.paper_order_id] = order

    @staticmethod
    def _mid(market: KernelMarketState) -> Decimal:
        return (market.bids[0].price + market.asks[0].price) / Decimal("2")

    @staticmethod
    def _displayed_size(market: KernelMarketState, side: OrderSide, price: Decimal) -> Decimal:
        levels = market.bids if side is OrderSide.BUY else market.asks
        return next((level.size for level in levels if level.price == price), Decimal("0"))

    @staticmethod
    def _trade_reaches_order(trade: KernelTrade, order: PaperOrder) -> bool:
        assert order.intent.limit_price is not None
        return (
            order.intent.side is OrderSide.BUY
            and trade.aggressor is AggressorSide.SELLER
            and trade.price <= order.intent.limit_price
        ) or (
            order.intent.side is OrderSide.SELL
            and trade.aggressor is AggressorSide.BUYER
            and trade.price >= order.intent.limit_price
        )

    def _round_adverse(self, price: Decimal, side: OrderSide) -> Decimal:
        rounding = ROUND_CEILING if side is OrderSide.BUY else ROUND_FLOOR
        ticks = (price / self.scenario.tick_size).to_integral_value(rounding=rounding)
        return ticks * self.scenario.tick_size
