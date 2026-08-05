"""Operator CLI for the split shadow gateway, engine, observer, and evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from prometheus_client import CollectorRegistry, start_http_server

from aiquanttrader.config import ConfigLoadError, load_config
from aiquanttrader.market_data.io import atomic_write_bytes
from aiquanttrader.paper.journal import PaperJournal
from aiquanttrader.risk.kill_switch import KillSwitchStore
from aiquanttrader.shadow.audit import ShadowAuditJournal
from aiquanttrader.shadow.config import load_shadow_artifacts
from aiquanttrader.shadow.evidence import compare_shadow_runs, evaluate_shadow_evidence
from aiquanttrader.shadow.gateway import ShadowGatewayService
from aiquanttrader.shadow.ingress import ShadowIngressReader, ShadowIngressWriter
from aiquanttrader.shadow.metrics import ShadowMetrics
from aiquanttrader.shadow.models import ShadowEvidenceReport, ShadowRuntimeStatus
from aiquanttrader.shadow.observer import ShadowObserver, serve_observer
from aiquanttrader.shadow.security import assert_no_ip_egress
from aiquanttrader.shadow.service import ShadowEngineService

IMAGE_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
DRILLS = (
    "host_reboot",
    "disk_pressure",
    "clock_degradation",
    "recorder_failure",
    "observability_failure",
    "operator_kill",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aqt-shadow")
    commands = parser.add_subparsers(dest="command", required=True)

    gateway = commands.add_parser("gateway", help="run the public raw-first ingress gateway")
    _config_arguments(gateway)
    gateway.add_argument("--ingress-path", type=Path, required=True)

    run = commands.add_parser("run", help="run the network-isolated shadow engine")
    _config_arguments(run)
    _identity_arguments(run)
    run.add_argument("--ingress-path", type=Path, required=True)

    replay = commands.add_parser("replay", help="replay durable ingress into a fresh journal")
    _config_arguments(replay)
    _identity_arguments(replay)
    replay.add_argument("--ingress-path", type=Path, required=True)
    replay.add_argument("--source-journal", type=Path, required=True)
    replay.add_argument("--source-run-id", required=True)
    replay.add_argument("--output-state-root", type=Path, required=True)

    observe = commands.add_parser("observe", help="serve read-only status and metrics")
    observe.add_argument("--state-root", type=Path, required=True)
    observe.add_argument("--host", default="0.0.0.0")
    observe.add_argument("--port", type=int, default=9113)
    observe.add_argument("--stale-after-ms", type=int, default=5_000)

    health = commands.add_parser("healthcheck", help="validate isolated shadow status")
    health.add_argument("--state-root", type=Path, required=True)
    health.add_argument("--stale-after-ms", type=int, default=5_000)

    status = commands.add_parser("status", help="print isolated shadow status")
    status.add_argument("--state-root", type=Path, required=True)

    kill = commands.add_parser("kill", help="activate or clear the shadow operator kill")
    kill.add_argument("action", choices=("activate", "clear"))
    kill.add_argument("--state-root", type=Path, required=True)
    kill.add_argument("--actor", required=True)
    kill.add_argument("--reason", required=True)

    drill = commands.add_parser("record-drill", help="bind retained fault evidence to a run")
    drill.add_argument("drill", choices=DRILLS)
    drill.add_argument("--state-root", type=Path, required=True)
    drill.add_argument("--evidence-file", type=Path, required=True)

    compare = commands.add_parser("compare", help="compare live and retained-ingress replay")
    compare.add_argument("--source-journal", type=Path, required=True)
    compare.add_argument("--replay-journal", type=Path, required=True)
    compare.add_argument("--source-run-id", required=True)
    compare.add_argument("--replay-run-id", required=True)
    compare.add_argument("--source-audit", type=Path)
    compare.add_argument("--output", type=Path, required=True)

    evidence = commands.add_parser("evidence", help="evaluate frozen Phase 8 gates")
    _config_arguments(evidence)
    evidence.add_argument("--run-id")
    evidence.add_argument("--sensitivity-report", type=Path, action="append", default=[])
    evidence.add_argument("--output", type=Path, required=True)
    return parser


def _config_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--environment", default="shadow")


def _identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--code-identity",
        default=os.environ.get("AQT_NATIVE_CODE_IDENTITY", "unreviewed-local"),
    )
    parser.add_argument(
        "--image-identity",
        default=os.environ.get("AQT_NATIVE_IMAGE_IDENTITY", ""),
    )


async def _gateway(args: argparse.Namespace) -> int:
    bundle = load_config(args.config_dir, args.environment)
    ingress = ShadowIngressWriter(args.ingress_path.resolve())
    registry = CollectorRegistry()
    start_http_server(
        bundle.settings.market_data.metrics_port,
        addr=bundle.settings.market_data.metrics_host,
        registry=registry,
    )
    service = ShadowGatewayService(bundle=bundle, ingress=ingress, registry=registry)
    stop = _signal_event()
    try:
        await service.run(stop)
    finally:
        ingress.close()
    return 0


async def _run_engine(args: argparse.Namespace) -> int:
    _validate_identity(args.image_identity)
    assert_no_ip_egress()
    bundle = load_config(args.config_dir, args.environment)
    artifacts = load_shadow_artifacts(args.config_dir, bundle)
    state = bundle.settings.storage.state_root / "shadow"
    ingress = ShadowIngressReader(args.ingress_path.resolve())
    journal = PaperJournal((state / "shadow-journal.sqlite3").resolve())
    audit = ShadowAuditJournal((state / "shadow-audit.sqlite3").resolve())
    metrics = ShadowMetrics(CollectorRegistry(), (state / "metrics.prom").resolve())
    service = ShadowEngineService(
        bundle=bundle,
        artifacts=artifacts,
        ingress=ingress,
        journal=journal,
        audit=audit,
        kill_switch=KillSwitchStore((state / "kill-switch.json").resolve()),
        code_identity=args.code_identity,
        image_identity=args.image_identity,
        metrics=metrics,
    )
    try:
        await service.run(_signal_event())
    finally:
        ingress.close()
        journal.close()
        audit.close()
    return 0


async def _replay(args: argparse.Namespace) -> int:
    _validate_identity(args.image_identity)
    output = args.output_state_root.resolve()
    output.mkdir(parents=True, exist_ok=True)
    journal_path = output / "shadow-journal.sqlite3"
    audit_path = output / "shadow-audit.sqlite3"
    if journal_path.exists() or audit_path.exists():
        raise ValueError("shadow replay output must not contain an existing journal")
    bundle = load_config(args.config_dir, args.environment)
    artifacts = load_shadow_artifacts(args.config_dir, bundle)
    source = PaperJournal(args.source_journal.resolve())
    try:
        source_manifest = source.latest_manifest()
        if source_manifest is None or source_manifest.run_id != args.source_run_id:
            raise ValueError("source journal does not contain the requested latest run")
        if (
            source_manifest.code_identity != args.code_identity
            or source_manifest.image_identity != args.image_identity
            or source_manifest.config_fingerprint != bundle.fingerprint
            or source_manifest.feature_config_sha256 != artifacts.paper.feature_config_sha256
            or source_manifest.strategy_config_sha256 != artifacts.paper.strategy_config_sha256
            or source_manifest.scenario_sha256 != artifacts.paper.scenario.sha256()
            or source_manifest.evidence_policy_sha256 != artifacts.paper.evidence_policy_sha256
        ):
            raise ValueError("source journal lineage does not match replay inputs")
        start_sequence = source_manifest.source_start_sequence
        source_checkpoint = source.latest_checkpoint(source_manifest.run_id)
        if source_checkpoint is None or source_checkpoint.source_sequence is None:
            raise ValueError("source run has no replayable ingress checkpoint")
        end_sequence = source_checkpoint.source_sequence
        if end_sequence <= start_sequence:
            raise ValueError("source replay boundary contains no processed market frame")
    finally:
        source.close()
    ingress = ShadowIngressReader(args.ingress_path.resolve())
    journal = PaperJournal(journal_path)
    audit = ShadowAuditJournal(audit_path)
    metrics = ShadowMetrics(CollectorRegistry(), output / "metrics.prom")
    service = ShadowEngineService(
        bundle=bundle,
        artifacts=artifacts,
        ingress=ingress,
        journal=journal,
        audit=audit,
        kill_switch=KillSwitchStore(output / "kill-switch.json"),
        code_identity=args.code_identity,
        image_identity=args.image_identity,
        metrics=metrics,
        replay_mode=True,
        start_sequence=start_sequence,
        status_path=output / "status.json",
    )
    try:
        retained_latest = ingress.latest_sequence()
        if end_sequence > retained_latest:
            raise ValueError("source replay boundary exceeds retained ingress")
        while service.cursor < end_sequence:
            records = ingress.read_after(
                service.cursor, limit=bundle.settings.shadow.ingress_batch_size
            )
            records = tuple(record for record in records if record.sequence <= end_sequence)
            if not records:
                raise ValueError("shadow replay ingress ended before its advertised sequence")
            for record in records:
                await service.consume_record(record)
        if service.engine is None:
            raise ValueError("shadow replay contains no valid BTC L2 state")
        print(
            json.dumps(
                {
                    "status": "complete",
                    "run_id": service.engine.manifest.run_id,
                    "ingress_start_sequence": start_sequence,
                    "ingress_end_sequence": end_sequence,
                    "ingress_frames": end_sequence - start_sequence,
                    "decisions": service.engine.decision_count,
                    "fills": service.engine.fill_count,
                },
                sort_keys=True,
            )
        )
    finally:
        ingress.close()
        journal.close()
        audit.close()
    return 0


def _healthcheck(state_root: Path, stale_after_ms: int) -> int:
    status, ready, age = ShadowObserver(state_root, stale_after_ms=stale_after_ms).status()
    print(
        json.dumps(
            {
                "status": "ready" if ready else "not_ready",
                "run_id": status.run_id,
                "heartbeat_age_ms": age / 1_000_000,
                "feed_connected": status.feed_connected,
                "feature_ready": status.feature_ready,
                "credential_capability": status.credential_capability,
                "ip_network_capability": status.ip_network_capability,
            },
            sort_keys=True,
        )
    )
    return 0 if ready else 1


def _status(state_root: Path) -> int:
    status = ShadowRuntimeStatus.model_validate_json(
        (state_root.resolve() / "shadow" / "status.json").read_bytes()
    )
    print(status.model_dump_json(indent=2))
    return 0


def _kill(args: argparse.Namespace) -> int:
    store = KillSwitchStore((args.state_root.resolve() / "shadow" / "kill-switch.json").resolve())
    record = (
        store.activate(actor=args.actor, reason=args.reason)
        if args.action == "activate"
        else store.clear(actor=args.actor, reason=args.reason)
    )
    print(record.model_dump_json())
    return 0


def _record_drill(args: argparse.Namespace) -> int:
    state = args.state_root.resolve() / "shadow"
    journal = PaperJournal((state / "shadow-journal.sqlite3").resolve())
    audit = ShadowAuditJournal((state / "shadow-audit.sqlite3").resolve())
    try:
        manifest = journal.latest_manifest()
        if manifest is None:
            raise ValueError("shadow journal contains no run")
        digest = audit.record_drill(
            manifest.run_id,
            args.drill,
            completed_ts_ns=time.time_ns(),
            evidence_path=args.evidence_file,
        )
    finally:
        journal.close()
        audit.close()
    print(json.dumps({"drill": args.drill, "evidence_sha256": digest}, sort_keys=True))
    return 0


def _compare(args: argparse.Namespace) -> int:
    source = PaperJournal(args.source_journal.resolve())
    replay = PaperJournal(args.replay_journal.resolve())
    try:
        report = compare_shadow_runs(
            source,
            replay,
            source_run_id=args.source_run_id,
            replay_run_id=args.replay_run_id,
        )
        if args.source_audit is not None:
            audit = ShadowAuditJournal(args.source_audit.resolve())
            try:
                audit.record_comparison(args.source_run_id, report)
            finally:
                audit.close()
    finally:
        source.close()
        replay.close()
    atomic_write_bytes(args.output.resolve(), report.canonical_bytes() + b"\n")
    print(report.model_dump_json())
    return 0 if report.decision_mismatches == 0 and report.command_mismatches == 0 else 1


def _evidence(args: argparse.Namespace) -> int:
    bundle = load_config(args.config_dir, args.environment)
    artifacts = load_shadow_artifacts(args.config_dir, bundle)
    state = bundle.settings.storage.state_root / "shadow"
    journal = PaperJournal((state / "shadow-journal.sqlite3").resolve())
    audit = ShadowAuditJournal((state / "shadow-audit.sqlite3").resolve())
    try:
        manifest = journal.latest_manifest()
        if manifest is None:
            raise ValueError("shadow journal contains no run")
        if args.run_id is not None and manifest.run_id != args.run_id:
            raise ValueError("requested shadow run is not the latest journal run")
        statistics = journal.statistics(manifest.run_id)
        observation_ns = max(0, statistics.ended_ts_ns - statistics.started_ts_ns)
        audit_statistics = audit.statistics(
            manifest.run_id,
            observation_ns=observation_ns,
            health_interval_ns=bundle.settings.shadow.health_sample_interval_ms * 1_000_000,
        )
        sensitivity = tuple(
            ShadowEvidenceReport.model_validate_json(path.read_bytes())
            for path in args.sensitivity_report
        )
        report = evaluate_shadow_evidence(
            manifest=manifest,
            statistics=statistics,
            audit=audit_statistics,
            artifacts=artifacts,
            determinism=audit.latest_comparison(manifest.run_id),
            sensitivity_reports=sensitivity,
        )
    finally:
        journal.close()
        audit.close()
    atomic_write_bytes(args.output.resolve(), report.canonical_bytes() + b"\n")
    print(
        json.dumps(
            {
                "report_id": report.report_id,
                "awaiting_human_approval": report.awaiting_human_approval,
                "output": str(args.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0 if report.awaiting_human_approval else 1


def _signal_event() -> asyncio.Event:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    return stop


def _validate_identity(image_identity: str) -> None:
    if not IMAGE_PATTERN.fullmatch(image_identity):
        raise ValueError("shadow image identity must be an immutable sha256 digest")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "gateway":
            return asyncio.run(_gateway(args))
        if args.command == "run":
            return asyncio.run(_run_engine(args))
        if args.command == "replay":
            return asyncio.run(_replay(args))
        if args.command == "observe":
            serve_observer(
                ShadowObserver(args.state_root, stale_after_ms=args.stale_after_ms),
                host=args.host,
                port=args.port,
            )
            return 0
        if args.command == "healthcheck":
            return _healthcheck(args.state_root, args.stale_after_ms)
        if args.command == "status":
            return _status(args.state_root)
        if args.command == "kill":
            return _kill(args)
        if args.command == "record-drill":
            return _record_drill(args)
        if args.command == "compare":
            return _compare(args)
        if args.command == "evidence":
            return _evidence(args)
    except (ConfigLoadError, OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2
    raise RuntimeError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
