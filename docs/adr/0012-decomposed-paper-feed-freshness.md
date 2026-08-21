# ADR 0012: Decomposed Paper-Feed Freshness

Status: accepted
Date: 2026-08-21

## Context

The paper risk gate intentionally requires more than an open Hyperliquid
WebSocket. A current raw frame, fresh mark/funding asset context, and a current
usable L2-derived market state must all fit inside the 1.5-second public-data
limit. The recorder already exposed socket state while paper exposed only the
combined verdict. When those gauges differed, an operator could see that paper
was degraded but not which required input was stale.

The explanation must remain safe for the trading path: bounded cardinality,
constant work, no network calls, no relaxed threshold, and no alternate source
of risk authority.

## Decision

- Track recorder socket transitions through a synchronous in-process callback.
  Socket state is not inferred from frame age.
- At every atomic paper-status write, derive signed ages for the latest public
  frame, asset context, and usable market state. Use the stricter of recorder
  and risk freshness limits.
- Treat missing, over-limit, and negative ages as separate fail-closed states.
  A negative age indicates wall-clock regression and is never silently clamped
  to zero.
- Store a schema-v2 typed status projection whose combined `feed_connected`
  value must equal the component verdict and whose checked time must equal the
  service heartbeat.
- Publish fixed-label component gauges and a one-hot blocker gauge. Timestamps,
  connection IDs, and free-form errors never become labels.
- Keep the standard-library health probe independent of the trading dependency
  graph, but make it revalidate the same age, verdict, and blocker invariants.
- Leave the 1.5-second limit, cancel-all behavior, strategy gates, operator
  kill, execution capability, and promotion process unchanged.

## Alternatives considered

- **Use only WebSocket connection state.** This misses a silent or partial
  subscription where books continue but the risk mark is stale.
- **Use only time since any frame.** Control or trade traffic can remain active
  while asset context or a usable L2 state is absent.
- **Add dynamic labels for error detail.** This makes incidents convenient to
  query but creates unbounded Prometheus cardinality. A closed enum is safer.
- **Increase the stale threshold to suppress dashboard flapping.** This hides
  real latency and changes risk. Attribution is added instead of weakening the
  gate.
- **Read recorder state from disk on each paper cycle.** That crosses a durable
  I/O boundary in the hot path and can race the in-process connection. A
  constant-time callback carries only the current Boolean.

## Consequences

- Grafana distinguishes an open socket from missing/stale frames, asset
  context, or usable market state and displays each age against the risk limit.
- Status schema v1 is superseded. The service writes schema v2 before becoming
  ready, and both full and lightweight health checks reject malformed v2
  projections.
- The first blocking reason uses deterministic priority: socket, frame,
  context, then market. Every component remains visible even when an earlier
  condition is the primary block.
- Work per status update is constant: three integer subtractions, bounded enum
  selection, and fixed Prometheus series. No Parquet, DuckDB, exchange, or
  model operation is introduced.
- The additional metrics improve diagnosis but do not prove exchange quality,
  profitability, fill calibration, or promotion eligibility.
