#!/usr/bin/env python3
"""
Continuous Backtesting System - NEW STRATEGY INTEGRATION

This version integrates all new and fixed strategies:
1. ADX Trend Following (NEW - research-backed)
2. London-NY Breakout (NEW - session-based)
3. Fixed Grid (FIXED - with proper risk management)
4. Mean Reversion (HFT replacement - BB + RSI)
5. Momentum Mean Reversion (RESEARCH - prop firm style)
6. Legacy strategies (grid, martingale, hft for comparison)

Author: FinRobot Research Team
Version: 3.0
"""

from __future__ import annotations

import os
import sys
import gc
import time
import json
import random
import traceback
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any
from pathlib import Path

import pandas as pd
import numpy as np

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

# Setup logging
from finrobot.utils.logging_config import setup_logging, get_logger
setup_logging()

from finrobot.utils.config import settings
from finrobot.utils.data_sources import fetch_candles

# Import NEW strategies
from finrobot.strategies.adx_trend import (
    ADXTrendConfig, 
    backtest_adx_trend_following
)
from finrobot.strategies.london_ny_breakout import (
    LondonNYBreakoutConfig,
    backtest_london_ny_breakout
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

# Import LEGACY strategies for comparison
from finrobot.strategies.grid import GridConfig, backtest_xauusd_grid
from finrobot.strategies.backtesting import BacktestConfig, backtest_trend_martingale
from finrobot.strategies.hft import HFTConfig, backtest_hft

# ============================================================================
# Configuration
# ============================================================================

LOG_DIR = Path("/home/openclaw/FinRobot/backtest_logs")
LOG_DIR.mkdir(exist_ok=True)

RESULTS_FILE = LOG_DIR / "backtest_results_new.jsonl"
BEST_PARAMS_FILE = LOG_DIR / "best_parameters_new.json"

SWEEP_INTERVAL = 10
CYCLE_SLEEP = 30
GC_INTERVAL = 5

# ============================================================================
# Logger
# ============================================================================

logger = get_logger("backtest_engine_v3")

# ============================================================================
# Parameter Spaces for NEW Strategies
# ============================================================================

PARAMETER_SPACES_NEW = {
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
    }
}


# Legacy parameter spaces (for comparison)
PARAMETER_SPACES_LEGACY = {
    "grid": {
        "grid_step_pips": [0.5, 1.0, 2.0, 3.0, 5.0, 7.5, 10.0],
        "take_profit_pips": [0.3, 0.5, 1.0, 1.5, 2.0, 3.0],
        "trend_ema_fast": [3, 5, 8, 12, 21],
        "trend_ema_slow": [8, 15, 21, 34, 55],
        "max_grid_levels": [2, 4, 6, 8, 12],
        "base_lot": [0.005, 0.01, 0.02, 0.05]
    },
    "martingale": {
        "multiplier": [1.25, 1.5, 1.75, 2.0, 2.25, 2.5],
        "base_lot": [0.005, 0.01, 0.02, 0.03],
        "max_steps": [2, 3, 4, 5, 6],
        "ema_fast": [3, 5, 8],
        "ema_slow": [12, 20, 34]
    },
    "hft": {
        "tick_threshold": [0.02, 0.05, 0.08, 0.12],
        "volume_filter": [10, 25, 50, 100],
        "latency_ms": [50, 100, 200],
        "spread_limit": [0.01, 0.02, 0.03]
    }
}


# ============================================================================
# Backtest Result Tracking
# ============================================================================

@dataclass
class BacktestResult:
    timestamp: str
    strategy: str
    parameters: Dict[str, Any]
    performance: Dict[str, float]
    data_bars: int
    cycle: int
    
    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "strategy": self.strategy,
            "parameters": self.parameters,
            "performance": self.performance,
            "data_bars": self.data_bars,
            "cycle": self.cycle
        }


class ResultsTracker:
    """Track and manage backtest results."""
    
    def __init__(self):
        self.best_results: Dict[str, BacktestResult] = {}
        self.cycle_count = 0
        self.total_tests = 0
        self.load_best_params()
        
    def load_best_params(self):
        """Load previously saved best parameters."""
        if BEST_PARAMS_FILE.exists():
            try:
                with open(BEST_PARAMS_FILE, 'r') as f:
                    data = json.load(f)
                    for strategy, result_data in data.items():
                        self.best_results[strategy] = BacktestResult(**result_data)
                logger.info(f"Loaded {len(self.best_results)} best parameter sets")
            except Exception as e:
                logger.warning(f"Could not load best parameters: {e}")
    
    def save_best_params(self):
        """Save best parameters to file."""
        data = {}
        for strategy, result in self.best_results.items():
            data[strategy] = result.to_dict()
        with open(BEST_PARAMS_FILE, 'w') as f:
            json.dump(data, f, indent=2, default=str)
    
    def log_result(self, result: BacktestResult):
        """Log a result to the results file."""
        self.total_tests += 1
        with open(RESULTS_FILE, 'a') as f:
            f.write(json.dumps(result.to_dict(), default=str) + '\n')
    
    def update_best(self, result: BacktestResult) -> bool:
        """Update best result if this one is better."""
        strategy = result.strategy
        
        # Skip if performance has error
        if 'error' in result.performance:
            return False
        
        current_best = self.best_results.get(strategy)
        
        if current_best is None:
            self.best_results[strategy] = result
            self.save_best_params()
            return True
        
        # Compare by total return
        current_return = current_best.performance.get('total_return', -999)
        new_return = result.performance.get('total_return', -999)
        
        if new_return > current_return:
            self.best_results[strategy] = result
            self.save_best_params()
            return True
        
        return False
    
    def print_summary(self):
        """Print concise summary of best results."""
        logger.info("-" * 80)
        logger.info(f"SUMMARY | Cycle {self.cycle_count} | Total Tests: {self.total_tests}")

        for strategy, result in self.best_results.items():
            perf = result.performance
            strat_letter = strategy[:1].upper()
            logger.info(
                f"  [{strat_letter}] Best: {perf.get('total_return', 0):+.2f}% | "
                f"WR:{perf.get('win_rate', 0):.1f}% | "
                f"DD:{perf.get('max_drawdown', 0):.2f}% | "
                f"T:{perf.get('total_trades', perf.get('num_trades', 0))}"
            )

        logger.info("-" * 80)


# ============================================================================
# Main Backtest Engine
# ============================================================================

class BacktestEngine:
    def __init__(self):
        self.tracker = ResultsTracker()
        self.running = True
        self.cycle_count = 0
        self.error_count = 0
        
        # Strategy selection - prioritize new strategies
        self.new_strategies = [
            "adx_trend",
            "london_ny_breakout", 
            "fixed_grid",
            "mean_reversion",
            "momentum_mean_reversion"
        ]
        
        self.legacy_strategies = [
            "grid",
            "martingale",
            "hft"
        ]
        
    def generate_random_params(self, strategy: str) -> dict:
        """Generate random parameters from the appropriate parameter space."""
        if strategy in PARAMETER_SPACES_NEW:
            space = PARAMETER_SPACES_NEW[strategy]
        elif strategy in PARAMETER_SPACES_LEGACY:
            space = PARAMETER_SPACES_LEGACY[strategy]
        else:
            return {}
        
        params = {}
        for key, values in space.items():
            params[key] = random.choice(values)
        return params
    
    def run_single_backtest(self, strategy: str, params: dict, df: pd.DataFrame) -> Optional[BacktestResult]:
        """Run a single backtest with given parameters."""
        try:
            # Use the integration module for new strategies
            if strategy in self.new_strategies:
                from finrobot.strategies.strategy_integration import run_strategy_backtest
                result = run_strategy_backtest(strategy, df, params)
            
            # Legacy strategies
            elif strategy == "grid":
                from finrobot.strategies.grid import GridConfig, backtest_xauusd_grid
                cfg = GridConfig(**params)
                result = backtest_xauusd_grid(df, cfg)
            
            elif strategy == "martingale":
                from finrobot.strategies.backtesting import BacktestConfig, backtest_trend_martingale
                cfg = BacktestConfig(**params)
                result = backtest_trend_martingale(df, cfg)
            
            elif strategy == "hft":
                from finrobot.strategies.hft import HFTConfig, backtest_hft
                cfg = HFTConfig(**params)
                result = backtest_hft(df, cfg)
            
            else:
                return None
            
            return BacktestResult(
                timestamp=datetime.utcnow().isoformat(),
                strategy=strategy,
                parameters=params,
                performance=result,
                data_bars=len(df),
                cycle=self.tracker.cycle_count
            )
            
        except Exception as e:
            logger.error(f"Backtest failed for {strategy}: {e}")
            logger.error(traceback.format_exc())
            return BacktestResult(
                timestamp=datetime.utcnow().isoformat(),
                strategy=strategy,
                parameters=params,
                performance={"error": str(e)},
                data_bars=len(df),
                cycle=self.tracker.cycle_count
            )
    
    def run_cycle(self, full_sweep: bool = False):
        """Run one backtest cycle."""
        self.tracker.cycle_count += 1
        cycle_num = self.tracker.cycle_count
        self.cycle_count = cycle_num
        df = None
        
        try:
            # Log cycle header every 10 cycles
            if cycle_num % 10 == 1 or full_sweep:
                logger.info(f"{'='*60}")
                logger.info(f"BACKTEST CYCLE #{cycle_num}")
                logger.info(f"{'='*60}")
            
            # Load data
            try:
                df = fetch_candles(limit=10000)
                if cycle_num % 10 == 1 or full_sweep:
                    logger.info(f"Loaded {len(df)} bars")
            except Exception as e:
                logger.error(f"Failed to load data: {e}")
                self.error_count += 1
                return
            
            # Determine how many tests to run
            tests_per_strategy = 3 if full_sweep else 1
            
            # Test NEW strategies (prioritized)
            for strategy in self.new_strategies:
                for i in range(tests_per_strategy):
                    params = self.generate_random_params(strategy)
                    
                    result = self.run_single_backtest(strategy, params, df)
                    
                    if result:
                        self.tracker.log_result(result)
                        is_best = self.tracker.update_best(result)
                        
                        perf = result.performance
                        if 'error' not in perf:
                            ret = perf.get('total_return', 0)
                            wr = perf.get('win_rate', 0)
                            trades = perf.get('num_trades', perf.get('total_trades', 0))
                            
                            if is_best:
                                logger.info(f"  [{strategy[:3].upper()}] ★NEW BEST: {ret:+.2f}% WR:{wr:.1f}% T:{trades}")
            
            # Test LEGACY strategies (for comparison, less frequently)
            if cycle_num % 5 == 0:  # Every 5th cycle
                for strategy in self.legacy_strategies:
                    params = self.generate_random_params(strategy)
                    
                    result = self.run_single_backtest(strategy, params, df)
                    
                    if result:
                        self.tracker.log_result(result)
                        is_best = self.tracker.update_best(result)
            
            # Print summary every 10 cycles
            if cycle_num % 10 == 0 or full_sweep:
                self.tracker.print_summary()
            
            # Decay error count
            self.error_count = max(0, self.error_count - 1)
            
        except Exception as e:
            self.error_count += 1
            logger.error(f"Error in cycle {cycle_num}: {e}")
            logger.error(traceback.format_exc())
            
        finally:
            # Cleanup
            if df is not None:
                del df
                df = None
            
            if cycle_num % GC_INTERVAL == 0:
                gc.collect()
    
    def run(self, cycles: Optional[int] = None):
        """Run the backtest engine."""
        logger.info("="*60)
        logger.info("CONTINUOUS BACKTEST ENGINE V3.0 (NEW STRATEGIES)")
        logger.info("="*60)
        logger.info(f"Strategies: {len(self.new_strategies)} new, {len(self.legacy_strategies)} legacy")
        logger.info(f"New strategies: {', '.join(self.new_strategies)}")
        logger.info(f"Results: {RESULTS_FILE}")
        logger.info(f"Best params: {BEST_PARAMS_FILE}")
        logger.info("="*60)
        
        try:
            cycle_count = 0
            consecutive_errors = 0
            
            while self.running:
                cycle_count += 1
                
                full_sweep = (cycle_count % SWEEP_INTERVAL) == 0
                
                try:
                    self.run_cycle(full_sweep=full_sweep)
                    consecutive_errors = 0
                except Exception as e:
                    consecutive_errors += 1
                    logger.error(f"Cycle {cycle_count} failed (consecutive errors: {consecutive_errors}): {e}")
                    
                    if consecutive_errors >= 5:
                        logger.critical("Too many consecutive errors, stopping engine")
                        break
                
                if cycles is not None and cycle_count >= cycles:
                    logger.info(f"Reached max cycles ({cycles}), stopping.")
                    break
                
                if self.running:
                    time.sleep(CYCLE_SLEEP)
                
        except KeyboardInterrupt:
            logger.info("\nReceived interrupt signal, shutting down...")
        finally:
            self.running = False
            logger.info("Backtest engine stopped.")
            self.tracker.print_summary()


def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Continuous Backtesting V3.0 (New Strategies)")
    parser.add_argument("--cycles", type=int, default=None, help="Number of cycles to run")
    parser.add_argument("--sleep", type=int, default=30, help="Seconds to sleep between cycles")
    args = parser.parse_args()
    
    global CYCLE_SLEEP
    CYCLE_SLEEP = args.sleep
    
    engine = BacktestEngine()
    engine.run(cycles=args.cycles)


if __name__ == "__main__":
    main()
