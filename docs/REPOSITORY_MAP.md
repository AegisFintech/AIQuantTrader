# AIQuantTrader Repository Map

Last verified: 2026-08-05

Use this document as the first navigation aid for repo work. It identifies the
active runtime, code ownership boundaries, data flows, and known validation
gaps. It is a map, not a substitute for reading the exact files being changed.

## Authority and Scope

The repository has two explicitly separate states:

- **Deployed legacy state:** MT5 demo trading for `XAUUSD` only. The existing
  MQL5, Wine, PM2, Common Files, and XAU instructions remain authoritative for
  that runtime until the separately approved Phase 10 retirement completes.
- **Approved target state:** Linux-native trading of
  `BTC-USD-PERP.HYPERLIQUID` through NautilusTrader and Hyperliquid. Target
  approval does not authorize mainnet trading or automatic production
  promotion.

Legacy authority order:

1. `AGENTS.md` for owner directives and change rules.
2. `broker/mt5/AIQuantTraderBridgeEA.mq5` and its three `.mqh` modules for live
   trading behavior.
3. Current MT5 Common Files plus `scripts/mt5_trade_report.py` for deployed
   state and realized performance.
4. `ecosystem.config.js` for active services.
5. Python under `aiquanttrader/backtest/`, `aiquanttrader/xau_profiles.py`, and
   `scripts/xau_strategy_lab.py` for the promotion research path.

`CLAUDE.md` and `QUANT_ROADMAP.md` contain useful historical analysis, but they
are point-in-time documents and are not runtime truth.

Native migration authority order:

1. `AGENTS.md` for migration and production-approval rules.
2. `docs/architecture/TARGET_ARCHITECTURE.md` and the accepted ADRs.
3. `docs/migration/PHASE_ACCEPTANCE_GATES.md` for completion criteria.
4. `docs/operations/NATIVE_PLATFORM_THREAT_MODEL.md` and
   `docs/operations/NATIVE_RELEASE_CHECKLIST.md` for security and release.
5. Phase implementation and runbooks after they are merged.

## Native Migration Topology

Phases 2-10 implement the native contracts, public BTC data, fail-closed testnet
execution, causal replay/validation, research strategy paths, and
credential-free paper under `native/` and `rust/`.
Phase 3 connects only to public market data. Phase 4 is the only native path
that can submit testnet orders; Phase 5 has no exchange credential. The runtime
ownership boundaries are:

```text
Hyperliquid public/private APIs
|- Nautilus trading node
|  `- features -> strategies -> synchronous risk -> execution
|- independent raw market-data recorder
|  `- raw segments -> validated Parquet -> Nautilus catalog
`- independent SDK safety sentinel
   `- dead-man renewal and emergency cancellation only

Tardis + local capture
`- research workers -> HftBacktest + Nautilus parity
   `- experiment registry -> paper -> shadow -> human approval -> canary

Prometheus -> Grafana + Alertmanager
DuckDB -> manifests, experiments, deployments, and offline analytics only
```

See `docs/architecture/diagrams/` for the system, live-order, and promotion
flows. The native hot path must not depend on DuckDB, Parquet, Grafana, or a
remote message broker.

Current native foundation ownership:

| Path | Responsibility |
|---|---|
| `native/src/aiquanttrader_native/config/` | Fail-closed environment configuration and immutable fingerprints. |
| `native/src/aiquanttrader_native/domain/` | Versioned market, feature, experiment, approval, and promotion contracts. |
| `native/schemas/` | Deterministic JSON Schemas checked against the Python contracts in CI. |
| `native/configs/` | Non-secret environment overlays; every checked-in overlay disables execution. |
| `native/Dockerfile`, `native/compose.yaml` | Non-root, read-only-compatible foundation container. |
| `rust/` | Pinned performance boundary; a Cargo workspace requires benchmark evidence and a real first crate. |
| `.github/workflows/native-ci.yml` | Native lock, lint, type, test, schema, dependency, secret, Rust, and image gates. |

Current Phase 3 market-data ownership:

| Path | Responsibility |
|---|---|
| `native/src/aiquanttrader_native/market_data/recorder.py` | Raw-first public Hyperliquid WebSocket capture, reconnect, stale-feed and disk guards. |
| `native/src/aiquanttrader_native/market_data/raw.py` | Framed Zstandard segments, exact payload hashes, footer verification, incomplete recovery. |
| `native/src/aiquanttrader_native/market_data/protocol.py` | Strict BTC public and account-event normalization without invented liquidation coverage. |
| `native/src/aiquanttrader_native/market_data/storage.py` | Deterministic typed Parquet and research dataset quality admission. |
| `native/src/aiquanttrader_native/market_data/normalizer.py` | Independent pending-segment normalization and quarantine worker. |
| `native/src/aiquanttrader_native/market_data/tardis.py` | Immutable Tardis gzip acquisition and dataset-specific CSV validation. |
| `native/src/aiquanttrader_native/market_data/catalog.py` | Process-exclusive DuckDB manifest catalogs; never in the frame hot path. |
| `native/observability/` | Recorder Prometheus scrape and initial Grafana dashboard provisioning. |
| `docs/operations/MARKET_DATA_RUNBOOK.md` | Start, health, recovery, Tardis, dataset, soak, incident, and rollback procedures. |

The Phase 3 code gates are implemented. Phase 3 must not be marked accepted
until the runbook's sustained public-feed soak finishes and every observed gap
is classified.

Current Phase 4/6 live strategy and execution ownership:

| Path | Responsibility |
|---|---|
| `native/src/aiquanttrader_native/execution/{artifacts,live}.py` | Strict live artifact loading, causal managed-L2 normalization, shared feature/strategy coordination, and restart-safe equity baselines. |
| `native/src/aiquanttrader_native/execution/strategy.py` | Sole Nautilus order owner; cache-derived risk snapshots, cancel-confirm replacement, terminal memory release, and lifecycle journaling. |
| `native/src/aiquanttrader_native/execution/node.py` | Pinned Hyperliquid node, live-pipeline construction, and synchronous client-connectivity probe. |
| `native/src/aiquanttrader_native/risk/` | Unbypassable synchronous limits, single-use approvals, and persistent operator kill. |
| `native/src/aiquanttrader_native/sentinel/` | Independently credentialed dead-man renewal and emergency cancel-only process. |
| `native/src/aiquanttrader_native/acceptance/` | Credential-free, deterministic assembly and verification of stopped, hash-bound testnet rehearsal evidence. |
| `native/configs/base.toml` (`live_strategy`) | Disabled exact feature/strategy selection and bounded fee/slippage assumptions. |
| `docs/operations/EXECUTION_RISK_RUNBOOK.md` | Credentialed testnet matrix, live-pipeline checks, incidents, and rollback. |

Phase 4/6 code convergence and offline acceptance assembly are automated, but
execution remains disabled in all checked-in environments. The trading node
and sentinel durably emit separate hash-linked operational streams; the
assembler derives locally provable journal facts and rejects incomplete,
mutable, or undeclared evidence. Acceptance still requires real testnet lifecycle,
latency, restart, stale/disconnect, unknown-outcome, dead-man, economic-baseline,
and strategy evidence. Mainnet additionally consumes the signed bundle's exact
strategy artifact; this does not create or activate an approval.

Current Phase 5 backtesting ownership:

| Path | Responsibility |
|---|---|
| `native/src/aiquanttrader_native/backtest/conversion.py` | Manifest-verified Tardis/admitted-Parquet conversion and byte-deterministic HftBacktest NPZ lineage. |
| `native/src/aiquanttrader_native/backtest/scenarios.py` | Strict scenario loading, liquidity stress, and pinned HftBacktest asset construction. |
| `native/src/aiquanttrader_native/backtest/replay.py` | Queue/latency/partial-fill replay plus fee, explicit slippage, mark, and hourly funding accounting. |
| `native/src/aiquanttrader_native/backtest/kernel.py` | Pure decision-kernel contract and Hft local-arrival/Nautilus object parity adapters. |
| `native/src/aiquanttrader_native/backtest/validation.py` | Purged walk-forward plans, validation-only selection receipts, and final-holdout authorization. |
| `native/src/aiquanttrader_native/backtest/statistics.py` | Seeded block-bootstrap intervals and multiple-selection lower bounds. |
| `native/configs/backtest/` | Versioned baseline, pessimistic, and walk-forward policies. Seed scenarios are uncalibrated and not promotable. |
| `docs/operations/BACKTESTING_RUNBOOK.md` | Conversion, calibration, replay, holdout, evidence, failure, and rollback procedures. |

Phase 5 implementation gates are automated. Acceptance still requires reviewed
calibration artifacts, full-dataset scenario reports, and evidence that
selection was frozen before final holdout access. Phase 6 now supplies automated
feature and production-strategy-kernel parity coverage.

Current Phase 6 feature, strategy, and research ownership:

| Path | Responsibility |
|---|---|
| `native/src/aiquanttrader_native/features/` | Causal bounded microstructure features and deterministic feature-dataset lineage. |
| `native/src/aiquanttrader_native/strategies/` | Pure Avellaneda-Stoikov and order-flow scalper decision kernels. |
| `native/src/aiquanttrader_native/research/model_adapters.py` | CPU-only LightGBM, XGBoost, and CatBoost training/prediction in safe native formats. |
| `native/src/aiquanttrader_native/research/search.py` | Bounded validation-only fold search and seeded negative controls. |
| `native/src/aiquanttrader_native/research/{artifacts,registry,governance,drift}.py` | Hash-bound model artifacts, immutable experiment history, promotion gates, and feature drift. |
| `native/configs/{features,strategies,research}/` | Versioned research seeds and frozen gate/search inputs. |
| `native/observability/grafana/dashboards/research.json` | Bounded research, drift, training, and governance telemetry views. |
| `docs/operations/RESEARCH_RUNBOOK.md` | Feature replay, model search, controls, registry, promotion review, backup, and rollback. |

Phase 6 implementation gates are automated. Acceptance still requires retained
multi-regime studies, reviewed fill/queue/arrival/latency/cost calibration,
strategy-risk load evidence, and a registry backup/restore drill. Its strategy
kernels now feed the native node only through the sole Phase 4 risk gateway;
the automation CLI still has no human approval capability.

Current Phase 7 paper ownership:

| Path | Responsibility |
|---|---|
| `native/src/aiquanttrader_native/paper/{market,engine,simulator}.py` | Live normalized-event assembly, exact production strategy/risk orchestration, and deterministic market-by-price fills. |
| `native/src/aiquanttrader_native/paper/{journal,evidence,drift}.py` | Transactional restart/account evidence, frozen paper gates, sensitivity binding, and bounded online drift. |
| `native/src/aiquanttrader_native/paper/{service,cli,metrics}.py` | Raw-first live lifecycle, watchdog/kill/status operations, evidence CLI, and Prometheus. |
| `native/configs/paper/` | Conservative live-paper scenarios plus the frozen sample/economic/drift/drill policy. |
| `native/compose.yaml` (`paper-trader`) | Non-root, read-only, public-only paper service with no secret mount. |
| `native/observability/grafana/dashboards/paper-trading.json` | Feed, readiness, PnL, inventory, risk, fill, latency, markout, and drift panels. |
| `docs/operations/PAPER_TRADING_RUNBOOK.md` | Credential proof, launch, drills, calibration, sensitivity, evidence, incident, and rollback. |

Phase 7 implementation gates are automated. Acceptance remains pending because
the checked-in execution scenarios are uncalibrated and no retained paper run
has yet met the frozen samples, regimes, sensitivity, drills, drift, economics,
and recommended observation window. Paper mode rejects every exchange account
and wallet reference and cannot instantiate an exchange execution client.

Current Phase 8 shadow ownership:

| Path | Responsibility |
|---|---|
| `native/src/aiquanttrader_native/shadow/{gateway,ingress}.py` | Raw-first public-only gateway and durable checksummed one-way ingress. |
| `native/src/aiquanttrader_native/shadow/{service,sink,security}.py` | No-network production kernel/risk runner, atomic counterfactual command boundary, and route proof. |
| `native/src/aiquanttrader_native/shadow/{audit,evidence}.py` | Availability/latency/fault evidence, deterministic replay comparison, and frozen human-gated report. |
| `native/src/aiquanttrader_native/shadow/{metrics,observer,cli}.py` | Atomic metrics handoff, read-only observer, lifecycle, kill, replay, compare, drill, and evidence operations. |
| `native/compose.shadow.yaml` | Exact-image gateway/engine/observer topology; engine uses `network_mode: none` and read-only ingress. |
| `docs/operations/SHADOW_DEPLOYMENT_RUNBOOK.md` | Production-host launch, isolation proof, drills, replay, evidence, incident, and rollback. |

Phase 8 implementation gates are automated. Acceptance remains pending because
the checked-in execution scenarios are uncalibrated and no intended-host
shadow run has yet passed seven days, samples/regimes, exact replay,
availability/latency/economics/drift, and every retained fault drill. A passing
report can only enter `AWAITING_APPROVAL`.

Current Phase 9 production-admission ownership:

| Path | Responsibility |
|---|---|
| `native/src/aiquanttrader_native/governance/` | Ed25519 artifact verification, explicit anti-replay admission, wallet/account/capital binding, and frozen canary evidence. |
| `native/src/aiquanttrader_native/governance/{approval,ledger}.py` | Chained seven-day production renewals which preserve the original admission and immutable release identity; schema-v1 ledgers migrate without an expiry extension. |
| `native/compose.mainnet.yaml` | Exact-image controller/trading/sentinel topology with separated wallet mounts. |
| `native/compose.rehearsal.yaml` | Explicit exact-image testnet dress rehearsal with separated testnet wallet mounts and release identity metadata. |
| `native/configs/production/` | Frozen production-admission evidence policies; never credentials or enabled execution. |
| `native/observability/grafana/dashboards/production-governance.json` | Admission, expiry, capital, denial, and emergency-cancel views. |
| `docs/operations/MAINNET_CANARY_RUNBOOK.md` | Two-person preflight, verify/admit, canary drills, evidence, incident, and scale procedure. |

Phase 9 code gates do not establish empirical acceptance. No signed release,
mainnet funding/order, or production scale is performed by the repository
implementation.

Production authority no longer becomes structurally terminal after its first
seven-day approval. A detached Ed25519 renewal must bind the current
authorization and the unchanged deployment, admission, account/vault,
artifacts, configuration, image, and capital before the unexpired schema-v2
ledger atomically extends authority. Runtime startup may re-verify an expired
original approval only when the same durable admission has a current chained
authorization. Renewal cannot revive expiry or modify a release. See
`docs/migration/PHASE_9_PRODUCTION_RENEWAL.md`.
Schema-v1 records migrate without extending time but cannot renew because the
old ledger did not retain the admitted trust-root fingerprint; they require a
fresh release/admission sequence.

The Phase 9 release-evidence increment adds typed final-testnet observations,
the frozen complete scenario evaluator, exact target-behavior fingerprints,
and deterministic unsigned bundle preparation. The Phase 4 acceptance
assembler now creates those observations from retained evidence without
network, wallet, signer, evaluation, or admission capability.
`governance/bundle.py` rejects
incompatible artifacts and evidence before offline signing, binds the live
strategy identity into target behavior, and verifies that the image-resident
feature configuration matches shadow evidence; it contains no signer or
admission action. See
`docs/migration/PHASE_9_RELEASE_EVIDENCE.md` and
`docs/migration/PHASE_4_TESTNET_ACCEPTANCE_EVIDENCE.md`.

Current Phase 10 legacy-retirement ownership:

| Path | Responsibility |
|---|---|
| `native/src/aiquanttrader_native/retirement/collector.py` | Credential-free exact-inventory reconstruction of signed production authority, renewal continuity, audits, incidents, drills, and native observation. |
| `native/src/aiquanttrader_native/retirement/archive.py` | Credential-free exact-inventory assembly and independent replay of the final legacy archive, restore proof, recursive scan evidence, and annotated-tag lineage. |
| `native/src/aiquanttrader_native/retirement/final_state.py` | Credential-free reconstruction and replay of final MT5/broker/service state from raw evidence embedded in the verified archive. |
| `native/src/aiquanttrader_native/retirement/readiness.py` | Cross-bundle assembly and replay of the native/archive/final-state readiness observation with completion-time authority and freshness checks. |
| `native/src/aiquanttrader_native/retirement/{models,evidence,approval}.py` | Immutable final-archive, terminal native-authorization observation, flat-MT5-state, disabled-window, scoped approval, and exact cleanup-manifest contracts. |
| `native/configs/retirement/evidence-v1.toml` | Frozen 30-day native, five-minute operational-evidence gap and final-state skew, one-hour final-state age, seven-day disabled, 365-day archive retention, and credential-scan identity. |
| `native/configs/retirement/archive-credential-scan-v1.toml` | Frozen recursive detector and zero-finding contract for credential-free legacy archives. |
| `native/schemas/retirement.schema.json` | Deterministic external contract for every Phase 10 evidence and approval record. |
| `docs/migration/PHASE_10_LEGACY_RETIREMENT.md` | Two-approval architecture, repository delta, tests, migration, and rollback. |
| `docs/migration/PHASE_10_PRODUCTION_EVIDENCE.md` | Native bundle layout, trust boundary, independent assembly, verification, tests, and rollback. |
| `docs/migration/PHASE_10_LEGACY_ARCHIVE.md` | Exact archive bundle, restore, recursive scan, retention, annotated-tag, assembly, and replay procedure. |
| `docs/migration/PHASE_10_FINAL_STATE.md` | Raw source packaging, MT5/broker reconciliation, writer inventory, freshness, final-state assembly, and replay procedure. |
| `docs/migration/PHASE_10_READINESS_ASSEMBLY.md` | Native/legacy identity binding, dual-root replay, readiness commands, timing boundary, tests, and rollback. |
| `docs/operations/LEGACY_RETIREMENT_RUNBOOK.md` | Final archive, exact disable, observation, cleanup approval, host cleanup, removal PR, and failure procedure. |

Phase 10 code is evidence-only. `aqt-retirement` has no command or dependency
that can stop services, touch brokers/exchanges, revoke credentials, remove
packages, or delete files. No readiness/disabled evidence or action approval
has been created, `mt5-final` has not been tagged, and the active MT5 runtime is
unchanged.
The 30-day native observation must retain the ordered renewal chain and end
before the terminal production authorization expires; an authorization gap
invalidates the observation window.
It must be independently reassembled from the retained exact-inventory bundle
using the externally pinned signer identity. Successful sentinel dead-man
samples must span the interval without a gap over five minutes; the assembler
derives typed risk/reconciliation/critical counts rather than accepting them as
operator-authored totals.
The final legacy archive must likewise be independently assembled and replayed
from all eleven category artifacts, isolated-restore evidence, a policy-pinned
recursive zero-finding credential scan, and annotated `mt5-final` tag evidence.
Its schema-v2 manifest binds those control artifacts and their source lineage;
it does not prove final broker flatness or authorize a stop.
Final account state must then be independently derived from raw report, broker,
status, pause, and writer evidence embedded in that verified archive. The
schema-v2 state binds the frozen policy and archive provenance, derives all
counts, and expires after the policy freshness window; it does not act on MT5.
The schema-v3 native observation now carries the same retirement identity.
Readiness assembly reverifies both immutable roots, rejects cross-retirement
substitution, and timestamps only after replay; readiness evaluation repeats
that replay before it can emit an approval-facing report.

## Current Legacy System Topology

```text
PM2
|- aiquanttrader-mt5
|  `- scripts/start_mt5.sh
|     `- MT5 under Wine/Xvfb
|        `- AIQuantTraderBridgeEA.ex5 (XAUUSD timer-driven trading)
|- aiquanttrader-watchdog
|  `- scripts/mt5_watchdog.py (heartbeat check and terminal restart only)
|- aiquanttrader-review
|  `- scripts/autonomous_review_loop.py
|     |- scripts/mt5_trade_report.py
|     `- scripts/xau_strategy_lab.py (analysis by default)
`- aiquanttrader-dashboard
   `- dashboard/app.py (read-only Streamlit UI on 127.0.0.1:8501)

AIQuantTraderBridgeEA.ex5
|- reads optional commands, strategy profile, and blackout CSV files
|- writes status, positions, deals, and acknowledgement files
`- MT5 Common Files
   |- report/status/dashboard readers
   `- cron ingestion -> data/aiquanttrader.duckdb -> research/metrics/validation
```

There is no active Python order executor. Automatic orders originate inside the
MQL5 EA. The dashboard is read-only. No PM2 process currently writes
`aiquanttrader_commands.csv`.

## Parallel Linux-native migration

The `native/` tree is an isolated, Docker-managed BTC perpetual replacement
under construction. It is not part of the active PM2/MT5 runtime. Phases 2-10
currently provide:

| Area | Native ownership |
|---|---|
| Deployment policy | `native/src/aiquanttrader_native/{config,governance}/`; checked configs remain disabled, while runtime mainnet requires exact signed artifacts plus explicit durable admission. |
| Public market data | `native/src/aiquanttrader_native/market_data/`; independent raw-first Hyperliquid recorder and normalizer. |
| Hard risk | `native/src/aiquanttrader_native/risk/`; synchronous approval authority and persistent operator kill. |
| Normal execution | `native/src/aiquanttrader_native/execution/`; shared live features/kernels feed the sole cache-derived hard-risk gateway, and only `RiskManagedExecutionStrategy` may call Nautilus order APIs. |
| Emergency control | `native/src/aiquanttrader_native/sentinel/`; independent SDK control wallet, dead-man renewal, and cancel-all only. |
| Testnet deployment | `native/compose.testnet.yaml`; trading and control secrets are mounted into different processes. |
| Final release rehearsal | `native/compose.rehearsal.yaml`; exact digest and target behavior are exercised on testnet before unsigned bundle preparation. |
| Backtesting | `native/src/aiquanttrader_native/backtest/`; causal HftBacktest replay, Nautilus-object parity, scenario stress, and guarded validation. |
| BTC research | `native/src/aiquanttrader_native/{features,strategies,research}/`; causal features, pure strategy kernels, native tabular models, bounded search, controls, drift, and an automation-ceiling registry. |
| Paper trading | `native/src/aiquanttrader_native/paper/`; live public data through production kernels/risk into a credential-free simulator and immutable evidence journal. |
| Shadow deployment | `native/src/aiquanttrader_native/shadow/`; public gateway into a no-network decision engine, recorded-only commands, replay comparison, and human-gated evidence. |
| Mainnet admission | `native/compose.mainnet.yaml`; exact-image controller, trading, and sentinel processes independently verify signed authority and the durable ledger. |
| Legacy retirement | `native/src/aiquanttrader_native/retirement/`; credential-free two-gate evidence and approval verification with no operational action capability. |

Phase 4 code is implemented but is not accepted until the credentialed testnet
scenario matrix and kill/dead-man drills in
`docs/operations/EXECUTION_RISK_RUNBOOK.md` are retained and reviewed. Phase 9
adds a structurally gated mainnet path, but no native mainnet order path is
authorized until all preceding evidence and the exact signed admission are
separately reviewed and activated.
Native work must not modify or restart the MT5 runtime unless the owner
separately requests it.

## Live Trading Path

### MQL5 ownership

| File | Live responsibility |
|---|---|
| `broker/mt5/AIQuantTraderBridgeEA.mq5` | EA inputs, timer lifecycle, runtime profile parser, command execution, auto signals, order placement, status/position/deal exports. |
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
`aiquanttrader/xau_profiles.py`.

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
| `aiquanttrader_status.json` | EA writes | Heartbeat, account, deployed version/SHA, profile, money management, quotes, signal counters. | watchdog, status, healthcheck, dashboard, ingestion, metrics |
| `aiquanttrader_positions.csv` | EA writes | Current magic-number managed positions. | report, healthcheck, dashboard, ingestion |
| `aiquanttrader_deals.csv` | EA writes | Rolling 14-day magic-number deal history. | report, dashboard, ingestion, parity tooling |
| `aiquanttrader_acks.csv` | EA appends | Command and automatic fill/rejection events. | report, dashboard, ingestion, parity tooling |
| `aiquanttrader_commands.csv` | EA reads/deletes | Optional external `MARKET`, `CLOSE`, and `CLOSE_ALL` requests. | no active writer |
| `aiquanttrader_strategy_profile.csv` | EA reads | Optional bounded key/value runtime overrides. | strategy lab may write only through gated deployment |
| `aiquanttrader_blackout.csv` | EA reads | Optional broker-time start/end/reason blackout windows. | operator-managed; only active when profile enables it |
| `aiquanttrader_entry_pause.flag` | EA reads | Operator-controlled pause for all new automatic and command-file market entries. | `scripts/mt5_entry_pause.py`; close actions and position management remain active |
| `aiquanttrader_export_XAUUSD_M1.tsv` | EA writes | Bounded periodic M1 bar export for fresh research. | autonomous review harvest and price loader |
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
| EA sync/release | `scripts/sync_mt5_ea.sh`, `aiquanttrader/release_manifest.py`, `scripts/release_manifest.py` | Sync regenerates release manifests, copies source, and invokes MetaEditor when present. Compile output still requires inspection before restart. |
| Health/recovery | `scripts/mt5_status.py`, `scripts/healthcheck.py`, `scripts/mt5_watchdog.py`, `scripts/mt5_entry_pause.py` | Healthcheck covers runtime, disk, research freshness, and all PM2 services. Watchdog remains heartbeat-only. The pause CLI manages the persistent no-new-entry flag. |
| Reporting | `scripts/mt5_trade_report.py`, `dashboard/app.py` | Current Common Files are the input. Strategy attribution comes from deal comments. |
| Scheduled operations | `scripts/mt5_minute_cycle.py`, `config/aiquanttrader.cron` | Common Files ingestion and bid/ask capture run sequentially; cron serializes all DuckDB jobs with a shared file lock. |
| Archive/log policy | `scripts/archive_common_files.py`, `config/logrotate-aiquanttrader`, `scripts/install_logrotate.sh` | Archives go under ignored `state/`; combined, cron, and alert logs rotate daily. |
| Reverse proxy | `config/nginx-trading.aims-sg.com.conf` | Proxies the read-only dashboard. |

Optional cron jobs in `config/aiquanttrader.cron` ingest bridge snapshots and quotes
every minute, export metrics every five minutes, validate hourly, and archive
daily. The cron configuration writes `logs/cron.log`; it is separate from the
four active PM2 processes and must be installed explicitly.

## Python Data and Research Path

### Warehouse and observability

| File | Responsibility |
|---|---|
| `aiquanttrader/data_store.py` | DuckDB schema and ingestion/query functions for status, positions, deals, acks, prices, and experiments. |
| `scripts/mt5_ingest_common_files.py` | Snapshot Common Files into DuckDB, preferring deployed status metadata. |
| `scripts/mt5_snapshot_prices.py` | Store live bid/ask/spread observations. |
| `scripts/load_historical_prices.py` | Load normalized historical bar CSVs. |
| `scripts/harvest_mt5_export.py` | Discover/copy MT5 exports and invoke the loader. |
| `aiquanttrader/validators.py`, `scripts/mt5_validate_warehouse.py` | Warehouse schema, freshness, reconciliation, and risk validation. |
| `aiquanttrader/metrics.py`, `aiquanttrader/alerts.py`, `aiquanttrader/alert_delivery.py` | Metrics snapshots, alert evaluation, and transition delivery. |

The tracked historical input is `data/XAUUSD1.csv`; a second local
`data/XAUUSD_M1.csv` is currently available but ignored. The local DuckDB
warehouse is also ignored by git even when a working copy exists.

### Deterministic backtester

| Module | Responsibility |
|---|---|
| `aiquanttrader/backtest/engine.py` | Bar loop, signal handling, positions, SL/TP exits, break-even, recovery gates, and trade ledger. |
| `aiquanttrader/backtest/position.py` | Position model, `PositionSizer`, and `DailyRiskSizer`. |
| `aiquanttrader/backtest/fills.py` | Deterministic point-size-aware spread/slippage/commission/swap fill assumptions. |
| `aiquanttrader/backtest/instruments.py` | Broker-calibrated point, tick-value, spread, and commission specifications used by XAU research. |
| `aiquanttrader/backtest/metrics.py` | PnL, drawdown, Sharpe/Sortino/Calmar, expectancy, loss streak, and distribution metrics. |
| `aiquanttrader/backtest/walkforward.py` | Purged and embargoed walk-forward folds plus stability aggregation. |
| `aiquanttrader/backtest/reporter.py` | Machine-readable and Markdown reports with verdicts and attribution. |
| `aiquanttrader/backtest/parity.py`, `parity_replay.py` | Compare Python decisions with EA acknowledgements. |
| `aiquanttrader/backtest/strategies/_xau_state.py` | Rolling M1-to-forming-M5 indicator state used to approximate the EA timer path. |
| `aiquanttrader/backtest/strategies/xau_gates.py` | Python port of the live XAU indicator/PDA/SMC gates. |
| `aiquanttrader/backtest/strategies/xau_atr_impulse.py` | Live ATR impulse strategy slice. |
| `aiquanttrader/backtest/strategies/xau_gated.py` | PDA/SMC/ADX/cooldown/blackout wrapper. |
| `aiquanttrader/backtest/strategies/xau_quick_momentum.py` | Quick-momentum parity/research slice. |

`buy_and_hold.py`, `stub_replay.py`, `xau_mean_reversion.py`,
`xau_ml_ensemble.py`, and `xau_seasonal.py` are offline sleeves or test helpers;
they do not place live orders.

### Profile lab and promotion

`aiquanttrader/xau_profiles.py` owns the compiled-equivalent incumbent and four
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
| `aiquanttrader/research/experiments.py`, `registry.py`, `comparison.py` | Experiment persistence, registry indexing, and incumbent/challenger decisions. |
| `aiquanttrader/research/features.py`, `regime.py`, `models.py`, `significance.py`, `optimizer.py` | Experimental feature, HMM, ML, statistical, and optimization tools. Optional dependencies are not all declared in core requirements. |
| `scripts/promote_compare.py`, `scripts/report_run.py`, `scripts/strategy_report.py` | Research report and promotion-support CLIs. |

### Not in the live or promotion path

These modules are retained research/legacy code and must not be mistaken for
deployed alpha:

- `aiquanttrader/hft.py`
- `aiquanttrader/indicators.py`
- `aiquanttrader/strategies/grid.py`
- `aiquanttrader/strategies/backtesting.py` (includes martingale research)
- `aiquanttrader/strategies/harmonics.py`
- `aiquanttrader/strategies/smart_money.py`
- `aiquanttrader/strategies/orchestrator.py`
- `aiquanttrader/risk/kelly.py`, `vol_target.py`, and `limits.py`
- `aiquanttrader/monitoring/alpha_decay.py`

`aiquanttrader/execution/` currently has no implementation. `python -m aiquanttrader`
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
python3 -m compileall -q aiquanttrader scripts
python3 scripts/mt5_trade_report.py
.venv/bin/python -m pytest -q -p no:cacheprovider
```

After EA changes also run `scripts/sync_mt5_ea.sh`, inspect the MetaEditor
compile result, restart only `aiquanttrader-mt5`, and verify deployed status/report.

## Verified Snapshot and Known Gaps

Observed on 2026-07-15; re-run the listed commands before relying on numbers:

- Runtime: all four PM2 services online, v2.00 heartbeat fresh, no open managed
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
- Release identity: live status reports v2.00 and the current repository HEAD
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
   comment does not start with `AIQuantTrader_`; healthcheck is the main detector for
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
