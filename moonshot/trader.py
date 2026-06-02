"""
Hyperliquid Moonshot Engine - Main Trading Loop
YOLO-mode aggressive trading to grow 100 USDT to 1000+ USDT
"""

import time
import json
import logging
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import pandas as pd

from moonshot.strategies.executor import (
    HyperliquidPaperTrading, HyperliquidDataFeed,
    OrderSide, OrderType, create_paper_trading_system
)
from moonshot.strategies.strategies import (
    AggressiveADXScalper, AggressiveCryptoScalper,
    BreakoutHunter, MeanReversionBandit,
    AvellanedaStoikovStrategy,
    TradingSignal, SignalType
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('logs/trader.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class HyperliquidMoonshotTrader:
    """
    Main trading engine for Hyperliquid Moonshot
    Manages strategies, executes trades, tracks performance
    """
    
    def __init__(
        self,
        initial_balance: float = 100.0,
        symbols: List[str] = None,
        max_open_positions: int = 5,
        check_interval: float = 10.0,  # Check for signals every 10 seconds
        data_lookback: int = 100  # Price history to keep
    ):
        self.initial_balance = initial_balance
        self.symbols = symbols or ["BTC-PERP", "ETH-PERP", "SOL-PERP", "AVAX-PERP", "LINK-PERP"]
        self.max_open_positions = max_open_positions
        self.check_interval = check_interval
        self.data_lookback = data_lookback
        
        # Initialize trading engine and data feed
        self.engine, self.feed = create_paper_trading_system(
            initial_balance=initial_balance,
            symbols=self.symbols
        )
        
        # Initialize strategies
        self.strategies = self._initialize_strategies()
        
        # Price data storage
        self.ohlcv_data: Dict[str, pd.DataFrame] = {}
        self._initialize_data_storage()
        
        # Trading state
        self.running = False
        self.trade_thread: Optional[threading.Thread] = None
        self.position_monitor_thread: Optional[threading.Thread] = None
        
        # Performance tracking
        self.daily_stats = {
            'date': datetime.now().date(),
            'starting_balance': initial_balance,
            'current_balance': initial_balance,
            'trades_today': 0,
            'wins_today': 0,
            'losses_today': 0,
            'pnl_today': 0.0
        }
        
        # Active signal tracking
        self.active_signals: Dict[str, TradingSignal] = {}
        self.signal_cooldown: Dict[str, float] = {}
        
        logger.info("=" * 70)
        logger.info("🚀 HYPERLIQUID MOONSHOT TRADER INITIALIZED")
        logger.info("=" * 70)
        logger.info(f"💰 Initial Balance: {initial_balance} USDT")
        logger.info(f"📊 Symbols: {', '.join(self.symbols)}")
        logger.info(f"⚡ Max Positions: {max_open_positions}")
        logger.info(f"🎯 Strategies: {len(self.strategies)} active")
        logger.info("=" * 70)
    
    def _initialize_strategies(self) -> Dict[str, Any]:
        """Initialize all trading strategies"""
        strategies = {
            'adx_scalper': AggressiveADXScalper(
                adx_period=14,
                adx_threshold=25.0,
                ema_fast=9,
                ema_slow=21,
                risk_reward=2.0,
                max_leverage=20.0
            ),
            'crypto_scalper': AggressiveCryptoScalper(
                ema_fast=3,
                ema_slow=8,
                rsi_period=7,
                rsi_overbought=70,
                rsi_oversold=30,
                volume_threshold=1.3,
                max_leverage=50.0
            ),
            'breakout_hunter': BreakoutHunter(
                lookback_period=20,
                consolidation_threshold=0.03,
                volume_multiplier=2.0,
                breakout_confirmation=0.005,
                max_leverage=30.0
            ),
            'mean_reversion': MeanReversionBandit(
                bb_period=20,
                bb_std_dev=2.5,
                rsi_period=14,
                rsi_overbought=75,
                rsi_oversold=25,
                adx_filter_max=20.0,
                max_leverage=15.0
            ),
            'avellaneda_stoikov': AvellanedaStoikovStrategy(
                gamma=0.1,
                kappa=1.5,
                max_leverage=10.0
            )
        }
        
        return strategies
    
    def _initialize_data_storage(self):
        """Initialize OHLCV data storage for each symbol"""
        for symbol in self.symbols:
            self.ohlcv_data[symbol] = pd.DataFrame(
                columns=['timestamp', 'open', 'high', 'low', 'close', 'volume']
            )
    
    def update_price_data(self, symbol: str, price_data: Dict):
        """Update price data for a symbol"""
        if symbol not in self.ohlcv_data:
            return
        
        # Create new row
        new_row = pd.DataFrame([{
            'timestamp': price_data.get('timestamp', pd.Timestamp.now()),
            'open': price_data.get('open', price_data.get('price', 0)),
            'high': price_data.get('high', price_data.get('price', 0)),
            'low': price_data.get('low', price_data.get('price', 0)),
            'close': price_data.get('close', price_data.get('price', 0)),
            'volume': price_data.get('volume', 0)
        }])
        
        # Append and maintain size
        df = self.ohlcv_data[symbol]
        df = pd.concat([df, new_row], ignore_index=True)
        
        if len(df) > self.data_lookback:
            df = df.iloc[-self.data_lookback:]
        
        self.ohlcv_data[symbol] = df
    
    def evaluate_strategies(
        self,
        symbol: str,
        current_price: float,
        account_balance: float
    ) -> List[TradingSignal]:
        """Run all strategies and collect signals"""
        
        signals = []
        ohlcv = self.ohlcv_data.get(symbol)
        
        if ohlcv is None or len(ohlcv) < 30:
            return signals
        
        # Check cooldown
        last_signal_time = self.signal_cooldown.get(symbol, 0)
        if time.time() - last_signal_time < 60:  # 1 minute cooldown per symbol
            return signals
        
        # Run each strategy
        for strategy_name, strategy in self.strategies.items():
            try:
                signal = strategy.generate_signal(
                    symbol=symbol,
                    ohlcv=ohlcv,
                    current_price=current_price,
                    account_balance=account_balance
                )
                
                if signal and signal.confidence > 0.6:  # Only high confidence signals
                    signal.rationale = f"[{strategy_name}] {signal.rationale}"
                    signals.append(signal)
                    
            except Exception as e:
                logger.error(f"Error in strategy {strategy_name}: {e}")
        
        # Update cooldown if we got signals
        if signals:
            self.signal_cooldown[symbol] = time.time()
        
        return signals
    
    def execute_signal(self, signal: TradingSignal) -> bool:
        """Execute a trading signal"""
        
        try:
            # Check if we already have a position for this symbol
            if signal.symbol in self.engine.positions:
                logger.debug(f"Already have position in {signal.symbol}, skipping")
                return False
            
            # Check position limit
            if len(self.engine.positions) >= self.max_open_positions:
                logger.debug(f"Max positions reached ({self.max_open_positions}), skipping")
                return False
            
            # Determine order side
            side = OrderSide.BUY if signal.signal_type == SignalType.BUY else OrderSide.SELL
            
            # Execute order
            order = self.engine.place_order(
                symbol=signal.symbol,
                side=side,
                size=signal.suggested_size,
                order_type=OrderType.MARKET,
                leverage=signal.suggested_leverage
            )
            
            # Store signal info
            self.active_signals[signal.symbol] = signal
            
            logger.info(
                f"✅ SIGNAL EXECUTED | {signal.symbol} | "
                f"{signal.signal_type.value.upper()} | "
                f"Leverage: {signal.suggested_leverage}x | "
                f"Confidence: {signal.confidence:.1%}"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Error executing signal: {e}")
            return False
    
    def monitor_positions(self):
        """Monitor and manage open positions"""
        
        while self.running:
            try:
                for symbol, position in list(self.engine.positions.items()):
                    current_price = self.engine.current_prices.get(symbol, 0.0)
                    
                    if current_price == 0.0:
                        continue
                    
                    # Check for stop loss or take profit
                    signal = self.active_signals.get(symbol)
                    
                    if signal:
                        # Check stop loss
                        if position.side == OrderSide.BUY:  # Long
                            if current_price <= signal.stop_loss:
                                logger.info(f"🛑 STOP LOSS HIT | {symbol} @ {current_price:.2f}")
                                self.engine.close_position(symbol)
                                if symbol in self.active_signals:
                                    del self.active_signals[symbol]
                                    
                            elif current_price >= signal.take_profit:
                                logger.info(f"🎯 TAKE PROFIT HIT | {symbol} @ {current_price:.2f}")
                                self.engine.close_position(symbol)
                                if symbol in self.active_signals:
                                    del self.active_signals[symbol]
                        
                        else:  # Short
                            if current_price >= signal.stop_loss:
                                logger.info(f"🛑 STOP LOSS HIT | {symbol} @ {current_price:.2f}")
                                self.engine.close_position(symbol)
                                if symbol in self.active_signals:
                                    del self.active_signals[symbol]
                                    
                            elif current_price <= signal.take_profit:
                                logger.info(f"🎯 TAKE PROFIT HIT | {symbol} @ {current_price:.2f}")
                                self.engine.close_position(symbol)
                                if symbol in self.active_signals:
                                    del self.active_signals[symbol]
                
                # Check for daily reset
                self._check_daily_reset()
                
                time.sleep(5)  # Check every 5 seconds
                
            except Exception as e:
                logger.error(f"Error in position monitor: {e}")
                time.sleep(5)
    
    def _check_daily_reset(self):
        """Check and reset daily statistics"""
        today = datetime.now().date()
        
        if today != self.daily_stats['date']:
            # Log yesterday's stats
            logger.info("=" * 70)
            logger.info(f"📊 DAILY SUMMARY - {self.daily_stats['date']}")
            logger.info(f"   Starting Balance: {self.daily_stats['starting_balance']:.2f} USDT")
            logger.info(f"   Ending Balance: {self.daily_stats['current_balance']:.2f} USDT")
            logger.info(f"   PnL: {self.daily_stats['pnl_today']:.2f} USDT ({(self.daily_stats['pnl_today']/self.daily_stats['starting_balance']*100):.1f}%)")
            logger.info(f"   Trades: {self.daily_stats['trades_today']} | Wins: {self.daily_stats['wins_today']} | Losses: {self.daily_stats['losses_today']}")
            logger.info("=" * 70)
            
            # Reset for new day
            current_balance = self.engine.get_balance()
            self.daily_stats = {
                'date': today,
                'starting_balance': current_balance,
                'current_balance': current_balance,
                'trades_today': 0,
                'wins_today': 0,
                'losses_today': 0,
                'pnl_today': 0.0
            }
    
    def trading_loop(self):
        """Main trading loop"""
        
        logger.info("🔥 MOONSHOT TRADING LOOP STARTED")
        
        iteration = 0
        
        while self.running:
            try:
                iteration += 1
                
                # Update price data for each symbol
                for symbol in self.symbols:
                    current_price = self.feed.get_price(symbol)
                    if current_price > 0:
                        # Create price data dict
                        price_data = {
                            'timestamp': time.time(),
                            'price': current_price,
                            'open': current_price,
                            'high': current_price,
                            'low': current_price,
                            'close': current_price,
                            'volume': 1000  # Mock volume
                        }
                        self.update_price_data(symbol, price_data)
                
                # Evaluate strategies for each symbol
                current_balance = self.engine.get_balance()
                
                for symbol in self.symbols:
                    # Skip if max positions reached
                    if len(self.engine.positions) >= self.max_open_positions:
                        break
                    
                    # Skip if already have position in this symbol
                    if symbol in self.engine.positions:
                        continue
                    
                    current_price = self.feed.get_price(symbol)
                    if current_price == 0.0:
                        continue
                    
                    # Get signals from strategies
                    signals = self.evaluate_strategies(symbol, current_price, current_balance)
                    
                    # Execute the best signal (highest confidence)
                    if signals:
                        best_signal = max(signals, key=lambda s: s.confidence)
                        
                        if best_signal.confidence >= 0.65:  # Only execute high confidence
                            self.execute_signal(best_signal)
                
                # Log status every 10 iterations
                if iteration % 10 == 0:
                    self._log_status()
                
                # Save state periodically
                if iteration % 100 == 0:
                    self.engine.save_state()
                
                time.sleep(self.check_interval)
                
            except Exception as e:
                logger.error(f"Error in trading loop: {e}")
                time.sleep(self.check_interval)
    
    def _log_status(self):
        """Log current system status"""
        stats = self.engine.get_stats_summary()
        
        # Create status line
        status = (
            f"[BAL: {stats['balance']:.2f} USDT | "
            f"RET: {stats['total_return_pct']:+.1f}% | "
            f"POS: {stats['current_positions']}/{self.max_open_positions} | "
            f"TRADES: {stats['total_trades']} | "
            f"WR: {stats['win_rate']:.0f}% | "
            f"DD: {stats['max_drawdown_pct']:.1f}%]"
        )
        
        logger.info(status)
    
    def start(self):
        """Start the trading system"""
        if self.running:
            logger.warning("Trading system is already running")
            return
        
        self.running = True
        
        # Start data feed
        self.feed.start()
        
        # Start position monitor
        self.position_monitor_thread = threading.Thread(
            target=self.monitor_positions,
            daemon=True
        )
        self.position_monitor_thread.start()
        
        # Start trading loop
        self.trade_thread = threading.Thread(
            target=self.trading_loop,
            daemon=True
        )
        self.trade_thread.start()
        
        logger.info("=" * 70)
        logger.info("🔥 HYPERLIQUID MOONSHOT ENGINE STARTED")
        logger.info("=" * 70)
        logger.info("💰 Growing 100 USDT → 1000+ USDT by any means necessary")
        logger.info("⚡ Aggressive scalping, breakouts, and mean reversion")
        logger.info("🎯 Target: 10-20% daily returns")
        logger.info("=" * 70)
    
    def stop(self):
        """Stop the trading system"""
        if not self.running:
            return
        
        logger.info("⛔ Stopping trading system...")
        self.running = False
        
        # Stop feed
        self.feed.stop()
        
        # Wait for threads
        if self.trade_thread and self.trade_thread.is_alive():
            self.trade_thread.join(timeout=5)
        
        if self.position_monitor_thread and self.position_monitor_thread.is_alive():
            self.position_monitor_thread.join(timeout=5)
        
        # Save state
        self.engine.save_state()
        
        # Log final stats
        stats = self.engine.get_stats_summary()
        logger.info("=" * 70)
        logger.info("📊 FINAL STATISTICS")
        logger.info("=" * 70)
        logger.info(f"Final Balance: {stats['balance']:.2f} USDT")
        logger.info(f"Total Return: {stats['total_return_pct']:+.1f}%")
        logger.info(f"Total Trades: {stats['total_trades']}")
        logger.info(f"Win Rate: {stats['win_rate']:.1f}%")
        logger.info(f"Max Drawdown: {stats['max_drawdown_pct']:.1f}%")
        logger.info("=" * 70)
        
        logger.info("✅ Trading system stopped. State saved.")
    
    def get_status(self) -> Dict:
        """Get current system status"""
        return {
            'running': self.running,
            'timestamp': datetime.now().isoformat(),
            'engine_stats': self.engine.get_stats_summary(),
            'daily_stats': self.daily_stats,
            'open_positions': len(self.engine.positions),
            'active_signals': len(self.active_signals),
            'symbols_tracked': len(self.symbols)
        }


def run_moonshot_demo(duration_minutes: float = 5.0):
    """
    Run a demo of the Hyperliquid Moonshot engine
    
    Args:
        duration_minutes: How long to run the demo
    """
    
    logger.info("=" * 70)
    logger.info("🚀 HYPERLIQUID MOONSHOT DEMO")
    logger.info("=" * 70)
    
    # Create and start trader
    trader = HyperliquidMoonshotTrader(
        initial_balance=100.0,
        symbols=["BTC-PERP", "ETH-PERP", "SOL-PERP"],
        max_open_positions=3,
        check_interval=5.0
    )
    
    try:
        # Start trading
        trader.start()
        
        # Let it run
        logger.info(f"⏱️  Running for {duration_minutes} minutes...")
        time.sleep(duration_minutes * 60)
        
    except KeyboardInterrupt:
        logger.info("\n⛔ Interrupted by user")
    finally:
        # Stop and get final stats
        trader.stop()
        
        final_stats = trader.engine.get_stats_summary()
        
        logger.info("\n" + "=" * 70)
        logger.info("🎯 DEMO COMPLETE")
        logger.info("=" * 70)
        logger.info(f"Started with: 100.00 USDT")
        logger.info(f"Final balance: {final_stats['balance']:.2f} USDT")
        logger.info(f"Profit/Loss: {final_stats['total_pnl']:.2f} USDT ({final_stats['total_return_pct']:+.1f}%)")
        logger.info(f"Total trades: {final_stats['total_trades']}")
        logger.info(f"Win rate: {final_stats['win_rate']:.1f}%")
        logger.info("=" * 70)


if __name__ == "__main__":
    # Run the demo
    run_moonshot_demo(duration_minutes=3.0)