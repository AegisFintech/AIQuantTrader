# Execution and Risk Runbook

This runbook is testnet-only for Phase 4. It does not authorize mainnet trading.
All commands run from `native/`. Never print, copy into shell history, or commit
a private key.

## Preconditions

1. Use a dedicated Hyperliquid testnet master account, then create two different
   **testnet agent/API wallets** under it: one trading wallet and one control
   wallet.
2. Fund the testnet account and verify the master account address independently.
3. Store each 32-byte hex private key in a different mode-`0600` file outside
   the repository.
4. Record the commit, `uv.lock` digest, image digest, account address, and frozen
   risk configuration in the test evidence directory.

The account address identifies the master account. Supplying an agent key
without this address causes account queries and private subscriptions to follow
the empty agent account and prevents correct reconciliation.

The execution overlay explicitly sets both
`AQT_NATIVE__EXECUTION__ENABLED=true` and
`AQT_NATIVE__LIVE_STRATEGY__ENABLED=true`. The node refuses to build the live
pipeline if either selected feature/strategy artifact is missing, malformed,
inconsistent with the selected strategy ID, or exceeds hard order/inventory
limits. Every checked-in environment still sets both switches false.

## Build and preflight

```bash
export AQT_TESTNET_ACCOUNT_ADDRESS=0x...
export AQT_TESTNET_TRADING_WALLET_FILE=/secure/path/testnet-trading.key
export AQT_TESTNET_CONTROL_WALLET_FILE=/secure/path/testnet-control.key
export AQT_TESTNET_LIVE_STRATEGY_ID=order-flow-scalper-v1
export AQT_TESTNET_LIVE_STRATEGY_CONFIG_PATH=strategies/order-flow-scalper-v1.toml

docker compose -f compose.yaml -f compose.testnet.yaml \
  --profile execution-testnet config --quiet
docker compose -f compose.yaml -f compose.testnet.yaml \
  --profile execution-testnet build
```

Inspect the rendered mounts without displaying secret contents. The trading
node must have only `/run/secrets/testnet-trading-wallet`; the sentinel must
have only `/run/secrets/testnet-control-wallet`. No path containing `mainnet-`
may exist in either container.

## Start

```bash
docker compose -f compose.yaml -f compose.testnet.yaml \
  --profile execution-testnet up -d trading-node safety-sentinel
docker compose -f compose.yaml -f compose.testnet.yaml ps
```

Before an order drill, confirm:

- the node completed Nautilus reconciliation;
- the heartbeat is fresh, `execution_healthy=true`, and
  `reconciliation_complete=true`;
- the sentinel health gauge is one and its scheduled deadline advances;
- the operator kill is inactive;
- exchange UI/API account, positions, and open orders match the journal/cache;
- configured leverage is no greater than the deployment limit. Nautilus does
  not set leverage automatically.
- `state/execution/equity-baseline.json` exists with mode `0600`, belongs to the
  exact execution account, and preserves the expected UTC-day start and
  lifetime high-water equity after a controlled restart;
- any reconciled startup orders were canceled and the cache reached zero open
  orders before `aqt_execution_live_market_cycles_total{result="processed"}`
  increased;
- the selected live strategy ID and strategy SHA-256 match the test evidence.

The checked-in order-flow scalper is a seed configuration, not profitability
or promotion evidence. The checked-in market-maker remains inert because it
requires a calibrated fill model while the seed feature file is explicitly
uncalibrated. Do not weaken that gate to manufacture testnet activity.

## Required scenario matrix

Run with the smallest venue-valid quantity and keep total notional below the
frozen `max_order_notional_usd`. Capture the intent, risk decision, journal
events, adapter event, exchange status, position, and Prometheus sample for each
case.

| Scenario | Required result |
|---|---|
| Passive post-only quote | accepted/resting, then canceled by CLOID |
| Crossing post-only quote | authoritative venue rejection |
| Non-marketable IOC | venue rejection/cancel semantics recorded without a false acceptance |
| Marketable IOC | fill or partial fill plus canceled remainder, no duplicate quantity |
| Cancel-replace | same CLOID, new venue OID, old-leg cancel not treated as terminal |
| Partial fill then cancel | cumulative fill exact; residual canceled |
| Reduce-only exit | absolute position falls; an exposure-increasing reduce-only intent is denied |
| Duplicate intent | second call rejected locally; one venue order maximum |
| Forced response timeout | state becomes `UNKNOWN`; reconciliation resolves by CLOID; no resubmit |
| Node restart | open orders, fills, and position reconcile before approval resumes |
| Strategy restart with resting quotes | reconciled quotes cancel; alpha waits for zero open orders; no duplicate quote |
| Quote revision | old quote cancel is confirmed before replacement submit |
| Feature warmup | no intent before the configured causal warmup count |
| Strategy denial | denied intent is absent from alpha memory and may be reconsidered only as a new deterministic intent |
| Public/private stale data | new exposure denied; cancel remains available |
| Daily loss/drawdown | only bounded reduce-only orders remain available |
| Operator kill | local approvals halt and sentinel cancels all resting orders |
| Trading-node death | sentinel stops renewal/immediately cancels; scheduled cancel is observed |
| Sentinel death | previously scheduled exchange cancel fires within the configured bound |

Partial fill is market-dependent; repeat a bounded IOC/passive scenario until a
real partial fill occurs. Do not synthesize a pass from a unit test.

## Final exact-release dress rehearsal

Phase 9 repeats the complete matrix with the immutable release image. Use
`compose.rehearsal.yaml`, not the development build overlay. The target
behavior fingerprint comes from `aqt-governance release-fingerprint`; testnet
account and wallet identities replace mainnet identities, while code, image,
dependency lock, dataset/model/feature/strategy artifacts, risk, and all other
behavior remain bound to the proposed release.

Set a new `AQT_REHEARSAL_ID`, plus the exact digest, commit, fingerprint,
testnet account, two testnet secret
file paths, `AQT_REHEARSAL_LIVE_STRATEGY_ID` from the release strategy artifact,
`AQT_REHEARSAL_STRATEGY_CONFIG_FILE` pointing to those exact artifact bytes,
and every risk override from the release specification. Compose mounts that
file read-only under the configured rehearsal-only path. Do not mount or
reference a mainnet credential:

```bash
docker compose -f compose.rehearsal.yaml \
  --profile release-rehearsal config --quiet
docker compose -f compose.rehearsal.yaml \
  --profile release-rehearsal up -d rehearsal-sentinel rehearsal-trading-node
```

Inspect the rendered configuration and container mounts. The image must be
`repository@sha256:...`, there must be no build context, the trading node must
mount only the testnet trading wallet, and the sentinel only the testnet control
wallet. Confirm the release commit, image, and behavior metadata inside both
containers without displaying either secret file.
The rehearsal ID creates three uniquely named volumes. Confirm they did not
exist before the run. Never reuse an ID or delete an older reviewed rehearsal
to obtain empty state.

During the run, preserve `state/execution/acceptance-events.jsonl` from the
execution state volume and `acceptance-events.jsonl` from the sentinel-owned
evidence volume mounted at `/var/lib/aiquanttrader/sentinel-state`. The shared
execution state remains read-only inside the sentinel. These are canonical
predecessor-hashed operational streams and must not be edited, concatenated, or
shared between processes. Capture raw venue exports, Prometheus snapshots,
process lifecycle, kill audit, and rendered mount/config inspection without
copying any secret content.

After the planned stop, build a new mode-`0700` evidence directory. It has a
closed inventory; names under `raw/` are operator-chosen and bound by the
manifest, while control and scenario names are fixed:

```text
testnet-rehearsal-<id>/
|- run-manifest.json
|- operational-facts.json
|- final-venue-state.json
|- scenarios/
|  |- passive_post_only.json
|  |- crossing_post_only_reject.json
|  |- non_marketable_ioc.json
|  |- marketable_ioc.json
|  |- cancel_replace.json
|  |- partial_fill_cancel.json
|  |- reduce_only.json
|  |- duplicate_intent.json
|  |- unknown_outcome_reconciliation.json
|  |- node_restart_reconciliation.json
|  |- stale_data_kill.json
|  |- loss_drawdown_reduce_only.json
|  |- operator_kill.json
|  |- trading_node_death.json
|  `- sentinel_death.json
`- raw/
   |- execution-journal.sqlite3
   |- execution-events.jsonl
   |- sentinel-events.jsonl
   |- execution-metrics.prom
   |- sentinel-metrics.prom
   |- venue-orders.json
   |- venue-fills.json
   |- venue-account.json
   |- kill-switch.audit.jsonl
   |- process-events.jsonl
   `- compose-inspection.txt
```

The raw inventory must bind exactly one artifact for each
`EvidenceCategory` in `native/schemas/acceptance.schema.json`. Do not leave
notes, WAL/SHM files, temporary downloads, or unrelated logs inside the bundle.
Copy the execution database only after its owner has stopped and the SQLite WAL
has checkpointed; validate the copy independently before deleting nothing from
the preserved state volume.

The assembler requires these exact `check_id` values. Additional checks are
allowed, but none of these may be omitted:

| Scenario file | Required check IDs |
|---|---|
| `passive_post_only` | `post_only_rested`, `client_order_identity`, `cancel_confirmed` |
| `crossing_post_only_reject` | `venue_rejected`, `no_fill` |
| `non_marketable_ioc` | `ioc_terminal`, `no_false_acceptance` |
| `marketable_ioc` | `fill_accounted`, `remainder_terminal` |
| `cancel_replace` | `old_leg_terminal`, `replacement_identity`, `no_overlap` |
| `partial_fill_cancel` | `cumulative_fill_exact`, `residual_cancel_confirmed` |
| `reduce_only` | `absolute_position_reduced`, `increase_denied` |
| `duplicate_intent` | `local_duplicate_denied`, `single_venue_order` |
| `unknown_outcome_reconciliation` | `unknown_recorded`, `reconciled_by_client_id`, `not_resubmitted` |
| `node_restart_reconciliation` | `reconciliation_precedes_approval`, `orders_fills_position_match`, `no_duplicate_order` |
| `stale_data_kill` | `new_exposure_denied`, `cancel_available` |
| `loss_drawdown_reduce_only` | `new_exposure_denied`, `bounded_reduce_only_available` |
| `operator_kill` | `approval_halted`, `cancel_all_confirmed` |
| `trading_node_death` | `sentinel_detected_failure`, `cancel_all_confirmed`, `deadman_observed` |
| `sentinel_death` | `scheduled_cancel_fired`, `fired_within_bound` |

Each check's `actual` and `required` fields must state the reviewed observation
and criterion. A generic statement that evidence was retained is not a valid
substitute. The two `cancel_all_confirmed` checks count only when the matching
execution/sentinel audit action also succeeded.

Create all control files with the canonical domain JSON serialization and one
trailing newline. `run-manifest.json` binds the exact commit, image, lock,
dataset, model selection, feature schema, strategy, risk, target behavior,
account/vault, distinct wallet addresses, run interval, and every raw file's
path, size, digest, and capture interval. Each scenario file records explicit
checks, invalidating events, and the raw artifact paths used for that result.
`final-venue-state.json` must be a real post-stop testnet account snapshot.

`operational-facts.json` records reviewed facts not safely derivable from the
local journal: reconciliation failures, risk breaches, actual exchange
dead-man cancellations, and absence of a mainnet credential. Bind it to the
venue-account, process-event, and configuration-inspection artifacts. Reviewers
must compare those claims with the raw sources; a typed zero is not proof by
itself.

Assemble and independently reproduce the observation without mounting either
wallet:

```bash
aqt-acceptance assemble \
  --evidence-root /secure/release/testnet-rehearsal-<id> \
  --output /secure/release/testnet-observation.json
aqt-acceptance verify \
  --evidence-root /secure/release/testnet-rehearsal-<id> \
  --observation /secure/release/testnet-observation.json
```

Assembly checks the complete inventory twice, raw hashes and intervals, every
scenario's required category lineage, SQLite integrity and lifecycle facts,
operational hash chains, account identity, and final state. It rejects a live,
extra, missing, mutable, noncanonical, traversing, symlinked, or writable
bundle. Output is a new mode-`0600` file and is never overwritten.

Evaluate the resulting observation only with the policy frozen before the run:

```bash
aqt-governance evaluate-testnet \
  --observation /secure/release/testnet-observation.json \
  --policy configs/production/testnet-dress-rehearsal-v1.toml \
  --output /secure/release/testnet-evidence.json
```

The assembler does not decide whether the rehearsal passed. The evaluator
reports each gate and exits nonzero on a failure. A pass stops at
`awaiting_canary_approval`; it does not sign a release, authorize funding, or
permit mainnet execution. Stop the rehearsal using the planned-stop sequence
and preserve its volumes until independent review is complete.

## Operator kill

Activate before investigating any inconsistent state:

```bash
docker compose -f compose.yaml -f compose.testnet.yaml run --rm trading-node \
  kill --config-dir /etc/aiquanttrader-native --environment testnet \
  --actor operator@example --reason "incident reference"
```

Verify the persisted state and exchange open orders. Clearing the kill permits
future approvals; it does not submit an order and must occur only after account,
journal, cache, feeds, and sentinel reconcile:

```bash
docker compose -f compose.yaml -f compose.testnet.yaml run --rm trading-node \
  clear-kill --config-dir /etc/aiquanttrader-native --environment testnet \
  --actor operator@example --reason "reconciliation complete"
```

## Unknown outcomes

Never resubmit an intent in `pending_submit`, `submitted`, or `unknown` state.
Use the original client order ID to reconcile open orders, historical order
status, fills, and position. If those disagree, keep the operator kill active,
cancel through the sentinel, preserve evidence, and escalate. A fresh intent ID
is not a valid workaround for an unresolved old intent.

## Live pipeline integrity

The normal path is:

```text
managed L2 deltas + trades -> KernelMarketState -> MicrostructureSnapshot
-> pure selected kernel -> OrderIntent -> synchronous risk -> sole gateway
```

Any empty/crossed book, unexpected instrument, noncausal timestamp, or
non-increasing L2 receipt timestamp is fatal. The gateway marks its heartbeat
unhealthy, requests cancel-all when possible, and lets the node shut down; the
independent sentinel is the final cancellation boundary.

The private-data freshness field records a successful synchronous data and
execution client connectivity check over a portfolio that Nautilus reconciled
before strategy start. Private streams are change-based, so an unchanged
account is not made stale by inventing periodic account events. Public-data
freshness always uses the actual L2 local receipt timestamp.

Do not delete or edit `equity-baseline.json` to clear a breaker. Corruption,
account mismatch, or clock rollback fails startup/runtime closed. Preserve the
file with the order journal during backup, restore, incident response, and
rollback.

## Planned stop

1. Activate the operator kill and confirm exchange open orders are empty.
2. Confirm position is flat or document the accepted residual inventory risk.
3. Stop the trading node, then the sentinel.
4. Preserve the state volume. Do not delete WAL files or the kill audit.

After the owner processes have stopped, export a stable journal copy for the
acceptance bundle. Keep the original volume, WAL, and kill audit unchanged until
the observation and its independent verification are complete.

The sentinel deliberately does not disarm the scheduled exchange cancellation
on shutdown. This favors cancel safety over retaining unattended resting orders.
