from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from finrobot.data_store import connect
from finrobot.prices import ingest_bars


ROOT = Path(__file__).resolve().parents[1]


def test_run_walkforward_cli_smoke(tmp_path):
    data_path = tmp_path / "bars.duckdb"
    registry_path = tmp_path / "registry.duckdb"
    with connect(data_path) as con:
        ingest_bars(con, "XAUUSD", _bars(240))

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/run_walkforward.py",
            "--strategy",
            "XauAtrImpulse",
            "--symbol",
            "XAUUSD",
            "--folds",
            "2",
            "--purge-bars",
            "10",
            "--embargo-bars",
            "10",
            "--min-train-bars",
            "10",
            "--min-test-bars",
            "10",
            "--run-id",
            "m3-cli-smoke",
            "--output-dir",
            str(tmp_path),
            "--registry",
            str(registry_path),
            "--data-source",
            str(data_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    json_path = tmp_path / "m3-cli-smoke.json"
    assert json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "m3-cli-smoke"
    assert payload["git_sha"]
    assert payload["data_hash"]
    assert payload["config"]["strategy_config_diff"] == {}

    with connect(registry_path) as con:
        rows = con.execute(
            "SELECT run_id, strategy, symbol FROM experiments WHERE run_id = ?",
            ["m3-cli-smoke"],
        ).fetchall()

    assert rows == [("m3-cli-smoke", "XauAtrImpulse", "XAUUSD")]
    assert "Walk-forward summary" in proc.stdout


def _bars(count: int) -> list[dict]:
    bars = []
    for idx in range(count):
        close = 4100.0 + ((idx % 12) - 6) * 0.8 + idx * 0.01
        bars.append(
            {
                "time": 1_700_000_000 + idx * 60,
                "open": close - 0.2,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 100.0,
                "source": "test",
            }
        )
    return bars
