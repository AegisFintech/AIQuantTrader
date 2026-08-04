# Mainnet Canary Runbook

This runbook is fail-closed. Do not execute its mainnet steps until Phases 3-8,
the final exact-image testnet rehearsal, two-person review, and a signed canary
approval are complete. Never paste wallet or approval private keys into a shell
command, environment variable, log, or repository file.

## Roles and prerequisites

- Approver: holds the offline Ed25519 key and never accesses the production
  host with that private key.
- Release operator: verifies image/commit/evidence and controls admission.
- Risk operator: independently verifies account/vault, capital, limits, BTC
  instrument, alerts, rollback target, and venue access.
- Trading node: mounts only `/run/secrets/mainnet-trading-wallet`.
- Sentinel: mounts only `/run/secrets/mainnet-control-wallet`.

Record the exact 40-character commit and `sha256:` image digest. Pull by digest,
scan it, and run the complete testnet rehearsal without rebuilding. Confirm the
approval expires within seven days and the rollback deployment is available.

## Approval bundle

The read-only bundle contains:

```text
deployment-approval.json
deployment-approval.sig.json
artifact-manifest.json
approver-ed25519.pub.pem
artifacts/
  uv.lock
  dataset-manifest.json
  model-manifest.json
  feature-schema.json
  strategy-config.toml
  risk-policy.json
  shadow-evidence.json
  testnet-evidence.json
```

Production approval adds `canary-evidence.json`. The detached signature envelope
contains `algorithm=ed25519`, key ID, canonical approval SHA-256, and the base64
64-byte signature. Use `aqt-governance canonicalize-approval` before offline
signing. Sign the output bytes exactly; the command deliberately emits no
trailing newline.

Mount the completed directory read-only. Set Compose inputs from a root-owned,
mode-0600 environment file; inspect rendered configuration without printing
secret files:

```bash
docker compose -f native/compose.mainnet.yaml \
  --profile mainnet-admission config --quiet
```

The image reference must render as `repository@sha256:...`, with no `build:`.

## Verify, admit, and launch

The controller has no wallet mount. First run its default `verify` command.
Then repeat with `admit`, supplying a named human actor and reviewed reason:

```bash
docker compose -f native/compose.mainnet.yaml \
  --profile mainnet-admission run --rm deployment-controller \
  admit --config-dir /etc/aiquanttrader-native --environment canary \
  --code-identity "$AQT_MAINNET_COMMIT_SHA" \
  --image-identity "$AQT_MAINNET_IMAGE_DIGEST" \
  --dependency-lock-path /opt/aiquanttrader/release/uv.lock \
  --actor "$AQT_RELEASE_ACTOR" --reason "$AQT_RELEASE_REASON"
```

Treat the JSON admission ID as release evidence. Launch the sentinel first,
then the trading node:

```bash
docker compose -f native/compose.mainnet.yaml \
  --profile mainnet-live up -d safety-sentinel
docker compose -f native/compose.mainnet.yaml \
  --profile mainnet-live up -d trading-node
```

Before allowing strategy input, verify both admission gauges equal one,
heartbeat and reconciliation are healthy, approval time remains positive,
capital is at or below the signed limit, there are no unknown orders, dead-man
renewals succeed, alert delivery works, and the venue shows only BTC perpetual.

## Canary operation

Fund only the approved isolated amount. Do not raise a Compose risk value after
signing; it changes the configuration hash and prevents startup. Observe orders,
fills, maker ratio, fees, funding, markouts, PnL, drawdown, rejection rate,
latency, reconnects, reconciliation, and capital continuously.

Complete and retain the five required drills at bounded exposure:

1. operator kill and cancel confirmation;
2. heartbeat/dead-man expiry and exchange cancellation;
3. process restart with order/position reconciliation;
4. trading and control credential rotation, one at a time;
5. admission ledger and operational-evidence backup/restore.

Evaluate the observation with the frozen policy. A passing report means only
that a reviewer may consider a new production approval:

```bash
aqt-governance evaluate-canary --config-dir native/configs \
  --environment canary --deployment-id "$AQT_MAINNET_DEPLOYMENT_ID" \
  --observation canary-observation.json \
  --policy native/configs/production/canary-evidence-v1.toml \
  --output canary-evidence.json
```

## Incident, rollback, and flatten

On stale data, reconciliation failure, unknown order outcome, wallet mismatch,
capital breach, approval expiry, or unexpected behavior:

1. activate the persistent execution kill switch;
2. confirm the sentinel has canceled all resting orders;
3. revoke or roll back admission through the credential-free controller;
4. stop the trading node but keep the sentinel running until cancellation is
   independently confirmed;
5. if inventory remains, use the account owner's reviewed venue control plane
   to reduce or flatten it—the sentinel cannot place a flattening order;
6. archive the ledger, heartbeat, journal, venue export, metrics, configuration,
   and incident timeline.

Rollback and revoke are terminal anti-replay states. Restoring any deployment
requires a fresh non-expired approval and explicit admission; no command
automatically restarts MT5 or another native champion.

## Production scale

Never edit the canary admission into production. Bind the retained canary report
into a new production artifact manifest. The new approval must name the active
canary approval as `prior_approval_id` and its deployment as the rollback
target. Repeat verification and explicit admission. The ledger transactionally
supersedes the canary; it does not reactivate it after rollback.
