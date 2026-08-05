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

## Freeze and rehearse the exact release

Create a root-owned, mode-0600 TOML release specification outside the
repository. Validate it against `ReleaseBundleSpec` in
`native/schemas/governance.schema.json`. It must contain the stage, unique
deployment and approval IDs, rollback deployment, exact commit/image, mainnet
account/vault and distinct wallet addresses, capital, approver, timezone-aware
approval/expiry no more than seven days apart, complete risk limits, and
absolute paths to every immutable source artifact. Production additionally
requires `prior_approval_id` and an absolute `canary_evidence` path.

Render the enabled target behavior without loading ambient environment
variables or a credential:

```bash
aqt-governance release-fingerprint --config-dir native/configs \
  --environment canary --spec /secure/release/canary-release.toml \
  --output /secure/release/behavior-configuration.json
```

Record the printed SHA-256. Run the exact digest on testnet with
`native/compose.rehearsal.yaml` as described in the execution-risk runbook.
Build the typed observation only from retained venue, order journal, metric,
and drill evidence. Evaluate it against the policy frozen before the run:

```bash
aqt-governance evaluate-testnet \
  --observation /secure/release/testnet-observation.json \
  --policy native/configs/production/testnet-dress-rehearsal-v1.toml \
  --output /secure/release/testnet-evidence.json
```

Exit status zero and `awaiting_canary_approval=true` mean only that the bundle
may receive independent review. A failed or incomplete report must not be
edited into a pass; correct the cause and collect a new observation.

Prepare the unsigned directory at a path which does not already exist:

```bash
aqt-governance prepare-release --config-dir native/configs \
  --environment canary --spec /secure/release/canary-release.toml \
  --output-dir /secure/release/canary-release-unsigned
```

The command rejects incompatible strategy/model/schema/lock identities,
an image-resident live feature configuration that differs from shadow evidence,
unpassed or mismatched evidence, excess strategy/risk bounds, ambiguous files,
and stage/rollback errors. The target behavior enables live alpha and derives
its strategy ID from the exact strategy artifact. The command never reads a
signing key and never writes admission state.

## Approval bundle

The read-only bundle contains:

```text
deployment-approval.json
deployment-approval.unsigned.json
deployment-approval.sig.json
artifact-manifest.json
approver-ed25519.pub.pem
behavior-configuration.json
release-bundle-receipt.json
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
64-byte signature. Transfer `deployment-approval.unsigned.json` to the offline
approver, review it against the receipt and artifacts, and sign those exact
bytes. Place the unchanged bytes in the final bundle as
`deployment-approval.json`; a rename or byte-for-byte copy is allowed, but
re-serialization is not. The offline approver returns only the approval,
detached signature envelope, and reviewed public key. Never transfer the
private key to the release or production host.

`aqt-governance canonicalize-approval` remains available for independently
assembled legacy inputs. Do not run it over preparer output because the
preparer has already emitted canonical bytes and the receipt binds them.

Mount the completed directory read-only. Set Compose inputs from a root-owned,
mode-0600 environment file; inspect rendered configuration without printing
secret files:

`AQT_MAINNET_LIVE_STRATEGY_ID` must equal the `strategy_id` in the approved
`artifacts/strategy-config.toml`. A missing or different value either prevents
Compose rendering or changes the signed behavior fingerprint and fails
admission.

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
