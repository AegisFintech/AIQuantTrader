# FinRobot Phase Roadmap — Working TODO

> Continuation doc. Last updated 2026-06-11 ~14:50 SGT by AegisMainBot (Aloy's GM agent).
> Written so we (and any future session) can pick this up cold and know exactly where we are, what's done, what's pending, and what decisions are open.

## How to resume

1. Read this file top-to-bottom.
2. Skim `/root/.openclaw/workspace/MEMORY.md` for agent-level context (operating model, sandbox policy, Aloy's preferences).
3. `cd /root/FinRobot && git log --all --oneline -15` to see the commit chain.
4. Pick the next action from **"Cross-cutting TODOs"** or the **active phase** section below.
5. The live MT5 is running v1.30 (Phase 1 build). Phase 2 M4's EA change is on disk, **not** compiled, **not** deployed.

## Live system snapshot (as of writing)

- **MT5 terminal**: running, online, 14 PM2 restarts, no new trades since v1.30 deploy at 11:56 SGT.
- **EA version on disk**: `broker/mt5/FinRobotBridgeEA.mq5` carries the Phase 1 hardening + risk-fraction rename + the Phase 2 M4 manifest reader. **#property version is still "1.30"** — we did not bump it on disk because version bump is tied to deploy.
- **Live EA in production**: still the compiled v1.30 from the Phase 1 deploy. Phase 2 M4 changes are sitting on disk waiting for compile + restart (Aloy drives both).
- **Equity**: ~S$1,007,024 (broker time, +3h ahead of host clock).
- **Daily PnL**: ~$0–50, no daily loss breach.
- **Broker clock skew**: ICMarkets broker clock is **consistently ~10,800s (3h, UTC+3) ahead of the host**. Surfaces in M2/M3 validators and the new `clock_skew_seconds` metric. Worth a ticket to ICMarkets, not a code bug.
- **Test suite**: 115/115 passing. Was 26 before Phase 1; +66 across Phase 1 + Phase 2 + Phase 3 M1.
- **DuckDB warehouse**: `data/finrobot.duckdb`, 4 tables from Phase 2 (status, positions, deals, acks) + 1 from Phase 3 (prices). 100,000 XAUUSD M1 bars loaded from `data/XAUUSD1.csv`; 2 live status_snapshot rows (XAUUSD + BTCUSD bid/ask).
- **No BTCUSD historical data yet.** Loader prints `[skip] no file found for BTCUSD`. Decision pending (see Open Questions).

## Branch state

```
main              ba07d53  [origin/main] xau to be active throughout the day
phase1/hardening  26c7d85  Phase 1 quick wins
phase2/warehouse  2871af5  Phase 2 M4: release manifest + JSON logging
* phase3/data-layer 9dbfd7f  Phase 3 M1: data layer
```

All three feature branches stack on top of `main`. None merged yet — that's intentional, the merge order is a decision (see below).

## Operating model + policies (do not violate without explicit ask)

- **Tmux is Aloy's interactive space only.** I read it read-only via `capture-pane`. I do not `send-keys` to it. I do not impersonate Aloy in chats with other agents.
- **I drive `codex` and `agy` directly** via non-interactive modes — `codex exec` and `agy -p`.
- **Codex sandbox policy** (Aloy's call, 2026-06-11): `--sandbox danger-full-access --dangerously-bypass-approvals-and-sandbox`. I no longer default to `workspace-write`. Trade-off: codex can pip install, hit the network, write anywhere on disk. Prompt-injection via fetched content is on Aloy; I document prompts.
- **Business constraints live in the prompt, not the OS sandbox**:
  - No `pm2 restart` unless Aloy says go
  - No edits to `AGENTS.md`, `.env*`, `ecosystem.config.js`
  - No `scripts/sync_mt5_ea.sh` against the real `.runtime` without explicit OK
  - No `data/finrobot.duckdb` writes from tests
- **Deploys are Aloy's.** I compile the EA and write the `.ex5`, but Aloy does `pm2 restart mt5-terminal --update-env`.
- **Skills applied** (workspace, /root/.openclaw/workspace/skills/): `antigravity`, `codex`, `coding-agent-codex`. Skill Workshop policy is `auto` so future apply/reject/quarantine doesn't need Aloy to tap a card.

## Phase 1 — MVP hardening (DONE, NOT MERGED, MOSTLY DEPLOYED)

**Branch**: `phase1/hardening` (commits `a9c783b`, `7835ed2`, `26c7d85`)

| Commit | What | Status |
|---|---|---|
| `a9c783b` | `ExecuteCommand` hardening: mandate whitelist, SL required, max-lot enforced, max-pos enforced, daily-loss enforced, magic-filtered `CLOSE`. v1.29 → v1.30. | **DEPLOYED** at 11:56 SGT |
| `7835ed2` | Risk % rename: `DailyRiskPerTradePct=0.0010` → `DailyRiskPerTradeFraction=0.0010`; `DailyLossLimitPct=0.01` → `DailyLossLimitFraction=0.01`; formulas drop the `/100.0`. **Live risk went from 0.001% / 0.01% to 0.10% / 1.00% of equity per trade / daily cap.** | **DEPLOYED** with v1.30 |
| `26c7d85` | Quick wins: `AUTOREVIEW_ENABLE_LLM=false` in `.env.sample`, fix `finrobot/utils/logging_config.py` hardcoded path, `scripts/healthcheck.py`, `scripts/archive_common_files.py`, `config/logrotate-finrobot` + `scripts/install_logrotate.sh`, `docs/RELEASE_CHECKLIST.md`, `tests/test_healthcheck.py` + `tests/test_mt5_trade_report.py`. Rotate `.env.bak-arm-wine` to `.env.bak-arm-wine.removed-20260611`. | Scripts live; logrotate policy **NOT installed** to `/etc/logrotate.d/` yet |

**Open Phase 1 TODOs**:
- [ ] Merge `phase1/hardening` → `main`
- [ ] `sudo scripts/install_logrotate.sh` to start daily rotation of `logs/combined.log`
- [ ] Decide what to do with `.env.bak-arm-wine.removed-20260611` (keep for audit or `trash`)

## Phase 2 — Productionization (DONE, NOT MERGED, NOT DEPLOYED)

**Branch**: `phase2/warehouse` (commits `32c3c1e`, `e903f72`, `8e17697`, `2871af5`)

| Commit | What | Status |
|---|---|---|
| `32c3c1e` | M1: DuckDB warehouse at `data/finrobot.duckdb`. 4 tables (status, positions, deals, acks). `finrobot/data_store.py` + `scripts/mt5_ingest_common_files.py` + 9 tests. | Code live; ingest not on cron |
| `e903f72` | M2: per-row validators + 4 reconciliation checks. **`MAX_FUTURE_SECONDS` = 900s (15 min)** for broker-time tolerance; override via `FINROBOT_VALIDATOR_FUTURE_TOLERANCE_SECONDS`. **Live finding: broker clock is 3h ahead of host.** | Validator not on cron |
| `8e17697` | M3: `MetricsSnapshot` (15+ fields incl. `clock_skew_seconds` via median), 7 alert rules, `data/metrics.json` snapshot. **Live: 2 WARNINGs (clock_skew_large, high_restart_count=14), 0 CRITICAL, exit 0.** | Export not on cron |
| `2871af5` | M4: `finrobot/release_manifest.py` + `state/mt5/RELEASE.json` + `EA_MANIFEST.txt`. EA reads manifest at `OnInit`, writes `ea_version` + `git_sha` to status.json. JSON logging via `setup_logging(json_format=True)` or `JSON_LOGS=1`. **EA source change is on disk but NOT compiled, NOT deployed.** | Compile + deploy pending |

**Open Phase 2 TODOs**:
- [ ] Compile v1.31 (the M4 EA change). I do the MetaEditor compile via `./scripts/sync_mt5_ea.sh`; you drive `pm2 restart`.
- [ ] After M4 deploy, `state/mt5/RELEASE.json` git SHA will start flowing into every `status.json` and every ingested row.
- [ ] Wire cron for the M1/M2/M3 CLIs:
  - [ ] `scripts/mt5_ingest_common_files.py` — every 1-5 min
  - [ ] `scripts/mt5_validate_warehouse.py` — hourly
  - [ ] `scripts/mt5_metrics_export.py` — every 5 min
  - [ ] Wire metrics alerts to something (Slack, email, Telegram bot, anything) — currently the metrics script writes JSON but no consumer is watching
- [ ] Merge `phase2/warehouse` → `main` (or to `phase1/hardening` first if you want stacked merges)
- [ ] **File ICMarkets ticket re: 3h broker clock skew** (UTC+3 vs our UTC+8 host). Not a code bug, but operationally uncomfortable — any future time-based reconciliation against host time will be ambiguous until broker clock is fixed.

## Phase 3 — Strategy Research Platform (PARTIAL)

**Branch**: `phase3/data-layer` (commit `9dbfd7f`)

| M | Goal | Status |
|---|---|---|
| **M1** | Data layer: `prices` table, TSV loader, live bid/ask snapshotter, schema validation | **DONE** (`9dbfd7f`). XAUUSD: 100K M1 bars (2026-01-19 → 2026-05-01). BTCUSD: only live snapshots, no historical. |
| M2 | EA-equivalent event-driven backtester — Python port of MQL5 signal logic, gates, risk, fills; matches live EA on recent decisions | **PENDING** |
| M3 | Purged walk-forward framework + experiment tracking (config / data hash / code hash / metrics / decision) | **PENDING** |
| M4 | Challenger vs incumbent comparison + promotion report | **PENDING** |

**Open Phase 3 TODOs**:
- [ ] Decide: M1.5 (one-shot MQL5 export script to pull historical bars directly from MT5) before M2, or skip and start M2 with what we have?
- [ ] Decide: XAU-only backtest first, or block on BTCUSD data?
- [ ] Merge `phase3/data-layer` → `main` (or to the stack) when you're ready

## Cross-cutting TODOs (not phase-specific)

### Live ops
- [ ] Compile v1.31 (Phase 2 M4 EA change) — see Phase 2 section
- [ ] Merge `phase1/hardening` → `main`
- [ ] Merge `phase2/warehouse` → `main`
- [ ] Merge `phase3/data-layer` → `main`
- [ ] `sudo scripts/install_logrotate.sh`
- [ ] Wire cron for all 5 Phase 2/3 CLIs
- [ ] Wire metrics alerts to a consumer (currently silent)
- [ ] File ICMarkets ticket re: 3h broker clock skew
- [ ] Delete or archive `.env.bak-arm-wine.removed-20260611`

### Documentation
- [ ] AGENTS.md references the new operational scripts (done in Phase 1 quick wins) — but should mention the data layer + Phase 2 metrics/validators
- [ ] QUANT_ROADMAP.md (in `state/mt5/`, uncommitted) is the source-of-truth phase plan from codex's initial audit; keep it updated as phases land

### Open decisions
1. **Phase 3 M1.5**: one-shot MQL5 export script to pull historical bars directly from MT5? Pro: refreshable, broker-grade, unblocks BTC. Con: 30-60 min of work plus an MQL5 deploy.
2. **BTCUSD historical data source**: (a) MQL5 export from live MT5, (b) third-party download, (c) synthetic for testing only, (d) skip BTC in early backtests.
3. **Phase 3 M2 scope**: full MQL5 port (all signals, all gates, all risk) or XAU-only first?
4. **Phase 3 M3 experiment store**: DuckDB table vs JSON files vs SQLite? DuckDB is the obvious choice (already in the stack); JSON is more git-friendly.
5. **Merge order**: stacked merges (phase1 → phase2 → phase3, each merged on top of the previous) or one big "Phase 1+2+3" merge?
6. **Metrics alert consumer**: where do CRITICAL alerts go? Options: write to a log file + email, Telegram bot, PagerDuty, or just print and let cron `|| true` swallow the exit code.

## Phase 4 (PENDING) — Multi-strategy / multi-asset scaling

Goal: support multiple strategy sleeves without increasing operational risk. Per codex's roadmap: strategy registry, per-strategy PnL attribution, portfolio exposure limits, independent kill switches, staged rollout. Stay on XAUUSD/BTCUSD until owner explicitly expands mandate.

Estimated effort: 2-3 months after Phase 3.

## Phase 5 (PENDING) — Institutional-grade

Goal: auditable, resilient, governable. Per codex: WORM-style audit logs, signed releases, secrets manager, least-privilege service accounts, DR environment, independent risk approvals, monthly strategy review, regulatory/tax reporting workflow.

Estimated effort: 3-6+ months.

## Skills + agent context

- **Skills live** (workspace, `/root/.openclaw/workspace/skills/`): `antigravity`, `codex`, `coding-agent-codex`. `skill_workshop.approvalPolicy = "auto"` so future apply/reject/quarantine don't need your tap.
- **Sandbox policy** (saved to `/root/.openclaw/workspace/MEMORY.md`): codex with `--sandbox danger-full-access --dangerously-bypass-approvals-and-sandbox` for FinRobot work.
- **Gitignored operational state**: `data/`, `state/`, `logs/`, `.runtime/`, `.env*`, `*.duckdb`, `*.ex5`. None of these are in source control.

## Open source conversations (in case I forget)

- `skill-wordpress` (Aloy's `agy` session) is mid-task on a 33-50% inference-call reduction for `article_builder.py` plus 5 SEO recommendations. Aloy is driving agy interactively. No code changes applied yet. Not part of FinRobot — separate business (aegiswallet.app).
