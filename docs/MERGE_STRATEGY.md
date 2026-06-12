# Merge Strategy

## TL;DR

Use phase-bounded feature branches for milestone work and trunk-based delivery
for small ops changes and hotfixes. Delete any branch whose head is already an
ancestor of `main` after the work lands; keeping redundant branches around is
branch debt and makes release ownership harder to see. See
[`scripts/check_stale_branches.sh`](../scripts/check_stale_branches.sh) to find
safe deletion candidates.

## Branch Lifecycle

- Feature branch for milestone work: create `m<N>/<short-desc>` or
  `phase<N>/<short-desc>`, open a PR into `main`, squash-merge it, then delete
  the branch locally and remotely after its head is an ancestor of `main`.
- Hotfix or small ops change: use a tiny PR into `main`, usually one commit, and
  avoid introducing a phase branch when the change has no design impact.
- Phase-bounded work: keep the phase branch alive only while multiple PRs are
  still landing for that phase. The phase branch is a naming convention and
  coordination aid, not a long-lived fork.

## Naming

- `m<N>/`: milestone work, for example `m23/xau-quick-momentum`.
- `phase<N>/`: multi-PR phase work, for example `phase3/research-platform`.
- `ops/`: small operational changes, cleanup, docs, or automation.
- `data/`: data-layer, ingestion, warehouse, or dataset plumbing work.
- `security/`: secrets, credentials, key handling, or access-control work.
- `m15/`: accepted for backward compatibility with older milestone prefixes.

Keep the suffix short, lowercase, and descriptive. Prefer a name that says what
ships, not how the work felt while it was being built.

## What To Delete

Delete any local or remote branch whose head is an ancestor of `main`. Run this
from the repository root:

```bash
./scripts/check_stale_branches.sh
```

The script reports one candidate per line with `[local]` or `[remote]`. A listed
branch is safe to delete from a Git graph perspective because `main` already
contains that branch head.

To delete all reported candidates, use both explicit flags:

```bash
./scripts/check_stale_branches.sh --delete --yes-i-mean-it
```

Review the output before using deletion mode. Do not remove a branch that is
still part of active work, even if it is technically merged.

## What NOT To Do

- Do not keep stacked phase branches after the phase lands.
- Do not keep "experimental" branches indefinitely.
- Do not create a new branch for a one-line fix when a tiny PR into `main` is
  enough.
- Do not use phase branches as long-lived forks of `main`.
- Do not delete branches that are still needed for an open PR, active review,
  release verification, or rollback coordination.

## Reference

This policy follows the Option A+C recommendation from
[issue #9](https://github.com/AegisFintech/FinRobot/issues/9): use
phase-bounded feature branches for milestone work and trunk-based delivery for
small ops or hotfix work. The immediate cleanup was raised because
`phase1/hardening`, `phase2/warehouse`, and `phase3/data-layer` were already
fully merged into `main`, making the branches redundant.
