# ADR 0017: Minimal Paper Runtime Image

Status: accepted
Date: 2026-08-21

## Context

The credential-free paper service used the general foundation image. That
image installed HftBacktest, NautilusTrader, the Hyperliquid SDK, PyArrow, and
governance cryptography even though the live paper process neither trains a
model, converts Parquet, submits an exchange order, nor signs an approval. On
the constrained Debian host, a code-only paper rebuild temporarily generated
5.738 GB of reclaimable build cache and made routine deployments slower and
more exposed to disk pressure.

Two source-level imports prevented a truly narrow image: the shared kernel
eagerly imported its HftBacktest and Nautilus representation adapters, and the
feature package eagerly imported its PyArrow storage writer. The pure paper
kernel and feature engine do not need those libraries.

## Decision

- Add a lockfile-backed `paper-runtime` dependency group containing only
  DuckDB, NumPy, the optional OpenAI observer client, Prometheus, Pydantic,
  WebSockets, and Zstandard plus their pinned transitive dependencies.
- Build the application wheel in an independent package-builder stage and
  install it into the paper environment with `--no-deps`. The lockfile-selected
  paper group is the complete runtime dependency authority.
- Add a dedicated non-root `paper` Docker target and
  `aiquanttrader-native-paper:0.1.0` image. Compose must select that target
  explicitly.
- Import HftBacktest constants and Nautilus data types only inside their
  representation-specific adapter functions. Import HftBacktest construction
  objects only inside backtest functions.
- Keep the feature dataset writer as a lazy package export. Research callers
  retain the same public API, while paper import does not load PyArrow.
- Prove in a fresh subprocess that the paper CLI loads while HftBacktest,
  NautilusTrader, Hyperliquid SDK, PyArrow, and PyCryptodome imports are
  rejected. Inspect the built image to prove those distributions are absent.
- Preserve all paper commands, OpenAI observer capability, configuration,
  strategy, risk, simulator, journal, raw recorder, healthcheck, UID, read-only
  filesystem, volumes, ports, and execution-disabled posture.

## Alternatives considered

- Keep the general image and prune after each deployment: recoverable space is
  restored eventually, but every build still incurs the download, compile,
  export, and temporary disk peak.
- Remove optional OpenAI support: smaller still, but it would silently remove
  an owner-requested shadow-only capability. The client remains pinned and lazy.
- Duplicate the kernel into a paper-only module: avoids optional imports but
  forks the strategy contract and weakens research/live parity.
- Install the wheel with its declared project dependencies: conventional, but
  it necessarily restores every general runtime dependency. The explicitly
  locked deployment group plus `--no-deps` is narrower and testable.
- Use Alpine: potentially smaller, but musl changes binary-wheel availability,
  build time, and numerical/runtime behavior. The pinned Debian base remains.
- Remove DuckDB from paper: rejected because the raw-first recorder owns the
  single-writer manifest catalog during paper capture.

## Consequences

- On the deployment host, the built paper image is 98,100,519 bytes versus
  577,778,314 bytes for the general foundation image: an 83.02% reduction.
- The paper build resolves 21 packages instead of the general environment's 89
  and no longer builds or exports the research/live-execution dependency tree.
- Adapter calls still fail naturally if invoked outside an environment that
  installs their pinned engine. Full runtime, research, test, and execution
  images retain those dependencies and parity tests cover the adapter paths.
- Lazy imports add one import operation only when an adapter or Parquet writer
  is actually called. There is no additional work in the paper market-data,
  feature, strategy, risk, or journal hot paths.
- Image size is diagnostic evidence, not permission to reduce disk reserves,
  weaken validation, clear the kill switch, or enable execution.
