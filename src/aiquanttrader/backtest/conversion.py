"""Deterministic Tardis/Parquet conversion into admitted HftBacktest events."""

from __future__ import annotations

import os
import secrets
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, cast

import numpy as np
import pyarrow.parquet as pq
from hftbacktest import (
    BUY_EVENT,
    DEPTH_EVENT,
    EXCH_EVENT,
    LOCAL_EVENT,
    SELL_EVENT,
    TRADE_EVENT,
    event_dtype,
)
from hftbacktest.data.utils.tardis import convert as convert_tardis
from hftbacktest.data.validation import correct_event_order, validate_event_order

from aiquanttrader.backtest.models import BacktestDatasetManifest, SourceArtifact
from aiquanttrader.domain.base import canonical_sha256
from aiquanttrader.domain.data import (
    DatasetManifest,
    NormalizedSegmentManifest,
    TardisFileManifest,
)
from aiquanttrader.market_data.io import atomic_write_bytes, fsync_directory, sha256_file
from aiquanttrader.market_data.storage import validate_normalized_files

CONVERTER_VERSION = "hft-events-v1"


def _resolved_child(root: Path, path: Path) -> tuple[Path, str]:
    resolved_root = root.resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(resolved_root).as_posix()
    except ValueError as exc:
        raise ValueError(f"path is outside the declared root: {path}") from exc
    return resolved, relative


def _output_path(output_root: Path, event_path: Path) -> tuple[Path, str]:
    candidate = event_path if event_path.is_absolute() else output_root / event_path
    return _resolved_child(output_root, candidate)


def _validate_events(events: np.ndarray[Any, np.dtype[Any]]) -> None:
    if events.dtype != event_dtype or not events.size:
        raise ValueError("converted dataset must contain pinned HftBacktest events")
    if np.any(events["exch_ts"] < 0) or np.any(events["local_ts"] < 0):
        raise ValueError("event timestamps cannot be negative")
    local = events["ev"] & LOCAL_EVENT == LOCAL_EVENT
    exchange = events["ev"] & EXCH_EVENT == EXCH_EVENT
    if not np.any(local) or not np.any(exchange):
        raise ValueError("events must include both local and exchange processing flags")
    if np.any(events["local_ts"][local] < events["exch_ts"][local]):
        raise ValueError("local receipt precedes exchange time; calibrate clock before admission")
    if np.any(~np.isfinite(events["px"])) or np.any(~np.isfinite(events["qty"])):
        raise ValueError("event prices and quantities must be finite")
    if np.any(events["px"] <= 0) or np.any(events["qty"] < 0):
        raise ValueError("event prices must be positive and quantities non-negative")
    validate_event_order(events)


def write_deterministic_npz(path: Path, events: np.ndarray[Any, np.dtype[Any]]) -> str:
    """Write NumPy data with fixed ZIP metadata and immutable-target semantics."""

    _validate_events(events)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.name}.{secrets.token_hex(8)}.partial")
    info = zipfile.ZipInfo("data.npy", date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100640 << 16
    try:
        with (
            zipfile.ZipFile(
                partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
            ) as archive,
            archive.open(info, "w", force_zip64=True) as stream,
        ):
            np.lib.format.write_array(stream, events, allow_pickle=False)  # type: ignore[no-untyped-call]
        descriptor = os.open(partial, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        digest = sha256_file(partial)
        if path.exists():
            if sha256_file(path) != digest:
                raise FileExistsError(f"immutable HftBacktest dataset differs: {path}")
            partial.unlink()
            return digest
        partial.rename(path)
        fsync_directory(path.parent)
        return digest
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def load_event_file(path: Path) -> np.ndarray[Any, np.dtype[Any]]:
    with np.load(path, allow_pickle=False) as archive:
        if archive.files != ["data"]:
            raise ValueError("HftBacktest archive must contain exactly the data array")
        events = archive["data"]
    _validate_events(events)
    return cast(np.ndarray[Any, np.dtype[Any]], events)


def _manifest_for_events(
    *,
    source_kind: Literal["tardis", "normalized_parquet"],
    sources: tuple[SourceArtifact, ...],
    output_root: Path,
    event_path: Path,
    event_digest: str,
    events: np.ndarray[Any, np.dtype[Any]],
) -> BacktestDatasetManifest:
    _, event_relative = _resolved_child(output_root, event_path)
    exchange = events["exch_ts"][events["ev"] & EXCH_EVENT == EXCH_EVENT]
    local = events["local_ts"][events["ev"] & LOCAL_EVENT == LOCAL_EVENT]
    identity: Mapping[str, Any] = {
        "converter_version": CONVERTER_VERSION,
        "instrument_id": "BTC-USD-PERP.HYPERLIQUID",
        "source_kind": source_kind,
        "sources": [item.model_dump(mode="json") for item in sources],
        "event_file_sha256": event_digest,
        "event_count": len(events),
        "first_exchange_ts_ns": int(exchange.min()),
        "last_exchange_ts_ns": int(exchange.max()),
        "first_local_ts_ns": int(local.min()),
        "last_local_ts_ns": int(local.max()),
    }
    return BacktestDatasetManifest(
        dataset_id=canonical_sha256(identity),
        source_kind=source_kind,
        sources=sources,
        event_file=event_relative,
        event_file_sha256=event_digest,
        event_count=len(events),
        first_exchange_ts_ns=int(exchange.min()),
        last_exchange_ts_ns=int(exchange.max()),
        first_local_ts_ns=int(local.min()),
        last_local_ts_ns=int(local.max()),
    )


def _write_manifest(event_path: Path, manifest: BacktestDatasetManifest) -> Path:
    path = event_path.with_suffix(event_path.suffix + ".manifest.json")
    content = manifest.canonical_bytes() + b"\n"
    if path.exists():
        if path.read_bytes() != content:
            raise FileExistsError(f"immutable backtest manifest differs: {path}")
    else:
        atomic_write_bytes(path, content)
    return path


def convert_tardis_day(
    *, source_root: Path, input_files: Iterable[Path], output_root: Path, event_path: Path
) -> tuple[Path, BacktestDatasetManifest]:
    """Convert one checksummed BTC day using HftBacktest's pinned Tardis parser."""

    entries: list[tuple[Path, str, TardisFileManifest]] = []
    for path in input_files:
        resolved, relative = _resolved_child(source_root, path)
        manifest_path = resolved.with_suffix(resolved.suffix + ".manifest.json")
        manifest = TardisFileManifest.model_validate_json(manifest_path.read_bytes())
        if (
            manifest.relative_path != relative
            or sha256_file(resolved) != manifest.compressed_sha256
        ):
            raise ValueError(f"Tardis artifact does not match its manifest: {resolved}")
        entries.append((resolved, relative, manifest))
    types = [item[2].data_type for item in entries]
    if sorted(types) != ["incremental_book_L2", "trades"]:
        raise ValueError("Tardis conversion requires exactly trades and incremental_book_L2")
    dates = {item[2].date for item in entries}
    if len(dates) != 1:
        raise ValueError("Tardis inputs must belong to one UTC day")
    entries.sort(key=lambda item: 0 if item[2].data_type == "trades" else 1)
    total_rows = sum(item[2].row_count for item in entries)
    events = convert_tardis(
        [str(item[0]) for item in entries],
        buffer_size=max(1_000, total_rows * 2 + 100),
        ss_buffer_size=max(1_000, total_rows + 10),
        base_latency=0,
        snapshot_mode="process",
    )
    events = cast(np.ndarray[Any, np.dtype[Any]], events)
    _validate_events(events)
    resolved_output, _ = _output_path(output_root, event_path)
    digest = write_deterministic_npz(resolved_output, events)
    sources = tuple(
        SourceArtifact(
            relative_path=item[1],
            artifact_sha256=item[2].compressed_sha256,
            row_count=item[2].row_count,
        )
        for item in entries
    )
    output_manifest = _manifest_for_events(
        source_kind="tardis",
        sources=sources,
        output_root=output_root,
        event_path=resolved_output,
        event_digest=digest,
        events=events,
    )
    return _write_manifest(resolved_output, output_manifest), output_manifest


def _raw_normalized_events(
    manifests: tuple[NormalizedSegmentManifest, ...], data_root: Path
) -> tuple[np.ndarray[Any, np.dtype[Any]], tuple[SourceArtifact, ...]]:
    rows: list[tuple[int, int, int, int, str, dict[str, Any]]] = []
    sources: list[SourceArtifact] = []
    for segment_index, manifest in enumerate(manifests):
        validate_normalized_files(manifest, data_root)
        for file in manifest.files:
            if file.event_type not in {"l2_book", "trade"}:
                continue
            path = data_root / file.relative_path
            sources.append(
                SourceArtifact(
                    relative_path=file.relative_path,
                    artifact_sha256=file.file_sha256,
                    row_count=file.row_count,
                )
            )
            for row in pq.read_table(path).to_pylist():
                priority = 0 if file.event_type == "trade" else 1
                rows.append(
                    (
                        int(row["event_ts_ns"]),
                        int(row["receive_ts_ns"]),
                        priority,
                        segment_index * 10**12 + int(row["event_order"]),
                        file.event_type,
                        row,
                    )
                )
    rows.sort(key=lambda item: item[:4])
    previous_bids: dict[float, float] = {}
    previous_asks: dict[float, float] = {}
    output: list[tuple[int, int, int, float, float, int, int, float]] = []
    for exchange_ts, local_ts, _, _, event_type, row in rows:
        if local_ts < exchange_ts:
            raise ValueError(
                "local receipt precedes exchange time; calibrate clock before conversion"
            )
        if event_type == "trade":
            side = {
                "buyer": BUY_EVENT,
                "seller": SELL_EVENT,
            }.get(str(row["aggressor"]), 0)
            output.append(
                (
                    TRADE_EVENT | side,
                    exchange_ts,
                    local_ts,
                    float(row["price"]),
                    float(row["size"]),
                    0,
                    0,
                    0.0,
                )
            )
            continue
        current_bids = {float(level["price"]): float(level["size"]) for level in row["bids"]}
        current_asks = {float(level["price"]): float(level["size"]) for level in row["asks"]}
        for side, current, previous in (
            (BUY_EVENT, current_bids, previous_bids),
            (SELL_EVENT, current_asks, previous_asks),
        ):
            for price, quantity in sorted(current.items()):
                if previous.get(price) != quantity:
                    output.append(
                        (DEPTH_EVENT | side, exchange_ts, local_ts, price, quantity, 0, 0, 0.0)
                    )
            for price in sorted(previous.keys() - current.keys()):
                output.append((DEPTH_EVENT | side, exchange_ts, local_ts, price, 0.0, 0, 0, 0.0))
        previous_bids = current_bids
        previous_asks = current_asks
    if not output:
        raise ValueError("normalized dataset has no L2 book or trade events")
    raw = np.asarray(output, dtype=event_dtype)
    corrected = correct_event_order(
        raw,
        np.argsort(raw["exch_ts"], kind="mergesort"),
        np.argsort(raw["local_ts"], kind="mergesort"),
    )
    return cast(np.ndarray[Any, np.dtype[Any]], corrected), tuple(
        sorted(sources, key=lambda x: x.relative_path)
    )


def convert_normalized_dataset(
    *,
    data_root: Path,
    dataset_manifest_path: Path,
    normalized_manifest_paths: Iterable[Path],
    output_root: Path,
    event_path: Path,
) -> tuple[Path, BacktestDatasetManifest]:
    """Convert only files admitted by a Phase 3 dataset-quality manifest."""

    dataset = DatasetManifest.model_validate_json(dataset_manifest_path.read_bytes())
    manifests: list[NormalizedSegmentManifest] = []
    for path in normalized_manifest_paths:
        manifest = NormalizedSegmentManifest.model_validate_json(path.read_bytes())
        if manifest.sha256() not in dataset.normalized_manifest_sha256s:
            raise ValueError(f"normalized manifest is not admitted by dataset: {path}")
        manifests.append(manifest)
    if {manifest.sha256() for manifest in manifests} != set(dataset.normalized_manifest_sha256s):
        raise ValueError("all and only admitted normalized manifests must be supplied")
    ordered = tuple(sorted(manifests, key=lambda item: item.source_segment_id))
    events, source_files = _raw_normalized_events(ordered, data_root.resolve())
    dataset_source = SourceArtifact(
        relative_path=f"admission:{dataset.dataset_id}",
        artifact_sha256=dataset.sha256(),
        row_count=sum(item.event_count for item in ordered),
    )
    sources = (dataset_source, *source_files)
    resolved_output, _ = _output_path(output_root, event_path)
    digest = write_deterministic_npz(resolved_output, events)
    output_manifest = _manifest_for_events(
        source_kind="normalized_parquet",
        sources=sources,
        output_root=output_root,
        event_path=resolved_output,
        event_digest=digest,
        events=events,
    )
    return _write_manifest(resolved_output, output_manifest), output_manifest
