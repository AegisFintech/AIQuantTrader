"""
STRATEGY INTEGRATION MODULE

This module integrates all new and fixed strategies into the
continuous backtest system.

Strategies Available:
1. ADX Trend Following (NEW - research-backed)
2. London-NY Breakout (NEW - session-based)
3. Fixed Grid (FIXED - with risk management)
4. Mean Reversion (HFT replacement - BB + RSI)
5. Momentum Mean Reversion (RESEARCH - prop firm style)

Author: FinRobot Research Team
Version: 2.0
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime
import logging

logger = logging.getLogger("strategy_integration")

# Import all strategies
from finrobot.strategies.adx_trend import (
    ADXTrendConfig, 
    backtest_adx_trend_following,
    calculate_adx
)

from finrobot.strategies.london_ny_breakout import (
    LondonNYBreakoutConfig,
    backtest_london_ny_breakout,
    is_within_trading_session
)

from finrobot.strategies.fixed_grid import (
    FixedGridConfig,
    backtest_fixed_grid
)

from finrobot.strategies.mean_reversion import (
    MeanReversionConfig,
    backtest_mean_reversion
)

from finrobot.strategies.research_strategies import (
    MomentumMeanReversionConfig,
    backtest_momentum_mean_reversion
)

# ============================================================================
# PARAMETER SPACES FOR OPTIMIZATION
# ============================================================================

PARAMETER_SPACES = {
    "adx_trend": {
        "adx_period": [10, 14, 20],
        "adx_threshold": [20.0, 25.0, 30.0],
        "ema_fast": [15, 20, 25],
        "ema_slow": [40, 50, 60],
        "atr_multiplier_stop": [1.5, 2.0, 2.5],
        "atr_multiplier_target": [2.5, 3.0, 3.5],
        "risk_per_trade": [0.005, 0.01, 0.015]
    },
    "london_ny_breakout": {
        "session_start_hour": [7, 8, 9],
        "session_end_hour": [11, 12, 13],
        "lookback_hours": [2, 4, 6],
        "breakout_threshold_pct": [0.0005, 0.001, 0.002],
        "stop_loss_atr_multiplier": [1.0, 1.5, 2.0],
        "take_profit_atr_multiplier": [2.0, 2.5, 3.0]
    },
    "fixed_grid": {
        "grid_step_pips": [1.0, 2.0, 3.0],
        "take_profit_pips": [0.5, 1.0, 1.5],
        "max_grid_levels": [2, 3, 4],
        "adx_threshold": [20.0, 25.0, 30.0],
        "max_daily_loss_pct": [0.01, 0.02, 0.03],
        "risk_per_grid_level": [0.003, 0.005, 0.007]
    },
    "mean_reversion": {
        "bb_period": [15, 20, 25],
        "bb_std_dev": [1.5, 2.0, 2.5],
        "rsi_period": [10, 14, 21],
        "rsi_overbought": [65, 70, 75],
        "rsi_oversold": [25, 30, 35],
        "adx_max": [15, 20, 25],
        "stop_loss_atr_multiplier": [1.0, 1.5, 2.0],
        "take_profit_atr_multiplier": [1.5, 2.0, 2.5]
    },
    "momentum_mean_reversion": {
        "bb_period": [15, 20, 25],
        "rsi_period": [10, 14, 21],
        "momentum_period": [3, 5, 8],
        "momentum_threshold": [0.0005, 0.001, 0.002],
        "adx_max": [15, 20, 25],
        "use_momentum_filter": [True, False],
        "use_mtf_alignment": [True, False],
        "stop_loss_atr_multiplier": [1.0, 1.5, 2.0],
        "take_profit_atr_multiplier": [1.5, 2.0, 2.5]
    },
}


# ============================================================================
# BACKTEST RUNNER
# ============================================================================

def run_strategy_backtest(strategy_name: str, df: pd.DataFrame, params: Dict[str, Any]) -> dict:
    """
    Run a single backtest for the specified strategy.
    
    Args:
        strategy_name: Name of the strategy (adx_trend, london_ny_breakout, etc.)
        df: Price data DataFrame
        params: Strategy parameters
    
    Returns:
        Backtest results dictionary
    """
    try:
        if strategy_name == "adx_trend":
            config = ADXTrendConfig(**params)
            return backtest_adx_trend_following(df, config)
        
        elif strategy_name == "london_ny_breakout":
            config = LondonNYBreakoutConfig(**params)
            return backtest_london_ny_breakout(df, config)
        
        elif strategy_name == "fixed_grid":
            config = FixedGridConfig(**params)
            return backtest_fixed_grid(df, config)
        
        elif strategy_name == "mean_reversion":
            config = MeanReversionConfig(**params)
            return backtest_mean_reversion(df, config)
        
        elif strategy_name == "momentum_mean_reversion":
            config = MomentumMeanReversionConfig(**params)
            return backtest_momentum_mean_reversion(df, config)
        
        else:
            return {
                "error": f"Unknown strategy: {strategy_name}",
                "total_return": 0.0,
                "max_drawdown": 0.0,
                "win_rate": 0.0,
                "num_trades": 0
            }
    
    except Exception as e:
        logger.error(f"Error running backtest for {strategy_name}: {e}")
        return {
            "error": str(e),
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "num_trades": 0
        }


# ============================================================================
# EXPORTS
# ============================================================================

__all__ = [
    # Config classes
    'ADXTrendConfig',
    'LondonNYBreakoutConfig', 
    'FixedGridConfig',
    'MeanReversionConfig',
    'MomentumMeanReversionConfig',
    # Functions
    'run_strategy_backtest',
    'PARAMETER_SPACES',
    # Legacy strategy imports
    'backtest_adx_trend_following',
    'backtest_london_ny_breakout',
    'backtest_fixed_grid',
    'backtest_mean_reversion',
    'backtest_momentum_mean_reversion'
]
