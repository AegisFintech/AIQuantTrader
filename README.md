# FinRobot - Autonomous Algorithmic Trading System

**Version**: 10.0 | **Status**: Active | **Last Updated**: 2026-05-17

## Overview

FinRobot is a self-improving autonomous algorithmic trading system with a closed feedback loop using Opencode. The primary trading system (Moonshot Daemon) trades BTC, ETH, and SOL perpetual futures on Hyperliquid via real-time WebSocket, using regime-adaptive strategies with automatic optimization.


## Current Operating Mode - 2026-05-22

FinRobot is now set up as an aggressive-growth paper-trading system with a simple PM2 process layout:

| Process | Purpose |
|---|---|
| `moonshot-daemon` | Main Python trading brain for crypto paper trading, currently reset to 500,000 USDT paper balance. |
| `moonshot-improver` | GPT-5.5 parameter improver. Runs every 6 hours and skips cycles with fewer than 20 closed trades. |
| `autonomous-review` | Opencode/GPT-5.5 coding review loop. Every 6 hours it reviews performance + memory, can patch local code/docs, then runs checks. It also skips if fewer than 20 closed trades. |
| `moonshot-dashboard` | Streamlit dashboard at `https://aiagent01.aegiswallet.app/dashboard/`. |
| `mt5-terminal` | Headless Wine + Xvfb MetaTrader 5 terminal logged into ICMarketsSC-Demo. |

### Strategy memory policy

Every review cycle checks which strategies, coins, and regimes are working. Good strategies continue or receive higher risk scale. Bad strategies are stopped via `disabled_strategies`, blocked for specific coins via `strategy_coin_blacklist`, or reduced through `risk_scale_by_strategy`. The prompt and memory history explicitly tell GPT-5.5 not to repeat rejected ideas.

### MT5 / broker execution direction

The preferred architecture is **Python brain + MT5 execution bridge**:

```text
FinRobot Python strategy brain
→ strategy memory / GPT-5.5 review / Opencode patches
→ MT5 bridge files
→ FinRobotBridgeEA.mq5 inside MT5
→ IC Markets MT5 demo execution with broker spread, commission, latency, and slippage
```

MT5 is installed under `/home/openclaw/mt5` using Wine prefix `/home/openclaw/.wine-mt5`. The bridge EA source lives at `broker/mt5/FinRobotBridgeEA.mq5`. MT5 login is stored in `.env`; do not commit or print secrets.

Important: MT5 is currently connected as a demo terminal, but the EA still needs to be attached/enabled on a chart before Python can route orders through it. Until that is validated, live order routing remains disabled and Hyperliquid paper trading remains the active trading engine.

### Management commands

```bash
pm2 list
pm2 restart moonshot-daemon moonshot-improver autonomous-review moonshot-dashboard mt5-terminal
python3 scripts/moonshot_health_check.py
python3 scripts/mt5_status.py
tail -f logs/daemon.log logs/improver.log logs/autonomous_review.log logs/mt5_terminal.log
```

## Architecture

```
FinRobot/
├── moonshot/                    # Moonshot Crypto Trading (PRIMARY)
│   ├── daemon/
│   │   ├── main.py              # Main loop, strategy orchestration, regime detection
│   │   ├── hyperliquid_ws_client.py  # Real-time price feed
│   │   ├── state_manager.py     # Position tracking, trade persistence
│   │   └── self_improvement.py  # Performance tracking, opencode feedback
│   ├── strategies/
│   │   ├── strategies.py        # 12 signal generators
│   │   └── executor.py          # Paper trading engine
│   ├── trader.py                # Legacy demo trader
│   └── monitor.py               # Live dashboard
├── finrobot/                    # XAUUSD strategies (preserved, not active)
├── scripts/                     # Daemon launcher, health check, backtests
├── state/moonshot/              # Live positions, trades, performance data
├── logs/                        # daemon.log (tail -f this)
├── backups/                     # Versioned strategy backups
├── data/                        # Market data cache
├── docs/                        # Deployment & daemon guides
├── AGENTS.md                    # Full agent documentation
└── tests/                       # Test suite
```

## Moonshot Daemon (V10)

### How It Works

1. **WebSocket**: Connects to Hyperliquid for real-time BTC/ETH/SOL prices
2. **Candle Building**: Constructs 60-second OHLCV candles from live ticks
3. **Regime Detection**: Hurst Exponent + ADX classifies market as ranging / mild_trend / trending
4. **Signal Generation**: 12 strategies evaluate every 15 seconds, filtered by regime
5. **Position Management**: Regime-adaptive SL/TP, trailing stops, early profit exit
6. **Self-Improvement**: Per-strategy performance tracking, auto-disable losing strategies, opencode feedback

### Active Strategies (12)

| Strategy | Regime | Type | Description |
|----------|--------|------|-------------|
| Quick_Momentum | All | Momentum | EMA 8/21 crosses with RSI filter |
| RSI_Reversion | All | Mean Reversion | RSI overbought/oversold reversal (65/35) |
| MACD_Divergence | Mild/Trending | Momentum | MACD divergence with RSI/EMA confirmation |
| Cross_Lead_Lag | All | Cross-Asset | BTC leads ETH/SOL, trade the laggard |
| Funding_Contrarian | Trending | Sentiment | Z-score of funding rates for reversal signals |
| Vol_Squeeze | Ranging/Mild | Breakout | BB/KC squeeze breakout detection |
| Fibonacci_Retracement | Mild | S/R | Key Fib levels as support/resistance |
| Range_Scalper | Ranging | Mean Reversion | BB + RSI + ADX ranging scalper |
| EMA_Ribbon | Mild/Trending | Pullback | EMA 8/13/21 stacked, trade pullback to EMA21 |
| VWAP_Revert | Ranging | Mean Reversion | Trade back to VWAP on deviations |
| Mom_Exhaust | Ranging/Trending | Reversal | RSI extended + shrinking candle ranges |
| VWAP_Mean | - | *Blacklisted* | Disabled (0% WR across all coins) |

### Regime-Adaptive Parameters

| Parameter | Ranging | Mild Trend | Trending |
|-----------|---------|------------|----------|
| SL | 0.4% | 0.6% | 0.7% |
| TP | 0.6% | 1.1% | 1.4% |
| Trail | 0.3% | 0.4% | 0.5% |
| Timeout | 20 min | 30 min | 40 min |
| Risk | 1% | 1.5% | 2% |

### Key Risk Controls

- **Daily Loss Limit**: -2% (pauses new trades for the day)
- **Max Open Positions**: 5
- **Max Correlated Positions**: 2 same-direction
- **Early Profit Exit**: Close if >0.1% profitable after 15 min
- **High Confidence Boost**: 1.5x position size when confidence >= 70%
- **Min Confidence**: 58% to enter a trade

## Quick Start

### Start the Daemon

```bash
# Start via systemd (recommended for 24/7)
pm2 start ecosystem.config.js

# Or run directly
python3 scripts/run_daemon.py --interval 15 --balance 100
```

### Monitor

```bash
# Watch live trading
tail -f logs/daemon.log

# Check daemon status
pm2 list

# Run health check
python3 scripts/moonshot_health_check.py
```

### Manage

```bash
# Restart
pm2 restart moonshot-daemon

# Stop
pm2 stop moonshot-daemon

# Enable on boot
systemctl --user enable moonshot-daemon.service
```

## Performance History

| Version | Key Change | Result | Lesson |
|---------|-----------|--------|--------|
| V1-V6 | Fixed SL/TP, various strategies | Unprofitable | Martingale/HFT/SMC don't work on 1m |
| V7 | Regime detection, 3 new strategies | -0.37%/9hr | TP too far, 1.3% hit rate |
| V8 | Wider SL/TP, disabled breakeven | -0.87%/11hr | 100% TIMEOUT exits - SL/TP unreachable |
| V9 | Regime-adaptive SL/TP, early profit exit | -0.44%/7hr | Better WR (64%), but fees eat profits |
| V10 | 3 new strategies, fix dormant ones, high-conf boost | Running | TBD |

## Development

### Adding a Strategy

1. Create strategy class in `moonshot/strategies/strategies.py` with `generate_signal()` method
2. Import and instantiate in `moonshot/daemon/main.py` strategy list
3. Add to `regime_strategy_filter` for appropriate regimes
4. Add any coin blacklists to `strategy_coin_blacklist`
5. Set `self.name` attribute (used for tracking)
6. Restart daemon: `pm2 restart moonshot-daemon`

### Version Control

Backups are stored in `backups/` with version labels (e.g., `main_v9_*.py`). Always backup before changes.

## License

Proprietary and confidential.