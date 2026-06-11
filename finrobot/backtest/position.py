"""Position state and risk-based sizing for the backtester."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class PositionState(str, Enum):
    """Lifecycle state names used by backtest reporting."""

    OPEN = "OPEN"
    CLOSED = "CLOSED"


@dataclass
class Position:
    """Open simulated position."""

    symbol: str
    side: str
    volume: float
    entry_price: float
    entry_time: int
    sl: float
    tp: float
    magic: int
    open_commission: float = 0.0
    open_swap_accrued: float = 0.0
    current_pnl: float = 0.0


def update_position_pnl(
    pos: Position,
    current_bid: float,
    current_ask: float,
    now_epoch: int,
    *,
    swap_per_lot_per_day: float = 0.0,
) -> Position:
    """Return a copy with marked-to-market PnL and accrued swap updated."""

    side = pos.side.upper()
    if side == "BUY":
        price_pnl = (float(current_bid) - pos.entry_price) * pos.volume
    elif side == "SELL":
        price_pnl = (pos.entry_price - float(current_ask)) * pos.volume
    else:
        raise ValueError(f"position side must be BUY or SELL, got {pos.side!r}")

    held_seconds = max(0, int(now_epoch) - int(pos.entry_time))
    swap = float(swap_per_lot_per_day) * pos.volume * held_seconds / 86400.0
    return replace(
        pos,
        open_swap_accrued=swap,
        current_pnl=price_pnl - pos.open_commission + swap,
    )


class PositionSizer:
    """Daily-risk position sizing with simple per-symbol caps."""

    def __init__(
        self,
        *,
        risk_per_trade_fraction: float,
        daily_loss_cap_fraction: float,
        max_lot_per_trade: float,
        max_positions_per_symbol: int,
    ):
        self.risk_per_trade_fraction = float(risk_per_trade_fraction)
        self.daily_loss_cap_fraction = float(daily_loss_cap_fraction)
        self.max_lot_per_trade = float(max_lot_per_trade)
        self.max_positions_per_symbol = int(max_positions_per_symbol)

    def size(
        self,
        *,
        symbol: str,
        equity: float,
        sl_distance: float,
        open_positions: list[Position],
        today_closed_pnl: float,
    ) -> float:
        """Return lot size for a new position, or ``0.0`` when capped."""

        equity_value = float(equity)
        if equity_value <= 0:
            return 0.0
        if float(today_closed_pnl) <= -self.daily_loss_cap_fraction * equity_value:
            return 0.0
        symbol_positions = [
            pos for pos in open_positions if pos.symbol.upper() == symbol.upper()
        ]
        if len(symbol_positions) >= self.max_positions_per_symbol:
            return 0.0
        distance = float(sl_distance)
        if distance <= 0:
            return 0.0
        risk_dollars = self.risk_per_trade_fraction * equity_value
        if risk_dollars <= 0:
            return 0.0
        lot = risk_dollars / distance
        return round(max(0.0, min(self.max_lot_per_trade, lot)), 6)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PositionSizer):
            return NotImplemented
        return (
            self.risk_per_trade_fraction == other.risk_per_trade_fraction
            and self.daily_loss_cap_fraction == other.daily_loss_cap_fraction
            and self.max_lot_per_trade == other.max_lot_per_trade
            and self.max_positions_per_symbol == other.max_positions_per_symbol
        )

    def __repr__(self) -> str:
        return (
            "PositionSizer("
            f"risk_per_trade_fraction={self.risk_per_trade_fraction!r}, "
            f"daily_loss_cap_fraction={self.daily_loss_cap_fraction!r}, "
            f"max_lot_per_trade={self.max_lot_per_trade!r}, "
            f"max_positions_per_symbol={self.max_positions_per_symbol!r})"
        )
