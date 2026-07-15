from __future__ import annotations

from dataclasses import dataclass
import pandas as pd
import numpy as np
import logging

# Set up logging
logger = logging.getLogger("hft_strategy")


@dataclass
class HFTConfig:
    """HFT configuration with more permissive defaults for XAUUSD."""
    # Move detection thresholds are fractional returns, e.g. 0.001 = 0.1%.
    tick_threshold: float = 0.01
    min_price_move_pct: float = 0.001  # 0.1% minimum move
    
    # Volume filtering - more permissive
    volume_filter: int = 1  # Was 25, now 1 (almost any volume)
    volume_threshold: float = 0.5  # 50% of average volume
    
    # Execution parameters
    latency_ms: int = 10  # Was 100, now 10ms
    spread_limit: float = 0.1  # Was 0.02, now 0.1 for XAUUSD
    
    # Signal generation
    fast_window: int = 3  # Was 5, faster signals
    slow_window: int = 10  # Was 20, faster signals
    
    # Risk management
    fee_bps: float = 2.0
    risk_per_trade: float = 0.005  # 0.5% per trade
    max_hold_bars: int = 5  # Maximum 5 bars hold time
    
    # Profit targets
    profit_target_ticks: int = 2  # 2 ticks profit target
    stop_loss_ticks: int = 3  # 3 ticks stop loss
    
    # Debug
    debug: bool = True


def _log_debug(msg: str, cfg: HFTConfig):
    """Log debug message if debug mode is enabled."""
    if cfg.debug:
        logger.debug(msg)


def _log_info(msg: str, cfg: HFTConfig):
    """Log info message."""
    logger.info(msg)


def _log_warning(msg: str, cfg: HFTConfig):
    """Log warning message."""
    logger.warning(msg)


def _log_error(msg: str, cfg: HFTConfig):
    """Log error message."""
    logger.error(msg)


@dataclass
class TrendMartingaleConfig:
    base_lot: float = 0.01
    multiplier: float = 2.0
    max_steps: int = 3


def next_martingale_lot(loss_count: int, cfg: TrendMartingaleConfig) -> tuple[int, float]:
    step = min(loss_count, cfg.max_steps)
    return step, cfg.base_lot * (cfg.multiplier ** step)


def trend_signal_1m_with_5m_filter(m1: pd.DataFrame, m5: pd.DataFrame) -> int:
    if m1.empty or m5.empty or "close" not in m1 or "close" not in m5:
        return 0
    m5_close = m5["close"].astype(float)
    m1_close = m1["close"].astype(float)
    if len(m5_close) < 2 or len(m1_close) < 2:
        return 0
    m5_trend = 1 if m5_close.iloc[-1] >= m5_close.iloc[0] else -1
    m1_momentum = 1 if m1_close.iloc[-1] >= m1_close.iloc[0] else -1
    if m5_trend == 1 and m1_momentum == 1:
        return 1
    if m1_momentum == -1:
        return -1
    return 0


def prepare_data(df: pd.DataFrame, cfg: HFTConfig) -> pd.DataFrame:
    """Prepare and validate data for HFT backtest."""
    df = df.copy()
    
    _log_debug(f"Input columns: {df.columns.tolist()}", cfg)
    _log_debug(f"Input shape: {df.shape}", cfg)
    
    # Handle index being datetime
    if isinstance(df.index, pd.DatetimeIndex):
        df = df.reset_index(names="time")
        _log_debug("Reset datetime index to 'time' column", cfg)
    
    # Handle both 'date' and 'time' columns
    if "date" in df.columns and "time" not in df.columns:
        df = df.rename(columns={"date": "time"})
        _log_debug("Renamed 'date' to 'time'", cfg)
    
    # Ensure time column exists
    if "time" not in df.columns:
        raise ValueError("DataFrame must have 'time' or 'date' column, or datetime index")
    
    # Convert time to datetime
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.sort_values("time").reset_index(drop=True)
    
    # Ensure required price columns exist
    required_cols = ["open", "high", "low", "close"]
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Required column '{col}' not found in data")
    
    # Handle volume column
    if "volume" not in df.columns:
        if "tick_volume" in df.columns:
            df["volume"] = df["tick_volume"]
            _log_debug("Using 'tick_volume' as 'volume'", cfg)
        elif "real_volume" in df.columns:
            df["volume"] = df["real_volume"]
            _log_debug("Using 'real_volume' as 'volume'", cfg)
        else:
            # Create synthetic volume
            df["volume"] = 1000  # Default volume
            _log_debug("Created synthetic volume (1000)", cfg)
    
    _log_info(f"Data prepared: {len(df)} bars", cfg)
    _log_info(f"Price range: {df['low'].min():.2f} - {df['high'].max():.2f}", cfg)
    _log_info(f"Time range: {df['time'].min()} to {df['time'].max()}", cfg)
    
    return df


def calculate_signals(df: pd.DataFrame, cfg: HFTConfig) -> pd.DataFrame:
    """Calculate HFT signals with momentum and microstructure analysis."""
    df = df.copy()
    
    # Price changes
    df["price_change"] = df["close"].pct_change().fillna(0.0)
    df["price_change_abs"] = df["price_change"].abs()
    
    # True range for volatility
    df["tr"] = np.maximum(
        df["high"] - df["low"],
        np.maximum(
            abs(df["high"] - df["close"].shift(1)),
            abs(df["low"] - df["close"].shift(1))
        )
    )
    df["atr"] = df["tr"].rolling(window=10).mean()
    
    # Volume analysis
    df["volume_ma"] = df["volume"].rolling(window=20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_ma"].replace(0, np.nan)
    
    # Momentum (fast EMA crossover)
    df["ema_fast"] = df["close"].ewm(span=cfg.fast_window, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=cfg.slow_window, adjust=False).mean()
    df["momentum"] = np.where(df["ema_fast"] > df["ema_slow"], 1, -1)
    
    # Tick detection (significant price moves)
    df["tick_signal"] = 0
    df.loc[df["price_change_abs"] > cfg.tick_threshold, "tick_signal"] = np.sign(df["price_change"])
    
    # Volume filter
    df["volume_signal"] = df["volume_ratio"] > cfg.volume_threshold
    
    # Combined signal
    df["signal"] = 0
    # Long: upward tick + volume confirmation + bullish momentum
    long_condition = (
        (df["tick_signal"] == 1) &
        (df["volume_signal"] | (df["volume"] >= cfg.volume_filter)) &
        (df["momentum"] == 1) &
        (df["price_change_abs"] > cfg.min_price_move_pct)
    )
    df.loc[long_condition, "signal"] = 1
    
    # Short: downward tick + volume confirmation + bearish momentum
    short_condition = (
        (df["tick_signal"] == -1) &
        (df["volume_signal"] | (df["volume"] >= cfg.volume_filter)) &
        (df["momentum"] == -1) &
        (df["price_change_abs"] > cfg.min_price_move_pct)
    )
    df.loc[short_condition, "signal"] = -1
    
    # Calculate spread (estimate from high/low)
    df["spread"] = (df["high"] - df["low"]) / df["close"]
    df["spread_ok"] = df["spread"] <= cfg.spread_limit
    
    # Filter signals by spread
    df.loc[~df["spread_ok"], "signal"] = 0
    
    # Log signal statistics
    signal_counts = df["signal"].value_counts()
    long_signals = signal_counts.get(1, 0)
    short_signals = signal_counts.get(-1, 0)
    total_bars = len(df)
    
    logger.info(f"HFT Signal Statistics:")
    logger.info(f"  Total bars: {total_bars}")
    logger.info(f"  Long signals: {long_signals} ({long_signals/total_bars*100:.2f}%)")
    logger.info(f"  Short signals: {short_signals} ({short_signals/total_bars*100:.2f}%)")
    logger.info(f"  No signal: {total_bars - long_signals - short_signals}")
    logger.info(f"  Spread filtered out: {(~df['spread_ok']).sum()} bars")
    
    return df


def _backtest_hft_core(df_input: pd.DataFrame, cfg: HFTConfig) -> dict:
    """
    High-frequency trading backtest with momentum and microstructure signals.
    """
    _log_info("=" * 60, cfg)
    _log_info("Starting HFT Backtest", cfg)
    _log_info("=" * 60, cfg)
    
    # Log configuration
    _log_info(f"Configuration:", cfg)
    _log_info(f"  Move threshold pct: {cfg.tick_threshold}", cfg)
    _log_info(f"  Min price move: {cfg.min_price_move_pct}", cfg)
    _log_info(f"  Volume filter: {cfg.volume_filter}", cfg)
    _log_info(f"  Volume threshold: {cfg.volume_threshold}", cfg)
    _log_info(f"  Spread limit: {cfg.spread_limit}", cfg)
    _log_info(f"  Latency: {cfg.latency_ms}ms", cfg)
    _log_info(f"  Fast/slow EMA: {cfg.fast_window}/{cfg.slow_window}", cfg)
    
    # Prepare data
    try:
        df = prepare_data(df_input, cfg)
    except Exception as e:
        _log_error(f"Data preparation failed: {e}", cfg)
        return {
            "error": str(e),
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "num_trades": 0
        }
    
    # Calculate signals
    try:
        df = calculate_signals(df, cfg)
    except Exception as e:
        _log_error(f"Signal calculation failed: {e}", cfg)
        return {
            "error": str(e),
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "num_trades": 0
        }
    
    # Execute backtest
    position = 0
    entry_price = 0.0
    entry_bar = 0
    trades = []
    pnl_list = []
    equity_curve = [1.0]
    
    max_consecutive_losses = 0
    current_consecutive_losses = 0
    
    for i in range(1, len(df)):
        current_price = df.loc[i, "close"]
        signal = df.loc[i, "signal"]
        
        # Check for exit conditions if in position
        if position != 0:
            # Check stop loss
            if position == 1:  # Long
                stop_price = entry_price * (1 - cfg.stop_loss_ticks * 0.0001)
                if current_price <= stop_price:
                    gross_pnl = (current_price - entry_price) / entry_price
                    net_pnl = gross_pnl - cfg.fee_bps / 10000
                    pnl_list.append(net_pnl)
                    trades.append({"type": "long", "pnl": net_pnl, "gross_pnl": gross_pnl, "exit": "stop_loss"})
                    equity_curve.append(equity_curve[-1] * (1 + net_pnl))
                    position = 0
                    
                    if net_pnl < 0:
                        current_consecutive_losses += 1
                        max_consecutive_losses = max(max_consecutive_losses, current_consecutive_losses)
                    else:
                        current_consecutive_losses = 0
                    continue
                
                # Check take profit
                target_price = entry_price * (1 + cfg.profit_target_ticks * 0.0001)
                if current_price >= target_price:
                    gross_pnl = (target_price - entry_price) / entry_price
                    net_pnl = gross_pnl - cfg.fee_bps / 10000
                    pnl_list.append(net_pnl)
                    trades.append({"type": "long", "pnl": net_pnl, "gross_pnl": gross_pnl, "exit": "take_profit"})
                    equity_curve.append(equity_curve[-1] * (1 + net_pnl))
                    position = 0
                    if net_pnl < 0:
                        current_consecutive_losses += 1
                        max_consecutive_losses = max(max_consecutive_losses, current_consecutive_losses)
                    else:
                        current_consecutive_losses = 0
                    continue
                
                # Check max hold time
                if i - entry_bar >= cfg.max_hold_bars:
                    gross_pnl = (current_price - entry_price) / entry_price
                    net_pnl = gross_pnl - cfg.fee_bps / 10000
                    pnl_list.append(net_pnl)
                    trades.append({"type": "long", "pnl": net_pnl, "gross_pnl": gross_pnl, "exit": "time_exit"})
                    equity_curve.append(equity_curve[-1] * (1 + net_pnl))
                    position = 0
                    
                    if net_pnl < 0:
                        current_consecutive_losses += 1
                        max_consecutive_losses = max(max_consecutive_losses, current_consecutive_losses)
                    else:
                        current_consecutive_losses = 0
                    continue
            
            else:  # Short position
                stop_price = entry_price * (1 + cfg.stop_loss_ticks * 0.0001)
                if current_price >= stop_price:
                    gross_pnl = (entry_price - current_price) / entry_price
                    net_pnl = gross_pnl - cfg.fee_bps / 10000
                    pnl_list.append(net_pnl)
                    trades.append({"type": "short", "pnl": net_pnl, "gross_pnl": gross_pnl, "exit": "stop_loss"})
                    equity_curve.append(equity_curve[-1] * (1 + net_pnl))
                    position = 0
                    
                    if net_pnl < 0:
                        current_consecutive_losses += 1
                        max_consecutive_losses = max(max_consecutive_losses, current_consecutive_losses)
                    else:
                        current_consecutive_losses = 0
                    continue
                
                target_price = entry_price * (1 - cfg.profit_target_ticks * 0.0001)
                if current_price <= target_price:
                    gross_pnl = (entry_price - target_price) / entry_price
                    net_pnl = gross_pnl - cfg.fee_bps / 10000
                    pnl_list.append(net_pnl)
                    trades.append({"type": "short", "pnl": net_pnl, "gross_pnl": gross_pnl, "exit": "take_profit"})
                    equity_curve.append(equity_curve[-1] * (1 + net_pnl))
                    position = 0
                    if net_pnl < 0:
                        current_consecutive_losses += 1
                        max_consecutive_losses = max(max_consecutive_losses, current_consecutive_losses)
                    else:
                        current_consecutive_losses = 0
                    continue
                
                if i - entry_bar >= cfg.max_hold_bars:
                    gross_pnl = (entry_price - current_price) / entry_price
                    net_pnl = gross_pnl - cfg.fee_bps / 10000
                    pnl_list.append(net_pnl)
                    trades.append({"type": "short", "pnl": net_pnl, "gross_pnl": gross_pnl, "exit": "time_exit"})
                    equity_curve.append(equity_curve[-1] * (1 + net_pnl))
                    position = 0
                    
                    if net_pnl < 0:
                        current_consecutive_losses += 1
                        max_consecutive_losses = max(max_consecutive_losses, current_consecutive_losses)
                    else:
                        current_consecutive_losses = 0
                    continue
        
        # Check for entry signals
        if position == 0:
            if signal == 1:  # Long signal
                position = 1
                entry_price = current_price
                entry_bar = i
                _log_debug(f"Entry LONG at bar {i}, price {current_price:.2f}", cfg)
            
            elif signal == -1:  # Short signal
                position = -1
                entry_price = current_price
                entry_bar = i
                _log_debug(f"Entry SHORT at bar {i}, price {current_price:.2f}", cfg)
    
    # Calculate final performance
    if len(pnl_list) == 0:
        _log_warning("No trades executed during backtest!", cfg)
        return {
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "num_trades": 0,
            "pnl_list": [],
            "trades": []
        }
    
    # Calculate metrics
    pnl_series = pd.Series(pnl_list)
    equity_series = pd.Series(equity_curve)
    
    total_return = float(equity_series.iloc[-1] - 1)
    max_drawdown = float((equity_series / equity_series.cummax() - 1).min())
    win_rate = float((pnl_series > 0).mean())
    num_trades = len(pnl_list)
    
    # Log comprehensive results
    _log_info(f"HFT BACKTEST SUMMARY", cfg)
    _log_info(f"  Total return: {total_return:.4f}", cfg)
    _log_info(f"  Win rate: {win_rate:.4f}", cfg)
    _log_info(f"  Total trades: {num_trades}", cfg)
    
    return {
        "total_return": total_return,
        "max_drawdown": max_drawdown,
        "win_rate": win_rate,
        "num_trades": num_trades,
        "bars": len(df) if 'df' in locals() else 0,
        "pnl_list": pnl_list,
        "trades": trades
    }


def backtest_hft(df_input: pd.DataFrame, cfg: HFTConfig = None) -> dict:
    if cfg is None:
        cfg = HFTConfig()
    
    _log_info("Starting XAUUSD HFT Backtest", cfg)
    
    # Prepare data
    try:
        df = prepare_data(df_input, cfg)
    except Exception as e:
        _log_error(f"Data preparation failed: {e}", cfg)
        return {"error": str(e), "total_return": 0.0, "num_trades": 0}
    
    # Run backtest
    try:
        results = _backtest_hft_core(df, cfg)
    except Exception as e:
        _log_error(f"Backtest execution failed: {e}", cfg)
        return {"error": str(e), "total_return": 0.0, "num_trades": 0}
    
    return results
