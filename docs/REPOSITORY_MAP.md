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
| `Dockerfile` | Pinned, non-root runtime and research images. |
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
| `market_data/` | Raw-first WebSocket capture, reconnects, integrity, normalization, service-specific health, cataloging, and Tardis acquisition. |
| `features/` | Incremental microstructure features and deterministic Parquet lineage. |
| `strategies/` | Pure Avellaneda-Stoikov market-maker and order-flow scalper kernels. |
| `execution/` | Nautilus execution gateway, strategy wiring, order journal, reconciliation, and metrics. |
| `risk/` | Synchronous risk authority and durable operator kill switch. |
| `sentinel/` | Separately credentialed dead-man renewal and emergency cancel-all. |
| `research/` | CPU model adapters, bounded search, drift, registry, and champion-challenger evaluation. |
| `paper/` | Public-feed market-by-price simulation, accounting, journals, evidence, and service lifecycle. |
| `shadow/` | Checksummed ingress, network-isolated counterfactual engine, observer, and evidence. |
| `governance/` | Artifact bundles, offline approvals, admission ledger, renewals, and release evidence. |
| `service/` | Common health/readiness service. |
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
`features/`, `paper/`, `production/`, `research/`, `shadow/`, and `strategies/`.

All checked-in environments default to execution disabled. Private keys are
mode-`0600` runtime files mounted below `/run/secrets`; their values are never
valid configuration fields, environment variables, CLI arguments, logs, or
repository artifacts.

## Storage

- Raw frames are archived before parsing and finalized atomically with hashes.
- Normalization is an independent worker; corrupt or incomplete data is
  quarantined rather than repaired in place.
- Recorder and normalizer publish separate atomic typed state; neither service
  can satisfy the other's healthcheck.
- Parquet is the immutable analytical format and DuckDB is the query/catalog
  layer.
- SQLite journals own restart continuity for live, paper, shadow, admission,
  and evidence state where transactional semantics are required.
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

Metrics cover market-data integrity and freshness, feature readiness, decision
and execution latency, risk denials, inventory, order/fill quality, markouts,
PnL attribution, drift, admission, renewal, sentinel continuity, and kill
state. Grafana dashboards are provisioned from
`observability/grafana/dashboards/`.

## Operational entry points

| Task | Entry point |
|---|---|
| Validate configuration/health | `aqt-native` |
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
