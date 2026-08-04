"""Operational CLI for capture, verification, normalization, and Tardis acquisition."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import signal
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Literal

from prometheus_client import start_http_server

from aiquanttrader_native.config import ConfigLoadError, load_config
from aiquanttrader_native.domain.data import DataQualityPolicy, RecorderState
from aiquanttrader_native.market_data.catalog import CatalogLockedError, ManifestCatalog
from aiquanttrader_native.market_data.io import atomic_write_bytes
from aiquanttrader_native.market_data.metrics import RecorderMetrics
from aiquanttrader_native.market_data.normalizer import NormalizationWorker
from aiquanttrader_native.market_data.raw import (
    RawSegmentError,
    RawSegmentReader,
    load_segment_manifest,
    quarantine_incomplete_segments,
)
from aiquanttrader_native.market_data.recorder import MarketDataRecorder
from aiquanttrader_native.market_data.storage import (
    DatasetQualityError,
    build_dataset_manifest,
    load_normalized_manifest,
    normalize_segment,
    validate_normalized_files,
)
from aiquanttrader_native.market_data.tardis import download_file


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aqt-market-data")
    commands = parser.add_subparsers(dest="command", required=True)

    record = commands.add_parser("record", help="run the public Hyperliquid recorder")
    record.add_argument("--config-dir", type=Path, required=True)
    record.add_argument("--environment", required=True)
    record.add_argument("--duration-seconds", type=float)

    verify = commands.add_parser("verify", help="verify an immutable raw segment")
    verify.add_argument("segment", type=Path)

    normalize = commands.add_parser("normalize", help="normalize one raw segment")
    normalize.add_argument("segment", type=Path)
    normalize.add_argument("--data-root", type=Path, required=True)

    recover = commands.add_parser("recover", help="quarantine incomplete raw artifacts")
    recover.add_argument("--data-root", type=Path, required=True)

    pending = commands.add_parser("normalize-pending", help="normalize completed raw segments")
    pending.add_argument("--data-root", type=Path, required=True)
    pending.add_argument("--state-root", type=Path, required=True)
    pending.add_argument("--poll-seconds", type=float)

    tardis = commands.add_parser("download-tardis", help="download one historical file")
    tardis.add_argument("--config-dir", type=Path, required=True)
    tardis.add_argument("--environment", required=True)
    tardis.add_argument(
        "--data-type",
        required=True,
        choices=(
            "incremental_book_L2",
            "book_snapshot_25",
            "quotes",
            "trades",
            "derivative_ticker",
        ),
    )
    tardis.add_argument("--date", required=True, type=date.fromisoformat)

    dataset = commands.add_parser("build-dataset", help="admit normalized segments")
    dataset.add_argument("--data-root", type=Path, required=True)
    dataset.add_argument("--raw-manifest", type=Path, action="append", required=True)
    dataset.add_argument("--output", type=Path, required=True)
    dataset.add_argument("--max-classified-gap-seconds", type=float, default=30)

    health = commands.add_parser("healthcheck", help="validate recorder heartbeat")
    health.add_argument("--state-root", type=Path, required=True)
    health.add_argument("--stale-after-seconds", type=float, default=30)
    return parser


async def _record(args: argparse.Namespace) -> int:
    bundle = load_config(args.config_dir, args.environment)
    settings = bundle.settings
    metrics = RecorderMetrics.create()
    start_http_server(
        settings.market_data.metrics_port,
        addr=settings.market_data.metrics_host,
        registry=metrics.registry,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    duration_task: asyncio.Task[None] | None = None
    if args.duration_seconds is not None:
        if args.duration_seconds <= 0:
            raise ValueError("record duration must be positive")

        async def stop_later() -> None:
            await asyncio.sleep(args.duration_seconds)
            stop.set()

        duration_task = asyncio.create_task(stop_later())
    catalog_path = settings.storage.state_root / "market-data" / "raw-catalog.duckdb"
    network: Literal["testnet", "mainnet"] = (
        "mainnet" if settings.exchange.network.value == "mainnet" else "testnet"
    )
    try:
        with ManifestCatalog(catalog_path) as catalog:
            recorder = MarketDataRecorder(
                websocket_url=str(settings.exchange.websocket_url),
                network=network,
                environment=settings.environment,
                config=settings.market_data,
                data_root=settings.storage.data_root,
                state_root=settings.storage.state_root,
                catalog=catalog,
                metrics=metrics,
            )
            await recorder.run(stop)
    finally:
        if duration_task is not None:
            duration_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await duration_task
    return 0


def _build_dataset(args: argparse.Namespace) -> int:
    if args.max_classified_gap_seconds < 0:
        raise ValueError("maximum classified gap cannot be negative")
    root = args.data_root.resolve()
    pairs = []
    for raw_path in args.raw_manifest:
        raw = load_segment_manifest(raw_path)
        normalized_path = (
            root / "normalized" / "manifests" / f"{raw.segment_id}.normalized.manifest.json"
        )
        normalized = load_normalized_manifest(normalized_path)
        if normalized.source_segment_sha256 != raw.compressed_sha256:
            raise DatasetQualityError("raw and normalized source digests differ")
        validate_normalized_files(normalized, root)
        pairs.append((raw, normalized))
    policy = DataQualityPolicy(
        max_classified_gap_ns=int(args.max_classified_gap_seconds * 1_000_000_000)
    )
    manifest = build_dataset_manifest(pairs, policy)
    atomic_write_bytes(args.output.resolve(), manifest.canonical_bytes() + b"\n")
    print(json.dumps({"status": "valid", "dataset_id": manifest.dataset_id}, sort_keys=True))
    return 0


def _healthcheck(state_root: Path, stale_after_seconds: float) -> int:
    if stale_after_seconds <= 0:
        raise ValueError("stale threshold must be positive")
    path = state_root.resolve() / "market-data" / "recorder-state.json"
    try:
        state = RecorderState.model_validate_json(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise ValueError(f"invalid recorder state: {exc}") from exc
    age_seconds = (time.time_ns() - state.heartbeat_ts_ns) / 1e9
    ready = state.status == "connected" and age_seconds <= stale_after_seconds
    print(
        json.dumps(
            {"status": "ready" if ready else "not_ready", "age_seconds": age_seconds},
            sort_keys=True,
        )
    )
    return 0 if ready else 1


def _normalize_pending(args: argparse.Namespace) -> int:
    if args.poll_seconds is not None and not 1 <= args.poll_seconds <= 3_600:
        raise ValueError("normalizer poll interval must be in [1, 3600] seconds")
    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    catalog_path = args.state_root.resolve() / "market-data" / "normalized-catalog.duckdb"
    with ManifestCatalog(catalog_path) as catalog:
        worker = NormalizationWorker(args.data_root, catalog)
        while not stop.is_set():
            batch = worker.run_once()
            print(json.dumps(asdict(batch), sort_keys=True), flush=True)
            if args.poll_seconds is None:
                break
            stop.wait(args.poll_seconds)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "record":
            return asyncio.run(_record(args))
        if args.command == "verify":
            reader = RawSegmentReader(args.segment)
            reader.verify()
            print(json.dumps({"status": "valid", "segment_id": reader.manifest.segment_id}))
            return 0
        if args.command == "normalize":
            root = args.data_root.resolve()
            result = normalize_segment(
                args.segment,
                output_root=root,
                quarantine_root=root / "quarantine" / "raw-corrupt",
            )
            print(json.dumps({"status": "valid", "manifest": str(result.manifest_path)}))
            return 0
        if args.command == "recover":
            moved = quarantine_incomplete_segments(args.data_root)
            print(json.dumps({"status": "valid", "quarantined": [str(p) for p in moved]}))
            return 0
        if args.command == "normalize-pending":
            return _normalize_pending(args)
        if args.command == "download-tardis":
            settings = load_config(args.config_dir, args.environment).settings
            target, _, manifest = download_file(
                root=settings.storage.data_root,
                data_type=args.data_type,
                day=args.date,
                api_key_secret_path=settings.market_data.tardis_api_key_secret_path,
            )
            with ManifestCatalog(
                settings.storage.state_root / "market-data" / "tardis-catalog.duckdb"
            ) as catalog:
                catalog.register_tardis(manifest)
            print(json.dumps({"status": "valid", "file": str(target)}, sort_keys=True))
            return 0
        if args.command == "build-dataset":
            return _build_dataset(args)
        if args.command == "healthcheck":
            return _healthcheck(args.state_root, args.stale_after_seconds)
    except (
        CatalogLockedError,
        ConfigLoadError,
        DatasetQualityError,
        OSError,
        RawSegmentError,
        ValueError,
    ) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}), file=sys.stderr)
        return 2
    raise RuntimeError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
