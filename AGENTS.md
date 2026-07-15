# AIQuantTrader Agent Guide

## Operating mandate

AIQuantTrader is now an MT5-first autonomous demo-trading repo. Trade and optimize only:

- `XAUUSD`

## Source of truth

- Repository navigation map: `docs/REPOSITORY_MAP.md` (read this before broad rescans)
- Active EA: `broker/mt5/AIQuantTraderBridgeEA.mq5` (v2.00)
- EA Modules: `broker/mt5/RiskManagement.mqh`, `SmartMoney.mqh`, `BridgeIO.mqh`
- Runtime process list: `ecosystem.config.js`
- MT5 heartbeat watchdog: `scripts/mt5_watchdog.py`
- Installer: `install.sh`
- MT5 status/report tools: `scripts/mt5_status.py`, `scripts/mt5_trade_report.py`
- Dashboard: `dashboard/app.py` served by `aiquanttrader-dashboard` on `127.0.0.1:8501`
- MT5 startup/profile helper: `scripts/mt5_configure_profile.py`
- 6-hour Opencode loop: `scripts/autonomous_review_loop.py`
- XAU profile lab: `scripts/xau_strategy_lab.py`
- Runtime XAU profile definitions: `aiquanttrader/xau_profiles.py`
- Indicators: `aiquanttrader/indicators.py` (consolidated)
- HFT Logic: `aiquanttrader/hft.py` (consolidated)
- Runtime MT5/Wine files live under `.runtime/` and are intentionally gitignored.
- State/logs are runtime artifacts and are intentionally gitignored.
- On arm64, MT5 is experimental and must run through x86_64 emulation. Prefer Hangover Wine when installed; otherwise use `scripts/wine_box64.sh` with Box64 and a repo-local new-WoW64 x86_64 Wine build under `.runtime/wine-x86_64/wine-11.10-amd64-wow64`.
- MT5 startup config should keep `[StartUp] Expert=AIQuantTrader\AIQuantTraderBridgeEA` and `[Experts] Enabled=1`. The Default profile should keep a single `XAUUSD` chart by default, so the PM2-managed terminal loads the bridge EA headlessly without opening duplicate startup charts.
- If `MT5_LOGIN`, `MT5_PASSWORD`, or `MT5_SERVER` are empty, MT5 can start and load the EA file but will show the account wizard and the bridge will not produce Common Files until credentials are configured.
- `scripts/start_mt5.sh` rewrites `Config\aiquanttrader-login.ini` from `.env` before every MT5 launch; do not print the generated file because it contains the MT5 password.
- On a clean generic MT5 install, seed the IC Markets server database once through MT5's account wizard by searching `Raw Trading Ltd` and selecting `ICMarketsSC-Demo`; the resulting `.runtime` server data survives normal PM2 restarts.

## PM2 processes

Use only these active processes:

```bash
./install.sh
pm2 start ecosystem.config.js
pm2 restart aiquanttrader-mt5 aiquanttrader-watchdog aiquanttrader-review aiquanttrader-dashboard --update-env
pm2 list
```

## MT5 bridge files

The EA uses MT5 Common Files:

- `aiquanttrader_status.json` for heartbeat/account/symbol status.
- `aiquanttrader_positions.csv` for open managed positions.
- `aiquanttrader_deals.csv` for managed deal history.
- `aiquanttrader_acks.csv` for fills/rejections/auto decisions.
- `aiquanttrader_commands.csv` for optional external commands.
- `aiquanttrader_strategy_profile.csv` for optional bounded XAU runtime profile overrides.
- `aiquanttrader_entry_pause.flag` for an operator-controlled new-entry pause that leaves monitoring and position management active.
- `aiquanttrader_export_XAUUSD_M1.tsv` for the EA's bounded periodic research-bar export.

Use `python3 scripts/mt5_trade_report.py` before making strategy changes. It summarizes open MT5 positions and closed managed deal performance.

## Auto-improvement loop

`aiquanttrader-review` runs every 6 hours. It:

1. Reads MT5 trade report and improvement memory.
2. On service restart, waits one full `AUTOREVIEW_INTERVAL_HOURS` period before the first review unless `AUTOREVIEW_RUN_ON_START=true`.
3. Skips if fewer than `AUTOREVIEW_MIN_TRADES` closed deals are available.
4. Harvests the EA's current XAU M1 export, then runs `scripts/xau_strategy_lab.py` at low CPU priority over the latest 50,000 bars by default. Timeouts are journaled as structured failures instead of aborting the entire cycle.
5. Writes a live `aiquanttrader_strategy_profile.csv` only when `AUTOREVIEW_ENABLE_PROMOTION_DEPLOY=true` and the lab winner clears promotion gates.
6. Calls Opencode with the current mandate only when `AUTOREVIEW_ENABLE_LLM=true`.
7. Runs `compileall` and `scripts/mt5_trade_report.py` after successful Opencode changes.
8. Restarts `aiquanttrader-mt5` and `aiquanttrader-review` when checks pass.

Default minimum is 12 closed deals and default cadence is every 6 hours. Keep this evidence gate unless the owner asks for more aggressive changes.

## Change rules

- Make direct changes; repo is in git.
- Before editing, inspect `git status --short` and relevant logs/reports.
- After editing, run at least `python3 -m compileall -q aiquanttrader scripts` and `python3 scripts/mt5_trade_report.py`.
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

- `broker/mt5/AIQuantTraderBridgeEA.mq5` must keep daily risk lot sizing enabled unless the owner explicitly disables it.
- New trades are sized from the broker-day equity snapshot, `DailyRiskPerTradeFraction`, and SL distance; do not revert to fixed `0.01` lots.
- `scripts/mt5_trade_report.py` reports total PnL, daily PnL, strategy expectancy, and the EA `money_management` status block. Check it before strategy edits.
- Current owner posture is XAUUSD-only high-risk demo trading. Keep daily risk lot sizing enabled, stricter SMC gates, and two managed positions per symbol.
- Owner directive (2026-07-14, superseding the earlier pause and reduced-risk posture): autonomous entries are enabled on the existing IC Markets demo account at a hard maximum of 1.00% planned stop risk per position. Keep the operator pause control available and do not switch to a real-money account without explicit owner approval.

## Current strategy posture

- Current owner directive: non-XAU symbols are retired completely and must not be traded, scanned, optimized, or restored without an explicit owner reversal.
- Owner directive (2026-06-26): keep proportional compounding lot sizing enabled for the high-equity demo account. Do not restore the emergency auto-entry pause or the `0.25` XAU lot ceiling unless the owner explicitly asks.
- `AIQuantTraderBridgeEA.mq5` should keep `AutoSymbols="XAUUSD"`, `AutoTimeframe=PERIOD_M1`, `EnableSmartMoneyGates=true`, `EnableXauAutoTrading=true`, `UseDailyRiskLotSizing=true`, `DisableWeakStrategySignals=true`, `EnableXauWeekdayMarketHours=true`, `MinSmcConfluenceScoreXAUUSD=4`, `MaxAutoPositionsPerSymbol=2`, `DailyRiskPerTradeFraction=0.0100`, and `MaxLotPerTradeXAUUSD=50.0` unless the owner changes risk again.
- Runtime profiles may override bounded XAU-only settings through `aiquanttrader_strategy_profile.csv`; compiled defaults remain the fallback when the file is missing, empty, or disabled.
- Runtime profiles may also arm recovery controls: loss-streak pause, bad-day risk downshift, recent drawdown pause, blackout-file windows, and ATR regime rejection.
- Owner-approved demo bounds are 1.00% effective risk per position and 50.0 XAU lots. The compiled daily loss limit remains 1.00% and the position limit remains two; do not raise these bounds again without an explicit owner reversal.
- Smart-money gate intent: trade XAU only from stricter premium/discount SMC score 4+ setups. High-confluence score 5+ entries may size up only until the hard 1.00% effective risk cap. `smc_reject` and `xau_pda_reject` mean the signal was intentionally blocked.
- Session intent: XAU may scan Monday-Friday whenever the broker symbol is inside its configured trade session; it should not be limited to London/NY windows.
- `aiquanttrader_status.json` includes per-symbol `session_gated`, `weekday_market_hours`, `session_open`, and daily `signal_telemetry` counters. Use these counters to distinguish no-signal periods from intentional market-closed, spread/cost, SMC, direction, PDA, cooldown, or session rejections before changing strategy.

## Logging

- `aiquanttrader-mt5` runs Wine with `WINEDEBUG=-all` (set in `scripts/start_mt5.sh`) to suppress Wine GUI debug spam (toolbar/datetime/etc.). Real MT5 trade/connection state surfaces in `aiquanttrader_status.json` and MT5's own Experts journal, not Wine stderr.
- `aiquanttrader-watchdog` is intentionally lightweight: it only checks the `aiquanttrader_status.json` heartbeat and restarts `aiquanttrader-mt5` through PM2 after the stale window/cooldown. It must not trade or write MT5 command files.
- On arm64, set `AIQUANTTRADER_ALLOW_EMULATED_MT5=true`; if `AIQUANTTRADER_WINE_CMD` is unset, install/start/sync scripts use Hangover's `wine` when present, otherwise `scripts/wine_box64.sh`.
- Active PM2 services write stdout/stderr directly to the single operator log: `logs/combined.log`. Do not add new sidecar app logs for active services unless the owner asks.
- Retired-process and stale install logs should stay out of `logs/`. Keep `logs/` focused on `combined.log` and active trading diagnostics only.

## Strategy & autonomy changes (2026-05-30)

- Owner-requested maximum-frequency demo defaults (2026-06-01) failed and are retired: do not restore `DisableWeakStrategySignals=false`, `MaxAutoPositionsPerSymbol=5`, `MinSecondsBetweenTrades=60`, `MaxLotPerTrade=1.00`, `DailyRiskPerTradeFraction=0.0050`, or `MinSmcConfluenceScore=1` without fresh evidence.
- Compounding demo defaults (2026-06-26): `DisableWeakStrategySignals=true`, `MaxAutoPositionsPerSymbol=2`, `MaxLotPerTrade=5.0`, `MaxLotPerTradeXAUUSD=5.0`, `DailyRiskPerTradeFraction=0.0010` (0.10% of equity), and `DailyLossLimitFraction=0.01` (1.00% of equity).
- High-risk demo update (2026-07-14): owner explicitly raised planned risk to `DailyRiskPerTradeFraction=0.0100` (1.00% per position). `MaxLotPerTrade` and `MaxLotPerTradeXAUUSD` are 50.0 so the risk calculation can reach its target on the high-equity demo account; high-confluence multiplication is hard-capped at 1.00% effective risk. The 1.00% realized daily loss limit and two-position limit remain unchanged.
- M1 execution update (2026-07-15): owner explicitly changed the active XAU strategy timeframe to `PERIOD_M1` for more opportunities. Keep the 1.00% per-position risk cap, two-position limit, SMC score 4+, PDA gates, and ADX filter unless separately changed.
- SMC tightening (2026-07-01): `MinSmcConfluenceScoreXAUUSD=4` after live fills showed score-3 XAU ATR impulse entries drove the recent drawdown. Keep score 4+ unless new evidence supports loosening.
- XAU-only update (2026-06-22): non-XAU trading is removed from active EA defaults, startup profile defaults, docs, and symbol-specific research/backtest scaffolding.
- XAU weekday-market update (2026-06-11): `EnableXauWeekdayMarketHours=true` bypasses the old London/NY-only gate for XAU and instead checks Monday-Friday plus the broker's symbol trade sessions/trade mode. If the broker session is closed, the signal is `market_closed`, not `outside_trading_session`.
- After EA source edits: run `scripts/sync_mt5_ea.sh`, then `pm2 restart aiquanttrader-mt5 --update-env`. The installer keeps MT5 and Wine under `.runtime/`; do not restore host-specific runtime hardcoding.
- `scripts/start_mt5.sh` refreshes `scripts/mt5_configure_profile.py` before launching MT5 unless `AIQUANTTRADER_CONFIGURE_PROFILE_ON_START=false`.
- `scripts/autonomous_review_loop.py`: fixed a truncation bug — the trade report was sliced to the last 20000 chars, dropping the `Closed deal summary:` marker (~char 1800), so `closed_deals` always parsed as 0 and the reviewer never ran. Now keeps the head.
- LLM editing is HARD-GATED OFF by default via `AUTOREVIEW_ENABLE_LLM` (unset/false = analysis-only: the loop logs the real closed-deal count and journals analysis but never invokes opencode). Set `AUTOREVIEW_ENABLE_LLM=true` to re-enable autonomous code edits.
- Runtime profile update (2026-07-07): `scripts/xau_strategy_lab.py` evaluates bounded aggressive XAU profiles and can write `aiquanttrader_strategy_profile.csv`; `AIQuantTraderBridgeEA.mq5` reloads the profile periodically and reports the active profile in `aiquanttrader_status.json`. Profile deployment is gated separately from LLM edits by `AUTOREVIEW_ENABLE_PROMOTION_DEPLOY`.
- Recovery-control update (2026-07-08): profile-lab promotion now requires recent-window evidence and challenger improvement over the incumbent. The EA exposes recovery settings in status and can block new entries after configured loss streaks, recent drawdown, active blackout windows from `aiquanttrader_blackout.csv`, or abnormal ATR regimes.
- M1 MACD repair research (2026-07-15): `macd_continuation_m1` requires a directionally strengthening MACD histogram and remains a lab-only challenger. Its first 50,000-bar run improved mean fold PnL to `16,808.09` with `0.60` consistency and strong recent results, but failed promotion on mean profit factor (`1.05`) and worst-fold PnL (`-37,275.95`). Do not force-deploy it; the EA v2.00 runtime gate defaults off.
- AIQuantTrader rebrand (2026-07-15): repository path, Python package, EA, PM2 services, environment prefix, Common Files, DuckDB, cron, logrotate, dashboard, and GitHub remote now use AIQuantTrader naming. `MagicNumber=20260522` and historical broker/deal data were intentionally preserved.

## Operational scripts (Phase 1 quick wins)

- `scripts/healthcheck.py`: stale heartbeat (>60s), missing Common Files, daily loss breach, unprotected managed positions, disk pressure, stale/failed autonomous research, and all active PM2 process states. Exits non-zero on any FAIL. `config/aiquanttrader.cron` runs it every five minutes.
- `scripts/mt5_watchdog.py`: lightweight PM2-managed heartbeat recovery for MT5. It restarts `aiquanttrader-mt5` only after stale/missing heartbeat detection and a cooldown.
- `scripts/archive_common_files.py`: snapshot `aiquanttrader_status.json`, `aiquanttrader_positions.csv`, `aiquanttrader_deals.csv`, `aiquanttrader_acks.csv` into `state/mt5/archive/YYYY-MM-DD/HHMMSS/`. Run daily via cron (state/ is gitignored).
- `scripts/mt5_minute_cycle.py`: runs Common Files ingestion and bid/ask capture sequentially. Cron wraps all DuckDB jobs with `/tmp/aiquanttrader-duckdb.lock` to prevent single-writer contention.
- `config/logrotate-aiquanttrader` + `scripts/install_logrotate.sh`: drop-in policy for `logs/combined.log`, `logs/cron.log`, and `logs/alerts.log` (rotate daily, keep 14, reload PM2 log handles). Install with `sudo scripts/install_logrotate.sh`.
- `docs/RELEASE_CHECKLIST.md`: pre-flight, compile, pre-deploy snapshot, restart, post-deploy verification, and rollback steps. Run this in order before any EA source / risk / bridge change.
- Tests cover the operational scripts, including aiquanttrader-review timeout handling and host/research health checks.
