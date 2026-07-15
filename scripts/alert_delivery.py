#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - dependency is installed in production.
    load_dotenv = None


def _load_repo_dotenv(path: Path = ROOT / ".env") -> None:
    if load_dotenv is not None:
        load_dotenv(path)


_load_repo_dotenv()

from finrobot.alert_delivery import (  # noqa: E402
    diff_alerts,
    last_state_path,
    load_metrics,
    save_state,
    should_send_to_telegram,
    telegram_send,
)


DEFAULT_METRICS = ROOT / "data" / "metrics.json"
DEFAULT_LOG = ROOT / "logs" / "alerts.log"


def main(argv: list[str] | None = None) -> int:
    """Deliver FinRobot metrics alert transitions to Telegram."""
    args = _parse_args(argv)
    metrics = load_metrics(args.metrics)
    if not metrics:
        print(f"metrics missing or invalid: {args.metrics}")
        return 2

    current_alerts = _current_alerts(metrics)
    previous = _load_previous_state(args.state)
    transitions = diff_alerts(current_alerts, previous)

    token = os.getenv(args.bot_token_env, "")
    chat_id = os.getenv(args.chat_id_env, "")
    missing_credentials = not args.dry_run and (not token or not chat_id)
    if missing_credentials:
        if not token:
            print(f"[skip] {args.bot_token_env} not set; alerts logged only")
        if not chat_id:
            print(f"[skip] {args.chat_id_env} not set; alerts logged only")

    delivered = 0
    skipped = 0
    errors = 0

    for transition in transitions:
        send = should_send_to_telegram(transition)
        status = "skipped"
        error = ""
        if send and not missing_credentials:
            try:
                telegram_send(
                    transition,
                    metrics,
                    bot_token=token,
                    chat_id=chat_id,
                    dry_run=args.dry_run,
                )
            except Exception as exc:  # noqa: BLE001 - CLI boundary logs and continues.
                errors += 1
                status = "error"
                error = str(exc)
                _log_transition(args.log, transition, status, error=error)
                _log_error(args.log, transition, error)
                continue
            else:
                delivered += 1
                status = "delivered" if not args.dry_run else "dry_run"
        else:
            skipped += 1

        _log_transition(args.log, transition, status, error=error)

    save_state(args.state, current_alerts)

    if args.json:
        print(json.dumps(transitions, indent=2, sort_keys=True))
    print(
        f"delivered: {delivered} skipped: {skipped} errors: {errors} "
        f"state_path={args.state}"
    )
    if args.dry_run:
        return 0
    return 1 if errors else 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deliver FinRobot metrics alerts.")
    parser.add_argument("--metrics", type=Path, default=DEFAULT_METRICS)
    parser.add_argument("--state", type=Path, default=last_state_path())
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--bot-token-env", default="TELEGRAM_BOT_TOKEN")
    parser.add_argument("--chat-id-env", default="TELEGRAM_ALERT_CHAT_ID")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def _load_previous_state(path: Path) -> list[dict] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, list) else None


def _current_alerts(metrics: dict) -> list[dict]:
    alerts = metrics.get("alerts")
    return alerts if isinstance(alerts, list) else []


def _log_transition(
    path: Path,
    transition: dict,
    status: str,
    *,
    error: str = "",
) -> None:
    payload: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "transition": transition,
    }
    if error:
        payload["error"] = error
    _append_json_line(path, payload)


def _log_error(path: Path, transition: dict, error: str) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": "telegram_error",
        "error": error,
        "transition": transition,
    }
    _append_json_line(path, payload)


def _append_json_line(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
