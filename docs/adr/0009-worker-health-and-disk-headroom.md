# ADR 0009: Explicit Worker Health and Post-Build Disk Headroom

Status: accepted
Date: 2026-08-05

## Context

The first Debian 13 ARM64 deployment showed two operational ambiguities. The
normalizer inherited the runtime image's HTTP healthcheck even though it does
not serve HTTP, and the image build cache could consume enough of a small root
filesystem to trip the recorder's five-GiB free-space guard. The recorder
correctly failed closed, but a continuously unhealthy normalizer and a restart
loop are not acceptable production signals.

## Decision

- Every long-running worker publishes its own atomic, typed heartbeat instead
  of inheriting an unrelated image healthcheck.
- The normalizer healthcheck accepts only a fresh `running` state. Starting,
  completed, stopped, failed, missing, malformed, and stale states are not
  ready.
- The normalizer state records bounded batch counters and an exception class,
  never payloads, paths, credentials, or unbounded labels.
- A deployment must inspect the successfully built image, prune disposable
  builder cache, and verify free space before starting the recorder.
- The container build installs locked third-party dependencies before copying
  source, then installs the project wheel as a small final layer. Code-only
  releases therefore share the large immutable dependency layer.
- The recorder's configured absolute and fractional disk floors remain hard
  gates. Operators must add capacity or remove disposable artifacts; they must
  not lower the floor to force startup.

## Alternatives considered

- Disable normalizer healthchecks: container process state provides basic
  liveness, but cannot distinguish a stale worker from a healthy polling loop.
- Probe the normalizer process with `pgrep` or `kill -0`: this couples health to
  process names/PIDs and still provides no durable restart evidence.
- Reuse the recorder heartbeat: the services are intentionally independent, so
  recorder health says nothing about normalization progress.
- Keep builder cache and lower the disk threshold: faster rebuilds on the same
  host trade away the raw archive's safety margin and can cause a real
  disk-exhaustion incident.
- Install the project and dependencies in one source-dependent layer: simpler,
  but each code change produces another multi-GiB image layer and makes local
  rollback impractical on a bounded host.
- Move Docker storage immediately to a second volume: preferable for a larger
  production node, but unavailable on this host and unnecessary for the
  credential-free acceptance soak once disposable build cache is removed.

## Consequences

- Docker reports recorder and normalizer readiness independently and restarts a
  failed process without masking stale state.
- One atomic state write occurs per normalizer polling cycle. At the default
  five-second cadence this overhead is negligible compared with Parquet and
  DuckDB work.
- Pruning builder cache makes later builds slower because dependencies may need
  to be downloaded again. Immutable dependency layers can still be shared by
  the current and rollback runtime images; data volumes are retained.
- A host with insufficient post-build headroom remains unable to record until
  capacity is added or disposable storage is reclaimed.
