# Phase 10: Cleanup Manifest Evidence

Status: evidence-derived cleanup-manifest assembly and independent replay are
implemented. No stop, revocation, package removal, file removal, host cleanup,
or repository cleanup is authorized or performed.

## Purpose

The cleanup approval is potentially irreversible. A syntactically valid,
hand-authored list is therefore insufficient: it can omit a legacy surface,
claim an unverified state, or include a shared host package. The cleanup
manifest is now derived from an immutable, exact-inventory evidence bundle and
can be reproduced byte-for-byte by an independent reviewer.

The flow is shown in
[`phase-10-cleanup-manifest.mmd`](../architecture/diagrams/phase-10-cleanup-manifest.mmd).

## Bundle contract

The evidence root contains only these declared files:

```text
cleanup-evidence/
|- cleanup-evidence.json
|- controls/
|  |- inventory-audit.json
|  |- credential-scan.json
|  `- targets/<target-id>.json
`- raw/
   |- repository/<exact-state-inventory>
   |- runtime/<exact-state-inventory>
   |- host/<installed-state-and-ownership-evidence>
   |- credentials/<provider-record-and-session-inventory>
   `- inventory/<scope-completeness-evidence>
```

`cleanup-evidence.json` binds every byte, its length, capture time, the frozen
retirement and credential-scan policies, `mt5-final` source commit, final
archive, and passing disabled-observation report. Undeclared files, missing
files, symlinks, changed files, group/world-writable files, path traversal, and
non-canonical controls fail assembly.

The independent inventory audit covers exactly these scopes, including an
evidence-backed absence when a scope is not present:

- MQL5 source;
- legacy MT5/Wine lifecycle;
- legacy XAU research;
- PM2, dashboard, cron, nginx, logrotate, and related operations;
- legacy-owned tests and documents;
- runtime state;
- host dependencies;
- credentials and broker sessions;
- the ADR 0008 native package migration.

Every cleanup target belongs to exactly one scope. The union of scoped target
IDs must equal the target-control inventory, so a target cannot be orphaned or
counted twice.

## Target state

Each target control is collected and reviewed by different people, contains no
invalidating event, and derives one `LegacyCleanupTarget`:

| State | Required facts | Expected-state hash |
|---|---|---|
| Repository/runtime path | Exact locator, object type, entry count, byte count, mode, per-entry state hashes, and a typed ordered file/tree inventory; repository inventories bind `mt5-final` | Canonical inventory-state SHA-256 excluding capture time |
| Host integration/package | Installed version, configuration state, ownership evidence, project ownership, and zero shared consumers | Canonical hash of installed/configuration/ownership state |
| Secret reference/session | Provider identifier, hashed provider record identity, provider state, and active-session inventory; never secret material | Canonical hash of provider/session state |

The schema-v3 target rules still reject globs, shell operators, traversal,
unresolved variables, broad host roots, invalid actions, duplicate identities,
duplicate locators, and duplicate migration destinations. `migrate_native`
targets must name one distinct safe repository destination before approval;
other actions cannot name a destination. Host dependencies cannot enter the
manifest without explicit ownership and zero-shared-consumer evidence.

Immediately before any separately authorized action, an operator must capture
the same state shape and compare its hash to `expected_state_sha256`. Capture
time is retained as audit metadata but deliberately excluded from the stable
state fingerprint, so unchanged state can compare equal. A changed state
invalidates both manifest and approval. The separate
[`PHASE_10_CLEANUP_PREFLIGHT.md`](PHASE_10_CLEANUP_PREFLIGHT.md) workflow
consumes a newly reviewed complete bundle and automates that replay and
comparison. Neither module implements an action-time collector or a destructive
executor.

## Credential scan

The cleanup bundle reuses the separately frozen recursive detector policy. Its
zero-finding scan must cover every raw artifact plus every inventory/target
control. The scan record and top-level manifest contain typed identifiers and
hashes only, so they cannot carry a credential value. The scan cannot silently
exclude an artifact because its exact checked paths must equal the manifest
inventory.

## Commands

Assemble into a new absolute output path outside the immutable evidence root:

```bash
aqt-retirement assemble-cleanup-manifest \
  --evidence-root /absolute/evidence/cleanup-evidence \
  --disabled-evidence-root /absolute/evidence/disabled-evidence \
  --native-evidence-root /absolute/evidence/native-production \
  --legacy-evidence-root /absolute/evidence/legacy-archive \
  --readiness-observation /absolute/evidence/readiness-observation.json \
  --readiness-report /absolute/evidence/readiness-report.json \
  --native-observation /absolute/evidence/native-production-observation.json \
  --archive-manifest /absolute/evidence/legacy-archive-manifest.json \
  --stop-approval /absolute/offline/stop-approval.json \
  --stop-signature /absolute/offline/stop-approval.sig.json \
  --stop-public-key /absolute/trust/retirement-approver.pub \
  --disabled-observation /absolute/evidence/disabled-observation.json \
  --disabled-report /absolute/evidence/disabled-report.json \
  --policy native/configs/retirement/evidence-v1.toml \
  --credential-scan-policy native/configs/retirement/archive-credential-scan-v1.toml \
  --native-approval-key-id <pinned-native-key-id> \
  --native-approval-public-key-sha256 <pinned-native-fingerprint> \
  --stop-approval-key-id <pinned-stop-key-id> \
  --stop-approval-public-key-sha256 <pinned-stop-fingerprint> \
  --output /absolute/evidence/cleanup-manifest.json
```

A different reviewer replays the same sources:

```bash
aqt-retirement verify-cleanup-manifest \
  --evidence-root /absolute/evidence/cleanup-evidence \
  --disabled-evidence-root /absolute/evidence/disabled-evidence \
  --native-evidence-root /absolute/evidence/native-production \
  --legacy-evidence-root /absolute/evidence/legacy-archive \
  --readiness-observation /absolute/evidence/readiness-observation.json \
  --readiness-report /absolute/evidence/readiness-report.json \
  --native-observation /absolute/evidence/native-production-observation.json \
  --archive-manifest /absolute/evidence/legacy-archive-manifest.json \
  --stop-approval /absolute/offline/stop-approval.json \
  --stop-signature /absolute/offline/stop-approval.sig.json \
  --stop-public-key /absolute/trust/retirement-approver.pub \
  --disabled-observation /absolute/evidence/disabled-observation.json \
  --disabled-report /absolute/evidence/disabled-report.json \
  --policy native/configs/retirement/evidence-v1.toml \
  --credential-scan-policy native/configs/retirement/archive-credential-scan-v1.toml \
  --native-approval-key-id <pinned-native-key-id> \
  --native-approval-public-key-sha256 <pinned-native-fingerprint> \
  --stop-approval-key-id <pinned-stop-key-id> \
  --stop-approval-public-key-sha256 <pinned-stop-fingerprint> \
  --manifest /absolute/evidence/cleanup-manifest.json
```

The legacy `validate-cleanup-manifest` command remains a schema/canonicalization
utility only. It is not sufficient for approval. The offline
`remove_and_clean` signature must bind the SHA-256 of the source-replayed,
schema-v3 cleanup manifest.

Both commands first replay the complete disabled, native, and archive evidence
roots and both pinned signer identities. They reproduce the disabled report at
its retained evaluation timestamp, while current native authority and archive
retention are rechecked by the source replay.

## Design decisions

| Decision | Why | Alternatives considered | Tradeoff and performance |
|---|---|---|---|
| Evidence-derived manifest | Completeness, state, ownership, and lineage must be independently reproducible before an irreversible approval. | Continue accepting a hand-authored manifest after schema validation. | More evidence preparation; bounded linear hashing occurs off the trading path. |
| Exact scope audit plus exact file inventory | Both omitted surfaces and undeclared evidence must fail closed. | Parse the prose disposition document or accept reviewer assertions without raw references. | Nine explicit scope checks require maintenance but avoid fragile Markdown parsing. |
| Typed path, host, and secret state | Different target classes require different safety facts and state fingerprints. | One generic string-to-hash record. | More schema surface; reviewers can reason about ownership and secret handling directly. |
| Require project ownership and zero shared consumers | Removing shared packages or integrations can damage native or unrelated workloads. | Treat package-manager presence as removal authority. | Some packages remain installed when proof is incomplete; safety takes precedence over cosmetic cleanup. |
| Reuse the frozen recursive credential policy | Cleanup evidence must never become a new credential archive. | Trust `contains_credentials=false` or run an unpinned scanner. | Adds a full bounded scan outside the hot path. |
| No collector or executor in this increment | Reading evidence and deriving approval input must not create operational authority. | Inspect the live host or execute cleanup from the same CLI. | Operators prepare evidence separately; the trust boundary remains small and credential-free. |

## Tests and rollback

Tests cover deterministic assembly/replay, canonical CLI output, failed disabled
reports, undeclared files, changed bytes, unsafe paths, independent review,
lineage, exact scopes, raw references, zero-finding scan coverage, host
ownership, and archive-retention requirements. Schema, lint, strict typing,
dependency, secret, and documentation gates remain required.

Failure changes no runtime state. Preserve the final archive and disabled
evidence, discard the invalid cleanup bundle, correct the inventory or review,
and create a new immutable bundle. Never edit a reviewed bundle in place and
never infer cleanup authority from a passing assembly or replay.
