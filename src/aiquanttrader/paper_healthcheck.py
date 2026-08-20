"""Lightweight fail-closed Docker health probe for the paper service.

This module intentionally uses only the Python standard library. Importing the
full paper CLI loads the execution, research, and analytics dependency graph,
which is inappropriate for a probe that runs every few seconds.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import cast

_RUNNING_STATUSES = frozenset({"warming", "ready"})
_VALID_STATUSES = frozenset({"starting", "warming", "ready", "degraded", "stopped", "failed"})


def _required_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"paper status field {key!r} must be a non-negative integer")
    return value


def _required_bool(payload: dict[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"paper status field {key!r} must be a boolean")
    return value


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"paper status field {key!r} must be a non-empty string")
    return value


def evaluate_status(
    state_root: Path,
    stale_after_ms: int,
    *,
    now_ns: int | None = None,
) -> tuple[dict[str, object], bool]:
    """Read the minimal health projection and return its output and readiness."""

    if stale_after_ms <= 0:
        raise ValueError("paper heartbeat stale threshold must be positive")
    status_path = state_root.resolve() / "paper" / "status.json"
    raw: object = json.loads(status_path.read_bytes())
    if not isinstance(raw, dict):
        raise ValueError("paper status must be a JSON object")
    payload = cast(dict[str, object], raw)
    if _required_int(payload, "schema_version") != 1:
        raise ValueError("paper status schema_version must be 1")
    status = _required_string(payload, "status")
    if status not in _VALID_STATUSES:
        raise ValueError("paper status contains an unsupported lifecycle state")
    run_id = _required_string(payload, "run_id")
    heartbeat_ts_ns = _required_int(payload, "heartbeat_ts_ns")
    feed_connected = _required_bool(payload, "feed_connected")
    feature_ready = _required_bool(payload, "feature_ready")
    operator_kill = _required_bool(payload, "operator_kill")

    observed_now_ns = time.time_ns() if now_ns is None else now_ns
    age_ns = observed_now_ns - heartbeat_ts_ns
    fresh = 0 <= age_ns <= stale_after_ms * 1_000_000
    ready = status in _RUNNING_STATUSES and feed_connected and not operator_kill and fresh
    result: dict[str, object] = {
        "status": "ready" if ready else "not_ready",
        "run_id": run_id,
        "heartbeat_age_ms": age_ns / 1_000_000,
        "feed_connected": feed_connected,
        "feature_ready": feature_ready,
        "operator_kill": operator_kill,
    }
    return result, ready


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aqt-paper-healthcheck")
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--stale-after-ms", type=int, default=5_000)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result, ready = evaluate_status(args.state_root, args.stale_after_ms)
    except (OSError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
