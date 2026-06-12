from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_report_run_cli_smoke(tmp_path):
    bars = tmp_path / "bars.tsv"
    bars.write_text(
        "time\topen\thigh\tlow\tclose\tvolume\n"
        "2026-01-01 00:00:00\t1\t2\t1\t2\t100\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [
            sys.executable,
            "scripts/report_run.py",
            "--bars",
            str(bars),
            "--run-id",
            "test-cli-smoke",
            "--output-dir",
            str(tmp_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    json_path = tmp_path / "test-cli-smoke.json"
    markdown_path = tmp_path / "test-cli-smoke.md"
    assert json_path.exists()
    assert markdown_path.exists()

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["metadata"]["run_id"] == "test-cli-smoke"
    assert payload["metadata"]["data_hash"]
    assert payload["metrics"]["n_trades"] == 0
    assert payload["walk_forward_stability"] is None
    assert payload["verdict"]["status"] in {"pass", "fail", "marginal"}
    assert "Verdict:" in proc.stdout
