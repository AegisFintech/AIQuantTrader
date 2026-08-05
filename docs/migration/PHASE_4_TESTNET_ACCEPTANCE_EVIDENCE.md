# Phase 4: Deterministic Testnet Acceptance Evidence

Status: implementation complete; a real credentialed testnet rehearsal and
independent review remain pending.

This increment makes the Phase 4/9 testnet handoff reproducible. It does not
run scenarios, query Hyperliquid, hold a wallet, evaluate the frozen policy, or
authorize deployment. It converts a stopped, retained rehearsal directory into
the exact `TestnetDressRehearsalObservation` consumed by governance.

## Architecture

The source diagram is
[`testnet-acceptance-evidence.mmd`](../architecture/diagrams/testnet-acceptance-evidence.mmd).

```text
exact-image rehearsal
  -> execution and sentinel hash-linked audit streams
  -> venue, metric, process, kill, and mount evidence
  -> typed manifest + 15 typed scenario records + final account state
  -> credential-free deterministic assembler
  -> canonical observation
  -> existing frozen-policy evaluator
  -> independent review; never automatic promotion
```

The execution node and sentinel each own a separate append-only JSONL stream.
Records are canonical, sequence-numbered, predecessor-hashed, synchronized to
disk, and serialized with an advisory file lock. The streams record
reconciliation, risk-state changes, live-pipeline faults, cancel actions,
heartbeat health, exchange dead-man scheduling, and emergency cancellation.
They contain no private key or secret value.
Compose keeps the shared execution state read-only in the sentinel and mounts a
different sentinel-owned writable evidence volume. The audit requirement never
widens the control process's access to the execution journal or heartbeat.
Exact-release rehearsals require a unique rehearsal ID and therefore fresh,
separately named execution, data, and sentinel-evidence volumes.

## Repository delta

```text
native/
|- Dockerfile, compose.{testnet,rehearsal,mainnet}.yaml
|- src/aiquanttrader_native/acceptance/
|  |- models.py       # exact run, artifact, scenario, and audit contracts
|  |- audit.py        # durable component-owned operational evidence
|  |- collector.py    # offline inventory and observation assembly
|  `- cli.py          # aqt-acceptance assemble/verify
|- schemas/acceptance.schema.json
`- tests/unit/test_testnet_acceptance.py

docs/
|- architecture/diagrams/testnet-acceptance-evidence.mmd
|- migration/PHASE_4_TESTNET_ACCEPTANCE_EVIDENCE.md
`- operations/EXECUTION_RISK_RUNBOOK.md
```

## Evidence contract

The bundle must contain exactly three canonical control files, one canonical
file for every frozen lifecycle scenario, and manifest-bound raw artifacts for
all of these categories:

- execution journal and execution operational audit;
- sentinel operational audit;
- execution and sentinel Prometheus snapshots;
- venue orders, fills, and final account state;
- operator-kill audit and process lifecycle events;
- rendered mount/configuration inspection proving no mainnet credential was
  present.

No undeclared file is allowed. Every raw artifact has a SHA-256, exact byte
count, and capture interval inside the run. Paths must remain below `raw/` and
cannot traverse or use symlinks. Files are non-empty, bounded, regular, and not
group/world writable. The complete bundle is limited to 256 files and 256 MiB.

The assembler opens the stopped SQLite journal read-only and immutable, runs
`PRAGMA quick_check`, validates every lifecycle event, and derives submitted
orders, filled orders, unknown outcomes, their later resolutions, and duplicate
venue-order identities. It validates both operational hash chains and requires
a successful startup reconciliation plus a successful dead-man schedule. It
also validates exact account/vault identities, a final testnet BTC state, every
scenario's required raw categories, and a complete frozen 15-scenario matrix.
It also requires a closed set of scenario-specific check identities documented
in the execution runbook; generic one-line pass assertions cannot replace the
required venue, lifecycle, risk, and safety observations.

Some facts cannot be inferred safely from one local process. Reconciliation
failure count, risk-breach count, actual exchange dead-man firings, and absence
of a mainnet credential are explicit reviewed facts bound to venue, process,
and mount-inspection artifacts. They are not silently guessed. A passing
sentinel-death scenario is mandatory when a dead-man cancellation is claimed.

## Security and failure behavior

- Assembly and verification have no network, signer, secret-file, or order
  capability.
- Control JSON must use the canonical domain serialization. Pretty-printed,
  partial, duplicated, ambiguous, or out-of-window evidence fails.
- The inventory is hashed before and after derivation; concurrent mutation,
  digest drift, SQLite corruption, a broken operational chain, or an unexpected
  file fails the run.
- Output uses a new absolute path, mode `0600`, fsync, and an atomic no-replace
  hard link. Reviewed output cannot be overwritten by a rerun.
- Hash chaining detects accidental or post-freeze mutation; it does not replace
  independent venue evidence, protected storage, or human review against a
  malicious evidence producer.
- Passing assembly means only that the observation is internally consistent.
  The frozen policy evaluator may still fail it, and a policy pass stops at
  `AWAITING_APPROVAL`.

## Design decisions

| Decision | Why | Alternatives and tradeoffs | Performance implication |
|---|---|---|---|
| Assemble only after a planned stop | SQLite and all captured files must represent one immutable interval. | Reading a live WAL or scraping during assembly is faster operationally but permits mixed generations. | One bounded offline scan; no hot-path impact. |
| Separate execution and sentinel logs | A crashed or compromised trading node must not own the independent safety record. | One shared file is simpler but creates cross-process write contention and one trust boundary. | One fsync per state transition or changed safety outcome; repeated successful renewals remain Prometheus counters, not audit records. |
| Strict closed inventory | Selective omission and undeclared evidence must be visible. | Recursive best-effort collection is convenient but can cherry-pick or silently mix files. | Two linear scans capped at 256 MiB. |
| Derive only locally provable counts | Journal facts should not depend on operator transcription. | Trusting all typed counters is simpler but makes observation errors easy. | One read-only SQLite scan after shutdown. |
| Retain typed reviewed facts for external claims | Dead-man firing and credential absence require exchange/host evidence outside the order journal. | Pretending they are locally observable would create false assurance. | Negligible parsing; independent review remains intentionally manual. |
| Credential-free assembler | Evidence processing cannot become another execution or approval principal. | Online collection could query the venue directly but would require credentials and weaken separation. | No network latency and deterministic replay. |

## Forward migration

1. Freeze the exact candidate, risk policy, testnet policy, image digest, and
   target behavior before the run.
2. Use distinct testnet trading and control API wallets and complete every
   scenario in the execution runbook.
3. Perform the planned-stop procedure, export the stopped journal and all raw
   evidence, and create canonical controls using the exported acceptance
   schema.
4. Run `aqt-acceptance assemble`, then `aqt-acceptance verify` from a separate
   review environment.
5. Evaluate the canonical observation with the already frozen governance
   policy. Preserve failures; never edit evidence to manufacture a pass.
6. Only after independent review may the existing unsigned release-preparation
   boundary consume the passing report.

## Rollback

This component changes no venue or deployment state. On any failure, retain the
entire evidence directory read-only, record the reason, keep execution disabled,
and repeat the complete rehearsal with a new rehearsal identity. Never replace
files inside a reviewed bundle. The deployed MT5 demo runtime is unaffected.
