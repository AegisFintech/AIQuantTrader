# Phase 2 Native Foundation

## Outcome

Phase 2 creates an independently buildable native Python distribution, typed
configuration and schemas, Rust workspace boundary, container security policy,
and native CI. It creates no strategy, market-data connection, wallet reader,
or order submission path.

The topology is rendered in
[`../architecture/diagrams/phase-2-foundation.mmd`](../architecture/diagrams/phase-2-foundation.mmd).

## Repository delta

```text
native/
  pyproject.toml
  uv.lock
  Dockerfile
  compose.yaml
  configs/
  schemas/
  scripts/
  src/aiquanttrader_native/{config,domain,service}/
  tests/{unit,integration}/
rust/
  Cargo.toml
  Cargo.lock
  rust-toolchain.toml
.github/workflows/native-ci.yml
Makefile
docs/adr/0008-native-legacy-package-isolation.md
docs/architecture/diagrams/phase-2-foundation.mmd
docs/migration/PHASE_2_FOUNDATION.md
```

## Configuration precedence

Configuration loads in this order:

1. `native/configs/base.toml`;
2. exactly one named environment overlay;
3. explicit `AQT_NATIVE__SECTION__FIELD` process-environment overrides.

Unknown keys, malformed values, missing sources, path traversal, oversized
files, invalid instruments, and unsafe execution combinations fail startup.
Private keys are never configuration values; only `/run/secrets/...` references
are accepted.

All checked-in modes have `execution.enabled=false`. Paper, shadow, and research
modes cannot enable execution. Mainnet execution is structurally restricted to
canary/production and requires separate trading/control wallet references plus a
complete artifact-bound approval. Production additionally requires a scale
approval identifier.

## Dependency and image policy

- uv is pinned to `0.11.29`; the universal `uv.lock` is committed.
- Python is pinned to `3.12.13`.
- NautilusTrader `1.230.0`, the official Hyperliquid SDK `0.24.0`, and
  HftBacktest `2.4.4` are locked optional groups but are not installed in the
  foundation runtime image.
- The Python base image is pinned to the multi-platform digest for
  `python:3.12.13-slim-bookworm` observed during Phase 2.
- The container runs as `65532:65532`, drops all capabilities, uses a read-only
  root, and has explicit data/state volumes and bounded local logs.
- Rust is pinned to `1.96.0`. No Rust crate is added until a measured hot path
  has an interface, benchmark, and owner.

## Hard bounds

Phase 2 introduces application ceilings, not approved production risk:

- daily loss fraction at most 2%;
- high-water drawdown at most 5%;
- leverage at most 5x;
- order notional at most USD 10,000;
- inventory notional at most USD 50,000;
- at most 20 open orders and 10 order commands per second.

Environment policies must be tighter. Raising a hard ceiling requires code
review and a risk ADR; the existence of a ceiling does not authorize capital.

## Validation

- Ruff formatting and linting;
- strict mypy checking with the Pydantic plugin;
- branch-covered unit and integration tests;
- deterministic checked-in JSON Schema generation;
- Python dependency audit and repository secret scan;
- documentation-link and Mermaid entry-point checks;
- Rust toolchain/lock metadata verification;
- Docker build, non-root identity, read-only execution, fail-closed config, and
  Compose policy checks.

## Forward migration

Phase 3 adds the recorder and data integrity/storage implementations inside the
isolated native project. It consumes the checked-in event schemas and cannot
enable exchange execution.

## Rollback

Revert the Phase 2 merge commit and remove any locally built native image and
unused native volumes. No MT5 service, Common File, `.env`, PM2 definition,
broker position, or runtime credential is changed by this phase, so legacy
trading rollback is neither required nor authorized.
