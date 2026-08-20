# Native BTC Research Runbook

This runbook operates the Phase 6 feature, strategy, model, and governance
tooling. It does not authorize testnet orders, mainnet credentials, live
strategy wiring, final-holdout access, or production promotion.

## Preconditions

1. Work from an immutable commit and a clean native tree.
2. Use Python 3.12.13 and uv 0.11.29 with the exact lock.
3. Admit input through the Phase 3 quality manifest and convert it through the
   Phase 5 deterministic event path.
4. Freeze a Phase 5 validation plan before training.
5. Keep the research host free of trading and sentinel wallet files.
6. Assign exactly one process/thread as the DuckDB registry writer.

Install and verify:

```bash

uv sync --frozen --extra research --group dev
uv run ruff format --check src tests scripts
uv run ruff check src tests scripts
uv run mypy
uv run pytest --cov
uv run aqt-native export-schemas --output schemas --check
```

For an isolated batch image with no wallet or production approval material:

```bash
docker build --target research --tag aiquanttrader-native-research:0.1.0 .
docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=256m \
  --user 65532:65532 aiquanttrader-native-research:0.1.0 --help
```

Mount admitted data read-only and dedicated research model/state paths only for
real jobs. Do not reuse trading-node or sentinel secret mounts.

## 1. Build deterministic features

The input event hash must match the Phase 5 dataset manifest. The output path
is relative to its artifact root and must end in `.parquet`.

```bash
uv run aqt-research feature-replay \
  --events data/backtest/2026-08-01/events.npz \
  --dataset-manifest data/backtest/2026-08-01/events.manifest.json \
  --config configs/features/microstructure-v1.toml \
  --output-root data/research \
  --relative-path features/2026-08-01/btc-microstructure-v1.parquet
```

Retain the emitted feature manifest with the source dataset manifest. Reject
the run if input age, event order, schema, output hash, rows, or time bounds do
not validate. Never edit a generated feature file or manifest in place.
Replay yields causal market states and writes deterministic 65,536-row Parquet
groups incrementally; memory use is bounded by the loaded HftBacktest event
array, feature-engine time windows, and one output row group rather than the
full state and feature histories.
Offline replay applies the same causal stale-trade and stale-book exclusions as
live assembly. Feature-manifest schema v2 records both exclusion counts; the
research path never hides a discarded bootstrap trade or stale book state.

## 2. Construct causal labels

Build the initial 30-second BTC return matrix directly from the immutable
feature Parquet. All horizon and gap assumptions are explicit command inputs:

```bash
uv run aqt-research build-matrix \
  --features data/research/features/2026-08-01/btc-microstructure-v1.parquet \
  --feature-manifest data/research/features/2026-08-01/btc-microstructure-v1.parquet.manifest.json \
  --output-root data/research \
  --relative-path matrices/next-mid-return-30s-v3-development.npz \
  --target next_mid_return_bps \
  --horizon-ns 30000000000 \
  --sample-interval-ns 1000000000 \
  --maximum-label-delay-ns 2000000000 \
  --validation-plan data/research/plans/walk-forward-v1.json
```

The builder verifies the feature file hash, manifest row count, feature schema,
timestamp ordering, finite model values, and positive midprices. It uses the
first ready observation at or after the horizon and rejects that candidate if
the observation arrives beyond `maximum-label-delay-ns`; it never interpolates
prices or backfills across a data gap. Warmup rows and unlabeled tail rows are
excluded and counted in the matrix manifest.
Candidates whose sample or resolved label reaches the final-holdout start are
separately counted and omitted. This command is the privileged sealing step;
after it succeeds, ordinary model workers should mount only the development
NPZ, its manifest, the validation plan, and other public control inputs—not the
source feature Parquet or holdout data.

The deterministic NumPy NPZ contains exactly these arrays:

- `features`: two-dimensional finite float64 matrix in
  `btc-microstructure-v1` order;
- `labels`: one-dimensional finite float64 target;
- `sample_ts_ns`: strictly increasing observation receipt time;
- `label_end_ts_ns`: time at which each label becomes fully known and strictly
  later than its sample time;
- `volatility_regimes`: causal low/normal/high semantic regime captured from
  the same feature observation as each sample;
- `feature_schema_sha256`: scalar string equal to the checked-in schema hash;
- `source_dataset_sha256`: scalar lowercase SHA-256 bound by the validation
  plan.

NumPy pickle loading is disabled. The companion manifest binds the source
feature dataset and raw dataset, target, schema, horizon, sample interval,
maximum label delay, candidate and regime accounting, semantic matrix hash,
NPZ hash, rows, causal time window, complete validation-plan hash, development
cutoff, and excluded holdout candidates. `run-search` requires and revalidates
schema v3 before loading an engine. Retain schema-v1/v2 artifacts as historical
evidence, but rebuild them under a new path before current research; never
overwrite or manually upgrade an old matrix.
The frozen validation-plan schema v2 independently carries the expected label
horizon; a horizon mismatch fails before a search policy or model adapter is
loaded.
Normalization or calibration must be fit inside each train fold; do not
pre-normalize against validation, test, final holdout, or future rows.

### Reject an economically infeasible target before model fitting

Audit the sealed matrix against the exact control policy and execution
scenario before running a model adapter:

```bash
uv run aqt-research audit-target-feasibility \
  --matrix data/research/matrices/next-mid-return-v3-development.npz \
  --matrix-manifest data/research/matrices/next-mid-return-v3-development.npz.manifest.json \
  --validation-plan data/research/plans/walk-forward-v1.json \
  --control-policy configs/research/controls-v2.json \
  --scenario configs/backtest/baseline.toml \
  --output state/research/challenger-20260804/target-feasibility.json
```

The schema-v1 report opens only each fold's training window. For aggregate,
low, normal, and high volatility it computes the maximum non-overlapping
observation count, counts labels with positive perfect-direction return after
round-trip taker cost, and computes maximum-net and maximum-single-trade
ceilings. The count, total-return, and average-return bounds are evaluated
separately, making them optimistic necessary conditions rather than a claimed
achievable portfolio. They ignore model error, latency, impact, adverse
selection, fills, funding, and inventory. A failure proves the target/scenario
lacks enough opportunity for the declared gate; a pass only permits model
research and is not performance evidence.

Exit `0` means the opportunity ceilings and required calibration passed. Exit
`3` is a valid failing report; `opportunity_sufficient=true` with `passed=false`
means diagnostic model research may proceed but the scenario is not calibrated
for promotion. Exit `2` means invalid input or an operational failure.
`run-search` recomputes the report from the sealed matrix and refuses an
inexact report or insufficient opportunity ceiling before loading an engine.
The checked baseline is intentionally uncalibrated, and the retained
30-second development target currently fails the opportunity ceiling as well.

The retained 2026-08-20 audit used a 10.0 bps round-trip cost; the separate
forecast signal threshold is 10.5 bps after the frozen edge margin. Training
folds 0 and 1 had no positive-net perfect-direction labels. Fold 2 had only
three maximum non-overlapping positive-net labels. High volatility was absent from
the first three training folds, so their maximum possible high-regime trade
count was zero against the required 20. Do not run another engine or widen a
search policy on this 30-second taker target. Gather broader regime evidence
and calibrate costs, or predeclare and seal a different horizon or
passive-maker target under a new validation plan. Do not change thresholds
after this result, and do not open the final holdout.

## 3. Run bounded fold research

First generate the no-signal control from immutable feature evidence. Do not
hand-author a zero count:

```bash
uv run aqt-research run-no-signal-control \
  --features data/research/features/BTC.parquet \
  --feature-manifest data/research/features/BTC.parquet.manifest.json \
  --strategy-config configs/strategies/order-flow-scalper-v1.toml \
  --scenario configs/backtest/baseline.toml \
  --output state/research/challenger-20260804/no-signal.json
```

The control streams every Parquet row through the real order-flow kernel after
neutralizing only `book_imbalance`, `trade_flow_imbalance`, and
`mid_return_bps`; movement forecast is zero. Spread, volatility, readiness,
market prices, timestamps, and scenario-derived costs remain unchanged. Any
non-HOLD action, order intent, or cancel is counted as a decision. Exit `0`
means zero decisions, exit `3` means a valid failing report, and exit `2` means
invalid input or an operational failure.

The checked-in LightGBM, XGBoost, and CatBoost policies are bounded seeds, not
evidence that their ranges are optimal. Freeze the engine set and policies
before inspecting results. Run each fold separately and use a model-native
extension:

```bash
uv run aqt-research run-search \
  --matrix data/research/matrices/next-mid-return-v3-development.npz \
  --matrix-manifest data/research/matrices/next-mid-return-v3-development.npz.manifest.json \
  --validation-plan data/research/plans/walk-forward-v1.json \
  --fold 0 \
  --policy configs/research/search-lightgbm-v1.json \
  --engine lightgbm \
  --target next_mid_return_bps \
  --artifact-root models/research \
  --artifact-path challenger-20260804/fold-0.txt \
  --dependency-lock uv.lock \
  --created-at 2026-08-04T00:00:00+00:00 \
  --control-policy configs/research/controls-v2.json \
  --target-feasibility-report state/research/challenger-20260804/target-feasibility.json \
  --no-signal-report state/research/challenger-20260804/no-signal.json \
  --no-signal-feature-manifest data/research/features/BTC.parquet.manifest.json \
  --no-signal-strategy-config configs/strategies/order-flow-scalper-v1.toml \
  --no-signal-scenario configs/backtest/baseline.toml \
  --output state/research/challenger-20260804/fold-0.json
```

Use `.json` for XGBoost and CatBoost. CatBoost artifacts deterministically bind
their non-predictive model GUID to the remaining native JSON bytes and replace
the wall-clock training timestamp with an epoch sentinel. The manifest's
`created_at` remains the declared artifact time. CBM and model-artifact
schema-v1 files are historical evidence only: rerun their exact bound
experiment into a new path to create a schema-v2 artifact. Do not rename or
manually convert them. Do not use pickle, joblib, or an arbitrary
callback/object parameter. A trial policy can declare at most 64
trials, and adapters reject parameters outside their fixed allowlists/bounds.
Every fold receipt reports the selected model's untouched walk-forward test
MSE beside a zero-prediction baseline and a train-window-mean baseline. A model
that does not improve on both baselines has not demonstrated forecast value,
regardless of its rank among candidates.

The control policy runs three deterministic shuffled-label fits per fold. Its
comparisons are relative rather than an absolute MSE floor: the shuffled median
must be at least `1.02x` the selected-model validation MSE, and the lowest
shuffled score must be at least `0.95x` the training-mean validation MSE. Do not
change these thresholds after viewing a result. Each seed and score is retained
in the v4 negative-control report.

The same policy uses the causal `volatility_regime` retained in matrix schema
v2. It does not calculate fold-specific quantiles. The untouched walk-forward
test is scored as aggregate, low, normal, and high slices. All four need at
least 100 rows and must have strictly lower MSE than both zero and training-mean
predictions. The v2 fractional margin is zero, but equality does not pass. A
failed or absent slice sets both
`forecast_robustness_passed` and `negative_controls_passed` false. The command
still retains the valid failing model and receipt for audit; it does not open
the final holdout or advance a governance stage.

The command also runs a non-overlapping directional cost screen against the
exact `--no-signal-scenario`. Absolute forecast edge must clear non-negative
round-trip taker fees, round-trip taker slippage, and the frozen 0.5 bps margin.
The checked policy requires at least 100 aggregate trades and 20 per semantic
regime, positive net and average return, profit factor at least 1.05, and a
calibrated scenario. `baseline.toml` is intentionally uncalibrated, so it can
produce diagnostic performance but must report `forecast_economic_passed=false`.
This is an early forecast-value screen, not fill evidence: retain baseline and
pessimistic HftBacktest results for queue, latency, funding, fill, inventory,
and execution-policy effects.

Revalidate retained artifacts before scoring or deployment review:

```bash
uv run aqt-research validate-model \
  --artifact-root models/research \
  --manifest models/research/challenger-20260804/fold-0.txt.manifest.json \
  --target next_mid_return_bps
```

A schema, target, dependency lineage, file size, file hash, format, feature
name, or safe-path mismatch invalidates the artifact. Retrain from immutable
inputs; do not repair the manifest manually.

## 4. Negative controls and scenario evidence

Every challenger must retain:

- the recomputed training-only target-feasibility report and outcome;
- every seeded randomized-label result and its policy-relative comparisons;
- the aggregate and causal semantic volatility-regime robustness report;
- the scenario-bound non-overlapping forecast-economic report;
- a no-signal replay with zero decisions;
- baseline and pessimistic Phase 5 scenario reports;
- fold-level post-cost metrics and an untouched walk-forward test result;
- feature drift against the frozen training baseline;
- exact code, data, schema, config, lock, model, search, validation-plan,
  scenario, metrics, controls, and report hashes.

A failed control is a failed challenger. Do not weaken a control after seeing
its result. Final holdout authorization remains under the Phase 5 frozen
selection receipt and is not performed by `run-search`.

The no-signal JSON must validate as `NoSignalControlReport` schema v2. It binds
the feature dataset, Parquet file, feature schema, strategy configuration, and
scenario hashes plus total/ready observation counts, the observation window,
and observed decision count. `run-search` re-hashes the supplied feature
manifest, strategy config, and scenario before loading a model engine, then
hashes the report into its negative-control result. Legacy or hand-authored v1
reports are rejected; the workflow never invents a passing zero.
The resulting `NegativeControlReport` is schema v4 and embeds the complete
research-control policy, target-feasibility hash/outcome, fold-derived seeds,
raw shuffled scores, validation baselines, forecast-robustness hash/outcome,
and forecast-economic hash/performance/calibration outcome. Legacy
absolute-threshold or pre-feasibility reports are rejected. Experiment
registration also rejects a v4 control report
bound to any search receipt other than the experiment's declared receipt.

## 5. Evaluate champion-challenger gates

Metrics and controls are schema-validated JSON. An optional champion requires
both its identity and metrics file.

```bash
uv run aqt-research evaluate \
  --challenger-id challenger-20260804 \
  --challenger-metrics state/research/challenger-20260804/metrics.json \
  --champion-id incumbent-paper-v1 \
  --champion-metrics state/research/incumbent-paper-v1/metrics.json \
  --policy configs/research/promotion-v1.json \
  --negative-controls state/research/challenger-20260804/controls.json
```

Exit `0` means all configured evidence gates passed. Exit `3` means a valid
report failed at least one gate. Exit `2` means input or operational failure.
A passed report authorizes at most `AWAITING_APPROVAL`; it is not a production
approval.

## 6. Register immutable evidence

Initialize one registry and submit a complete draft experiment manifest:

```bash
uv run aqt-research registry-init \
  --path state/research/native-research.duckdb

uv run aqt-research registry-register-experiment \
  --path state/research/native-research.duckdb \
  --manifest state/research/challenger-20260804/experiment.json
```

The orchestrator registers dataset, feature-schema, model, report, and later
deployment artifacts through `ResearchRegistry.register_artifact()` before it
advances the related evidence stage. An existing identity with different
content is corruption, not an update. Create a new identity/version. The final
automated transition requires the registered passing promotion report itself
as evidence and verifies its challenger, metrics, controls, and hash against
the experiment manifest.

Advance one legal stage at a time with an aware, monotonically increasing
timestamp and an evidence hash:

```bash
uv run aqt-research registry-advance \
  --path state/research/native-research.duckdb \
  --experiment-id challenger-20260804 \
  --target candidate \
  --actor automation \
  --actor-id research-worker-1 \
  --evidence-sha256 "${EVIDENCE_SHA256}" \
  --occurred-at 2026-08-04T01:00:00+00:00
```

Set `EVIDENCE_SHA256` to the lowercase SHA-256 of the retained evidence bundle;
the registry rejects missing, uppercase, or malformed values.

The CLI accepts only automation and safety-controller actors. It cannot issue
human approval. The Phase 6 registry also rejects those stages for a human API
caller until Phase 9 supplies signed deployment-approval verification.

## 7. Observability

Long-running research orchestration embeds `ResearchMetrics` in its Prometheus
registry and exposes only bounded engine, target, stage, result, strategy,
gate, and feature-set labels. Provision
`observability/grafana/dashboards/research.json` to display experiment
stages, outcomes, training duration, drift, and promotion gates.

The one-shot CLI always writes immutable JSON/registry evidence and does not
start a background metrics server. Phase 7 service deployment must expose the
collector on a dedicated research-only endpoint before treating the dashboard
as an availability monitor.

Alert or stop new research when:

- registry acquisition reports another writer;
- any artifact or experiment identity conflicts;
- drift exceeds frozen policy;
- randomized-label/no-signal controls fail;
- an operational failure is nonzero;
- a stage timestamp regresses or a transition is illegal;
- artifact validation fails after retention or restore.

## Backup and restore

1. Stop the sole writer and verify it released the `.writer.lock` file lock.
2. Copy the DuckDB file, immutable model/data artifacts, manifests, reports,
   exact commit, and `uv.lock` as one snapshot.
3. Restore into an isolated path with no writer.
4. Open the registry, confirm experiment count/stages, and validate every model
   referenced by the candidate.
5. Hash-compare restored inputs and reports before resuming at the prior stage.

Never copy a live writable DuckDB file and call it a verified backup.

## Failure and rollback

- Training failure: retain stderr and run metadata, mark the experiment failed,
  and create a new experiment identity for a retry that changes inputs.
- Schema/artifact mismatch: quarantine the artifact and retrain. Never bypass
  feature-name or hash validation.
- Registry writer crash: ensure no process owns the lock, restore the last
  verified snapshot if integrity checks fail, then replay only retained stage
  evidence.
- Excessive drift or operational failures: reject the challenger. A safety
  controller may move only toward `REJECTED`, `ROLLED_BACK`, or `RETIRED` where
  the legal graph permits.
- Phase rollback: stop research jobs and restore the prior native image and
  lock.
