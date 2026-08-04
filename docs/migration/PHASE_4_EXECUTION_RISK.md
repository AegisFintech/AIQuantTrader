# Phase 4: Hyperliquid Execution and Hard Risk

Status: implementation complete; credentialed testnet acceptance evidence pending.

Phase 4 adds the first exchange-order-capable native code. Every checked-in
environment remains execution-disabled. The legacy MT5 process list, EA,
credentials, and XAUUSD behavior are unchanged.

## Architecture

The source diagram is
[`phase-4-execution-risk.mmd`](../architecture/diagrams/phase-4-execution-risk.mmd).

```text
pure strategy kernel -> typed intent -> synchronous risk authority
  -> single-use approval -> sole Nautilus gateway -> Hyperliquid adapter
  -> durable lifecycle journal -> reconciliation without blind resubmission

trading wallet -> trading-node container only
control wallet -> independent sentinel container only
sentinel -> scheduleCancel renewal + emergency cancel-all, never ordinary orders
```

NautilusTrader `1.230.0` owns ordinary submit, IOC, post-only, modify, cancel,
order events, portfolio reconciliation, and external-order discovery. The
official Hyperliquid SDK `0.24.0` exists only in the sentinel wrapper, whose
public surface is restricted to `schedule_cancel` and `cancel_all`.

## Repository delta

```text
native/
|- compose.testnet.yaml
|- observability/grafana/dashboards/execution-risk.json
|- src/aiquanttrader_native/
|  |- domain/execution.py
|  |- execution/{cli,heartbeat,journal,metrics,node,secrets,strategy}.py
|  |- risk/{authority,kill_switch}.py
|  `- sentinel/{cli,metrics,service}.py
`- tests/{unit,integration}/test_{execution,risk,sentinel}*.py

docs/
|- architecture/diagrams/phase-4-execution-risk.mmd
|- migration/PHASE_4_EXECUTION_RISK.md
`- operations/EXECUTION_RISK_RUNBOOK.md
```

## Safety invariants

- Alpha components remain pure kernels and cannot call Nautilus. Only
  `RiskManagedExecutionStrategy` invokes the Nautilus order API.
- An exposure-changing intent is approved against an immutable account/feed
  snapshot. The approval is HMAC-bound to the intent, snapshot, and limits,
  expires after 250 ms by default, and can be consumed once.
- Position size, worst-case pending inventory, notional, current/projected
  leverage, open orders, rate, UTC-day loss, high-water drawdown, stale public
  and private state, reconciliation, connectivity, and operator kill state are
  evaluated synchronously.
- Economic breakers retain bounded reduce-only exits. Disconnect, stale data,
  incomplete reconciliation, and operator kill reject every new order. Cancel
  remains available outside the exposure approval path.
- Intent state is committed with SQLite `WAL` and `synchronous=FULL` before
  submission. A submission older than the unknown-outcome timeout becomes
  `UNKNOWN`; it is queried/reconciled under the same client order identity and
  is never automatically submitted again.
- Modify uses Hyperliquid's cancel-replace behavior through Nautilus while
  preserving the client order identity.
- A corrupt operator-kill file fails closed. Activations and clears are atomic,
  mode `0600`, and append to a separately fsynced audit file. The authority
  reads this operator-owned state itself; strategy input cannot clear it.
- Phase 4's testnet-only boundary originally rejected enabled mainnet wallets.
  Phase 9 replaces that blanket lock only for canary/production configurations
  which pass exact artifact-bound signature verification and explicit durable
  admission; Phase 4 itself remains testnet-scoped.
- Testnet wallet references must use the `testnet-` secret namespace. The
  Compose overlay mounts only the trading key into the trading node and only
  the control key into the sentinel.

## Design decisions

### One Nautilus strategy as the execution gateway

Chosen because Nautilus routes every `Strategy.submit_order` through its own
risk and execution engines. Future market-making, scalping, and ML components
will be pure decision kernels feeding this gateway instead of additional
Nautilus strategies. Multiple order-capable strategies were rejected because
they would make the custom economic risk authority bypassable. The tradeoff is
one in-process translation hop and a deliberately narrow extension point.

### Local synchronous risk instead of a risk microservice

Chosen to keep approval deterministic and available during network partitions.
An external service would add serialization, scheduling, and timeout risk to
every quote. The HMAC is not a cross-host security token; it is a misuse guard
that makes stale, forged, or replayed approvals fail inside the one trusted
process.

### SQLite journal instead of DuckDB or a broker

Chosen because SQLite supplies transactional uniqueness, WAL recovery, and
full synchronous durability in the Python standard library. DuckDB remains an
analytical store and must not have competing hot-path writers. Kafka was
rejected for the single-host first deployment because a broker outage would
enter the order path. Each order transition adds one local transaction and
fsync; this is intentional audit latency and is measured before capital grows.

### External SDK sentinel

Chosen because a dead trading process cannot cancel its own orders. The
sentinel renews an exchange-hosted cancellation deadline only for a fresh,
reconciled, exact-config heartbeat. On failure it also issues an immediate
cancel-all with its independently mounted control wallet and repeats that
cancel at the bounded renewal interval while failure persists. The dead-man
switch does not flatten inventory, so reduce-only flattening remains a
trading-node responsibility when connectivity is available.

### Adapter price normalization enabled

Chosen because Hyperliquid enforces five significant figures plus asset decimal
limits. Letting the pinned Rust adapter normalize prevents avoidable rejects.
The submitted normalized price can differ slightly from a kernel's price, so
execution reports and markouts use the venue/adapter price rather than assuming
the raw proposal was sent unchanged.

## Performance implications

- Risk evaluation is bounded arithmetic over one BTC instrument and a bounded
  one-second submission deque, plus one bounded local read of the persistent
  operator-kill state. It performs no network or analytical-storage I/O.
- Approval hashing covers small canonical JSON objects and uses one HMAC.
- Journal durability adds local transaction/fsync latency before and after
  submission. Network latency remains dominant, but p50/p99 journal time must be
  measured during the testnet soak before mainnet.
- Nautilus's Rust adapter owns HTTP/WebSocket I/O, price normalization, CLOID
  mapping, reconnect, and reconciliation. The native code does not duplicate
  its order state machine.
- Prometheus label values are closed enums. Client IDs, order IDs, wallet
  addresses, and intent IDs never become labels.

## Forward migration

1. Complete the Phase 3 sustained public-feed soak and retain its evidence.
2. Create separate Hyperliquid testnet agent wallets for trading and control.
3. Run the testnet deployment and the full scenario matrix in the execution
   runbook, retaining journal, Prometheus, adapter logs, commit, lock, and image
   digest.
4. Demonstrate process death, stale/malformed heartbeat, persistent operator
   kill, dead-man expiry, and restart reconciliation.
5. Do not enable canary/mainnet. Phase 4 rejects an enabled mainnet path; Phase
   9 must implement artifact-bound cryptographic approval after the intervening
   paper, shadow, and promotion gates pass.

## Rollback

1. Activate the persistent operator kill.
2. Confirm the sentinel cancel-all result and the exchange open-order view.
3. Stop only `trading-node` and `safety-sentinel`; do not remove state volumes.
4. Preserve the SQLite journal, heartbeat, kill audit, metrics evidence, and
   container logs.
5. Re-deploy the prior native image with execution disabled. MT5 remains
   independent throughout this rollback.

## Acceptance evidence

Automated now:

- hard risk limits, stale/disconnect/reconciliation/kill states, reduce-only
  behavior, approval expiry/signature/replay rejection, and rate limits;
- durable lifecycle transitions, duplicate-intent rejection, unknown-outcome
  startup/runtime classification, and no-resubmit behavior;
- pinned Nautilus configuration, post-only/IOC/reduce-only translation, modify
  and cancel gateway behavior, and event journaling;
- secret-file validation, wallet mount separation, canonical endpoint binding,
  crash-safe heartbeat/kill state, dead-man renewal, and emergency cancel;
- strict typing, schema export, non-root/read-only containers, metrics, and
  more than 90% native test coverage.

Still required before Phase 4 is declared accepted:

- credentialed testnet evidence for post-only, IOC, cancel, cancel-replace,
  partial fill, venue reject, reduce-only flatten, restart, unknown outcome,
  reconciliation, exchange dead-man expiry, and operator/local kill;
- verification that the testnet deployment has no mainnet key mounted;
- measured journal/decision/submit latency and retained reviewer sign-off.

Mainnet enablement is not a Phase 4 acceptance item. Phase 9 implements its
separate admission controls and still requires separate evidence and review.
