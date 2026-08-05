# Phase 10 Native-Production Evidence Assembly

Status: implemented as a credential-free prerequisite to retirement readiness;
no legacy stop or cleanup is authorized

The retirement evaluator previously accepted a typed native-production
observation but could not reconstruct it from retained evidence. This increment
adds an independent assembler. A passing assembly proves the exact signed
production release, the durable admission, every authorization renewal, bounded
operational continuity, typed risk outcomes, incident review, and required
drills before producing the observation consumed by Phase 10.

## Security boundary

`aqt-retirement assemble-native` and `verify-native` have no exchange, wallet,
signer, process-manager, package-manager, broker, stop, or deletion capability.
They read one immutable local bundle and a frozen policy. The approval key ID
and Ed25519 public-key fingerprint are mandatory external inputs; the evidence
bundle and its SQLite ledger are not allowed to nominate their own trust root.
Only a public key may be retained. A private key in the public-key slot is
rejected. The verifier treats the secured host clock as an operational trust
input; clock synchronization and health evidence must be retained with the
operator timeline.

Assembly does not make retirement ready. The output still needs the 30-day
policy gate, final MT5 flat-state/archive evidence, an independent replay, and a
separately signed `stop_and_observe` approval.

## Evidence bundle

The root is an absolute, non-symlink directory containing canonical
`production-manifest.json` and only the files it binds below `raw/`. Files are
non-empty regular files, not group/world writable, and bounded to 4,096 files
and 1 GiB total. Any missing, extra, changed, symlinked, oversized, or
non-regular object invalidates assembly.

The manifest requires exactly:

- one checkpointed schema-v2 admission SQLite database with no WAL/SHM
  sidecars;
- the production approval, detached signature, public key, and artifact
  manifest;
- all nine deployed release artifacts at the manifest-prescribed paths;
- every signed renewal and its detached signature, paired by renewal ID;
- the complete hash-linked execution and sentinel operational audits;
- one reviewed incident register covering the exact observation interval;
- one exact report for each rollback, backup/restore, alert-delivery, and
  operator-access drill;
- all supporting evidence referenced by an incident or drill report.

Signed JSON files use their exact canonical bytes. Bundle control JSON uses
canonical bytes plus one newline. The assembler hashes the complete inventory
twice and rejects changes during assembly. The assembly timestamp comes from
the host clock, never from the manifest; a future-dated manifest is rejected.

## Verification

The assembler independently:

1. verifies the pinned Ed25519 trust root and original approval signature;
2. reconstructs the admission ID and checks every immutable approval,
   manifest, release-artifact, account, image, configuration, and capital
   binding against the retained ledger;
3. verifies each renewal signature, reconstructs each authorization ID, and
   walks the single predecessor chain from admission to the ledger terminal;
4. rejects forks, disconnected history, replay, non-extending expiry, renewal
   after expiry, renewal before admission, identity changes, inactive state,
   or a terminal mismatch;
5. parses the component-owned hash-linked audits and derives reconciliation
   failures, typed economic risk breaches, and runtime-critical failures;
6. requires successful sentinel dead-man samples across the whole interval,
   including both boundaries, with no gap over the frozen five-minute policy;
7. validates the reviewed incident register and the frozen check set for all
   four drills; and
8. emits the schema-v3 observation with the manifest retirement identity,
   policy identity, signer identity, ordered authorization-chain hash, terminal
   authorization/expiry, operational sample coverage, derived failure counts,
   manifest hash, and complete bundle hash.

Independent verification also uses the host clock. It rejects an observation
dated after verification and any authorization that is no longer active at
verification time before reconstructing the exact original assembly result.

Risk-state audit records now contain typed `RiskReason` values. A non-active
risk state without typed reasons is rejected, so a retirement observation must
start after this evidence-capable runtime is deployed. This avoids classifying
free-form log text as a risk breach.

## Decisions and tradeoffs

| Decision | Why | Alternatives | Tradeoff and performance |
|---|---|---|---|
| External pinned trust root | A copied ledger and bundle cannot establish their own signer authority. | Trusting the key stored beside the evidence was vulnerable to coordinated bundle/ledger replacement. | Operators must retain two public identifiers separately; Ed25519 verification is offline and negligible. |
| Read-only immutable SQLite snapshot | It proves the terminal ledger and renewal history without mutating production or creating WAL sidecars. | Reading the live ledger risked inconsistent generations and filesystem writes. | Requires a checkpoint/backup step; `quick_check` and bounded queries are offline. |
| Exact signed renewal-chain reconstruction | A terminal expiry alone cannot prove 30 days without an authorization gap. | Trusting `renewal_count` or the final row was simpler but unauditable. | Verification is linear in weekly renewals and immaterial to trading latency. |
| Sentinel schedule continuity with a five-minute maximum gap | Elapsed timestamps plus one success event do not prove continuous native supervision. | A self-attested uptime percentage was rejected. | Retained audit volume grows predictably; parsing is bounded and outside the hot path. |
| Typed risk reasons | Free-form detail cannot safely distinguish a stale-feed protection from a daily-loss breach. | String parsing was brittle and could silently misclassify new reasons. | Adds small audit records and a deliberate compatibility boundary for old non-active events. |
| Exact drill check sets | A single `passed=true` field is insufficient evidence for recovery readiness. | Unstructured operator notes were not machine-verifiable. | More evidence preparation; no live execution cost. |

## Commands

Create a checkpointed, immutable evidence copy first. Never point the assembler
at the live ledger or runtime directory.

```bash
aqt-retirement assemble-native \
  --evidence-root /absolute/retained/native-production \
  --policy native/configs/retirement/evidence-v1.toml \
  --approval-key-id <independently-recorded-key-id> \
  --approval-public-key-sha256 <independently-pinned-fingerprint> \
  --output /absolute/retained/native-production-observation.json

aqt-retirement verify-native \
  --evidence-root /absolute/retained/native-production \
  --policy native/configs/retirement/evidence-v1.toml \
  --approval-key-id <independently-recorded-key-id> \
  --approval-public-key-sha256 <independently-pinned-fingerprint> \
  --observation /absolute/retained/native-production-observation.json
```

The assembly output path is fail-on-exist, mode `0600`, atomic, and outside the
immutable evidence root.
Verification reassembles every fact and requires byte-equivalent typed output.
The retirement identity is later required to match the legacy archive during
`assemble-readiness`; deployment identity alone is not a retirement-case key.

## Failure and rollback

An assembly failure creates no observation and grants no action authority.
Preserve the rejected bundle, classify the cause, and restart the observation
window for an authorization gap, evidence gap, critical incident,
reconciliation failure, or risk breach as required by policy. Do not edit
retained evidence to make it pass. A packaging-only error is corrected by
building a new immutable bundle from the unchanged source evidence and
recording the superseded bundle identity.

Rollback of this code is a normal native software rollback. It does not change
the production admission, MT5 runtime, retirement evidence, or either human
approval boundary.
