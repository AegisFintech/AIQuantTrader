from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from finrobot.data_store import connect
from finrobot.prices import ingest_bars


ROOT = Path(__file__).resolve().parents[1]


def test_xau_strategy_lab_cli_smoke(tmp_path):
    data_path = tmp_path / "bars.duckdb"
    registry_path = tmp_path / "registry.duckdb"
    output_dir = tmp_path / "profile_lab"
    experiment_dir = tmp_path / "experiments"
    with connect(data_path) as con:
        ingest_bars(con, "XAUUSD", _bars(360))

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/xau_strategy_lab.py",
            "--candidate",
            "incumbent_smc4",
            "--candidate",
            "attack_atr_m1",
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
            "--data-source",
            str(data_path),
            "--registry",
            str(registry_path),
            "--output-dir",
            str(output_dir),
            "--experiment-dir",
            str(experiment_dir),
            "--run-id",
            "lab-smoke",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    report_path = output_dir / "lab-smoke.json"
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["winner"]["profile"]["profile_name"] in {
        "incumbent_smc4",
        "attack_atr_m1",
    }
    assert "recent_total_pnl" in payload["winner"]
    assert "recent_profit_factor" in payload["winner"]
    assert "incumbent_delta_pnl" in payload["winner"]
    assert payload["backtest_defaults"]["min_challenger_pnl_delta"] == 250.0
    assert len(payload["candidates"]) == 2
    assert (experiment_dir / "lab-smoke-incumbent_smc4.json").exists()


def _bars(count: int) -> list[dict]:
    bars = []
    for idx in range(count):
        wave = ((idx % 20) - 10) * 0.9
        close = 4100.0 + wave + idx * 0.02
        bars.append(
            {
                "time": 1_700_000_000 + idx * 60,
                "open": close - 0.35,
                "high": close + 1.4,
                "low": close - 1.4,
                "close": close,
                "volume": 100.0,
                "source": "test",
            }
        )
    return bars
