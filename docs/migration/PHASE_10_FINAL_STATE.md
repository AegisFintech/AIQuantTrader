# Phase 10 Final MT5 State Assembly

Status: implemented as a credential-free Gate A prerequisite; no broker,
pause, flatten, stop, tag, or cleanup action is authorized

This increment replaces the assertion-only `LegacyFinalState` with a
schema-v2 state independently reconstructed from retained archive facts. It
reverifies the complete legacy archive, reads three bounded evidence-bearing
category archives, validates their raw source hashes, reconciles MT5 and broker
positions, and derives every readiness count without accepting operator-written
totals.

## Security boundary

`aqt-retirement assemble-final-state` and `verify-final-state` read only an
immutable local archive, its schema-v2 manifest, and the two frozen policy
files. They have no MT5, Wine, broker, exchange, network, signer, process
manager, credential store, package manager, order, pause, stop, tag, or deletion
capability. They neither capture facts nor close or transfer positions.

Account numbers, broker servers, position IDs, and order IDs are retained only
as SHA-256 identities in normalized records. Raw retained sources must already
be credential-free and pass the separately pinned recursive archive scan.

The secured host clock is a trust input. The v1 policy permits at most five
minutes of skew among the three captures and one hour from the newest capture
to assembly or independent verification. A second operator therefore cannot
replay stale state as current stop-readiness evidence.

## Evidence-bearing archive categories

The complete 15-file archive remains exactly as specified by
`PHASE_10_LEGACY_ARCHIVE.md`. Three category artifacts are uncompressed tar
archives containing these exact evidence members plus any other retained
regular files needed for audit:

```text
final_trade_report.tar
  final-state/final-trade-report.json
  final-state/final-trade-report.txt

broker_account_state.tar
  final-state/broker-account-state.json
  final-state/broker-account-export.txt

service_configuration.tar
  final-state/service-configuration.json
  final-state/aiquanttrader-status.json
  final-state/aiquanttrader-entry-pause.flag       # only when present
  final-state/command-writers/process_table.txt
  final-state/command-writers/pm2.txt
  final-state/command-writers/cron.txt
  final-state/command-writers/systemd.txt
  final-state/command-writers/command_file_handles.txt
```

The three normalized JSON records are canonical JSON plus one newline and are
limited to 16 MiB each. Their retained-source hashes must match the raw members.
Every tar member path must be normalized and traversal-free; only directories
and regular files are accepted. Links, devices, duplicate paths, compressed or
invalid tar streams, more than 100,000 members, or over 1 TiB of declared member
content fail closed.

The trade-report record lists managed position identities and the total
account-position count reported by MT5. The independently captured and reviewed
broker record lists every account position and pending order and records whether
the account is demo, live, contest, or unknown. The independently reviewed
service record binds the raw MT5 status, the pause flag when present, and exact
checks of process-table, PM2, cron, systemd, and open-command-file surfaces.

## Derived state and cross-checks

The assembler:

1. independently replays the exact archive, restore, credential scan, tag, and
   remaining-retention checks;
2. requires record capture times to equal their archive bindings and enforces
   the policy skew and freshness bounds;
3. requires one retirement identity and matching hashed account/server
   identities across MT5 and broker evidence;
4. requires the MT5 total position count to equal the broker position inventory;
5. requires every managed position to exist at the broker under `XAUUSD`;
6. derives managed, unmanaged, and pending-order counts from unique record
   identities;
7. requires the report and service records to bind the same raw MT5 status and
   agree on the EA pause state;
8. treats entry pause as active only when both the EA reports it and the retained
   flag exists with the declared hash;
9. derives command-writer count from the five exact reviewed inventories; and
10. emits policy, archive-manifest, archive-bundle, raw-status, category, and
    capture provenance in canonical `LegacyFinalState` schema v2.

Live, contest, unknown, unpaused, non-flat, pending-order, or writer-present
evidence remains valid evidence but produces a non-ready state. Cross-source
identity, position, instrument, status, timestamp, or raw-hash disagreement is
invalid evidence and stops assembly.

## Decisions and tradeoffs

| Decision | Why | Alternatives | Tradeoff and performance |
|---|---|---|---|
| Derive state from three sources | MT5 owns managed-position context, the broker owns complete account truth, and host inventory owns writer/pause capability. No one source can prove all retirement gates. | A single operator summary was easy to fabricate or omit unmanaged state from. | More capture and peer-review work; offline reconciliation has no trading-path cost. |
| Embed normalized facts beside raw sources | Machine-readable facts remain traceable to the exact retained report, broker export, status, flag, and host inventories. | Parsing human interfaces directly was broker/version fragile; retaining normalized JSON alone lost source provenance. | Requires deterministic packaging and several small tar passes. |
| Accept non-ready facts but reject inconsistent facts | Evaluators must explain live/unpaused/open state rather than making failures impossible to represent, while contradictions cannot be promoted as evidence. | Literal demo/flat-only input models hid which safety gate failed. | Slightly larger enum/contracts; clearer operations and no hot-path impact. |
| Five-minute skew and one-hour freshness | Captures describe one operational state and leave enough time for restore, scan, peer review, and independent replay. | No age bound permits stale stop evidence; a five-minute total age is impractical for reviewed archive assembly. | Gate A must be repeated when review exceeds one hour. The operator still repeats broker flatness and archive hashes immediately before an approved stop. |
| Uncompressed bounded tar for evidence-bearing categories | Standard-library streaming inspection avoids extraction, decompression ambiguity, path traversal, and a native compression dependency. | Zstandard categories are smaller but require another pinned parser; loose files break the exact top-level archive inventory. | Three small categories use more storage and are read repeatedly offline. Other archive categories may remain deterministic compressed artifacts. |

## Commands

After the archive itself passes independent assembly and verification:

```bash
aqt-retirement assemble-final-state \
  --evidence-root /absolute/retained/legacy-final \
  --archive-manifest /absolute/retained/legacy-archive-manifest.json \
  --policy native/configs/retirement/evidence-v1.toml \
  --credential-scan-policy native/configs/retirement/archive-credential-scan-v1.toml \
  --output /absolute/retained/legacy-final-state.json

aqt-retirement verify-final-state \
  --evidence-root /absolute/retained/legacy-final \
  --archive-manifest /absolute/retained/legacy-archive-manifest.json \
  --final-state /absolute/retained/legacy-final-state.json \
  --policy native/configs/retirement/evidence-v1.toml \
  --credential-scan-policy native/configs/retirement/archive-credential-scan-v1.toml
```

Output creation is atomic, mode `0600`, absolute-path-only, fail-on-exist, and
must be outside the immutable evidence root.
Independent verification repeats archive and state reconstruction and requires
exact typed equality. A flat demo result still grants no stop authority; it is
only one input to the independently replayed
[`Retirement Readiness Assembly`](PHASE_10_READINESS_ASSEMBLY.md) and the
separate signed approval.

## Failure and rollback

Failure creates no final-state output and changes no runtime. Preserve the
source capture, rejected bundle, and reviewer record. A contradiction requires
new synchronized captures, not editing retained evidence. A stale result
requires Gate A recapture and replay. A credential finding requires source
redaction and a full archive rescan. Rolling back this code does not change MT5,
positions, orders, credentials, native production, or either approval boundary.
