from __future__ import annotations

from dataclasses import dataclass
import pandas as pd
import numpy as np
import logging

# Set up logging
logger = logging.getLogger("martingale_strategy")


@dataclass
class BacktestConfig:
    """Martingale configuration with risk management."""
    # Position sizing
    base_lot: float = 0.005  # Was 0.01, smaller base for XAUUSD
    multiplier: float = 1.25  # Was 2.0, less aggressive
    max_steps: int = 3  # Was 4, fewer steps to limit risk
    
    # Risk management - NEW
    stop_loss_pct: float = 0.02  # 2% stop loss per trade
    take_profit_pct: float = 0.01  # 1% take profit
    trailing_stop_pct: float = 0.005  # 0.5% trailing stop
    max_position_hold_bars: int = 20  # Close after 20 bars
    daily_loss_limit: float = 0.05  # Stop after 5% daily loss
    
    # EMA settings
    ema_fast: int = 8  # Was 5, slower for trend confirmation
    ema_slow: int = 21  # Was 20, standard 8/21 EMA
    
    # Trend strength filter - NEW
    adx_period: int = 14
    adx_threshold: float = 20.0  # Only trade when ADX > 20
    
    # Fees
    fee_bps: float = 2.0
    
    # Debug
    debug: bool = True


def _log_debug(msg: str, cfg: BacktestConfig):
    """Log debug message if debug mode is enabled."""
    if cfg.debug:
        logger.debug(msg)


def _log_info(msg: str, cfg: BacktestConfig):
    """Log info message."""
    logger.info(msg)


def _log_warning(msg: str, cfg: BacktestConfig):
    """Log warning message."""
    logger.warning(msg)


def _log_error(msg: str, cfg: BacktestConfig):
    """Log error message."""
    logger.error(msg)


def calculate_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Calculate Average Directional Index (ADX)."""
    df = df.copy()
    
    # True Range
    df['tr1'] = df['high'] - df['low']
    df['tr2'] = abs(df['high'] - df['close'].shift(1))
    df['tr3'] = abs(df['low'] - df['close'].shift(1))
    df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
    
    # +DM and -DM
    df['plus_dm'] = np.where(
        (df['high'] - df['high'].shift(1)) > (df['low'].shift(1) - df['low']),
        np.maximum(df['high'] - df['high'].shift(1), 0),
        0
    )
    df['minus_dm'] = np.where(
        (df['low'].shift(1) - df['low']) > (df['high'] - df['high'].shift(1)),
        np.maximum(df['low'].shift(1) - df['low'], 0),
        0
    )
    
    # Smoothed averages
    df['atr'] = df['tr'].ewm(alpha=1/period, adjust=False).mean()
    df['plus_di'] = 100 * (df['plus_dm'].ewm(alpha=1/period, adjust=False).mean() / df['atr'])
    df['minus_di'] = 100 * (df['minus_dm'].ewm(alpha=1/period, adjust=False).mean() / df['atr'])
    
    # DX and ADX
    df['dx'] = 100 * abs(df['plus_di'] - df['minus_di']) / (df['plus_di'] + df['minus_di'])
    df['adx'] = df['dx'].ewm(alpha=1/period, adjust=False).mean()
    
    # Clean up
    df = df.drop(['tr1', 'tr2', 'tr3', 'tr', 'plus_dm', 'minus_dm', 'atr', 'plus_di', 'minus_di', 'dx'], axis=1)
    
    return df


def build_trend_signals_from_m1(m1: pd.DataFrame, cfg: BacktestConfig = None) -> pd.DataFrame:
    """
    Build 1m trade signals using 5m EMA(8/21) trend filter with ADX confirmation.
    
    This is an improved version with:
    - Better EMA periods (8/21 instead of 5/20)
    - ADX trend strength filter
    - More robust data handling
    """
    df = m1.copy()
    if cfg is None:
        cfg = BacktestConfig()
    
    # Handle index being datetime
    if isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index(names="time")
    
    # Handle both 'date' and 'time' columns
    if "date" in df.columns and "time" not in df.columns:
        df = df.rename(columns={"date": "time"})
    
    # Ensure time column exists
    if "time" not in df.columns:
        raise ValueError("DataFrame must have 'time' or 'date' column, or datetime index")
    
    df = df.sort_values("time").reset_index(drop=True)
    df["time"] = pd.to_datetime(df["time"], utc=True)

    # Handle both tick_volume and volume column names
    volume_col = "tick_volume" if "tick_volume" in df.columns else "volume"
    
    # Ensure required OHLC columns exist
    required_cols = ["open", "high", "low", "close"]
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Required column '{col}' not found in data")
    
    # Resample to 5m for trend analysis
    m5 = (
        df.set_index("time")
        .resample("5min")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", volume_col: "sum"})
        .dropna()
    )
    m5.rename(columns={volume_col: "tick_volume"}, inplace=True)
    
    # Calculate EMAs on 5m timeframe
    m5["ema_fast"] = m5["close"].ewm(span=cfg.ema_fast, adjust=False).mean()
    m5["ema_slow"] = m5["close"].ewm(span=cfg.ema_slow, adjust=False).mean()
    
    # Calculate ADX for trend strength
    m5 = calculate_adx(m5, cfg.adx_period)
    
    # Merge trend data back to 1m
    merged = df.merge(
        m5[["ema_fast", "ema_slow", "adx"]],
        left_on=df["time"].dt.floor("5min"),
        right_index=True,
        how="left"
    )
    
    # Forward fill missing values
    merged["ema_fast"] = merged["ema_fast"].ffill()
    merged["ema_slow"] = merged["ema_slow"].ffill()
    merged["adx"] = merged["adx"].ffill().fillna(0)
    
    # Generate signals with ADX filter
    merged["signal"] = 0
    
    # Long signal: Price above both EMAs, ADX > threshold (strong uptrend)
    long_condition = (
        (merged["close"] > merged["ema_fast"]) &
        (merged["close"] > merged["ema_slow"]) &
        (merged["adx"] > cfg.adx_threshold)
    )
    merged.loc[long_condition, "signal"] = 1
    
    # Short signal: Price below both EMAs, ADX > threshold (strong downtrend)
    short_condition = (
        (merged["close"] < merged["ema_fast"]) &
        (merged["close"] < merged["ema_slow"]) &
        (merged["adx"] > cfg.adx_threshold)
    )
    merged.loc[short_condition, "signal"] = -1
    
    # Log signal statistics
    signal_counts = merged["signal"].value_counts()
    long_signals = signal_counts.get(1, 0)
    short_signals = signal_counts.get(-1, 0)
    total_bars = len(merged)
    
    logger.info(f"Martingale Signal Statistics:")
    logger.info(f"  Total bars: {total_bars}")
    logger.info(f"  Long signals: {long_signals} ({long_signals/total_bars*100:.2f}%)")
    logger.info(f"  Short signals: {short_signals} ({short_signals/total_bars*100:.2f}%)")
    logger.info(f"  No signal: {total_bars - long_signals - short_signals}")
    logger.info(f"  ADX filtered out: {(merged['adx'] <= cfg.adx_threshold).sum()} bars")
    
    return merged


def backtest_trend_martingale(m1: pd.DataFrame, cfg: BacktestConfig) -> dict:
    """
    Improved martingale trend following with risk management.
    
    Key improvements:
    - ADX trend strength filter
    - Stop losses and take profits
    - Trailing stops
    - Daily loss limits
    - Better EMA periods (8/21)
    - Less aggressive martingale (1.25x instead of 2x)
    """
    _log_info("=" * 60, cfg)
    _log_info("Starting Martingale Trend Following Backtest", cfg)
    _log_info("=" * 60, cfg)
    
    # Log configuration
    _log_info(f"Configuration:", cfg)
    _log_info(f"  Base lot: {cfg.base_lot}", cfg)
    _log_info(f"  Multiplier: {cfg.multiplier}x", cfg)
    _log_info(f"  Max steps: {cfg.max_steps}", cfg)
    _log_info(f"  Stop loss: {cfg.stop_loss_pct*100:.1f}%", cfg)
    _log_info(f"  Take profit: {cfg.take_profit_pct*100:.1f}%", cfg)
    _log_info(f"  Trailing stop: {cfg.trailing_stop_pct*100:.1f}%", cfg)
    _log_info(f"  EMA periods: {cfg.ema_fast}/{cfg.ema_slow}", cfg)
    _log_info(f"  ADX threshold: {cfg.adx_threshold}", cfg)
    _log_info(f"  Fee bps: {cfg.fee_bps}", cfg)
    
    # Build signals with ADX filter
    try:
        df = build_trend_signals_from_m1(m1, cfg)
    except Exception as e:
        _log_error(f"Failed to build trend signals: {e}", cfg)
        return {
            "error": str(e),
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "num_trades": 0
        }
    
    df["ret"] = df["close"].pct_change().fillna(0.0)
    
    # Trading variables
    position = 0  # 0 = flat, 1 = long, -1 = short
    martingale_step = 0
    entry_price = 0.0
    highest_price = 0.0  # For trailing stop
    lowest_price = float('inf')  # For trailing stop
    bars_in_trade = 0
    
    strategy_returns = []
    trade_returns = []
    trades = []  # Detailed trade log
    
    daily_pnl = 0.0
    current_day = None
    daily_loss_triggered = False
    
    # Statistics
    total_longs = 0
    total_shorts = 0
    wins = 0
    losses = 0
    
    for i in range(1, len(df)):
        current_price = df.loc[i, "close"]
        signal = int(df.loc[i - 1, "signal"])
        current_time = df.loc[i, "time"]
        
        # Check for new day
        if current_day != current_time.date():
            daily_pnl = 0.0
            current_day = current_time.date()
            daily_loss_triggered = False
        
        # Check daily loss limit
        if daily_pnl < -cfg.daily_loss_limit:
            daily_loss_triggered = True
        
        # Calculate position size
        if position != 0:
            step_lot = cfg.base_lot * (cfg.multiplier ** martingale_step)
            
            # Check stop loss
            if position == 1:  # Long
                loss_pct = (entry_price - current_price) / entry_price
                profit_pct = (current_price - entry_price) / entry_price
                
                # Update highest price for trailing stop
                highest_price = max(highest_price, current_price)
                
                # Check stop loss
                if loss_pct >= cfg.stop_loss_pct:
                    r = -loss_pct * step_lot
                    fee = (cfg.fee_bps / 10000.0) * step_lot
                    r -= fee
                    strategy_returns.append(r)
                    trade_returns.append(r)
                    trades.append({
                        "type": "long",
                        "entry": entry_price,
                        "exit": current_price,
                        "pnl": r,
                        "exit_reason": "stop_loss",
                        "step": martingale_step,
                        "bar": i
                    })
                    daily_pnl += r
                    losses += 1
                    
                    # Increase martingale step
                    martingale_step = min(martingale_step + 1, cfg.max_steps)
                    position = 0
                    continue
                
                # Check take profit
                if profit_pct >= cfg.take_profit_pct:
                    r = profit_pct * step_lot
                    fee = (cfg.fee_bps / 10000.0) * step_lot
                    r -= fee
                    strategy_returns.append(r)
                    trade_returns.append(r)
                    trades.append({
                        "type": "long",
                        "entry": entry_price,
                        "exit": current_price,
                        "pnl": r,
                        "exit_reason": "take_profit",
                        "step": martingale_step,
                        "bar": i
                    })
                    daily_pnl += r
                    wins += 1
                    
                    # Reset martingale step
                    martingale_step = 0
                    position = 0
                    continue
                
                # Check trailing stop
                trailing_stop_price = highest_price * (1 - cfg.trailing_stop_pct)
                if current_price <= trailing_stop_price and current_price > entry_price:
                    r = (current_price - entry_price) / entry_price * step_lot
                    fee = (cfg.fee_bps / 10000.0) * step_lot
                    r -= fee
                    strategy_returns.append(r)
                    trade_returns.append(r)
                    trades.append({
                        "type": "long",
                        "entry": entry_price,
                        "exit": current_price,
                        "pnl": r,
                        "exit_reason": "trailing_stop",
                        "step": martingale_step,
                        "bar": i
                    })
                    daily_pnl += r
                    wins += 1
                    
                    # Reset martingale step
                    martingale_step = 0
                    position = 0
                    continue
                
                # Check max hold time
                if bars_in_trade >= cfg.max_position_hold_bars:
                    r = (current_price - entry_price) / entry_price * step_lot
                    fee = (cfg.fee_bps / 10000.0) * step_lot
                    r -= fee
                    strategy_returns.append(r)
                    trade_returns.append(r)
                    trades.append({
                        "type": "long",
                        "entry": entry_price,
                        "exit": current_price,
                        "pnl": r,
                        "exit_reason": "time_exit",
                        "step": martingale_step,
                        "bar": i
                    })
                    daily_pnl += r
                    if r > 0:
                        wins += 1
                        martingale_step = 0
                    else:
                        losses += 1
                        martingale_step = min(martingale_step + 1, cfg.max_steps)
                    position = 0
                    continue
                
                bars_in_trade += 1
            
            else:  # Short position
                loss_pct = (current_price - entry_price) / entry_price
                profit_pct = (entry_price - current_price) / entry_price
                
                # Update lowest price for trailing stop
                lowest_price = min(lowest_price, current_price)
                
                # Check stop loss
                if loss_pct >= cfg.stop_loss_pct:
                    r = -loss_pct * step_lot
                    fee = (cfg.fee_bps / 10000.0) * step_lot
                    r -= fee
                    strategy_returns.append(r)
                    trade_returns.append(r)
                    trades.append({
                        "type": "short",
                        "entry": entry_price,
                        "exit": current_price,
                        "pnl": r,
                        "exit_reason": "stop_loss",
                        "step": martingale_step,
                        "bar": i
                    })
                    daily_pnl += r
                    losses += 1
                    
                    # Increase martingale step
                    martingale_step = min(martingale_step + 1, cfg.max_steps)
                    position = 0
                    continue
                
                # Check take profit
                if profit_pct >= cfg.take_profit_pct:
                    r = profit_pct * step_lot
                    fee = (cfg.fee_bps / 10000.0) * step_lot
                    r -= fee
                    strategy_returns.append(r)
                    trade_returns.append(r)
                    trades.append({
                        "type": "short",
                        "entry": entry_price,
                        "exit": current_price,
                        "pnl": r,
                        "exit_reason": "take_profit",
                        "step": martingale_step,
                        "bar": i
                    })
                    daily_pnl += r
                    wins += 1
                    
                    # Reset martingale step
                    martingale_step = 0
                    position = 0
                    continue
                
                # Check trailing stop
                trailing_stop_price = lowest_price * (1 + cfg.trailing_stop_pct)
                if current_price >= trailing_stop_price and current_price < entry_price:
                    r = (entry_price - current_price) / entry_price * step_lot
                    fee = (cfg.fee_bps / 10000.0) * step_lot
                    r -= fee
                    strategy_returns.append(r)
                    trade_returns.append(r)
                    trades.append({
                        "type": "short",
                        "entry": entry_price,
                        "exit": current_price,
                        "pnl": r,
                        "exit_reason": "trailing_stop",
                        "step": martingale_step,
                        "bar": i
                    })
                    daily_pnl += r
                    wins += 1
                    
                    # Reset martingale step
                    martingale_step = 0
                    position = 0
                    continue
                
                # Check max hold time
                if bars_in_trade >= cfg.max_position_hold_bars:
                    r = (entry_price - current_price) / entry_price * step_lot
                    fee = (cfg.fee_bps / 10000.0) * step_lot
                    r -= fee
                    strategy_returns.append(r)
                    trade_returns.append(r)
                    trades.append({
                        "type": "short",
                        "entry": entry_price,
                        "exit": current_price,
                        "pnl": r,
                        "exit_reason": "time_exit",
                        "step": martingale_step,
                        "bar": i
                    })
                    daily_pnl += r
                    if r > 0:
                        wins += 1
                        martingale_step = 0
                    else:
                        losses += 1
                        martingale_step = min(martingale_step + 1, cfg.max_steps)
                    position = 0
                    continue
                
                bars_in_trade += 1
        
        # Check for new entry signal
        if position == 0 and not daily_loss_triggered:
            if signal == 1:  # Long signal
                position = 1
                entry_price = current_price
                highest_price = current_price
                bars_in_trade = 0
                total_longs += 1
                _log_debug(f"Entry LONG at bar {i}, price {current_price:.2f}, step {martingale_step}", cfg)
            
            elif signal == -1:  # Short signal
                position = -1
                entry_price = current_price
                lowest_price = current_price
                bars_in_trade = 0
                total_shorts += 1
                _log_debug(f"Entry SHORT at bar {i}, price {current_price:.2f}, step {martingale_step}", cfg)
    
    # Calculate final performance metrics
    if strategy_returns:
        equity_series = pd.Series(strategy_returns)
        cumulative_returns = equity_series.cumsum()
        
        total_return = cumulative_returns.iloc[-1]
        
        # Calculate max drawdown
        rolling_max = cumulative_returns.expanding().max()
        drawdown = cumulative_returns - rolling_max
        max_drawdown = drawdown.min()
        
        # Win rate
        win_rate = wins / (wins + losses) if (wins + losses) > 0 else 0.0
        
        # Average trade metrics
        avg_trade = np.mean(trade_returns) if trade_returns else 0.0
        avg_win = np.mean([t["pnl"] for t in trades if t["pnl"] > 0]) if any(t["pnl"] > 0 for t in trades) else 0.0
        avg_loss = np.mean([t["pnl"] for t in trades if t["pnl"] <= 0]) if any(t["pnl"] <= 0 for t in trades) else 0.0
        
        # Exit reason breakdown
        exit_reasons = {}
        for t in trades:
            reason = t["exit_reason"]
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
        
        # Martingale step distribution
        step_distribution = {}
        for t in trades:
            step = t["step"]
            step_distribution[step] = step_distribution.get(step, 0) + 1
        
        _log_info(f"", cfg)
        _log_info("=" * 60, cfg)
        _log_info("MARTINGALE TREND BACKTEST SUMMARY", cfg)
        _log_info("=" * 60, cfg)
        _log_info(f"Performance:", cfg)
        _log_info(f"  Total return: {total_return:.4f} ({total_return*100:.2f}%)", cfg)
        _log_info(f"  Max drawdown: {max_drawdown:.4f} ({max_drawdown*100:.2f}%)", cfg)
        _log_info(f"  Win rate: {win_rate:.4f} ({win_rate*100:.1f}%)", cfg)
        _log_info(f"", cfg)
        _log_info(f"Trade statistics:", cfg)
        _log_info(f"  Total trades: {len(trades)}", cfg)
        _log_info(f"  Long trades: {total_longs}", cfg)
        _log_info(f"  Short trades: {total_shorts}", cfg)
        _log_info(f"  Wins: {wins}", cfg)
        _log_info(f"  Losses: {losses}", cfg)
        _log_info(f"", cfg)
        _log_info(f"Average metrics:", cfg)
        _log_info(f"  Average trade: {avg_trade:.4f}", cfg)
        _log_info(f"  Average win: {avg_win:.4f}", cfg)
        _log_info(f"  Average loss: {avg_loss:.4f}", cfg)
        if avg_loss != 0:
            _log_info(f"  Profit factor: {abs(avg_win / avg_loss):.2f}", cfg)
        _log_info(f"", cfg)
        _log_info(f"Exit reasons:", cfg)
        for reason, count in exit_reasons.items():
            _log_info(f"  {reason}: {count}", cfg)
        _log_info(f"", cfg)
        _log_info(f"Martingale step distribution:", cfg)
        for step, count in sorted(step_distribution.items()):
            _log_info(f"  Step {step}: {count} trades", cfg)
        _log_info("=" * 60, cfg)
        
        return {
            "total_return": total_return,
            "max_drawdown": max_drawdown,
            "win_rate": win_rate,
            "num_trades": len(trades),
            "long_trades": total_longs,
            "short_trades": total_shorts,
            "wins": wins,
            "losses": losses,
            "avg_trade": avg_trade,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "exit_reasons": exit_reasons,
            "step_distribution": step_distribution,
            "trades": trades
        }
    else:
        _log_warning("No trades executed during backtest!", cfg)
        return {
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "num_trades": 0,
            "error": "No trades executed"
        }