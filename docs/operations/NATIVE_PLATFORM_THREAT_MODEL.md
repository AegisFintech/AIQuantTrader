# Native BTC Platform Threat Model

## Scope

This model covers the Hyperliquid trading node, market-data recorder, safety
sentinel, research workers, governance registry, storage, observability, Docker
host, CI artifacts, operator access, and exchange/API credentials.

Protected assets are trading capital, private keys, approval authority, order
integrity, risk limits, positions, market and execution history, model artifacts,
dataset lineage, and operational availability.

## Trust boundaries

1. Hyperliquid public and private network interfaces.
2. Tardis download service and licensed files.
3. CI/build environment to container registry.
4. Runtime host to containers and mounted secrets.
5. Trading node to independent safety sentinel.
6. Research environment to governance artifacts.
7. Human approver to production deployment controller.
8. Prometheus/Grafana/Alertmanager to operator notification channels.
9. Public shadow gateway to the no-network shadow engine through checksummed,
   read-only durable ingress.

## Threats and controls

| Threat | Impact | Primary controls | Detection/recovery |
|---|---|---|---|
| Trading key disclosure | Unauthorized orders or loss | Runtime secret mount, least-privilege API wallet, no key in Git/image/logs, host access control, rotation runbook | Exchange/account audit, unexpected-order alert, cancel/flatten, revoke wallet |
| Control key disclosure | Malicious cancellation or safety loss | Separate sentinel wallet, minimal scope, isolated service, rotation | Sentinel action audit, renewal anomaly alert, revoke wallet |
| Nonce collision/replay | Rejected or ambiguous commands | One API wallet per signing process, monotonic command identity, reconciliation before retry | Reject/unknown-outcome metrics, order reconciliation |
| Compromised dependency/image | Code execution or order manipulation | Exact locks, image digest, SBOM, vulnerability/signature checks, protected registry | Provenance verification, halt and roll back to approved image |
| Market-data corruption | Bad quotes and adverse orders | Independent validation, stale/crossed checks, source timestamps, raw archive, fail-closed feature age | Integrity metrics, halt/cancel, deterministic replay |
| Feed disconnect or silent stall | Stale quoting | Public/private heartbeat limits, reconnect, stale-data circuit breaker | Alerts, cancel-all, sentinel dead-man cancellation |
| Exchange/API degradation | Unknown order state or excess rejects | Rate budgets, bounded inflight requests, idempotent identity, reconciliation, reject circuit breaker | Halt exposure, reconcile, operator review |
| Trading process or host crash | Resting orders and unmanaged inventory | Exchange scheduled cancel, external sentinel, container restart policy | Cancel confirmation, inventory alert, controlled flatten after recovery |
| Sentinel failure | Dead-man cancellation not renewed | Trading node monitors sentinel/DMS expiry and refuses exposure when unsafe | Alert, cancel orders, halt until restored |
| Strategy defect or runaway loop | Excess orders/inventory | Central hard risk authority, order-rate/open-order/notional caps, application hard bounds | Circuit breaker, cancel/flatten, artifact rollback |
| Model/schema mismatch | Invalid decisions | Hash-bound feature schema, safe model format, startup compatibility checks | Fail startup, retain approved champion |
| Research leakage/overfit | Loss after promotion | Purged validation, untouched holdout, negative controls, immutable datasets, frozen gates | Paper/shadow failure, reject challenger, drift monitoring |
| Approval forgery or replay | Unauthorized production deployment | Offline Ed25519 signature, configured public-key fingerprint, exact artifact/account/wallet/capital binding, expiring approval, durable one-use ledger | Independent startup verification, per-command ledger check, revoke/rollback |
| Renewal replay, mutation, or expiry gap | Stale authority continues or a release changes without promotion | Signed prior-authorization chain, immutable release/capital comparison, transactional single use, no renewal after expiry | Ledger history and heartbeat/sentinel expiry comparison; halt and require a new release/admission |
| Fabricated or selectively omitted rehearsal evidence | Unsafe release appears validated | Frozen complete scenario enum, strict declared inventory, typed checks, exact artifact/config binding, offline journal derivation, independent review | Deterministic reassembly, failed testnet gate, retained venue/journal comparison, halt before signing |
| Rehearsal evidence mutation or mixed generations | Observation does not describe one real run | Per-process predecessor-hashed audit streams on separately owned volumes, bounded non-symlink reads, raw digests/intervals, pre/post inventory scans, stopped immutable SQLite | Assembly/verification failure; preserve the bundle and repeat under a new rehearsal ID |
| Partial or mixed-generation release bundle | Reviewer signs inconsistent artifacts | New absolute output path, bounded non-symlink reads, semantic cross-checks, mode-0600 atomic directory write and receipt hashes | Preparation failure and temporary-directory cleanup; rebuild from immutable sources |
| Controller compromise | Unauthorized runtime authority | Controller has no wallet, verification and admission are separate actions, trading and sentinel independently reverify | Admission transition audit, wallet-role and heartbeat mismatch, halt/cancel |
| Master/vault confusion | Orders or capital applied to the wrong account | Signed master and optional vault identities, independent address verification, account-equity capital clamp | Reconciliation failure, capital-limit denial, two-person venue check |
| Configuration tampering | Relaxed risk or wrong account | Signed policy, hard clamps, unknown-key rejection, two-person preflight | Startup failure, configuration fingerprint alert |
| DuckDB/Parquet corruption | Lost lineage or analytics | Atomic writes, checksummed manifests, backups, restore drills, single writers | Integrity scan, quarantine, restore/rebuild from raw archive |
| Disk exhaustion | Recorder or journal failure | Disk reservations, quotas/retention, pressure thresholds, trading dependency policy | Halt new exposure before critical storage loss, alert and recover |
| Clock drift | Invalid ordering/latency | Host time synchronization and monotonic timestamps | Drift metrics, reject research window, halt if operational threshold exceeded |
| Metrics/log cardinality attack | Observability outage | Bounded labels, IDs only in structured logs, retention limits | Scrape/storage alerts, preserve trading hot path, recover monitoring |
| Operator mistake | Wrong venue/account/capital | Environment banners, allowlisted instrument, signed policy, two-person mainnet checklist | Immediate kill, cancel/flatten, incident review |
| Shadow mode escape | Unapproved order reaches the venue | No account/wallet/signer, engine `network_mode: none`, no default route, read-only one-way ingress, recorded-only sink | Startup route proof, static architecture tests, zero egress metric, invalidate run |
| Shadow ingress mutation/gap | False decision or misleading evidence | Raw-first archive, full-synchronous local sequence, SHA-256 per envelope, read-only engine mount | Fatal sequence/hash check, retained replay, run-integrity failure |

## Failure invariants

- Loss of public data, private account data, reconciliation, risk state, approval,
  or required safety state cannot increase exposure.
- Observability and research storage failures do not block cancel or reduce-only
  actions.
- A strategy cannot directly access exchange clients or private keys.
- A research worker cannot approve or deploy production.
- A dead-man switch is never treated as protection for existing inventory.
- Recovery does not submit a replacement order until the prior command's state
  is reconciled.
- Expiry, rollback, revoke, or ledger mismatch cannot block cancellation but
  cannot authorize submit or replace.
- Passing canary evidence cannot create production or capital authority.
- Passing testnet evidence cannot create a signature, admission, funding, or
  execution authority.
- Acceptance assembly has no network, wallet, signer, evaluator, or deployment
  capability; externally unobservable claims remain raw-evidence-bound human
  review items.

## Required exercises before mainnet

- revoke and rotate each wallet independently;
- kill the trading container and the host network while quotes are resting on
  testnet;
- stop the sentinel and verify the trading node enters a restrictive state;
- inject stale/crossed/malformed market data and stale private account events;
- simulate unknown submission outcomes and reconciliation lag;
- exhaust a test disk and corrupt a raw/Parquet segment;
- restore registry and data manifests from backup;
- reject expired, wrong-account, wrong-image, and over-capital approvals;
- execute operator cancel-all and bounded flatten procedures.
