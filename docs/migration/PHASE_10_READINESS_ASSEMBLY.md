# Phase 10: Retirement Readiness Assembly

Status: cross-bundle assembly, independent replay, and source-reverified
readiness evaluation implemented; no MT5 stop or cleanup is authorized

This increment removes the manually composed handoff between independently
verified native-production evidence and the final legacy archive. A readiness
observation is now emitted only after both immutable roots are replayed under
the same frozen policy. `evaluate-readiness` repeats that replay before it can
write a report.

## Security boundary

The readiness assembler and verifier are credential-free, offline readers.
They have no wallet, signer, broker, exchange, network, process-manager,
package-manager, entry-pause, order, stop, credential-revocation, or deletion
capability. They cannot create either human approval.

The native approval key ID and Ed25519 public-key fingerprint remain mandatory
external inputs. The legacy recursive credential-scan policy remains a separate
frozen input. Outputs use absolute, fail-on-exist, mode-`0600`, atomic paths
outside both immutable evidence roots.

A passing report says only `AWAITING STOP APPROVAL`. It does not pause entries,
flatten positions, stop services, create `mt5-final`, or authorize an operator
command.

## Bound evidence

`assemble-readiness` accepts only canonical outputs from the earlier stages:

- the schema-v3 `NativeProductionObservation`, which now carries the retirement
  identity in addition to the frozen policy, deployment, admission, terminal
  authorization, signer, interval, drills, and exact native bundle hashes;
- the schema-v2 `LegacyArchiveManifest` bound to its eleven categories,
  restore, credential scan, annotated tag, retention, and bundle identity; and
- the schema-v2 `LegacyFinalState` bound to that exact archive, policy, raw
  source captures, account identity, inventory, pause, and writer evidence.

The native and legacy retirement identities must match. This prevents a valid
native observation for one retirement case from being combined with another
case's legacy archive.

## Replay and timing rules

Assembly performs these steps in order:

1. replay the entire native-production evidence root against the external
   signer identity and frozen retirement policy;
2. replay the final legacy state, which itself reverifies the complete archive,
   scan policy, raw sources, reconciliation, and capture timing;
3. take the observation timestamp only after both replays finish;
4. require native authorization to remain active at that completion timestamp;
5. require the final-state capture to remain inside the one-hour policy window;
6. bind both verified objects and the archive into canonical readiness bytes.

Independent verification repeats all six steps and requires byte-equivalent
output. It rejects future observations, authority that expires during replay,
state that becomes stale during replay, changed source artifacts, a different
trust root, mixed retirement identities, or non-canonical/symlinked input.

`evaluate-readiness` invokes this verifier internally. A caller cannot obtain a
report by passing only a typed or hand-edited observation.

## Decisions, alternatives, and tradeoffs

| Decision | Why chosen | Alternatives considered | Tradeoff and performance implication |
|---|---|---|---|
| Replay both immutable roots at assembly and evaluation | The observation and the approval-facing report must describe evidence that still exists and remains current. | Trusting prior console success or hashes copied into a hand-authored observation allowed omission and time-of-check/time-of-use gaps. | Native signature/ledger/audit parsing and legacy archive hashing run twice; this is bounded offline work and adds no trading-path latency. |
| Add retirement identity to native observation schema v3 | Deployment identity alone cannot prove that native and legacy evidence belong to the same retirement case. | Inferring identity from filenames or operator notes was unauditable. | Existing pre-production observation fixtures must be regenerated; no issued retirement evidence exists and runtime behavior is unchanged. |
| Timestamp only after replay | Authority or final-state freshness can expire while large evidence bundles are being checked. | Timestamping before replay understated evidence age and could outlive authority at output time. | One additional clock read and terminal checks; negligible offline cost. |
| Require evaluation-time replay | A separately verified observation could be changed or its sources could become stale before report generation. | A standalone verifier plus a pure evaluator was simpler but left a procedural TOCTOU boundary. | Evaluation needs both evidence roots and trust inputs, making the command longer but fail-closed. |
| Keep non-ready economic facts evaluable | A valid live account, open position, pending order, inactive pause, or writer should produce failed gates rather than be disguised as malformed evidence. | Rejecting all non-ready states at assembly hid the exact blocker. | Operators receive actionable gate output; only structural inconsistency, stale evidence, or replay failure prevents a report. |

## Commands

All inputs are retained evidence. Never use mutable production or MT5 runtime
directories as evidence roots.

```bash
aqt-retirement assemble-readiness \
  --native-evidence-root /absolute/evidence/native-production \
  --legacy-evidence-root /absolute/evidence/legacy-archive-bundle \
  --native-observation /absolute/evidence/native-production-observation.json \
  --archive-manifest /absolute/evidence/legacy-archive-manifest.json \
  --final-state /absolute/evidence/legacy-final-state.json \
  --policy native/configs/retirement/evidence-v1.toml \
  --credential-scan-policy native/configs/retirement/archive-credential-scan-v1.toml \
  --approval-key-id <independently-recorded-key-id> \
  --approval-public-key-sha256 <independently-pinned-fingerprint> \
  --output /absolute/evidence/readiness-observation.json

aqt-retirement verify-readiness \
  --native-evidence-root /absolute/evidence/native-production \
  --legacy-evidence-root /absolute/evidence/legacy-archive-bundle \
  --observation /absolute/evidence/readiness-observation.json \
  --policy native/configs/retirement/evidence-v1.toml \
  --credential-scan-policy native/configs/retirement/archive-credential-scan-v1.toml \
  --approval-key-id <independently-recorded-key-id> \
  --approval-public-key-sha256 <independently-pinned-fingerprint>

aqt-retirement evaluate-readiness \
  --native-evidence-root /absolute/evidence/native-production \
  --legacy-evidence-root /absolute/evidence/legacy-archive-bundle \
  --observation /absolute/evidence/readiness-observation.json \
  --policy native/configs/retirement/evidence-v1.toml \
  --credential-scan-policy native/configs/retirement/archive-credential-scan-v1.toml \
  --approval-key-id <independently-recorded-key-id> \
  --approval-public-key-sha256 <independently-pinned-fingerprint> \
  --output /absolute/evidence/readiness-report.json
```

The independent verifier should use separately retained policy and trust-root
records. Run evaluation immediately after verification because the final-state
and production-authorization clocks continue to advance.

## Tests and rollback

Tests assemble genuine signed native evidence and a genuine evidence-bearing
legacy archive, then exercise assembly, independent verification, evaluation,
source and observation tampering, cross-retirement substitution, future/stale
timing, output-root protection, canonical input, and symlink rejection.

Failure creates no authority. Preserve the rejected evidence, recapture stale
legacy state or renew native authority through its existing reviewed process,
and rerun every upstream verifier. Rolling back this code changes no account,
service, admission, archive, or MT5 runtime state.
