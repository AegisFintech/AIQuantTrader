"""
AVELLANEDA-STOIKOV MARKET MAKING STRATEGY

This strategy implements the classic Avellaneda-Stoikov market-making model.
It determines optimal bid and ask prices based on inventory risk and price volatility.

Key Parameters:
- gamma: Risk aversion parameter.
- sigma: Volatility of the asset.
- T: Terminal time horizon.
- k: Order book liquidity/intensity parameter.

Author: FinRobot Research Team
Version: 1.0
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
import logging

logger = logging.getLogger("avellaneda_stoikov")


@dataclass
class AvellanedaStoikovConfig:
    """
    Avellaneda-Stoikov Strategy Configuration
    """
    # Risk aversion parameter (gamma)
    # Higher gamma means the market maker is more sensitive to inventory risk
    gamma: float = 0.1
    
    # Order book liquidity parameter (kappa)
    # Relates to the probability of an order being filled
    kappa: float = 1.5
    
    # Time horizon (T) in bars
    # Usually represents a trading day
    horizon_bars: int = 24 * 60  # Assuming 1-minute bars
    
    # Volatility lookback period
    volatility_period: int = 20
    
    # Inventory management
    max_inventory: int = 10  # Maximum units to hold
    base_order_size: float = 0.01  # Base lot size
    
    # Risk Management
    stop_loss_pct: float = 0.01  # 1% stop loss for inventory
    
    # Position Sizing
    account_balance: float = 100000.0
    
    # Execution
    fee_bps: float = 2.0
    
    # Asset specific adjustments (Gold vs BTC)
    asset_type: str = "XAUUSD"  # "XAUUSD" or "BTC"
    
    # Debug
    debug: bool = False


def calculate_volatility(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Calculate rolling volatility (sigma) in absolute price terms."""
    # Using price changes for absolute volatility
    price_changes = df['close'].diff()
    return price_changes.rolling(window=period).std()


def backtest_avellaneda_stoikov(df_input: pd.DataFrame, config: AvellanedaStoikovConfig) -> dict:
    """
    Backtest the Avellaneda-Stoikov market-making strategy.
    
    Strategy Rules:
    1. Calculate optimal mid-price (r) based on current mid-price (s) and inventory (q).
    2. Calculate optimal spread (delta).
    3. Place bid and ask quotes at r +/- delta/2.
    4. Check if next bar's price range hits either quote.
    """
    logger.info("=" * 60)
    logger.info("AVELLANEDA-STOIKOV STRATEGY BACKTEST")
    logger.info("=" * 60)
    logger.info(f"Asset: {config.asset_type}")
    logger.info(f"Gamma: {config.gamma}, Kappa: {config.kappa}")
    logger.info(f"Max Inventory: {config.max_inventory}, Order Size: {config.base_order_size}")
    
    # Prepare data
    df = df_input.copy()
    
    # Handle headerless CSV (common in MT5 exports)
    if isinstance(df.columns[0], int) or df.columns[0] == '0':
        logger.info("Detected headerless data, assigning default MT5 column names")
        # MT5 default: Date, Time (or combined), Open, High, Low, Close, Volume
        if len(df.columns) >= 6:
            df.columns = ['time', 'open', 'high', 'low', 'close', 'volume'][:len(df.columns)]
        else:
            logger.warning("Data has fewer than 6 columns, cannot assign default names safely")

    if isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index(names="time")
    
    if "date" in df.columns and "time" not in df.columns:
        df = df.rename(columns={"date": "time"})
    
    if "time" not in df.columns:
        # Fallback to first column if no 'time' column found
        logger.warning("No 'time' column found, using first column as 'time'")
        df = df.rename(columns={df.columns[0]: "time"})

    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.sort_values("time").reset_index(drop=True)
    
    # Ensure required price columns exist
    for col in ['open', 'high', 'low', 'close']:
        if col not in df.columns:
            # Try to guess based on position if names missing
            cols = list(df.columns)
            if col == 'open' and len(cols) > 1: df['open'] = df[cols[1]]
            elif col == 'high' and len(cols) > 2: df['high'] = df[cols[2]]
            elif col == 'low' and len(cols) > 3: df['low'] = df[cols[3]]
            elif col == 'close' and len(cols) > 4: df['close'] = df[cols[4]]
            else:
                raise ValueError(f"Required column '{col}' not found in data")

    # Calculate indicators
    df['sigma'] = calculate_volatility(df, config.volatility_period)
    
    # Trading simulation
    trades = []
    inventory = 0  # Units of the asset
    cash = config.account_balance
    
    equity_curve = [config.account_balance]
    inventory_history = [0]
    
    for i in range(config.volatility_period, len(df) - 1):
        current_bar = df.iloc[i]
        next_bar = df.iloc[i + 1]
        
        s = current_bar['close']
        sigma = current_bar['sigma']
        
        if pd.isna(sigma) or sigma == 0:
            sigma = 0.0001  # Minimum volatility
            
        # Time remaining in horizon (T-t)
        # For simplicity, we assume a rolling horizon or fixed T
        t_rem = (config.horizon_bars - (i % config.horizon_bars)) / config.horizon_bars
        
        # 1. Calculate optimal mid-price (r)
        # r = s - q * gamma * sigma^2 * (T-t)
        r = s - (inventory * config.gamma * (sigma ** 2) * t_rem)
        
        # 2. Calculate optimal spread (delta)
        # delta = gamma * sigma^2 * (T-t) + (2/gamma) * ln(1 + gamma/kappa)
        term1 = config.gamma * (sigma ** 2) * t_rem
        term2 = (2 / config.gamma) * np.log(1 + config.gamma / config.kappa)
        delta = term1 + term2
        
        # Calculate bid and ask prices
        p_bid = r - (delta / 2)
        p_ask = r + (delta / 2)
        
        # Log quotes in debug mode
        if config.debug and i % 100 == 0:
            logger.debug(f"Time {current_bar['time']}: Mid={s:.2f}, r={r:.2f}, Bid={p_bid:.2f}, Ask={p_ask:.2f}, Inv={inventory}")

        # 3. Simulate execution on next bar
        # Buy if next bar's low hits p_bid
        buy_filled = next_bar['low'] <= p_bid and inventory < config.max_inventory
        # Sell if next bar's high hits p_ask
        sell_filled = next_bar['high'] >= p_ask and inventory > -config.max_inventory
        
        fee_factor = config.fee_bps / 10000
        
        if buy_filled:
            # Execute buy
            fill_price = p_bid
            cost = config.base_order_size * fill_price * (1 + fee_factor)
            cash -= cost
            inventory += config.base_order_size
            trades.append({
                'time': next_bar['time'],
                'type': 'buy',
                'price': fill_price,
                'size': config.base_order_size,
                'inventory': inventory
            })
            
        if sell_filled:
            # Execute sell
            fill_price = p_ask
            revenue = config.base_order_size * fill_price * (1 - fee_factor)
            cash += revenue
            inventory -= config.base_order_size
            trades.append({
                'time': next_bar['time'],
                'type': 'sell',
                'price': fill_price,
                'size': config.base_order_size,
                'inventory': inventory
            })
            
        # Daily Equity Check
        current_equity = cash + (inventory * next_bar['close'])
        equity_curve.append(current_equity)
        inventory_history.append(inventory)
        
        # Check Stop Loss (if inventory too underwater)
        # This is a simple version
        if len(trades) > 0 and abs(inventory) > 0:
            avg_price = sum(t['price'] for t in trades[-10:] if t['type'] == ('buy' if inventory > 0 else 'sell')) / 10 if len(trades) >= 10 else s
            unrealized_pnl_pct = (next_bar['close'] - avg_price) / avg_price if inventory > 0 else (avg_price - next_bar['close']) / avg_price
            
            if unrealized_pnl_pct < -config.stop_loss_pct:
                # Force liquidation
                if inventory > 0:
                    cash += inventory * next_bar['close'] * (1 - fee_factor)
                else:
                    cash -= abs(inventory) * next_bar['close'] * (1 + fee_factor)
                
                trades.append({
                    'time': next_bar['time'],
                    'type': 'stop_liquidation',
                    'price': next_bar['close'],
                    'size': abs(inventory),
                    'inventory': 0
                })
                inventory = 0

    # Final Liquidation
    final_bar = df.iloc[-1]
    if inventory != 0:
        if inventory > 0:
            cash += inventory * final_bar['close'] * (1 - config.fee_bps / 10000)
        else:
            cash -= abs(inventory) * final_bar['close'] * (1 + config.fee_bps / 10000)
        
        trades.append({
            'time': final_bar['time'],
            'type': 'final_liquidation',
            'price': final_bar['close'],
            'size': abs(inventory),
            'inventory': 0
        })
        inventory = 0
        equity_curve.append(cash)

    # Calculate metrics
    if len(trades) == 0:
        return {
            'total_return': 0.0,
            'max_drawdown': 0.0,
            'win_rate': 0.0,
            'num_trades': 0,
            'trades': []
        }
    
    final_equity = cash
    total_return = (final_equity - config.account_balance) / config.account_balance
    
    equity_series = pd.Series(equity_curve)
    rolling_max = equity_series.expanding().max()
    drawdown = (equity_series - rolling_max) / rolling_max
    max_drawdown = drawdown.min()
    
    # For market making, win rate is less meaningful than profit factor
    # but we can calculate it based on trade PnL pairs if we track them.
    # Here we'll just return basic metrics.
    
    logger.info("=" * 60)
    logger.info("AVELLANEDA-STOIKOV RESULTS")
    logger.info("=" * 60)
    logger.info(f"Total Return: {total_return:.2%}")
    logger.info(f"Max Drawdown: {max_drawdown:.2%}")
    logger.info(f"Number of Trades: {len(trades)}")
    logger.info(f"Final Inventory: {inventory}")
    logger.info("=" * 60)
    
    return {
        'total_return': total_return,
        'max_drawdown': max_drawdown,
        'num_trades': len(trades),
        'equity_curve': equity_curve,
        'inventory_history': inventory_history,
        'trades': trades
    }


# Export configuration and backtest function
__all__ = ['AvellanedaStoikovConfig', 'backtest_avellaneda_stoikov']
