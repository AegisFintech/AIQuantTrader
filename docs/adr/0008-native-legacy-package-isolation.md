# ADR 0008: Native and Legacy Package Isolation During Migration

Status: accepted
Date: 2026-08-04

## Context

The deployed MT5 runtime imports the root `aiquanttrader` package. Moving that
package into the target `src/aiquanttrader` layout during Phase 2 would alter
module resolution for operational scripts and could mix native and legacy
dependencies before the replacement system is validated.

## Decision

During Phases 2-8, native code is an isolated project under `native/` with the
import package `aiquanttrader_native`. It has its own Python version, lockfile,
virtual environment, Docker build context, configuration, schemas, and tests.
It cannot import the legacy package, and legacy code cannot import the native
package. Root pytest discovery remains scoped to `tests/`; the native project
owns and runs `native/tests/` from its independently locked environment.

After Phase 9 disables and archives MT5, a dedicated mechanical migration will:

1. remove the retired root package;
2. move `native/src/aiquanttrader_native` to `src/aiquanttrader`;
3. move the native project metadata and lockfile to the repository root;
4. update imports and entry points with no behavioral changes;
5. rerun all native replay, contract, and release tests.

## Alternatives considered

- Move the legacy package to `src/` immediately: rejected because it changes the
  deployed runtime during a foundation phase.
- Place native modules inside the legacy package: rejected because dependencies,
  import paths, and process ownership would no longer be isolated.
- Use a second repository: strong isolation, but it splits migration history,
  governance, review, and eventual archival without a present operational need.

## Consequences

- The interim tree differs deliberately from the final target tree.
- Native containers and CI cannot accidentally resolve legacy modules.
- Final package renaming is postponed until no deployed legacy consumer exists.
- Any cross-import between `aiquanttrader` and `aiquanttrader_native` is a CI
  failure and architecture violation.
