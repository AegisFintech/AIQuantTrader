# AIQuantTrader Agent Guide

## Operating mandate

AIQuantTrader is a Linux-native BTC perpetual trading platform. Work only on:

- venue: Hyperliquid;
- instrument: `BTC-USD-PERP.HYPERLIQUID`;
- execution: NautilusTrader Hyperliquid adapter behind the synchronous risk
  authority;
- research: HftBacktest plus Tardis/local tick data;
- storage: Parquet, DuckDB, and transactional service journals;
- deployment: Docker Compose on Debian Linux;
- observability: Prometheus and Grafana.

MT5, MQL5, Wine, PM2, Streamlit, XAU trading, Common Files, and their host
integrations are retired. Do not restore them without a new explicit owner
directive. A broker position opened manually by the owner is outside this
repository's authority and must not be modified.

## Source of truth

Read `docs/REPOSITORY_MAP.md` before broad rescans. Key paths are:

- application: `src/aiquanttrader/`;
- configuration: `configs/`;
- tests: `tests/unit/` and `tests/integration/`;
- schemas: `schemas/`;
- deployment: `Dockerfile` and `compose*.yaml`;
- observability: `observability/`;
- operations: `docs/operations/`;
- architecture and decisions: `docs/architecture/` and `docs/adr/`;
- dependency lock: `uv.lock`;
- CI: `.github/workflows/native-ci.yml`.

The `src/aiquanttrader/retirement/` package and Phase 10 documents are retained
for offline evidence replay only. They must never gain process-control,
package-manager, credential-revocation, network, or trading capability.

## Safety invariants

- Every checked-in environment keeps execution and live strategy disabled.
- Paper and shadow services have no wallet, signer, account secret, or
  order-capable exchange client.
- Shadow strategy execution remains under `network_mode: none`.
- Trading and sentinel wallets are distinct and mounted only into their owning
  service.
- The risk authority overrides strategies and enforces position, inventory,
  leverage, order, daily-loss, drawdown, freshness, disconnect, and kill limits.
- Reconciliation must complete before exposure can change.
- Production requires an exact immutable image, signed human approval,
  anti-replay admission, and unexpired renewals.
- Research automation may reject or nominate a challenger; it may not approve
  production or increase capital.
- Each long-running worker owns an atomic typed heartbeat and a service-specific
  healthcheck; never reuse another service's readiness signal.
- Verify post-build filesystem headroom before capture and never lower the
  recorder's disk floors to force startup.
- Never print, log, commit, or pass private keys on command lines. Runtime
  configuration accepts secret-file paths below `/run/secrets` only.

## Development workflow

Before editing, inspect `git status --short` and preserve unrelated user work.
After editing, run the checks proportional to the change; the full gate is:

```bash
uv lock --check
uv sync --frozen --extra research --group dev
uv run --frozen ruff format --check src tests scripts
uv run --frozen ruff check src tests scripts
uv run --frozen mypy
uv run --frozen pytest --cov
uv run --frozen aqt-native export-schemas --output schemas --check
uv run --frozen python scripts/check_repository_docs.py
./scripts/check_secrets.sh
docker compose config --quiet
```

Use `apply_patch` for edits, strong typing for production code, and unit plus
integration tests for behavioral changes. Update README, repository map,
runbooks, schemas, and diagrams whenever their contracts change.

## Deployment progression

The only valid progression is:

`research -> backtest -> walk-forward -> paper -> shadow -> human promotion review -> canary -> production`

No stage may be skipped. A failing or incomplete gate leaves the candidate
rejected or awaiting approval; it never falls forward.
