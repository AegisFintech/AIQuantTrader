# FinRobot - Agent Documentation

## Project Overview

**FinRobot** is a self-improving autonomous algorithmic trading system with a closed feedback loop using Opencode. It trades XAUUSD (Gold) and Crypto (BTC, ETH, SOL) using multiple strategies with automatic optimization.

## Project Structure

```
FinRobot/
├── moonshot/                    # Moonshot Crypto Trading System (Daemon 2 - PRIMARY)
│   ├── daemon/                  # 24/7 trading daemon core
│   │   ├── main.py              # Main loop, strategy orchestration, WebSocket
│   │   ├── hyperliquid_ws_client.py  # Real-time price feed from Hyperliquid
│   │   ├── state_manager.py     # Position tracking, trade history, persistence
│   │   └── self_improvement.py  # Strategy performance tracking, optimization, opencode feedback, strategy lab
│   ├── strategies/              # Trading strategies & execution
│   │   ├── strategies.py        # Signal generators (10 strategies - see below)
│   │   └── executor.py          # Paper trading engine
│   ├── trader.py                # Legacy trader (run_moonshot.py demo mode)
│   └── monitor.py               # Live monitoring dashboard
├── finrobot/                    # FinRobot Core Package (Daemon 1 - XAUUSD)
│   ├── strategies/              # XAUUSD strategies (grid, martingale, hft, etc.)
│   ├── execution/               # MT5/cTrader execution adapters
│   ├── optimization/            # Feedback loop, genetic optimizer, opencode integration
│   └── utils/                   # Config, data sources, indicators, logging
├── scripts/                     # All management scripts
│   ├── run_daemon.py            # Moonshot daemon launcher (systemd entry point)
│   ├── moonshot_health_check.py # Watchdog: monitors & restarts daemon
│   ├── run_moonshot.py          # Demo mode launcher
│   ├── daemon_service.py        # XAUUSD daemon (Daemon 1)
│   ├── start_daemon.sh          # XAUUSD daemon startup script
│   └── ...                      # Backtest, health, test scripts
├── logs/                        # ALL log files
│   └── daemon.log               # Main trading log (tail -f this)
├── state/                       # ALL runtime state
│   ├── moonshot/                # Moonshot state (positions, trades, performance)
│   └── daemon1/                 # XAUUSD daemon state
├── data/                        # Market data (CSV, cache)
├── backups/                     # Strategy backups
├── docs/                        # Documentation
└── tests/                       # Test suite
```

## Core Architecture - Moonshot Daemon (Daemon 2)

### How It Works
1. **WebSocket Connection**: Connects to Hyperliquid API for real-time BTC/ETH/SOL prices
2. **Candle Building**: Constructs 60-second OHLCV candles from live tick data
3. **Signal Generation**: 9 strategies evaluate every 15 seconds (best signal per coin selected), filtered by market regime:
   - **QuickMomentum**: EMA 8/21 crosses with RSI filter
   - **RsiDivergence**: RSI overbought/oversold mean reversion
   - **FibonacciRetracement**: Key Fib levels (0.382, 0.5, 0.618, 0.786) as S/R
   - **MACDStrategy**: MACD divergence & crossover with RSI/EMA confirmation
   - **VWAPStrategy**: VWAP as dynamic S/R with deviation bands
   - **RangeScalper**: Bollinger Band + RSI + ADX ranging market scalper
   - **CrossAssetLeadLag**: BTC leads ETH/SOL by 1-3 min, trade the laggard
   - **FundingRateContrarian**: Extreme funding rates predict reversals
   - **VolatilitySqueeze**: BB/KC squeeze breakout with volume confirmation
4. **Regime Detection**: Hurst Exponent + ADX classifies market as ranging/mild_trend/trending, filters strategies accordingly
5. **Multi-Signal Execution**: Opens up to `max_open_positions - current_positions` trades per iteration (1 per coin), max 2 correlated (same-direction) positions
  6. **Position Management**: Regime-adaptive SL/TP (ranging: 0.3%/0.6%, mild_trend: 0.4%/0.9%, trending: 0.5%/1.2%), trailing stop (ranging: 0.2%, mild: 0.3%, trending: 0.4%), breakeven stop at +0.5% (moves SL to entry+fees), TIMEOUT DISABLED (trades exit only via SL/TP/trail), early profit exit DISABLED (let winners run to TP)
7. **Self-Improvement**: Tracks per-strategy performance, adjusts parameters every hour
8. **Strategy Lab**: Auto-disables persistently losing strategies (WR<20%, avg_pnl<-0.5%), re-enables after 2hr cooldown
9. **Opencode Feedback**: Actually invokes opencode via subprocess when return < -1% or WR < 50% or DD > 3%
10. **Risk Management**: Daily loss limit (-2%), correlation check (max 2 same-direction positions), funding rate data from Hyperliquid API

### Key Parameters
- **Initial Balance**: 100 USDT (paper trading)
- **Max Open Positions**: 3
- **Max Leverage**: 5x
- **Risk Per Trade**: 1% of balance
- **Min Confidence**: 0.70 (70%)
- **BTC SL/TP/Trail**: trending 0.5%/1.2%/0.4% | mild_trend 0.4%/0.9%/0.3% | ranging 0.3%/0.6%/0.2%
- **Max Duration**: TIMEOUT DISABLED | **Breakeven**: ENABLED at +0.5% | **Trail Activation**: 0.4%
- **Daily Loss Limit**: -2% | **Max Correlated Positions**: 2 | **Early Profit Exit**: DISABLED
- **Trade Cooldown**: 15s | **Partial TP**: DISABLED | **High Conf Boost**: 1.3x at >=80%
- **Strategy Coin Blacklist**: Fibonacci→SOL, MACD→{SOL,ETH}, VWAP→{BTC,ETH,SOL} (fully disabled), Range_Scalper→{BTC,ETH}, Funding_Contrarian→{SOL}
- **Regime Risk**: ranging 50% | mild_trend 75% | trending 100%

## How to Monitor

```bash
# Watch live moonshot trading
tail -f logs/daemon.log

# Check daemon status
systemctl --user status moonshot-daemon.service

# Run health check
python3 scripts/moonshot_health_check.py

# Watch daemon 1 (XAUUSD)
tail -f logs/trading_daemon.log
```

### Log Format
```
--- Iteration 5 ---
Prices: BTC=$79,607.50 | ETH=$2,279.95 | SOL=$88.27
Balance: 100.00 | Equity: 100.00 | Open: 0/5 | Trades: 0 | Signals: 0
=== SUMMARY | Bal: 100.00 | Return: +0.00% | Trades: 15 | Win: 67% | DD: 0.0% | Positions: 3 ===
  STRATEGIES: Quick_Mo:3t|67wr|+0.080% | SMC_Orde:2t|50wr|+0.120% | Fib_Retr:1t|100wr|+0.250%
```

## Daemon Management

```bash
# Start moonshot daemon (paper trading - default)
systemctl --user start moonshot-daemon.service

# Stop
systemctl --user stop moonshot-daemon.service

# Restart
systemctl --user restart moonshot-daemon.service

# Enable on boot
systemctl --user enable moonshot-daemon.service
```

## Live Trading

The daemon supports live trading on Hyperliquid with real funds. By default it runs in **paper mode**.

### Setup

1. **Copy and edit `.env`**:
   ```bash
   cp .env.sample .env
   nano .env
   ```

2. **Add your wallet credentials** to `.env`:
   ```
   TRADING_MODE=live
   HYPERLIQUID_PRIVATE_KEY=0x_your_private_key
   HYPERLIQUID_WALLET_ADDRESS=0x_your_wallet_address
   HYPERLIQUID_NETWORK=mainnet
   LIVE_MAX_POSITION_USD=50
   ```

3. **Fund your wallet** with USDC on Arbitrum + some ETH for gas

4. **Enable live mode** - either via `.env` (`TRADING_MODE=live`) or CLI flag (`--live`):
   ```bash
   # Via CLI flag (overrides .env)
   python3 scripts/run_daemon.py --interval 15 --balance 100 --live

   # Or update systemd service to add --live flag
   systemctl --user edit moonshot-daemon.service
   # Add: ExecStart=.../run_daemon.py --interval 15 --balance 100 --live
   ```

5. **Restart**:
   ```bash
   systemctl --user restart moonshot-daemon.service
   ```

### Safety

- **Start with paper mode** for at least a few days to verify performance
- **Use a dedicated trading wallet** - never your main wallet
- **Set `LIVE_MAX_POSITION_USD`** to limit exposure per position
- **Daily loss limit** (-2%) still applies in live mode
- The live executor uses the Hyperliquid Python SDK for on-chain order placement
- If `.env` is missing `HYPERLIQUID_PRIVATE_KEY` or `HYPERLIQUID_WALLET_ADDRESS`, it falls back to paper mode automatically

## File Modification Guidelines

When modifying code:
1. **Backup First**: Copy originals to `backups/`
2. **Test Immediately**: Run daemon and check logs
3. **Update AGENTS.md**: Document what changed and why
4. **Hot Reload**: Daemon picks up changes on restart (systemctl --user restart)
5. **Validate**: Check `tail -f logs/daemon.log` for errors

## Critical Lessons Learned

### 1. Warmup Period Required
The daemon needs 120 seconds to build enough candles before trading starts. Don't panic if you see "No high-confidence signals found" in the first few minutes.

### 2. Stale State Causes Crashes
If `state/moonshot/positions.json` has very old positions (>1hr), the daemon may crash on startup. Reset with: `echo '{}' > state/moonshot/positions.json`

### 3. systemd Service Configuration
`StartLimitIntervalSec` must be in `[Unit]` section, not `[Service]`. Wrong placement causes warnings and restart failures.

### 4. Duplicate Logging
Multiple modules configuring `logging.basicConfig()` causes duplicate log lines. The daemon's `main.py` now handles all logging configuration.

### 5. SL/TP Must Match Timeframe
Wide SL/TP (2%/4%) on 60-second candles causes 97% of trades to exit via STALE at random PnL. Tightened to 0.5%/1% for scalping. Later V4 update: widened to 0.6%/1.2% with breakeven stops to balance hit rate vs. reward.

### 6. Only One Signal Per Cycle Starves The System
Picking only the best signal across all coins/strategies means 1 trade per minute max. Changed to open 1 signal per coin per cycle, filling all available position slots.

### 7. Opencode Feedback Must Actually Invoke Opencode
The old `OpencodeFeedback` only wrote to a JSONL file. Now it invokes opencode via subprocess with a structured performance report prompt.

### 8. DRIFT/TIMEOUT Exits Dominate When Params Are Too Tight
V3 had 88.6% of 247 trades exiting via DRIFT (44.5%) or TIMEOUT (44.1%) with only 1.3% hitting TP. Root cause: too-tight SL/TP floors (0.4%/0.7%), short max duration (1800s), and aggressive DRIFT exit (age>900s, pnl<0.15%). V4 fixes: SL=0.6%, TP=1.2%, Trail=0.5%, max_duration=3600s, DRIFT age>2700s/pnl<0.05%, breakeven stop at 0.3% favorable move, trail activation at 0.5%. V5: disabled ATR-based SL/TP (was computing too-tight levels from 1m ATR), disabled DRIFT exit entirely, raised min_confidence 0.45→0.55.

### 9. Blacklist Losing Coin/Strategy Combos
SMC+ETH had 37.5% WR (-0.177 USDT), MACD+SOL had 50% WR (-0.096 USDT), VWAP+BTC had 0% WR (-0.188 USDT). Added to strategy_coin_blacklist to prevent these combos from trading.

### 10. ATR SL/TP Can Be Counterproductive on 1m Candles
With `use_atr_sl_tp=True`, the system computed SL/TP from 1m candle ATR and then clipped to floor minimums. Since ATR on 1m candles is tiny, the ATR-based levels were often tighter than the floors, defeating the purpose of widened fixed floors. V5 disables ATR SL/TP entirely and uses fixed 0.6%/1.2%/0.5%.

### 11. Strategy-Provided SL/TP Can Be Tighter Than Floor Minimums
When `use_atr_sl_tp=False`, the code used `signal.stop_loss` and `signal.take_profit` from strategies, which were much tighter than the floor minimums (e.g. 0.3% SL / 0.9% TP from VWAP vs. 0.6%/1.2% floors). The floor enforcement only ran in the ATR branch. V6 moves floor enforcement (min and max) to run after both the ATR and signal branches, ensuring SL is always 0.6%-1.5% and TP is always 1.2%-2.5% regardless of source.

### 12. Trailing Stops for Shorts Were Broken (CRITICAL BUG)
`position_highest` always tracked the max price using `max()`. For short positions, the trailing stop should track the LOWEST price (most favorable). The old code: `trailing_stop = highest + pos_trail` for shorts, with activation condition `highest < entry * (1 - trail_activation_pct)` which can NEVER be true since highest >= entry for profitable shorts. V7 adds `position_lowest` tracking and uses it for short trailing stops: `trailing_stop = lowest + pos_trail` with activation `lowest < entry * (1 - trail_activation_pct)`.

### 13. SMC_OrderFlow Was the Biggest Loser
Over 571 V6 trades, SMC_OrderFlow generated 229 trades (40% of all volume) with 42.8% WR and -3.66 USDT loss (49% of all losses). SMC+ETH alone had 35% WR. V7 removes SMC_OrderFlow entirely and replaces with CrossAssetLeadLag, FundingRateContrarian, and VolatilitySqueeze.

### 14. TP Almost Never Hit (1% rate)
With SL=0.6% and TP=1.2% (R:R 1:2), only 6 of 571 trades (1%) hit TP while 89 (15.6%) hit SL. The TP target was too far for 1m candle moves. V7 reduces TP to 0.9% (R:R 1:1.5) for BTC/ETH and uses 1.2% for SOL which has wider 1m candles.

### 15. No Regime Detection Caused Wrong Strategies in Wrong Markets
Mean reversion strategies traded in trending markets (losing), momentum strategies traded in ranging markets (losing). V7 adds Hurst Exponent + ADX regime detection with strategy filtering per regime.

### 16. No Cross-Asset or Alternative Data Signals
BTC leads ETH/SOL by 1-3 minutes on 1m charts. The system treated all coins independently, missing the strongest available alpha. V7 adds CrossAssetLeadLag strategy and FundingRateContrarian (using Hyperliquid API funding rate data).

### 17. No Daily Loss Limit or Correlation Check
One bad hour could wipe out a week of gains, and all 3 positions could be long simultaneously (3x long crypto). V7 adds -2% daily loss limit and max 2 same-direction position correlation check.

### 18. Wide SL/TP and Long Hold Times Cause TIMEOUT Dominated Exits (CRITICAL)
V10 had regime SL/TP of ranging 0.4%/0.6%, mild 0.6%/1.1%, trending 0.7%/1.4% with max durations of 20-40min. This caused most trades to exit via TIMEOUT at random PnL instead of hitting TP. The TP was too close relative to normal 1m candle noise but the SL was too wide, creating asymmetric losses. V11 tightens SL (ranging 0.3%, mild 0.4%, trending 0.5%) to cut losses faster, widens TP (ranging 0.9%, mild 1.4%, trending 1.8%) to let winners run, halves max hold times (10-20min), enables breakeven stops at +0.5%, and raises min_confidence to 0.65 to reduce low-quality trades.

### 19. TIMEOUT and Early Profit Exit Are the #1 and #2 EV Killers (CRITICAL)
V12 data: 173 trades, 105 (61%) exited via TIMEOUT with avg PnL -0.028. Early profit exit captured 57 wins but avg only +0.051. The fundamental problem: TIMEOUT exits at random PnL destroy expected value because they're noise, not alpha. Early profit exit at +0.15% cuts winners before they can reach TP at +0.6-1.2%. The win/loss ratio was 0.72 (avg win +0.034 vs avg loss -0.048). Quant principle: you need either WR>60% with R:R>0.8, or R:R>1.5 with WR>40%. V13 disables both TIMEOUT and early profit exit. Trades now exit only via SL (cut losers), TP (let winners run), or trailing stop (lock in profits). This eliminates two sources of negative EV.

---

**Last Updated**: 2026-05-19
**Major Changes**: V13 - Disabled TIMEOUT exit entirely (was causing 61% of trades to exit at random PnL, destroying EV), disabled early profit exit (was cutting winners at +0.15% before TP could be hit), raised min_confidence 0.65→0.70, raised breakeven activation +0.3%→+0.5%, raised trail activation 0.3%→0.4%, disabled partial TP, reduced max positions 5→3, increased trade cooldown 5s→15s, reduced high-conf boost 1.5x→1.3x at >=80%. Core quant principle: let winners run to TP, cut losers at SL, eliminate noise exits.
**Daemon 2 Status**: Running via nohup (V13)
**Daemon 1 Status**: Preserved but not actively used (all strategies unprofitable)
