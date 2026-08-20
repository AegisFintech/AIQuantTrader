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
  -> semantic-regime and post-cost replay -> randomized-label/no-signal controls
  -> champion-challenger gates
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
|- configs/research/search-{lightgbm,xgboost,catboost}-v1.json
|- configs/research/{promotion-v1,controls-v2}.json
|- schemas/{features,research}.schema.json
|- src/aiquanttrader_native/features/{models,engine,storage}.py
|- src/aiquanttrader_native/strategies/{common,config,market_maker,scalper}.py
|- src/aiquanttrader_native/research/
|  |- {models,model_adapters,artifacts,search,controls}.py
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
The research matrix builder verifies that lineage and constructs 30-second or
other explicitly configured future-mid labels without interpolation. Its
deterministic NPZ manifest binds the target horizon, sample interval, maximum
label delay, dropped-gap/tail accounting, each sample's causal semantic
volatility regime, regime counts, semantic matrix hash, and file hash. Matrix
schema v1 lacks this evidence and cannot be loaded by the current search path;
retain it for audit and build a new immutable schema-v2 artifact.

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
declared trials; it is not an open-ended optimizer. Search refuses an unbound
NPZ and revalidates the matrix manifest before loading any training rows.
Each fold also reports zero-prediction and train-window-mean test MSE; ranking
first among candidate models is insufficient when the winner cannot improve
on both non-leaking baselines.

The checked-in research-control policy removes the absolute randomized-label
MSE threshold, whose meaning changed with fold target variance. Every fold now
trains the selected parameters against three deterministic label permutations.
The median shuffled-label MSE must be at least `1.02` times the selected real-
label validation MSE, and even the lowest shuffled-label MSE must remain at
least `0.95` times the train-mean validation baseline. Seeds are derived from
the immutable policy and fold index and recorded with every raw score.

Forecast robustness consumes the exact `volatility_regime` stored at the
sample's causal feature snapshot. It does not infer regimes from test data or
repartition tied values with fold-specific quantiles. The untouched
walk-forward aggregate and low, normal, and high slices must each contain at
least 100 rows, and the selected model must strictly improve on both zero and
train-mean predictions in every slice. The configured fractional margin is
zero, but equality still fails. A missing or undersized regime fails closed.

The same policy performs a chronological, non-overlapping directional replay
over the untouched forecast rows. A signal is eligible only when absolute
predicted edge exceeds conservative round-trip taker fees, configured taker
slippage, and a 0.5 bps margin. Each accepted signal blocks observations until
its label exit time. Aggregate and all semantic regimes need the configured
trade counts, positive net/average return, and profit factor of at least 1.05.
Negative maker fees are not treated as rebates in this screen. The report binds
the scenario identity/hash, calibration state, search receipt, exact test
matrix/window, fold, costs, counts, and accounting. An uncalibrated checked
scenario can report diagnostic performance but cannot pass. This screen
deliberately excludes fill, queue,
latency, funding, and inventory simulation and therefore does not replace the
Phase 5 HftBacktest scenario suite.

The complete robustness and economic reports are hashed into mandatory
negative controls, so aggregate performance cannot conceal a regime failure.
Experiment manifests reject controls whose search-receipt hash differs from the
experiment's selected search receipt.

The v2 no-signal control streams immutable feature Parquet and replays the real
order-flow kernel with only its three alpha inputs neutralized and forecast set
to zero. Market prices, spread, volatility, readiness, timestamps, and
scenario-derived costs remain intact. The report records total/ready rows and
every non-HOLD action, intent, or cancel, and binds feature file/schema,
strategy, and scenario identities. Model search revalidates all of that
lineage before training; a supplied zero is not trusted.

## Governance and registry

Promotion reports gate post-cost PnL, maximum drawdown, 99% tail loss, absolute
inventory, fills, maker ratio, adverse-selection markout, decision latency,
fold consistency, feature drift, operational failures, champion improvement,
and negative controls. Repeated relative randomized-label results, causal
semantic-regime robustness, scenario-bound forecast economics, and zero
no-signal decisions are explicit mandatory evidence by default.

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
- Relative shuffled-label comparisons were chosen over one absolute MSE floor
  because label variance changes across folds. Three repetitions limit
  single-shuffle luck without turning the control into an open-ended test, at
  the cost of three additional selected-parameter fits per fold.
- Feature-engine semantic regimes were chosen over training or test quantiles.
  They preserve the causal decision-time label, remain comparable across
  folds, and avoid empty middle slices caused by tied quantiles. Their quality
  depends on the frozen feature thresholds, so threshold changes require a new
  feature dataset, matrix, and experiment identity.
- A non-overlapping taker-cost replay was chosen as an early economic rejection
  gate. An overlapping vectorized return sum would double-count simultaneous
  exposure, while a full fill simulator at this stage would conflate forecast
  value with execution policy. The screen is conservative and cheap but cannot
  establish executable profitability; Phase 5 replay remains mandatory.
- First-observation-after-horizon labeling with an explicit maximum delay was
  chosen over interpolation. It preserves observed prices and makes feed gaps
  visible, at the cost of dropping candidates when the next observation is too
  late.
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
- Relative controls add exactly three selected-parameter fits per fold. Regime
  evaluation uses four vectorized MSE slices, while economic replay makes one
  chronological pass over the test rows; neither retrains nor materializes
  another feature matrix.
- Feature Parquet construction and no-signal replay stream deterministic Arrow
  batches. Feature construction retains one output row group plus bounded
  feature windows; no-signal replay retains one input batch and strategy
  memory.
- Matrix construction vectorizes feature columns and label lookup, then writes
  a compressed deterministic NPZ. Memory is linear in source rows; partition
  long captures before concatenating fold inputs.
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
  deterministic manifest-bound matrix construction, gap/tail accounting,
  repeated relative randomized-label controls, causal semantic-regime slicing,
  non-overlapping cost replay, and calibration gating on untouched walk-forward
  tests;
- generated neutral-alpha no-signal reports with immutable feature, strategy,
  and scenario lineage plus fail-before-training mismatch tests;
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
