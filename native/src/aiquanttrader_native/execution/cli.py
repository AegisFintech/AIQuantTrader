"""Trading-node and persistent kill-switch command line."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from prometheus_client import start_http_server

from aiquanttrader_native.config import ConfigLoadError, load_config
from aiquanttrader_native.execution.heartbeat import (
    HeartbeatPublisher,
    read_heartbeat,
)
from aiquanttrader_native.execution.journal import ExecutionJournal
from aiquanttrader_native.execution.metrics import ExecutionMetrics
from aiquanttrader_native.execution.node import (
    build_trading_node,
    mark_stale_submissions,
    run_trading_node,
)
from aiquanttrader_native.execution.secrets import private_key_address, read_private_key
from aiquanttrader_native.governance.approval import (
    configured_artifact_paths,
    verify_deployment_admission,
)
from aiquanttrader_native.governance.ledger import (
    DeploymentAdmissionGuard,
    DeploymentAdmissionLedger,
)
from aiquanttrader_native.risk import KillSwitchStore, RiskAuthority


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aqt-execution")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run the risk-gated Nautilus trading node")
    _add_config(run)
    run.add_argument("--code-identity")
    run.add_argument("--image-identity")
    run.add_argument("--dependency-lock-path", type=Path)

    health = commands.add_parser("healthcheck", help="verify execution heartbeat readiness")
    _add_config(health)

    for name in ("kill", "clear-kill", "kill-status"):
        command = commands.add_parser(name)
        _add_config(command)
        if name != "kill-status":
            command.add_argument("--actor", required=True)
            command.add_argument("--reason", required=True)
    return parser


def _add_config(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--environment", required=True)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        bundle = load_config(args.config_dir, args.environment)
        state_root = bundle.settings.storage.state_root
        kill_switch = KillSwitchStore(state_root / "execution" / "operator-kill.json")
        if args.command == "healthcheck":
            try:
                status_heartbeat = read_heartbeat(state_root / "execution" / "heartbeat.json")
            except (OSError, ValueError) as exc:
                print(json.dumps({"status": "unhealthy", "error": str(exc)}), file=sys.stderr)
                return 1
            settings = bundle.settings
            now_ns = time.time_ns()
            account = settings.exchange.account_address
            age_ms = max(0, now_ns - status_heartbeat.heartbeat_ts_ns) // 1_000_000
            healthy = (
                age_ms <= settings.sentinel.heartbeat_stale_after_ms
                and status_heartbeat.environment == settings.environment
                and account is not None
                and status_heartbeat.account_address.lower() == account.lower()
                and status_heartbeat.config_fingerprint == bundle.fingerprint
                and status_heartbeat.execution_healthy
                and status_heartbeat.reconciliation_complete
                and not status_heartbeat.operator_kill
            )
            payload = {"status": "ready" if healthy else "unhealthy", "age_ms": age_ms}
            print(json.dumps(payload, sort_keys=True), file=sys.stdout if healthy else sys.stderr)
            return 0 if healthy else 1
        if args.command == "kill-status":
            print(kill_switch.read().model_dump_json())
            return 0
        if args.command in {"kill", "clear-kill"}:
            record = (
                kill_switch.activate(actor=args.actor, reason=args.reason)
                if args.command == "kill"
                else kill_switch.clear(actor=args.actor, reason=args.reason)
            )
            print(record.model_dump_json())
            return 0
        if args.command == "run":
            settings = bundle.settings
            secret_path = settings.exchange.trading_wallet_secret_path
            account = settings.exchange.account_address
            if secret_path is None or account is None:
                raise ValueError("enabled execution requires trading wallet and account references")
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
                    raise ValueError(
                        "mainnet execution requires code, image, and dependency identities"
                    )
                admission = verify_deployment_admission(
                    bundle,
                    configured_artifact_paths(
                        bundle,
                        runtime_dependency_lock_path=args.dependency_lock_path,
                    ),
                    code_identity=args.code_identity,
                    image_identity=args.image_identity,
                    wallet_role="trading",
                    wallet_address=private_key_address(private_key),
                )
                admission_ledger = DeploymentAdmissionLedger(
                    state_root / "governance" / "admissions.sqlite3",
                    read_only=True,
                )
                admission_guard = DeploymentAdmissionGuard(admission_ledger, admission)
                admission_guard.require_active()
            journal = ExecutionJournal(state_root / "execution" / "journal.sqlite3")
            mark_stale_submissions(
                journal,
                cutoff_ts_ns=time.time_ns()
                - settings.execution.unknown_order_timeout_ms * 1_000_000,
            )
            heartbeat = HeartbeatPublisher(
                state_root / "execution" / "heartbeat.json",
                environment=settings.environment,
                account_address=account,
                config_fingerprint=bundle.fingerprint,
                kill_switch=kill_switch,
                admission=admission,
            )
            authority = RiskAuthority(
                settings.risk,
                settings.execution,
                kill_switch=kill_switch,
                inflight_count=journal.unresolved_command_count,
            )
            metrics = ExecutionMetrics()
            if admission is not None and admission_guard is not None:
                metrics.set_deployment_admission(
                    active=True,
                    expiry_seconds=admission.approval.expires_at.timestamp(),
                    capital_limit_usd=float(admission.approval.capital_limit_usd),
                )
            start_http_server(
                settings.observability.execution_metrics_port,
                addr=settings.observability.execution_metrics_host,
                registry=metrics.registry,
            )
            built = build_trading_node(
                bundle,
                private_key,
                journal=journal,
                authority=authority,
                heartbeat=heartbeat,
                metrics=metrics,
                admission=admission,
                admission_guard=admission_guard,
            )
            try:
                run_trading_node(
                    built,
                    heartbeat=heartbeat,
                    heartbeat_interval_ms=settings.execution.heartbeat_interval_ms,
                    journal=journal,
                    unknown_order_timeout_ms=settings.execution.unknown_order_timeout_ms,
                    admission_guard=admission_guard,
                )
            finally:
                journal.close()
                if admission_ledger is not None:
                    admission_ledger.close()
            return 0
    except (ConfigLoadError, OSError, sqlite3.Error, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2
    raise RuntimeError(f"unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
