from __future__ import annotations

import json
from pathlib import Path

import pytest

from aiquanttrader import paper_healthcheck


def _write_status(state_root: Path, **updates: object) -> None:
    freshness: dict[str, object] = {
        "schema_version": 1,
        "checked_ts_ns": 10_000_000_000,
        "stale_after_ms": 5_000,
        "socket_connected": True,
        "public_frame_age_ms": 1_000,
        "asset_context_age_ms": 1_000,
        "market_state_age_ms": 1_000,
        "public_frame_fresh": True,
        "asset_context_fresh": True,
        "market_state_fresh": True,
        "ready": True,
        "blocking_reason": "none",
    }
    if updates.get("feed_connected") is False:
        freshness.update(
            socket_connected=False,
            ready=False,
            blocking_reason="socket_disconnected",
        )
    payload: dict[str, object] = {
        "schema_version": 2,
        "status": "ready",
        "run_id": "paper-health-probe-test",
        "heartbeat_ts_ns": 10_000_000_000,
        "feed_connected": True,
        "feature_ready": True,
        "operator_kill": False,
        "feed_freshness": freshness,
    }
    payload.update(updates)
    path = state_root / "paper" / "status.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_lightweight_probe_accepts_fresh_running_status(tmp_path: Path) -> None:
    _write_status(tmp_path)
    result, ready = paper_healthcheck.evaluate_status(
        tmp_path,
        5_000,
        now_ns=11_000_000_000,
    )
    assert ready
    assert result == {
        "status": "ready",
        "run_id": "paper-health-probe-test",
        "heartbeat_age_ms": 1_000.0,
        "feed_connected": True,
        "feed_blocking_reason": "none",
        "feed_socket_connected": True,
        "feed_component_fresh": {
            "public_frame": True,
            "asset_context": True,
            "market_state": True,
        },
        "feed_component_age_ms": {
            "public_frame": 1_000,
            "asset_context": 1_000,
            "market_state": 1_000,
        },
        "feed_stale_after_ms": 5_000,
        "feature_ready": True,
        "operator_kill": False,
    }


def test_health_heartbeat_threshold_is_independent_from_feed_threshold(tmp_path: Path) -> None:
    _write_status(tmp_path)
    path = tmp_path / "paper" / "status.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["feed_freshness"]["stale_after_ms"] = 1_500
    path.write_text(json.dumps(payload), encoding="utf-8")

    result, ready = paper_healthcheck.evaluate_status(
        tmp_path,
        5_000,
        now_ns=14_000_000_000,
    )

    assert ready
    assert result["heartbeat_age_ms"] == 4_000
    assert result["feed_stale_after_ms"] == 1_500


@pytest.mark.parametrize(
    ("updates", "now_ns"),
    [
        ({"status": "degraded"}, 11_000_000_000),
        ({"feed_connected": False}, 11_000_000_000),
        ({"operator_kill": True}, 11_000_000_000),
        ({}, 16_000_000_000),
        ({}, 9_000_000_000),
    ],
)
def test_lightweight_probe_fails_closed(
    tmp_path: Path,
    updates: dict[str, object],
    now_ns: int,
) -> None:
    _write_status(tmp_path, **updates)
    result, ready = paper_healthcheck.evaluate_status(tmp_path, 5_000, now_ns=now_ns)
    assert not ready
    assert result["status"] == "not_ready"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 1),
        ("status", "unknown"),
        ("run_id", ""),
        ("heartbeat_ts_ns", True),
        ("feed_connected", 1),
        ("feature_ready", "yes"),
        ("operator_kill", 0),
    ],
)
def test_lightweight_probe_rejects_invalid_contract_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    _write_status(tmp_path, **{field: value})
    with pytest.raises(ValueError):
        paper_healthcheck.evaluate_status(tmp_path, 5_000, now_ns=11_000_000_000)


def test_lightweight_probe_cli_reports_missing_status(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert paper_healthcheck.main(["--state-root", str(tmp_path)]) == 2
    error = json.loads(capsys.readouterr().err)
    assert error["status"] == "error"


@pytest.mark.parametrize(
    ("updates", "match"),
    (
        ({"schema_version": 2}, "schema_version must be 1"),
        ({"checked_ts_ns": 9}, "timestamp must match"),
        ({"stale_after_ms": 0}, "threshold must be positive"),
        ({"public_frame_age_ms": "old"}, "integer or null"),
        ({"public_frame_fresh": False}, "freshness must match"),
        ({"blocking_reason": "unknown"}, "unsupported blocking reason"),
        ({"ready": False}, "verdict must match"),
    ),
)
def test_lightweight_probe_rejects_invalid_feed_freshness(
    tmp_path: Path,
    updates: dict[str, object],
    match: str,
) -> None:
    _write_status(tmp_path)
    path = tmp_path / "paper" / "status.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["feed_freshness"].update(updates)
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=match):
        paper_healthcheck.evaluate_status(tmp_path, 5_000, now_ns=11_000_000_000)
