# Phase 10 Legacy Archive Evidence Assembly

Status: implemented as a credential-free Gate A prerequisite; no pause, tag,
broker, stop, or cleanup action is authorized

This increment replaces an operator-authored `LegacyArchiveManifest` with an
independently reproducible schema-v2 manifest. It verifies the exact retained
archive inventory, every category digest, an isolated restore replay, a frozen
recursive credential scan, the annotated `mt5-final` lineage, remaining
retention, and stable file identity before emitting evidence for retirement
readiness.

## Security boundary

`aqt-retirement assemble-archive` and `verify-archive` read one local immutable
bundle, the frozen retirement policy, and its separately stored credential-scan
policy. They have no MT5, Wine, broker, Git mutation, network, signer,
credential-store, process-manager, package-manager, stop, or deletion
capability. They do not create `mt5-final`, pause entries, capture live data, or
build the archive.

The secured host clock is a trust input. A bundle dated after assembly, an
output dated after verification, or an archive with less than the frozen
365-day remaining-retention window is rejected.

## Exact bundle

The absolute non-symlink root contains exactly 15 non-empty, non-symlink,
non-group/world-writable regular files and only the two required directories:

```text
legacy-archive-evidence.json
artifacts/
  final_trade_report.tar.zst
  broker_account_state.tar.zst
  deployed_release.tar.zst
  runtime_configuration.tar.zst
  common_files.tar.zst
  deal_order_history.tar.zst
  strategy_research.tar.zst
  service_configuration.tar.zst
  operational_logs.tar.zst
  restore_test.tar.zst
  operator_timeline.tar.zst
controls/
  restore-evidence.json
  credential-scan-evidence.json
  final-tag-evidence.json
```

The filenames are illustrative: the evidence manifest may choose different
bounded paths below `artifacts/` and `controls/`, but it must declare exactly
one artifact for each of the eleven frozen categories and exactly the three
control records. Extra files or directories invalidate the bundle. Category
artifacts may be deterministic archives; each is limited to 1 TiB and the
complete bundle to 12 TiB. Controls are canonical JSON plus one newline.
For final-state reconstruction, the final-trade-report, broker-account-state,
and service-configuration categories use the bounded uncompressed tar/member
contract in `PHASE_10_FINAL_STATE.md`; the `.tar.zst` names above are not the
required format for those three categories.

The eleven category artifacts contain the actual retained material. The three
controls provide machine-verifiable review results:

- restore evidence covers every category exactly once and requires source and
  isolated-restored byte counts and SHA-256 digests to match;
- credential-scan evidence covers every category exactly once, records zero
  findings, and binds the externally frozen recursive scan policy; and
- final-tag evidence proves review of an annotated `mt5-final` tag object that
  resolves to the archived source commit.

The checked-in scan policy requires recursive inspection for API tokens,
passwords, private keys, seed phrases, and session credentials with zero
findings. A report cannot nominate a weaker policy because the retirement
policy pins both its ID and canonical SHA-256.

## Verification

The assembler:

1. loads canonical control records and validates exact category/control sets;
2. checks every file path, type, permission, size, digest, and captured time;
3. requires the restore and scan to start after all category captures and be
   independently reviewed before final bundle creation;
4. matches every restore source/restored digest and size to the retained
   category artifact;
5. matches every recursive zero-finding scan check to the same artifact and
   externally pinned policy;
6. matches the annotated final-tag evidence to the retirement, source commit,
   and `mt5-final` commit;
7. rejects future evidence and insufficient remaining retention;
8. verifies file device, inode, size, modification time, and change time remain
   stable throughout assembly; and
9. emits the evidence-manifest hash, complete bundle hash, scan-policy hash,
   restore/tag evidence hashes, and all category bindings in a canonical
   `LegacyArchiveManifest`.

Independent verification repeats every check at the original assembly time,
requires exact typed output equality, and separately enforces retention at the
current verification time.

## Decisions and tradeoffs

| Decision | Why | Alternatives | Tradeoff and performance |
|---|---|---|---|
| Exact 11-category inventory | Completeness is reviewable and compatible with the frozen retirement policy. | Loose files could omit inconvenient history; one monolithic archive made partial recovery expensive. | More packaging work; verification remains linear and offline. |
| Separate restore, scan, and tag controls | Opaque archives cannot prove their own recovery, redaction, or Git lineage. | Boolean manifest fields were easy to author without retained proof. | Three additional reviewed records; no trading-path cost. |
| Externally pinned recursive scan policy | The bundle cannot select a weaker credential detector after seeing results. | Trusting scanner name or free text was not reproducible. | Policy changes require a newly frozen retirement policy and native observation. |
| One content-hash pass plus final identity recheck | Multi-terabyte archives should not be read twice merely to detect normal concurrent mutation. | Double hashing is stronger against a privileged attacker but doubles I/O; trusting only declared hashes was insufficient. | Normal changes are detected through inode/size/mtime/ctime; immutable off-host storage and independent replay remain required against a privileged host. |
| Output schema v2 with provenance | Readiness can distinguish independently assembled evidence from the former assertion-only manifest. | Preserving schema v1 would keep ambiguous booleans. | Any preexisting draft evidence must be rebuilt; no real retirement evidence had been issued. |

## Commands

Prepare and review the immutable bundle outside this tool, then run:

```bash
aqt-retirement assemble-archive \
  --evidence-root /absolute/retained/legacy-final \
  --policy native/configs/retirement/evidence-v1.toml \
  --credential-scan-policy native/configs/retirement/archive-credential-scan-v1.toml \
  --output /absolute/retained/legacy-archive-manifest.json

aqt-retirement verify-archive \
  --evidence-root /absolute/retained/legacy-final \
  --manifest /absolute/retained/legacy-archive-manifest.json \
  --policy native/configs/retirement/evidence-v1.toml \
  --credential-scan-policy native/configs/retirement/archive-credential-scan-v1.toml
```

Output creation is atomic, mode `0600`, absolute-path-only, fail-on-exist, and
must be outside the immutable evidence root.
A passing archive verification still does not prove the final account is flat.
The evidence-bearing category members described in
[`Phase 10: Final MT5 State Assembly`](PHASE_10_FINAL_STATE.md) must next pass
`assemble-final-state` and independent `verify-final-state` before readiness
evaluation.

## Failure and rollback

Failure creates no manifest and grants no authority. Preserve the rejected
bundle and original source evidence. Never edit retained files to force a pass;
build a new immutable bundle and record the superseded identity. A credential
finding requires redaction at the source and a complete rescan. A restore,
digest, tag, timing, or inventory mismatch requires investigation and replay.
Rolling back this code does not alter MT5, the archive, native production, or
either human approval boundary.
