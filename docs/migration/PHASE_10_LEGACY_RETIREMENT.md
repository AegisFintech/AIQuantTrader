# Phase 10: Legacy MT5 Retirement and Native Repository Cutover

Status: evidence contracts, independent native-production and final-archive
assembly, and offline approval verification implemented; retirement is not
authorized and the deployed MT5 runtime remains unchanged

Phase 10 is a separately approved operational migration after native production,
not a side effect of Phase 9 admission. It stops the MT5/Wine deployment,
proves that it remains unnecessary through a reversible observation window,
then removes its host and repository surface through a mechanical cleanup.

## Safety boundary

Phase 10 has two independent human approvals:

1. `stop_and_observe` binds a passing readiness report, native production
   identity, final MT5 archive, `mt5-final` commit, and a maximum 24-hour action
   window. It authorizes only the exact disable procedure.
2. `remove_and_clean` binds a passing disabled-observation report and an exact,
   traversal-free, glob-free cleanup manifest. It authorizes only those listed
   targets.

`aqt-retirement` evaluates immutable evidence, canonicalizes approval bytes,
independently assembles and replays exact evidence bundles, validates cleanup
manifests, and verifies detached Ed25519 signatures. It has no process-manager,
broker, exchange, network, package-manager, credential-store, tag-creation, or
file-deletion capability. Evidence outputs require absolute paths and are
created atomically without overwriting an existing file. A passing report means
`AWAITING_*_APPROVAL`; it is not action authority.

No MT5 position is flattened by this phase automatically. Position closure or
responsibility transfer remains a separate explicit owner decision. A failed
native deployment leaves both venues halted and does not silently restart MT5.

## Decisions, alternatives, and tradeoffs

| Decision | Why chosen | Alternatives considered | Tradeoff and performance impact |
|---|---|---|---|
| Separate stop and cleanup approvals | Stopping is reversible while credential revocation and deletion are not; splitting authority prevents a readiness report from authorizing irreversible cleanup. | One cutover approval was simpler but coupled two different blast radii and removed the observation gate. | Adds one offline review and at least seven days of elapsed time; it adds no trading hot-path latency. |
| Evidence-only CLI with no executor | A parser, evaluator, or signing mistake cannot directly stop services or erase evidence. Operational targets remain visible to a human at action time. | A fully automated decommission controller was rejected because it would need host root, secret-store, package-manager, and process-manager authority in one component. | More operator work and slower cutover; evaluation is bounded offline JSON/TOML work and is irrelevant to live execution performance. |
| Exact glob-free cleanup manifest | Every destructive target and its expected file/tree, package, integration, or revocation state are hash-bound and reviewable, so changed targets cannot be silently swept into deletion. | Directory globs and recursive repository-root cleanup were rejected as unauditable and unsafe in a dirty or evolving workspace. | The manifest requires maintenance and can contain many entries; validation is linear in at most 2,048 targets and off the hot path. |
| 30-day native, seven-day disabled, 365-day archive baseline | Native operation must span meaningful uptime before removing the independent legacy deployment; a full disabled week covers restarts and scheduled jobs; one-year evidence supports incident and financial audit. | Immediate cutover, 24-hour observation, and indefinite retention were respectively too risky, too short for weekly behavior, and operationally unbounded. | Delays storage reclamation and consumes archive capacity. A newly reviewed policy may be stricter, but cannot be weakened after its observation begins. |
| Restore-tested category archives rather than loose files | Category bundles have bounded identities, independent hashes, retention ownership, and practical restore checks without forcing one very large artifact. | One monolithic archive makes partial verification expensive; loose files make completeness difficult to prove. | Requires eleven hashes and restore checks. Archive work is offline and has no market-data or order latency impact. |
| Externally executed, policy-pinned recursive credential scan | A separately reviewed scanner can cover private-key, token, password, seed-phrase, and session formats without granting this verifier secret-store access. The required detector set, recursion, zero-finding threshold, scanner name/version, and result hashes are retained for review. | Embedding a regex-only scanner was incomplete; trusting an unpinned operator summary was not reproducible. | Scanner execution and its independent review remain operator prerequisites. Verification is bounded metadata and artifact hashing, outside the trading path. |
| Ed25519 detached approvals using the existing governance primitive | Small deterministic signatures, offline signing, pinned public-key fingerprints, and existing operational familiarity reduce implementation divergence. | Unsigned tickets and online signing services were rejected because they are forgeable or add network/trust dependencies. | Requires offline key custody and canonicalization; verification cost is negligible and never enters the trading hot path. |
| ADR 0008 package migration only in the removal PR | Keeping `aiquanttrader_native` isolated prevents legacy imports and dependency resolution from changing while MT5 is deployed. | Renaming before retirement was rejected because it could alter the active legacy Python runtime. | Defers the final clean layout and creates a mechanical rename diff, with no runtime performance penalty. |

## Implemented contracts

The `aiquanttrader_native.retirement` package provides:

- exact-inventory native-production assembly which independently verifies the
  pinned signer, deployed release, checkpointed admission ledger, complete
  signed renewal chain, typed operational audits, incident register, and
  frozen drill checks;
- exact-inventory assembly and independent replay of an eleven-category,
  credential-free final archive manifest bound to isolated-restore checks,
  recursive scan evidence, retention, and annotated `mt5-final` tag lineage;
- a native-production observation bound to its deployment, admission, approval,
  terminal chained authorization, renewal count/expiry, artifact manifest,
  drills, and evidence bundle;
- a final MT5 demo-account state with managed and unmanaged positions, pending
  orders, entry pause, command writers, and archived report hashes;
- a thirteen-gate readiness report which can only await stop approval;
- an exact ten-capability disabled observation covering PM2, cron, nginx,
  logrotate, autostart, Wine/MT5, and command-file writers;
- an eight-gate disabled-window report which can only await cleanup approval;
- short-lived, scope-specific retirement approvals and detached Ed25519
  verification against an operator-supplied trust-root fingerprint;
- cleanup manifests whose targets reject traversal, globs, broad host roots,
  duplicate locators, non-revocation actions for secret references, and bind an
  expected state hash for every target.

The contracts are exported in
`native/schemas/retirement.schema.json`. The checked-in policy is
`native/configs/retirement/evidence-v1.toml`; it pins the canonical identity of
`native/configs/retirement/archive-credential-scan-v1.toml`.

## Frozen baseline policy

The v1 policy requires at least:

- 30 days of stable native production after policy freeze;
- successful sentinel/dead-man evidence spanning that production interval with
  no gap over five minutes;
- an unbroken signed production-authorization chain whose terminal expiry is
  later than the observation end;
- every native rollback, backup/restore, alert-delivery, and operator-access
  drill;
- zero critical incidents, reconciliation failures, or risk breaches in the
  evidence window;
- a restore-tested final archive retained for at least 365 days, with all
  eleven categories covered by a policy-pinned recursive scan reporting zero
  credential findings;
- the MT5 account to be demo-only, entry-paused, flat, and free of command-file
  writers;
- seven full days with every legacy capability disabled, zero active instances,
  zero post-stop broker orders, stable native operation, reverified archives,
  and quarantined legacy credentials.

A future policy change must be reviewed and frozen before its observation
begins. Thresholds are never loosened after observing results.

## Evidence flow

See
[`phase-10-legacy-retirement.mmd`](../architecture/diagrams/phase-10-legacy-retirement.mmd).

```text
native production evidence + exact archive/restore/scan/tag evidence
  -> independent archive assembly and replay
final MT5 state + verified archive
  -> readiness evaluation
  -> signed stop_and_observe approval
  -> exact operator disable procedure
  -> seven-day disabled observation
  -> signed remove_and_clean approval + exact cleanup manifest
  -> mechanical host/repository cleanup
  -> native package-root migration and full native revalidation
```

## Commands

All inputs are operator-created retained evidence. These commands do not collect
live facts or perform actions.

```bash
aqt-retirement assemble-native \
  --evidence-root /absolute/evidence/native-production \
  --policy native/configs/retirement/evidence-v1.toml \
  --approval-key-id <independently-recorded-key-id> \
  --approval-public-key-sha256 <independently-pinned-fingerprint> \
  --output /absolute/evidence/native-production-observation.json

aqt-retirement verify-native \
  --evidence-root /absolute/evidence/native-production \
  --policy native/configs/retirement/evidence-v1.toml \
  --approval-key-id <independently-recorded-key-id> \
  --approval-public-key-sha256 <independently-pinned-fingerprint> \
  --observation /absolute/evidence/native-production-observation.json

aqt-retirement assemble-archive \
  --evidence-root /absolute/evidence/legacy-archive-bundle \
  --policy native/configs/retirement/evidence-v1.toml \
  --credential-scan-policy native/configs/retirement/archive-credential-scan-v1.toml \
  --output /absolute/evidence/legacy-archive-manifest.json

aqt-retirement verify-archive \
  --evidence-root /absolute/evidence/legacy-archive-bundle \
  --manifest /absolute/evidence/legacy-archive-manifest.json \
  --policy native/configs/retirement/evidence-v1.toml \
  --credential-scan-policy native/configs/retirement/archive-credential-scan-v1.toml

aqt-retirement evaluate-readiness \
  --observation /absolute/evidence/readiness-observation.json \
  --policy native/configs/retirement/evidence-v1.toml \
  --output /absolute/evidence/readiness-report.json

aqt-retirement canonicalize-approval \
  --input /absolute/offline/retirement-approval-draft.json \
  --output /absolute/offline/retirement-approval.canonical.json

aqt-retirement verify-approval \
  --approval /absolute/offline/retirement-approval.canonical.json \
  --signature /absolute/offline/retirement-approval.sig.json \
  --public-key /absolute/trust/retirement-approver.pub \
  --public-key-sha256 <pinned-fingerprint> \
  --key-id <approved-key-id> \
  --expected-retirement-id <retirement-id> \
  --expected-scope stop_and_observe \
  --expected-report-sha256 <readiness-report-sha256> \
  --expected-native-deployment-id <deployment-id> \
  --expected-native-admission-id <admission-id> \
  --expected-archive-manifest-sha256 <archive-manifest-sha256> \
  --expected-source-commit-sha <mt5-final-commit>

aqt-retirement evaluate-disabled \
  --observation /absolute/evidence/disabled-observation.json \
  --policy native/configs/retirement/evidence-v1.toml \
  --output /absolute/evidence/disabled-report.json

aqt-retirement validate-cleanup-manifest \
  --manifest /absolute/evidence/cleanup-manifest.json \
  --output /absolute/evidence/cleanup-manifest.canonical.json
```

Cleanup approval uses the same verifier with
`--expected-scope remove_and_clean` and the canonical cleanup-manifest hash.
Signing remains an offline operator responsibility; no signer is included.

## Repository delta after cleanup approval

The cleanup PR is deliberately mechanical:

1. remove the exact legacy source, PM2, MT5/Wine, XAU research, Streamlit,
   cron/nginx/logrotate, legacy data, and owned tests listed in the approved
   cleanup manifest and `FILE_DISPOSITION.md`;
2. move `native/src/aiquanttrader_native` to `src/aiquanttrader` and native
   metadata to the repository root as specified by ADR 0008;
3. update imports, entry points, Compose paths, CI, documentation, and operator
   commands without strategy or risk-policy changes;
4. retain the immutable archive, signed approvals, reports, cleanup manifest,
   Git history, and `mt5-final` tag outside the mutable runtime;
5. run all native schema, replay, risk, research, paper, shadow, governance,
   container, security, and documentation gates before merge.

The removal PR must contain no alpha, model, execution, capital, or risk-limit
change. New behavior requires a separate PR.

## Tests

Unit tests cover exact production inventories, signature and trust-root
tampering, release hashes, SQLite integrity, renewal terminal consistency,
typed audit derivation, sentinel continuity, drill check sets, archive
completeness and binding, restore equality, recursive detector coverage,
credential-policy and tag-lineage tampering, retention, path and symlink safety,
exact gate sets, stable report identities, approval scope/expiry, broad cleanup
targets, and failed observations. Integration tests exercise native and archive
CLI assembly, independent replay, canonicalization, and verification and prove
there is no `stop` action command.
Schema determinism and repository documentation checks remain part of native
CI.

## Migration and rollback

The forward procedure is the
[`Legacy MT5 Retirement Runbook`](../operations/LEGACY_RETIREMENT_RUNBOOK.md).
The exact final-archive contract and operator handoff are documented in
[`Phase 10: Final Legacy Archive`](PHASE_10_LEGACY_ARCHIVE.md).
Before the cleanup approval, rollback means leaving native safe and halted while
the owner decides whether to issue new, explicit MT5 reactivation authority.
After cleanup, recovery uses the immutable archive and `mt5-final` tag in a
demo-first rehearsal; it is not an automated production fallback.
