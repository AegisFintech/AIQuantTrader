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

## Build and preflight

```bash
export AQT_TESTNET_ACCOUNT_ADDRESS=0x...
export AQT_TESTNET_TRADING_WALLET_FILE=/secure/path/testnet-trading.key
export AQT_TESTNET_CONTROL_WALLET_FILE=/secure/path/testnet-control.key

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

Set the exact digest, commit, fingerprint, testnet account, two testnet secret
file paths, and every risk override from the release specification. Do not
mount or reference a mainnet credential:

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

Retain one evidence hash per required scenario plus the raw venue export,
private/public event journal, order journal, reconciliation records, metrics,
alerts, container inspection, final account/open-order state, and proof that no
mainnet credential was present. Construct
`TestnetDressRehearsalObservation` according to
`native/schemas/governance.schema.json`; counts must reconcile, wallet roles
must be distinct, unknown outcomes must all be resolved, and the final position
and open-order count must be zero. Evaluate it only with the policy frozen
before the run:

```bash
aqt-governance evaluate-testnet \
  --observation /secure/release/testnet-observation.json \
  --policy configs/production/testnet-dress-rehearsal-v1.toml \
  --output /secure/release/testnet-evidence.json
```

The evaluator reports each gate and exits nonzero on a failure. A pass stops at
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

## Planned stop

1. Activate the operator kill and confirm exchange open orders are empty.
2. Confirm position is flat or document the accepted residual inventory risk.
3. Stop the trading node, then the sentinel.
4. Preserve the state volume. Do not delete WAL files or the kill audit.

The sentinel deliberately does not disarm the scheduled exchange cancellation
on shutdown. This favors cancel safety over retaining unattended resting orders.
