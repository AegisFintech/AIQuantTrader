# ADR 0004: Immutable Parquet Data Lake with DuckDB Metadata

Status: proposed; accepted when the migration blueprint PR is merged
Date: 2026-08-04

## Context

The MT5 platform exchanges rolling CSV files and stores periodic snapshots in a
single DuckDB database. BTC microstructure research requires exact raw capture,
large columnar scans, reproducible dataset identities, and isolation from the
live decision path.

## Decision

- Archive exact WebSocket payload bytes in hourly compressed, append-only raw
  segments with checksummed manifests.
- Normalize validated events to versioned Parquet schemas partitioned by venue,
  channel, instrument, date, and hour.
- Produce a Nautilus-compatible catalog from normalized events.
- Use separate DuckDB databases for data manifests, research experiments, and
  trade analytics; each writable database has one owning process.
- Keep all storage and database calls outside synchronous order evaluation.

Dataset identities hash ordered partition manifests, schema versions,
normalization code, and quality exclusions. Rebuilding a dataset with different
inputs creates a new identity.

## Alternatives considered

- DuckDB for raw mutable ingestion: simple, but concurrent writer and crash
  recovery concerns make it a poor raw event log.
- PostgreSQL/TimescaleDB: strong concurrent transactional behavior, but more
  operational cost and less efficient immutable research scans.
- Kafka plus object storage: valuable at multi-venue scale, but unnecessary
  operational overhead for one instrument on one host.

## Consequences

- Research scans and compression are efficient and reproducible.
- Raw data remains available when normalization logic changes.
- Recent operational analytics can query DuckDB without making it a production
  dependency.
- Atomic finalization, disk monitoring, retention, and backup verification are
  mandatory operational responsibilities.
