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
from enum import StrEnum
from pathlib import Path
from typing import cast

_READY_STATUSES = frozenset({"warming", "ready"})
_LIVE_STATUSES = frozenset({"starting", "warming", "ready", "degraded"})
_VALID_STATUSES = frozenset({"starting", "warming", "ready", "degraded", "stopped", "failed"})
_FEED_BLOCK_REASONS = frozenset(
    {
        "none",
        "socket_disconnected",
        "public_frame_missing",
        "public_frame_clock_regression",
        "public_frame_stale",
        "asset_context_missing",
        "asset_context_clock_regression",
        "asset_context_stale",
        "bbo_missing",
        "bbo_clock_regression",
        "bbo_stale",
    }
)
_L2_DEPTH_STATES = frozenset({"missing", "clock_regression", "stale", "fresh"})


class ProbeMode(StrEnum):
    """Bound whether the probe checks process liveness or operational readiness."""

    LIVENESS = "liveness"
    READINESS = "readiness"


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


def _optional_signed_int(payload: dict[str, object], key: str) -> int | None:
    value = payload.get(key)
    if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
        raise ValueError(f"paper status field {key!r} must be an integer or null")
    return value


def _expected_feed_block_reason(
    socket_connected: bool,
    component_age_ms: dict[str, int | None],
    stale_after_ms: int,
) -> str:
    if not socket_connected:
        return "socket_disconnected"
    for component in ("public_frame", "asset_context", "bbo"):
        age_ms = component_age_ms[component]
        if age_ms is None:
            return f"{component}_missing"
        if age_ms < 0:
            return f"{component}_clock_regression"
        if age_ms > stale_after_ms:
            return f"{component}_stale"
    return "none"


def evaluate_status(
    state_root: Path,
    stale_after_ms: int,
    *,
    mode: ProbeMode = ProbeMode.READINESS,
    now_ns: int | None = None,
) -> tuple[dict[str, object], bool]:
    """Read the minimal health projection and return the selected probe outcome."""

    if stale_after_ms <= 0:
        raise ValueError("paper heartbeat stale threshold must be positive")
    status_path = state_root.resolve() / "paper" / "status.json"
    raw: object = json.loads(status_path.read_bytes())
    if not isinstance(raw, dict):
        raise ValueError("paper status must be a JSON object")
    payload = cast(dict[str, object], raw)
    if _required_int(payload, "schema_version") != 3:
        raise ValueError("paper status schema_version must be 3")
    status = _required_string(payload, "status")
    if status not in _VALID_STATUSES:
        raise ValueError("paper status contains an unsupported lifecycle state")
    run_id = _required_string(payload, "run_id")
    heartbeat_ts_ns = _required_int(payload, "heartbeat_ts_ns")
    feed_connected = _required_bool(payload, "feed_connected")
    feature_ready = _required_bool(payload, "feature_ready")
    operator_kill = _required_bool(payload, "operator_kill")
    raw_freshness = payload.get("feed_freshness")
    if not isinstance(raw_freshness, dict):
        raise ValueError("paper status feed_freshness must be an object")
    freshness = cast(dict[str, object], raw_freshness)
    if _required_int(freshness, "schema_version") != 2:
        raise ValueError("paper feed freshness schema_version must be 2")
    checked_ts_ns = _required_int(freshness, "checked_ts_ns")
    if checked_ts_ns != heartbeat_ts_ns:
        raise ValueError("paper feed freshness timestamp must match the heartbeat")
    feed_stale_after_ms = _required_int(freshness, "stale_after_ms")
    if feed_stale_after_ms == 0:
        raise ValueError("paper feed stale threshold must be positive")
    depth_stale_after_ms = _required_int(freshness, "depth_stale_after_ms")
    if depth_stale_after_ms == 0:
        raise ValueError("paper L2 depth stale threshold must be positive")
    socket_connected = _required_bool(freshness, "socket_connected")
    component_fresh = {
        "public_frame": _required_bool(freshness, "public_frame_fresh"),
        "asset_context": _required_bool(freshness, "asset_context_fresh"),
        "bbo": _required_bool(freshness, "bbo_fresh"),
        "l2_depth": _required_bool(freshness, "l2_depth_fresh"),
    }
    component_age_ms = {
        "public_frame": _optional_signed_int(freshness, "public_frame_age_ms"),
        "asset_context": _optional_signed_int(freshness, "asset_context_age_ms"),
        "bbo": _optional_signed_int(freshness, "bbo_age_ms"),
        "l2_depth": _optional_signed_int(freshness, "l2_depth_age_ms"),
    }
    expected_component_fresh = {
        component: age_ms is not None
        and 0
        <= age_ms
        <= (depth_stale_after_ms if component == "l2_depth" else feed_stale_after_ms)
        for component, age_ms in component_age_ms.items()
    }
    if component_fresh != expected_component_fresh:
        raise ValueError("paper feed component freshness must match its age")
    feed_ready = _required_bool(freshness, "ready")
    if feed_ready != feed_connected:
        raise ValueError("paper feed freshness verdict must match feed_connected")
    block_reason = _required_string(freshness, "blocking_reason")
    if block_reason not in _FEED_BLOCK_REASONS:
        raise ValueError("paper feed freshness has an unsupported blocking reason")
    depth_state = _required_string(freshness, "l2_depth_state")
    if depth_state not in _L2_DEPTH_STATES:
        raise ValueError("paper feed freshness has an unsupported L2 depth state")
    depth_age_ms = component_age_ms["l2_depth"]
    expected_depth_state = (
        "missing"
        if depth_age_ms is None
        else "clock_regression"
        if depth_age_ms < 0
        else "stale"
        if depth_age_ms > depth_stale_after_ms
        else "fresh"
    )
    if depth_state != expected_depth_state:
        raise ValueError("paper L2 depth state must match its age")
    expected_block_reason = _expected_feed_block_reason(
        socket_connected, component_age_ms, feed_stale_after_ms
    )
    if block_reason != expected_block_reason or feed_ready != (block_reason == "none"):
        raise ValueError("paper feed blocking reason must match its readiness verdict")

    observed_now_ns = time.time_ns() if now_ns is None else now_ns
    age_ns = observed_now_ns - heartbeat_ts_ns
    fresh = 0 <= age_ns <= stale_after_ms * 1_000_000
    live = status in _LIVE_STATUSES and fresh
    ready = status in _READY_STATUSES and feed_connected and not operator_kill and fresh
    passed = live if mode is ProbeMode.LIVENESS else ready
    outcome = (
        ("live" if live else "not_live")
        if mode is ProbeMode.LIVENESS
        else ("ready" if ready else "not_ready")
    )
    result: dict[str, object] = {
        "status": outcome,
        "probe_mode": mode.value,
        "lifecycle": status,
        "live": live,
        "readiness": ready,
        "run_id": run_id,
        "heartbeat_age_ms": age_ns / 1_000_000,
        "feed_connected": feed_connected,
        "feed_blocking_reason": block_reason,
        "feed_socket_connected": socket_connected,
        "feed_component_fresh": component_fresh,
        "feed_component_age_ms": component_age_ms,
        "feed_stale_after_ms": feed_stale_after_ms,
        "depth_stale_after_ms": depth_stale_after_ms,
        "l2_depth_state": depth_state,
        "feature_ready": feature_ready,
        "operator_kill": operator_kill,
    }
    return result, passed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aqt-paper-healthcheck")
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--stale-after-ms", type=int, default=5_000)
    parser.add_argument(
        "--mode",
        choices=tuple(mode.value for mode in ProbeMode),
        default=ProbeMode.READINESS.value,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result, passed = evaluate_status(
            args.state_root,
            args.stale_after_ms,
            mode=ProbeMode(args.mode),
        )
    except (OSError, TypeError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
