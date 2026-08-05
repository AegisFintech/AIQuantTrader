# ADR 0008: Native and Legacy Package Isolation During Migration

Status: completed
Date: 2026-08-04
Completed: 2026-08-05

## Context

The former MT5 runtime imported a root `aiquanttrader` package. Moving the
replacement into that namespace before retirement could have mixed native and
legacy dependencies in a deployed process.

## Decision

During migration, native code used an isolated `native/` project and the
temporary import package `aiquanttrader_native`. It had an independent lockfile,
Docker context, configuration, schemas, and test suite. Cross-imports were
prohibited.

After MT5 retirement, the approved mechanical migration would:

1. remove the retired root package and runtime files;
2. move `native/src/aiquanttrader_native` to `src/aiquanttrader`;
3. move project metadata, configuration, tests, and container definitions to
   the repository root;
4. update imports and entry points without changing trading behavior;
5. rerun all native contract, replay, schema, and release tests.

## Alternatives considered

- Immediate namespace reuse was rejected because it could alter the deployed
  MT5 process during migration.
- Adding native modules to the legacy package was rejected because it erased
  the dependency and execution boundary.
- A second repository offered stronger isolation but fragmented governance,
  review, and migration history.

## Completion

Phase 10 retired the MT5/Wine runtime and completed the mechanical migration.
The canonical project now lives at the repository root and imports exclusively
from `src/aiquanttrader`. The temporary `native/` tree and legacy package no
longer exist. This ADR is retained as migration history.
