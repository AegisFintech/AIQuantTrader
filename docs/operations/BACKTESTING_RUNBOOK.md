# Native BTC Backtesting Runbook

This runbook operates the Linux-native research path and has no live-execution
authority.

## 1. Verify the environment

From the repository root:

```bash
uv sync --frozen --group dev
uv run aqt-backtest validate-scenario --scenario configs/backtest/baseline.toml
uv run aqt-backtest validate-scenario --scenario configs/backtest/pessimistic.toml
uv run aqt-native export-schemas --output schemas --check
```

The checked-in scenarios must report `promotion_eligible: false` until reviewed
calibration evidence exists. A surprising `true` is a stop condition.

## 2. Convert one Tardis UTC day

Acquire both source types with `aqt-market-data download-tardis`, then convert
their manifest-verified absolute paths:

```bash
uv run aqt-backtest convert-tardis \
  --source-root /var/lib/aiquanttrader/data \
  --input /var/lib/aiquanttrader/data/historical/source=tardis/exchange=hyperliquid/data_type=trades/date=YYYY-MM-DD/BTC.csv.gz \
  --input /var/lib/aiquanttrader/data/historical/source=tardis/exchange=hyperliquid/data_type=incremental_book_L2/date=YYYY-MM-DD/BTC.csv.gz \
  --output-root /var/lib/aiquanttrader/data \
  --event-path backtest/source=tardis/date=YYYY-MM-DD/BTC.events.npz
```

The command refuses a missing, corrupt, mismatched-day, duplicate-type, or
non-BTC input. Keep the adjacent `.manifest.json` output.

## 3. Convert admitted local capture

First complete Phase 3 normalization and quality admission. Then pass the
dataset manifest plus every normalized segment it names:

```bash
uv run aqt-backtest convert-normalized \
  --data-root /var/lib/aiquanttrader/data \
  --dataset-manifest /var/lib/aiquanttrader/data/datasets/DATASET.manifest.json \
  --normalized-manifest /var/lib/aiquanttrader/data/normalized/manifests/SEGMENT.normalized.manifest.json \
  --output-root /var/lib/aiquanttrader/data \
  --event-path backtest/source=local/dataset=DATASET/BTC.events.npz
```

Repeat `--normalized-manifest` for all admitted segments. Missing and extra
segments fail closed. Conversion applies admitted L2 snapshots, high-frequency
BBO changes, and trades in causal receipt order; dropping BBO would make the
research book stale between the venue's periodic depth snapshots.

## 4. Freeze validation windows

```bash
uv run aqt-backtest plan-validation \
  --events /var/lib/aiquanttrader/data/backtest/DATASET/BTC.events.npz \
  --dataset-sha256 DATASET_SHA256 \
  --policy configs/backtest/validation-v1.toml \
  > /var/lib/aiquanttrader/state/research/DATASET.validation-plan.json
```

Record the policy hash before running a search. Do not edit thresholds, window
lengths, candidates, or metrics after observing validation or test results.
Validation-plan schema v2 also carries the policy's label horizon. Research
training rejects a forecast matrix whose manifest declares a different
horizon, even when its source dataset hash matches the plan.

## 5. Run replay and parity

Phase 6 research workers load the NPZ with `load_event_file()`, build one
`HftReplaySession` per scenario, and feed decisions from the shared pure kernel.
Every candidate must run against both checked-in scenario families. Record:

- commit and dependency-lock hashes;
- event manifest/dataset ID;
- scenario ID and hash;
- kernel, feature schema, and parameter hashes;
- complete fills, fees, explicit slippage, hourly funding, inventory, and
  marked equity;
- Hft/Nautilus decision parity output;
- fold, bootstrap, selection-family, and final-holdout reports.

Use `select_candidate()` only with validation metrics. Persist its receipt
before calling `authorize_holdout()`. Never calculate holdout features or
metrics for rejected challengers.

## 6. Calibrate scenarios

Calibration input must contain raw timestamps and venue identities for:

- local submit to exchange acknowledgement and local response;
- public feed exchange to local receipt;
- quote lifetime, displayed depth evolution, partial fills, and non-fills;
- effective maker/taker fees and rebates from the account endpoint/fills;
- taker order size versus observed book and realized execution price;
- hourly funding rate, oracle price, position, and actual payment.

Write a dated, immutable calibration report with code/data hashes and reviewer.
Create a new scenario version, set `calibration_state = "calibrated"`, and bind
`calibration_sha256` to that artifact. Never relabel the seed v1 files.

## 7. Required verification

```bash
uv run ruff format --check src tests scripts
uv run ruff check src tests scripts
uv run mypy
uv run pytest --cov
uv run aqt-native export-schemas --output schemas --check
uv lock --check
```

Retain the command output with the experiment. A deterministic rerun must
produce the same event-file hash, scenario hash, decisions, fills, and metrics.

## Failure handling

- Source hash mismatch: quarantine or reacquire the source. Never repair an
  immutable artifact in place.
- Local time before exchange time: stop and create a reviewed clock-offset
  calibration. Do not let the converter infer an offset.
- Crossed local book: reject the dataset and investigate normalization/order.
- Hft/Nautilus trace mismatch: reject the candidate; compare the first market
  state and decision that diverged.
- Pessimistic result improves because of less displayed depth: inspect maker
  and taker effects separately. Lower book depth can worsen taker slippage while
  a separate lower trade-flow multiplier makes passive fills harder.
- Holdout authorization failure: preserve the receipt and plan, correct the
  orchestration error, and do not bypass the guard.
- Uncalibrated scenario: research may continue, but governance must reject
  promotion evidence.

## Rollback and evidence

Stop the research worker, preserve immutable source/event manifests and partial
reports, and restore the prior native image/lock. Evidence for review includes
the exact command, commit, lock, source and output hashes, scenarios, plan,
selection receipt, reports, parity trace, test output, and reviewer identity.
