from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import mt5_minute_cycle  # noqa: E402


def test_minute_cycle_runs_ingestion_then_snapshot():
    commands = []

    def runner(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "ok\n", "")

    assert mt5_minute_cycle.run_cycle(runner=runner) == 0
    assert [command[-1] for command in commands] == list(
        mt5_minute_cycle.MINUTE_SCRIPTS
    )


def test_minute_cycle_stops_after_failure():
    commands = []

    def runner(cmd, **kwargs):
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 3, "", "failed\n")

    assert mt5_minute_cycle.run_cycle(runner=runner) == 3
    assert len(commands) == 1


def test_minute_cycle_returns_timeout_status():
    def runner(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    assert mt5_minute_cycle.run_cycle(runner=runner) == 124
