# FinRobot Agent Guide

## Operating mandate

FinRobot is now an MT5-first autonomous demo-trading repo. Trade and optimize only:

- `XAUUSD`
- `BTCUSD`

## Source of truth

- Active EA: `broker/mt5/FinRobotBridgeEA.mq5` (v1.29)
- EA Modules: `broker/mt5/RiskManagement.mqh`, `SmartMoney.mqh`, `BridgeIO.mqh`
- Runtime process list: `ecosystem.config.js`
- Installer: `install.sh`
- MT5 status/report tools: `scripts/mt5_status.py`, `scripts/mt5_trade_report.py`
- Dashboard: `dashboard/app.py` served by `finrobot-dashboard` on `127.0.0.1:8501`
- MT5 startup/profile helper: `scripts/mt5_configure_profile.py`
- 6-hour Opencode loop: `scripts/autonomous_review_loop.py`
- Indicators: `finrobot/indicators.py` (consolidated)
- HFT Logic: `finrobot/hft.py` (consolidated)
- Runtime MT5/Wine files live under `.runtime/` and are intentionally gitignored.
- State/logs are runtime artifacts and are intentionally gitignored.
- On arm64, MT5 is experimental and must run through x86_64 emulation. Prefer Hangover Wine when installed; otherwise use `scripts/wine_box64.sh` with Box64 and a repo-local new-WoW64 x86_64 Wine build under `.runtime/wine-x86_64/wine-11.10-amd64-wow64`.
- MT5 startup config should keep `[StartUp] Expert=FinRobot\FinRobotBridgeEA` and `[Experts] Enabled=1`. The Default profile should keep a single `BTCUSD` chart by default, so the PM2-managed terminal loads the bridge EA headlessly without opening duplicate startup charts.
- If `MT5_LOGIN`, `MT5_PASSWORD`, or `MT5_SERVER` are empty, MT5 can start and load the EA file but will show the account wizard and the bridge will not produce Common Files until credentials are configured.
- `scripts/start_mt5.sh` rewrites `Config\finrobot-login.ini` from `.env` before every MT5 launch; do not print the generated file because it contains the MT5 password.
- On a clean generic MT5 install, seed the IC Markets server database once through MT5's account wizard by searching `Raw Trading Ltd` and selecting `ICMarketsSC-Demo`; the resulting `.runtime` server data survives normal PM2 restarts.

## PM2 processes

Use only these active processes:

```bash
./install.sh
pm2 start ecosystem.config.js
pm2 restart mt5-terminal autonomous-review finrobot-dashboard --update-env
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
5. Restarts `mt5-terminal` and `autonomous-review` when checks pass.

Default minimum is 12 closed deals and default cadence is every 6 hours. Keep this evidence gate unless the owner asks for more aggressive changes.

## Change rules

- Make direct changes; repo is in git.
- Before editing, inspect `git status --short` and relevant logs/reports.
- After editing, run at least `python3 -m compileall -q finrobot scripts` and `python3 scripts/mt5_trade_report.py`.
- If EA source changes, run `scripts/sync_mt5_ea.sh` and compile with MetaEditor when available.
- Update `README.md` and this file when operating behavior changes.
- Do not print secrets from `.env`.

## Quick health checks

```bash
python3 scripts/mt5_status.py
python3 scripts/mt5_trade_report.py
tail -n 120 logs/combined.log
pm2 list
```

## Money management guardrails

- `broker/mt5/FinRobotBridgeEA.mq5` must keep daily risk lot sizing enabled unless the owner explicitly disables it.
- New trades are sized from the broker-day equity snapshot, `DailyRiskPerTradeFraction`, and SL distance; do not revert to fixed `0.01` lots.
- `scripts/mt5_trade_report.py` reports total PnL, daily PnL, strategy expectancy, and the EA `money_management` status block. Check it before strategy edits.
- Current owner posture is recovery trading after the maximum-frequency BTC experiment failed. Keep daily risk lot sizing enabled, reduced risk, stricter SMC gates, two managed positions per symbol, and weak BTC reversion/impulse signals disabled.

## Current strategy posture

- Loss diagnosis from the current MT5 sample: the 2026-06-01 maximum-frequency profile overtraded BTCUSD. `BTCUSD_RSI_reversion` was the dominant loss source and must stay disabled; XAUUSD remains historically negative and must keep stricter gates.
- `FinRobotBridgeEA.mq5` should keep `EnableSmartMoneyGates=true`, `EnableXauAutoTrading=true`, `UseDailyRiskLotSizing=true`, `DisableWeakStrategySignals=true`, `EnableBtcRsiReversion=false`, `EnableBtcAtrImpulse=false`, `EnableBtcContinuousTrading=true`, `EnableXauWeekdayMarketHours=true`, `EnableBtcCostFilters=true`, and `MaxAutoPositionsPerSymbol=2` unless the owner changes risk again.
- Smart-money gate intent: trade BTC only with higher-timeframe trend alignment plus directional PDA/SMC confirmation, and trade XAU only from stricter premium/discount SMC setups. High-confluence score 5+ entries can size up via the risk model while respecting symbol lot caps and daily loss controls. `smc_reject`, `btc_direction_reject`, and `xau_pda_reject` mean the signal was intentionally blocked.
- Session intent: BTC may scan continuously outside London/NY, but only with fixed spread plus ATR/target-distance cost filters. XAU may scan Monday-Friday whenever the broker symbol is inside its configured trade session; it should not be limited to London/NY windows.
- `finrobot_status.json` includes per-symbol `session_gated`, `weekday_market_hours`, `session_open`, and daily `signal_telemetry` counters. Use these counters to distinguish no-signal periods from intentional market-closed, spread/cost, SMC, direction, PDA, cooldown, or session rejections before changing strategy.

## Logging

- `mt5-terminal` runs Wine with `WINEDEBUG=-all` (set in `scripts/start_mt5.sh`) to suppress Wine GUI debug spam (toolbar/datetime/etc.). Real MT5 trade/connection state surfaces in `finrobot_status.json` and MT5's own Experts journal, not Wine stderr.
- On arm64, set `FINROBOT_ALLOW_EMULATED_MT5=true`; if `FINROBOT_WINE_CMD` is unset, install/start/sync scripts use Hangover's `wine` when present, otherwise `scripts/wine_box64.sh`.
- Active PM2 services write stdout/stderr directly to the single operator log: `logs/combined.log`. Do not add new sidecar app logs for active services unless the owner asks.
- Retired-process and stale install logs should stay out of `logs/`. Keep `logs/` focused on `combined.log` and active trading diagnostics only.

## Strategy & autonomy changes (2026-05-30)

- `MaxSpreadPointsBTCUSD` tightened `250000.0 -> 5000.0` (the old value was an effective no-op; current BTC spread ~1200 pts). XAUUSD cap unchanged (80).
- BTC `MACD_trend` disabled inside the `DisableWeakStrategySignals` BTC block (`macdLong/macdShort=false`) — it had negative expectancy (~-$7.34/trade over 2 deals). `Momentum_trend` and `QuickMomentum` retained.
- Owner-requested maximum-frequency demo defaults (2026-06-01) failed in live BTC trading and are retired: do not restore `DisableWeakStrategySignals=false`, `MaxAutoPositionsPerSymbol=5`, `MinSecondsBetweenTrades=60`, `MaxLotPerTrade=1.00`, `DailyRiskPerTradeFraction=0.0050`, or `MinSmcConfluenceScore=1` without fresh evidence.
- Recovery defaults (2026-06-02, risk semantics clarified in v1.30): `DisableWeakStrategySignals=true`, `EnableBtcRsiReversion=false`, `EnableBtcAtrImpulse=false`, `EnableBtcMomentumTrend=false`, `MaxAutoPositionsPerSymbol=2`, `MaxLotPerTrade=0.25`, `DailyRiskPerTradeFraction=0.0010` (0.10% of equity), `DailyLossLimitFraction=0.01` (1.00% of equity), BTC requires H1 trend alignment and directional PDA confirmation.
- BTC continuous-scanning update (2026-06-11): `EnableBtcContinuousTrading=true` bypasses the London/NY session gate for BTC only; `EnableBtcCostFilters=true` rejects BTC entries when spread exceeds `MaxBtcSpreadAtrRatio=0.15` of ATR or `MaxBtcSpreadTakeProfitRatio=0.08` of target distance. Do not confuse this with the retired maximum-frequency profile; weak BTC signals and aggressive sizing remain disabled.
- XAU weekday-market update (2026-06-11): `EnableXauWeekdayMarketHours=true` bypasses the old London/NY-only gate for XAU and instead checks Monday-Friday plus the broker's symbol trade sessions/trade mode. If the broker session is closed, the signal is `market_closed`, not `outside_trading_session`.
- After EA source edits: run `scripts/sync_mt5_ea.sh`, then `pm2 restart mt5-terminal --update-env`. The installer keeps MT5 and Wine under `.runtime/`; do not restore host-specific runtime hardcoding.
- `scripts/start_mt5.sh` refreshes `scripts/mt5_configure_profile.py` before launching MT5 unless `FINROBOT_CONFIGURE_PROFILE_ON_START=false`.
- `scripts/autonomous_review_loop.py`: fixed a truncation bug — the trade report was sliced to the last 20000 chars, dropping the `Closed deal summary:` marker (~char 1800), so `closed_deals` always parsed as 0 and the reviewer never ran. Now keeps the head.
- LLM editing is HARD-GATED OFF by default via `AUTOREVIEW_ENABLE_LLM` (unset/false = analysis-only: the loop logs the real closed-deal count and journals analysis but never invokes opencode). Set `AUTOREVIEW_ENABLE_LLM=true` to re-enable autonomous code edits.

## Operational scripts (Phase 1 quick wins)

- `scripts/healthcheck.py`: stale heartbeat (>60s), missing Common Files, daily loss breach, unprotected managed positions, PM2 process state. Exits non-zero on any FAIL. Wire to cron or systemd timer.
- `scripts/archive_common_files.py`: snapshot `finrobot_status.json`, `finrobot_positions.csv`, `finrobot_deals.csv`, `finrobot_acks.csv` into `state/mt5/archive/YYYY-MM-DD/HHMMSS/`. Run daily via cron (state/ is gitignored).
- `config/logrotate-finrobot` + `scripts/install_logrotate.sh`: drop-in policy for `logs/combined.log` (rotate daily, keep 14, reload PM2 log handles). Install with `sudo scripts/install_logrotate.sh`.
- `docs/RELEASE_CHECKLIST.md`: pre-flight, compile, pre-deploy snapshot, restart, post-deploy verification, and rollback steps. Run this in order before any EA source / risk / bridge change.
- Tests cover the new scripts: `tests/test_healthcheck.py` and `tests/test_mt5_trade_report.py`. Full suite: `26 passed`.
