from __future__ import annotations

import json
from pathlib import Path

import pytest

from aiquanttrader import paper_healthcheck


def _write_status(state_root: Path, **updates: object) -> None:
    payload: dict[str, object] = {
        "schema_version": 1,
        "status": "ready",
        "run_id": "paper-health-probe-test",
        "heartbeat_ts_ns": 10_000_000_000,
        "feed_connected": True,
        "feature_ready": True,
        "operator_kill": False,
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
        "feature_ready": True,
        "operator_kill": False,
    }


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
        ("schema_version", 2),
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
