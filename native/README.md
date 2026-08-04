# AIQuantTrader Native Foundation

This project is the isolated Python foundation for the Linux-native BTC
perpetual platform. During migration it deliberately uses the import package
`aiquanttrader_native` so it cannot shadow or change the deployed legacy
`aiquanttrader` package. ADR 0008 defines the coexistence and final rename.

The Phase 2 package provides:

- typed, fail-closed deployment configuration;
- versioned market-data, feature, experiment, and deployment schemas;
- canonical serialization and artifact fingerprints;
- champion-challenger transition enforcement;
- a configuration/health CLI and HTTP readiness service.

It contains no strategy and no exchange order path. Installing the optional
`execution` dependency group does not authorize or enable trading.

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
