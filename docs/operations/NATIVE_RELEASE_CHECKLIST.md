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
- [ ] The release behavior fingerprint was rendered before final testnet and
  matches the retained report and unsigned bundle receipt.

## 3. Validate market data and time

- [ ] Public/private feed freshness and reconnect metrics are healthy.
- [ ] Book integrity, publication cadence, duplicate, and gap checks are healthy.
- [ ] Raw archive, Parquet finalization, manifests, and disk headroom are healthy.
- [ ] Host time synchronization and monotonic timing checks are healthy.

## 4. Validate execution and risk

- [ ] For paper/shadow, confirm account and wallet references are absent and the
  container has no secret mount or exchange-order-capable client.
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

- [ ] Paper evidence binds calibrated baseline/pessimistic scenarios, required
  independent decisions/fills/regimes, drift, operational drills, and the
  frozen policy without post-result threshold changes.
- [ ] The candidate completed the required backtest, walk-forward, paper, and
  shadow stages with immutable evidence.
- [ ] Production/canary has a valid human signature bound to the exact account,
  image, code, data, model, configuration, capital ceiling, limits, and expiry.
- [ ] Approval key ID and public-key fingerprint match the offline trust root;
  trading/control key-derived addresses match their separately signed roles.
- [ ] The dependency lock and every manifest-bound artifact hash match bytes in
  the read-only release bundle.
- [ ] The approval has not been reused for a different release or capital level.
- [ ] For continued production, the next signed renewal binds the current
  authorization and unchanged admission/release/capital, is applied before
  expiry, and its ledger/heartbeat/sentinel expiry values agree.
- [ ] If the release contributes to Phase 10 observation, preserve checkpointed
  ledger generations, signed renewal/envelope pairs, exact release artifacts,
  hash-linked audits with typed risk reasons, incident review, and drill
  reports; sentinel/dead-man sample gaps remain below five minutes.
- [ ] Before Phase 10 stop review, independently assemble and verify the exact
  eleven-category legacy archive; restore hashes/sizes match, the frozen
  recursive credential scan has zero findings, `mt5-final` is annotated and
  resolves to the archived commit, and 365 days of retention remain.
- [ ] Independently assemble and verify final MT5 state from the same archive;
  raw trade report, broker export, MT5 status, pause flag, and all five writer
  inventories match their normalized records, capture skew is within five
  minutes, and the state remains inside its one-hour freshness window.
- [ ] The complete 15-scenario final-testnet report passed with real retained
  evidence, flat final state, resolved unknown outcomes, and no mainnet key.
- [ ] The unsigned bundle was created at a new path, all receipt hashes and
  modes were checked, and no signature/private key was produced on the host.
- [ ] The offline signer signed the exact unchanged
  `deployment-approval.unsigned.json` bytes; the final approval is byte-for-byte
  identical before detached-signature verification.
- [ ] The credential-free controller explicitly admitted the verified identity;
  execution and sentinel independently observe the same active ledger record.
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
- [ ] Execution and sentinel admission gauges are one, approval time remaining
  is positive, and account/vault equity does not exceed approved capital.

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
