# ADR 0016: Separate Paper Liveness From Operational Readiness

Status: accepted
Date: 2026-08-21

## Context

The paper container used the same probe verdict for Docker health and
operational readiness. Operational readiness correctly fails when the durable
operator kill is active, the public feed is unavailable, the heartbeat is
stale, or the service is in a terminal lifecycle state. The production-safe
deployment posture intentionally keeps the operator kill active, so Docker
reported a functioning paper process as `unhealthy`. This made the platform
dashboard unable to distinguish a dead process from a live process that was
safely inhibited.

The distinction must not weaken the risk path. Feed freshness, the operator
kill, feature warmup, execution-disabled configuration, and promotion gates
remain independent authorities.

## Decision

- Keep one dependency-light, standard-library probe and give it two explicit
  modes: `liveness` and `readiness`.
- Make Docker use `liveness`. It passes only when the atomic status contract is
  valid, its heartbeat is current, and its lifecycle is `starting`, `warming`,
  `ready`, or `degraded`.
- Preserve `readiness` as the default for operators and existing callers. It
  continues to require a `warming` or `ready` lifecycle, an executable feed,
  an inactive operator kill, and a current heartbeat.
- In both modes, fully revalidate schema-v3 status, schema-v2 feed freshness,
  signed component ages, the executable-feed verdict, the independent L2
  state, and the exact blocker. Malformed state fails both probes.
- Return the selected mode plus both computed Boolean verdicts so an operator
  cannot mistake liveness for permission to trade.
- Do not change the paper engine, simulator, strategy, risk authority,
  watchdog, kill store, account capability, configuration, or evidence gates.

## Alternatives considered

- Clear the operator kill so Docker becomes healthy: rejected because a
  monitoring concern must never disarm a deliberate safety control.
- Treat `degraded` as ready: rejected because stale or disconnected public data
  must remain fail-closed for new intents and cancel-all behavior.
- Add a second HTTP server or sidecar for liveness: rejected because it adds a
  process, port, dependency, and failure mode when the atomic status already
  contains the required evidence.
- Ignore Docker health and rely only on Prometheus: rejected because Compose
  status and infrastructure monitors would remain misleading during normal
  killed operation.
- Let any parseable status count as live: rejected because stopped, failed,
  stale, future-dated, or internally inconsistent state must remain unhealthy.

## Consequences

- Docker and the platform dashboard can report the paper process as live while
  readiness separately shows an active kill, warmup, or feed degradation.
- Liveness does not authorize an order. The kill switch, public-data risk gate,
  strategy warmup, and execution-disabled configuration remain unchanged.
- Each probe performs one bounded JSON read and constant-time contract checks;
  there is no trading-hot-path, database, model, exchange, or network work.
- Existing direct invocations retain fail-closed readiness because that remains
  the CLI default. Deployment configuration opts into liveness explicitly.
