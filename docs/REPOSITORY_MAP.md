# FinRobot Repository Map

Last verified: 2026-07-14

Use this document as the first navigation aid for repo work. It identifies the
active runtime, code ownership boundaries, data flows, and known validation
gaps. It is a map, not a substitute for reading the exact files being changed.

## Authority and Scope

The current mandate is MT5 demo trading for `XAUUSD` only.

Authority order:

1. `AGENTS.md` for owner directives and change rules.
2. `broker/mt5/FinRobotBridgeEA.mq5` and its three `.mqh` modules for live
   trading behavior.
3. Current MT5 Common Files plus `scripts/mt5_trade_report.py` for deployed
   state and realized performance.
4. `ecosystem.config.js` for active services.
5. Python under `finrobot/backtest/`, `finrobot/xau_profiles.py`, and
   `scripts/xau_strategy_lab.py` for the promotion research path.

`CLAUDE.md` and `QUANT_ROADMAP.md` contain useful historical analysis, but they
are point-in-time documents and are not runtime truth.

## System Topology

```text
PM2
|- mt5-terminal
|  `- scripts/start_mt5.sh
|     `- MT5 under Wine/Xvfb
|        `- FinRobotBridgeEA.ex5 (XAUUSD timer-driven trading)
|- mt5-watchdog
|  `- scripts/mt5_watchdog.py (heartbeat check and terminal restart only)
|- autonomous-review
|  `- scripts/autonomous_review_loop.py
|     |- scripts/mt5_trade_report.py
|     `- scripts/xau_strategy_lab.py (analysis by default)
`- finrobot-dashboard
   `- dashboard/app.py (read-only Streamlit UI on 127.0.0.1:8501)

FinRobotBridgeEA.ex5
|- reads optional commands, strategy profile, and blackout CSV files
|- writes status, positions, deals, and acknowledgement files
`- MT5 Common Files
   |- report/status/dashboard readers
   `- cron ingestion -> data/finrobot.duckdb -> research/metrics/validation
```

There is no active Python order executor. Automatic orders originate inside the
MQL5 EA. The dashboard is read-only. No PM2 process currently writes
`finrobot_commands.csv`.

## Live Trading Path

### MQL5 ownership

| File | Live responsibility |
|---|---|
| `broker/mt5/FinRobotBridgeEA.mq5` | EA inputs, timer lifecycle, runtime profile parser, command execution, auto signals, order placement, status/position/deal exports. |
| `broker/mt5/SmartMoney.mqh` | Premium/discount range, FVG, order block, liquidity sweep, structure shift, long/short SMC scores. |
| `broker/mt5/RiskManagement.mqh` | Broker-day closed PnL aggregation, legacy session windows, dynamic break-even. |
| `broker/mt5/BridgeIO.mqh` | CSV acknowledgement append and string sanitizing helpers. |
| `broker/mt5/scripts/ExportM1Bars.mq5` | Manual MT5 M1 history export for offline research. Not part of order execution. |

The EA is timer-driven at one-second intervals. Its lifecycle is:

1. `OnInit`: read `EA_MANIFEST.txt`, load managed symbols, load the optional
   runtime profile, initialize the money-management snapshot, and write bridge
   files.
2. `OnTimer`: reload the profile every 30 ticks, poll commands, enforce stop
   policy, apply break-even, run `ManageAutoSymbol`, write status/positions, and
   refresh the 14-day deal export every 10 ticks.
3. `OnTick`: refresh status only.

`ManageAutoSymbol` evaluates gates in this order:

```text
global/account trading enabled
-> daily realized-loss limit
-> XAU enabled
-> weekday + broker session
-> recent drawdown / loss streak / blackout recovery controls
-> max positions / cooldown
-> M1 bars and indicator availability
-> spread
-> signal (ATR impulse plus remaining quick-momentum/momentum paths)
-> ATR regime
-> ADX regime
-> same-side position cap
-> XAU premium/discount gate
-> SMC score
-> SL/TP distances
-> daily-risk volume
-> market order and acknowledgement
```

Compiled defaults are M1, SMC score 4+, `1.00%` broker-day snapshot risk per
position, `1.00%` realized daily loss limit, two XAU positions, 50.0 lots
maximum, 1.2 ATR stop, and 2.4R take profit. The score-5 multiplier is retained
for lower-risk runtime profiles but cannot exceed the hard 1.00% effective-risk
cap.
Runtime profile values are clamped in both the EA and
`finrobot/xau_profiles.py`.

### Money-management mechanics

Live sizing is implemented by `DailyRiskVolume` in the EA:

```text
risk money = daily equity snapshot * risk fraction
            * high-confluence multiplier when score threshold is met
            capped at 1.00% of the daily equity snapshot
            * bad-day multiplier after realized broker-day PnL turns negative

risk per lot = (SL distance / broker tick size) * broker tick value
lots         = min(symbol cap, risk money / risk per lot)
```

The daily kill switch currently uses managed closed PnL only. It does not add
floating PnL or reserved risk from other open positions.

### Common Files contract

| File | Direction | Purpose | Main consumers |
|---|---|---|---|
| `finrobot_status.json` | EA writes | Heartbeat, account, deployed version/SHA, profile, money management, quotes, signal counters. | watchdog, status, healthcheck, dashboard, ingestion, metrics |
| `finrobot_positions.csv` | EA writes | Current magic-number managed positions. | report, healthcheck, dashboard, ingestion |
| `finrobot_deals.csv` | EA writes | Rolling 14-day magic-number deal history. | report, dashboard, ingestion, parity tooling |
| `finrobot_acks.csv` | EA appends | Command and automatic fill/rejection events. | report, dashboard, ingestion, parity tooling |
| `finrobot_commands.csv` | EA reads/deletes | Optional external `MARKET`, `CLOSE`, and `CLOSE_ALL` requests. | no active writer |
| `finrobot_strategy_profile.csv` | EA reads | Optional bounded key/value runtime overrides. | strategy lab may write only through gated deployment |
| `finrobot_blackout.csv` | EA reads | Optional broker-time start/end/reason blackout windows. | operator-managed; only active when profile enables it |
| `finrobot_entry_pause.flag` | EA reads | Operator-controlled pause for all new automatic and command-file market entries. | `scripts/mt5_entry_pause.py`; close actions and position management remain active |
| `finrobot_export_XAUUSD_M1.tsv` | EA writes | Bounded periodic M1 bar export for fresh research. | autonomous review harvest and price loader |
| `EA_MANIFEST.txt` | EA reads at init | Deployed EA version and git SHA. | generated by release tooling and copied by sync |

`scripts/runtime_paths.py` is the shared Python resolver for repo-local runtime,
Wine prefix, terminal, and Common Files locations. Runtime artifacts under
`.runtime/`, `state/`, and `logs/` are intentionally gitignored.

## Runtime and Operations

| Area | Files | Notes |
|---|---|---|
| Process definitions | `ecosystem.config.js` | Only the four PM2 services shown above are active. All PM2 output uses `logs/combined.log`. |
| Install/bootstrap | `install.sh`, `.env.sample` | Installs Python/PM2/MT5 and configures the repo-local runtime. Never print `.env` or the generated login INI. |
| MT5 startup | `scripts/start_mt5.sh`, `scripts/mt5_configure_profile.py`, `scripts/wine_box64.sh` | Rewrites the secret login INI, enforces the startup profile, and starts MT5 through the selected Wine path. |
| EA sync/release | `scripts/sync_mt5_ea.sh`, `finrobot/release_manifest.py`, `scripts/release_manifest.py` | Sync regenerates release manifests, copies source, and invokes MetaEditor when present. Compile output still requires inspection before restart. |
| Health/recovery | `scripts/mt5_status.py`, `scripts/healthcheck.py`, `scripts/mt5_watchdog.py`, `scripts/mt5_entry_pause.py` | Healthcheck covers runtime, disk, research freshness, and all PM2 services. Watchdog remains heartbeat-only. The pause CLI manages the persistent no-new-entry flag. |
| Reporting | `scripts/mt5_trade_report.py`, `dashboard/app.py` | Current Common Files are the input. Strategy attribution comes from deal comments. |
| Scheduled operations | `scripts/mt5_minute_cycle.py`, `config/finrobot.cron` | Common Files ingestion and bid/ask capture run sequentially; cron serializes all DuckDB jobs with a shared file lock. |
| Archive/log policy | `scripts/archive_common_files.py`, `config/logrotate-finrobot`, `scripts/install_logrotate.sh` | Archives go under ignored `state/`; combined, cron, and alert logs rotate daily. |
| Reverse proxy | `config/nginx-trading.aims-sg.com.conf` | Proxies the read-only dashboard. |

Optional cron jobs in `config/finrobot.cron` ingest bridge snapshots and quotes
every minute, export metrics every five minutes, validate hourly, and archive
daily. The cron configuration writes `logs/cron.log`; it is separate from the
four active PM2 processes and must be installed explicitly.

## Python Data and Research Path

### Warehouse and observability

| File | Responsibility |
|---|---|
| `finrobot/data_store.py` | DuckDB schema and ingestion/query functions for status, positions, deals, acks, prices, and experiments. |
| `scripts/mt5_ingest_common_files.py` | Snapshot Common Files into DuckDB, preferring deployed status metadata. |
| `scripts/mt5_snapshot_prices.py` | Store live bid/ask/spread observations. |
| `scripts/load_historical_prices.py` | Load normalized historical bar CSVs. |
| `scripts/harvest_mt5_export.py` | Discover/copy MT5 exports and invoke the loader. |
| `finrobot/validators.py`, `scripts/mt5_validate_warehouse.py` | Warehouse schema, freshness, reconciliation, and risk validation. |
| `finrobot/metrics.py`, `finrobot/alerts.py`, `finrobot/alert_delivery.py` | Metrics snapshots, alert evaluation, and transition delivery. |

The tracked historical input is `data/XAUUSD1.csv`; a second local
`data/XAUUSD_M1.csv` is currently available but ignored. The local DuckDB
warehouse is also ignored by git even when a working copy exists.

### Deterministic backtester

| Module | Responsibility |
|---|---|
| `finrobot/backtest/engine.py` | Bar loop, signal handling, positions, SL/TP exits, break-even, recovery gates, and trade ledger. |
| `finrobot/backtest/position.py` | Position model, `PositionSizer`, and `DailyRiskSizer`. |
| `finrobot/backtest/fills.py` | Deterministic point-size-aware spread/slippage/commission/swap fill assumptions. |
| `finrobot/backtest/instruments.py` | Broker-calibrated point, tick-value, spread, and commission specifications used by XAU research. |
| `finrobot/backtest/metrics.py` | PnL, drawdown, Sharpe/Sortino/Calmar, expectancy, loss streak, and distribution metrics. |
| `finrobot/backtest/walkforward.py` | Purged and embargoed walk-forward folds plus stability aggregation. |
| `finrobot/backtest/reporter.py` | Machine-readable and Markdown reports with verdicts and attribution. |
| `finrobot/backtest/parity.py`, `parity_replay.py` | Compare Python decisions with EA acknowledgements. |
| `finrobot/backtest/strategies/_xau_state.py` | Rolling M1-to-forming-M5 indicator state used to approximate the EA timer path. |
| `finrobot/backtest/strategies/xau_gates.py` | Python port of the live XAU indicator/PDA/SMC gates. |
| `finrobot/backtest/strategies/xau_atr_impulse.py` | Live ATR impulse strategy slice. |
| `finrobot/backtest/strategies/xau_gated.py` | PDA/SMC/ADX/cooldown/blackout wrapper. |
| `finrobot/backtest/strategies/xau_quick_momentum.py` | Quick-momentum parity/research slice. |

`buy_and_hold.py`, `stub_replay.py`, `xau_mean_reversion.py`,
`xau_ml_ensemble.py`, and `xau_seasonal.py` are offline sleeves or test helpers;
they do not place live orders.

### Profile lab and promotion

`finrobot/xau_profiles.py` owns the compiled-equivalent incumbent and four
bounded candidates. `scripts/xau_strategy_lab.py` loads XAU bars from DuckDB,
runs five purged/embargoed walk-forward evaluations plus a recent window, writes
experiment records, and ranks candidates.

The lab rejects data older than 72 hours by default. The autonomous loop
harvests the EA's periodic XAU M1 export before evaluation unless
`AUTOREVIEW_HARVEST_FIRST=false`.

A challenger must pass the report verdict, positive mean PnL, trade count,
consistency, worst-fold, recent PnL/PF, and incumbent-relative gates. The lab
does not deploy unless `--write-profile` is supplied; the autonomous loop adds
that flag only when `AUTOREVIEW_ENABLE_PROMOTION_DEPLOY=true`. LLM editing is a
separate default-off gate controlled by `AUTOREVIEW_ENABLE_LLM`.

Research orchestration:

| File | Purpose |
|---|---|
| `scripts/run_backtest.py` | Single deterministic run. |
| `scripts/run_walkforward.py` | Walk-forward CLI for supported XAU strategies. |
| `scripts/run_parity.py`, `scripts/xau_parity_watch.sh` | EA/Python parity replay and scheduled checks. |
| `scripts/run_quant_pipeline.py` | Experimental features/regime/ML/significance pipeline. Not used by live execution or profile promotion. |
| `finrobot/research/experiments.py`, `registry.py`, `comparison.py` | Experiment persistence, registry indexing, and incumbent/challenger decisions. |
| `finrobot/research/features.py`, `regime.py`, `models.py`, `significance.py`, `optimizer.py` | Experimental feature, HMM, ML, statistical, and optimization tools. Optional dependencies are not all declared in core requirements. |
| `scripts/promote_compare.py`, `scripts/report_run.py`, `scripts/strategy_report.py` | Research report and promotion-support CLIs. |

### Not in the live or promotion path

These modules are retained research/legacy code and must not be mistaken for
deployed alpha:

- `finrobot/hft.py`
- `finrobot/indicators.py`
- `finrobot/strategies/grid.py`
- `finrobot/strategies/backtesting.py` (includes martingale research)
- `finrobot/strategies/harmonics.py`
- `finrobot/strategies/smart_money.py`
- `finrobot/strategies/orchestrator.py`
- `finrobot/risk/kelly.py`, `vol_target.py`, and `limits.py`
- `finrobot/monitoring/alpha_decay.py`

`finrobot/execution/` currently has no implementation. `python -m finrobot`
only prints the PM2 startup hint.

## Test and Release Surface

The Python suite lives in `tests/`. High-impact ownership is:

| Change | Required adjacent tests/checks |
|---|---|
| EA signals or SMC/PDA gates | `test_xau_atr_impulse.py`, `test_xau_gates.py`, `test_xau_gated.py`, live parity tests, MetaEditor compile |
| Money management | `test_daily_risk_sizer.py`, `test_backtester.py`, `test_break_even.py`, trade report, healthcheck, MetaEditor compile |
| Runtime profiles/promotion | `test_xau_profiles.py`, `test_xau_strategy_lab.py`, walk-forward tests |
| Bridge schema/reporting | `test_mt5_trade_report.py`, `test_healthcheck.py`, data-store/validator/metrics tests |
| Startup/watchdog | `test_mt5_watchdog.py`, profile/start scripts, PM2 status |
| Release/parity | release-manifest, parity, parity-replay, and parity-watch tests |

The only checked-in GitHub Actions workflow currently deploys GitHub Pages. It
does not run Python tests or compile MQL5. Use `docs/RELEASE_CHECKLIST.md` for EA
changes, but verify its version/test-count examples against current code.

Minimum repo checks after edits:

```bash
python3 -m compileall -q finrobot scripts
python3 scripts/mt5_trade_report.py
.venv/bin/python -m pytest -q -p no:cacheprovider
```

After EA changes also run `scripts/sync_mt5_ea.sh`, inspect the MetaEditor
compile result, restart only `mt5-terminal`, and verify deployed status/report.

## Verified Snapshot and Known Gaps

Observed on 2026-07-15; re-run the listed commands before relying on numbers:

- Runtime: all four PM2 services online, v1.41 heartbeat fresh, no open managed
  positions, compiled defaults active, and autonomous demo entries enabled.
- Performance: the sliding deal export currently contains 28 closed XAU deals,
  total PnL `-7,989.96`, win rate `17.86%`, and expectancy `-285.36`.
- Research data: the corrected local warehouse has 200,000 XAU M1 bars from
  2026-01-19 through 2026-07-14. The EA exports broker-wall epoch timestamps and
  the loader preserves the same server-time convention for legacy text bars.
- Latest 50,000-bar M1 profile lab completed in 363 seconds at low priority.
  Every candidate failed promotion. Breakout had mean fold PnL `10,023.54` and
  `0.60` consistency, but recent PnL was `-5,767.87` and its worst fold was
  `-12,701.68`; no profile was deployed.
- The targeted `macd_continuation_m1` repair improved mean fold PnL to
  `16,808.09` and recent PnL to `115,280.72`, but mean PF `1.05` and worst-fold
  PnL `-37,275.95` failed promotion. The challenger remains undeployed.
- Release identity: live status reports v1.41 and the current repository HEAD
  SHA. MetaEditor compiled the deployed artifact with zero errors.

Known issues to address before trusting strategy promotion or further increasing risk:

1. The active ATR impulse strategy has negative live expectancy and no current
   candidate has cleared promotion gates. Demo entries were resumed by explicit
   owner instruction; do not infer that this is evidence of positive expectancy.
2. The corrected dataset is current but still covers less than seven months;
   it is insufficient for multi-regime or statistical edge claims.
3. The EA broker-day equity snapshot is in memory and is reset from current
   equity when the EA restarts. Daily loss uses realized managed PnL only.
4. Each position is capped at 1.00% planned stop risk, but two simultaneous
   positions can expose roughly 2.00% and the daily loss gate does not reserve
   aggregate open-position risk.
5. Recovery controls exist but compiled defaults leave loss-streak, early
   drawdown, blackout, and ATR-regime pauses disabled.
6. Signals use the forming M1 bar and are evaluated every timer tick. Cooldown
   and `lastTradeTimes` are in memory and reset after an EA restart.
7. `EnforceManagedRisk` closes stopless managed-symbol positions only when the
   comment does not start with `FinRobot_`; healthcheck is the main detector for
   an EA-owned position that loses SL/TP protection.
8. The bridge deal export covers 14 days, acknowledgement IDs can repeat after
    restart, and status telemetry resets daily/restart, so Common Files alone
    are not a durable audit ledger.
9. Compile success, Python tests, and parity are not enforced in CI.
10. Several historical documents and package descriptions have become stale;
    use this map's authority order and current command output.

## Targeted Read Checklist

Do not rescan the entire repository for routine work. Start with:

1. `AGENTS.md`, this map, and `git status --short`.
2. `python3 scripts/mt5_trade_report.py` and the relevant current log slice.
3. The ownership row for the requested behavior.
4. The adjacent tests in the test/release table.
5. For EA/risk changes, `docs/RELEASE_CHECKLIST.md` and the live status before
   syncing or restarting anything.

Update this map whenever a runtime process, live order path, bridge file schema,
promotion gate, ownership boundary, or major validation gap changes.
