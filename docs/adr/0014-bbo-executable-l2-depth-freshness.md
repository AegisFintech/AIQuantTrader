# ADR 0014: Separate Executable BBO and L2-Depth Freshness

Status: accepted
Date: 2026-08-21

## Context

Paper readiness originally used the last full `l2Book`-derived market state as
the executable-quote heartbeat. A production observation showed why those are
different contracts: during a 45-second direct mainnet sample, BTC delivered
416 BBO frames but only nine full L2 snapshots, with a maximum L2 interarrival
gap of 5.535 seconds. The unchanged public-data risk limit is 1.5 seconds and
the feature input-age limit is two seconds. Treating the slower L2 snapshot as
the executable heartbeat therefore caused intermittent false disconnects.

Simply extending the L2 lifetime would be unsafe. A recent BBO proves current
top-of-book prices and sizes, but it does not prove that deeper levels from an
older snapshot are unchanged. The live path also needs a bounded processing
cadence: the observed BBO rate was about 9.2 frames per second, while the
adaptive forecast and retained paper evidence use one-second samples.

## Decision

- A valid Hyperliquid BBO is the executable top-of-book source and the final
  required price component in the 1.5-second feed-ready verdict.
- Full L2 depth has a separate age and state: `missing`, `clock_regression`,
  `stale`, or `fresh`. Its limit remains the feature configuration's existing
  `maximum_input_age_ns`; depth age does not masquerade as BBO age.
- Every valid BBO refreshes the readiness observation. Engine states are
  emitted at the configured one-second cadence, which must remain faster than
  the public-data risk limit. Trades remain buffered between emitted states.
- When L2 depth is within its existing source-age bound, the assembler replaces
  its top level with the newer BBO and retains only non-crossing deeper levels.
  When depth is older, it emits a BBO-only one-level state. It never relabels
  stale deeper levels as current.
- A fresh L2 snapshot may still emit a full-depth state, preserving retained
  L2-only replay and the shared HftBacktest/Nautilus kernel boundary.
- Paper status advances to schema v3 and nested feed freshness to schema v2.
  The standard-library health probe revalidates both the executable verdict
  and the independent depth classification.
- Fixed-cardinality metrics expose BBO/L2 ages, both limits, the latest state's
  depth count and whether it incorporated L2, plus stale BBO exclusions.
- Execution remains disabled and the durable operator kill is unaffected.

## Alternatives considered

- **Raise the 1.5-second risk limit above the observed L2 gap.** Rejected: this
  weakens executable-price safety to accommodate an unrelated depth cadence.
- **Refresh the entire book on any public frame or BBO.** Rejected: trades,
  context, and top-of-book changes cannot prove that deeper queues are current.
- **Keep using only full L2 snapshots.** Rejected: it makes feed health and
  position management inherit the slower snapshot cadence despite a current
  exchange-native BBO stream.
- **Use only BBO and discard L2 permanently.** Rejected: current depth remains
  useful for imbalance, queue, VAMP, and fill features when its lineage is
  within the existing age limit.
- **Process and journal every BBO.** Rejected: it increases SQLite and feature
  work roughly ninefold without improving the frozen 1 Hz forecast sample.
- **Poll the REST L2 endpoint between snapshots.** Rejected: polling adds rate
  limits, request latency, a second ordering domain, and unnecessary network
  work to a WebSocket-first hot path.

## Consequences

- A slower full-depth stream no longer makes a current executable quote appear
  disconnected. Missing or stale BBO, context, frames, or socket state still
  fails closed and initiates the existing risk behavior.
- Depth-dependent values may be computed from top-of-book only while the L2
  state is stale. The dashboard makes that degradation explicit; future model
  validation must preserve the same event/cadence semantics.
- Work for each inbound BBO is constant except at the bounded emission cadence,
  where at most ten levels per side are filtered. Journal/feature writes remain
  bounded to about one market state per second in total.
- Existing status consumers must upgrade atomically to schema v3. Old probes
  reject the new contract rather than silently misinterpreting it.
- This decision supersedes ADR 0012 only for the meaning of the final market
  component. ADR 0012's socket, frame, context, signed-age, fixed-cardinality,
  health-probe, and unchanged-risk decisions remain in force.
