# Market Data Runbook

This runbook operates the Linux-native public BTC data path only. It does not
enable exchange execution.

## Start and verify

```bash

docker compose --profile market-data build
docker image inspect aiquanttrader-native-foundation:0.1.0
docker builder prune --all --force
df -h /
docker compose --profile market-data up -d market-data-recorder market-data-normalizer
docker compose ps
docker compose logs --tail=100 market-data-recorder market-data-normalizer
curl --fail http://127.0.0.1:9109/metrics
```

Inspect the image before pruning so a failed build cannot destroy the last
useful cache without producing a deployable artifact. Pruning removes builder
cache only; it must not remove the runtime image or the named data/state
volumes. Start the recorder only when free space exceeds both configured disk
floors. Never lower a disk floor to force startup.

Both services must become healthy. The recorder healthcheck requires a fresh
connected public feed. The normalizer healthcheck requires a fresh typed
`running` heartbeat at
`/var/lib/aiquanttrader/state/market-data/normalizer-state.json`; it does not
reuse the recorder or image-default HTTP healthcheck.

The paper overlay enables public market data and keeps execution disabled.
Readiness requires a connected recorder heartbeat no older than 30 seconds.

Local development:

```bash

uv sync --frozen --group dev
uv run aqt-market-data record \
  --config-dir configs --environment paper --duration-seconds 60
uv run aqt-market-data normalize-pending \
  --data-root /var/lib/aiquanttrader/data \
  --state-root /var/lib/aiquanttrader/state
```

## Verify and recover artifacts

```bash
aqt-market-data verify /path/to/segment.raw.zst
aqt-market-data normalize /path/to/segment.raw.zst --data-root /var/lib/aiquanttrader/data
aqt-market-data recover --data-root /var/lib/aiquanttrader/data
```

`recover` moves incomplete raw artifacts into `quarantine/raw-incomplete`. It
does not remove or repair them. `normalize` moves a corrupt finalized segment
and its manifest into `quarantine/raw-corrupt` and returns non-zero.
Recovery is an explicit offline operator action and must not run concurrently
with the recorder. The continuous normalizer reads finalized manifest/segment
pairs only and never scans, moves, or opens the recorder's active `.partial`
file.

## Tardis historical data

The API key is optional for free sample days. For authenticated downloads,
mount a one-line secret below `/run/secrets` and set only the secret-file path
in configuration. Never pass the key as an argument or environment variable.

```bash
aqt-market-data download-tardis \
  --config-dir /etc/aiquanttrader-native \
  --environment paper \
  --data-type trades \
  --date 2024-10-29
```

Supported datasets are `trades`, `quotes`, `incremental_book_L2`,
`book_snapshot_25`, and `derivative_ticker`. Each gzip is read to EOF so CRC and
length failures are detected before the immutable rename.

## Dataset admission

```bash
aqt-market-data build-dataset \
  --data-root /var/lib/aiquanttrader/data \
  --raw-manifest /path/one.manifest.json \
  --raw-manifest /path/two.manifest.json \
  --max-classified-gap-seconds 30 \
  --output /var/lib/aiquanttrader/data/datasets/example.manifest.json
```

The command rejects missing or digest-mismatched Parquet, unexplained gaps,
classified gaps over policy, and data-quality issues over policy.

## Six-hour acceptance soak

Use the frozen policy at `configs/market-data/soak-v1.toml`. Record the exact
runtime identity and disk baseline before starting on the target Debian host:

```bash
SOAK_STARTED_TS_NS="$(date +%s%N)"
START_FREE_BYTES="$(df -B1 --output=avail / | tail -n 1)"
RUNTIME_COMMIT="$(git rev-parse HEAD)"
IMAGE_DIGEST="$(docker image inspect --format '{{.Id}}' \
  aiquanttrader-native-foundation:0.1.0)"
docker compose --profile market-data up -d --no-build --force-recreate \
  market-data-recorder market-data-normalizer
docker compose --profile market-data ps
docker run --rm aiquanttrader-native-foundation:0.1.0 show-config \
  --config-dir /etc/aiquanttrader-native --environment paper
```

Retain the `config_fingerprint` printed by `show-config` as
`RUNTIME_CONFIG_FINGERPRINT`. The runtime commit must come from the reviewed
image build receipt; `git rev-parse` is valid only when the image was built
from the current clean commit. A local Docker image ID is accepted as the
content digest.

After at least six continuous hours, capture the metrics before its timestamp,
read Docker's restart counters, and run the offline evaluator. The evaluator
does not connect to Hyperliquid, load a wallet, or submit orders.

```bash
mkdir -p state/market-data-evidence
curl --fail --silent http://127.0.0.1:9109/metrics \
  > state/market-data-evidence/recorder.prom
METRICS_CAPTURED_TS_NS="$(date +%s%N)"
RECORDER_RESTARTS="$(docker inspect --format '{{.RestartCount}}' \
  "$(docker compose ps -q market-data-recorder)")"
NORMALIZER_RESTARTS="$(docker inspect --format '{{.RestartCount}}' \
  "$(docker compose ps -q market-data-normalizer)")"
COLLECTOR_COMMIT="$(git rev-parse HEAD)"

docker compose --profile market-data run --rm --no-deps \
  -v "$PWD/state/market-data-evidence/recorder.prom:/evidence/recorder.prom:ro" \
  market-data-normalizer evaluate-soak \
  --config-dir /etc/aiquanttrader-native \
  --environment paper \
  --policy /etc/aiquanttrader-native/market-data/soak-v1.toml \
  --data-root /var/lib/aiquanttrader/data \
  --state-root /var/lib/aiquanttrader/state \
  --metrics-snapshot /evidence/recorder.prom \
  --metrics-captured-ts-ns "$METRICS_CAPTURED_TS_NS" \
  --requested-started-ts-ns "$SOAK_STARTED_TS_NS" \
  --runtime-code-identity "$RUNTIME_COMMIT" \
  --collector-code-identity "$COLLECTOR_COMMIT" \
  --image-digest "$IMAGE_DIGEST" \
  --runtime-config-fingerprint "$RUNTIME_CONFIG_FINGERPRINT" \
  --start-free-bytes "$START_FREE_BYTES" \
  --recorder-restart-count "$RECORDER_RESTARTS" \
  --normalizer-restart-count "$NORMALIZER_RESTARTS" \
  --output /var/lib/aiquanttrader/data/evidence/market-data-soak.json

docker compose --profile market-data ps
docker compose --profile market-data logs --since=6h \
  market-data-recorder market-data-normalizer
```

Exit status `0` means accepted, `1` means a valid rejection report was written,
and `2` means the input was malformed, missing, corrupt, or ambiguous and no
report was written. Never treat a missing report as a pass. Copy the report
from the named volume into the retained operator evidence directory:

```bash
docker compose --profile market-data run --rm --no-deps \
  --entrypoint /bin/cat market-data-normalizer \
  /var/lib/aiquanttrader/data/evidence/market-data-soak.json \
  > state/market-data-evidence/market-data-soak.json
```

The content-addressed report retains:

- runtime and collector commits, image digest, configuration and policy hashes;
- recorder and normalizer typed states plus the parsed Prometheus snapshot;
- raw-manifest hashes, normalized-manifest hashes, admitted dataset, and gaps;
- restart, reconnect, finalization, exclusion, quarantine, and quality counts;
- disk usage at start and finish and every individual gate verdict.

Discovery is automatic and includes only finalized segments that start inside
the requested soak window. Earlier failed deployment artifacts cannot be
silently selected. Missing normalized output, digest mismatches, or corrupt
inputs abort evaluation. Reconnects, restarts, stale state, non-rotation
finalization, exclusions, critical quality issues, unexplained/excessive gaps,
in-window quarantine artifacts, or either disk-floor breach reject the run.
`cadence_anomaly` remains visible but is not itself corruption: channels with
naturally sparse updates can cross the common 15-second diagnostic threshold.

## Incident responses

### Stale feed or reconnect loop

Check `aqt_market_data_reconnects_total`, DNS/TLS reachability, system clock, and
the Hyperliquid status channel. Do not delete segments. Each failed connection
must finalize as `disconnect`, `stale_feed`, or `error`; unexplained `error`
gaps are rejected from research.

### Disk pressure

The recorder finalizes with `disk_pressure` and exits. Add capacity or copy
finalized immutable partitions to managed storage, verify their hashes, then
restart. Never delete an open `.partial` file; stop the process and run
`recover`.

### Corrupt segment

Keep the quarantine evidence, inspect host storage health, and compare source
and destination hashes if data was copied. Tardis may fill historical research
coverage, but it does not retroactively prove local capture continuity.

### Normalizer failure

The recorder is independent and should continue. Stop/restart only the
normalizer, correct the dependency or storage issue, and rerun
`normalize-pending`. Immutable output makes the operation idempotent.

## Stop and rollback

```bash
docker compose stop market-data-normalizer market-data-recorder
```

Keep both named volumes so captured data and recorder state remain recoverable.
