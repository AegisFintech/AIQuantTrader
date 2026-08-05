# Phase 6: BTC Features, Strategies, and Research Governance

Status: implementation complete; market calibration and retained full-dataset
acceptance evidence pending.

Phase 6 adds the first native BTC strategy and forecasting code. This original
increment did not wire a strategy into the Phase 4 order gateway, mount
exchange credentials, or alter the deployed MT5 runtime. The later
[`Phase 4/6 convergence increment`](PHASE_4_6_LIVE_STRATEGY_CONVERGENCE.md)
wires the same pure kernels behind the sole risk gateway while retaining those
boundaries. Research automation can stop only at `AWAITING_APPROVAL`.

## Architecture

The source diagram is
[`phase-6-research.mmd`](../architecture/diagrams/phase-6-research.mmd).

```text
Hft local-arrival state ----> shared feature engine ----> pure strategy kernels
Nautilus market objects ----/                              | exact parity

admitted data -> causal labels -> bounded validation search -> native model
  -> randomized-label/no-signal controls -> champion-challenger gates
  -> append-only stage events -> AWAITING_APPROVAL -> human-only boundary
```

The feature engine and strategy kernels own no network, storage, clock, risk,
or exchange handles. The same incremental update path consumes both offline
HftBacktest states and normalized Nautilus objects. Strategies emit typed order
intents; the synchronous Phase 4 risk authority remains the final authority
in paper, shadow, and the disabled-by-default exchange pipeline.

## Repository delta

```text
native/
|- Dockerfile (separate dependency-complete, non-root `research` target)
|- configs/features/microstructure-v1.toml
|- configs/strategies/{avellaneda-stoikov,order-flow-scalper}-v1.toml
|- configs/research/{search-lightgbm,promotion}-v1.json
|- schemas/{features,research}.schema.json
|- src/aiquanttrader_native/features/{models,engine,storage}.py
|- src/aiquanttrader_native/strategies/{common,config,market_maker,scalper}.py
|- src/aiquanttrader_native/research/
|  |- {models,model_adapters,artifacts,search}.py
|  `- {drift,governance,registry,metrics,cli}.py
|- observability/grafana/dashboards/research.json
`- tests/{unit,integration}/test_{feature,research,strategy,model}*.py

docs/
|- architecture/diagrams/phase-6-research.mmd
|- migration/PHASE_6_RESEARCH.md
`- operations/RESEARCH_RUNBOOK.md
```

## Feature contracts and causality

`btc-microstructure-v1` has one ordered, hashed model feature schema. Every
snapshot records source-event, receive, and computation timestamps; maximum
input age; readiness; calibration identity; and the exact feature-schema hash.
The engine rejects non-monotonic receive time, stale input, crossed books,
non-finite values, and computation before receipt.

The bounded windows compute:

- book imbalance, microprice, VAMP, weighted midprice, queue imbalance, and
  depth imbalance;
- trade-flow imbalance, buy/sell pressure, aggressor ratio, and volume delta;
- realized volatility, ATR in basis points, spread change/z-score, short-term
  mid return, and volatility regime;
- confirmed/target inventory drift, utilization, liquidation distance, and
  normalized inventory risk;
- bid/ask fill estimates, public queue-ahead estimates, and causal trade
  markouts for adverse selection.

Multiple trades arriving at one local timestamp are preserved. A trade is
consumed once rather than retained as sticky state across later book updates.
Feature Parquet is deterministic and accompanied by a manifest binding source
dataset, feature configuration, schema, file hash, rows, path, and time range.

Fill probability and queue position remain estimates. The checked-in feature
configuration labels its fill model uncalibrated, and the market maker rejects
it by default. Public market-by-price depth cannot establish private queue
ground truth.

## Strategy kernels

### Avellaneda-Stoikov market maker

The passive kernel derives reservation price and half-spread from realized
volatility, risk aversion, horizon, arrival intensity, inventory, funding,
adverse selection, and optional forecasts. It applies tick rounding, post-only
orders, an absolute inventory cap, one-sided quoting at the cap, quote lifetime,
hysteresis, cancel-replace identity, and a maximum observed-spread gate.

The checked-in config requires a calibrated fill model. It is therefore a
research seed, not a production-ready quoting policy. Arrival intensity, risk
aversion, quote lifetime, and fill response require retained testnet/paper
calibration before promotion evidence is valid.

### Order-flow scalper

The scalper combines book/depth imbalance, signed trade flow, mid momentum, and
an optional schema-bound forecast. It requires expected edge to exceed fees,
slippage, and a safety margin for taker entries. It also enforces spread,
volatility, cooldown, inventory, model-identity, and reduce-only behavior.
Passive mode emits post-only limits; taker mode emits IOC market intents.

Neither kernel can bypass Phase 4 risk. Integration must preserve the ordering
`feature -> strategy intent -> risk authority -> execution gateway`.

## Forecasting and search

The reference engines are pinned LightGBM, CPU-only XGBoost, and CatBoost.
Each adapter accepts a small allowlist of bounded parameters, fixes its random
seed and worker count, validates finite matrices, and retains exact feature
names. Artifacts use LightGBM text, XGBoost JSON, or CatBoost CBM. Pickle is not
accepted.

An artifact manifest binds engine, target, native format, file path/hash/size,
feature schema, training data/window, parameters, dependency lock, and model
identity. Loading re-hashes the file and rejects the wrong target, schema,
format, extension, path, or byte count.

Every fold trains on its causal train window, chooses parameters using only the
validation window, and reports the walk-forward test afterward. Changes to
test labels cannot alter the selected trial. Purge and embargo windows come
from the Phase 5 frozen validation plan. Search is bounded to at most 64
declared trials; it is not an open-ended optimizer.

## Governance and registry

Promotion reports gate post-cost PnL, maximum drawdown, 99% tail loss, absolute
inventory, fills, maker ratio, adverse-selection markout, decision latency,
fold consistency, feature drift, operational failures, champion improvement,
and negative controls. Randomized-label score and zero no-signal decisions are
explicit mandatory evidence by default.

The DuckDB registry permits one writer process/thread. Artifact and experiment
identities are immutable. New experiments must enter at `DRAFT`; stage events
are append-only, monotonically timestamped, and checked against the legal
transition graph. The automation CLI intentionally offers no human actor. It
cannot move `AWAITING_APPROVAL` to `APPROVED_CANARY` or production. The
registry also blocks a human API caller at that boundary until Phase 9
implements signed deployment-approval verification. Entry to
`AWAITING_APPROVAL` requires a registered passing report whose challenger,
metrics, controls, and report hash match the immutable experiment manifest.

Registry single-writer locking prevents in-process worker contention, but it is
not a distributed consensus system. Run exactly one research registry writer;
parallel workers must hand immutable results to that owner.

## Design decisions, alternatives, and tradeoffs

- One incremental feature implementation was chosen over separate vectorized
  offline and stateful live implementations. This makes parity testable and
  limits semantic drift, at the cost of less offline vectorization.
- Pure kernels were chosen over subclassing the live Nautilus strategy for
  alpha logic. Adapters remain more explicit, while research and live paths can
  compare identical decisions without a running node.
- Model-native formats plus manifests were chosen over pickle/joblib. They
  narrow code-execution risk and make feature identity reviewable, but artifacts
  remain engine-version-sensitive, so the lock hash is part of their identity.
- Declared bounded search was chosen over Bayesian or unconstrained search. It
  reduces compute and selection bias and makes reruns auditable, but may leave
  parameter performance unexplored.
- DuckDB with an OS writer lock was chosen over a network database for the
  isolated research host. It is simple and analytically useful, but requires a
  single owner and is not suitable for multiple writable hosts.
- Population stability index and standardized mean shift were chosen as
  interpretable initial drift alarms. They detect distribution change, not
  profitability decay or causation, so drift is one gate rather than a model
  promotion decision.
- Deep learning was excluded. The current single-instrument evidence and
  operational maturity do not justify its training, latency, and governance
  burden over deterministic tabular baselines.

## Performance implications

- Live feature updates are `O(configured depth + new trades)` with bounded
  deques. The configured depth is capped at ten levels.
- Current rolling sums scan bounded windows rather than maintaining subtractive
  accumulators. This favors auditability at the initial event rate; profile
  receive-to-feature p99 before introducing Rust or more stateful arithmetic.
- Model training is CPU-only and single-threaded for deterministic, bounded
  research. Parallelism belongs across isolated experiments, not inside one
  registry writer.
- Feature Parquet construction materializes the selected replay in memory.
  Partition production studies by admitted UTC hour/day artifacts until a
  benchmark supports a streaming writer.
- Strategies use `Decimal` at the order boundary for deterministic price and
  quantity semantics. Feature/model arrays remain NumPy `float64`.

## Acceptance evidence

Automated now:

- deterministic incremental/batch feature replay, bounded windows, warmup,
  stale/non-monotonic failure, and Parquet lineage;
- exact feature and strategy-decision parity from Hft local-arrival states and
  real Nautilus market objects;
- inventory, calibration, spread, cost, volatility, model-identity,
  reduce-only, hysteresis, and cancellation invariants;
- native save/load tests for all three engines, artifact tamper checks, and
  unsafe-format rejection;
- validation-only selection, causal label-boundary tests, test-label isolation,
  and seeded randomized-label controls;
- complete promotion gates, stable/shifted drift tests, immutable registry,
  single-writer/thread ownership, monotonic stage history, and the human
  approval ceiling;
- strict schemas, typing, pinned research dependencies, dashboard JSON, and
  native quality gates;
- a separate non-root research container target with no wallet mounts or
  production approval capability.

Still required before Phase 6 is accepted:

- reviewed fill, arrival-intensity, queue, latency, fee, slippage, and markout
  calibration from retained testnet/paper evidence;
- admitted multi-regime Tardis/local datasets and full baseline plus pessimistic
  walk-forward reports;
- out-of-sample evidence for each forecast target, including label definition,
  class/regime coverage, calibration, and economic post-cost value;
- retained strategy/risk integration results and load benchmarks at recorded
  burst rates;
- an operator-reviewed registry backup/restore drill and signed evidence that
  no workflow crossed `AWAITING_APPROVAL`.

## Rollback

Stop research invocations, retain their immutable inputs and outputs, and
restore the prior native image/lock. Do not load an artifact whose dependency
lock, schema, or file hash no longer matches. No Phase 6 rollback action changes
or restarts the legacy MT5 deployment.
