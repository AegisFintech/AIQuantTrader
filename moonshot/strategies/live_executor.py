"""
Hyperliquid Live Trading Executor
Drops-in replacement for HyperliquidPaperTrading when TRADING_MODE=live
Uses the official Hyperliquid Python SDK for real on-chain order execution.
"""

import time
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional
from datetime import datetime
from enum import Enum

from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

from moonshot.strategies.executor import (
    OrderSide, OrderType, Order, Position
)

logger = logging.getLogger(__name__)


class HyperliquidLiveTrading:
    def __init__(
        self,
        private_key: str,
        wallet_address: str,
        network: str = "mainnet",
        initial_balance: float = 100.0,
        symbols: List[str] = None,
        default_leverage: float = 5.0,
        max_leverage: float = 5.0,
        max_position_usd: float = 50.0,
    ):
        self.private_key = private_key
        self.wallet_address = wallet_address
        self.network = network
        self.initial_balance = initial_balance
        self.symbols = symbols or ["BTC-PERP", "ETH-PERP", "SOL-PERP"]
        self.default_leverage = default_leverage
        self.max_leverage = max_leverage
        self.max_position_usd = max_position_usd

        if network == "testnet":
            api_url = constants.TESTNET_API_URL
        else:
            api_url = constants.MAINNET_API_URL

        self.info = Info(api_url)
        self.exchange = Exchange(wallet_address, private_key, api_url)

        self.positions: Dict[str, Position] = {}
        self.orders: Dict[str, Order] = {}
        self.order_history: List[Order] = []
        self.trade_history: List[Dict] = []

        self.current_prices: Dict[str, float] = {}

        self.stats = {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "total_pnl": 0.0,
            "max_drawdown": 0.0,
            "peak_balance": initial_balance,
            "current_streak": 0,
            "max_consecutive_wins": 0,
            "max_consecutive_losses": 0,
        }

        self._sync_leverage()
        self.balance = self._fetch_balance()
        logger.info(f"LIVE Hyperliquid Trading Initialized")
        logger.info(f"   Wallet: {wallet_address[:8]}...{wallet_address[-6:]}")
        logger.info(f"   Network: {network}")
        logger.info(f"   Balance: {self.balance:.2f} USDT")
        logger.info(f"   Symbols: {', '.join(self.symbols)}")
        logger.info(f"   Max Leverage: {max_leverage}x | Max Position: ${max_position_usd}")

    def _sync_leverage(self):
        for symbol in self.symbols:
            coin = symbol.replace("-PERP", "")
            try:
                lev_result = self.exchange.update_leverage(
                    int(self.default_leverage), coin, is_cross=True
                )
                logger.debug(f"Leverage sync {coin}: {lev_result}")
            except Exception as e:
                logger.warning(f"Failed to sync leverage for {coin}: {e}")

    def _fetch_balance(self) -> float:
        try:
            user_state = self.info.user_state(self.wallet_address)
            margin = user_state.get("marginSummary", {})
            acct_val = float(margin.get("accountValue", 0))
            return acct_val
        except Exception as e:
            logger.error(f"Failed to fetch balance: {e}")
            return 0.0

    def _fetch_positions(self) -> Dict[str, dict]:
        try:
            user_state = self.info.user_state(self.wallet_address)
            asset_positions = user_state.get("assetPositions", [])
            result = {}
            for ap in asset_positions:
                pos = ap.get("position", {})
                coin = pos.get("coin", "")
                symbol = f"{coin}-PERP"
                if symbol in self.symbols:
                    result[symbol] = pos
            return result
        except Exception as e:
            logger.error(f"Failed to fetch positions: {e}")
            return {}

    def update_price(self, symbol: str, price: float):
        self.current_prices[symbol] = price
        if symbol in self.positions:
            self.positions[symbol].unrealized_pnl = (
                self.positions[symbol].calculate_unrealized_pnl(price)
            )

    def get_balance(self) -> float:
        self.balance = self._fetch_balance()
        total_unrealized = sum(
            pos.unrealized_pnl for pos in self.positions.values()
        )
        return self.balance + total_unrealized

    def get_available_balance(self) -> float:
        self.balance = self._fetch_balance()
        total_margin = sum(
            (pos.size * pos.entry_price) / pos.leverage
            for pos in self.positions.values()
        )
        return self.balance - total_margin

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        size: float,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        leverage: float = None,
    ) -> Order:
        leverage = min(leverage or self.default_leverage, self.max_leverage)
        coin = symbol.replace("-PERP", "")
        current_price = self.current_prices.get(symbol, 0)
        if current_price == 0:
            raise ValueError(f"No price available for {symbol}")

        order_value = size * current_price
        if order_value > self.max_position_usd:
            size = self.max_position_usd / current_price
            logger.info(f"  Position size capped to ${self.max_position_usd} for {coin}")

        is_buy = side == OrderSide.BUY

        try:
            result = self.exchange.market_open(coin, is_buy, size)
            status = result.get("status", "")
            if status == "ok":
                fill_data = result.get("response", {}).get("data", {}).get("statuses", [{}])
                if fill_data and "filled" in fill_data[0]:
                    fill_info = fill_data[0]["filled"]
                    fill_price = float(fill_info.get("avgPx", current_price))
                    fill_size = float(fill_info.get("totalSz", size))
                else:
                    fill_price = current_price
                    fill_size = size

                fee = order_value * 0.0005
                self.balance -= fee

                order = Order(
                    symbol=symbol, side=side, order_type=order_type,
                    size=fill_size, price=fill_price, leverage=leverage,
                    status="filled", filled_size=fill_size,
                    average_fill_price=fill_price,
                )
                self.order_history.append(order)
                self._update_position(order, fill_price)

                side_emoji = "\U0001f7e2" if is_buy else "\U0001f534"
                logger.info(
                    f"{side_emoji} LIVE ORDER FILLED | {symbol} | "
                    f"{side.value.upper()} {fill_size:.6f} @ {fill_price:.2f} | "
                    f"Lev: {leverage}x | Fee: ~{fee:.4f} USDT"
                )
                return order
            else:
                err_msg = result.get("response", {}).get("data", {}).get("statuses", [{}])[0].get("error", "unknown")
                logger.error(f"LIVE ORDER REJECTED | {symbol} | {err_msg}")
                raise ValueError(f"Order rejected: {err_msg}")

        except Exception as e:
            if "Order rejected" in str(e):
                raise
            logger.error(f"LIVE ORDER ERROR | {symbol} | {e}")
            raise

    def _update_position(self, order: Order, fill_price: float):
        symbol = order.symbol
        if symbol not in self.positions:
            self.positions[symbol] = Position(
                symbol=symbol, side=order.side, size=order.size,
                entry_price=fill_price, leverage=order.leverage,
                timestamp=time.time(),
            )
        else:
            pos = self.positions[symbol]
            if pos.side == order.side:
                total = pos.size + order.size
                avg = ((pos.size * pos.entry_price) + (order.size * fill_price)) / total
                pos.size = total
                pos.entry_price = avg
            else:
                pnl = pos.calculate_unrealized_pnl(fill_price)
                self.balance += pnl
                self.stats["total_pnl"] += pnl
                if pnl > 0:
                    self.stats["winning_trades"] += 1
                else:
                    self.stats["losing_trades"] += 1
                remaining = order.size - pos.size
                if remaining > 0:
                    self.positions[symbol] = Position(
                        symbol=symbol, side=order.side, size=remaining,
                        entry_price=fill_price, leverage=order.leverage,
                        timestamp=time.time(),
                    )
                elif remaining == 0:
                    del self.positions[symbol]
                else:
                    pos.size -= order.size

    def close_position(self, symbol: str, order_type: OrderType = OrderType.MARKET) -> Optional[Order]:
        if symbol not in self.positions:
            return None

        pos = self.positions[symbol]
        coin = symbol.replace("-PERP", "")
        current_price = self.current_prices.get(symbol, pos.entry_price)
        is_buy = pos.side != OrderSide.BUY

        try:
            result = self.exchange.market_close(coin, is_buy, pos.size)
            status = result.get("status", "")

            if status == "ok":
                realized = pos.calculate_unrealized_pnl(current_price)
                self.balance += realized
                self.stats["total_pnl"] += realized
                if realized > 0:
                    self.stats["winning_trades"] += 1
                    self.stats["current_streak"] = max(1, self.stats["current_streak"] + 1)
                else:
                    self.stats["losing_trades"] += 1
                    self.stats["current_streak"] = min(-1, self.stats["current_streak"] - 1)

                cur_bal = self.get_balance()
                if cur_bal > self.stats["peak_balance"]:
                    self.stats["peak_balance"] = cur_bal
                else:
                    dd = (self.stats["peak_balance"] - cur_bal) / self.stats["peak_balance"]
                    self.stats["max_drawdown"] = max(self.stats["max_drawdown"], dd)

                del self.positions[symbol]

                emoji = "\u2705" if realized > 0 else "\u274c"
                logger.info(
                    f"{emoji} LIVE POSITION CLOSED | {symbol} | PnL: {realized:.4f} USDT | "
                    f"Balance: {self.balance:.2f} USDT | Win Rate: {self.get_win_rate():.1f}%"
                )

                close_side = OrderSide.SELL if pos.side == OrderSide.BUY else OrderSide.BUY
                return Order(
                    symbol=symbol, side=close_side, order_type=order_type,
                    size=pos.size, price=current_price, leverage=pos.leverage,
                    status="filled", filled_size=pos.size,
                    average_fill_price=current_price,
                )
            else:
                err = result.get("response", {}).get("data", {}).get("statuses", [{}])[0].get("error", "unknown")
                logger.error(f"LIVE CLOSE REJECTED | {symbol} | {err}")
                return None

        except Exception as e:
            logger.error(f"LIVE CLOSE ERROR | {symbol} | {e}")
            return None

    def get_win_rate(self) -> float:
        total = self.stats["winning_trades"] + self.stats["losing_trades"]
        return (self.stats["winning_trades"] / total * 100) if total > 0 else 0.0

    def get_stats_summary(self) -> Dict:
        return {
            "balance": self.get_balance(),
            "available_balance": self.get_available_balance(),
            "initial_balance": self.initial_balance,
            "total_return_pct": ((self.get_balance() - self.initial_balance) / self.initial_balance) * 100,
            "total_trades": self.stats["total_trades"],
            "winning_trades": self.stats["winning_trades"],
            "losing_trades": self.stats["losing_trades"],
            "win_rate": self.get_win_rate(),
            "total_pnl": self.stats["total_pnl"],
            "max_drawdown_pct": self.stats["max_drawdown"] * 100,
            "peak_balance": self.stats["peak_balance"],
            "current_positions": len(self.positions),
            "timestamp": datetime.now().isoformat(),
            "mode": "LIVE",
        }
