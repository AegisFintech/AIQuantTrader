# FinRobot — Quant Analysis & Development Guide

## System Overview

MT5-first autonomous demo trading system for XAUUSD. MetaTrader 5 runs under Wine/Xvfb, the FinRobot EA trades inside MT5, PM2 keeps MT5 + autonomous review loop + dashboard alive. Python layer mirrors the MQL5 signal logic for backtesting and provides an institutional-grade research framework (ML signals, regime detection, significance testing).

**Active EA:** `broker/mt5/FinRobotBridgeEA.mq5` v1.35  
**Symbols:** XAUUSD only (BTC retired)  
**Timeframe:** M5 (AutoTimeframe=PERIOD_M5)  
**Broker:** ICMarketsSC-Demo  

---

## Quant Analysis (2026-06-18)

### What's Working Well

- **Risk sizing is correct.** `DailyRiskPerTradeFraction=0.001` (0.10% equity/trade), `DailyLossLimitFraction=0.01` (1% daily cap). `PositionSizer` and `DailyRiskSizer` in Python mirror the EA path — the parity is real and valuable.
- **Walk-forward framework is properly built.** Purge + embargo windows in `finrobot/backtest/walkforward.py` guard against leakage. Most hobby quant projects skip this.
- **Backtest engine is deterministic and EA-faithful.** `engine.py` is event-driven, `fills.py` models spread/slippage/commission/swap. EMA/RSI/MACD/ATR all use Wilder-style smoothing matching MT5's calculation. `xau_gates.py` and `btc_gates.py` directly mirror the MQL5 signal paths.
- **SMC implementation is coherent.** FVG, order blocks, liquidity sweeps, structure shifts, and PDA scoring in both MQL5 and Python are consistent and correctly gated (SMC score ≥ 3 for XAU, ≥ 2 for BTC).
- **Multi-layered risk controls.** Spread cap, session gate, max positions, same-direction cap, cooldown, daily loss limit, dynamic break-even — each layer is meaningful.

---

### Fixed Bugs (2026-07-09)

- **SL/TP gap-open exit** — `engine.py:308-325` now correctly uses `low <= sl` (BUY) / `high >= sl` (SELL). Edge-case tests added.
- **Metrics annualization** — `metrics.py` now parameterizes `periods_per_year` by timeframe (M1/M5/H1/D1).
- **O(n²) rsi_divergence** — `indicators.py` vectorized with `pandas.rolling()`, ~100x faster.
- **Missing metrics** — Added: Sortino, Calmar, skewness, kurtosis, max consecutive losses, recovery factor, tail ratio, monthly P&L buckets.

---

### Issues Ranked by Severity

#### 1. R:R 1.5:1 is untested out-of-sample
`StopAtrMultiplier=1.2`, `TakeProfitAtrMultiplier=1.8` → R:R = 1.5. After spread/commission, effective R:R ≈ 1.2-1.3. Breakeven win rate = 43-45%. The 100K bar dataset covers only Jan-May 2026 (3.5 months) — not enough to assess regime robustness.  
**Target:** 2-3 years minimum across trending, ranging, and high-volatility event regimes.

#### 2. High confluence lot multiplier 3x is aggressive
`HighConfluenceLotMultiplier=3.0` at `HighConfluenceScore=5`. With 2 max positions open, a high-confidence double entry = 6x base lot. A string of wrong high-confidence calls causes outsized drawdown.  
**Target:** Cap at 1.5-2x, or validate the SMC score ≥ 5 edge in walk-forward before using it for sizing.

#### 3. No regime detection in the live EA
EA fires on any bar passing SMC score + session gate. In ranging/choppy conditions (~40-50% of XAUUSD time), EMA crosses are noisy. ADX already computed in `indicators.py` but not in the EA.  
**Fix:** Add ADX(14) > 20 gate to `FinRobotBridgeEA.mq5`.

#### 4. SMC structure is M5-only — too shallow
`smc_lookback=48` M5 bars = 4 hours of structure. Real SMC uses H4 for major swings, H1 for intermediate, M5 for entry. Micro-structure shifts on M5-only get invalidated frequently.  
**Fix:** Add H1 EMA/trend gate as a higher-timeframe filter.

#### 5. FVG threshold too permissive
`FvgMinAtrMultiplier=0.15` — any gap > 0.15× ATR qualifies. Normal M5 spread variation creates false FVG signals.  
**Fix:** Raise to 0.30-0.50× ATR. Optionally require FVG to be untouched on retest.

#### 6. Liquidity sweep threshold too loose
`LiquiditySweepAtrMultiplier=0.10` — essentially any wick below a prior low qualifies. Real liquidity sweeps need clear displacement (0.30-0.50× ATR) + sharp reversal.  
**Fix:** Raise to 0.30× ATR minimum.

#### 7. Python research code disconnected from live EA path
`hft.py`, `strategies/grid.py`, `strategies/harmonics.py`, and `indicators.py` contain SMC logic that differs from `SmartMoney.mqh`. These do not feed the parity backtester. Creates false sense of research activity.  
**Fix:** Either integrate into the `finrobot/backtest/` path or remove.

#### 8. O(n²) loops in research indicators
`indicators.py` and `smart_money.py` use `.iloc[i]` inside for loops. On 100K bars ≈ 10 seconds per call.  
**Fix:** Vectorize with pandas shift/rolling or numpy — reduces to <100ms.

#### 9. Missing key metrics in `backtest/metrics.py`
Only has Sharpe. Missing: Sortino, Calmar, max drawdown duration, trade P&L distribution (skewness/kurtosis), monthly P&L buckets.

#### 10. Deprecated pandas API in `grid.py`
`fillna(method="ffill")` → FutureWarning on newer pandas. Replace with `.ffill()`.

---

### Gap vs Best-Practice Quant Systems

| Practice | Current State | Gap |
|---|---|---|
| Multi-timeframe structure | M5 only | Add H1 or H4 trend gate |
| Regime filter | None | Add ADX > 20 gate |
| Out-of-sample validation | Not done for live params | Run walk-forward on EA defaults now |
| Monte Carlo drawdown simulation | Not present | Add to metrics pipeline |
| Correlation monitoring (XAU/BTC) | Not tracked | Both can risk-off simultaneously |
| R:R ≥ 2:1 | 1.5:1 (1.2x/1.8x ATR) | Raise TP to 2.4x ATR for 2:1 |
| FVG quality filter | 0.15x ATR | Raise to 0.35x ATR |
| Liquidity sweep quality | 0.10x ATR | Raise to 0.30x ATR |
| Benchmark comparison | None | Add buy-and-hold XAU curve comparison |
| Parameter sensitivity analysis | None | Vary SL/TP multiplier ±20%, measure Sharpe |

---

### Priority Action List (updated 2026-07-09)

1. ~~Fix backtest exit bug~~ DONE
2. ~~Add Sortino/Calmar/skewness/kurtosis~~ DONE
3. ~~Vectorize research indicators~~ DONE
4. **Acquire multi-year M1 data** — current 3.5 months is too short for ML; need 2020-2026 via MT5 export
5. **Retrain ML with multi-year data** — current AUC 0.52 is random; needs 2+ years
6. **Tighten FVG threshold** `FvgMinAtrMultiplier` 0.15 → 0.30; **sweep** 0.10 → 0.30
7. **Add H1 trend gate** as higher-timeframe filter for SMC structure
8. **Fix deprecated `.fillna(method=)`** in `strategies/grid.py`

### Research Findings (2026-07-09)

Pipeline run on 100K M1 bars (Jan-May 2026):

| Strategy | Walk-Forward Sharpe | Profit Factor | Verdict |
|----------|-------------------|---------------|---------|
| ATR Impulse (ungated) | -20.7 | 0.61 | FAIL |
| ATR Impulse (SMC>=4) | -1.1 | 0.92 | FAIL |
| ATR Impulse (SMC>=3) | -6.0 | 0.73 | FAIL |
| Quick Momentum (ungated) | -10.9 | 0.70 | FAIL |

- **No strategy has statistically significant edge** (DSR p=1.0 after 49 trials)
- Regime model shows XAUUSD is 48% ranging, 39% volatile, 13% trending
- ML top features: time-of-day (hour_sin/cos), h1_ema_slope, rvol_240 → seasonal patterns are lowest-hanging fruit
- **Bottleneck is data** — 3.5 months is insufficient for ML training or robust validation

---

## Key File Map

### Live Trading (MQL5)

| Path | Purpose |
|---|---|
| `broker/mt5/FinRobotBridgeEA.mq5` | Live EA — all trading logic, risk controls, SMC gates |
| `broker/mt5/SmartMoney.mqh` | MQL5 SMC: FVG, OB, sweep, structure, PDA scoring |
| `broker/mt5/RiskManagement.mqh` | MQL5 daily PnL, session gate, break-even |

### Backtest Engine

| Path | Purpose |
|---|---|
| `finrobot/backtest/engine.py` | Deterministic bar-by-bar backtest engine |
| `finrobot/backtest/fills.py` | Fill simulation: spread, slippage, commission, swap |
| `finrobot/backtest/position.py` | Position state + `DailyRiskSizer` (mirrors EA sizing) |
| `finrobot/backtest/metrics.py` | Sharpe, Sortino, Calmar, skewness, kurtosis, recovery factor, monthly P&L |
| `finrobot/backtest/walkforward.py` | Purged walk-forward with embargo windows |
| `finrobot/backtest/strategies/xau_gates.py` | Python port of MQL5 SMC/PDA gate logic |
| `finrobot/backtest/strategies/xau_gated.py` | XAU gate wrapper strategy |
| `finrobot/backtest/strategies/xau_atr_impulse.py` | ATR impulse breakout (mirrors EA) |
| `finrobot/backtest/strategies/xau_quick_momentum.py` | EMA crossover momentum (mirrors EA) |
| `finrobot/backtest/strategies/xau_mean_reversion.py` | Mean reversion sleeve (ranging regime) |
| `finrobot/backtest/strategies/xau_ml_ensemble.py` | ML-driven signal sleeve |
| `finrobot/backtest/strategies/xau_seasonal.py` | Calendar/session patterns sleeve |

### Research Framework (added 2026-07-09)

| Path | Purpose |
|---|---|
| `finrobot/research/features.py` | 24-feature engineering pipeline (price, vol, technical, MTF, seasonal) |
| `finrobot/research/regime.py` | HMM regime detection (trending/ranging/volatile) |
| `finrobot/research/models.py` | LightGBM walk-forward training with purge+embargo |
| `finrobot/research/significance.py` | Deflated Sharpe Ratio, Monte Carlo permutation, bootstrap CI |
| `finrobot/research/optimizer.py` | Optuna Bayesian parameter optimization (OOS objective) |
| `finrobot/research/experiments.py` | Experiment record tracking |
| `finrobot/research/comparison.py` | Challenger-vs-incumbent promotion comparison |
| `finrobot/research/registry.py` | DuckDB experiment registry |

### Risk Management

| Path | Purpose |
|---|---|
| `finrobot/risk/kelly.py` | Fractional Kelly sizing with exponential decay |
| `finrobot/risk/vol_target.py` | Volatility targeting (constant-vol position sizing) |
| `finrobot/risk/limits.py` | Graduated drawdown response (reduce/halt/flatten) |

### Multi-Strategy Orchestration

| Path | Purpose |
|---|---|
| `finrobot/strategies/orchestrator.py` | Regime-aware multi-sleeve orchestrator with conflict resolution |
| `finrobot/monitoring/alpha_decay.py` | Rolling Sharpe + CUSUM change-point detection |

### Scripts

| Path | Purpose |
|---|---|
| `scripts/run_quant_pipeline.py` | Full research pipeline: data → features → regime → ML → walk-forward → DSR |
| `scripts/run_walkforward.py` | Walk-forward validation CLI |
| `scripts/run_backtest.py` | Single backtest runner |
| `scripts/autonomous_review_loop.py` | 6-hour strategy review cycle (PM2 managed) |

### Data

| Path | Purpose |
|---|---|
| `data/finrobot.duckdb` | DuckDB warehouse: status, positions, deals, acks, prices tables |
| `data/XAUUSD1.csv` | 100K M1 XAUUSD bars, Jan-May 2026 |

### Legacy/Research (not in live path)

| Path | Purpose |
|---|---|
| `finrobot/indicators.py` | Research indicators (vectorized rsi_divergence) |
| `finrobot/hft.py` | HFT research module |
| `finrobot/strategies/smart_money.py` | Research SMC (diverges from MQL5) |

---

## Constraints

- Demo-only unless owner explicitly says otherwise
- Trade only XAUUSD (BTC retired from active mandate)
- Keep PM2 as the service manager
- Do not commit `.env`, `.runtime/`, `logs/`, or `state/`
- Do not add more symbols before XAUUSD is consistently profitable with statistical significance
- Do not run martingale/grid sizing as live alpha
- Do not let LLM auto-edits touch production without human review
- Do not trust in-sample Sharpe — only walk-forward OOS matters
- Do not deploy strategies without DSR p < 0.05 significance

## Python Dependencies (key packages)

Core: `pandas`, `numpy`, `duckdb`, `pyarrow`, `streamlit`  
ML/Research: `scipy`, `scikit-learn`, `lightgbm`, `hmmlearn`, `optuna`, `joblib`  
All ARM64-native. Python 3.13 in `.venv/`.
