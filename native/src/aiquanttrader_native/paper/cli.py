"""Operational entry point for live paper trading, kills, health, and evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
import time
from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path

from prometheus_client import CollectorRegistry, start_http_server
from pydantic import ValidationError

from aiquanttrader_native.config import ConfigLoadError, load_config
from aiquanttrader_native.market_data.io import atomic_write_bytes
from aiquanttrader_native.market_data.protocol import ProtocolError, parse_frame
from aiquanttrader_native.market_data.raw import RawSegmentReader
from aiquanttrader_native.paper.config import load_paper_artifacts
from aiquanttrader_native.paper.evidence import evaluate_paper_evidence
from aiquanttrader_native.paper.journal import PaperJournal
from aiquanttrader_native.paper.models import PaperEvidenceReport, PaperRuntimeStatus
from aiquanttrader_native.paper.service import PaperLiveService
from aiquanttrader_native.risk.kill_switch import KillSwitchStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aqt-paper")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run live public-feed paper trading")
    run.add_argument("--config-dir", type=Path, required=True)
    run.add_argument("--environment", default="paper")
    run.add_argument(
        "--code-identity",
        default=os.environ.get("AQT_NATIVE_CODE_IDENTITY", "unreviewed-local"),
    )

    replay = commands.add_parser("replay", help="replay retained raw data through paper code")
    replay.add_argument("--config-dir", type=Path, required=True)
    replay.add_argument("--environment", default="paper")
    replay.add_argument(
        "--code-identity",
        default=os.environ.get("AQT_NATIVE_CODE_IDENTITY", "unreviewed-local"),
    )
    replay.add_argument("--raw-segment", type=Path, action="append", required=True)

    health = commands.add_parser("healthcheck", help="validate paper heartbeat and feed")
    health.add_argument("--state-root", type=Path, required=True)
    health.add_argument("--stale-after-ms", type=int, default=5_000)
    health.add_argument("--record-observability", action="store_true")

    status = commands.add_parser("status", help="print the current paper state")
    status.add_argument("--state-root", type=Path, required=True)

    kill = commands.add_parser("kill", help="activate or clear the paper operator kill")
    kill.add_argument("action", choices=("activate", "clear"))
    kill.add_argument("--state-root", type=Path, required=True)
    kill.add_argument("--actor", required=True)
    kill.add_argument("--reason", required=True)

    evidence = commands.add_parser("evidence", help="evaluate one immutable paper run")
    evidence.add_argument("--config-dir", type=Path, required=True)
    evidence.add_argument("--environment", default="paper")
    evidence.add_argument("--run-id")
    evidence.add_argument("--sensitivity-report", type=Path, action="append", default=[])
    evidence.add_argument("--output", type=Path, required=True)
    return parser


async def _run(args: argparse.Namespace) -> int:
    bundle = load_config(args.config_dir, args.environment)
    artifacts = load_paper_artifacts(args.config_dir, bundle)
    settings = bundle.settings
    journal = PaperJournal(
        (settings.storage.state_root / "paper" / "paper-journal.sqlite3").resolve()
    )
    kill = KillSwitchStore((settings.storage.state_root / "paper" / "kill-switch.json").resolve())
    registry = CollectorRegistry()
    start_http_server(
        settings.paper.metrics_port,
        addr=settings.paper.metrics_host,
        registry=registry,
    )
    service = PaperLiveService(
        bundle=bundle,
        artifacts=artifacts,
        journal=journal,
        kill_switch=kill,
        code_identity=args.code_identity,
        registry=registry,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    try:
        await service.run(stop)
    finally:
        journal.close()
    return 0


async def _replay(args: argparse.Namespace) -> int:
    bundle = load_config(args.config_dir, args.environment)
    artifacts = load_paper_artifacts(args.config_dir, bundle)
    readers = [RawSegmentReader(path.resolve()) for path in args.raw_segment]
    for reader in readers:
        reader.verify()
    readers.sort(key=lambda reader: (reader.manifest.started_at_ns, reader.manifest.segment_id))
    identities = [reader.manifest.segment_id for reader in readers]
    if len(set(identities)) != len(identities):
        raise ValueError("paper replay raw segments must be unique")
    expected_network = bundle.settings.exchange.network.value
    if any(reader.manifest.network != expected_network for reader in readers):
        raise ValueError("paper replay raw segment network does not match configuration")
    if any(
        current.manifest.started_at_ns < previous.manifest.ended_at_ns
        for previous, current in pairwise(readers)
    ):
        raise ValueError("paper replay raw segments overlap")

    state_root = bundle.settings.storage.state_root
    journal = PaperJournal((state_root / "paper" / "paper-journal.sqlite3").resolve())
    service = PaperLiveService(
        bundle=bundle,
        artifacts=artifacts,
        journal=journal,
        kill_switch=KillSwitchStore((state_root / "paper" / "kill-switch.json").resolve()),
        code_identity=args.code_identity,
        registry=CollectorRegistry(),
    )
    frames = 0
    excluded = 0
    try:
        for reader in readers:
            for record in reader.records():
                try:
                    frame = parse_frame(record.payload, record.metadata)
                except (ProtocolError, ValidationError):
                    excluded += 1
                    continue
                await service.consume_frame(frame)
                frames += 1
        if service.engine is None:
            raise ValueError("paper replay contains no valid BTC L2 state")
        if excluded:
            journal.record_event(
                service.engine.manifest.run_id,
                ts_ns=time.time_ns(),
                kind="replay_exclusions",
                detail=f"excluded {excluded} malformed or invalid raw frames",
            )
        service.mark_stopped()
        print(
            json.dumps(
                {
                    "status": "complete",
                    "run_id": service.engine.manifest.run_id,
                    "raw_segments": len(readers),
                    "consumed_frames": frames,
                    "excluded_frames": excluded,
                    "decisions": service.engine.decision_count,
                    "fills": service.engine.fill_count,
                },
                sort_keys=True,
            )
        )
    finally:
        journal.close()
    return 0


def _healthcheck(state_root: Path, stale_after_ms: int, *, record: bool) -> int:
    if stale_after_ms <= 0:
        raise ValueError("paper heartbeat stale threshold must be positive")
    path = state_root.resolve() / "paper" / "status.json"
    status = PaperRuntimeStatus.model_validate_json(path.read_bytes())
    age_ns = time.time_ns() - status.heartbeat_ts_ns
    ready = (
        status.status in {"warming", "ready"}
        and status.feed_connected
        and not status.operator_kill
        and age_ns <= stale_after_ms * 1_000_000
    )
    if record:
        if not ready:
            raise ValueError("cannot record observability drill while paper service is unhealthy")
        journal = PaperJournal((state_root.resolve() / "paper" / "paper-journal.sqlite3").resolve())
        try:
            manifest = journal.latest_manifest()
            if manifest is None or manifest.run_id != status.run_id:
                raise ValueError("paper status does not match the latest journal run")
            journal.record_drill(
                manifest.run_id,
                "observability",
                ts_ns=time.time_ns(),
                evidence="independent CLI parsed a fresh healthy status contract",
            )
        finally:
            journal.close()
    print(
        json.dumps(
            {
                "status": "ready" if ready else "not_ready",
                "run_id": status.run_id,
                "heartbeat_age_ms": age_ns / 1_000_000,
                "feed_connected": status.feed_connected,
                "feature_ready": status.feature_ready,
            },
            sort_keys=True,
        )
    )
    return 0 if ready else 1


def _status(state_root: Path) -> int:
    status = PaperRuntimeStatus.model_validate_json(
        (state_root.resolve() / "paper" / "status.json").read_bytes()
    )
    print(status.model_dump_json(indent=2))
    return 0


def _kill(args: argparse.Namespace) -> int:
    store = KillSwitchStore((args.state_root.resolve() / "paper" / "kill-switch.json").resolve())
    record = (
        store.activate(actor=args.actor, reason=args.reason)
        if args.action == "activate"
        else store.clear(actor=args.actor, reason=args.reason)
    )
    print(record.model_dump_json())
    return 0


def _evidence(args: argparse.Namespace) -> int:
    bundle = load_config(args.config_dir, args.environment)
    artifacts = load_paper_artifacts(args.config_dir, bundle)
    journal = PaperJournal(
        (bundle.settings.storage.state_root / "paper" / "paper-journal.sqlite3").resolve()
    )
    try:
        manifest = journal.latest_manifest()
        if manifest is None:
            raise ValueError("paper journal contains no run")
        if args.run_id is not None and manifest.run_id != args.run_id:
            raise ValueError("requested paper run is not the latest journal run")
        sensitivity = tuple(
            PaperEvidenceReport.model_validate_json(path.read_bytes())
            for path in args.sensitivity_report
        )
        report = evaluate_paper_evidence(
            manifest=manifest,
            statistics=journal.statistics(manifest.run_id),
            scenario=artifacts.scenario,
            policy=artifacts.evidence_policy,
            required_scenarios=artifacts.sensitivity_scenarios,
            sensitivity_reports=sensitivity,
        )
    finally:
        journal.close()
    output = args.output.resolve()
    atomic_write_bytes(output, report.canonical_bytes() + b"\n")
    print(
        json.dumps(
            {
                "report_id": report.report_id,
                "promotion_eligible": report.promotion_eligible,
                "output": str(output),
            },
            sort_keys=True,
        )
    )
    return 0 if report.promotion_eligible else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "run":
            return asyncio.run(_run(args))
        if args.command == "replay":
            return asyncio.run(_replay(args))
        if args.command == "healthcheck":
            return _healthcheck(
                args.state_root,
                args.stale_after_ms,
                record=args.record_observability,
            )
        if args.command == "status":
            return _status(args.state_root)
        if args.command == "kill":
            return _kill(args)
        if args.command == "evidence":
            return _evidence(args)
    except (ConfigLoadError, OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2
    raise RuntimeError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
