# MT5/Wine to Hyperliquid Migration Plan

Status: approved; Phases 2-5 implemented in parallel, with phase-specific acceptance evidence pending
Migration mode: parallel replacement with gated cutover
Target: Linux-native BTC perpetual trading on Hyperliquid

## Safety principle

The existing MT5/XAUUSD system and the native BTC platform are separate
deployments. Migration does not translate live positions, order identifiers,
broker history, model state, or credentials. No process may own orders on both
venues. MT5 remains governed by the existing release checklist until the final
cutover phase.

Before migration implementation begins, the current uncommitted working tree
must be checkpointed on its own branch or commit. Migration commits must not
silently absorb unrelated XAU/MT5 work.

## Workstream sequence

### Phase 1: Architecture ratification

Deliverables:

- target system, live order-flow, and promotion diagrams;
- ADRs for instrument scope, execution, simulation, storage, risk, promotion,
  and liquidation data;
- complete file-disposition and threat-model documents;
- phase acceptance gates and native release checklist;
- repository documentation that distinguishes deployed legacy behavior from
  the approved target.

Tests and review:

- documentation links and Mermaid syntax;
- consistency review across README, AGENTS, repository map, ADRs, and gates;
- owner approval of the human production boundary and BTC perpetual scope.

Migration action: none. The current runtime is unchanged.

### Phase 2: Repository and build foundation

Deliverables:

- `src/` application layout and typed domain/configuration packages;
- Python 3.12 project with an exact dependency lock;
- pinned Rust toolchain and reserved performance boundary without premature
  application code; create the workspace and lockfile with the first
  evidence-backed crate;
- multi-stage Docker image and Docker Compose service definitions;
- CI for linting, type checks, unit tests, dependency audit, image build, secret
  scan, and documentation links;
- environment-specific configuration overlays whose live mode defaults to off;
- schemas for market events, features, experiments, approvals, and deployments.

Tests:

- configuration precedence and fail-closed production validation;
- container non-root/read-only behavior and health checks;
- deterministic serialization and schema compatibility;
- clean bootstrap on the supported Debian image.

Migration action: legacy packages remain installed and operational; no shared
imports or process management are introduced.

### Phase 3: Market-data infrastructure

Deliverables:

- independent Hyperliquid raw WebSocket recorder;
- normalized L2, BBO, trade, mark, index, funding, open-interest, and private
  account event schemas;
- reconnect, rate-limit, cadence, stale-data, and integrity handling;
- raw segment writer, manifests, Parquet normalizer, Nautilus catalog builder,
  retention controls, and disk-pressure protection;
- Tardis downloader/importer with coverage manifests and license-safe storage;
- metrics and a market-data Grafana dashboard.

Tests:

- fixture-based protocol contract tests;
- disconnect/reconnect, duplicate, malformed, stale, crossed-book, clock-skew,
  partial-segment, disk-full, and atomic-recovery tests;
- deterministic raw-to-Parquet replay;
- continuous testnet/mainnet-public capture soak test without trading keys.

Migration action: begin accumulating BTC data while MT5 continues unchanged.

### Phase 4: Execution and risk

Deliverables:

- Nautilus Hyperliquid data and execution clients in testnet configuration;
- canonical order identity and reconciliation journal;
- submit, cancel, cancel-replace, post-only, IOC, reduce-only, reject, partial
  fill, funding, and position handling;
- synchronous risk authority and signed deployment-policy validation;
- independent SDK safety sentinel with a separate API wallet;
- dead-man switch, operator kill, stale-data kill, disconnect kill, and flatten
  procedures;
- execution, risk, and reconciliation dashboards.

Tests:

- adapter contract tests pinned to the selected Nautilus release;
- Hyperliquid testnet order lifecycle and restart reconciliation;
- rate-limit, timeout/unknown-outcome, duplicate-command, reject-storm, stale
  private stream, dead process, dead network, sentinel failure, and kill tests;
- property tests proving strategies cannot exceed hard risk bounds;
- latency benchmarks under recorded event bursts.

Migration action: testnet only. Mainnet private keys are not provisioned.

### Phase 5: Backtesting framework

Deliverables:

- Tardis/local-capture conversion to HftBacktest events;
- calibrated order latency, feed latency, fee, rebate, funding, slippage,
  partial-fill, and queue-model scenarios;
- Nautilus replay and sandbox harness using production decision kernels;
- purged walk-forward, embargo, untouched holdout, stress, bootstrap, and
  deflated-selection reporting;
- deterministic experiment dataset and configuration hashes.

Tests:

- timestamp order and no-lookahead properties;
- known synthetic fill/latency scenarios;
- deterministic reruns on fixed inputs;
- HftBacktest/Nautilus decision parity;
- pessimistic queue and liquidity-taking sensitivity.

Migration action: declare all legacy bar/HFT results ineligible for native
promotion. Historical reports remain available for audit only.

### Phase 6: Feature, strategy, and research framework

Deliverables:

- incremental order-book, flow, volatility, inventory, and microstructure
  features with offline/live parity;
- Avellaneda-Stoikov market maker and order-flow scalper;
- LightGBM reference forecasts plus XGBoost/CatBoost challenger adapters;
- automated retraining and bounded hyperparameter search;
- experiment, dataset, model, feature, and deployment registries;
- champion-challenger reports and drift monitoring;
- a research dashboard and reproducible command interface.

Tests:

- feature causality, warmup, numerical stability, missing-event, and replay/live
  parity tests;
- strategy invariants and risk interaction tests;
- leakage tests for folds, normalization, labels, and hyperparameter selection;
- safe model serialization and feature-schema compatibility;
- negative-control and randomized-label experiments.

Migration action: automated workers may create challengers but have no
production credential or approval capability.

### Phase 7: Paper trading

Deliverables:

- live public feeds driving the production trading node with simulated orders;
- queue/fill simulator calibrated from testnet and later shadow observations;
- complete order decision, PnL attribution, inventory, latency, and drift
  telemetry;
- paper incident and restart runbooks.

Tests:

- multi-day fault injection and restart continuity;
- simulated fill calibration and sensitivity reporting;
- risk-limit, loss-limit, stale-data, and operator-kill drills;
- artifact and configuration reproducibility from the registry.

Migration action: no mainnet trading key is mounted. A candidate advances only
after satisfying the frozen paper policy and sample-size requirements.

### Phase 8: Shadow trading

Deliverables:

- production image, configuration, features, strategy, and risk path running on
  live market/account data;
- an execution sink that records approved commands but cryptographically cannot
  submit them;
- comparison of shadow commands with testnet/paper fills and live book outcomes;
- production-host operational soak and disaster-recovery rehearsal.

Tests:

- proof that zero exchange orders can be emitted in shadow mode;
- credential absence and network egress tests;
- decision, latency, markout, inventory-counterfactual, and drift gates;
- host reboot, disk pressure, clock degradation, and observability failure.

Migration action: successful automation stops at `AWAITING_APPROVAL`.

### Phase 9: Mainnet canary and cutover

Deliverables:

- signed human approval binding artifacts, capital, limits, expiry, and rollback;
- final testnet dress rehearsal using the exact image and configuration;
- minimum-capital mainnet canary with conservative hard limits;
- verified dead-man switch and independent sentinel;
- incident, flatten, cancel-all, credential-rotation, backup, restore, and
  rollback runbooks;
- final MT5 tag, archived operational evidence, and legacy removal PR.

Tests and review:

- two-person verification of account, subaccount, wallet, instrument, and policy;
- canary order/fill/reconciliation and fee/funding attribution;
- live kill drill at bounded exposure;
- explicit approval before any capital increase.

Migration action: after an agreed stable observation period, disable and archive
the MT5 deployment, tag it `mt5-final`, remove legacy code from main, and retain
reports/data according to policy. Rollback restores the last approved native
champion or leaves the system halted; it never silently restarts MT5 trading.

## Target repository structure

```text
apps/
  trading_node.py
  market_data_recorder.py
  control_sentinel.py
  research_worker.py
  governance.py
src/aiquanttrader/
  config/
  domain/
  market_data/{hyperliquid,tardis,integrity,storage}/
  features/{book,flow,volatility,inventory,microstructure}/
  strategies/{common,market_maker,scalper,ml}/
  execution/{nautilus,hyperliquid}/
  risk/
  backtest/{hftbacktest,nautilus}/
  research/
  governance/
  observability/
rust/
  crates/{microstructure,hft_data}/
configs/{base,testnet,paper,shadow,production,promotion}/
schemas/{market_data,features,experiments,deployments}/
infra/{docker,prometheus,grafana,alertmanager}/
tests/{unit,integration,contract,replay,chaos,performance}/
docs/{architecture,adr,data,research,operations,migration}/
scripts/
data/ models/ state/
compose.yaml Dockerfile Makefile pyproject.toml uv.lock Cargo.lock
```

## Coexistence controls

- Separate repositories are not required, but legacy and native services have
  distinct process names, directories, credentials, databases, logs, and ports.
- No implementation PR modifies or restarts MT5 unless its stated purpose
  explicitly includes a legacy fix.
- Native CI cannot access host runtime secrets.
- Research workers cannot access production trading or control keys.
- Testnet and mainnet wallets are different; trading and control wallets are
  different within each environment.
- Only one deployment manifest may own mainnet `BTC-USD-PERP.HYPERLIQUID`.

## Rollback policy

Before mainnet, rollback means halting the native stage and returning to the
previous validation stage. After mainnet, rollback means canceling orders,
reducing/flattening according to the incident policy, and restoring a previously
approved native artifact or remaining halted. A failed native BTC release does
not authorize automatic MT5/XAU reactivation.
