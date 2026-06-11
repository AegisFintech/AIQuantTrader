## 1. AUDIT OF CURRENT STATE

- **Live artifact:** [broker/mt5/FinRobotBridgeEA.mq5](/root/FinRobot/broker/mt5/FinRobotBridgeEA.mq5:1), declared v1.29, is the deployed MT5 EA. It trades configured `AutoSymbols="XAUUSD,BTCUSD"`, polls `finrobot_commands.csv`, writes `finrobot_status.json`, `finrobot_positions.csv`, `finrobot_deals.csv`, and `finrobot_acks.csv`.
- **EA modules:** [BridgeIO.mqh](/root/FinRobot/broker/mt5/BridgeIO.mqh:1) handles CSV acks/string cleanup; [RiskManagement.mqh](/root/FinRobot/broker/mt5/RiskManagement.mqh:1) handles day PnL/session/break-even logic; [SmartMoney.mqh](/root/FinRobot/broker/mt5/SmartMoney.mqh:1) implements SMC scoring.
- **Runtime:** [ecosystem.config.js](/root/FinRobot/ecosystem.config.js:1) runs `mt5-terminal` and `autonomous-review` through PM2, logging to `logs/combined.log`. Current `pm2 list` also showed unrelated `skill-odoo-bot` in the same PM2 namespace.
- **Install/startup:** [install.sh](/root/FinRobot/install.sh:1), [scripts/start_mt5.sh](/root/FinRobot/scripts/start_mt5.sh:1), [scripts/sync_mt5_ea.sh](/root/FinRobot/scripts/sync_mt5_ea.sh:1), and [scripts/mt5_configure_profile.py](/root/FinRobot/scripts/mt5_configure_profile.py:1) manage Wine/MT5, EA sync, profile config, and PM2 startup.
- **Live status/reporting:** [scripts/mt5_status.py](/root/FinRobot/scripts/mt5_status.py:1) and [scripts/mt5_trade_report.py](/root/FinRobot/scripts/mt5_trade_report.py:1) read MT5 Common Files. Current report showed fresh heartbeat, `ICMarketsSC-Demo`, zero open managed positions, and zero closed managed deals.
- **Risk controls in place:** daily risk lot sizing, daily loss limit, max positions, same-side cap, cooldown, spread caps, BTC cost filters, BTC H1/PDA gates, XAU PDA gates, SMC gates, dynamic break-even, and partial no-SL/TP protection.
- **Research/data:** [data/XAUUSD1.csv](/root/FinRobot/data/XAUUSD1.csv:1) has 100,000 XAUUSD M1 rows from `2026-01-19 18:51` to `2026-05-01 10:59`. Python research code exists in [finrobot/hft.py](/root/FinRobot/finrobot/hft.py:1), [finrobot/indicators.py](/root/FinRobot/finrobot/indicators.py:1), and `finrobot/strategies/`.
- **Tests:** [tests/](/root/FinRobot/tests/conftest.py:1) contains 6 Python smoke tests. They passed with `.venv/bin/python -B -m pytest -q -s -p no:cacheprovider`. There are no EA, bridge, deployment, or risk-gate tests.
- **Monitoring:** file-based only. No metrics, alerts, SLOs, dashboards, durable ledger, or incident escalation.
- **Security:** `.env` is ignored, but an env-like `/root/FinRobot/.env.bak-arm-wine` exists. I did not open it. `autonomous_review_loop.py` can run Opencode with `--dangerously-skip-permissions`, but current code gates LLM edits unless `AUTOREVIEW_ENABLE_LLM=true`.
- **Article/blog generator:** not present in current tracked files. I found only `.github/workflows/jekyll-gh-pages.yml`.
- **Verdict:** credible live demo prototype. Not production-grade.

## 2. GAP ANALYSIS

**Blocker**

- `ExecuteCommand` in the EA can trade symbols outside the mandate and bypass auto risk sizing, max lot caps, required SL/TP, and daily-risk policy. `CLOSE` is also not magic-filtered like `CLOSE_ALL`.
- No reproducible EA build gate. `sync_mt5_ea.sh` may compile, but there is no CI compile proof, retained compile log, or source-to-`.ex5` release manifest.
- No durable trading ledger. `WriteDealsHistory` exports only a 14-day MT5 history slice to CSV.
- No real alerts for stale heartbeat, PM2 restart loop, unprotected positions, order rejects, or daily drawdown.
- Risk input semantics are ambiguous: `DailyRiskPerTradePct=0.0010` and `DailyLossLimitPct=0.01` are divided by 100 in code, so they mean `0.001%` and `0.01%`, not `0.10%` and `1.00%`.
- PM2 host/process isolation is weak because non-FinRobot processes share the namespace.

**Important**

- No BTC research dataset, no tick/bid/ask data, no broker spread history, no walk-forward framework, no experiment tracker.
- Python strategy code does not reproduce live MQL5 logic, fills, session gates, stops, costs, or broker constraints.
- Logs are human text, not structured operational data.
- Secrets are file-based; no rotation, least privilege, or audit trail.
- `finrobot/utils/logging_config.py` still points to `/home/openclaw/FinRobot/finrobot.log`, conflicting with the active `/root/FinRobot/logs/combined.log`.

**Nice-to-have**

- Dashboard after metrics exist.
- Research notebooks after data warehouse exists.
- Blog/article generation after operations produce reliable research artifacts.
- Portfolio optimizer after multiple validated strategies exist.

## 3. PHASED ROADMAP

### Phase 1: MVP Hardening This Week

**Goal:** make the current demo EA safe, observable, and reproducible enough to continue running.

**Exit criteria:** command path enforces mandate/risk; healthcheck fails loudly; Python tests pass; EA sync/compile status is recorded; `.env.sample` documents safety toggles; runbook exists.

**Key work items:**

- Harden `ExecuteCommand`: whitelist `XAUUSD/BTCUSD`, require SL/TP for market commands, enforce max lot, daily loss, max positions, and magic-safe close.
- Decide percent-vs-fraction semantics for `DailyRiskPerTradePct` and `DailyLossLimitPct`; rename or adjust docs/defaults.
- Add `AUTOREVIEW_ENABLE_LLM=false` to `.env.sample` and README.
- Add `scripts/healthcheck.py`: stale heartbeat, missing Common Files, PM2 offline, unprotected managed positions, daily loss breach.
- Add tests for `mt5_trade_report.py`.
- Fix `finrobot/utils/logging_config.py`.
- Remove/rotate `.env.bak-arm-wine`.
- Archive Common Files snapshots daily.
- Capture EA compile result/log after sync.

**Effort:** 3-5 focused days.

### Phase 2: Productionization

**Goal:** turn the demo runtime into a controlled trading service.

**Exit criteria:** MT5 files ingested to DuckDB/Parquet; alerts exist; CI runs Python tests and MQL compile; deployed EA has version manifest.

**Key work items:**

- Build `scripts/mt5_ingest_common_files.py` and `finrobot/data_store.py`.
- Store raw status/positions/deals/acks with ingestion time, broker time, EA version, git SHA, symbol, bid/ask/spread, schema version.
- Add schema validation and reconciliation checks.
- Add JSON structured logs and log rotation.
- Add metrics exporter and alerts.
- Run FinRobot in a dedicated PM2 namespace or host.
- Release `.mq5`, `.mqh`, `.ex5`, config, and git SHA together.

**Effort:** 2-4 weeks.

### Phase 3: Strategy Research Platform

**Goal:** stop hand-tuning live MQL inputs and build a proper research loop.

**Exit criteria:** event-driven backtester matches EA assumptions; walk-forward validation is standard; costs use broker spreads/commission/swap; every experiment has immutable inputs and promotion decision.

**Key work items:**

- Ingest BTCUSD and XAUUSD broker bid/ask/tick or M1 bid/ask data.
- Implement an EA-equivalent simulator for current MQL gates, stops, sizing, spread filters, sessions, and fills.
- Add purged walk-forward splits and leakage checks.
- Track experiment configs, data hashes, code hashes, metrics, and decisions.
- Compare challenger strategies against current EA before live changes.

**Effort:** 4-8 weeks.

### Phase 4: Multi-Strategy / Multi-Asset Scaling

**Goal:** support multiple strategy sleeves without increasing operational risk.

**Exit criteria:** strategy registry, per-strategy PnL attribution, portfolio exposure limits, independent kill switches, staged rollout. Stay on XAUUSD/BTCUSD until owner explicitly expands mandate.

**Key work items:**

- Add `strategies/registry.yaml`.
- Tag every order with `strategy_id` and version.
- Build portfolio risk aggregator.
- Separate signal generation from execution.
- Add champion/challenger deployment and capital allocation rules.

**Effort:** 2-3 months after Phase 3.

### Phase 5: Institutional-Grade

**Goal:** make the operation auditable, resilient, and governable.

**Exit criteria:** compliance-ready audit trail, access control, BCP/DR, tested restore, model-risk review, incident process, legal review.

**Key work items:**

- WORM-style audit logs.
- Signed releases.
- Secrets manager.
- Least-privilege service accounts.
- Disaster recovery environment.
- Independent risk approvals.
- Monthly strategy review.
- Regulatory/tax reporting workflow before real or client capital.

**Effort:** 3-6+ months.

## 4. SPECIFIC RECOMMENDATIONS

- **Data pipeline:** use DuckDB locally first, Parquet partitions by date/symbol. Ingest Common Files into durable raw tables before doing analysis.
- **Data validation:** reject duplicate deals, impossible prices, stale timestamps, missing SL/TP, negative volume, unknown symbols, schema drift.
- **Feature store:** keep it simple: `finrobot/features.py` generates reproducible features from warehouse tables. Persist only promoted features.
- **Backtesting:** keep vectorized scans for discovery, but promotion requires event-driven simulation matching MT5/EA behavior.
- **Leakage prevention:** forbid current-bar future leakage in research. Add purged walk-forward validation and embargo windows.
- **Cost model:** use broker-specific bid/ask spreads, commission, swap, slippage, stop execution, and reject rates.
- **Execution layer:** treat MT5 EA as broker adapter. Build a small OMS around command ID, intent, quote snapshot, risk approval, ack, retry, reconciliation.
- **Fill analysis:** extend acks with requested price, observed bid/ask, result price, spread, retcode, latency bucket, strategy ID, risk decision.
- **Risk:** apply identical pre-trade checks to auto and command trades: whitelist, max lot/notional, max positions, SL/TP, daily loss, margin, spread, kill switch.
- **Kill switches:** add file/env kill switch checked every timer tick; block new orders and optionally flatten managed positions.
- **Monitoring:** alert on heartbeat age, PM2 status, Common Files missing, open positions without stops, repeated rejects, daily loss, high spread, no telemetry movement.
- **Deployment:** add CI for Python tests/lint and MQL compile. Release by git tag with artifact manifest.
- **Security:** delete or secure `.env.bak-arm-wine`; move secrets to a real secrets manager before live capital.
- **MLOps:** define research -> paper -> staging -> demo prod -> live -> retirement. Use champion/challenger with written promotion criteria.
- **Compliance:** before live capital, document jurisdiction, account ownership, leverage/margin rules, audit retention, and reporting obligations.
- **Team/process:** require review for every EA/risk/deploy change; maintain runbooks; write post-mortems for drawdown, bad fill, outage, restart loop.

## 5. QUICK WINS <= 1 DAY EACH

1. Harden `ExecuteCommand` in `broker/mt5/FinRobotBridgeEA.mq5`.
2. Add `AUTOREVIEW_ENABLE_LLM=false` to `.env.sample` and docs.
3. Fix `finrobot/utils/logging_config.py` hardcoded `/home/openclaw` path.
4. Add unit tests for `scripts/mt5_trade_report.py`.
5. Add `scripts/healthcheck.py`.
6. Decide and document risk percent semantics.
7. Remove/rotate `.env.bak-arm-wine`.
8. Add PM2/logrotate policy for `logs/combined.log`.
9. Archive Common Files daily under `state/mt5/archive/YYYY-MM-DD/`.
10. Add release checklist: `git status`, `mt5_trade_report`, tests, EA sync, compile result.

## 6. MEDIUM-TERM PROJECTS 1-4 WEEKS EACH

1. MT5 warehouse ingestion from Common Files to DuckDB/Parquet.
2. MQL build pipeline with CI compile logs/artifacts.
3. Broker data collector for XAUUSD/BTCUSD bid/ask/spread history.
4. EA-equivalent backtester.
5. Metrics exporter, alert rules, and minimal dashboard.
6. Durable OMS command protocol.
7. Versioned strategy registry.
8. Paper/staging challenger workflow.
9. Secrets and PM2 namespace hardening.
10. Runbooks for startup, restart, kill switch, broker outage, stale status, rollback.

## 7. ANTI-RECOMMENDATIONS

- Do not add more symbols now.
- Do not revive martingale/grid sizing as live alpha.
- Do not optimize on the single XAUUSD CSV and call it edge.
- Do not let LLM auto-edits touch production without human review.
- Do not build dashboards before metrics and alerts.
- Do not treat MT5 Common Files as a database.
- Do not run real capital from single-host Wine/PM2 without DR.
- Do not trust Python backtests unless they match the EA exactly.
- Do not store secrets in backup env files, logs, screenshots, or generated MT5 configs.
- Do not scale to multi-asset before data quality, portfolio risk, and execution reconciliation are solved.

I attempted to write `/root/FinRobot/QUANT_ROADMAP.md` with `apply_patch`, but this session’s filesystem is read-only and the sandbox rejected the write.