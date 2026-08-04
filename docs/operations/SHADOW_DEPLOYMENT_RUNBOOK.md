# BTC Shadow Deployment Runbook

Use this runbook only for Phase 8. Shadow records what the native production
path would do; it cannot submit a Hyperliquid order. It does not replace or
modify the deployed XAUUSD MT5 demo runtime.

## 1. Preconditions

- Complete the native release checklist through the shadow stage.
- Use Debian Linux with Docker Compose, synchronized UTC time, monitored disk,
  and tested host restart procedures.
- Build and scan one immutable native runtime image. Record registry reference,
  `sha256:<digest>`, commit, `uv.lock` hash, effective config hash, feature,
  strategy, scenario, and policy hashes.
- Confirm `native/configs/shadow.toml` has `execution.enabled = false`,
  `shadow.enabled = true`, and no account or wallet reference.
- Confirm the baseline and pessimistic scenario calibration state. The
  checked-in seeds are intentionally `uncalibrated` and cannot pass evidence.
- Do not export or mount any Hyperliquid wallet, account identity, production
  approval, or `/run/secrets` volume.

Set immutable deployment identities without printing secrets:

```bash
export AQT_NATIVE_IMAGE_REPOSITORY='registry.example/aiquanttrader-native'
export AQT_NATIVE_IMAGE_DIGEST='sha256:<digest>'
export AQT_NATIVE_CODE_IDENTITY="$(git rev-parse HEAD)"
```

Compose constructs the image reference as
`$AQT_NATIVE_IMAGE_REPOSITORY@$AQT_NATIVE_IMAGE_DIGEST`, so a mutable tag
cannot be deployed. Record the resolved registry digest in release evidence.

## 2. Preflight

```bash
cd native
uv run --frozen ruff format --check src tests scripts
uv run --frozen ruff check src tests scripts
uv run --frozen mypy
uv run --frozen pytest --cov
uv run --frozen aqt-native export-schemas --output schemas --check
uv run --frozen aqt-native validate-config --config-dir configs --environment shadow
docker compose -f compose.shadow.yaml config --quiet
```

Inspect the rendered engine service:

```bash
docker compose -f compose.shadow.yaml config | sed -n '/shadow-engine:/,/shadow-observer:/p'
```

It must contain `network_mode: none`, no `ports`, no `secrets`, a read-only
ingress mount, and only the shadow-state writable volume. Gateway and engine
state volumes must be different.

## 3. Launch

```bash
docker compose -f compose.shadow.yaml pull
docker compose -f compose.shadow.yaml up -d shadow-gateway
docker compose -f compose.shadow.yaml ps
docker compose -f compose.shadow.yaml up -d shadow-engine shadow-observer
```

Check all roles:

```bash
docker compose -f compose.shadow.yaml exec shadow-engine \
  aqt-shadow healthcheck --state-root /var/lib/aiquanttrader/state
curl --fail --silent http://127.0.0.1:9113/health/ready
curl --fail --silent http://127.0.0.1:9113/metrics | \
  grep '^aqt_shadow_network_egress_capability 0'
curl --fail --silent http://127.0.0.1:9109/metrics | \
  grep '^aqt_market_data_connected 1'
```

Prove the kernel boundary from inside the engine:

```bash
docker compose -f compose.shadow.yaml exec shadow-engine sh -c \
  'test ! -e /run/secrets && ! awk "NR>1 && \$2==\"00000000\" && \$1!=\"lo\" {exit 1}" /proc/net/route'
```

The application repeats the route proof on every startup and refuses to run
with an IP default route.

## 4. Routine health

```bash
docker compose -f compose.shadow.yaml ps
docker compose -f compose.shadow.yaml logs --tail 100 shadow-gateway shadow-engine shadow-observer
docker compose -f compose.shadow.yaml exec shadow-engine \
  aqt-shadow status --state-root /var/lib/aiquanttrader/state
```

Alert on:

- gateway/engine/observer health failure or restart;
- feed disconnected, feature not ready after warmup, or operator kill;
- ingress p99 above 250 ms or engine-cycle p99 above 10 ms under the frozen
  policy;
- raw recorder reconnect, integrity, disk, or stale-feed events;
- nonzero egress-capability gauge;
- command count diverging from approved decision count;
- counterfactual loss/drawdown, adverse markout, fill, or drift breach;
- ingress database, command journal, or audit failure.

Do not treat a healthy observer as proof of a healthy gateway. Monitor both
scrape jobs and the engine status contract.

## 5. Kill and recovery

Activate kill through a one-shot no-network engine namespace:

```bash
docker compose -f compose.shadow.yaml exec shadow-engine \
  aqt-shadow kill activate \
  --state-root /var/lib/aiquanttrader/state \
  --actor '<operator-id>' \
  --reason '<incident-or-drill-id>'
```

Verify `operator_kill=true`, health is not ready, and all counterfactual orders
reach canceled state after response latency. Clear only after the gateway,
ingress, journal, clock, disk, and config are reviewed:

```bash
docker compose -f compose.shadow.yaml exec shadow-engine \
  aqt-shadow kill clear \
  --state-root /var/lib/aiquanttrader/state \
  --actor '<operator-id>' \
  --reason '<review-record-id>'
```

Shadow kill has no venue-side effect because no venue order exists.

## 6. Required drills

Run each drill on the intended host. Store command output, timestamps, status
before/after, alerts, relevant metrics, and recovery result in one non-empty
artifact. Bind it to the active run:

```bash
docker compose -f compose.shadow.yaml exec shadow-engine \
  aqt-shadow record-drill <drill> \
  --state-root /var/lib/aiquanttrader/state \
  --evidence-file /var/lib/aiquanttrader/state/shadow/evidence/<file>
```

Allowed drill names:

- `host_reboot`: reboot the host, verify gateway-first recovery, exact run
  identity, checkpoint cursor, cancel-on-resume, and no missing audit sample
  hidden from availability.
- `disk_pressure`: inject a bounded quota/loopback volume, verify the recorder
  fails before reserve breach and the engine becomes stale/cancel-only.
- `clock_degradation`: introduce a controlled test namespace offset, verify
  beyond-policy gateway lead is fatal and retained evidence is invalidated.
- `recorder_failure`: stop/corrupt the gateway connection, verify stale risk,
  cancel-all, alert, sequence continuity or explicit new run.
- `observability_failure`: stop the observer/Prometheus path, verify the engine
  risk and journal continue, the independent alert fires, and recovery is
  read-only.
- `operator_kill`: activate kill with open counterfactual orders and verify no
  new approved submit plus complete cancellation.

An artifact name alone is not evidence. The CLI hashes file contents; reviewers
must inspect the retained artifact.

## 7. Determinism replay

Snapshot the ingress volume read-only or stop the gateway briefly at a reviewed
boundary. Replay into an empty directory:

```bash
aqt-shadow replay \
  --config-dir configs \
  --environment shadow \
  --code-identity "$AQT_NATIVE_CODE_IDENTITY" \
  --image-identity "$AQT_NATIVE_IMAGE_DIGEST" \
  --ingress-path /snapshot/frames.sqlite3 \
  --source-journal /snapshot/shadow-journal.sqlite3 \
  --source-run-id '<shadow-run-id>' \
  --output-state-root /evidence/shadow-replay
```

Replay reads the immutable ingress start boundary from the source manifest and
the processed end boundary from its durable checkpoint. It refuses mismatched
code, image, configuration, feature, strategy, scenario, or policy lineage.
Frames written before engine admission or after its last committed cycle
therefore cannot contaminate the determinism interval.

Then compare and bind the result:

```bash
aqt-shadow compare \
  --source-journal /snapshot/shadow-journal.sqlite3 \
  --replay-journal /evidence/shadow-replay/shadow-journal.sqlite3 \
  --source-run-id '<shadow-run-id>' \
  --replay-run-id '<shadow-replay-run-id>' \
  --source-audit /snapshot/shadow-audit.sqlite3 \
  --output /evidence/shadow-determinism.json
```

Any decision or command mismatch fails the determinism gate. Do not edit or
exclude rows to make a comparison pass.

## 8. Sensitivity and evidence

Run the identical admitted interval with new immutable calibrated baseline and
pessimistic scenarios. Each sensitivity report must share code, image, config,
feature, strategy, engine-policy, shadow-policy, and observation duration.

```bash
aqt-shadow evidence \
  --config-dir configs \
  --environment shadow \
  --run-id '<shadow-run-id>' \
  --sensitivity-report /evidence/pessimistic-shadow-report.json \
  --output /evidence/baseline-shadow-report.json
```

Exit `1` means the report was generated but one or more gates failed. Exit `2`
means configuration, journal, or evidence input was invalid. A passing report
says `awaiting_human_approval=true`; it does not approve mainnet.

## 9. Backup and retention

Retain together:

- raw Zstandard segments, manifests, and recorder state;
- ingress SQLite database and filesystem snapshot metadata;
- shadow command/state journal and audit journal, including WAL sidecars;
- baseline/pessimistic reports and determinism report;
- Prometheus/Grafana export and alert-delivery evidence;
- drill artifacts and SHA-256 receipts;
- exact image reference/digest, SBOM/provenance, commit, lock, rendered Compose,
  effective configuration fingerprint, and reviewer notes.

Test restore into a separate host/path. Never open the only evidence copy with
a mutating tool.

## 10. Incident and rollback

1. Activate shadow kill.
2. Preserve status, metrics, logs, ingress, journals, raw segment, image, and
   rendered config.
3. Stop `shadow-observer`, then `shadow-engine`, then `shadow-gateway`.
4. Classify clock, disk, feed, integrity, command, simulator, strategy, risk,
   observer, or host cause.
5. Restore the last reviewed immutable image or remain stopped.
6. Start gateway first, then engine, then observer; verify route isolation and
   run identity before clearing kill.

```bash
docker compose -f compose.shadow.yaml stop shadow-observer shadow-engine shadow-gateway
```

Do not prune volumes during an incident. Rollback never mounts a wallet,
enables mainnet execution, promotes a challenger, or changes MT5/XAU behavior.
