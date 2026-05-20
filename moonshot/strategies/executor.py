"""
Hyperliquid Moonshot Engine - Executor Module
Paper trading with mock 100 USDT for aggressive crypto trading
"""

import json
import time
import logging
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from enum import Enum
import random

import os
os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('logs/executor.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class OrderSide(Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP_MARKET = "stop_market"


@dataclass
class Order:
    symbol: str
    side: OrderSide
    order_type: OrderType
    size: float  # In base currency (BTC, ETH, etc.)
    price: Optional[float] = None  # For limit orders
    stop_price: Optional[float] = None  # For stop orders
    leverage: float = 1.0
    timestamp: float = 0.0
    order_id: str = ""
    status: str = "pending"  # pending, filled, partial, cancelled
    filled_size: float = 0.0
    average_fill_price: float = 0.0
    
    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()
        if not self.order_id:
            self.order_id = f"ORD_{int(self.timestamp * 1000)}_{random.randint(1000, 9999)}"


@dataclass
class Position:
    symbol: str
    side: OrderSide  # BUY = long, SELL = short
    size: float  # Position size in base currency
    entry_price: float
    leverage: float
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    timestamp: float = 0.0
    
    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()
    
    def calculate_unrealized_pnl(self, current_price: float) -> float:
        """Calculate unrealized PnL based on current price"""
        if self.side == OrderSide.BUY:  # Long
            pnl = (current_price - self.entry_price) * self.size
        else:  # Short
            pnl = (self.entry_price - current_price) * self.size
        return pnl
    
    def calculate_liquidation_price(self) -> float:
        """Calculate liquidation price for the position"""
        maintenance_margin = 0.005  # 0.5%
        margin = (self.size * self.entry_price) / self.leverage
        
        if self.side == OrderSide.BUY:  # Long
            liq_price = self.entry_price * (1 - (1 / self.leverage) + maintenance_margin)
        else:  # Short
            liq_price = self.entry_price * (1 + (1 / self.leverage) - maintenance_margin)
        
        return liq_price


class HyperliquidPaperTrading:
    """
    Paper trading engine for Hyperliquid with mock 100 USDT
    Simulates live trading environment without real funds
    """
    
    def __init__(
        self,
        initial_balance: float = 100.0,
        symbols: List[str] = None,
        default_leverage: float = 20.0,
        max_leverage: float = 50.0,
        maker_fee: float = 0.0002,  # 0.02%
        taker_fee: float = 0.0005,  # 0.05%
        data_source: str = "mock",  # mock, hyperliquid, or combined
        spread_bps: float = 1.5,
        slippage_bps: float = 0.5,
        gas_fee_usd: float = 0.02,
    ):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.symbols = symbols or ["BTC-PERP", "ETH-PERP", "SOL-PERP"]
        self.default_leverage = default_leverage
        self.max_leverage = max_leverage
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.data_source = data_source
        self.spread_bps = spread_bps
        self.slippage_bps = slippage_bps
        self.gas_fee_usd = gas_fee_usd
        
        # Trading state
        self.positions: Dict[str, Position] = {}
        self.orders: Dict[str, Order] = {}
        self.order_history: List[Order] = []
        self.trade_history: List[Dict] = []
        self.daily_pnl: List[Dict] = []
        
        # Price tracking
        self.current_prices: Dict[str, float] = {}
        self.price_history: Dict[str, List] = {sym: [] for sym in self.symbols}
        
        # Statistics
        self.stats = {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'total_pnl': 0.0,
            'max_drawdown': 0.0,
            'peak_balance': initial_balance,
            'current_streak': 0,
            'max_consecutive_wins': 0,
            'max_consecutive_losses': 0
        }
        
        # Initialize mock prices
        self._initialize_mock_prices()
        
        logger.info(f"🚀 Hyperliquid Paper Trading Initialized")
        logger.info(f"   Initial Balance: {self.initial_balance} USDT")
        logger.info(f"   Symbols: {', '.join(self.symbols)}")
        logger.info(f"   Default Leverage: {self.default_leverage}x")
        logger.info(f"   Max Leverage: {self.max_leverage}x")
        logger.info(f"   Realistic Costs: spread={self.spread_bps}bps | slippage={self.slippage_bps}bps | gas=${self.gas_fee_usd}")
    
    def _initialize_mock_prices(self):
        """Initialize mock prices for testing"""
        mock_prices = {
            "BTC-PERP": 45000.0,
            "ETH-PERP": 2500.0,
            "SOL-PERP": 95.0
        }
        
        for symbol in self.symbols:
            self.current_prices[symbol] = mock_prices.get(symbol, 100.0)
            self.price_history[symbol] = []
    
    def update_price(self, symbol: str, price: float):
        """Update current price for a symbol"""
        if symbol in self.current_prices:
            old_price = self.current_prices[symbol]
            self.current_prices[symbol] = price
            
            # Store price history
            self.price_history[symbol].append({
                'timestamp': time.time(),
                'price': price
            })
            
            # Keep only last 1000 prices
            if len(self.price_history[symbol]) > 1000:
                self.price_history[symbol] = self.price_history[symbol][-1000:]
            
            # Update position PnL
            self._update_position_pnl(symbol, price)
    
    def _update_position_pnl(self, symbol: str, current_price: float):
        """Update unrealized PnL for positions"""
        if symbol in self.positions:
            position = self.positions[symbol]
            position.unrealized_pnl = position.calculate_unrealized_pnl(current_price)
    
    def get_balance(self) -> float:
        """Get current balance including unrealized PnL"""
        total_unrealized = sum(pos.unrealized_pnl for pos in self.positions.values())
        return self.balance + total_unrealized
    
    def get_available_balance(self) -> float:
        """Get available balance for new positions"""
        total_margin_used = sum(
            (pos.size * pos.entry_price) / pos.leverage
            for pos in self.positions.values()
        )
        return self.balance - total_margin_used
    
    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        size: float,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        leverage: float = None
    ) -> Order:
        """Place a new order"""
        
        leverage = leverage or self.default_leverage
        leverage = min(leverage, self.max_leverage)
        
        # Validate symbol
        if symbol not in self.symbols:
            raise ValueError(f"Symbol {symbol} not supported")
        
        # Get current price
        current_price = self.current_prices.get(symbol, 0.0)
        if current_price == 0.0:
            raise ValueError(f"No price available for {symbol}")
        
        # Calculate order value and margin required
        order_value = size * current_price
        margin_required = order_value / leverage
        
        # Check available balance
        available = self.get_available_balance()
        if margin_required > available:
            raise ValueError(
                f"Insufficient balance. Required: {margin_required:.2f} USDT, "
                f"Available: {available:.2f} USDT"
            )
        
        # Create order
        order = Order(
            symbol=symbol,
            side=side,
            order_type=order_type,
            size=size,
            price=price,
            stop_price=stop_price,
            leverage=leverage
        )
        
        # Execute order immediately (market order simulation)
        if order_type == OrderType.MARKET:
            self._execute_order(order, current_price)
        else:
            # For limit/stop orders, add to pending
            order.status = "pending"
            self.orders[order.order_id] = order
        
        return order
    
    def _execute_order(self, order: Order, fill_price: float):
        """Execute an order and update positions"""
        
        # Apply spread: buy at ask (higher), sell at bid (lower)
        half_spread = fill_price * (self.spread_bps / 10000) / 2
        if order.side == OrderSide.BUY:
            fill_price = fill_price + half_spread
        else:
            fill_price = fill_price - half_spread
        
        # Apply slippage: proportional to order size
        order_value = order.size * fill_price
        slippage_pct = self.slippage_bps / 10000
        slippage_amount = fill_price * slippage_pct
        if order.side == OrderSide.BUY:
            fill_price = fill_price + slippage_amount
        else:
            fill_price = fill_price - slippage_amount
        
        # Calculate fees
        fee_rate = self.taker_fee if order.order_type == OrderType.MARKET else self.maker_fee
        order_value = order.size * fill_price
        fee = order_value * fee_rate
        
        # Deduct fee + gas from balance
        self.balance -= fee
        self.balance -= self.gas_fee_usd
        
        # Update order
        order.status = "filled"
        order.filled_size = order.size
        order.average_fill_price = fill_price
        
        # Update or create position
        self._update_position(order, fill_price)
        
        # Record trade
        trade = {
            'timestamp': time.time(),
            'order_id': order.order_id,
            'symbol': order.symbol,
            'side': order.side.value,
            'size': order.size,
            'price': fill_price,
            'value': order_value,
            'fee': fee,
            'leverage': order.leverage,
            'pnl': 0.0  # Will be updated on close
        }
        
        self.trade_history.append(trade)
        self.stats['total_trades'] += 1
        
        # Log execution
        side_emoji = "🟢" if order.side == OrderSide.BUY else "🔴"
        logger.info(
            f"{side_emoji} ORDER FILLED | {order.symbol} | "
            f"{order.side.value.upper()} {order.size:.6f} @ {fill_price:.2f} | "
            f"Leverage: {order.leverage}x | Fee: {fee:.4f} USDT"
        )
    
    def _update_position(self, order: Order, fill_price: float):
        """Update position after order execution"""
        
        symbol = order.symbol
        
        if symbol not in self.positions:
            # Create new position
            self.positions[symbol] = Position(
                symbol=symbol,
                side=order.side,
                size=order.size,
                entry_price=fill_price,
                leverage=order.leverage,
                timestamp=time.time()
            )
        else:
            # Update existing position
            pos = self.positions[symbol]
            
            if pos.side == order.side:
                # Adding to position
                total_size = pos.size + order.size
                avg_price = ((pos.size * pos.entry_price) + (order.size * fill_price)) / total_size
                pos.size = total_size
                pos.entry_price = avg_price
            else:
                # Reducing or flipping position
                if order.size >= pos.size:
                    # Close and possibly flip
                    pnl = pos.calculate_unrealized_pnl(fill_price)
                    self.balance += pnl
                    
                    remaining = order.size - pos.size
                    if remaining > 0:
                        # Flip to opposite side
                        self.positions[symbol] = Position(
                            symbol=symbol,
                            side=order.side,
                            size=remaining,
                            entry_price=fill_price,
                            leverage=order.leverage,
                            timestamp=time.time()
                        )
                    else:
                        del self.positions[symbol]
                else:
                    # Partial close
                    pnl = ((fill_price - pos.entry_price) * order.size if pos.side == OrderSide.BUY 
                           else (pos.entry_price - fill_price) * order.size)
                    self.balance += pnl
                    pos.size -= order.size
    
    def close_position(self, symbol: str, order_type: OrderType = OrderType.MARKET) -> Optional[Order]:
        """Close an open position"""
        if symbol not in self.positions:
            return None
        
        pos = self.positions[symbol]
        
        # Place closing order
        close_side = OrderSide.SELL if pos.side == OrderSide.BUY else OrderSide.BUY
        
        order = self.place_order(
            symbol=symbol,
            side=close_side,
            size=pos.size,
            order_type=order_type,
            leverage=pos.leverage
        )
        
        # Calculate realized PnL
        current_price = self.current_prices.get(symbol, pos.entry_price)
        realized_pnl = pos.calculate_unrealized_pnl(current_price)
        
        self.stats['total_pnl'] += realized_pnl
        if realized_pnl > 0:
            self.stats['winning_trades'] += 1
            self.stats['current_streak'] = max(1, self.stats['current_streak'] + 1)
            self.stats['max_consecutive_wins'] = max(
                self.stats['max_consecutive_wins'],
                self.stats['current_streak']
            )
        else:
            self.stats['losing_trades'] += 1
            self.stats['current_streak'] = min(-1, self.stats['current_streak'] - 1)
            self.stats['max_consecutive_losses'] = max(
                self.stats['max_consecutive_losses'],
                abs(self.stats['current_streak'])
            )
        
        # Update max drawdown
        current_balance = self.get_balance()
        if current_balance > self.stats['peak_balance']:
            self.stats['peak_balance'] = current_balance
        else:
            drawdown = (self.stats['peak_balance'] - current_balance) / self.stats['peak_balance']
            self.stats['max_drawdown'] = max(self.stats['max_drawdown'], drawdown)
        
        # Remove position (may already be removed by _update_position)
        if symbol in self.positions:
            del self.positions[symbol]
        
        # Log close
        pnl_emoji = "✅" if realized_pnl > 0 else "❌"
        logger.info(
            f"{pnl_emoji} POSITION CLOSED | {symbol} | PnL: {realized_pnl:.4f} USDT | "
            f"Balance: {self.balance:.2f} USDT | Win Rate: {self.get_win_rate():.1f}%"
        )
        
        return order
    
    def get_win_rate(self) -> float:
        """Calculate current win rate"""
        total = self.stats['winning_trades'] + self.stats['losing_trades']
        if total == 0:
            return 0.0
        return (self.stats['winning_trades'] / total) * 100
    
    def get_stats_summary(self) -> Dict:
        """Get comprehensive statistics summary"""
        return {
            'balance': self.get_balance(),
            'available_balance': self.get_available_balance(),
            'initial_balance': self.initial_balance,
            'total_return_pct': ((self.get_balance() - self.initial_balance) / self.initial_balance) * 100,
            'total_trades': self.stats['total_trades'],
            'winning_trades': self.stats['winning_trades'],
            'losing_trades': self.stats['losing_trades'],
            'win_rate': self.get_win_rate(),
            'total_pnl': self.stats['total_pnl'],
            'max_drawdown_pct': self.stats['max_drawdown'] * 100,
            'peak_balance': self.stats['peak_balance'],
            'current_positions': len(self.positions),
            'timestamp': datetime.now().isoformat()
        }
    
    def save_state(self, filepath: str = "hyperliquid_state.json"):
        """Save current state to file"""
        state = {
            'balance': self.balance,
            'initial_balance': self.initial_balance,
            'positions': {
                sym: {
                    'symbol': pos.symbol,
                    'side': pos.side.value,
                    'size': pos.size,
                    'entry_price': pos.entry_price,
                    'leverage': pos.leverage,
                    'unrealized_pnl': pos.unrealized_pnl,
                    'realized_pnl': pos.realized_pnl,
                    'timestamp': pos.timestamp
                }
                for sym, pos in self.positions.items()
            },
            'stats': self.stats,
            'trade_history': self.trade_history[-100:],  # Keep last 100
            'timestamp': time.time()
        }
        
        with open(filepath, 'w') as f:
            json.dump(state, f, indent=2)
        
        logger.debug(f"State saved to {filepath}")
    
    def load_state(self, filepath: str = "hyperliquid_state.json"):
        """Load state from file"""
        try:
            with open(filepath, 'r') as f:
                state = json.load(f)
            
            self.balance = state['balance']
            self.initial_balance = state['initial_balance']
            self.stats = state['stats']
            self.trade_history = state.get('trade_history', [])
            
            # Restore positions
            for sym, pos_data in state['positions'].items():
                self.positions[sym] = Position(
                    symbol=pos_data['symbol'],
                    side=OrderSide.BUY if pos_data['side'] == 'buy' else OrderSide.SELL,
                    size=pos_data['size'],
                    entry_price=pos_data['entry_price'],
                    leverage=pos_data['leverage'],
                    unrealized_pnl=pos_data.get('unrealized_pnl', 0.0),
                    realized_pnl=pos_data.get('realized_pnl', 0.0),
                    timestamp=pos_data['timestamp']
                )
            
            logger.info(f"State loaded from {filepath}")
            logger.info(f"Restored balance: {self.balance:.2f} USDT")
            logger.info(f"Restored positions: {len(self.positions)}")
            
        except FileNotFoundError:
            logger.warning(f"State file not found: {filepath}")
        except Exception as e:
            logger.error(f"Error loading state: {e}")


class HyperliquidDataFeed:
    """
    Simulated and real data feed for Hyperliquid
    Can use mock data, Hyperliquid API, or both
    """
    
    def __init__(
        self,
        symbols: List[str] = None,
        mode: str = "mock",  # mock, api, hybrid
        update_interval: float = 1.0  # seconds
    ):
        self.symbols = symbols or ["BTC-PERP", "ETH-PERP", "SOL-PERP"]
        self.mode = mode
        self.update_interval = update_interval
        self.running = False
        
        # Price data
        self.current_prices: Dict[str, float] = {}
        self.price_history: Dict[str, List[Dict]] = {sym: [] for sym in self.symbols}
        
        # Mock price generators
        self.mock_base_prices = {
            "BTC-PERP": 45000.0,
            "ETH-PERP": 2500.0,
            "SOL-PERP": 95.0,
            "AVAX-PERP": 35.0,
            "LINK-PERP": 18.0
        }
        
        # Initialize
        self._initialize_prices()
    
    def _initialize_prices(self):
        """Initialize starting prices"""
        for symbol in self.symbols:
            base_price = self.mock_base_prices.get(symbol, 100.0)
            # Add some randomness
            self.current_prices[symbol] = base_price * (1 + random.uniform(-0.02, 0.02))
    
    def update_mock_prices(self):
        """Simulate price movements"""
        for symbol in self.symbols:
            current = self.current_prices[symbol]
            
            # Random walk with slight momentum
            change_pct = random.gauss(0, 0.001)  # 0.1% std dev
            
            # Occasional larger moves (breakouts)
            if random.random() < 0.05:  # 5% chance
                change_pct += random.choice([-1, 1]) * random.uniform(0.005, 0.02)
            
            new_price = current * (1 + change_pct)
            
            # Keep within realistic bounds (±10% from base)
            base = self.mock_base_prices.get(symbol, 100.0)
            new_price = max(base * 0.9, min(base * 1.1, new_price))
            
            self.current_prices[symbol] = new_price
            
            # Store history
            self.price_history[symbol].append({
                'timestamp': time.time(),
                'price': new_price
            })
            
            # Keep last 1000 prices
            if len(self.price_history[symbol]) > 1000:
                self.price_history[symbol] = self.price_history[symbol][-1000:]
    
    def get_price(self, symbol: str) -> float:
        """Get current price for symbol"""
        return self.current_prices.get(symbol, 0.0)
    
    def get_price_history(self, symbol: str, lookback: int = 100) -> List[Dict]:
        """Get price history for symbol"""
        history = self.price_history.get(symbol, [])
        return history[-lookback:] if len(history) > lookback else history
    
    def start(self):
        """Start the data feed"""
        self.running = True
        logger.info(f"📊 Data feed started in {self.mode} mode")
        
        # Start price update loop in a separate thread
        import threading
        self._price_thread = threading.Thread(target=self._price_loop, daemon=True)
        self._price_thread.start()
    
    def stop(self):
        """Stop the data feed"""
        self.running = False
        logger.info("📊 Data feed stopped")
    
    def _price_loop(self):
        """Price update loop"""
        while self.running:
            try:
                if self.mode in ["mock", "hybrid"]:
                    self.update_mock_prices()
                time.sleep(self.update_interval)
            except Exception as e:
                logger.error(f"Error in price loop: {e}")
                time.sleep(1)


# Convenience function to create a paper trading instance
def create_paper_trading_system(
    initial_balance: float = 100.0,
    symbols: List[str] = None,
    default_leverage: float = 10.0
) -> Tuple[HyperliquidPaperTrading, HyperliquidDataFeed]:
    """
    Create a complete paper trading system
    
    Args:
        initial_balance: Starting balance (default 100 USDT)
        symbols: List of trading pairs
        default_leverage: Default leverage to use
    
    Returns:
        Tuple of (trading_engine, data_feed)
    """
    symbols = symbols or ["BTC-PERP", "ETH-PERP", "SOL-PERP"]
    
    # Create trading engine
    engine = HyperliquidPaperTrading(
        initial_balance=initial_balance,
        symbols=symbols,
        default_leverage=default_leverage,
        max_leverage=50.0
    )
    
    # Create data feed
    data_feed = HyperliquidDataFeed(
        symbols=symbols,
        mode="mock",  # Start with mock data
        update_interval=1.0
    )
    
    # Link engine to data feed prices
    for symbol in symbols:
        engine.current_prices[symbol] = data_feed.get_price(symbol)
    
    logger.info("=" * 60)
    logger.info("🚀 HYPERLIQUID MOONSHOT ENGINE INITIALIZED")
    logger.info("=" * 60)
    logger.info(f"💰 Initial Balance: {initial_balance} USDT")
    logger.info(f"📊 Trading Pairs: {', '.join(symbols)}")
    logger.info(f"⚡ Default Leverage: {default_leverage}x")
    logger.info(f"🎯 Target: Grow to 1000+ USDT ASAP")
    logger.info("=" * 60)
    
    return engine, data_feed


if __name__ == "__main__":
    # Test the paper trading system
    engine, feed = create_paper_trading_system()
    
    # Start data feed
    feed.start()
    
    try:
        # Run for a bit
        time.sleep(3)
        
        # Test placing an order
        logger.info("\n🧪 Testing order placement...")
        order = engine.place_order(
            symbol="BTC-PERP",
            side=OrderSide.BUY,
            size=0.01,  # Small test size
            leverage=10.0
        )
        
        # Let it run
        time.sleep(5)
        
        # Show stats
        stats = engine.get_stats_summary()
        logger.info(f"\n📊 Current Stats:")
        logger.info(f"   Balance: {stats['balance']:.2f} USDT")
        logger.info(f"   Positions: {stats['current_positions']}")
        logger.info(f"   Win Rate: {stats['win_rate']:.1f}%")
        
        # Close position
        if "BTC-PERP" in engine.positions:
            logger.info("\n🔒 Closing position...")
            engine.close_position("BTC-PERP")
        
    except KeyboardInterrupt:
        logger.info("\n⛔ Stopping...")
    finally:
        feed.stop()
        engine.save_state()
        logger.info("✅ Test complete. State saved.")