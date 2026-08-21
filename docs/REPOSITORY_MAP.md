# AIQuantTrader Repository Map

## Runtime authority

AIQuantTrader is a Linux-native BTC perpetual platform. Hyperliquid is the only
venue and `BTC-USD-PERP.HYPERLIQUID` is the only instrument. The project uses
NautilusTrader for exchange-native execution, the Hyperliquid SDK only inside
the independent safety sentinel, HftBacktest for causal replay, Tardis/local
ticks for research data, Parquet and DuckDB for analytics, and Prometheus plus
Grafana for observability.

The former MT5/MQL5/Wine/XAU/PM2/Streamlit implementation and its host
integrations are retired. Git history and Phase 10 evidence documents preserve
the migration record; none of those systems has runtime authority.

## Top-level layout

| Path | Ownership |
|---|---|
| `src/aiquanttrader/` | Canonical Python application package. |
| `tests/unit/` | Fast deterministic component and contract tests. |
| `tests/integration/` | Cross-layer, CLI, replay, service, and deployment-contract tests. |
| `configs/` | Typed, fail-closed environment and artifact policies. |
| `schemas/` | Checked-in JSON schemas generated from domain models. |
| `observability/` | Prometheus and provisioned Grafana configuration. |
| `Dockerfile` | Pinned, non-root general, paper, readiness, and research images. |
| `compose.yaml` | Credential-free foundation, market-data, and paper services. |
| `compose.testnet.yaml` | Explicit testnet execution and independent sentinel overlay. |
| `compose.shadow.yaml` | Public gateway plus network-isolated shadow engine. |
| `compose.rehearsal.yaml` | Exact-image final testnet release rehearsal. |
| `compose.mainnet.yaml` | Signed-admission canary and production overlay. |
| `pyproject.toml`, `uv.lock` | Python 3.12 project and exact dependency resolution. |
| `rust/` | Pinned future performance-critical boundary; no Rust workspace yet. |
| `docs/` | Architecture, ADRs, migration records, and operator runbooks. |
| `scripts/` | Repository documentation, secret, and branch-hygiene checks only. |

## Application layers

| Package | Responsibility |
|---|---|
| `acceptance/` | Final-testnet lifecycle evidence assembly and verification. |
| `backtest/` | Tardis/Parquet conversion, HftBacktest replay, scenarios, statistics, and purged validation. |
| `config/` | Immutable typed configuration, environment overlays, validation, and fingerprints. |
| `domain/` | Versioned market, data, feature, execution, experiment, and governance contracts. |
| `market_data/` | Raw-first WebSocket capture, integrity, normalization, health, cataloging, Tardis acquisition, and content-addressed host-soak evidence. |
| `features/` | Incremental microstructure plus causal 1m/5m/15m structure and bounded-memory deterministic Parquet lineage. |
| `strategies/` | Pure Avellaneda-Stoikov, order-flow, bounded smart-money, and paper-only adaptive scalper kernels. |
| `execution/` | Nautilus execution gateway, strategy wiring, order journal, reconciliation, and metrics. |
| `risk/` | Synchronous risk authority and durable operator kill switch. |
| `sentinel/` | Separately credentialed dead-man renewal and emergency cancel-all. |
| `research/` | Causal forecast-matrix construction, CPU model adapters, bounded search, drift, registry, and champion-challenger evaluation. |
| `paper/` | Public-feed simulation, accounting, per-cycle strategy gate diagnostics, journals, evidence, and optional shadow-only LLM review. |
| `shadow/` | Checksummed ingress, network-isolated counterfactual engine, observer, and evidence. |
| `governance/` | Artifact bundles, offline approvals, admission ledger, renewals, and release evidence. |
| `service/` | Common health/readiness service plus read-only host storage expansion inspection and evidence. |
| `retirement/` | Offline replay of retained Phase 10 migration evidence; no operational capability. |

## Data and execution flow

```text
Hyperliquid public WebSocket
  -> raw Zstandard archive + manifest
  -> integrity and deterministic Parquet normalization
  -> incremental features
  -> pure strategy kernel
  -> synchronous risk authority
  -> paper simulator, isolated shadow sink, or Nautilus execution gateway
  -> durable journal + Prometheus metrics + immutable evidence
```

Only `execution/strategy.py` may submit, modify, or cancel ordinary exchange
orders through NautilusTrader. The SDK sentinel is a separate control plane and
may only maintain the exchange dead-man deadline and execute emergency
cancel-all. Paper and shadow code must not import an order-capable client.

## Configuration and secrets

The root configuration files (`base.toml`, `paper.toml`, `shadow.toml`,
`testnet.toml`, `canary.toml`, and `production.toml`) compose into an immutable
configuration bundle. Nested artifact policies live below `configs/backtest/`,
`features/`, `market-data/`, `operations/`, `paper/`, `production/`, `research/`,
`retirement/`, `shadow/`, and `strategies/`.

All checked-in environments default to execution disabled. Private keys are
mode-`0600` runtime files mounted below `/run/secrets`; their values are never
valid configuration fields, environment variables, CLI arguments, logs, or
repository artifacts.

Paper never receives an exchange account or wallet. Its optional OpenAI key is
mounted as a read-only file and is usable only by the asynchronous confirmation
observer. LLM output is evidence, never order or risk authority.
The dedicated paper image installs a lockfile-backed minimal dependency group;
HftBacktest, NautilusTrader, Hyperliquid SDK, PyArrow, and approval cryptography
remain outside that credential-free runtime. Shared adapter and storage APIs are
lazy so this isolation does not fork kernel behavior.

## Storage

- Raw frames are archived before parsing and finalized atomically with hashes.
- Normalization is an independent worker; corrupt or incomplete data is
  quarantined rather than repaired in place.
- Recorder and normalizer publish separate atomic typed state; neither service
  can satisfy the other's healthcheck.
- The frozen deployment soak evaluator discovers only in-window segments and
  binds verified lineage to runtime/collector commits, image/config identities,
  Prometheus counters, restart counts, and disk floors.
- The storage expansion preflight binds a fresh research-readiness projection,
  checked recorder/maintenance reserves, filesystem usage, and Linux sysfs
  layout into an immutable report. It identifies the next EBS, partition, or
  filesystem layer but has no cloud, package-manager, process, or resize
  capability.
- Research-readiness schema v2 gates only on the latest uninterrupted chain and
  binds complete bounded reset-cause counts plus the latest reset boundary,
  signed gap, and prior finalization reason. Prometheus exports the current
  streak without using segment IDs as labels; diagnostics never weaken the
  frozen continuity or validation policies.
- Parquet is the immutable analytical format and DuckDB is the query/catalog
  layer.
- SQLite journals own restart continuity for live, paper, shadow, admission,
  and evidence state where transactional semantics are required.
- Research forecast matrices are deterministic schema-v3 development-only NPZ
  artifacts whose
  manifests bind the immutable feature dataset, raw dataset, feature schema,
  target horizon, sampling cadence, label-gap policy, causal semantic
  volatility regimes and counts, validation-plan hash, physical holdout cutoff,
  excluded-candidate accounting, semantic matrix hash, and file hash. Ordinary
  search rejects matrices containing samples or labels at/after the cutoff.
- Validation-plan schema v2 independently binds that target horizon so model
  search cannot pair a matrix with incompatible purge assumptions.
- No-signal control schema v2 is generated by a bounded neutral-alpha replay
  over immutable feature Parquet and binds the feature file/schema, strategy
  configuration, and execution scenario. Model search revalidates that lineage
  before loading an engine or training a trial.
- Research-control policy v2 replaces target-scale-dependent shuffled-label
  thresholds with repeated relative comparisons. Its forecast robustness
  report uses each row's causal feature-engine regime and requires the
  untouched test aggregate plus every low/normal/high slice to beat zero and
  train-mean baselines. A scenario-bound, non-overlapping directional replay
  must also clear conservative round-trip taker costs in every regime; all
  results are bound into mandatory negative controls.
- Target-feasibility schema v1 audits only the frozen fold training windows and
  computes separate optimistic non-overlapping count, total-return, and
  single-return ceilings across aggregate and semantic regimes. Model search
  recomputes it before loading an engine, stops when a required outcome is
  impossible, and binds its hash/full outcome into negative-control schema v4;
  the oracle is never promotion evidence.
- Horizon-family policy/report schema v1 freezes a sorted sub-five-minute
  candidate set, derives a horizon-safe validation plan and development matrix
  for every member, preserves one final-holdout boundary, and embeds every
  target-feasibility result. It performs no ranking, selection, model training,
  or holdout inclusion.
- Model-artifact manifest schema v2 uses deterministic native serialization:
  LightGBM text plus XGBoost/CatBoost JSON. CatBoost's non-predictive random
  model GUID is replaced with a content-derived value and its wall-clock model
  timestamp is replaced with an epoch sentinel; historical CBM/schema-v1
  artifacts remain audit evidence and are ineligible for current loading.
- Paper journals retain every strategy action and gate reason atomically with
  its feature/account/checkpoint cycle, including outcomes that emit no order
  intent; `aqt-paper diagnostics` summarizes that evidence without changing it.
- Paper runtime status schema v3 binds the feed-ready verdict to socket state
  plus signed public-frame, asset-context, and executable-BBO ages. Full L2
  depth has an independent two-second validity state and is never relabeled as
  current by a BBO; bounded metrics expose both limits, the exact fail-closed
  reason, and whether the latest 1 Hz market state incorporated L2 depth.
- `data/`, `state/`, models, databases, logs, credentials, and runtime files are
  gitignored.

## Governance

The promotion path is fixed:

```text
research -> causal backtest -> purged walk-forward -> paper -> shadow
         -> human review/signature -> canary -> production
```

Research may generate experiments and nominate challengers but cannot sign,
admit, promote to production, or increase capital. Production identity binds
the commit, image digest, data, model, feature schema, strategy, configuration,
risk limits, account/vault, wallet roles, capital, expiry, and rollback target.

## Observability

Metrics cover market-data integrity and freshness, continuous research-data
readiness and projected retention capacity, feature readiness, decision and
execution latency, risk denials, inventory, order/fill quality, markouts, PnL
attribution, drift, admission, renewal, sentinel continuity, and kill state.
Node Exporter supplies host CPU, memory, disk, network, uptime, and load.
Grafana dashboards are provisioned from
`observability/grafana/dashboards/`.
The paper container's dependency-light Docker probe checks fresh process
liveness; operational readiness remains a separate fail-closed verdict over
feed state and the durable operator kill. Liveness never authorizes trading.
The read-only `research-readiness` service exposes latest-chain progress and
gate state on port 9114. Its Docker health reports evaluator freshness rather
than requiring the expected multi-week capture gate to have passed.

## Operational entry points

| Task | Entry point |
|---|---|
| Validate configuration/health | `aqt-native` |
| Inspect storage expansion stage | `aqt-native storage-expansion-preflight` |
| Record/normalize/download data | `aqt-market-data` |
| Convert/replay/validate | `aqt-backtest` |
| Feature/model research | `aqt-research` |
| Credential-free paper | `aqt-paper` |
| Network-isolated shadow | `aqt-shadow` |
| Testnet/live execution | `aqt-execution` |
| Independent safety control | `aqt-sentinel` |
| Approval/admission/release | `aqt-governance` |
| Final-testnet evidence | `aqt-acceptance` |
| Historical retirement replay | `aqt-retirement` |

Use the corresponding runbook under `docs/operations/` before operating a
service. The authoritative release gate is
`docs/operations/NATIVE_RELEASE_CHECKLIST.md`.

## Verification

Run from the repository root:

```bash
make native-ci
uv run --frozen python scripts/check_repository_docs.py
docker compose config --quiet
```

CI additionally checks the exact lock, schemas, branch coverage, dependency
audit, committed-secret scan, container identity, read-only/non-root policy,
and the pinned Rust boundary.
