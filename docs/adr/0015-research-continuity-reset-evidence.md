# ADR 0015: Research Continuity-Reset Evidence

Status: accepted
Date: 2026-08-21

## Context

ADR 0011 correctly gates the horizon audit on the latest uninterrupted capture
chain, but its schema-v1 output exposes only a total reset count. When the
latest span returns to zero, operators cannot tell from the typed state or
Grafana whether the cause was an exchange/recorder error, a gap beyond policy,
an overlap, or a segment that failed frozen quality bounds. Diagnosing that
question with an ad hoc scan is slow and makes the 70-day collection clock hard
to operate.

The deployment evidence at adoption contained 668 paired segments and 225
resets: 210 followed error-finalized segments, five were quality-ineligible,
and ten were non-error gaps above 30 seconds. The latest reset followed a
disk-pressure finalization before the EBS expansion. These are historical
facts, not permission to weaken continuity or splice chains.

## Decision

- Upgrade the readiness report and state to schema v2.
- Attribute every reset to exactly one bounded primary cause, in precedence
  order: current segment quality failure, overlap, unexplained positive gap
  after an error finalization, or a gap above the frozen limit.
- Retain the latest reset boundary with its signed gap and previous segment
  finalization reason. Do not attach segment IDs to Prometheus labels.
- Require the four reason counts to be complete, unique, and equal the total
  reset count. Bind them and the latest boundary into the report hash.
- Export bounded metrics for cause counts, latest reset identity/time/gap, and
  current-chain start time. Clear the single info series before publishing a
  changed latest cause.
- Show uninterrupted duration, time since reset, reset identity, and cumulative
  causes on the research dashboard and in the CLI summary.
- Preserve the existing latest-chain duration gate, 30-second gap policy,
  validation plan, final holdout, storage projection, and explicit false
  training/production authorities.

## Alternatives considered

- Infer causes from logs: logs can rotate and are not bound into readiness
  evidence; this would make the dashboard dependent on a second data source.
- Export segment IDs as metric labels: useful for ad hoc lookup, but unbounded
  cardinality would grow continuously. Exact boundaries remain in typed state.
- Count every applicable cause: richer diagnostics, but totals would exceed the
  number of resets and make alerting ambiguous. One deterministic primary cause
  plus the previous finalization reason preserves both accounting and context.
- Ignore prior resets and expose only current span: cheaper, but it hides
  recurring recorder failure patterns that should block confidence in a long
  capture.
- Relax the 70-day or 30-second policies after a reset: faster cosmetically,
  but invalidates the frozen purged walk-forward design and is rejected.

## Consequences

- Operators can identify why the evidence clock restarted without reading raw
  manifests or changing the market-data service.
- Evaluation remains linear in manifest count and adds only constant-size
  counters plus one boundary object. There is no trading-hot-path, label,
  model-training, or network work.
- The report identity changes, so consumers and the dependency-light health
  probe require schema v2. Storage preflight continues to consume the typed
  model and therefore receives the migration automatically.
- Historical cause totals are descriptive. A clean current streak must still
  reach the full frozen duration before a new horizon audit is allowed.
