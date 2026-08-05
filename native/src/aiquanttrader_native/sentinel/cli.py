"""Independent safety-sentinel command line."""

from __future__ import annotations

import argparse
import json
import signal
import sqlite3
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path

from prometheus_client import start_http_server

from aiquanttrader_native.acceptance.audit import OperationalEvidenceLog
from aiquanttrader_native.acceptance.models import AcceptanceComponent
from aiquanttrader_native.config import ConfigLoadError, load_config
from aiquanttrader_native.execution.secrets import private_key_address, read_private_key
from aiquanttrader_native.governance.approval import (
    configured_artifact_paths,
    verify_deployment_admission,
)
from aiquanttrader_native.governance.ledger import (
    DeploymentAdmissionGuard,
    DeploymentAdmissionLedger,
)
from aiquanttrader_native.sentinel.metrics import SentinelMetrics
from aiquanttrader_native.sentinel.service import HyperliquidControlClient, SafetySentinel


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aqt-sentinel")
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--healthcheck", action="store_true")
    parser.add_argument("--code-identity")
    parser.add_argument("--image-identity")
    parser.add_argument("--dependency-lock-path", type=Path)
    parser.add_argument("--operational-evidence-path", type=Path)
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
        private_key = read_private_key(secret_path)
        admission = None
        admission_ledger = None
        admission_guard = None
        if settings.requires_signed_approval:
            if (
                not args.code_identity
                or not args.image_identity
                or args.dependency_lock_path is None
            ):
                raise ValueError("mainnet sentinel requires code, image, and dependency identities")
            admission = verify_deployment_admission(
                bundle,
                configured_artifact_paths(
                    bundle,
                    runtime_dependency_lock_path=args.dependency_lock_path,
                ),
                code_identity=args.code_identity,
                image_identity=args.image_identity,
                wallet_role="control",
                wallet_address=private_key_address(private_key),
                require_active_approval=False,
            )
            admission_ledger = DeploymentAdmissionLedger(
                settings.storage.state_root / "governance" / "admissions.sqlite3",
                read_only=True,
            )
            admission_guard = DeploymentAdmissionGuard(admission_ledger, admission)
            admission_guard.require_active()
        metrics = SentinelMetrics()
        start_http_server(
            settings.sentinel.metrics_port,
            addr=settings.sentinel.metrics_host,
            registry=metrics.registry,
        )
        client = HyperliquidControlClient(
            private_key=private_key,
            base_url=str(settings.exchange.http_url),
            account_address=account,
            vault_address=settings.exchange.vault_address,
            timeout_seconds=settings.execution.adapter_http_timeout_seconds,
        )
        sentinel = SafetySentinel(
            bundle=bundle,
            heartbeat_path=settings.storage.state_root / "execution" / "heartbeat.json",
            client=client,
            metrics=metrics,
            admission=admission,
            admission_guard=admission_guard,
            operational_log=OperationalEvidenceLog(
                args.operational_evidence_path
                or settings.storage.state_root / "sentinel" / "acceptance-events.jsonl",
                component=AcceptanceComponent.SENTINEL,
            ),
        )
        stop = threading.Event()

        def request_stop(_signum: int, _frame: object) -> None:
            stop.set()

        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
        while not stop.wait(settings.sentinel.poll_interval_ms / 1_000):
            sentinel.step()
        if admission_ledger is not None:
            admission_ledger.close()
        return 0
    except (ConfigLoadError, OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
