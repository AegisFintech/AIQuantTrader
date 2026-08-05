# Market Data Runbook

This runbook operates the Linux-native public BTC data path only. It does not
enable exchange execution.

## Start and verify

```bash

docker compose --profile market-data build
docker compose --profile market-data up -d market-data-recorder market-data-normalizer
docker compose ps
docker compose logs --tail=100 market-data-recorder market-data-normalizer
curl --fail http://127.0.0.1:9109/metrics
```

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

Run on the target Debian host:

```bash
aqt-market-data record \
  --config-dir /etc/aiquanttrader-native \
  --environment paper \
  --duration-seconds 21600
aqt-market-data normalize-pending \
  --data-root /var/lib/aiquanttrader/data \
  --state-root /var/lib/aiquanttrader/state
```

Retain:

- commit and image digest;
- effective configuration fingerprint;
- recorder state and Prometheus counter snapshot;
- all raw and normalized manifests;
- dataset admission result;
- reconnect and gap classifications;
- disk usage at start and finish.

The acceptance gate fails if a gap is unexplained, a corruption is admitted,
the recorder is repeatedly stale, or free disk crosses its threshold.

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
