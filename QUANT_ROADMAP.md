# FinRobot Quant Roadmap - XAUUSD-Only Status

Last updated: 2026-06-22

Status legend:

- `[done]` implemented or operationally present in the current repo/runtime.
- `[partial]` usable pieces exist, but the control is incomplete or not enforced end to end.
- `[not done]` not implemented yet.
- `[retired]` no longer part of the active mandate.

## 1. Current Standing

- Active trading mandate is `XAUUSD` only. BTC and all non-XAU symbols are retired from active trading, scanning, optimization, and roadmap scope unless the owner explicitly reverses the mandate. `[done]`
- Active EA is `broker/mt5/FinRobotBridgeEA.mq5`, version `1.35`, with `AutoSymbols="XAUUSD"` and `AutoTimeframe=PERIOD_M5`. `[done]`
- Daily risk lot sizing is enabled with `DailyRiskPerTradeFraction=0.0010` and `DailyLossLimitFraction=0.01`. `[done]`
- Current recovery defaults are conservative: weak signals disabled, XAU auto-trading enabled, SMC/PDA gates enabled, ADX regime filter enabled, max two managed XAU positions. `[done]`
- PM2 active runtime is `mt5-terminal`, `mt5-watchdog`, `autonomous-review`, and `finrobot-dashboard`, all writing to `logs/combined.log`. `[done]`
- MT5 healthcheck currently passes: Common Files present, heartbeat fresh, no daily loss breach, no open unprotected managed positions, `mt5-terminal` online. `[done]`
- Live trade report currently shows zero open managed positions and positive total managed XAU closed PnL over the available 14-day MT5 deal export. `[done]`
- Full Python test suite passed with `.venv/bin/python -m pytest -q -p no:cacheprovider`: `324 passed`. `[done]`
- `QUANT_ROADMAP.md` was stale before this update: it referenced EA v1.29, `XAUUSD,BTCUSD`, six smoke tests, and several blockers that have since been fixed. `[done]`

## 2. Phase 1: MVP Hardening

Goal: make the current demo EA safe, observable, and reproducible enough to keep running.

Exit criteria:

- Command path enforces mandate and basic risk policy. `[done]`
- Healthcheck fails loudly on stale heartbeat, missing Common Files, PM2 offline, unprotected positions, or daily loss breach. `[done]`
- Python tests pass. `[done]`
- EA sync/compile status is recorded and checked before deploy. `[partial]`
- `.env.sample` documents safety toggles. `[done]`
- Operator runbook/release checklist exists. `[done]`

Tasks:

- Harden `ExecuteCommand`: whitelist only managed symbols, require SL/TP for market commands, enforce max lot, daily loss, max positions, and magic-safe close. `[done]`
- Retire BTC from the command and auto-trading mandate. `[done]`
- Rename/clarify risk inputs from ambiguous percent semantics to fraction semantics. `[done]`
- Keep `UseDailyRiskLotSizing=true` unless the owner explicitly disables it. `[done]`
- Add `AUTOREVIEW_ENABLE_LLM=false` to `.env.sample` and docs. `[done]`
- Keep autonomous review LLM editing hard-gated off by default. `[done]`
- Add `scripts/healthcheck.py` for heartbeat, Common Files, PM2, unprotected positions, and daily loss status. `[done]`
- Add tests for `scripts/mt5_trade_report.py`. `[done]`
- Fix the hardcoded `/home/openclaw` logging path. `[done]`
- Keep active service logs consolidated in `logs/combined.log`. `[done]`
- Remove/rotate `.env.bak-arm-wine` without printing secrets. `[done]`
- Archive Common Files snapshots daily under `state/mt5/archive/YYYY-MM-DD/HHMMSS/`. `[done]`
- Capture EA compile log after sync. `[partial]`
- Make compile success a hard CI/release gate, not only a checklist step. `[not done]`

## 3. Phase 2: Productionization

Goal: turn the demo runtime into a controlled trading service.

Exit criteria:

- MT5 Common Files are ingested into a durable local warehouse. `[done]`
- Alerts exist for core runtime/risk failures. `[done]`
- CI runs Python tests and MQL compile. `[partial]`
- Deployed EA version, git SHA, source, and compiled artifact are released together. `[partial]`

Tasks:

- Build `scripts/mt5_ingest_common_files.py` and `finrobot/data_store.py`. `[done]`
- Store raw status, positions, deals, and acks with ingestion time, broker time, EA version, git SHA, symbol, and core risk fields. `[partial]`
- Store XAU bid/ask/spread snapshots from live status into the warehouse. `[done]`
- Add Parquet export/partitioning for long-term research portability. `[not done]`
- Add schema validation and reconciliation checks. `[done]`
- Add metrics exporter and alert rules. `[done]`
- Deliver alert transitions to Telegram or equivalent operator channel. `[partial]`
- Add JSON structured logs for active services. `[not done]`
- Add log rotation for `logs/combined.log`. `[done]`
- Run FinRobot in a dedicated PM2 namespace or dedicated host. `[partial]`
- Add watchdog restart recovery for stale MT5 heartbeat. `[done]`
- Release `.mq5`, `.mqh`, `.ex5`, config, release manifest, and git SHA together. `[partial]`
- Fix DuckDB lock contention between ingestion, metrics, validation, and research readers. `[not done]`
- Make all DuckDB-backed cron/docs commands consistently use `.venv/bin/python` or bootstrap dependencies. `[partial]`

## 4. Phase 3: XAU Strategy Research Platform

Goal: stop hand-tuning live MQL inputs and promote changes only from reproducible XAU evidence.

Exit criteria:

- Event-driven backtester matches EA assumptions for XAU entries, gates, stops, sizing, and fills. `[partial]`
- Walk-forward validation is standard before promotion. `[partial]`
- Costs use broker spread, commission, swap, reject, and slippage data. `[partial]`
- Every experiment has immutable inputs, code/data hashes, metrics, and a written promotion decision. `[partial]`

Tasks:

- Ingest XAUUSD broker M1 bid/ask/spread data from MT5 exports and live snapshots. `[partial]`
- Ingest BTCUSD research data. `[retired]`
- Implement an EA-equivalent XAU simulator for M5 indicators, PDA/SMC gates, ADX filter, stops, sizing, sessions, and fills. `[partial]`
- Keep the live XAU parity test green; current documented result is `19/19 matched (100.00%)`. `[done]`
- Add purged walk-forward splits and leakage checks. `[partial]`
- Track experiment configs, data hashes, code hashes, metrics, and decisions in DuckDB. `[partial]`
- Compare challenger strategies against the current EA before live changes. `[partial]`
- Promote no XAU strategy from a single CSV or single short live window. `[done]`
- Add richer fill/reject attribution: requested price, observed bid/ask, result price, spread, retcode, latency bucket, strategy ID, gate decisions, and risk decision. `[not done]`
- Persist signal telemetry counters durably; status JSON counters reset and are not a reliable trade ledger. `[not done]`

## 5. Phase 4: XAU Multi-Strategy Control

Goal: support multiple XAU strategy sleeves without increasing operational risk.

Exit criteria:

- Strategy registry exists with per-strategy versioning and promotion state. `[partial]`
- Every order has durable strategy attribution. `[partial]`
- Risk limits can be applied per strategy and globally. `[partial]`
- Independent kill switches and staged rollout exist. `[not done]`

Tasks:

- Add or formalize a versioned strategy registry for XAU strategy sleeves. `[partial]`
- Tag every order with `strategy_id` and strategy version, not only a free-form MT5 comment. `[partial]`
- Build per-strategy PnL attribution from deals and acks. `[partial]`
- Build portfolio risk aggregator for multi-asset expansion. `[retired]`
- Build XAU strategy-level risk aggregator: max daily loss, max loss streak, max exposure, max same-side concentration. `[not done]`
- Separate signal generation from execution so the EA can act as a broker adapter. `[not done]`
- Add champion/challenger deployment and capital allocation rules. `[partial]`
- Add file/env kill switch checked every EA timer tick; block new orders and optionally flatten managed positions. `[not done]`

## 6. Phase 5: Institutional-Grade Operations

Goal: make the operation auditable, resilient, and governable before real or client capital.

Exit criteria:

- Compliance-ready audit trail exists. `[not done]`
- Access control and least-privilege service accounts exist. `[not done]`
- BCP/DR restore process is tested. `[not done]`
- Model-risk review and incident process are written and exercised. `[not done]`
- Legal, tax, and jurisdictional obligations are documented before live capital. `[not done]`

Tasks:

- Add WORM-style audit logs. `[not done]`
- Add signed releases. `[not done]`
- Move secrets to a real secrets manager. `[not done]`
- Add least-privilege service accounts. `[not done]`
- Build disaster recovery environment. `[not done]`
- Test restore from backup. `[not done]`
- Require independent risk approval for EA/risk/deploy changes. `[not done]`
- Run monthly strategy review. `[not done]`
- Add regulatory/tax reporting workflow before real or client capital. `[not done]`

## 7. Current XAU Market Posture

This section is operational context, not a trading signal.

- XAUUSD is trading around the low `4200` area on 2026-06-22, with the live broker quote observed near `4198.64/4198.74` and external spot gold reporting near `4211`. `[done]`
- The macro backdrop is mixed: central-bank gold demand remains a structural support, while Fed policy expectations are less friendly to gold because markets are no longer pricing an easy path to cuts. `[done]`
- Recent gold behavior is volatile after a sharp correction from the January 2026 high. This favors controlled recovery trading over looser high-frequency settings. `[done]`
- The live EA is positive over the available managed XAU deal window, but recent daily performance was uneven: strong June 11-12 and June 16, losses on June 15, June 17-19, then a June 22 recovery. `[done]`
- Keep XAU-only scope until the XAU execution, data, and promotion loop is more durable. `[done]`
- Keep conservative defaults: 0.10% daily-risk lot sizing, 1.00% daily loss limit, max two XAU positions, SMC/PDA gates, ADX filter, and weak-signal suppression. `[done]`
- Do not loosen SMC/PDA/ADX gates just because today recovered. `[done]`
- Add a bad-day throttle: reduce risk or pause new entries after a configured daily loss, loss streak, or two losing broker days in a rolling window. `[partial]`
- Add a high-volatility/news blackout for Fed decisions, CPI/PCE, NFP, major geopolitical shock windows, and abnormal XAU spread/ATR regimes. `[partial]`
- Use deals, acks, and the warehouse as performance truth; use `finrobot_status.json` telemetry only as a live diagnostic. `[partial]`

## 8. Prioritized Backlog

### P0: Next Safety/Quality Work

- Fix DuckDB warehouse lock contention with retry/backoff, a single-writer pattern, or separate read snapshots. `[not done]`
- Make `scripts/mt5_validate_warehouse.py` resilient when another process holds the DuckDB lock. `[not done]`
- Add XAU bad-day/loss-streak throttle in the EA and parity layer. `[not done]`
- Add market-event blackout configuration for XAU. `[not done]`
- Enrich acks and warehouse rows with fill/reject context and risk/gate decisions. `[not done]`
- Add CI/release gate for MetaEditor compile result and `.ex5` manifest. `[not done]`
- Extend durable deal history beyond the EA's 14-day MT5 export window. `[partial]`

### P1: Research/Promotion Work

- Keep harvesting fresh XAU M1/M5 broker history and bid/ask/spread data. `[partial]`
- Add Parquet partitions by date/symbol/source for warehouse exports. `[not done]`
- Expand event-driven backtest cost model with commission, swap, spread, slippage, and rejects. `[partial]`
- Enforce written champion/challenger promotion decisions before EA parameter changes. `[partial]`
- Add strategy-level risk and PnL dashboard panels for XAU sleeves. `[partial]`
- Add daily XAU parity watch to approved operational cron once scheduling is accepted. `[partial]`

### P2: Governance/Resilience

- Move secrets out of local `.env` before live capital. `[not done]`
- Add signed release artifacts. `[not done]`
- Add restore-tested backup and disaster recovery plan. `[not done]`
- Run FinRobot in a dedicated host or isolated PM2 namespace. `[partial]`
- Write incident playbooks for stale heartbeat, broker outage, spread spike, drawdown breach, bad fill, and restart loop. `[partial]`

## 9. Anti-Recommendations

- Do not add symbols beyond XAUUSD. `[done]`
- Do not restore retired BTC code or BTC optimization without explicit owner reversal. `[done]`
- Do not revive martingale/grid sizing as live alpha. `[done]`
- Do not optimize on the single legacy XAUUSD CSV and call it edge. `[done]`
- Do not let LLM auto-edits touch production without human review. `[done]`
- Do not treat MT5 Common Files as a database; ingest them into the warehouse. `[done]`
- Do not trust Python backtests unless parity against live EA behavior remains green. `[done]`
- Do not store secrets in backup env files, logs, screenshots, or generated MT5 configs. `[done]`
- Do not run real capital from single-host Wine/PM2 without DR, audited releases, and incident process. `[done]`
