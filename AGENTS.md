# FinRobot Agent Guide

## Operating mandate

FinRobot is now an MT5-first autonomous demo-trading repo. Trade and optimize only:

- `XAUUSD`
- `BTCUSD`

## Source of truth

- Active EA: `broker/mt5/FinRobotBridgeEA.mq5`
- Runtime process list: `ecosystem.config.js`
- MT5 status/report tools: `scripts/mt5_status.py`, `scripts/mt5_trade_report.py`
- 6-hour Opencode loop: `scripts/autonomous_review_loop.py`
- Dashboard: `dashboard/app.py`
- State/logs are runtime artifacts and are intentionally gitignored.

## PM2 processes

Use only these active processes:

```bash
pm2 start ecosystem.config.js
pm2 restart mt5-terminal autonomous-review moonshot-dashboard log-aggregator --update-env
pm2 list
```

## MT5 bridge files

The EA uses MT5 Common Files:

- `finrobot_status.json` for heartbeat/account/symbol status.
- `finrobot_positions.csv` for open managed positions.
- `finrobot_deals.csv` for managed deal history.
- `finrobot_acks.csv` for fills/rejections/auto decisions.
- `finrobot_commands.csv` for optional external commands.

Use `python3 scripts/mt5_trade_report.py` before making strategy changes. It summarizes open MT5 positions and closed managed deal performance.

## Auto-improvement loop

`autonomous-review` runs every 6 hours. It:

1. Reads MT5 trade report and improvement memory.
2. On service restart, waits one full `AUTOREVIEW_INTERVAL_HOURS` period before the first review unless `AUTOREVIEW_RUN_ON_START=true`.
3. Skips if fewer than `AUTOREVIEW_MIN_TRADES` closed deals are available.
4. Calls Opencode with the current mandate.
4. Runs `compileall` and `scripts/mt5_trade_report.py` after successful Opencode changes.
5. Restarts `mt5-terminal` and `moonshot-dashboard` when checks pass.

Default minimum is 12 closed deals and default cadence is every 6 hours. Keep this evidence gate unless the owner asks for more aggressive changes.

## Change rules

- Make direct changes; repo is in git.
- Before editing, inspect `git status --short` and relevant logs/reports.
- After editing, run at least `python3 -m compileall -q moonshot finrobot scripts` and `python3 scripts/mt5_trade_report.py`.
- If EA source changes, copy it to the installed MT5 Experts path and compile with MetaEditor when available.
- Update `README.md` and this file when operating behavior changes.
- Do not print secrets from `.env`.

## Quick health checks

```bash
python3 scripts/mt5_status.py
python3 scripts/mt5_trade_report.py
tail -n 120 logs/autonomous_review.log
pm2 list
```

## Money management guardrails

- `broker/mt5/FinRobotBridgeEA.mq5` must keep daily risk lot sizing enabled unless the owner explicitly disables it.
- New trades are sized from the broker-day equity snapshot, `DailyRiskPerTradePct`, and SL distance; do not revert to fixed `0.01` lots.
- `scripts/mt5_trade_report.py` reports total PnL, daily PnL, strategy expectancy, and the EA `money_management` status block. Check it before strategy edits.
- Current evidence gate: BTC RSI reversion and XAUUSD quick-cross/MACD entries are disabled; do not re-enable without a stronger closed-deal sample.

## Current strategy posture

- Loss diagnosis from the current MT5 sample: BTCUSD is roughly breakeven/slightly positive, while XAUUSD is the dominant drawdown source. Do not re-enable XAUUSD auto trading unless a future review adds a gold-specific edge and verifies it on fresh closed deals.
- `FinRobotBridgeEA.mq5` should keep `EnableSmartMoneyGates=true`, `EnableXauAutoTrading=false`, and `MaxAutoPositionsPerSymbol=1` unless the owner explicitly asks for more aggressive risk.
- Smart-money gate intent: trade BTC momentum only when aligned with a 3+ ICT/SMC confluence score across premium/discount arrays, order blocks, fair-value gaps, liquidity sweeps, and structure shifts. High-confluence score 5+ entries can size up via the risk model while respecting `MaxLotPerTrade` and daily loss controls. `smc_reject score=` means the old momentum signal was intentionally blocked.

## Logging

- `mt5-terminal` runs Wine with `WINEDEBUG=-all` (set in `scripts/start_mt5.sh`) to suppress Wine GUI debug spam (toolbar/datetime/etc.). Real MT5 trade/connection state surfaces in `finrobot_status.json` and MT5's own Experts journal, not Wine stderr.
- `log-aggregator` (PM2) runs `scripts/log_aggregator.sh`, tailing the active runtime logs into a single `logs/combined.log` with `timestamp [source] line` formatting for traceability. It excludes pm2 `*.out` mirrors of app logs to avoid duplication and self-rotates `combined.log` past ~50MB.
- Retired-process and stale install logs were moved under `backups/log_cleanup_*/retired_logs/`. Keep `logs/` limited to the four active processes.

## Strategy & autonomy changes (2026-05-30)

- `MaxSpreadPointsBTCUSD` tightened `250000.0 -> 5000.0` (the old value was an effective no-op; current BTC spread ~1200 pts). XAUUSD cap unchanged (80).
- BTC `MACD_trend` disabled inside the `DisableWeakStrategySignals` BTC block (`macdLong/macdShort=false`) — it had negative expectancy (~-$7.34/trade over 2 deals). `Momentum_trend` and `QuickMomentum` retained.
- After EA source edits: copy `broker/mt5/FinRobotBridgeEA.mq5` to the ACTIVE install `.../ICMarketsSCOfficialMT5/MQL5/Experts/FinRobot/`, compile with `WINEPREFIX=/home/openclaw/.wine-mt5 xvfb-run -a wine MetaEditor64.exe /compile:"MQL5\Experts\FinRobot\FinRobotBridgeEA.mq5" /log:compile.log`, then `pm2 restart mt5-terminal`. EA inputs are NOT pinned in the chart (only file/magic/poll inputs are), so compiled defaults take effect on restart.
- `scripts/autonomous_review_loop.py`: fixed a truncation bug — the trade report was sliced to the last 20000 chars, dropping the `Closed deal summary:` marker (~char 1800), so `closed_deals` always parsed as 0 and the reviewer never ran. Now keeps the head.
- LLM editing is HARD-GATED OFF by default via `AUTOREVIEW_ENABLE_LLM` (unset/false = analysis-only: the loop logs the real closed-deal count and journals analysis but never invokes opencode). Set `AUTOREVIEW_ENABLE_LLM=true` to re-enable autonomous code edits.
