# AIQuantTrader Native Platform

This project is the isolated Python foundation for the Linux-native BTC
perpetual platform. During migration it deliberately uses the import package
`aiquanttrader_native` so it cannot shadow or change the deployed legacy
`aiquanttrader` package. ADR 0008 defines the coexistence and final rename.

The package currently provides the Phase 2 foundation, Phase 3 public market
data path, and Phase 4 fail-closed execution/risk path:

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

It contains no alpha strategy. The exchange order path is installed but remains
disabled in every checked-in environment. Configuration accepts only secret
file references. Phase 4 permits explicit testnet runtime enablement only;
enabled mainnet wallets are rejected until Phase 9 implements cryptographic
artifact-approval verification.

## Development

Use uv `0.11.29` and Python `3.12.13`:

```bash
uv sync --frozen --group dev
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
