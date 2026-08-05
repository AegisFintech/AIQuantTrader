"""Command-line entry point for native configuration and contract operations."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from aiquanttrader.config import ConfigLoadError, load_config
from aiquanttrader.schemas import export_schemas
from aiquanttrader.service import create_health_server


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
    except (ConfigLoadError, ValueError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}), file=sys.stderr)
        return 2
    raise RuntimeError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
