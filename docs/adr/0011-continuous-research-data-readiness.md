# ADR 0011: Continuous Research-Data Readiness

Status: accepted
Date: 2026-08-21

## Context

Short-horizon BTC research can produce attractive but meaningless results when
an interrupted capture is treated as one continuous dataset, when an older
long run hides a recent outage, or when the host cannot retain enough future
data to finish the frozen validation plan. The production validation policy
needs 70.045 days for three disjoint walk-forward folds and the sealed final
holdout. The retained deployment capture is much shorter and contains multiple
independent chains.

A readiness monitor must be safe to run beside paper trading. It must not read
labels, fit a model, select a horizon, open the final holdout, clear a kill
switch, or turn a diagnostic result into deployment authority.

## Decision

- Derive the minimum required span from every duration in the checked
  `ValidationPolicy`; do not maintain a second hand-calculated day threshold.
- Discover all finalized raw and normalized manifests from the mounted data
  root. Require one-to-one source hashes and present files with matching byte
  counts. Full file hashing remains part of offline dataset admission rather
  than the minute-level monitor.
- Split capture chains at policy-exceeding gaps, overlaps, unexplained error
  finalizations, or segments that exceed frozen critical-quality bounds. Gate
  on the latest admissible chain. Report the longest chain separately, but do
  not allow an older chain to mask a recent discontinuity.
- Re-run normal dataset admission over the latest chain. Ordinary cadence
  diagnostics remain visible but are not treated as corrupt data.
- Project the additional bytes needed for the remaining validation span from
  observed retained bytes per finalized segment-time, apply a 1.25 safety
  factor, and preserve the 5 GiB runtime reserve. This is a capacity warning,
  not an automatic deletion or storage-resize action.
- Publish an atomic typed state every 60 seconds and bounded Prometheus metrics
  on port 9114. Docker health means the monitor is running and fresh; the
  separate data-ready verdict is expected to remain false while evidence is
  accumulating.
- Make the monitor output explicitly state that model training and production
  promotion are not authorized. Existing feasibility, negative-control,
  walk-forward, paper, shadow, and human-signature gates remain mandatory.
- Mount market data read-only. The service receives no wallet, exchange
  credential, Docker socket, or order-submission capability.
- Build a dedicated dependency-light image from the pinned Python base with
  only Pydantic and Prometheus runtime packages. The monitor must not duplicate
  the much larger Nautilus, HftBacktest, PyArrow, or model-training layers.

## Alternatives considered

- Use first-to-last timestamps: inexpensive, but downtime between segments is
  falsely counted as evidence.
- Use the longest chain: useful diagnostically, but an old capture can hide a
  broken current recorder. The latest chain is the operational gate.
- Hash every Parquet and compressed segment each minute: stronger per-scan
  verification, but repeatedly reading gigabytes competes with the live
  recorder. The monitor verifies lineage and metadata; sealing performs the
  cryptographic scan once.
- Trigger research automatically when ready: convenient, but it couples
  monitoring to privileged holdout and model operations. The monitor emits a
  prerequisite verdict only.
- Ignore storage until writes fail: simple, but the current disk can exhaust
  before the 70-day policy is satisfiable. Projection makes that impossibility
  visible early without mutating the host.

## Consequences

- Interrupted history cannot silently satisfy the capture-duration gate, and
  a healthy service can honestly remain in `collecting` state for weeks.
- Every poll parses a bounded number of small manifests and stats normalized
  files. It scans directory metadata to estimate retained bytes but does not
  read Parquet payloads, decompress raw frames, load features, or affect the
  trading hot path.
- The dedicated image is about 50 MB on the deployment host, so monitoring does
  not consume the disk reserve it is responsible for enforcing.
- Growth-rate projection is conservative and changes as venue activity and
  compression change. It is an operational capacity gate, not research
  evidence or a profitability estimate.
- A transient normalizer lag may temporarily fail the one-to-one lineage gate;
  the next successful poll clears it. Monitor liveness remains independently
  observable.
- Reaching readiness permits a new immutable horizon-family audit only. It
  does not imply that any horizon is economically viable or that any model may
  be promoted.
