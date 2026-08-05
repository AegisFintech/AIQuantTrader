# Legacy MT5 Retirement Runbook

Status: Phase 10 procedure only. The active MT5 runtime must not be stopped or
removed until the exact evidence and approvals below exist.

This runbook has no implied flatten, deletion, package-removal, credential-
revocation, or production authority. Preserve its evidence outside the mutable
host. Rollback never silently restarts MT5 trading.

## Roles and separation

- The evidence preparer collects facts and cannot approve the action.
- The risk owner reviews native and broker state.
- The offline approver signs one exact, short-lived action scope.
- The operator verifies the signature, rechecks live state, and performs only
  the approved steps.
- The removal reviewer checks every cleanup target and the final Git diff.

Use separate people for approver and operator whenever staffing permits.

## Gate A: final archive and stop readiness

1. Confirm the exact native production deployment and admission have remained
   healthy for the frozen minimum window. Retain the production approval,
   artifact manifest, ordered renewal approvals and ledger transitions,
   terminal authorization identity/expiry, operational evidence, drill reports,
   alerts, and backup restore evidence. Any expiry gap restarts the window.
   Create a checkpointed schema-v2 SQLite backup; do not copy a live WAL
   generation or point evidence tooling at the runtime ledger. Retain every
   signed renewal and detached signature. Build the exact-inventory production
   bundle described in `PHASE_10_PRODUCTION_EVIDENCE.md`.
2. Obtain the approval key ID and Ed25519 public-key fingerprint from the
   independent release trust record, not from the evidence bundle. Run
   `aqt-retirement assemble-native`, then have a second operator run
   `verify-native`. Any signature, artifact, ledger, renewal-chain, typed-risk,
   incident, drill, or five-minute sentinel-continuity failure stops Gate A.
3. Activate `aiquanttrader_entry_pause.flag`. Confirm automatic and command-file
   entries are rejected while monitoring and position management remain active.
4. Run `python3 scripts/mt5_trade_report.py` and retain its raw output;
   independently export the complete broker account positions and pending
   orders. Retain the raw MT5 status and entry-pause flag plus reviewed
   process-table, PM2, cron, systemd, and command-file-handle inventories.
5. Closing or transferring any position requires explicit owner direction.
   Continue only when all three counts are zero.
6. Hash account, server, position, and order identifiers in the normalized
   records; never include credentials. A second person reviews the broker and
   service captures. Package the raw and canonical normalized members in the
   three uncompressed evidence-bearing tar categories specified by
   `PHASE_10_FINAL_STATE.md`. Their capture times must be no more than five
   minutes apart.
7. Capture the eleven required archive categories: final trade report, broker
   state, deployed source/compiled release identity, redacted runtime
   configuration, Common Files, deal/order history, strategy/research evidence,
   service configuration, operational logs, restore-test result, and operator
   timeline. Never archive credentials in this evidence bundle.
8. Copy the category artifacts to the approved immutable destination, hash each
   artifact, and restore each one into an isolated location. Run the externally
   reviewed scanner recursively over the restored content using the exact
   policy pinned by `evidence-v1.toml`. Record matching source/restored bytes
   and hashes for all eleven categories and zero credential findings for all
   required detector classes. A finding, omitted category, partial scan, or
   weaker scan policy stops Gate A.
9. After archive review, create the annotated `mt5-final` tag at the archived
   commit. Push it once; never move or reuse it. Retain the tag-object identity
   and proof that it resolves to the exact archived source commit.
10. Build the exact 15-file evidence bundle described in
   `PHASE_10_LEGACY_ARCHIVE.md`: eleven category artifacts plus canonical
   restore, credential-scan, and final-tag control records and their evidence
   manifest. Run `aqt-retirement assemble-archive`; then have a second operator
   run `verify-archive` against the immutable bundle and separately stored
   policy files. The resulting schema-v2 manifest must retain at least 365 days
   from verification. Never substitute an operator-authored archive summary.
11. Run `aqt-retirement assemble-final-state`; then have a second operator run
   `verify-final-state` against the same immutable archive and separately stored
   policies. Any raw-source hash, account identity, position inventory,
   instrument, pause, writer, capture-skew, or tar-safety disagreement stops
   Gate A. The final state must remain inside its one-hour freshness window and
   must report demo, entry-paused, flat, no pending orders, and zero writers.
12. Run `aqt-retirement assemble-readiness` against the immutable native and
   legacy roots, canonical native/archive/final-state outputs, separately
   retained policies, and independently pinned signer identity. A second
   operator runs `verify-readiness`. Never hand-compose the observation.
13. Immediately run `aqt-retirement evaluate-readiness` with both immutable
   roots and the same external policy/trust inputs. Evaluation repeats both
   source replays; a nonzero exit, expired authority, stale final state, changed
   source, identity mismatch, or failed gate stops the procedure.

A passing report says only `awaiting_stop_approval`. Canonicalize the
`stop_and_observe` approval, sign it offline, and verify the detached signature
against the pinned key ID and public-key fingerprint. The approval must bind the
report, native deployment/admission, archive manifest, source commit, and
`mt5-final`, and must still be inside its 24-hour window at action time.

## Gate B: disable legacy capability

Immediately before action, repeat the broker flat-state, pause/writer, and
archive hash checks. If any changed, or the verified final state is stale,
discard the approval and restart Gate A.

Disable in this order so the watchdog cannot revive the terminal:

1. stop `aiquanttrader-watchdog` and `aiquanttrader-review`;
2. stop `aiquanttrader-mt5`;
3. stop `aiquanttrader-dashboard`;
4. remove exactly those four entries from PM2 startup state and save the new
   PM2 state;
5. disable the installed AIQuantTrader cron file, legacy dashboard nginx route,
   and legacy logrotate policy at their inventory-resolved paths;
6. disable any MT5 autostart outside PM2;
7. quarantine MT5 credentials so no retained process can read them;
8. confirm zero MT5/Wine processes and zero command-file writers;
9. independently confirm zero new broker orders after the stop timestamp.

Do not delete source, `.runtime`, Common Files, state, logs, service files, or
archives during this gate. Record exact commands, resolved targets, timestamps,
process output, broker evidence, and hashes in the disabled evidence bundle.

## Gate C: reversible disabled observation

Observe for at least seven full days under the v1 policy. During the entire
window:

- native production remains on the exact approved deployment/admission;
- critical incidents, reconciliation failures, and risk breaches remain zero;
- all ten legacy capabilities remain disabled with zero active instances;
- post-stop MT5 broker orders remain zero;
- legacy credentials remain unavailable to services;
- the final archive is rehashed and restorable;
- disk, clocks, alerts, backups, and operator access remain healthy.

Build `DisabledObservation` and run `aqt-retirement evaluate-disabled`. A
passing report says only `awaiting_cleanup_approval`.

## Gate D: exact cleanup approval

Create a cleanup manifest with one explicit target per repository path, runtime
path, host integration, secret reference, or host package. The validator rejects
globs, traversal, duplicate locators, and broad roots such as `/`, `/root`,
`/tmp`, `/etc`, `/usr`, and `/var`. Bind every target to a canonical expected
state hash: file bytes or tree inventory for paths, installed-state evidence for
packages/integrations, and provider record identity for credential revocation.
Recompute and compare that state immediately before action; any mismatch
invalidates the manifest and cleanup approval.

The manifest must include, where present and independently verified:

- the MQL5 bridge and modules under `broker/mt5/`;
- MT5/Wine lifecycle, Common Files, XAU research, PM2, Streamlit, cron, nginx,
  and logrotate repository files listed by `FILE_DISPOSITION.md`;
- legacy-owned tests and point-in-time planning documents;
- the repo-local Wine prefix, terminal, downloads, Common Files, legacy state,
  and logs as separate explicit runtime paths;
- installed host integrations and packages, but only after proving no other
  workload uses them;
- MT5 credentials and broker sessions as revocation targets;
- the ADR 0008 native package and project-root migration actions.

Run `aqt-retirement validate-cleanup-manifest`, retain its canonical bytes and
hash, then obtain a new `remove_and_clean` approval. This approval must bind the
disabled report and exact cleanup manifest. A stop approval cannot authorize
cleanup.

## Gate E: cleanup execution and PR

1. Snapshot current host inventory and reverify approval, archive, disabled
   state, and cleanup-manifest hashes.
2. Move recoverable runtime targets to an approved dated quarantine location
   before permanent deletion when practical. Never use an unresolved variable,
   wildcard, repository root, home directory, or broad recursive target.
3. Revoke legacy credentials and broker sessions listed in the manifest.
4. Remove only approved host integrations and packages. Leave shared packages
   installed and record the variance.
5. Create a dedicated mechanical PR removing only approved legacy repository
   targets and performing the ADR 0008 package migration.
6. Make no strategy, model, execution, capital, or risk-policy change in that
   PR.
7. Run the complete native CI, replay, container, schema, security,
   documentation, and release suite from a clean checkout.
8. Verify the production deployment is still the approved native identity and
   that no MT5/Wine/order-writer capability remains.
9. Retain the final archive, readiness/disabled reports, both signed approvals,
   cleanup manifest, operator timeline, removal commit, and `mt5-final` tag for
   the approved retention period.

## Failure and rollback

Before cleanup, a failed native or retirement gate means cancel native orders as
policy requires, revoke native admission if necessary, and leave both systems
halted. Restoring MT5 requires new owner authority, archive and credential
validation, broker-state reconciliation, a demo-only rehearsal, and a new
deployment record.

After cleanup, Git and the off-host archive provide recovery material, not
automatic runtime authority. Any restoration starts as a new reviewed incident
recovery and never bypasses the native promotion or retirement evidence chain.
