"""Dependency-light CLI and service for research-data readiness."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

from prometheus_client import start_http_server

from aiquanttrader.market_data.io import atomic_write_bytes
from aiquanttrader.research.metrics import DataReadinessMetrics
from aiquanttrader.research.readiness import (
    DataReadinessMonitor,
    evaluate_data_readiness,
    load_readiness_inputs,
)
from aiquanttrader.research.readiness_models import ResearchDataReadinessReport


def readiness_summary(report: ResearchDataReadinessReport) -> dict[str, object]:
    return {
        "status": "ready" if report.ready_for_horizon_audit else "collecting",
        "report_id": report.report_id,
        "completion_bps": report.completion_bps,
        "current_chain_started_ts_ns": report.latest_contiguous_started_ts_ns,
        "latest_contiguous_span_ns": report.latest_contiguous_span_ns,
        "required_validation_span_ns": report.required_validation_span_ns,
        "remaining_validation_span_ns": report.remaining_validation_span_ns,
        "estimated_additional_bytes_required": report.estimated_additional_bytes_required,
        "storage_headroom_bytes": report.storage_headroom_bytes,
        "continuity_break_count": report.continuity_break_count,
        "continuity_breaks_by_reason": {
            item.name: item.count for item in report.continuity_breaks_by_reason
        },
        "latest_continuity_break": (
            None
            if report.latest_continuity_break is None
            else report.latest_continuity_break.model_dump(mode="json")
        ),
        "failed_gates": [gate.gate for gate in report.gates if not gate.passed],
        "model_training_authorized": report.model_training_authorized,
        "production_promotion_authorized": report.production_promotion_authorized,
    }


def serve_data_readiness(args: argparse.Namespace) -> int:
    if not 5 <= args.poll_seconds <= 3_600:
        raise ValueError("readiness poll interval must be in [5, 3600] seconds")
    if not 1 <= args.metrics_port <= 65_535:
        raise ValueError("readiness metrics port must be in [1, 65535]")
    policy, validation_policy = load_readiness_inputs(args.policy, args.validation_policy)
    metrics = DataReadinessMetrics()
    start_http_server(args.metrics_port, addr=args.metrics_host, registry=metrics.registry)
    monitor = DataReadinessMonitor(
        data_root=args.data_root,
        state_root=args.state_root,
        policy=policy,
        validation_policy=validation_policy,
        metrics=metrics,
    )
    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    monitor.write_state("starting")
    latest: ResearchDataReadinessReport | None = None
    while not stop.is_set():
        try:
            latest = monitor.run_once()
            print(json.dumps(readiness_summary(latest), sort_keys=True), flush=True)
        except Exception as exc:
            monitor.record_failure(exc)
            print(
                json.dumps({"status": "failed", "error_code": type(exc).__name__}, sort_keys=True),
                file=sys.stderr,
                flush=True,
            )
        stop.wait(args.poll_seconds)
    metrics.set_service_healthy(False)
    monitor.write_state("stopped", report=latest)
    return 0


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--validation-policy", type=Path, required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aqt-research-readiness")
    commands = parser.add_subparsers(dest="command", required=True)
    evaluate = commands.add_parser("evaluate", help="write a one-shot readiness report")
    _add_common(evaluate)
    evaluate.add_argument("--output", type=Path, required=True)

    serve = commands.add_parser("serve", help="publish continuous readiness state and metrics")
    _add_common(serve)
    serve.add_argument("--state-root", type=Path, required=True)
    serve.add_argument("--poll-seconds", type=int, default=60)
    serve.add_argument("--metrics-host", default="0.0.0.0")  # nosec B104
    serve.add_argument("--metrics-port", type=int, default=9114)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "serve":
            return serve_data_readiness(args)
        policy, validation_policy = load_readiness_inputs(args.policy, args.validation_policy)
        report = evaluate_data_readiness(
            data_root=args.data_root,
            policy=policy,
            validation_policy=validation_policy,
        )
        atomic_write_bytes(args.output, report.canonical_bytes() + b"\n")
        print(json.dumps({**readiness_summary(report), "report": str(args.output)}, sort_keys=True))
        return 0 if report.ready_for_horizon_audit else 3
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
