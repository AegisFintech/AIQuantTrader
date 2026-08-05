# Phase 10: Disabled Observation Assembly

Status: immutable disabled-window assembly, independent replay, and
source-reverified evaluation implemented; no stop or cleanup is authorized

This increment removes the operator-authored `DisabledObservation` handoff.
The observation can now be assembled only from an exact immutable evidence
bundle, the passing readiness artifacts, the signed stop approval, the retained
native-production evidence, and the final legacy archive. `evaluate-disabled`
replays every source before it can write the cleanup-review report.

## Security boundary

`aqt-retirement` remains an offline, credential-free evidence reader. It has no
PM2, systemd, cron, nginx, logrotate, package-manager, broker, exchange, wallet,
signer, credential-revocation, stop, restart, delete, or network capability.
It does not collect evidence from a mutable runtime and cannot execute any step
recorded in the stop control.

A passing report says only `AWAITING CLEANUP APPROVAL`. It cannot create the
separate `remove_and_clean` approval or authorize a cleanup target.

## Exact evidence bundle

The disabled root is immutable before assembly and contains only:

```text
disabled-evidence/
├── disabled-evidence.json
├── controls/
│   ├── stop-execution.json
│   ├── capability-audit.json
│   ├── broker-order-audit.json
│   ├── credential-quarantine.json
│   ├── native-stability-audit.json
│   └── credential-scan.json
└── raw/
    └── operator-selected evidence files bound by the manifest
```

The manifest binds every file by relative path, byte count, SHA-256, capture
interval, category, retirement identity, readiness report, stop approval,
archive manifest, native deployment, and admission. The verifier rejects
missing or extra files, extra directories, symlinks, non-regular files,
group/world-writable files, traversal, unreferenced raw artifacts, changed
files, and resource-bound violations.

Raw artifacts are categorized as stop output, capability snapshots, broker
order export, credential quarantine audit, or native operational audit. Every
artifact must be referenced by a typed control, and every typed reference must
resolve to the correct category and cover the stated timestamp or interval.
The bundle declares `contains_credentials=false`; raw evidence must use
credential references and hashes, never secret values.

## Replay rules

Assembly and verification perform these checks in order:

1. validate the exact disabled-root inventory and canonical controls;
2. require the thirteen stop actions in their prescribed order, with an
   independent reviewer and raw evidence covering every completion timestamp;
3. bind the original canonical readiness observation and passing report;
4. cryptographically verify the detached `stop_and_observe` signature against
   the external key ID and Ed25519 fingerprint at the recorded stop-completion
   time, and require the whole stop to begin after approval;
5. reverify the retained native evidence and require its current authorization,
   retirement, deployment, admission, policy, and signer lineage to match;
6. rehash and reverify the complete legacy archive, recursive scan policy,
   retention, restore proof, and `mt5-final` lineage;
7. require the capability audit to cover all ten legacy capabilities at every
   sample, derive the maximum edge/inter-sample gap, and retain active or
   invalidating evidence as failed gates;
8. require complete broker-history coverage from the recorded MT5 stop through
   observation end and bind it to the readiness account/server hashes;
9. require a complete, continuously audited credential inventory with zero
   service readers for the full window;
10. require independently reviewed native operational evidence over the exact
    disabled interval and derive incident, reconciliation, and risk totals;
11. require a policy-bound recursive zero-finding credential scan over every
    raw artifact, with exact artifact ID and SHA-256 coverage;
12. rehash the entire disabled root after semantic validation and emit only a
    canonical schema-v2 observation outside all evidence roots.

The frozen policy requires seven full days and no capability-evidence gap over
five minutes. Failed economic or operational facts produce failed gates;
missing lineage, incomplete intervals, invalid authority, archive corruption,
or malformed evidence aborts assembly.

## Decisions, alternatives, and tradeoffs

| Decision | Why chosen | Alternatives considered | Tradeoff and performance implication |
|---|---|---|---|
| Exact six-control bundle with fully referenced raw evidence | A summary boolean cannot prove continuous disabled state or explain a failure. | Trust operator totals or console output copied into the observation. | Periodic snapshots and recursive scanning increase offline work; there is no trading hot-path latency. |
| Historical signature verification at stop completion plus current source replay | A 24-hour stop approval is intentionally expired by the end of a seven-day observation, but its action-time validity must remain provable. | Require an active stop approval after seven days, or ignore expiry during replay. | The stop control must retain precise reviewed timestamps; the signature remains fully verified and cannot authorize cleanup. |
| Separate native stability control and current native authority verification | The disabled window needs interval-specific facts while production authorization must still be current at cleanup review. | Reuse pre-stop totals or require the original native bundle to be mutable. | One extra typed audit and raw stream are retained; immutable pre-stop evidence stays unchanged. |
| Derive continuity and summaries during replay | Derived gaps, maxima, and totals cannot be lowered by editing a summary. | Accept sample counts, maximum gaps, or health totals from the manifest. | Replay is linear in bounded samples and files; no runtime latency is added. |
| Preserve unsafe facts as failed gates | An observed process, broker order, reader, or incident is valuable evidence and must block cleanup visibly. | Reject the bundle as malformed and lose the precise blocker. | Reports are more actionable; structural and trust failures still abort. |

## Commands

All roots and artifacts are retained, immutable inputs. The native and stop
approval trust identities are independent external records.

```bash
aqt-retirement assemble-disabled \
  --disabled-evidence-root /absolute/evidence/disabled-window \
  --native-evidence-root /absolute/evidence/native-production \
  --legacy-evidence-root /absolute/evidence/legacy-archive-bundle \
  --readiness-observation /absolute/evidence/readiness-observation.json \
  --readiness-report /absolute/evidence/readiness-report.json \
  --native-observation /absolute/evidence/native-production-observation.json \
  --archive-manifest /absolute/evidence/legacy-archive-manifest.json \
  --stop-approval /absolute/offline/stop-approval.json \
  --stop-signature /absolute/offline/stop-approval.sig.json \
  --stop-public-key /absolute/trust/retirement-approver.pub \
  --policy native/configs/retirement/evidence-v1.toml \
  --credential-scan-policy native/configs/retirement/archive-credential-scan-v1.toml \
  --native-approval-key-id <independently-recorded-native-key-id> \
  --native-approval-public-key-sha256 <pinned-native-fingerprint> \
  --stop-approval-key-id <independently-recorded-stop-key-id> \
  --stop-approval-public-key-sha256 <pinned-stop-fingerprint> \
  --output /absolute/evidence/disabled-observation.json

aqt-retirement verify-disabled \
  <the same source and trust arguments> \
  --observation /absolute/evidence/disabled-observation.json

aqt-retirement evaluate-disabled \
  <the same source and trust arguments> \
  --observation /absolute/evidence/disabled-observation.json \
  --output /absolute/evidence/disabled-report.json
```

The angle-bracket shorthand above is documentation only: the actual CLI
requires every explicit source and trust argument shown by
`aqt-retirement evaluate-disabled --help`.

## Tests and rollback

Tests assemble genuine readiness/native/archive evidence, sign a real Ed25519
stop approval, build the exact disabled root, and exercise assembly,
verification, evaluation, CLI output safety, continuity gaps, active
capabilities, post-stop orders, incomplete broker coverage, credential readers,
native incidents, cross-bundle substitution, path safety, raw tampering, and
unexpected inventory.

Failure grants no authority and changes no runtime. Preserve the rejected
bundle. Before cleanup approval, rollback remains an owner-directed incident
decision: native safety may require cancellation or admission revocation, while
MT5 reactivation requires new explicit authority, reconciliation, credential
validation, and demo rehearsal. Never treat the retained stop approval as
reactivation or cleanup authority.
