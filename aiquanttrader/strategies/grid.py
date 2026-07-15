from __future__ import annotations

from dataclasses import dataclass
import pandas as pd
import numpy as np
import logging

# Set up logging
logger = logging.getLogger("grid_strategy")


@dataclass
class GridConfig:
    """Grid trading configuration with sensible defaults for XAUUSD."""
    # Trend filter settings (5min chart)
    trend_ema_fast: int = 5
    trend_ema_slow: int = 15
    trend_timeframe: str = "5min"
    ema_fast: int = 5
    ema_slow: int = 15
    
    # Grid execution settings (1min chart)
    grid_step_pips: float = 5.0
    grid_step: float = 5.0
    take_profit_pips: float = 1.0
    max_grid_levels: int = 6
    base_lot: float = 0.01
    fee_bps: float = 2.0
    pip_value: float = 0.01  # XAUUSD = 0.01 per pip
    
    # Debug mode
    debug: bool = True


def _log_debug(msg: str, cfg: GridConfig):
    """Log debug message if debug mode is enabled."""
    if cfg.debug:
        logger.debug(msg)


def _log_info(msg: str, cfg: GridConfig):
    """Log info message."""
    logger.info(msg)


def _log_warning(msg: str, cfg: GridConfig):
    """Log warning message."""
    logger.warning(msg)


def _log_error(msg: str, cfg: GridConfig):
    """Log error message."""
    logger.error(msg)


def calculate_trend_direction(df_m1: pd.DataFrame, cfg: GridConfig) -> pd.DataFrame:
    """
    Calculate overall trend direction using 5min EMA 5/15 crossover.
    
    This function handles various data format issues and provides detailed logging.
    """
    df = df_m1.copy().reset_index()
    
    _log_debug(f"Input dataframe columns: {df.columns.tolist()}", cfg)
    _log_debug(f"Input dataframe shape: {df.shape}", cfg)
    _log_debug(f"Input dataframe dtypes:\n{df.dtypes}", cfg)
    
    # Handle various index/column naming conventions
    if 'index' in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df['index']):
            df = df.rename(columns={'index': 'time'})
            _log_debug("Renamed 'index' column to 'time'", cfg)
    
    # Handle both 'date' and 'time' columns
    if "date" in df.columns and "time" not in df.columns:
        df = df.rename(columns={"date": "time"})
        _log_debug("Renamed 'date' column to 'time'", cfg)
    
    if "time" in df.columns and "date" not in df.columns:
        df["date"] = df["time"]
    
    # Check for required price columns
    required_cols = ['open', 'high', 'low', 'close']
    missing_cols = [col for col in required_cols if col not in df.columns]
    
    if missing_cols:
        _log_error(f"Missing required columns: {missing_cols}", cfg)
        _log_error(f"Available columns: {df.columns.tolist()}", cfg)
        raise ValueError(f"DataFrame missing required columns: {missing_cols}")
    
    # Ensure time column exists and is datetime
    if "time" not in df.columns:
        raise ValueError("DataFrame must have 'time' column or datetime index")
    
    df["time"] = pd.to_datetime(df["time"], utc=True)
    
    # Check for sufficient data
    if len(df) < 100:
        _log_warning(f"Very few data points: {len(df)}. Results may be unreliable.", cfg)
    
    _log_info(f"Processing {len(df)} bars of 1m data", cfg)
    
    # Resample to 5 minute timeframe for trend filter
    try:
        df_5m = (
            df.set_index("time")
            .resample(cfg.trend_timeframe)
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna()
        )
        _log_info(f"Resampled to {len(df_5m)} 5m bars for trend analysis", cfg)
    except Exception as e:
        _log_error(f"Failed to resample to 5m timeframe: {e}", cfg)
        raise
    
    # Calculate EMAs
    df_5m["ema_fast"] = df_5m["close"].ewm(span=cfg.trend_ema_fast, adjust=False).mean()
    df_5m["ema_slow"] = df_5m["close"].ewm(span=cfg.trend_ema_slow, adjust=False).mean()
    
    # Determine trend
    df_5m["trend"] = 0
    df_5m.loc[df_5m["ema_fast"] > df_5m["ema_slow"], "trend"] = 1   # Bullish
    df_5m.loc[df_5m["ema_fast"] < df_5m["ema_slow"], "trend"] = -1  # Bearish
    
    # Calculate trend statistics
    trend_counts = df_5m["trend"].value_counts()
    bullish_pct = (trend_counts.get(1, 0) / len(df_5m)) * 100
    bearish_pct = (trend_counts.get(-1, 0) / len(df_5m)) * 100
    neutral_pct = (trend_counts.get(0, 0) / len(df_5m)) * 100
    
    _log_info(f"Trend distribution - Bullish: {bullish_pct:.1f}%, Bearish: {bearish_pct:.1f}%, Neutral: {neutral_pct:.1f}%", cfg)
    
    # Merge trend back to 1min dataframe
    try:
        merged = df.merge(
            df_5m[["trend"]],
            left_on=df["time"].dt.floor(cfg.trend_timeframe),
            right_index=True,
            how="left"
        )
        # Forward fill missing trend values
        merged["trend"] = merged["trend"].ffill().fillna(0)
        
        _log_info(f"Successfully merged trend data with {len(merged)} rows", cfg)
        
    except Exception as e:
        _log_error(f"Failed to merge trend data: {e}", cfg)
        raise
    
    return merged


def backtest_xauusd_grid(df_m1: pd.DataFrame, cfg: GridConfig) -> dict:
    """
    Grid trading strategy for XAUUSD with comprehensive debug logging.
    
    Strategy:
    1. Determine overall trend on 5min chart with EMA 5/15
    2. Place grid orders every grid_step_pips in trend direction
    3. Close each position at take_profit_pips profit
    """
    _log_info("=" * 60, cfg)
    _log_info("Starting XAUUSD Grid Backtest", cfg)
    _log_info("=" * 60, cfg)
    
    # Log configuration
    _log_info(f"Configuration:", cfg)
    _log_info(f"  Grid step: {cfg.grid_step_pips} pips ({cfg.grid_step_pips * cfg.pip_value:.4f} price units)", cfg)
    _log_info(f"  Take profit: {cfg.take_profit_pips} pips ({cfg.take_profit_pips * cfg.pip_value:.4f} price units)", cfg)
    _log_info(f"  Max grid levels: {cfg.max_grid_levels}", cfg)
    _log_info(f"  Base lot: {cfg.base_lot}", cfg)
    _log_info(f"  Fee bps: {cfg.fee_bps}", cfg)
    _log_info(f"  Trend EMA: {cfg.trend_ema_fast}/{cfg.trend_ema_slow}", cfg)
    
    try:
        df = calculate_trend_direction(df_m1, cfg)
    except Exception as e:
        _log_error(f"Failed to calculate trend direction: {e}", cfg)
        return {
            "error": str(e),
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "total_trades": 0,
            "win_trades": 0,
            "grid_levels_used_max": cfg.max_grid_levels,
            "bars": len(df_m1) if hasattr(df_m1, '__len__') else 0
        }
    
    # Calculate pip steps
    grid_step = cfg.grid_step_pips * cfg.pip_value
    tp_step = cfg.take_profit_pips * cfg.pip_value
    
    _log_info(f"", cfg)
    _log_info(f"Grid calculation:", cfg)
    _log_info(f"  Grid step price: {grid_step:.5f}", cfg)
    _log_info(f"  Take profit price: {tp_step:.5f}", cfg)
    
    # Trading variables
    position = 0.0
    active_entries: List[float] = []  # List of entry prices
    strategy_returns: List[float] = []
    trades_count = 0
    wins = 0
    
    # Debug tracking
    debug_stats = {
        "trend_bullish": 0,
        "trend_bearish": 0,
        "trend_neutral": 0,
        "orders_placed": 0,
        "take_profits_hit": 0,
        "positions_closed_end": 0,
        "no_trade_reasons": {
            "neutral_trend": 0,
            "max_levels_reached": 0,
            "price_not_at_grid": 0,
        }
    }
    
    _log_info(f"", cfg)
    _log_info(f"Starting bar-by-bar simulation ({len(df)} bars)...", cfg)
    
    for i in range(1, len(df)):
        current_price = df.loc[i, "close"]
        trend = df.loc[i, "trend"]
        
        # Track trend statistics
        if trend == 1:
            debug_stats["trend_bullish"] += 1
        elif trend == -1:
            debug_stats["trend_bearish"] += 1
        else:
            debug_stats["trend_neutral"] += 1
        
        if trend == 0:
            # No clear trend, close all positions
            if active_entries:
                for entry in active_entries:
                    ret = (current_price - entry) * position / entry
                    strategy_returns.append(ret)
                    trades_count += 1
                    if ret > 0:
                        wins += 1
                debug_stats["positions_closed_end"] += len(active_entries)
                active_entries.clear()
                position = 0.0
            strategy_returns.append(0.0)
            debug_stats["no_trade_reasons"]["neutral_trend"] += 1
            continue
        
        # Check take profit on existing positions
        remaining = []
        for entry in active_entries:
            if trend == 1:  # Long positions
                if current_price >= entry + tp_step:
                    # Close position for profit
                    ret = tp_step / entry * cfg.base_lot
                    strategy_returns.append(ret - (cfg.fee_bps / 10000) * cfg.base_lot)
                    trades_count += 1
                    wins += 1
                    debug_stats["take_profits_hit"] += 1
                else:
                    remaining.append(entry)
            else:  # Short positions
                if current_price <= entry - tp_step:
                    ret = tp_step / entry * cfg.base_lot
                    strategy_returns.append(ret - (cfg.fee_bps / 10000) * cfg.base_lot)
                    trades_count += 1
                    wins += 1
                    debug_stats["take_profits_hit"] += 1
                else:
                    remaining.append(entry)
        
        active_entries = remaining
        
        # Open new grid positions if within max levels
        if len(active_entries) < cfg.max_grid_levels:
            last_entry = active_entries[-1] if active_entries else current_price
            
            if trend == 1:  # Bullish grid - place below current price
                next_grid_level = last_entry - grid_step
                if current_price <= next_grid_level or not active_entries:
                    active_entries.append(current_price)
                    position += cfg.base_lot
                    strategy_returns.append(-(cfg.fee_bps / 10000) * cfg.base_lot)
                    debug_stats["orders_placed"] += 1
                else:
                    debug_stats["no_trade_reasons"]["price_not_at_grid"] += 1
            else:  # Bearish grid - place above current price
                next_grid_level = last_entry + grid_step
                if current_price >= next_grid_level or not active_entries:
                    active_entries.append(current_price)
                    position -= cfg.base_lot
                    strategy_returns.append(-(cfg.fee_bps / 10000) * cfg.base_lot)
                    debug_stats["orders_placed"] += 1
                else:
                    debug_stats["no_trade_reasons"]["price_not_at_grid"] += 1
        else:
            debug_stats["no_trade_reasons"]["max_levels_reached"] += 1
            strategy_returns.append(0.0)
    
    # Close any remaining open positions at end of backtest
    if active_entries:
        final_price = df.iloc[-1]["close"]
        _log_info(f"", cfg)
        _log_info(f"Closing {len(active_entries)} open positions at end of backtest", cfg)
        _log_info(f"  Final price: {final_price:.2f}", cfg)
        
        for entry in active_entries:
            ret = (final_price - entry) * cfg.base_lot / entry
            strategy_returns.append(ret)
            trades_count += 1
            if ret > 0:
                wins += 1
        
        debug_stats["positions_closed_end"] += len(active_entries)
    
    # Calculate performance metrics
    if strategy_returns:
        equity = pd.Series(strategy_returns).add(1).cumprod()
        total_return = float(equity.iloc[-1] - 1) if len(equity) > 0 else 0.0
        max_drawdown = float((equity / equity.cummax() - 1).min())
    else:
        total_return = 0.0
        max_drawdown = 0.0
    
    win_rate = wins / trades_count if trades_count > 0 else 0.0
    
    # Log comprehensive debug statistics
    _log_info(f"", cfg)
    _log_info("=" * 60, cfg)
    _log_info("GRID BACKTEST SUMMARY", cfg)
    _log_info("=" * 60, cfg)
    _log_info(f"Total bars processed: {len(df)}", cfg)
    _log_info(f"Trend distribution:", cfg)
    _log_info(f"  Bullish: {debug_stats['trend_bullish']} bars ({debug_stats['trend_bullish']/len(df)*100:.1f}%)", cfg)
    _log_info(f"  Bearish: {debug_stats['trend_bearish']} bars ({debug_stats['trend_bearish']/len(df)*100:.1f}%)", cfg)
    _log_info(f"  Neutral: {debug_stats['trend_neutral']} bars ({debug_stats['trend_neutral']/len(df)*100:.1f}%)", cfg)
    _log_info(f"", cfg)
    _log_info(f"Trading activity:", cfg)
    _log_info(f"  Orders placed: {debug_stats['orders_placed']}", cfg)
    _log_info(f"  Take profits hit: {debug_stats['take_profits_hit']}", cfg)
    _log_info(f"  Positions closed at end: {debug_stats['positions_closed_end']}", cfg)
    _log_info(f"", cfg)
    _log_info(f"No-trade reasons:", cfg)
    _log_info(f"  Neutral trend: {debug_stats['no_trade_reasons']['neutral_trend']} bars", cfg)
    _log_info(f"  Max levels reached: {debug_stats['no_trade_reasons']['max_levels_reached']} times", cfg)
    _log_info(f"  Price not at grid: {debug_stats['no_trade_reasons']['price_not_at_grid']} times", cfg)
    _log_info(f"", cfg)
    _log_info(f"Performance:", cfg)
    _log_info(f"  Total return: {total_return:.4f} ({total_return*100:.2f}%)", cfg)
    _log_info(f"  Max drawdown: {max_drawdown:.4f} ({max_drawdown*100:.2f}%)", cfg)
    _log_info(f"  Win rate: {win_rate:.4f} ({win_rate*100:.1f}%)", cfg)
    _log_info(f"  Total trades: {trades_count}", cfg)
    _log_info(f"  Winning trades: {wins}", cfg)
    _log_info(f"  Grid levels used: {cfg.max_grid_levels}", cfg)
    _log_info("=" * 60, cfg)
    
    return {
        "bars": len(df),
        "total_return": total_return,
        "max_drawdown": max_drawdown,
        "win_rate": win_rate,
        "total_trades": trades_count,
        "win_trades": wins,
        "grid_levels_used_max": cfg.max_grid_levels,
        "debug_stats": debug_stats
    }