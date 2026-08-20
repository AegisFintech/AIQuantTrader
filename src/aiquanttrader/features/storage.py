"""Deterministic Parquet materialization for causal feature snapshots."""

from __future__ import annotations

import os
import secrets
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from aiquanttrader.backtest.kernel import KernelMarketState
from aiquanttrader.domain.base import canonical_sha256
from aiquanttrader.features.engine import IncrementalFeatureEngine
from aiquanttrader.features.models import (
    MODEL_FEATURE_SCHEMA,
    FeatureDatasetManifest,
    FeatureEngineConfig,
)
from aiquanttrader.market_data.io import atomic_write_bytes, fsync_directory, sha256_file


def _safe_output(root: Path, relative_path: str) -> Path:
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("feature output path must be safe and relative")
    resolved_root = root.resolve()
    resolved = (resolved_root / Path(*relative.parts)).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("feature output path escapes output root") from exc
    return resolved


def _arrow_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    return value


FEATURE_ROW_GROUP_SIZE = 65_536


@dataclass(slots=True)
class _FeatureReplayStats:
    stale_trade_exclusion_count: int = 0
    stale_book_exclusion_count: int = 0


def _parquet_writer(path: Path, schema: pa.Schema) -> pq.ParquetWriter:
    return pq.ParquetWriter(
        path,
        schema,
        compression="zstd",
        compression_level=9,
        use_dictionary=False,
        write_statistics=True,
        data_page_version="2.0",
    )


def _feature_rows(
    states: Iterable[KernelMarketState],
    config: FeatureEngineConfig,
    stats: _FeatureReplayStats,
) -> Iterable[dict[str, Any]]:
    engine = IncrementalFeatureEngine(config)
    for state in states:
        cutoff_ns = state.observed_ts_ns - config.maximum_input_age_ns
        fresh_trades = tuple(trade for trade in state.trades if trade.exchange_ts_ns >= cutoff_ns)
        stats.stale_trade_exclusion_count += len(state.trades) - len(fresh_trades)
        if state.book_exchange_ts_ns < cutoff_ns:
            stats.stale_book_exclusion_count += 1
            continue
        if len(fresh_trades) != len(state.trades):
            state = state.model_copy(update={"trades": fresh_trades})
        snapshot = engine.update(state)
        yield {
            key: _arrow_value(value) for key, value in snapshot.model_dump(mode="python").items()
        }


def write_feature_dataset(
    states: Iterable[KernelMarketState],
    *,
    config: FeatureEngineConfig,
    source_dataset_sha256: str,
    output_root: Path,
    relative_path: str,
) -> tuple[Path, FeatureDatasetManifest]:
    if not relative_path.endswith(".parquet"):
        raise ValueError("feature dataset must use a .parquet extension")
    final_path = _safe_output(output_root, relative_path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    partial = final_path.with_name(f".{final_path.name}.{secrets.token_hex(8)}.partial")
    row_count = 0
    first_receive_ts_ns: int | None = None
    last_receive_ts_ns: int | None = None
    writer: pq.ParquetWriter | None = None
    stats = _FeatureReplayStats()
    try:
        rows: list[dict[str, Any]] = []
        for row in _feature_rows(states, config, stats):
            receive_ts_ns = int(row["receive_ts_ns"])
            if first_receive_ts_ns is None:
                first_receive_ts_ns = receive_ts_ns
            last_receive_ts_ns = receive_ts_ns
            rows.append(row)
            if len(rows) < FEATURE_ROW_GROUP_SIZE:
                continue
            table = pa.Table.from_pylist(rows)
            if writer is None:
                writer = _parquet_writer(partial, table.schema)
            writer.write_table(table, row_group_size=FEATURE_ROW_GROUP_SIZE)
            row_count += len(rows)
            rows.clear()
        if rows:
            table = pa.Table.from_pylist(rows)
            if writer is None:
                writer = _parquet_writer(partial, table.schema)
            writer.write_table(table, row_group_size=FEATURE_ROW_GROUP_SIZE)
            row_count += len(rows)
        if writer is None:
            raise ValueError("feature replay produced no snapshots")
        writer.close()
        writer = None
        descriptor = os.open(partial, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        digest = sha256_file(partial)
        if final_path.exists():
            if sha256_file(final_path) != digest:
                raise FileExistsError(f"immutable feature dataset differs: {final_path}")
            partial.unlink()
        else:
            partial.rename(final_path)
            fsync_directory(final_path.parent)
    except BaseException:
        if writer is not None:
            with suppress(Exception):
                writer.close()
        partial.unlink(missing_ok=True)
        raise
    assert first_receive_ts_ns is not None
    assert last_receive_ts_ns is not None
    identity = {
        "source_dataset_sha256": source_dataset_sha256,
        "feature_schema_sha256": MODEL_FEATURE_SCHEMA.sha256(),
        "feature_config_sha256": config.sha256(),
        "relative_path": relative_path,
        "file_sha256": digest,
        "row_count": row_count,
        "stale_trade_exclusion_count": stats.stale_trade_exclusion_count,
        "stale_book_exclusion_count": stats.stale_book_exclusion_count,
        "first_receive_ts_ns": first_receive_ts_ns,
        "last_receive_ts_ns": last_receive_ts_ns,
    }
    manifest = FeatureDatasetManifest(
        feature_dataset_id=canonical_sha256(identity),
        source_dataset_sha256=source_dataset_sha256,
        feature_schema_sha256=MODEL_FEATURE_SCHEMA.sha256(),
        feature_config_sha256=config.sha256(),
        relative_path=relative_path,
        file_sha256=digest,
        row_count=row_count,
        stale_trade_exclusion_count=stats.stale_trade_exclusion_count,
        stale_book_exclusion_count=stats.stale_book_exclusion_count,
        first_receive_ts_ns=first_receive_ts_ns,
        last_receive_ts_ns=last_receive_ts_ns,
    )
    manifest_path = final_path.with_suffix(final_path.suffix + ".manifest.json")
    content = manifest.canonical_bytes() + b"\n"
    if manifest_path.exists():
        if manifest_path.read_bytes() != content:
            raise FileExistsError(f"immutable feature manifest differs: {manifest_path}")
    else:
        atomic_write_bytes(manifest_path, content)
    return manifest_path, manifest
