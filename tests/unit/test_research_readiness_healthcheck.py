from __future__ import annotations

import json
from pathlib import Path

import pytest

from aiquanttrader.research_readiness_healthcheck import evaluate_status, main


def _write_state(root: Path, *, status: str = "running", heartbeat_ns: int = 1_000) -> None:
    path = root / "research" / "data-readiness.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "status": status,
                "heartbeat_ts_ns": heartbeat_ns,
                "report": {"schema_version": 2, "ready_for_horizon_audit": False},
                "last_error_code": None,
            }
        ),
        encoding="utf-8",
    )


def test_healthcheck_separates_monitor_health_from_data_readiness(tmp_path: Path) -> None:
    _write_state(tmp_path)

    result, healthy = evaluate_status(tmp_path, 1, now_ns=1_500)

    assert healthy
    assert result["status"] == "healthy"
    assert result["data_ready"] is False


def test_healthcheck_rejects_a_stale_monitor(tmp_path: Path) -> None:
    _write_state(tmp_path)

    result, healthy = evaluate_status(tmp_path, 1, now_ns=2_000_000_000)

    assert not healthy
    assert result["status"] == "unhealthy"


def test_healthcheck_cli_fails_closed_on_invalid_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--state-root", str(tmp_path)]) == 2
    assert '"status": "error"' in capsys.readouterr().err


@pytest.mark.parametrize(
    ("payload", "match"),
    (
        ([], "JSON object"),
        ({"schema_version": 1, "status": "running", "heartbeat_ts_ns": 1}, "must be 2"),
        ({"schema_version": 2, "status": "unknown", "heartbeat_ts_ns": 1}, "unsupported"),
        (
            {
                "schema_version": 2,
                "status": "running",
                "heartbeat_ts_ns": 1,
                "report": "invalid",
            },
            "object or null",
        ),
        (
            {
                "schema_version": 2,
                "status": "running",
                "heartbeat_ts_ns": 1,
                "report": {"schema_version": 2},
            },
            "verdict must be a boolean",
        ),
        (
            {
                "schema_version": 2,
                "status": "running",
                "heartbeat_ts_ns": 1,
                "report": {"schema_version": 1, "ready_for_horizon_audit": False},
            },
            "report schema_version must be 2",
        ),
    ),
)
def test_healthcheck_rejects_malformed_state(tmp_path: Path, payload: object, match: str) -> None:
    path = tmp_path / "research" / "data-readiness.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match=match):
        evaluate_status(tmp_path, 1, now_ns=1)


def test_healthcheck_validates_threshold_and_cli_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_state(tmp_path, heartbeat_ns=1)
    with pytest.raises(ValueError, match="must be positive"):
        evaluate_status(tmp_path, 0, now_ns=1)

    assert main(["--state-root", str(tmp_path), "--stale-after-seconds", "1"]) == 1
    assert '"status": "unhealthy"' in capsys.readouterr().out
