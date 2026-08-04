"""Independent safety-sentinel command line."""

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

from prometheus_client import start_http_server

from aiquanttrader_native.config import ConfigLoadError, load_config
from aiquanttrader_native.execution.secrets import read_private_key
from aiquanttrader_native.sentinel.metrics import SentinelMetrics
from aiquanttrader_native.sentinel.service import HyperliquidControlClient, SafetySentinel


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aqt-sentinel")
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--healthcheck", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        bundle = load_config(args.config_dir, args.environment)
        settings = bundle.settings
        if args.healthcheck:
            url = f"http://127.0.0.1:{settings.sentinel.metrics_port}/metrics"
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    body = response.read(65_536)
            except (OSError, urllib.error.URLError) as exc:
                print(json.dumps({"status": "unhealthy", "error": str(exc)}), file=sys.stderr)
                return 1
            healthy = response.status == 200 and b"aqt_sentinel_" in body
            print(json.dumps({"status": "ready" if healthy else "unhealthy"}))
            return 0 if healthy else 1
        account = settings.exchange.account_address
        secret_path = settings.exchange.control_wallet_secret_path
        if not settings.sentinel.enabled or account is None or secret_path is None:
            raise ValueError("sentinel, account, and control-wallet reference must be enabled")
        metrics = SentinelMetrics()
        start_http_server(
            settings.sentinel.metrics_port,
            addr=settings.sentinel.metrics_host,
            registry=metrics.registry,
        )
        client = HyperliquidControlClient(
            private_key=read_private_key(secret_path),
            base_url=str(settings.exchange.http_url),
            account_address=account,
            timeout_seconds=settings.execution.adapter_http_timeout_seconds,
        )
        sentinel = SafetySentinel(
            bundle=bundle,
            heartbeat_path=settings.storage.state_root / "execution" / "heartbeat.json",
            client=client,
            metrics=metrics,
        )
        stop = threading.Event()

        def request_stop(_signum: int, _frame: object) -> None:
            stop.set()

        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
        while not stop.wait(settings.sentinel.poll_interval_ms / 1_000):
            sentinel.step()
        return 0
    except (ConfigLoadError, OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
