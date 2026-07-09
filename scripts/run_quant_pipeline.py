#!/usr/bin/env python3
"""Full quantitative research pipeline: data → features → regime → ML → walk-forward → report.

This is the main entry point for the institutional-grade research workflow.
It produces an honest, statistically rigorous evaluation of whether the
current strategies have real edge.

Usage:
    .venv/bin/python scripts/run_quant_pipeline.py
    .venv/bin/python scripts/run_quant_pipeline.py --bars 50000 --timeframe M5
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data" / "XAUUSD1.csv")
    parser.add_argument("--bars", type=int, default=0, help="Limit bars (0=all)")
    parser.add_argument("--timeframe", default="M1", help="M1 or M5")
    parser.add_argument("--output", type=Path, default=ROOT / "state" / "research" / "pipeline_report.json")
    parser.add_argument("--n-folds", type=int, default=5)
    parser.add_argument("--optimize", action="store_true", help="Run Optuna optimization")
    parser.add_argument("--opt-trials", type=int, default=50)
    args = parser.parse_args(argv)

    print("=" * 70)
    print("FINROBOT QUANTITATIVE RESEARCH PIPELINE")
    print("=" * 70)
    t0 = time.time()

    # --- Step 1: Load data ---
    print("\n[1/7] Loading data...")
    df = _load_data(args.data, max_bars=args.bars)
    print(f"  Loaded {len(df):,} bars from {args.data.name}")
    print(f"  Date range: {pd.to_datetime(df['time'].iloc[0], unit='s')} → "
          f"{pd.to_datetime(df['time'].iloc[-1], unit='s')}")

    # --- Step 2: Compute features ---
    print("\n[2/7] Computing features...")
    from finrobot.research.features import compute_features, compute_target
    features = compute_features(df, timeframe=args.timeframe)
    target = compute_target(df, horizon=5)
    valid = features.notna().all(axis=1) & target.notna()
    print(f"  {len(features.columns)} features computed")
    print(f"  {valid.sum():,} valid rows (of {len(df):,})")

    # --- Step 3: Fit regime model ---
    print("\n[3/7] Fitting regime model (HMM, 3 states)...")
    from finrobot.research.regime import fit_regime_model, predict_regime, regime_stability_score
    train_cutoff = int(len(df) * 0.7)
    regime_model = fit_regime_model(df.iloc[:train_cutoff], n_states=3)
    regimes = predict_regime(regime_model, df)
    stability = regime_stability_score(regimes)
    print(f"  State labels: {regime_model.state_labels}")
    print(f"  Stability score: {stability:.3f} (>0.7 = good)")
    print(f"  Regime distribution:")
    for regime, count in regimes.value_counts().items():
        print(f"    {regime}: {count:,} bars ({100*count/len(df):.1f}%)")

    # --- Step 4: Train ML model ---
    print("\n[4/7] Training ML model (LightGBM walk-forward)...")
    from finrobot.research.models import train_walkforward
    try:
        ml_result = train_walkforward(
            features, target,
            n_folds=args.n_folds,
            min_train_size=max(5000, int(len(df) * 0.3)),
            purge_bars=60,
            embargo_bars=12,
        )
        print(f"  Mean AUC: {ml_result.mean_auc:.4f}")
        print(f"  Mean Accuracy: {ml_result.mean_accuracy:.4f}")
        print(f"  OOS predictions: {len(ml_result.all_predictions):,}")
        print(f"  Top features:")
        sorted_imp = sorted(ml_result.feature_importance_agg.items(), key=lambda x: x[1], reverse=True)[:5]
        for feat, imp in sorted_imp:
            print(f"    {feat}: {imp:.0f}")
        ml_auc = ml_result.mean_auc
    except ValueError as e:
        print(f"  Skipped: {e}")
        ml_result = None
        ml_auc = 0.5

    # --- Step 5: Walk-forward backtest with regime gating ---
    print("\n[5/7] Running regime-gated walk-forward backtest...")
    from finrobot.backtest.engine import BacktestConfig, Backtester
    from finrobot.backtest.strategies.xau_atr_impulse import XauAtrImpulseStrategy, XauAtrImpulseParams
    from finrobot.backtest.metrics import compute_metrics
    from finrobot.backtest import FillConfig, PositionSizer, WalkForwardConfig, run_walkforward

    bars_list = df.to_dict("records")

    # Run ungated (baseline)
    def ungated_factory():
        return XauAtrImpulseStrategy()

    wf_config = WalkForwardConfig(
        n_folds=args.n_folds,
        n_purge_bars=50,
        n_embargo_bars=50,
        min_train_bars=max(1000, int(len(bars_list) * 0.15)),
        min_test_bars=max(100, int(len(bars_list) * 0.05)),
    )
    bt_config = BacktestConfig(
        fill_config=FillConfig(),
        sizer=PositionSizer(
            risk_per_trade_fraction=0.001,
            daily_loss_cap_fraction=0.01,
            max_lot_per_trade=0.10,
            max_positions_per_symbol=2,
        ),
    )

    try:
        wf_result = run_walkforward(
            bars_list,
            strategy_factory=ungated_factory,
            config=wf_config,
            backtest_config=bt_config,
        )
        ungated_sharpe = wf_result.aggregated_metrics.sharpe_ratio.mean
        ungated_pf = wf_result.aggregated_metrics.profit_factor.mean
        ungated_trades = int(wf_result.aggregated_metrics.n_trades.mean)
        ungated_verdict = wf_result.verdict.status
        print(f"  Ungated ATR Impulse:")
        print(f"    Sharpe: {ungated_sharpe:.3f}")
        print(f"    Profit Factor: {ungated_pf:.3f}")
        print(f"    Avg trades/fold: {ungated_trades}")
        print(f"    Verdict: {ungated_verdict}")
        print(f"    Consistency: {wf_result.walk_forward_stability.consistency_score:.2f}")
    except Exception as e:
        print(f"  Walk-forward failed: {e}")
        ungated_sharpe = 0.0
        ungated_pf = 0.0
        ungated_trades = 0
        ungated_verdict = "FAIL"

    # --- Step 6: Significance testing ---
    print("\n[6/7] Statistical significance testing...")
    from finrobot.research.significance import (
        deflated_sharpe_ratio,
        minimum_backtest_length,
        bootstrap_metric,
    )

    n_experiments = 44 + 1  # existing experiments + this one
    dsr = deflated_sharpe_ratio(
        observed_sharpe=ungated_sharpe,
        n_trials=n_experiments,
        n_observations=ungated_trades * args.n_folds,
        skewness=0.0,
        kurtosis=0.0,
    )
    min_length = minimum_backtest_length(max(0.01, ungated_sharpe))

    print(f"  Deflated Sharpe Ratio test (correcting for {n_experiments} trials):")
    print(f"    Observed Sharpe: {dsr.observed_sharpe:.3f}")
    print(f"    Expected max under null: {dsr.expected_max_sharpe:.3f}")
    print(f"    DSR statistic: {dsr.deflated_sharpe:.3f}")
    print(f"    p-value: {dsr.p_value:.4f}")
    print(f"    Significant at 5%: {'YES ✓' if dsr.is_significant else 'NO ✗'}")
    print(f"  Minimum observations needed: {min_length:,}")
    print(f"  Observations available: {ungated_trades * args.n_folds}")

    # --- Step 7: Optimization (optional) ---
    opt_result = None
    if args.optimize:
        print(f"\n[7/7] Running Optuna optimization ({args.opt_trials} trials)...")
        from finrobot.research.optimizer import optimize_strategy

        split_idx = int(len(bars_list) * 0.6)
        train_bars = bars_list[:split_idx]
        test_bars = bars_list[split_idx:]

        def make_strategy(params):
            return XauAtrImpulseStrategy(
                impulse_atr_mult=params.get("impulse_atr_mult", 0.12),
                stop_atr_mult=params.get("stop_atr_mult", 1.2),
                tp_atr_mult=params.get("tp_atr_mult", 2.4),
                rsi_long_ceiling=params.get("rsi_long_ceiling", 80.0),
                rsi_short_floor=params.get("rsi_short_floor", 20.0),
            )

        param_space = {
            "impulse_atr_mult": (0.05, 0.30, 0.01),
            "stop_atr_mult": (0.8, 2.0, 0.1),
            "tp_atr_mult": (1.5, 4.0, 0.1),
            "rsi_long_ceiling": (70.0, 90.0, 2.0),
            "rsi_short_floor": (10.0, 30.0, 2.0),
        }

        opt_result = optimize_strategy(
            strategy_factory=make_strategy,
            param_space=param_space,
            train_bars=train_bars,
            test_bars=test_bars,
            backtest_config=bt_config,
            n_trials=args.opt_trials,
            timeframe=args.timeframe,
        )
        print(f"  Best Sharpe: {opt_result.best_sharpe:.3f}")
        print(f"  Best params: {opt_result.best_params}")
    else:
        print("\n[7/7] Optimization skipped (use --optimize to enable)")

    # --- Summary ---
    elapsed = time.time() - t0
    print("\n" + "=" * 70)
    print("PIPELINE SUMMARY")
    print("=" * 70)
    print(f"  Data: {len(df):,} M1 bars")
    print(f"  Regime stability: {stability:.3f}")
    print(f"  ML AUC (OOS): {ml_auc:.4f}")
    print(f"  Walk-forward Sharpe: {ungated_sharpe:.3f}")
    print(f"  Walk-forward PF: {ungated_pf:.3f}")
    print(f"  DSR significant: {'YES' if dsr.is_significant else 'NO'} (p={dsr.p_value:.4f})")
    print(f"  Verdict: {ungated_verdict}")
    print(f"  Elapsed: {elapsed:.1f}s")

    if dsr.is_significant and ungated_verdict == "PASS":
        print("\n  → STRATEGY HAS STATISTICALLY SIGNIFICANT EDGE")
    elif ungated_sharpe > 0 and ungated_pf > 1.0:
        print("\n  → POSITIVE BUT NOT STATISTICALLY SIGNIFICANT — need more data")
    else:
        print("\n  → NO EDGE DETECTED — strategy is not profitable OOS")

    # Save report
    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bars": len(df),
        "timeframe": args.timeframe,
        "regime_stability": stability,
        "regime_distribution": regimes.value_counts().to_dict(),
        "ml_auc": ml_auc,
        "ml_top_features": sorted_imp if ml_result else [],
        "walkforward_sharpe": ungated_sharpe,
        "walkforward_pf": ungated_pf,
        "walkforward_trades_per_fold": ungated_trades,
        "walkforward_verdict": ungated_verdict,
        "dsr_p_value": dsr.p_value,
        "dsr_significant": dsr.is_significant,
        "min_observations_needed": min_length,
        "optimization": {
            "best_params": opt_result.best_params if opt_result else None,
            "best_sharpe": opt_result.best_sharpe if opt_result else None,
        } if opt_result else None,
        "elapsed_seconds": elapsed,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str))
    print(f"\n  Report saved to: {args.output}")

    return 0


def _load_data(path: Path, max_bars: int = 0) -> pd.DataFrame:
    df = pd.read_csv(
        path, sep="\t", header=None,
        names=["time", "open", "high", "low", "close", "volume"],
    )
    ts = pd.to_datetime(df["time"])
    df["time"] = (ts - pd.Timestamp("1970-01-01")) // pd.Timedelta("1s")
    if max_bars > 0:
        df = df.iloc[:max_bars]
    return df.reset_index(drop=True)


if __name__ == "__main__":
    sys.exit(main())
