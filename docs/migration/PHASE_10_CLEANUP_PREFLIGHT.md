# Phase 10 Cleanup Preflight

Status: evidence-only implementation. It does not stop a process, revoke a
credential, remove a package, change Git, or delete a file. The active MT5
runtime remains outside this tool's authority.

This increment closes the time-of-check/time-of-use gap between an approved
cleanup manifest and a later operator action. A signed manifest is insufficient
when a path, package, integration, or broker session has changed since review.
The preflight therefore requires a second complete cleanup-evidence bundle
captured after approval and immediately before action.

See the [cleanup preflight flow](../architecture/diagrams/phase-10-cleanup-preflight.mmd)
and the [legacy retirement runbook](../operations/LEGACY_RETIREMENT_RUNBOOK.md).

## Repository delta

```text
native/src/aiquanttrader_native/retirement/
|- cleanup.py       # verified replay exposes typed evidence timestamps
|- preflight.py     # fresh-state, target, and approval checks only
|- models.py        # canonical short-lived receipt and gate contracts
`- cli.py           # prepare/verify evidence commands

native/schemas/retirement.schema.json
docs/architecture/diagrams/phase-10-cleanup-preflight.mmd
docs/migration/PHASE_10_CLEANUP_PREFLIGHT.md
native/tests/unit/test_retirement_cleanup.py
```

## Required inputs

The preparer supplies all existing source-replay inputs plus:

- the approved cleanup-evidence root and its canonical schema-v3 manifest;
- a distinct, fully reviewed action-time cleanup-evidence root;
- the offline `remove_and_clean` approval, detached Ed25519 signature, public
  key, externally pinned key ID, and externally pinned key fingerprint.

The action-time root uses the same nine-scope audit, typed path/host/secret
states, exact inventory, independent review, raw-artifact binding, and
recursive zero-finding credential scan as the approved root. A hand-authored
state summary is not accepted.

Every action-time evidence timestamp must be on or after approval and no later
than evaluation. The oldest evidence must remain inside the retirement
policy's frozen five-minute `maximum_final_state_capture_skew_ns` bound. This
reuses the already approved cutover simultaneity limit and is stricter than the
one-hour final-state age bound. The receipt expires at the earlier of that
fresh-state boundary and the signed approval expiry.

## Gates and output

`prepare-cleanup-preflight` fails before writing output unless it can prove:

1. the disabled report still has every gate passed;
2. complete native, archive, disabled, and approved-manifest source replay;
3. an active signature for `remove_and_clean`, the exact disabled report,
   native deployment/admission, archive, source commit, and cleanup manifest;
4. a new complete action-time evidence replay captured after that approval;
5. exact equality of target ID, kind, locator, action, migration destination,
   and rationale;
6. exact equality of every stable target-state SHA-256;
7. a positive remaining approval and state-freshness window.

The canonical receipt binds both evidence roots, both manifest identities, the
approval verification and trust fingerprint, native identity, source commit,
all per-target observed hashes, all gate results, and the exclusive expiry. It
sets `execution_mode=evidence_only` and `operator_action_required=true`.
It contains no commands and grants no authority beyond the signed approval.

`verify-cleanup-preflight` replays the retained receipt from all immutable
sources and rejects it when its short validity window has elapsed. It does not
refresh or extend a receipt.

## Commands

Prepare a new output outside every evidence root:

```bash
aqt-retirement prepare-cleanup-preflight \
  --evidence-root /absolute/evidence/approved-cleanup \
  --action-evidence-root /absolute/evidence/action-time-cleanup \
  --cleanup-manifest /absolute/evidence/cleanup-manifest.json \
  --cleanup-approval /absolute/offline/cleanup-approval.json \
  --cleanup-signature /absolute/offline/cleanup-approval.sig.json \
  --cleanup-public-key /absolute/trust/retirement-approver.pub \
  --cleanup-approval-key-id <pinned-cleanup-key-id> \
  --cleanup-approval-public-key-sha256 <pinned-cleanup-fingerprint> \
  --disabled-evidence-root /absolute/evidence/disabled \
  --native-evidence-root /absolute/evidence/native-production \
  --legacy-evidence-root /absolute/evidence/legacy-archive \
  --readiness-observation /absolute/evidence/readiness-observation.json \
  --readiness-report /absolute/evidence/readiness-report.json \
  --native-observation /absolute/evidence/native-observation.json \
  --archive-manifest /absolute/evidence/archive-manifest.json \
  --stop-approval /absolute/offline/stop-approval.json \
  --stop-signature /absolute/offline/stop-approval.sig.json \
  --stop-public-key /absolute/trust/stop-approver.pub \
  --disabled-observation /absolute/evidence/disabled-observation.json \
  --disabled-report /absolute/evidence/disabled-report.json \
  --policy native/configs/retirement/evidence-v1.toml \
  --credential-scan-policy native/configs/retirement/archive-credential-scan-v1.toml \
  --native-approval-key-id <pinned-native-key-id> \
  --native-approval-public-key-sha256 <pinned-native-fingerprint> \
  --stop-approval-key-id <pinned-stop-key-id> \
  --stop-approval-public-key-sha256 <pinned-stop-fingerprint> \
  --output /absolute/evidence/cleanup-preflight.json
```

An independent reviewer uses the same arguments with
`verify-cleanup-preflight` and replaces `--output` with
`--preflight /absolute/evidence/cleanup-preflight.json`.

## Design decisions

| Decision | Why | Alternatives considered | Tradeoff and performance |
|---|---|---|---|
| Reuse the complete cleanup-evidence contract | Action-time state receives the same inventory, ownership, review, and credential-scan scrutiny as approval-time state. | Accept a target-to-hash JSON summary. | More preparation and bounded linear hashing; no trading hot-path cost. |
| Require recapture after approval | Replaying the original bundle cannot establish current state and leaves a TOCTOU gap. | Permit a still-recent approval bundle. | Adds a second capture; proves the operator reviewed current targets under active authority. |
| Stable state hash excludes capture time | Unchanged inventories compare exactly while timestamps remain independently auditable. | Hash the entire evidence record. | Two bundles can agree on state despite different capture times; explicit freshness gates remain mandatory. |
| Five-minute frozen bound | It is the existing cutover simultaneity limit and produces a genuinely short-lived receipt. | Add a new unfrozen constant or use the one-hour final-state age. | Large inventories may need optimized capture; an incomplete window fails safely. |
| Receipt is evidence-only | Verification and irreversible action must remain separate trust boundaries. | Emit shell commands or execute cleanup from the CLI. | Operator work remains manual and separately accountable; the evidence package cannot mutate the host. |

## Migration and rollback

1. Preserve the approved cleanup root and signed approval unchanged.
2. Recapture every target into a new immutable action-time root after approval.
3. Run prepare, then independent verify, inside the exclusive receipt window.
4. If any check fails or the receipt expires, perform no action. Preserve the
   failed evidence for audit, recapture into a new root, and obtain a new
   approval whenever the approved inventory itself must change.

Because this increment is read-only apart from writing a new receipt, rollback
is simply removal of the untrusted receipt from the operator workflow. Evidence
roots and approvals are never edited in place.

After separately authorized operator action, the evidence-only
[`PHASE_10_CLEANUP_OUTCOME.md`](PHASE_10_CLEANUP_OUTCOME.md) workflow proves
typed postconditions without refreshing this receipt or performing cleanup.
Before action, derive and independently replay the canonical expiring
[`PHASE_10_CLEANUP_ACTION_PLAN.md`](PHASE_10_CLEANUP_ACTION_PLAN.md); it orders
the exact targets and typed evidence requirements without executing them.

## Tests

Unit and CLI tests cover successful independent replay, post-approval capture,
stable state equality, state drift, stale evidence, expired receipts, canonical
loading, forged identities/verdicts, duplicate targets, and missing gates. The
architecture contract continues to reject process, network, venue, package,
stop, removal, and cleanup-execution capability in the retirement boundary.
