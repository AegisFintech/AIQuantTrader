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
    break_even_applied: bool = False


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
        smc_score: int = 0,
    ) -> float:
        """Return lot size for a new position, or ``0.0`` when capped."""

        equity_value = float(equity)
        if not self._can_open(
            symbol=symbol,
            equity=equity_value,
            open_positions=open_positions,
            today_closed_pnl=today_closed_pnl,
        ):
            return 0.0
        distance = float(sl_distance)
        if distance <= 0:
            return 0.0
        risk_dollars = self.risk_per_trade_fraction * equity_value
        if risk_dollars <= 0:
            return 0.0
        lot = risk_dollars / distance
        return round(max(0.0, min(self.max_lot_per_trade, lot)), 6)

    def _can_open(
        self,
        *,
        symbol: str,
        equity: float,
        open_positions: list[Position],
        today_closed_pnl: float,
    ) -> bool:
        if float(equity) <= 0:
            return False
        if float(today_closed_pnl) <= -self.daily_loss_cap_fraction * float(equity):
            return False
        symbol_positions = [
            pos for pos in open_positions if pos.symbol.upper() == symbol.upper()
        ]
        return len(symbol_positions) < self.max_positions_per_symbol

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        assert isinstance(other, PositionSizer)
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


class DailyRiskSizer(PositionSizer):
    """SMC-aware daily risk sizing that mirrors the MT5 bridge volume path."""

    def __init__(
        self,
        *,
        risk_per_trade_fraction: float,
        daily_loss_cap_fraction: float,
        max_lot_per_trade: float,
        max_positions_per_symbol: int,
        max_lot_per_symbol: dict[str, float] | None = None,
        high_confluence_lot_multiplier: float = 3.0,
        high_confluence_score: int = 5,
        max_effective_risk_fraction: float = 0.01,
        bad_day_downshift_fraction: float = 1.0,
        lot_digits: int = 2,
    ):
        super().__init__(
            risk_per_trade_fraction=risk_per_trade_fraction,
            daily_loss_cap_fraction=daily_loss_cap_fraction,
            max_lot_per_trade=max_lot_per_trade,
            max_positions_per_symbol=max_positions_per_symbol,
        )
        self.max_lot_per_symbol = {
            str(symbol).upper(): float(max_lot)
            for symbol, max_lot in (max_lot_per_symbol or {}).items()
        }
        self.high_confluence_lot_multiplier = float(high_confluence_lot_multiplier)
        self.high_confluence_score = int(high_confluence_score)
        self.max_effective_risk_fraction = max(
            0.0,
            float(max_effective_risk_fraction),
        )
        self.bad_day_downshift_fraction = max(
            0.0,
            min(1.0, float(bad_day_downshift_fraction)),
        )
        self.lot_digits = int(lot_digits)

    def size(
        self,
        *,
        symbol: str,
        equity: float,
        sl_distance: float,
        open_positions: list[Position],
        today_closed_pnl: float,
        smc_score: int = 0,
    ) -> float:
        """Return the risk lot, boosted for high SMC confluence, or ``0.0``."""

        equity_value = float(equity)
        if not self._can_open(
            symbol=symbol,
            equity=equity_value,
            open_positions=open_positions,
            today_closed_pnl=today_closed_pnl,
        ):
            return 0.0

        distance = float(sl_distance)
        if distance <= 0:
            return 0.0

        risk_dollars = self.risk_per_trade_fraction * equity_value
        if risk_dollars <= 0:
            return 0.0
        if int(smc_score) >= self.high_confluence_score:
            risk_dollars *= self.high_confluence_lot_multiplier
        risk_dollars = min(
            risk_dollars,
            self.max_effective_risk_fraction * equity_value,
        )
        if float(today_closed_pnl) < 0.0:
            risk_dollars *= self.bad_day_downshift_fraction
        if risk_dollars <= 0:
            return 0.0

        volume = risk_dollars / distance

        max_lot = self.max_lot_per_symbol.get(
            str(symbol).upper(),
            self.max_lot_per_trade,
        )
        rounded = round(max(0.0, min(float(max_lot), volume)), self.lot_digits)
        return rounded if rounded > 0.0 else 0.0

    def __eq__(self, other: object) -> bool:
        if type(other) is not type(self):
            return NotImplemented
        assert isinstance(other, DailyRiskSizer)
        return (
            super().__eq__(other)
            and self.max_lot_per_symbol == other.max_lot_per_symbol
            and self.high_confluence_lot_multiplier
            == other.high_confluence_lot_multiplier
            and self.high_confluence_score == other.high_confluence_score
            and self.max_effective_risk_fraction
            == other.max_effective_risk_fraction
            and self.bad_day_downshift_fraction == other.bad_day_downshift_fraction
            and self.lot_digits == other.lot_digits
        )

    def __repr__(self) -> str:
        return (
            "DailyRiskSizer("
            f"risk_per_trade_fraction={self.risk_per_trade_fraction!r}, "
            f"daily_loss_cap_fraction={self.daily_loss_cap_fraction!r}, "
            f"max_lot_per_trade={self.max_lot_per_trade!r}, "
            f"max_positions_per_symbol={self.max_positions_per_symbol!r}, "
            f"max_lot_per_symbol={self.max_lot_per_symbol!r}, "
            "smc_score='size-time input', "
            f"high_confluence_lot_multiplier="
            f"{self.high_confluence_lot_multiplier!r}, "
            f"high_confluence_score={self.high_confluence_score!r}, "
            f"max_effective_risk_fraction="
            f"{self.max_effective_risk_fraction!r}, "
            f"bad_day_downshift_fraction={self.bad_day_downshift_fraction!r}, "
            f"lot_digits={self.lot_digits!r})"
        )
