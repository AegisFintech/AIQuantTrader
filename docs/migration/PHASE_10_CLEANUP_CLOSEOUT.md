# Phase 10 Cleanup Closeout Ledger

Status: canonical evidence-only closeout assembly and replay are implemented.
The module cannot stop services, revoke credentials, remove packages, change
Git, migrate code, delete files, or place orders.

## Purpose

The cleanup completion report is the authoritative pass/fail verdict. A final
retirement record also needs one compact, independently reproducible ledger
that maps the approved plan order to the exact reviewed outcomes. The
`CleanupOperatorLedger` provides that closeout record without becoming an
executor or a new authorization token.

See the [closeout flow](../architecture/diagrams/phase-10-cleanup-closeout.mmd),
the [outcome contract](PHASE_10_CLEANUP_OUTCOME.md), and the
[operator runbook](../operations/LEGACY_RETIREMENT_RUNBOOK.md).

## Replay contract

Assembly replays, at their original timestamps:

1. both immutable preflight evidence roots and the externally pinned cleanup
   approval trust root;
2. the exact cleanup manifest and historical preflight receipt;
3. the deterministically rebuilt cleanup action plan;
4. every plan-bound target outcome, raw artifact, and credential-scan check;
5. the canonical schema-v2 completion report.

The ledger is emitted only when the replayed report is byte-equivalent in
meaning, `cleanup_complete=true`, and the final archive still has the policy's
minimum remaining retention. Later verification repeats the complete replay at
the ledger's retained generation timestamp, so receipt and plan expiry cannot
erase auditability or refresh authority.

Each contiguous ledger entry contains the plan step hash, sequence, stage,
target/action/destination, approved state hash, required outcome and evidence
requirements, observed action interval, target-outcome hash, typed
postcondition hash, collector, independent reviewer, and
`status=verified_complete`. The ledger also hash-binds the policy, source
commit, archive, disabled report, cleanup manifest, preflight, action plan,
signed approval verification, completion report, outcome manifest/bundle,
credential scan, and native deployment/admission identity.

## Commands

Both commands require the same complete source and trust arguments as cleanup
completion, including `--preflight`, `--action-plan`, and
`--outcome-evidence-root`:

```bash
aqt-retirement assemble-cleanup-closeout \
  <all cleanup-completion source and trust arguments> \
  --report /absolute/evidence/cleanup-completion.json \
  --output /absolute/evidence/cleanup-closeout.json

aqt-retirement verify-cleanup-closeout \
  <the same source and trust arguments> \
  --report /absolute/evidence/cleanup-completion.json \
  --ledger /absolute/evidence/cleanup-closeout.json
```

The assembly output must be outside every immutable evidence root and is
created atomically. Neither command accepts a private key, credential value,
shell command, mutation target, or execution flag.

## Design decisions

| Decision | Why chosen | Alternatives considered | Tradeoff and performance |
|---|---|---|---|
| Derive rather than hand-author the ledger | Operator notes cannot prove exact plan/outcome lineage. | Accept a ticket, spreadsheet, or manually edited checklist. | Full replay and hashing are linear in retained evidence size and remain off the trading path. |
| Bind both target evidence and typed postcondition hashes | A reviewer can distinguish the signed observation from the semantic result it proves. | Store only target IDs and `success=true`. | Slightly larger record; materially stronger independent audit. |
| Preserve plan order | Closeout must show whether every ordered step was fulfilled exactly once. | Sort by target ID after action. | Entries are deterministic and reviewable; draft unordered evidence must be regenerated. |
| Recheck archive retention at closeout | A complete action history is insufficient if required recovery evidence is about to expire. | Trust the earlier completion gate indefinitely. | Late assembly can fail and require renewed retention, but no operational authority is changed. |
| Keep closeout evidence-only | Combining audit and irreversible action would enlarge the most sensitive trust boundary. | Add stop/remove/revoke/migrate commands. | Operators use separately authorized procedures; replay remains safe and credential-free. |

## Failure and rollback

A missing, changed, future-dated, non-canonical, incomplete, out-of-order, or
insufficient-retention input fails closed and produces no ledger. Preserve the
failed evidence for incident review. If another operational action is needed,
obtain fresh evidence, approval, preflight, and plan; never edit a retained
ledger or treat verification as renewed authority.

Before publication, a bad generated file can be discarded because it grants no
authority. After publication, corrections are new append-only retirement
records linked to the incident; the original ledger remains immutable. Git and
the retained archive are recovery material only.

## Tests

Tests cover plan-step lineage, contiguous order, typed entry derivation,
canonical loading, completion-report tampering, post-expiry replay, CLI
assembly/verification, JSON Schema export, archive-retention enforcement, and
the no-executor architecture boundary.
