# ADR 0010: Content-Addressed Market-Data Soak Evidence

Status: accepted
Date: 2026-08-05

## Context

Phase 3 requires a sustained public-mainnet capture on the deployment host.
The prior runbook listed evidence to retain, but a manual list could select
segments from an earlier failed deployment, omit a discontinuity, conflate a
collector build with the runtime build, or call an incomplete evidence bundle
successful. CI smoke tests cannot establish deployment-host continuity, disk
headroom, or container restart behavior.

Hyperliquid public channels also have different natural cadences. A common
receive-gap diagnostic can therefore report `cadence_anomaly` without a
disconnect, stale recorder, malformed message, or dataset gap. Treating every
such diagnostic as corruption would make acceptance depend on ordinary venue
activity rather than integrity.

## Decision

- Freeze a versioned six-hour policy in
  `configs/market-data/soak-v1.toml`.
- Discover every finalized raw segment whose start falls inside the requested
  deployment window. Operators cannot provide a hand-picked manifest list.
- Cryptographically and structurally verify each raw segment, require its
  matching normalized manifest, verify every Parquet file, then run normal
  dataset admission with the frozen data-quality policy.
- Bind separate runtime and collector Git commits, the runtime image digest,
  effective configuration fingerprint, policy hash, typed recorder/normalizer
  states, a strictly parsed Prometheus snapshot, restart counts, and start/end
  disk headroom into one report.
- Return an immutable accepted or rejected report when inputs are complete.
  Missing, corrupt, malformed, or ambiguous inputs return an invalid exit and
  no report; absence can never be interpreted as acceptance.
- Require zero reconnects, process restarts, non-rotation finalizations,
  excluded frames, critical quality defects, and in-window quarantine
  artifacts for the initial production baseline. Require both disk samples to
  remain above the stricter of runtime configuration and evidence policy.
- Report cadence anomalies but do not classify them as corruption. Actual
  silence timeouts, stale state, reconnects, timestamp regressions, schema
  failures, crossed books, duplicates, disk pressure, and unexplained gaps are
  gated.
- Keep evaluation offline and free of Docker-socket, network, wallet, SDK, and
  order-submission capability.
- Keep incomplete-artifact recovery out of the continuous normalizer. Only the
  explicit offline `recover` command may move `.partial` files, preventing a
  cross-process rename of the recorder's active segment.

## Alternatives considered

- Retain shell output and review it manually: flexible, but selection and
  arithmetic are not reproducible or content-addressed.
- Accept an operator-provided manifest list: simpler, but permits accidental or
  deliberate omission of a failed in-window segment.
- Query the DuckDB catalogs only: fast, but a catalog is metadata and cannot
  replace verification of raw bytes, normalized manifests, and Parquet hashes.
- Make the evaluator call Docker and Prometheus directly: more automated, but
  grants deployment-control and network capabilities to an evidence process,
  complicates deterministic replay, and couples the contract to one runtime.
- Gate all cadence anomalies at zero: superficially strict, but sparse public
  channels legitimately cross the shared threshold and do not imply lost or
  invalid data.
- Combine runtime and collector identity: adequate only when the same artifact
  both records and evaluates. Separate identities preserve the audit trail when
  a later reviewed collector evaluates an already-running soak.

## Consequences

- Acceptance is deterministic from retained files and explicit host
  observations. The report ID changes if any bound fact or gate changes.
- Evidence generation reads and decompresses every selected raw segment and
  hashes every normalized file. This is intentional offline I/O after the soak;
  it adds no recorder hot-path latency and uses bounded memory.
- A normalizer that has not caught up produces an invalid evaluation. Operators
  may wait and rerun; they may not waive missing lineage.
- The initial zero-reconnect policy may require repeating a soak after a benign
  venue or network interruption. That cost establishes a clean baseline and
  can be changed only through a reviewed policy revision.
- A discovered recorder/normalizer ownership race invalidates the soak; it is
  fixed and regression-tested instead of being reclassified as an acceptable
  reconnect.
- Docker health and restart observations remain explicit operator inputs, while
  state/counter/data consistency prevents a single self-reported value from
  satisfying the overall verdict.
