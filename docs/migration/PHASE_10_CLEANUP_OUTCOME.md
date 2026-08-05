# Phase 10 Cleanup Outcome Evidence

Status: evidence-only completion assembly and replay are implemented. This
module does not stop a service, revoke a credential, remove a package, change
Git, migrate a package, or delete a file. Operator actions remain outside the
retirement CLI.

## Purpose

A passing cleanup preflight proves that approved state is current immediately
before action. It does not prove what happened afterward. Phase 10 therefore
requires a distinct immutable outcome bundle and a canonical completion report
before cleanup may be recorded as complete.

The evidence-only [cleanup action plan](PHASE_10_CLEANUP_ACTION_PLAN.md) gives
operators one canonical target order and exact outcome-evidence checklist. It
expires with preflight, requires an operator ledger, and is neither authority
nor completion evidence.

See the [outcome flow](../architecture/diagrams/phase-10-cleanup-outcome.mmd)
and the [retirement runbook](../operations/LEGACY_RETIREMENT_RUNBOOK.md).

## Approved action contract

The cleanup manifest is schema v3. Every target now has an unambiguous typed
postcondition:

| Approved action | Allowed target | Required outcome |
|---|---|---|
| `remove` | Repository/runtime path, host integration, or host package | Exact path absence at a commit or runtime capture, or exact uninstalled host state |
| `revoke` | Secret reference only | The same hashed provider record is revoked and has zero active sessions; secret material is forbidden |
| `migrate_native` | Repository path only | Source absent, explicitly approved destination present, migration commit bound, and exact typed destination inventory |
| `retain_archive_only` | Repository/runtime path only | Operational copy absent and the exact approved archive manifest retained |

`migrate_native` must declare one safe relative `destination_locator` before
approval. Destinations cannot duplicate one another or also be cleanup source
paths. Other actions cannot declare a destination. This replaces post-hoc
operator interpretation with approval-time intent.

## Outcome bundle

```text
cleanup-outcome-evidence/
|- cleanup-outcome-evidence.json
|- controls/
|  |- credential-scan.json
|  `- targets/<target-id>.json
`- raw/
   |- repository/<absence-or-destination-inventory>
   |- runtime/<absence-proof>
   |- host/<absence-and-independent-state-proof>
   `- credentials/<revocation-and-session-proof>
```

The schema-v2 top-level manifest binds the frozen retirement and
credential-scan policies, `mt5-final` source commit, archive, disabled report,
schema-v3 cleanup manifest, exact preflight receipt, and exact canonical action
plan. Its declared byte inventory must equal
the complete directory inventory. Symlinks, traversal, changed files,
undeclared files, group/world-writable files, and resource-bound violations
fail replay.

Every schema-v2 target outcome records its plan step hash, contiguous sequence,
stage, exact approved target and pre-action state hash, action start and
completion, evidence capture time, collector, independent reviewer, typed
result, and raw-artifact references. An outcome whose order or target contract
differs from its plan step fails replay. Every raw artifact must be captured
after its action completes and before the reviewed target control. Unreferenced
or missing artifacts fail replay.

The recursive zero-finding credential scan covers every raw artifact and every
target control. The scan begins only after all target controls are captured.
No provider credential value, private key, or session token may enter the
bundle.

## Time and authority semantics

Completion replay reconstructs the original preflight at its recorded
evaluation time from both cleanup evidence roots, the signed approval, and the
externally pinned Ed25519 trust root. It then deterministically rebuilds the
action plan at its retained preparation timestamp and requires an exact match.
It does not require that approval, receipt, or plan remain active during later
audit.

For every target:

```text
preflight evaluated <= action started < preflight valid until
action started <= action completed <= raw capture <= target review
```

This is intentional. Authority is checked at the start of each exact target
action. A long-running mechanical PR may complete later, but an unstarted
target cannot consume expired authority. Split a large cleanup into smaller
approved manifests when all target actions cannot start inside the receipt
window.

## Commands

Supply the complete argument set documented for `prepare-cleanup-preflight`,
plus the retained receipt and outcome root:

```bash
aqt-retirement assemble-cleanup-completion \
  <all prepare-cleanup-preflight source and trust arguments> \
  --preflight /absolute/evidence/cleanup-preflight.json \
  --action-plan /absolute/evidence/cleanup-action-plan.json \
  --outcome-evidence-root /absolute/evidence/cleanup-outcome \
  --output /absolute/evidence/cleanup-completion.json

aqt-retirement verify-cleanup-completion \
  <the same source and trust arguments> \
  --preflight /absolute/evidence/cleanup-preflight.json \
  --action-plan /absolute/evidence/cleanup-action-plan.json \
  --outcome-evidence-root /absolute/evidence/cleanup-outcome \
  --report /absolute/evidence/cleanup-completion.json
```

The output must be outside every immutable evidence root. Verification replays
the report at its retained generation timestamp and rejects future-dated,
changed, incomplete, or forged reports. It never refreshes approval.

## Completion gates

The report includes exactly these gates:

1. historical preflight source replay;
2. historical action-plan source replay;
3. exact one-to-one plan sequence;
4. action start inside the exclusive authority window;
5. exact approved target inventory;
6. approved pre-action state binding;
7. typed postcondition verification;
8. exact immutable outcome inventory;
9. complete policy-bound zero-finding scan;
10. required archive retention remaining; and
11. independent review for every target.

`cleanup_complete=true` is valid only when every gate and every target
postcondition passes. The report says `verification_mode=evidence_only` and
`operator_actions_observed=true`; it is an audit fact, not authority.

## Design decisions

| Decision | Why | Alternatives considered | Tradeoff and performance |
|---|---|---|---|
| Declare destinations before approval | A migration cannot be reviewed safely when its destination is chosen after signing. | Record a destination only in operator notes or outcome evidence. | Schema-v2 manifests must be regenerated and reapproved; no production manifests exist yet. |
| Replay authority historically | Receipts must expire quickly, while audit evidence must remain independently verifiable. | Require an active receipt forever or trust the receipt without source replay. | Replay reads and hashes all retained inputs; it never extends authority. |
| Hash-bind every result to one verified plan step | Reviewers must prove the executed order and evidence contract, not infer them from target IDs. | Treat the plan as an optional operator attachment. | Outcome/report schema v2 is intentionally incompatible with draft v1 evidence; no production cleanup evidence exists. |
| Require every target to start in-window | One receipt must not authorize work first attempted after its state proof expires. | Check only the first batch action. | Large batches may require multiple manifests; TOCTOU scope stays bounded. |
| Typed postconditions by action | Absence, revocation, archive retention, and migration prove different facts. | A generic `success=true` record. | More schema and tests; reviewers get deterministic semantics. |
| Exact bundle plus credential scan | Outcome evidence can omit failures or accidentally archive secrets. | Accept tickets, logs, or provider screenshots directly. | Linear file hashing and scanning occur off the trading path. |
| No executor | Evidence verification must not gain irreversible operational capability. | Combine verification and cleanup in one command. | Operators perform separately authorized work manually; the trust boundary stays small. |

## Migration, failure, and rollback

1. Regenerate any draft schema-v2 cleanup manifest under schema v3 and obtain a
   new signature; never reinterpret an old signature. Regenerate draft
   schema-v1 outcome controls, manifests, and completion reports under schema
   v2 so every result binds its exact action-plan step.
2. After successful preflight, record each exact operator action and its raw
   outcome without including credentials.
3. Independently review the target controls, scan the complete bundle, assemble
   the report, and have a different operator replay it.
4. Attach the report, evidence hashes, and migration commit to the mechanical
   cleanup PR, then assemble the canonical closeout ledger described in
   [`PHASE_10_CLEANUP_CLOSEOUT.md`](PHASE_10_CLEANUP_CLOSEOUT.md).

Any failed gate means cleanup is incomplete. Preserve the failed evidence and
the final archive. Corrective work requires a fresh inventory, manifest,
approval, and preflight whenever another action is needed. Git and the retained
archive provide recovery material only; they do not restore MT5 authority.

## Tests

Tests cover schema-v3 action compatibility, schema-v2 plan-bound outcomes,
explicit migration destinations, exact post-action inventory, historical
preflight and plan replay, start-time expiry, canonical report loading,
post-expiry independent replay, sequence tampering, CLI assembly/verification,
JSON Schema export, and the no-executor architecture boundary.
