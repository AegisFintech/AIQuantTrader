"""Standard-library-only health probe for the research readiness monitor."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import cast


def _non_negative_int(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"readiness state field {key!r} must be a non-negative integer")
    return value


def evaluate_status(
    state_root: Path,
    stale_after_seconds: int,
    *,
    now_ns: int | None = None,
) -> tuple[dict[str, object], bool]:
    if stale_after_seconds <= 0:
        raise ValueError("readiness heartbeat stale threshold must be positive")
    path = state_root.resolve() / "research" / "data-readiness.json"
    raw: object = json.loads(path.read_bytes())
    if not isinstance(raw, dict):
        raise ValueError("readiness state must be a JSON object")
    payload = cast(dict[str, object], raw)
    if _non_negative_int(payload, "schema_version") != 2:
        raise ValueError("readiness state schema_version must be 2")
    status = payload.get("status")
    if status not in {"starting", "running", "stopped", "failed"}:
        raise ValueError("readiness state has an unsupported lifecycle status")
    heartbeat_ns = _non_negative_int(payload, "heartbeat_ts_ns")
    observed_ns = time.time_ns() if now_ns is None else now_ns
    age_ns = observed_ns - heartbeat_ns
    fresh = 0 <= age_ns <= stale_after_seconds * 1_000_000_000
    report = payload.get("report")
    data_ready = False
    if report is not None:
        if not isinstance(report, dict):
            raise ValueError("readiness report must be an object or null")
        if _non_negative_int(cast(dict[str, object], report), "schema_version") != 2:
            raise ValueError("readiness report schema_version must be 2")
        ready_value = report.get("ready_for_horizon_audit")
        if not isinstance(ready_value, bool):
            raise ValueError("readiness report verdict must be a boolean")
        data_ready = ready_value
    healthy = status == "running" and fresh and report is not None
    return (
        {
            "status": "healthy" if healthy else "unhealthy",
            "lifecycle": status,
            "heartbeat_age_seconds": age_ns / 1_000_000_000,
            "data_ready": data_ready,
        },
        healthy,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aqt-research-readiness-healthcheck")
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--stale-after-seconds", type=int, default=180)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result, healthy = evaluate_status(args.state_root, args.stale_after_seconds)
    except (OSError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
