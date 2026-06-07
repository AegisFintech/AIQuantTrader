# FinRobot

FinRobot is an autonomous MT5 demo-trading system for exactly two instruments:

- `XAUUSD`
- `BTCUSD`

## Current runtime

| PM2 process | Purpose |
|---|---|
| `mt5-terminal` | Headless Wine/Xvfb MetaTrader 5 terminal logged into ICMarketsSC-Demo. |
| `autonomous-review` | Every 6 hours, reviews MT5 XAUUSD/BTCUSD results and asks Opencode to patch the repo when enough evidence exists. |

## Trading path

```text
FinRobotBridgeEA.mq5 inside MT5
→ broker demo fills/spread/commission/slippage
→ Common Files heartbeat, positions, deals, and acks
→ Python reports/autonomous-review
→ Opencode improvements when enough closed trades exist
```

Important files:

- `broker/mt5/FinRobotBridgeEA.mq5` — active EA source for XAUUSD/BTCUSD (v1.27).
- `broker/mt5/RiskManagement.mqh`, `SmartMoney.mqh`, `BridgeIO.mqh` — modular EA components.
- `scripts/start_mt5.sh` — starts the MT5 terminal under Wine.
- `scripts/mt5_status.py` — heartbeat/status check.
- `scripts/mt5_trade_report.py` — open positions and closed-deal performance report.
- `scripts/autonomous_review_loop.py` — 6-hour Opencode review loop.
- `ecosystem.config.js` — canonical PM2 process list.
- `AGENTS.md` — operating notes for future agents.

## Management commands

```bash
cd /home/openclaw/FinRobot
pm2 list
pm2 restart mt5-terminal autonomous-review --update-env
python3 scripts/mt5_status.py
python3 scripts/mt5_trade_report.py
tail -f logs/combined.log
```

## MT5 common files

The EA writes these files under the MT5 Common Files directory:

- `finrobot_status.json` — heartbeat, account, symbol prices, and last signal per symbol.
- `finrobot_positions.csv` — open managed positions for XAUUSD/BTCUSD.
- `finrobot_deals.csv` — managed deal history exported from MT5 history.
- `finrobot_acks.csv` — command acknowledgements and auto-trade decisions.
- `finrobot_commands.csv` — optional command input consumed by the EA.

## Improvement policy

The autonomous review loop runs every 6 hours. On restart it waits one full interval before the first review unless `AUTOREVIEW_RUN_ON_START=true`. It checks closed MT5 deals first, appends decisions to `state/mt5/improver_journal.jsonl`, updates `state/mt5/improver_memory.json`, shows recent memory to Opencode, and lets Opencode patch code/docs directly only when enough trade evidence exists. The default minimum is 12 closed deals.

## Guardrails

- Demo-only unless the owner explicitly says otherwise.
- Trade only `XAUUSD` and `BTCUSD`.
- Keep PM2 simple; do not add systemd services.
- Never print or commit `.env` secrets.

## Money management

The MT5 EA uses daily risk-based lot sizing. At the start of each broker day it records an equity snapshot, sizes new trades from `DailyRiskPerTradePct` and the actual SL distance, halves risk after the day's closed PnL turns negative, caps lots with `MaxLotPerTrade`, and stops opening new trades if `DailyLossLimitPct` is hit. The status JSON includes a `money_management` block so `scripts/mt5_trade_report.py` can verify the current daily snapshot, risk setting, and closed PnL.

The May 2026 audit disabled weak live signals after poor closed-trade expectancy. A later maximum-frequency BTC experiment overtraded RSI reversion and low-confluence MACD signals, producing large BTC drawdown. The EA is now in recovery posture: daily risk lot sizing remains enabled, BTC RSI reversion/ATR impulse are disabled, positions are capped at two per symbol, risk is reduced, the daily loss gate is tighter, and new BTC entries require higher-timeframe trend alignment plus directional PDA/SMC confirmation. The EA also makes one throttled close attempt per broker day for old managed-symbol probe positions that have the FinRobot magic number but no SL/TP, so failures cannot spam acknowledgements.

## Current loss diagnosis

The live MT5 report shows XAUUSD remains historically negative, while the 2026-06-01 maximum-frequency profile made BTCUSD the immediate drawdown source. `BTCUSD_RSI_reversion` had very poor expectancy and is disabled. The current posture prioritizes preserving the demo account and collecting a cleaner closed-trade sample before increasing frequency again.

## Smart-money filters

`FinRobotBridgeEA.mq5` now defaults to `EnableSmartMoneyGates=true`, `EnableXauAutoTrading=true`, `MaxAutoPositionsPerSymbol=2`, and symbol-specific cooldowns. Entries must pass recovery gates before order placement:

- BTC longs require H1 uptrend alignment, acceptable discount/PDA, and sufficient bullish SMC confluence; BTC shorts require H1 downtrend alignment, acceptable premium/PDA, and sufficient bearish SMC confluence.
- BTC RSI reversion, BTC ATR impulse, and weak BTC momentum trend entries are disabled by default after the live drawdown sample.
- XAU entries remain enabled but require stricter premium/discount alignment and higher SMC confluence because historical XAU expectancy is negative.
- High-confluence entries (score 5+) can size up through the risk model via `HighConfluenceLotMultiplier`, still capped by `MaxLotPerTrade` and daily loss controls.
- Status messages expose `pda=`, `smc=`, and `smc_reject score=` so the MT5 reports and performance logs show why trades were accepted or rejected.
- **Session Gating (v1.27)**: New entries are restricted to London and New York volatility windows to avoid low-volume Asian session "chop."
- **Dynamic Break-Even (v1.27)**: Stops are automatically moved to break-even plus a small buffer once a 1:1 Risk/Reward ratio is achieved, protecting profits on volatile reversals.

Runtime logs are consolidated into one operator-facing file: `logs/combined.log`. PM2 stdout/stderr for the active services writes there directly; obsolete sidecar process logs are not part of normal operation.
