# AIQuantTrader Native Platform

This project is the isolated Python foundation for the Linux-native BTC
perpetual platform. During migration it deliberately uses the import package
`aiquanttrader_native` so it cannot shadow or change the deployed legacy
`aiquanttrader` package. ADR 0008 defines the coexistence and final rename.

The package currently provides the Phase 2 foundation, Phase 3 public market
data path, Phase 4 fail-closed execution/risk path, Phase 5 causal backtesting
framework, Phase 6 BTC research/strategy framework, Phase 7 paper path,
Phase 8 network-isolated shadow path, Phase 9 production-admission boundary,
and Phase 10 evidence-only legacy-retirement boundary:

- typed, fail-closed deployment configuration;
- versioned market-data, feature, experiment, and deployment schemas;
- canonical serialization and artifact fingerprints;
- champion-challenger transition enforcement;
- a configuration/health CLI and HTTP readiness service.
- raw-first Hyperliquid BTC WebSocket capture with reconnect and disk guards;
- deterministic, independently operated Parquet normalization and quarantine;
- immutable Tardis historical-file acquisition;
- DuckDB manifest catalogs and bounded Prometheus metrics.
- a synchronous position, inventory, leverage, loss, drawdown, freshness, and
  operator-kill risk authority with short-lived single-use approvals;
- one risk-managed NautilusTrader Hyperliquid execution gateway and a durable
  SQLite order journal with unknown-outcome reconciliation;
- the same incremental features and pure market-making/scalping kernels wired
  into that sole gateway using managed Nautilus L2/trade data, reconciled
  account state, cancel-confirm quote replacement, and durable equity baselines;
- a separately credentialed SDK sentinel for exchange dead-man renewal and
  emergency cancel-all.
- deterministic Tardis and admitted-Parquet conversion into HftBacktest events
  with immutable lineage manifests;
- versioned queue, latency, fee, liquidity, slippage, partial-fill, and funding
  scenarios with an explicit calibration gate;
- a pure strategy-kernel boundary shared by Hft local-arrival replay and actual
  Nautilus market-data objects;
- purged walk-forward planning, validation-only selection receipts, untouched
  holdout authorization, block bootstrap, and multiple-selection penalties.
- bounded causal order-book, flow, volatility, inventory, fill, and
  adverse-selection features with deterministic Parquet lineage;
- pure Avellaneda-Stoikov market-making and cost-aware order-flow scalping
  kernels with HftBacktest/Nautilus representation parity;
- CPU-only LightGBM, XGBoost, and CatBoost adapters using native model formats,
  schema/hash validation, bounded validation search, and negative controls;
- immutable single-writer research registry, full champion-challenger gates,
  drift reports, automation ceiling, metrics contract, and Grafana dashboard.
- raw-first live public events driving the same feature, strategy, and hard-risk
  code with no exchange account or wallet capability;
- deterministic market-by-price paper queue/fill execution, cash/inventory/PnL,
  fees, funding, latency, markouts, and scenario lineage;
- transactional SQLite restart continuity, cancel-on-resume, stale/kill
  handling, frozen sample/regime/drift/drill evidence, and a paper dashboard.
- a public-only raw-first gateway feeding checksummed durable ingress into a
  `network_mode: none` shadow engine with no wallet, signer, or execution
  client;
- atomic counterfactual submit/cancel commands, exact retained-ingress replay,
  operational latency/availability/fault evidence, and a read-only observer.
- offline Ed25519 deployment approval bound to exact artifacts, image, commit,
  account/vault, wallet roles, capital, risk, expiry, and rollback;
- a durable anti-replay admission ledger checked independently by execution and
  the sentinel, conservative canary hard caps, and frozen canary evidence gates.
- chained, short-lived production authorization renewals which preserve the
  exact admission, release identity, and capital while preventing expiry gaps,
  replay, or mutation through the renewal path.
- a frozen complete final-testnet evaluator, exact target-behavior fingerprint,
  and atomic semantic preparation of unsigned release bundles for offline
  review and signing.
- hash-linked execution/sentinel safety evidence plus a credential-free,
  deterministic assembler for strict stopped final-testnet bundles.
- typed final-archive/readiness and disabled-window evidence, scoped retirement
  approvals, exact cleanup manifests, and offline Ed25519 verification with no
  stop, removal, credential, package-manager, network, or trading capability.
- independent native-production evidence assembly over the signed release,
  checkpointed admission ledger, complete renewal chain, exact artifacts,
  hash-linked audits, typed risk reasons, bounded sentinel continuity,
  incidents, and four recovery drills.
- exact legacy final-archive assembly over eleven immutable categories,
  isolated restore equality, an externally frozen recursive zero-finding
  credential scan, annotated `mt5-final` lineage, and remaining retention.
- derived final MT5 state from raw retained trade-report, broker-export,
  MT5-status, pause-file, and five-surface command-writer evidence with
  cross-source reconciliation and bounded freshness.
- cross-bundle retirement-readiness assembly with a shared retirement identity,
  dual immutable-root replay, completion-time authority/freshness checks, and
  mandatory source replay during readiness report evaluation.
- exact disabled-window assembly over ordered stop execution, six independently
  reviewed controls, bounded capability continuity, complete broker history,
  credential quarantine, native stability, action-time stop-approval
  verification, and mandatory source replay before cleanup review.
- a post-approval cleanup preflight that replays a second complete evidence
  bundle, enforces five-minute freshness, compares every stable target hash,
  revalidates the exact `remove_and_clean` signature, and emits only a
  short-lived evidence receipt.
- an exact post-action outcome replay that proves every action started inside
  that receipt window, validates typed removal/revocation/migration/archive
  postconditions, scans all evidence, and emits a canonical completion report.

The Phase 6 strategy kernels are now wired into the sole Phase 4 exchange
gateway, but the exchange order path remains disabled in every checked-in
environment. Configuration accepts only secret file references. Phase 4
permits explicit testnet runtime enablement. Phase 9 permits structural mainnet
enablement only when complete approval references are supplied; startup and
every exposure-changing command still require valid signed artifacts and an
explicit active ledger admission. Sustained production additionally requires
signed renewals before each authorization expires. No checked-in overlay enables
execution.

## Development

Use uv `0.11.29` and Python `3.12.13`:

```bash
uv sync --frozen --extra research --group dev
uv run ruff format --check src tests scripts
uv run ruff check src tests scripts
uv run mypy
uv run pytest --cov
uv run aqt-native export-schemas --output schemas --check
```

Validate an environment:

```bash
uv run aqt-native validate-config --config-dir configs --environment paper
```

Environment overrides use a double-underscore path and the prefix
`AQT_NATIVE__`, for example:

```bash
AQT_NATIVE__OBSERVABILITY__HEALTH_PORT=9200 \
  uv run aqt-native validate-config --config-dir configs --environment paper
```

Evaluate retained Phase 10 evidence without performing any operational action:

```bash
uv run aqt-retirement --help
uv run aqt-retirement assemble-native \
  --evidence-root /absolute/retained/native-production \
  --policy configs/retirement/evidence-v1.toml \
  --approval-key-id <pinned-key-id> \
  --approval-public-key-sha256 <pinned-fingerprint> \
  --output /absolute/retained/native-production-observation.json

uv run aqt-retirement assemble-archive \
  --evidence-root /absolute/retained/legacy-final \
  --policy configs/retirement/evidence-v1.toml \
  --credential-scan-policy configs/retirement/archive-credential-scan-v1.toml \
  --output /absolute/retained/legacy-archive-manifest.json

uv run aqt-retirement assemble-final-state \
  --evidence-root /absolute/retained/legacy-final \
  --archive-manifest /absolute/retained/legacy-archive-manifest.json \
  --policy configs/retirement/evidence-v1.toml \
  --credential-scan-policy configs/retirement/archive-credential-scan-v1.toml \
  --output /absolute/retained/legacy-final-state.json
```

Configuration never accepts a private key value. It accepts only absolute
secret-file references below `/run/secrets`.

Mainnet admission is documented in
`../docs/operations/MAINNET_CANARY_RUNBOOK.md`. A passing canary report never
promotes automatically and a separate signed approval is required for scale.
Chained production renewal is specified in
`../docs/migration/PHASE_9_PRODUCTION_RENEWAL.md`; it cannot alter a release or
revive expired authority.
Phase 10 production-evidence assembly is specified in
`../docs/migration/PHASE_10_PRODUCTION_EVIDENCE.md`; its output grants no stop
or cleanup authority.
Phase 10 legacy-archive assembly is specified in
`../docs/migration/PHASE_10_LEGACY_ARCHIVE.md`; it does not collect live facts,
create the final tag, or prove the broker account is flat.
Phase 10 final-state assembly is specified in
`../docs/migration/PHASE_10_FINAL_STATE.md`; it reads retained evidence only and
cannot pause, flatten, stop, or otherwise act on MT5 or the broker.
Phase 10 disabled-window assembly is specified in
`../docs/migration/PHASE_10_DISABLED_OBSERVATION.md`; it verifies retained
post-stop evidence and can only produce `AWAITING CLEANUP APPROVAL`.
Phase 10 action-time cleanup preflight is specified in
`../docs/migration/PHASE_10_CLEANUP_PREFLIGHT.md`; it recaptures no state and
cannot stop, revoke, remove, migrate, or delete anything.
Phase 10 cleanup outcome evidence is specified in
`../docs/migration/PHASE_10_CLEANUP_OUTCOME.md`; it verifies retained operator
evidence and cannot execute or authorize cleanup.

## Execution and risk

The testnet deployment is an explicit overlay so ordinary development does not
even resolve wallet-file variables:

```bash
docker compose -f compose.yaml -f compose.testnet.yaml \
  --profile execution-testnet config --quiet
```

`trading-node` mounts only the testnet trading wallet. `safety-sentinel` mounts
only the independently approved testnet control wallet. The exact setup,
scenario matrix, kill procedure, evidence requirements, and rollback are in
[`EXECUTION_RISK_RUNBOOK.md`](../docs/operations/EXECUTION_RISK_RUNBOOK.md).
The live strategy pipeline and its cancel-confirm/restart invariants are in
[`PHASE_4_6_LIVE_STRATEGY_CONVERGENCE.md`](../docs/migration/PHASE_4_6_LIVE_STRATEGY_CONVERGENCE.md).

## Market data

The checked-in paper overlay enables public data while execution remains
disabled. Start the isolated services with:

```bash
docker compose --profile market-data up --build \
  market-data-recorder market-data-normalizer
```

The recorder writes and flushes the exact logical WebSocket frame before any
parsing. The normalizer is a separate process so Arrow/Parquet work cannot
delay capture. Operational procedures, failure handling, Tardis downloads,
dataset admission, and the required soak are in
[`MARKET_DATA_RUNBOOK.md`](../docs/operations/MARKET_DATA_RUNBOOK.md).

## Backtesting

Validate the non-promotable seed scenarios and inspect conversion commands:

```bash
uv run aqt-backtest validate-scenario --scenario configs/backtest/baseline.toml
uv run aqt-backtest validate-scenario --scenario configs/backtest/pessimistic.toml
uv run aqt-backtest convert-tardis --help
uv run aqt-backtest convert-normalized --help
uv run aqt-backtest plan-validation --help
```

Conversion preserves exchange and local-arrival timestamps and produces a
byte-deterministic NPZ plus a SHA-256 lineage manifest. The checked-in scenarios
remain `uncalibrated`; governance must reject them for promotion until new,
reviewed calibration artifacts are bound by hash. Procedures and evidence
requirements are in
[`BACKTESTING_RUNBOOK.md`](../docs/operations/BACKTESTING_RUNBOOK.md).

## Features, strategies, and research

Inspect the reproducible feature, model-search, model-validation, registry, and
promotion commands:

```bash
uv run aqt-research feature-replay --help
uv run aqt-research run-search --help
uv run aqt-research validate-model --help
uv run aqt-research registry-register-experiment --help
uv run aqt-research evaluate --help
```

The market-maker seed requires calibrated fill evidence and therefore fails
closed with the checked-in uncalibrated feature configuration. Research may
advance a passing challenger only to `AWAITING_APPROVAL`; the CLI has no human
approval actor. See
[`PHASE_6_RESEARCH.md`](../docs/migration/PHASE_6_RESEARCH.md) and
[`RESEARCH_RUNBOOK.md`](../docs/operations/RESEARCH_RUNBOOK.md).

## Paper trading

Validate the credential-free paper config and render its isolated container:

```bash
uv run aqt-native validate-config --config-dir configs --environment paper
docker compose --profile paper config --quiet
AQT_NATIVE_CODE_IDENTITY="$(git rev-parse HEAD)" \
  docker compose --profile paper up --build paper-trader
```

The paper service owns raw capture; do not start `market-data-recorder` against
the same state volume. It durably syncs each raw frame before the live consumer
and accepts only conservative risk-adverse, zero-synthetic-feed-delay paper
scenarios. The checked-in scenarios are uncalibrated and therefore cannot pass
`aqt-paper evidence`. Procedures, drills, sensitivity rules, and rollback are in
[`PAPER_TRADING_RUNBOOK.md`](../docs/operations/PAPER_TRADING_RUNBOOK.md).
`aqt-paper replay` verifies finalized raw segments and runs required sensitivity
scenarios through the same consumer path without network access.

## Shadow deployment

Render the exact-image, split-network Phase 8 stack:

```bash
export AQT_NATIVE_IMAGE_REPOSITORY='registry.example/aiquanttrader-native'
export AQT_NATIVE_IMAGE_DIGEST='sha256:<digest>'
export AQT_NATIVE_CODE_IDENTITY="$(git rev-parse HEAD)"
docker compose -f compose.shadow.yaml config --quiet
```

Only `shadow-gateway` has internet access. `shadow-engine` has
`network_mode: none`, a read-only ingress volume, no secret/account mount, and
an application startup proof that no IP default route exists.
`shadow-observer` reads status/Prometheus files from the engine volume without
a control path. See
[`SHADOW_DEPLOYMENT_RUNBOOK.md`](../docs/operations/SHADOW_DEPLOYMENT_RUNBOOK.md)
for launch, kill, host/disk/clock/recorder/observer drills, replay comparison,
evidence, and rollback.

## Final testnet evidence and unsigned release

Render the proposed canary behavior from a complete root-owned release spec:

```bash
uv run aqt-governance release-fingerprint --config-dir configs \
  --environment canary --spec /secure/release/canary-release.toml \
  --output /secure/release/behavior-configuration.json
```

Run that exact image with `compose.rehearsal.yaml`, retain real evidence for all
15 lifecycle scenarios, then assemble and reproduce the typed observation
without either wallet:

```bash
uv run aqt-acceptance assemble \
  --evidence-root /secure/release/testnet-rehearsal-<id> \
  --output /secure/release/testnet-observation.json
uv run aqt-acceptance verify \
  --evidence-root /secure/release/testnet-rehearsal-<id> \
  --observation /secure/release/testnet-observation.json
```

Evaluate that observation with
`configs/production/testnet-dress-rehearsal-v1.toml`. A passing report stops at
`awaiting_canary_approval`.

`aqt-governance prepare-release` then cross-checks every artifact and evidence
identity and atomically emits a mode-0600 unsigned bundle. It has no signing or
admission capability. See
[`PHASE_9_RELEASE_EVIDENCE.md`](../docs/migration/PHASE_9_RELEASE_EVIDENCE.md)
and [`MAINNET_CANARY_RUNBOOK.md`](../docs/operations/MAINNET_CANARY_RUNBOOK.md).
The exact evidence inventory and failure behavior are in
[`PHASE_4_TESTNET_ACCEPTANCE_EVIDENCE.md`](../docs/migration/PHASE_4_TESTNET_ACCEPTANCE_EVIDENCE.md).
