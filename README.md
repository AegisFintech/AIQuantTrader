# FinRobot - Autonomous Algorithmic Trading System

**Version**: 10.0 | **Status**: Active | **Last Updated**: 2026-05-17

## Overview

FinRobot is a self-improving autonomous algorithmic trading system with a closed feedback loop using Opencode. The primary trading system (Moonshot Daemon) trades BTC, ETH, and SOL perpetual futures on Hyperliquid via real-time WebSocket, using regime-adaptive strategies with automatic optimization.

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
systemctl --user start moonshot-daemon.service

# Or run directly
python3 scripts/run_daemon.py --interval 15 --balance 100
```

### Monitor

```bash
# Watch live trading
tail -f logs/daemon.log

# Check daemon status
systemctl --user status moonshot-daemon.service

# Run health check
python3 scripts/moonshot_health_check.py
```

### Manage

```bash
# Restart
systemctl --user restart moonshot-daemon.service

# Stop
systemctl --user stop moonshot-daemon.service

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
6. Restart daemon: `systemctl --user restart moonshot-daemon.service`

### Version Control

Backups are stored in `backups/` with version labels (e.g., `main_v9_*.py`). Always backup before changes.

## License

Proprietary and confidential.