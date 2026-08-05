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

## 2. Construct causal labels

The labeling job must write a NumPy NPZ with exactly these arrays:

- `features`: two-dimensional finite float64 matrix in
  `btc-microstructure-v1` order;
- `labels`: one-dimensional finite float64 target;
- `sample_ts_ns`: strictly increasing observation receipt time;
- `label_end_ts_ns`: time at which each label becomes fully known and strictly
  later than its sample time;
- `feature_schema_sha256`: scalar string equal to the checked-in schema hash;
- `source_dataset_sha256`: scalar lowercase SHA-256 bound by the validation
  plan.

NumPy pickle loading is disabled. Normalization or calibration must be fit
inside each train fold; do not pre-normalize against validation, test, final
holdout, or future rows. Document the target horizon and economic meaning in
the experiment manifest.

## 3. Run bounded fold research

The checked-in LightGBM policy is a bounded seed, not evidence that its ranges
are optimal. Run each fold separately and use a model-native extension:

```bash
uv run aqt-research run-search \
  --matrix data/research/matrices/next-mid-return-v1.npz \
  --validation-plan data/research/plans/walk-forward-v1.json \
  --fold 0 \
  --policy configs/research/search-lightgbm-v1.json \
  --engine lightgbm \
  --target next_mid_return_bps \
  --artifact-root models/research \
  --artifact-path challenger-20260804/fold-0.txt \
  --dependency-lock uv.lock \
  --created-at 2026-08-04T00:00:00+00:00 \
  --randomized-label-minimum-mse 1.0 \
  --randomized-seed 20260804 \
  --no-signal-report state/research/challenger-20260804/no-signal.json \
  --output state/research/challenger-20260804/fold-0.json
```

Use `.json` for XGBoost and `.cbm` for CatBoost. Do not use pickle, joblib, or
an arbitrary callback/object parameter. A trial policy can declare at most 64
trials, and adapters reject parameters outside their fixed allowlists/bounds.

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

- the seeded randomized-label result and configured minimum error;
- a no-signal replay with zero decisions;
- baseline and pessimistic Phase 5 scenario reports;
- fold-level post-cost metrics and an untouched walk-forward test result;
- feature drift against the frozen training baseline;
- exact code, data, schema, config, lock, model, search, validation-plan,
  scenario, metrics, controls, and report hashes.

A failed control is a failed challenger. Do not weaken a control after seeing
its result. Final holdout authorization remains under the Phase 5 frozen
selection receipt and is not performed by `run-search`.

The no-signal JSON must validate as `NoSignalControlReport`: it binds feature
dataset, strategy configuration, and scenario hashes plus a positive
observation count and the observed decision count. `run-search` hashes that
report into its negative-control result; it does not invent a passing zero.

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
