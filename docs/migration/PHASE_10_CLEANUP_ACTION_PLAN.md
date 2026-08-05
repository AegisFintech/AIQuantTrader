# Phase 10 Cleanup Action Plan

Status: evidence-only manual-action planning is implemented. This increment
does not stop a service, revoke a credential, remove a package, change Git,
migrate a package, or delete a file. The active MT5 runtime remains outside
the retirement CLI's authority.

## Purpose

The approved cleanup manifest says what may be changed, and the short-lived
preflight proves that its target state is current. Neither artifact establishes
one canonical order for the exact actions or tells the outcome reviewer which
typed evidence each target must produce. The cleanup action plan closes that
coordination gap without acquiring execution capability.

See the [action-plan flow](../architecture/diagrams/phase-10-cleanup-action-plan.mmd),
the [cleanup preflight](PHASE_10_CLEANUP_PREFLIGHT.md), and the
[legacy retirement runbook](../operations/LEGACY_RETIREMENT_RUNBOOK.md).

## Repository delta

```text
src/aiquanttrader/retirement/
|- action_plan.py  # active-preflight replay and deterministic plan assembly
|- models.py       # typed stages, steps, evidence requirements, and plan
`- cli.py          # prepare/verify commands without an executor

schemas/retirement.schema.json
docs/architecture/diagrams/phase-10-cleanup-action-plan.mmd
docs/migration/PHASE_10_CLEANUP_ACTION_PLAN.md
tests/unit/test_retirement_cleanup.py
```

## Source and validity contract

Plan preparation requires the exact inputs used by
`verify-cleanup-preflight`: both immutable cleanup evidence roots, schema-v3
cleanup manifest, retained preflight receipt, signed `remove_and_clean`
approval, complete native/archive/disabled lineage, and externally pinned
trust identities. Preparation first replays that entire chain.

The plan is timestamped only after successful replay and is valid only until
the receipt's existing exclusive `valid_until_ts_ns`. It cannot refresh,
extend, or replace the approval or preflight. Verification rejects a
future-dated or expired plan, repeats current preflight replay, rebuilds the
plan at its original timestamp, and requires byte-equivalent semantics.

## Canonical stages

Every approved target appears exactly once. Steps are contiguous and sorted by
stage, then kind, locator, and target ID:

1. revoke credential references and active sessions;
2. retire operational runtime paths while the final archive remains retained;
3. remove project-owned host integrations;
4. remove project-owned, non-shared host packages;
5. migrate the isolated native repository package to its approved destination;
6. remove or archive-only the remaining legacy repository paths.

Credential revocation is first because the disabled observation and final
archive already exist, and eliminating latent broker authority before host or
repository mechanics reduces reactivation risk. Native migration precedes
repository removal so the target package has a reviewed destination before
legacy source deletion.

Each step binds the approved target, expected pre-action state hash, typed
outcome, and exact evidence requirements. Common requirements are in-window
action start, post-action raw evidence, independent review, and a complete
zero-finding credential scan. Action-specific requirements cover path absence,
dual-source host absence, provider revocation plus zero sessions, source and
destination migration evidence plus commit identity, or operational-copy
absence plus final-archive binding.

The canonical plan sets:

- `execution_mode=evidence_only`;
- `commands_included=false`;
- `operator_action_required=true`;
- `operator_ledger_required=true`; and
- `ready_for_manual_action=true` only after active source replay.

`ready_for_manual_action` is a verified coordination fact, not a grant of
authority and not proof that an action occurred. Authority remains the signed
approval. Every outcome must hash-bind its exact plan step, sequence, and stage;
completion remains the exact outcome bundle and report.

## Commands

Prepare a new canonical file outside every immutable evidence root:

```bash
aqt-retirement prepare-cleanup-action-plan \
  <all prepare-cleanup-preflight source and trust arguments> \
  --preflight /absolute/evidence/cleanup-preflight.json \
  --output /absolute/evidence/cleanup-action-plan.json
```

An independent reviewer repeats every source and trust argument:

```bash
aqt-retirement verify-cleanup-action-plan \
  <all prepare-cleanup-preflight source and trust arguments> \
  --preflight /absolute/evidence/cleanup-preflight.json \
  --action-plan /absolute/evidence/cleanup-action-plan.json
```

Both commands are read-only except for atomic creation of the prepare output.
They contain no action command, shell payload, target expansion, or mutation
dependency.

## Design decisions

| Decision | Why chosen | Alternatives considered | Tradeoff and performance |
|---|---|---|---|
| Derive the plan from manifest plus current preflight | Ordering cannot change target scope or reinterpret signed intent. | Accept an operator-authored checklist. | Full source replay and canonical hashing are linear in retained evidence size; no trading hot-path impact. |
| Revoke credentials first | Final archive and disabled evidence already exist, so latent broker authority is the highest remaining reactivation risk. | Remove files/packages first. | Provider work may slow the short window; large sets should use smaller approved manifests. |
| Bind typed evidence requirements per step | The outcome collector knows the exact proof required before any action starts. | Record only target and action. | More schema detail; fewer ambiguous or incomplete outcome bundles. |
| Expire with the original receipt | Planning cannot become a second, longer-lived authority token. | Give the plan its own validity period. | Operators may need to recapture evidence; the TOCTOU bound remains intact. |
| No executable instructions | Evidence verification must remain isolated from irreversible host, provider, and repository mutations. | Emit shell commands or call an executor. | Operators implement separately approved actions manually; the verifier remains credential-free and safe to replay. |

## Migration, failure, and rollback

1. Prepare and independently verify the preflight.
2. Prepare and independently verify the action plan before its inherited expiry.
3. If either verification fails or the shared window expires, perform no new
   action. Preserve the rejected artifacts, recapture current state, and repeat
   the approval flow if approved scope changed.
4. A separately authorized operator records every started/completed action in
   the outcome controls. The plan itself is retained in the operator timeline.
5. Assemble and independently replay the plan-bound cleanup completion report,
   then derive and replay the canonical closeout ledger. A plan is never
   completion evidence by itself.

Rollback before operator action is removal of the untrusted plan from the
workflow; evidence roots and signed records remain immutable. After action,
follow the outcome workflow and incident process. Git and the final archive are
recovery material, not automatic permission to reactivate MT5.

## Tests

Tests cover canonical assembly and loading, current source replay, inherited
expiry, CLI prepare/verify, deterministic safe ordering, typed outcome/evidence
mapping, forged plan and step identities, canonical file enforcement, JSON
Schema export, and the architecture boundary's continuing prohibition on an
executor.
