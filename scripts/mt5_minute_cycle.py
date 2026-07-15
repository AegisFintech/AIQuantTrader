#!/usr/bin/env python3
"""Run the one-minute DuckDB ingestion jobs sequentially."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
MINUTE_SCRIPTS = (
    "scripts/mt5_ingest_common_files.py",
    "scripts/mt5_snapshot_prices.py",
)


def run_cycle(
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> int:
    python = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    for script in MINUTE_SCRIPTS:
        try:
            completed = runner(
                [python, script],
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=45,
                check=False,
            )
        except subprocess.TimeoutExpired:
            print(f"[ERR] minute cycle timeout: {script}", file=sys.stderr)
            return 124
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        if completed.returncode != 0:
            print(
                f"[ERR] minute cycle stopped: {script} exit={completed.returncode}",
                file=sys.stderr,
            )
            return int(completed.returncode)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cycle())
