"""Deterministic Parquet materialization for causal feature snapshots."""

from __future__ import annotations

import os
import secrets
from decimal import Decimal
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from aiquanttrader.backtest.kernel import KernelMarketState
from aiquanttrader.domain.base import canonical_sha256
from aiquanttrader.features.engine import replay_features
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


def write_feature_dataset(
    states: tuple[KernelMarketState, ...],
    *,
    config: FeatureEngineConfig,
    source_dataset_sha256: str,
    output_root: Path,
    relative_path: str,
) -> tuple[Path, FeatureDatasetManifest]:
    if not relative_path.endswith(".parquet"):
        raise ValueError("feature dataset must use a .parquet extension")
    snapshots = replay_features(states, config=config)
    if not snapshots:
        raise ValueError("feature replay produced no snapshots")
    rows = [
        {key: _arrow_value(value) for key, value in item.model_dump(mode="python").items()}
        for item in snapshots
    ]
    table = pa.Table.from_pylist(rows)
    final_path = _safe_output(output_root, relative_path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    partial = final_path.with_name(f".{final_path.name}.{secrets.token_hex(8)}.partial")
    try:
        pq.write_table(
            table,
            partial,
            compression="zstd",
            compression_level=9,
            use_dictionary=False,
            write_statistics=True,
            row_group_size=65_536,
            data_page_version="2.0",
        )
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
        partial.unlink(missing_ok=True)
        raise
    identity = {
        "source_dataset_sha256": source_dataset_sha256,
        "feature_schema_sha256": MODEL_FEATURE_SCHEMA.sha256(),
        "feature_config_sha256": config.sha256(),
        "relative_path": relative_path,
        "file_sha256": digest,
        "row_count": len(snapshots),
        "first_receive_ts_ns": snapshots[0].receive_ts_ns,
        "last_receive_ts_ns": snapshots[-1].receive_ts_ns,
    }
    manifest = FeatureDatasetManifest(
        feature_dataset_id=canonical_sha256(identity),
        source_dataset_sha256=source_dataset_sha256,
        feature_schema_sha256=MODEL_FEATURE_SCHEMA.sha256(),
        feature_config_sha256=config.sha256(),
        relative_path=relative_path,
        file_sha256=digest,
        row_count=len(snapshots),
        first_receive_ts_ns=snapshots[0].receive_ts_ns,
        last_receive_ts_ns=snapshots[-1].receive_ts_ns,
    )
    manifest_path = final_path.with_suffix(final_path.suffix + ".manifest.json")
    content = manifest.canonical_bytes() + b"\n"
    if manifest_path.exists():
        if manifest_path.read_bytes() != content:
            raise FileExistsError(f"immutable feature manifest differs: {manifest_path}")
    else:
        atomic_write_bytes(manifest_path, content)
    return manifest_path, manifest
