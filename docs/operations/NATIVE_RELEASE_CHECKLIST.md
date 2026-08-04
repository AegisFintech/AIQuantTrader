# Linux-Native BTC Release Checklist

This checklist applies to the Hyperliquid/NautilusTrader platform after the
relevant implementation phases land. It does not replace the MT5 release
checklist while MT5 remains deployed.

## 1. Identify the release

- [ ] Record commit SHA, signed container digest, dependency lock digests,
  configuration hash, schema version, strategy/model hash, and rollback target.
- [ ] Confirm the deployment stage: testnet, paper, shadow, canary, or production.
- [ ] Confirm the only allowed instrument is `BTC-USD-PERP.HYPERLIQUID`.
- [ ] Confirm the target account/subaccount and environment without printing
  private keys.

## 2. Validate code and artifacts

- [ ] Formatting, lint, type checks, unit, integration, contract, replay, chaos,
  and applicable performance tests pass.
- [ ] Secret scan, dependency audit, SBOM generation, image scan, and provenance
  verification pass.
- [ ] Raw/normalized schemas and configuration migrations are compatible.
- [ ] The exact image passed the stage-specific testnet or shadow rehearsal.

## 3. Validate market data and time

- [ ] Public/private feed freshness and reconnect metrics are healthy.
- [ ] Book integrity, publication cadence, duplicate, and gap checks are healthy.
- [ ] Raw archive, Parquet finalization, manifests, and disk headroom are healthy.
- [ ] Host time synchronization and monotonic timing checks are healthy.

## 4. Validate execution and risk

- [ ] Trading and sentinel API wallets are distinct and have the intended master
  or subaccount address.
- [ ] Open orders, positions, margin, leverage, funding, and PnL reconcile with
  the exchange.
- [ ] Daily loss, drawdown, position, inventory, leverage, order size, open
  order, order-rate, stale-data, disconnect, and operator-kill limits match the
  signed policy and application hard bounds.
- [ ] Exchange dead-man cancellation is armed and the sentinel is renewing it.
- [ ] Cancel-all, reduce-only, flatten, and rollback commands are available.

## 5. Validate governance

- [ ] The candidate completed the required backtest, walk-forward, paper, and
  shadow stages with immutable evidence.
- [ ] Production/canary has a valid human signature bound to the exact account,
  image, code, data, model, configuration, capital ceiling, limits, and expiry.
- [ ] The approval has not been reused for a different release or capital level.
- [ ] Production promotion and capital increase remain impossible for research
  automation.

## 6. Deploy

- [ ] Snapshot current orders, positions, risk state, champion manifest, and
  operational metrics.
- [ ] Deploy by immutable image digest.
- [ ] Keep exposure disabled until startup reconciliation and all health checks
  pass.
- [ ] Enable only the approved stage and capital ceiling.
- [ ] Observe first book updates, decisions, commands, acknowledgements, fills,
  PnL attribution, and sentinel renewal.

## 7. Post-deploy verification

- [ ] No unexpected instrument, order type, position, or increase in limits.
- [ ] Submit-to-ack, cancel, feature, decision, and event-loop latency remain
  within policy.
- [ ] Reject, reconnect, reconciliation, adverse markout, and inventory metrics
  remain within policy.
- [ ] Alerts reach the operator through the tested route.
- [ ] Deployment registry points to the running artifact and rollback target.

## 8. Rollback or incident response

- [ ] Halt new exposure.
- [ ] Cancel resting orders and confirm exchange state.
- [ ] Reduce or flatten inventory according to the approved incident policy.
- [ ] Restore the last approved native artifact or remain halted.
- [ ] Reconcile account state before re-enabling any order path.
- [ ] Preserve journals, metrics, raw data, configuration fingerprints, and the
  deployment manifest for incident analysis.

Rollback never authorizes an unapproved artifact, increased capital, or
automatic return to MT5/XAU trading.
