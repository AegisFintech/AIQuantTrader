# AIQuantTrader Native Platform

This project is the isolated Python foundation for the Linux-native BTC
perpetual platform. During migration it deliberately uses the import package
`aiquanttrader_native` so it cannot shadow or change the deployed legacy
`aiquanttrader` package. ADR 0008 defines the coexistence and final rename.

The package currently provides the Phase 2 foundation, Phase 3 public market
data path, Phase 4 fail-closed execution/risk path, Phase 5 causal backtesting
framework, and Phase 6 BTC research/strategy framework:

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

The Phase 6 strategies are research candidates and are not wired into the
exchange node. The exchange order path remains disabled in every checked-in
environment. Configuration accepts only secret file references. Phase 4
permits explicit testnet runtime enablement only; enabled mainnet wallets are
rejected until Phase 9 implements cryptographic artifact-approval verification.

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

Configuration never accepts a private key value. It accepts only absolute
secret-file references below `/run/secrets`.

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
