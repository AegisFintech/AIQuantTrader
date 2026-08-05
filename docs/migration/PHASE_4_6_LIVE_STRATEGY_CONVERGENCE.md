# Phase 4/6 Convergence: Live Strategy Pipeline

Status: implementation complete; credentialed testnet and release evidence pending.

This increment closes the code-level gap between the Phase 6 feature/strategy
kernels and the Phase 4 Nautilus execution gateway. It does not enable any
checked-in environment, create an approval, fund an account, submit a mainnet
order, promote a model, or change the deployed MT5 runtime.

## Architecture

The source diagram is
[`live-strategy-pipeline.mmd`](../architecture/diagrams/live-strategy-pipeline.mmd).

One Nautilus `Strategy` remains the only component permitted to call order
APIs. It now receives managed L2 deltas, trade ticks, mark price, and funding;
normalizes them to the same `KernelMarketState` used by HftBacktest and paper;
runs the shared incremental feature engine and selected pure kernel; constructs
an account snapshot from the reconciled Nautilus portfolio/cache; and submits
each resulting intent through the existing synchronous risk authority.

```text
Nautilus L2/trades -> shared kernel state -> shared features -> pure strategy
Nautilus account/cache + durable equity baseline -> hard risk snapshot
pure intent -> sole gateway -> risk approval -> journal -> Nautilus adapter
```

## Repository delta

```text
src/aiquanttrader/execution/
|- artifacts.py       strict feature/strategy artifact loading and hard-limit checks
|- live.py            L2 normalization, pure pipeline, durable equity baseline
|- strategy.py        event-loop orchestration behind the sole order gateway
|- node.py            pipeline construction and connectivity probe
`- metrics.py         bounded live-cycle, feature, equity, and position metrics

configs/base.toml
`- [live_strategy]    disabled exact artifact selection and cost assumptions

docs/
|- architecture/diagrams/live-strategy-pipeline.mmd
|- migration/PHASE_4_6_LIVE_STRATEGY_CONVERGENCE.md
`- operations/EXECUTION_RISK_RUNBOOK.md
```

## Safety invariants

- `live_strategy.enabled` is false in every checked-in configuration. A live
  strategy cannot be enabled unless exchange execution is also enabled, and
  the trading node refuses to build when execution is enabled without live
  strategy artifacts.
- Alpha kernels still have no Nautilus, wallet, journal, admission, or network
  capability. Only `RiskManagedExecutionStrategy` can submit, modify, or
  cancel an exchange order.
- Nautilus calls `on_start` only after client connection, execution
  reconciliation, and portfolio initialization. Reconciled startup orders are
  canceled and the cache must report zero open orders before alpha resumes.
- Unknown journal commands make reconciliation incomplete. Disconnect,
  inactive mainnet admission, stale public data, or operator kill prevents
  alpha submission and cancel-requests all resting BTC orders. Economic
  breakers drain resting orders first and then permit only authority-approved
  position-reducing intents.
- Market-maker replacements use cancel-confirm-before-replace. A cancel request
  does not clear strategy memory; only a terminal venue event releases the old
  quote identity. This sacrifices some quote presence to prevent overlapping
  old and new inventory.
- A denied or failed submit is never committed to strategy memory. An adapter
  exception remains `UNKNOWN`, stops the event handler, expires the heartbeat,
  and relies on reconciliation rather than resubmission.
- UTC-day start equity and the lifetime high-water mark are mode-`0600`,
  atomically replaced state bound to the execution account. A restart cannot
  reset either loss control. Day rollover resets only the daily baseline.
- Mainnet loads the strategy TOML from the already signature- and hash-verified
  approval artifact directory. Testnet loads the selected checked-in strategy
  file. The feature TOML is part of the exact image and its path/parameters are
  covered by the approved behavior configuration and image/commit identities.

## Decisions, alternatives, and tradeoffs

### Compose the kernels into the sole gateway

Chosen to retain one unbypassable order owner and one event loop. A second
Nautilus alpha strategy was rejected because it could call order APIs directly
or require an asynchronous cross-strategy command channel. A separate strategy
microservice was rejected because serialization, scheduling, and network
failure would enter the latency-sensitive path. In-process composition adds
only bounded feature/kernel work to each managed-book update.

### Use Nautilus's managed L2 book

Chosen because the pinned Hyperliquid adapter already owns snapshots, deltas,
sequence validation, reconnect, and cache updates. Reconstructing a second book
would duplicate state and create reconciliation races. The normalizer copies at
most ten levels and trades received since the prior book update. The pending
trade buffer has a hard bound; overflow, non-monotonic, crossed, empty, or
noncausal state raises, marks execution unhealthy, and cancel-requests resting
orders.

### Rebuild risk state from cache on every decision

Chosen over a separately updated mutable account mirror. Position, leaves
quantity, open-order count, and portfolio equity are read immediately before
each approval, so a two-sided quote receives two distinct snapshots. This adds
bounded cache traversal for one instrument and at most the configured open
orders, while eliminating stale mirror synchronization.

The private-data timestamp represents a successful synchronous execution/data
client connectivity check against an already reconciled account cache. Public
freshness remains the actual L2 receipt timestamp. Treating an unchanged
private account as stale was rejected because private streams are change-based;
connection health plus reconciliation is the authoritative liveness signal.

### Persist only economic baselines, not alpha memory

Chosen because venue order and position state are authoritative after a crash.
Persisting quote memory independently would create a second order-state source.
On restart the gateway cancels reconciled orders and rebuilds alpha state from
zero only after the cache is empty. The tradeoff is a deliberate no-quote
interval during restart, which is preferable to duplicate exposure.

## Performance implications

- Each L2 batch copies and decimal-normalizes at most ten bid and ten ask levels.
- Feature windows are bounded deques; the pure strategy calculation performs no
  I/O. Prometheus labels use fixed action/result values.
- Portfolio/cache reads are bounded by one BTC instrument and hard-capped open
  orders. The equity baseline writes only at initialization, UTC-day rollover,
  or a new high-water mark—not on every tick.
- Durable order-journal transactions remain intentional pre-dispatch latency.
  The new metrics separate market cycles, risk blocks, cancel-before-replace,
  submit outcomes, feature warmup, equity, and inventory for testnet profiling.

## Acceptance and migration steps

1. Build the exact image and validate that both execution and live strategy are
   disabled without explicit runtime overrides.
2. Run the credentialed testnet scenario matrix in the execution runbook with
   the selected scalper artifact and retain raw adapter/journal/metric evidence.
3. Prove L2/trade causality, feature warmup, startup order drain, cancel-confirm
   replacement, kill/stale/disconnect behavior, unknown-outcome reconciliation,
   process death, and equity-baseline restart/day-rollover behavior.
4. Calibrate fees, slippage, fill probability, queue behavior, latency, and
   strategy parameters. The checked-in seed files are not promotion evidence.
5. Repeat with the exact release image on testnet. A passing report may enter
   human promotion review only; it cannot approve or activate production. The
   unsigned release preparer binds the selected live strategy ID into target
   behavior and rejects feature configuration that differs from shadow
   evidence.
6. Mainnet remains blocked until the separate Phase 9 signed bundle, explicit
   admission ledger action, funding authorization, canary procedure, and human
   scale approval all exist outside this increment.

## Rollback

Activate the operator kill, confirm the sentinel and exchange show no resting
orders, flatten or document residual inventory, stop the node and sentinel, and
preserve the journal, equity baseline, heartbeat, metrics, and container logs.
Deploy the prior image with execution disabled. Do not delete state to obtain a
fresh daily-loss or drawdown baseline.
