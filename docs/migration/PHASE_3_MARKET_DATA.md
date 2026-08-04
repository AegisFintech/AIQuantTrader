# Phase 3: Linux-native BTC Market Data

Status: implementation complete; sustained-soak acceptance evidence pending.

Phase 3 adds an independent, public-only Hyperliquid recorder and a separate
normalization worker. It does not add an order path, mount a wallet, change the
MT5 deployment, or authorize BTC trading.

## Architecture

```text
Hyperliquid WebSocket
  -> raw recorder
     -> exact logical frames in framed Zstandard segments
     -> atomic state + Prometheus metrics
     -> raw-manifest DuckDB catalog after segment finalization

raw segments
  -> independent normalizer
     -> cryptographic and structural verification
     -> typed, deterministic Parquet + immutable manifests
     -> corrupt/incomplete quarantine
     -> normalized-manifest DuckDB catalog

Tardis HTTPS
  -> immutable gzip CSV + validation manifest
  -> Tardis DuckDB catalog

raw + normalized manifests + quality policy
  -> admitted research dataset or explicit rejection
```

The source diagram is
[`phase-3-market-data.mmd`](../architecture/diagrams/phase-3-market-data.mmd).

### Hot-path ordering

For each inbound message the recorder performs this ordering:

1. record receive and monotonic timestamps;
2. hash the exact bytes returned by the WebSocket implementation;
3. append metadata and payload to the open Zstandard segment;
4. flush a compression block;
5. parse, validate, and update bounded metrics.

A schema error therefore cannot erase its source frame. Text messages are
requested from `websockets` with `decode=False`, preserving their UTF-8 bytes
rather than decode/re-encode output.

## Repository delta

```text
native/
|- observability/
|  |- prometheus.yml
|  `- grafana/{dashboards,provisioning}/
|- src/aiquanttrader_native/
|  |- domain/data.py
|  `- market_data/
|     |- catalog.py
|     |- cli.py
|     |- integrity.py
|     |- io.py
|     |- metrics.py
|     |- normalizer.py
|     |- protocol.py
|     |- raw.py
|     |- recorder.py
|     |- storage.py
|     `- tardis.py
`- tests/{unit,integration}/test_market_data_*.py

docs/
|- architecture/diagrams/phase-3-market-data.mmd
|- migration/PHASE_3_MARKET_DATA.md
`- operations/MARKET_DATA_RUNBOOK.md
```

## Data contracts and storage

Raw segments use a versioned binary envelope inside Zstandard. Every record
contains canonical metadata, payload length, exact payload bytes, and a payload
SHA-256. A footer contains record count and a digest over all uncompressed
record blocks. The immutable sidecar manifest also records compressed SHA-256,
byte counts, time bounds, connection ID, and finalization reason.

Normalized Parquet uses explicit Arrow schemas; schema inference is forbidden.
Rows retain exchange time, receive time, connection identity, source record
identity, and deterministic event order. File names derive from the raw digest
and event type. Dictionary encoding is disabled and writer parameters are fixed
so identical input produces identical bytes with the pinned PyArrow version.

DuckDB is metadata-only and never participates in frame capture. Separate
single-writer catalogs are used for raw, normalized, and Tardis manifests so
independent services cannot contend for a write connection. Parquet remains the
research fact store.

## Integrity model

Hyperliquid's public WebSocket does not expose a universal sequence number for
all subscribed channels. The system therefore does not claim sequence-perfect
capture. It records and gates on what can be established:

- frame cadence and feed silence;
- payload and segment cryptographic integrity;
- event timestamp regression;
- duplicate trade identity within a bounded window;
- crossed or malformed books;
- schema failures;
- disconnect/restart/stale/disk finalization reasons;
- explicit gaps between segment time ranges.

Dataset admission rejects unexplained gaps and any classified gap or issue
count above its frozen `DataQualityPolicy`. Market-wide liquidations are
reported as unavailable. Private user liquidation messages and liquidation
metadata on account fills are modeled without inventing market-wide side,
price, or size observations.

## Design decisions

### Independent recorder instead of the Nautilus process

Chosen because raw evidence must survive a trading-node crash, adapter schema
change, or strategy defect. The alternative was adapter-only capture. It has
fewer connections but couples audit evidence to the execution process and may
lose unknown fields. One additional public connection and modest duplicate
bandwidth are accepted.

### Raw-first local files instead of Kafka

Chosen because a single-host BTC deployment can durably append local bytes
without operating a distributed broker. Kafka/Redpanda would improve multi-host
fan-out but add network hops, quorum operations, and another failure domain.
The framed format is sequential, checksummed, hourly partitioned, and can be
copied to object storage later.

### Separate normalization process

Chosen so Arrow/Parquet CPU, allocation, corruption recovery, or dependency
failure cannot delay WebSocket reads. In-process normalization was rejected
because it creates capture gaps during segment finalization. The cost is a
small delay before research-ready Parquet appears and separate metadata
catalogs.

### Full `l2Book` snapshots plus BBO and trades

Chosen because the documented Hyperliquid public feed supplies full
market-by-price snapshots, not market-by-order queue events. This supports
imbalance, microprice, VAMP, depth, spread, and flow features. Exact queue
position is not observable and must be modeled later with calibrated bounds;
it must never be presented as measured truth.

### Python before custom Rust

Chosen because WebSocket I/O, compression, Arrow, and DuckDB already execute in
native libraries while orchestration remains typed Python. Rust is deferred
until a reproducible benchmark shows the normalizer or feature path misses a
latency or throughput budget. This avoids an unearned FFI boundary.

## Performance implications

- The recorder performs one SHA-256 and one framed append per inbound message.
- `FLUSH_BLOCK` makes received frames recoverable before parsing; `fdatasync`
  occurs at a configurable record cadence to balance durability and IOPS.
- Normalization scans a raw segment twice: once for full verification and once
  for conversion. This intentionally spends offline I/O to prevent partial
  evidence from reaching research.
- Duplicate tracking is bounded to one million trade identities.
- Prometheus labels are fixed enums; connection IDs, instruments, and payload
  hashes are never labels.
- Parquet uses Zstandard level 9 and 65,536-row groups for compact offline scans,
  outside the capture process.

## Forward migration

1. Build the pinned native image and keep the default execution flag false.
2. Create or verify the dedicated data and state volumes.
3. Start the `market-data` Compose profile.
4. Verify recorder health, frame counters, raw segment growth, and free disk.
5. Let the normalizer finalize the first segment and verify both manifests.
6. Run a six-hour minimum public-feed soak and save the command output,
   manifests, gap report, image digest, and commit SHA as acceptance evidence.
7. Admit a dataset only after the soak's gaps pass the frozen policy.

No MT5 service is stopped in this phase.

## Rollback

1. Stop only `market-data-recorder` and `market-data-normalizer`.
2. Preserve the named data/state volumes; do not delete or rewrite segments.
3. Re-deploy the prior pinned native image or leave the profile stopped.
4. Verify the legacy MT5 PM2 processes are unchanged.
5. Quarantine artifacts from an interrupted write with `recover`; finalized
   immutable data remains readable by the prior schema version.

Rollback never requires enabling execution or restoring MT5 files.

## Acceptance evidence

Implemented and automated:

- archive-before-parse ordering and exact-payload round trip;
- deterministic two-root normalization;
- truncated/corrupt/partial quarantine;
- reconnect, silence threshold, duplicate, schema, timestamp regression,
  crossed-book, and disk-pressure accounting;
- policy rejection for unexplained and excessive gaps;
- immutable Tardis download validation;
- explicit false market-wide liquidation capability;
- strict typing, unit/integration tests, locked dependencies, non-root images.

Still required before Phase 3 is declared accepted:

- a sustained public mainnet feed soak on the deployment host, with every
  observed discontinuity classified and the evidence retained. A short CI or
  developer smoke test does not satisfy this gate.

### Bounded live smoke evidence

On 2026-08-04, the corrected recorder completed a 15-second read-only mainnet
smoke with execution disabled: 107 raw frames, clean `shutdown` finalization,
184 normalized events, zero exclusions, zero quality issues, and no quarantine.
The event mix was 60 BBO, 60 trades, four L2 books, and 15 each of funding,
index price, mark price, and open interest. This verifies the public handshake
and current message shapes only; it is not the sustained acceptance soak.
