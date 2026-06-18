# FinRobot — Quant Analysis & Development Guide

## System Overview

MT5-first autonomous demo trading system for XAUUSD and BTCUSD. MetaTrader 5 runs under Wine/Xvfb, the FinRobot EA trades inside MT5, PM2 keeps MT5 + autonomous review loop + dashboard alive. Python layer mirrors the MQL5 signal logic for backtesting and research.

**Active EA:** `broker/mt5/FinRobotBridgeEA.mq5` v1.31  
**Symbols:** XAUUSD, BTCUSD only  
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

### Known Bugs

#### Bug 1 — SL/TP exit check misses gap-opens (`engine.py:170-175`)
**File:** `finrobot/backtest/engine.py`, `_exit_for_bar` method  
**Current (wrong):**
```python
sl_hit = position.sl > 0 and low <= position.sl <= high
tp_hit = position.tp > 0 and low <= position.tp <= high
```
**Problem:** If a bar gaps fully past the stop (e.g., XAUUSD opens below SL on news), `sl > high` is true and the stop never fires — position bleeds through.  
**Correct logic:**

| Side | SL trigger | TP trigger |
|---|---|---|
| BUY | `low <= sl` | `high >= tp` |
| SELL | `high >= sl` | `low <= tp` |

**Priority: Fix this before trusting any walk-forward results.**

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

### Priority Action List

1. **Fix backtest exit bug** in `finrobot/backtest/engine.py:170-175` — corrupts all walk-forward results
2. **Run walk-forward on live EA parameters** using existing framework + 100K bar dataset
3. **Add ADX regime gate** to `FinRobotBridgeEA.mq5` and `xau_gates.py`
4. **Tighten FVG threshold** `FvgMinAtrMultiplier` 0.15 → 0.30; **sweep** 0.10 → 0.30
5. **Raise TP multiplier** `TakeProfitAtrMultiplier` 1.8 → 2.4 (R:R 2:1)
6. **Add Sortino + Calmar** to `finrobot/backtest/metrics.py`
7. **Vectorize research code** in `indicators.py` and `smart_money.py`
8. **Fix deprecated `.fillna(method=)`** in `strategies/grid.py`

---

### Quick Win Estimates

| Item | Effort | Impact |
|---|---|---|
| Fix SL/TP exit bug | 30 min | Fixes backtest correctness |
| Add Sortino/Calmar metrics | 30 min | Improves reporting immediately |
| Fix pandas deprecation in grid.py | 5 min | Eliminates FutureWarning |
| Run walk-forward on live params | 1 hour | First honest edge validation |
| ADX gate in EA + backtest | 1 day | Reduces false entries in ranging markets |
| Raise FVG/sweep thresholds | 30 min | Reduces noise in signal generation |
| Raise TP to 2.4x ATR | 10 min | Improves R:R to 2:1 |

---

## Key File Map

| Path | Purpose |
|---|---|
| `broker/mt5/FinRobotBridgeEA.mq5` | Live EA — all trading logic, risk controls, SMC gates |
| `broker/mt5/SmartMoney.mqh` | MQL5 SMC: FVG, OB, sweep, structure, PDA scoring |
| `broker/mt5/RiskManagement.mqh` | MQL5 daily PnL, session gate, break-even |
| `finrobot/backtest/engine.py` | Deterministic bar-by-bar backtest engine |
| `finrobot/backtest/fills.py` | Fill simulation: spread, slippage, commission, swap |
| `finrobot/backtest/position.py` | Position state + `DailyRiskSizer` (mirrors EA sizing) |
| `finrobot/backtest/metrics.py` | Sharpe, profit factor, expectancy, drawdown |
| `finrobot/backtest/walkforward.py` | Purged walk-forward with embargo windows |
| `finrobot/backtest/strategies/xau_gates.py` | Python port of MQL5 SMC/PDA gate logic |
| `finrobot/backtest/strategies/xau_gated.py` | XAU gate wrapper strategy |
| `finrobot/backtest/strategies/btc_gates.py` | BTC gate helpers |
| `finrobot/backtest/strategies/btc_gated.py` | BTC gate wrapper strategy |
| `finrobot/indicators.py` | Research indicators (not in live EA path) |
| `finrobot/hft.py` | HFT research module (not in live EA path) |
| `finrobot/strategies/smart_money.py` | Research SMC (diverges from MQL5 — do not treat as authoritative) |
| `data/finrobot.duckdb` | DuckDB warehouse: status, positions, deals, acks, prices tables |
| `data/XAUUSD1.csv` | 100K M1 XAUUSD bars, Jan-May 2026 |

---

## Constraints

- Demo-only unless owner explicitly says otherwise
- Trade only XAUUSD and BTCUSD
- Keep PM2 as the service manager
- Do not commit `.env`, `.runtime/`, `logs/`, or `state/`
- Do not add more symbols before data quality, portfolio risk, and execution reconciliation are solved
- Do not run martingale/grid sizing as live alpha
- Do not let LLM auto-edits touch production without human review
