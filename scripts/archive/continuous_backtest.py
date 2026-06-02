#!/usr/bin/env python3
"""
Continuous Backtesting System for XAUUSD 1m Data - IMPROVED VERSION

Key improvements:
- Garbage collection after each cycle
- Memory management (explicit cleanup)
- Longer sleep intervals (30s instead of 2s)
- Better error recovery
- Rotating log files
- Memory usage monitoring
- Consolidated logging to single file
"""

from __future__ import annotations

import os
import sys
import gc
import time
import json
import random
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Any
from pathlib import Path

import pandas as pd
import numpy as np

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

# Setup consolidated logging first
from finrobot.utils.logging_config import setup_logging, get_logger
setup_logging()

from finrobot.utils.config import settings
from finrobot.utils.data_sources import fetch_candles
from finrobot.strategies.grid import GridConfig, backtest_xauusd_grid
from finrobot.strategies.backtesting import BacktestConfig, backtest_trend_martingale
from finrobot.strategies.hft import HFTConfig, backtest_hft

# ============================================================================
# Configuration - IMPROVED
# ============================================================================

LOG_DIR = Path("/home/openclaw/FinRobot/backtest_logs")
LOG_DIR.mkdir(exist_ok=True)

RESULTS_FILE = LOG_DIR / "backtest_results.jsonl"
BEST_PARAMS_FILE = LOG_DIR / "best_parameters.json"
SUMMARY_FILE = LOG_DIR / "summary.txt"

# INCREASED: How often to run full parameter sweep (in cycles)
SWEEP_INTERVAL = 10

# INCREASED: Sleep between cycles (seconds) - was 2, now 30
CYCLE_SLEEP = 30

# NEW: Memory threshold for warning (MB)
MEMORY_WARNING_MB = 300

# NEW: Force GC every N cycles
GC_INTERVAL = 5

# ============================================================================
# Get consolidated logger
# ============================================================================

logger = get_logger("backtest_engine")

# ============================================================================
# Parameter Space Definitions
# ============================================================================

PARAMETER_SPACE = {
    "grid": {
        "grid_step_pips": [0.5, 1.0, 2.0, 3.0, 5.0, 7.5, 10.0],
        "take_profit_pips": [0.3, 0.5, 1.0, 1.5, 2.0, 3.0],
        "trend_ema_fast": [3, 5, 8, 12, 21],
        "trend_ema_slow": [8, 15, 21, 34, 55],
        "max_grid_levels": [2, 4, 6, 8, 12],
        "base_lot": [0.005, 0.01, 0.02, 0.05],
        "fee_bps": [1.0, 2.0, 3.0],
        "pip_value": [0.01]
    },
    "martingale": {
        "multiplier": [1.25, 1.5, 1.75, 2.0, 2.25, 2.5],
        "base_lot": [0.005, 0.01, 0.02, 0.03],
        "max_steps": [2, 3, 4, 5, 6],
        "ema_fast": [3, 5, 8],
        "ema_slow": [12, 20, 34],
        "fee_bps": [1.0, 2.0, 3.0]
    },
    "hft": {
        "tick_threshold": [0.02, 0.05, 0.08, 0.12],
        "volume_filter": [10, 25, 50, 100],
        "latency_ms": [50, 100, 200],
        "spread_limit": [0.01, 0.02, 0.03],
        "fast_window": [3, 5, 8],
        "slow_window": [12, 20, 34],
        "fee_bps": [1.0, 2.0, 3.0],
        "risk_per_trade": [0.002, 0.005, 0.01]
    }
}

# ============================================================================
# Results Tracking
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
# Main Backtest Engine - IMPROVED
# ============================================================================

class BacktestEngine:
    def __init__(self):
        self.tracker = ResultsTracker()
        self.running = True
        self.current_params = {
            "grid": GridConfig(),
            "martingale": BacktestConfig(),
            "hft": HFTConfig()
        }
        self.cycle_count = 0
        self.error_count = 0
        
    def generate_random_params(self, strategy: str) -> dict:
        """Generate random parameters from the parameter space."""
        params = {}
        for key, values in PARAMETER_SPACE[strategy].items():
            params[key] = random.choice(values)
        return params
    
    def run_single_backtest(self, strategy: str, params: dict, df: pd.DataFrame) -> Optional[BacktestResult]:
        """Run a single backtest with given parameters."""
        try:
            if strategy == "grid":
                cfg = GridConfig(**params)
                result = backtest_xauusd_grid(df, cfg)
            elif strategy == "martingale":
                cfg = BacktestConfig(**params)
                result = backtest_trend_martingale(df, cfg)
            elif strategy == "hft":
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
            return BacktestResult(
                timestamp=datetime.utcnow().isoformat(),
                strategy=strategy,
                parameters=params,
                performance={"error": str(e)},
                data_bars=len(df),
                cycle=self.tracker.cycle_count
            )
    
    def run_cycle(self, full_sweep: bool = False):
        """Run one backtest cycle with memory management."""
        self.tracker.cycle_count += 1
        cycle_num = self.tracker.cycle_count
        self.cycle_count = cycle_num
        df = None
        
        try:
            # Only log cycle header every 10 cycles to reduce noise
            if cycle_num % 10 == 1 or full_sweep:
                logger.info(f"{'='*60}")
                logger.info(f"BACKTEST CYCLE #{cycle_num}")
                logger.info(f"{'='*60}")
            
            # Check memory before loading data
            try:
                process = psutil.Process()
                mem_before = process.memory_info().rss / 1024 / 1024
                if mem_before > MEMORY_WARNING_MB:
                    logger.warning(f"High memory usage: {mem_before:.1f}MB")
            except:
                pass
            
            # Load data
            try:
                df = fetch_candles(limit=10000)
                if cycle_num % 10 == 1 or full_sweep:
                    logger.info(f"Loaded {len(df)} bars | Progress: {cycle_num}/10000 ({cycle_num/100:.1f}%)")
            except Exception as e:
                logger.error(f"Failed to load data: {e}")
                self.error_count += 1
                return

            # Determine how many parameter combinations to test
            if full_sweep:
                tests_per_strategy = 10
                if cycle_num % 10 == 1:
                    logger.info("Running FULL PARAMETER SWEEP")
            else:
                # REDUCED: Run only 1 test per strategy instead of 3
                tests_per_strategy = 1

            # Track best results in this cycle
            cycle_best = {"strategy": "-", "return": -999, "win_rate": 0}

            # Run backtests for each strategy
            for strategy in ["grid", "martingale", "hft"]:
                for i in range(tests_per_strategy):
                    # Generate parameters
                    if i == 0:
                        if strategy == "grid":
                            params = asdict(GridConfig())
                        elif strategy == "martingale":
                            params = asdict(BacktestConfig())
                        else:
                            params = asdict(HFTConfig())
                    else:
                        params = self.generate_random_params(strategy)

                    # Run backtest
                    result = self.run_single_backtest(strategy, params, df)

                    if result:
                        self.tracker.log_result(result)
                        is_best = self.tracker.update_best(result)

                        perf = result.performance
                        if 'error' not in perf:
                            ret = perf.get('total_return', 0)
                            wr = perf.get('win_rate', 0)
                            trades = perf.get('total_trades', perf.get('num_trades', 0))

                            # Track cycle best
                            if ret > cycle_best["return"]:
                                cycle_best = {
                                    "strategy": strategy[:1].upper(),
                                    "return": ret,
                                    "win_rate": wr
                                }

                            # Log only best results
                            if is_best:
                                logger.info(f"  [{strategy[:1].upper()}] ★NEW BEST: {ret:+.2%} WR:{wr:.1f}% T:{trades}")

            # Only print summary every 10 cycles or on full sweep
            if cycle_num % 10 == 0 or full_sweep:
                self.tracker.print_summary()
                
            # Record success
            self.error_count = max(0, self.error_count - 1)  # Decay error count
            
        except Exception as e:
            self.error_count += 1
            logger.error(f"Error in cycle {cycle_num}: {e}")
            logger.error(traceback.format_exc())
            
        finally:
            # ALWAYS clean up DataFrame to free memory
            if df is not None:
                del df
                df = None
                
            # Force garbage collection periodically
            if cycle_num % GC_INTERVAL == 0:
                gc.collect()
                try:
                    process = psutil.Process()
                    mem_mb = process.memory_info().rss / 1024 / 1024
                    logger.info(f"GC completed. Memory: {mem_mb:.1f}MB")
                except:
                    pass
    
    def run(self, cycles: Optional[int] = None):
        """Run the backtest engine continuously with error recovery."""
        logger.info("="*60)
        logger.info("CONTINUOUS BACKTEST ENGINE STARTED (IMPROVED)")
        logger.info("="*60)
        logger.info(f"Data: XAUUSD 1m from local CSV")
        logger.info(f"Strategies: Grid, Martingale, HFT")
        logger.info(f"Results logged to: {LOG_DIR}")
        logger.info(f"Cycle sleep: {CYCLE_SLEEP}s")
        logger.info(f"Tests per cycle: 1 per strategy (reduced from 3)")
        logger.info("="*60)
        
        try:
            cycle_count = 0
            consecutive_errors = 0
            
            while self.running:
                cycle_count += 1
                
                # Run full sweep every N cycles
                full_sweep = (cycle_count % SWEEP_INTERVAL) == 0
                
                try:
                    self.run_cycle(full_sweep=full_sweep)
                    consecutive_errors = 0  # Reset on success
                except Exception as e:
                    consecutive_errors += 1
                    logger.error(f"Cycle {cycle_count} failed (consecutive errors: {consecutive_errors}): {e}")
                    
                    if consecutive_errors >= 5:
                        logger.critical("Too many consecutive errors, stopping engine")
                        break
                
                # Check if we've reached max cycles
                if cycles is not None and cycle_count >= cycles:
                    logger.info(f"Reached max cycles ({cycles}), stopping.")
                    break
                
                # Sleep before next cycle
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
    
    parser = argparse.ArgumentParser(description="Continuous Backtesting for XAUUSD (IMPROVED)")
    parser.add_argument("--cycles", type=int, default=None, help="Number of cycles to run (default: infinite)")
    parser.add_argument("--sleep", type=int, default=None, help="Seconds to sleep between cycles (default: 30)")
    args = parser.parse_args()
    
    sleep_interval = args.sleep if args.sleep else CYCLE_SLEEP
    
    engine = BacktestEngine()
    engine.run(cycles=args.cycles, sleep_interval=sleep_interval)


if __name__ == "__main__":
    main()
