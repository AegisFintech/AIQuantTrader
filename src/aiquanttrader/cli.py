"""Command-line entry point for native configuration and contract operations."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from aiquanttrader.config import ConfigLoadError, load_config
from aiquanttrader.market_data.io import atomic_write_bytes
from aiquanttrader.schemas import export_schemas
from aiquanttrader.service import create_health_server
from aiquanttrader.service.storage import (
    evaluate_storage_expansion,
    inspect_host_storage,
    load_retention_requirement,
    load_storage_expansion_policy,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aqt-native")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_config_arguments(command: argparse.ArgumentParser) -> None:
        command.add_argument("--config-dir", type=Path, required=True)
        command.add_argument("--environment", required=True)

    validate = subparsers.add_parser("validate-config", help="validate effective configuration")
    add_config_arguments(validate)

    show = subparsers.add_parser("show-config", help="print effective non-secret configuration")
    add_config_arguments(show)

    serve = subparsers.add_parser("serve-health", help="serve liveness and readiness endpoints")
    add_config_arguments(serve)

    healthcheck = subparsers.add_parser("healthcheck", help="check a readiness endpoint")
    healthcheck.add_argument("--url", default="http://127.0.0.1:9108/health/ready")
    healthcheck.add_argument("--timeout", type=float, default=2.0)

    schemas = subparsers.add_parser("export-schemas", help="write or verify JSON schemas")
    schemas.add_argument("--output", type=Path, required=True)
    schemas.add_argument("--check", action="store_true")

    storage = subparsers.add_parser(
        "storage-expansion-preflight",
        help="write a read-only host storage expansion report",
    )
    storage.add_argument("--data-root", type=Path, required=True)
    storage.add_argument("--readiness-state", type=Path, required=True)
    storage.add_argument("--policy", type=Path, required=True)
    storage.add_argument("--output", type=Path, required=True)
    return parser


def _summary(bundle: Any) -> dict[str, Any]:
    settings = bundle.settings
    return {
        "status": "valid",
        "environment": settings.environment,
        "mode": settings.mode.value,
        "network": settings.exchange.network.value,
        "instrument_id": settings.instrument.instrument_id,
        "execution_enabled": settings.execution.enabled,
        "can_submit_orders": settings.can_submit_orders,
        "config_fingerprint": bundle.fingerprint,
        "sources": [str(path) for path in bundle.sources],
    }


def _run_healthcheck(url: str, timeout: float) -> int:
    if timeout <= 0 or timeout > 30:
        raise ValueError("healthcheck timeout must be in (0, 30] seconds")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read())
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "unhealthy", "error": str(exc)}), file=sys.stderr)
        return 1
    if response.status != 200 or payload.get("status") != "ready":
        print(json.dumps({"status": "unhealthy", "response": payload}), file=sys.stderr)
        return 1
    print(json.dumps(payload, sort_keys=True))
    return 0


def _serve(config_dir: Path, environment: str) -> int:
    bundle = load_config(config_dir, environment)
    server = create_health_server(bundle)
    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    server.timeout = 0.5
    print(json.dumps({"status": "starting", **_summary(bundle)}, sort_keys=True), flush=True)
    try:
        while not stop.is_set():
            server.handle_request()
    finally:
        server.server_close()
    return 0


def _storage_expansion_preflight(
    *,
    data_root: Path,
    readiness_state: Path,
    policy_path: Path,
    output: Path,
) -> int:
    now_ns = time.time_ns()
    policy = load_storage_expansion_policy(policy_path)
    requirement = load_retention_requirement(
        readiness_state,
        now_ns=now_ns,
        maximum_age_ns=policy.maximum_readiness_age_ns,
    )
    report = evaluate_storage_expansion(
        policy=policy,
        requirement=requirement,
        snapshot=inspect_host_storage(data_root),
        generated_ts_ns=now_ns,
    )
    artifact_path = output.resolve()
    atomic_write_bytes(artifact_path, report.canonical_bytes() + b"\n")
    print(
        json.dumps(
            {
                "status": ("ready" if report.ready_for_expansion_closeout else "action_required"),
                "report_id": report.report_id,
                "stage": report.stage.value,
                "filesystem_device_id": report.snapshot.filesystem_device_id,
                "filesystem_total_bytes": report.snapshot.filesystem_total_bytes,
                "filesystem_available_bytes": report.snapshot.filesystem_available_bytes,
                "capacity_shortfall_bytes": report.capacity_shortfall_bytes,
                "minimum_block_device_bytes": report.minimum_block_device_bytes,
                "recommended_block_device_bytes": report.recommended_block_device_bytes,
                "operator_action_required": report.operator_action_required,
                "output": str(artifact_path),
            },
            sort_keys=True,
        )
    )
    return 0 if report.ready_for_expansion_closeout else 3


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-config":
            print(
                json.dumps(
                    _summary(load_config(args.config_dir, args.environment)),
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "show-config":
            bundle = load_config(args.config_dir, args.environment)
            payload = bundle.settings.model_dump(mode="json")
            payload["config_fingerprint"] = bundle.fingerprint
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        if args.command == "serve-health":
            return _serve(args.config_dir, args.environment)
        if args.command == "healthcheck":
            return _run_healthcheck(args.url, args.timeout)
        if args.command == "export-schemas":
            paths = export_schemas(args.output, check=args.check)
            print(json.dumps({"status": "valid", "schemas": [str(path) for path in paths]}))
            return 0
        if args.command == "storage-expansion-preflight":
            return _storage_expansion_preflight(
                data_root=args.data_root,
                readiness_state=args.readiness_state,
                policy_path=args.policy,
                output=args.output,
            )
    except (ConfigLoadError, OSError, ValueError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}), file=sys.stderr)
        return 2
    raise RuntimeError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
