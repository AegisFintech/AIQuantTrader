"""Thin deterministic session over the pinned Rust HftBacktest engine."""

from __future__ import annotations

from bisect import bisect_right
from decimal import Decimal
from itertools import pairwise
from typing import Any

import numpy as np
from hftbacktest import FILLED, GTC, GTX, LIMIT, LOCAL_EVENT, HashMapMarketDepthBacktest
from hftbacktest.order import PARTIALLY_FILLED
from hftbacktest.types import UNTIL_END_OF_DATA

from aiquanttrader.backtest.models import (
    ExecutionScenario,
    FillObservation,
    FundingCashflow,
    FundingObservation,
    PositionObservation,
    ReplayResult,
)
from aiquanttrader.backtest.scenarios import build_hft_asset
from aiquanttrader.domain.market import OrderSide


def calculate_funding_cashflows(
    *,
    positions: tuple[PositionObservation, ...],
    funding: tuple[FundingObservation, ...],
    multiplier: Decimal = Decimal("1"),
) -> tuple[FundingCashflow, ...]:
    if multiplier < 0:
        raise ValueError("funding multiplier cannot be negative")
    if any(left.ts_ns >= right.ts_ns for left, right in pairwise(positions)):
        raise ValueError("position observations must be strictly increasing")
    if any(left.settlement_ts_ns >= right.settlement_ts_ns for left, right in pairwise(funding)):
        raise ValueError("funding observations must be strictly increasing")
    timestamps = [item.ts_ns for item in positions]
    cashflows: list[FundingCashflow] = []
    for item in funding:
        index = bisect_right(timestamps, item.settlement_ts_ns) - 1
        position = Decimal("0") if index < 0 else positions[index].position_base
        cashflow = -(position * item.oracle_price * item.funding_rate * multiplier)
        cashflows.append(
            FundingCashflow(
                settlement_ts_ns=item.settlement_ts_ns,
                position_base=position,
                funding_rate=item.funding_rate,
                oracle_price=item.oracle_price,
                cashflow_usd=cashflow,
            )
        )
    return tuple(cashflows)


class HftReplaySession:
    """Execute explicit decisions while retaining fill and funding evidence."""

    def __init__(
        self,
        *,
        events: np.ndarray[Any, np.dtype[Any]],
        dataset_sha256: str,
        scenario: ExecutionScenario,
    ) -> None:
        local_rows = events[events["ev"] & LOCAL_EVENT == LOCAL_EVENT]
        if not local_rows.size:
            raise ValueError("replay dataset has no local-arrival events")
        self._scenario = scenario
        self._dataset_sha256 = dataset_sha256
        self._dataset_end_ns = int(local_rows["local_ts"].max())
        self._backtest = HashMapMarketDepthBacktest([build_hft_asset(events, scenario)])
        self._known_orders: set[int] = set()
        self._executed: dict[int, float] = {}
        self._executed_notional: dict[int, float] = {}
        self._fills: list[FillObservation] = []
        self._positions: list[PositionObservation] = []
        self._closed = False
        self._started = False
        self._ended = False
        self._last_timestamp_ns = 0

    @property
    def current_timestamp_ns(self) -> int:
        timestamp = int(self._backtest.current_timestamp)
        if timestamp > self._last_timestamp_ns:
            self._last_timestamp_ns = timestamp
        return self._last_timestamp_ns

    @property
    def position_base(self) -> Decimal:
        return Decimal(str(float(self._backtest.position(0))))

    def start(self) -> None:
        if self._started:
            raise RuntimeError("replay session has already started")
        while True:
            result = int(self._backtest.wait_next_feed(False, UNTIL_END_OF_DATA))
            if result == 1:
                raise ValueError("dataset ended before establishing a valid BBO")
            depth = self._backtest.depth(0)
            if np.isfinite(depth.best_bid) and np.isfinite(depth.best_ask):
                break
        self._started = True
        self._record_position()

    def submit_limit(
        self,
        *,
        order_id: int,
        side: OrderSide,
        price: Decimal,
        quantity_base: Decimal,
        post_only: bool = True,
        wait: bool = True,
    ) -> None:
        self._require_active()
        if order_id <= 0 or order_id in self._known_orders:
            raise ValueError("order_id must be positive and unique within a replay")
        if price <= 0 or quantity_base <= 0:
            raise ValueError("price and quantity must be positive")
        tif = GTX if post_only else GTC
        submit = (
            self._backtest.submit_buy_order
            if side is OrderSide.BUY
            else self._backtest.submit_sell_order
        )
        result = int(submit(0, order_id, float(price), float(quantity_base), tif, LIMIT, wait))
        if result != 0:
            raise RuntimeError(f"HftBacktest rejected order submission with code {result}")
        self._known_orders.add(order_id)
        self._executed[order_id] = 0.0
        self._executed_notional[order_id] = 0.0
        self._capture_fills()

    def cancel(self, order_id: int, *, wait: bool = True) -> None:
        self._require_active()
        if order_id not in self._known_orders:
            raise ValueError("cannot cancel an unknown replay order")
        result = int(self._backtest.cancel(0, order_id, wait))
        if result != 0:
            raise RuntimeError(f"HftBacktest rejected cancellation with code {result}")
        self._capture_fills()

    def advance(self, timeout_ns: int = UNTIL_END_OF_DATA) -> bool:
        self._require_active()
        if timeout_ns <= 0:
            raise ValueError("advance timeout must be positive")
        result = int(self._backtest.wait_next_feed(True, timeout_ns))
        if result == 1:
            self._last_timestamp_ns = max(self._last_timestamp_ns, self._dataset_end_ns)
        self._capture_fills()
        if result == 1:
            self._ended = True
            return False
        return True

    def advance_until_end(self) -> None:
        while not self._ended and self.advance():
            pass

    def result(self, funding: tuple[FundingObservation, ...] = ()) -> ReplayResult:
        if not self._started:
            raise RuntimeError("replay session has not started")
        self._capture_fills()
        replay_start = self._positions[0].ts_ns
        replay_end = self._dataset_end_ns
        if any(
            item.settlement_ts_ns < replay_start or item.settlement_ts_ns > replay_end
            for item in funding
        ):
            raise ValueError("funding settlement is outside the replay interval")
        state = self._backtest.state_values(0)
        depth = self._backtest.depth(0)
        mark = Decimal(str((float(depth.best_bid) + float(depth.best_ask)) / 2))
        if not mark.is_finite() or mark <= 0:
            raise RuntimeError("cannot mark replay result without a valid BBO")
        cash_balance = Decimal(str(float(state.balance)))
        exchange_fee = Decimal(str(float(state.fee)))
        ending_position = Decimal(str(float(state.position)))
        slippage = sum(
            (
                fill.quantity_base
                * fill.price
                * self._scenario.taker_slippage_bps
                / Decimal("10000")
                for fill in self._fills
                if not fill.maker
            ),
            Decimal("0"),
        )
        funding_cashflows = calculate_funding_cashflows(
            positions=tuple(self._positions),
            funding=funding,
            multiplier=self._scenario.funding_rate_multiplier,
        )
        funding_total = sum((item.cashflow_usd for item in funding_cashflows), Decimal("0"))
        equity = cash_balance + ending_position * mark - exchange_fee - slippage + funding_total
        return ReplayResult(
            dataset_sha256=self._dataset_sha256,
            scenario_sha256=self._scenario.sha256(),
            ending_position_base=ending_position,
            ending_mark_price=mark,
            cash_balance_usd=cash_balance,
            exchange_fee_usd=exchange_fee,
            explicit_slippage_usd=slippage,
            funding_cashflow_usd=funding_total,
            marked_equity_usd=equity,
            fills=tuple(self._fills),
            funding_cashflows=funding_cashflows,
        )

    def close(self) -> None:
        if not self._closed:
            result = int(self._backtest.close())
            self._closed = True
            if result != 0:
                raise RuntimeError(f"HftBacktest close failed with code {result}")

    def __enter__(self) -> HftReplaySession:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _require_active(self) -> None:
        if not self._started or self._ended or self._closed:
            raise RuntimeError("replay session is not active")

    def _record_position(self) -> None:
        observation = PositionObservation(
            ts_ns=self.current_timestamp_ns,
            position_base=self.position_base,
        )
        if self._positions and self._positions[-1].ts_ns == observation.ts_ns:
            self._positions[-1] = observation
        elif not self._positions or self._positions[-1].position_base != observation.position_base:
            self._positions.append(observation)

    def _capture_fills(self) -> None:
        changed = False
        for order_id in sorted(self._known_orders):
            order = self._backtest.orders(0).get(order_id)
            if order is None:
                continue
            executed = float(order.exec_qty)
            previous = self._executed[order_id]
            increment = executed - previous
            if increment <= 1e-12:
                continue
            if int(order.status) not in {PARTIALLY_FILLED, FILLED}:
                raise RuntimeError("execution quantity changed without a fill status")
            maker = bool(order.arr[0]["maker"])
            total_notional = executed * float(order.exec_price)
            increment_notional = total_notional - self._executed_notional[order_id]
            if increment_notional <= 0:
                raise RuntimeError("fill notional must increase with execution quantity")
            self._fills.append(
                FillObservation(
                    local_ts_ns=self.current_timestamp_ns,
                    order_id=order_id,
                    side="buy" if int(order.side) > 0 else "sell",
                    quantity_base=Decimal(str(increment)),
                    price=Decimal(str(increment_notional / increment)),
                    maker=maker,
                )
            )
            self._executed[order_id] = executed
            self._executed_notional[order_id] = total_notional
            changed = True
        if changed:
            self._record_position()
