from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from mt5_watchdog import should_restart, status_age_seconds  # noqa: E402


def test_status_age_seconds_missing_status_returns_none(tmp_path):
    assert status_age_seconds(now=time.time(), common=tmp_path) is None


def test_status_age_seconds_reads_mtime(tmp_path):
    status = tmp_path / "aiquanttrader_status.json"
    status.write_text("{}")
    now = time.time()
    old = now - 45
    os.utime(status, (old, old))

    assert status_age_seconds(now=now, common=tmp_path) == 45


def test_should_restart_only_when_stale_and_cooldown_elapsed():
    now = 1000.0

    assert not should_restart(
        30,
        stale_seconds=180,
        last_restart_at=None,
        restart_cooldown_seconds=300,
        now=now,
    )
    assert should_restart(
        181,
        stale_seconds=180,
        last_restart_at=None,
        restart_cooldown_seconds=300,
        now=now,
    )
    assert not should_restart(
        181,
        stale_seconds=180,
        last_restart_at=900,
        restart_cooldown_seconds=300,
        now=now,
    )
    assert should_restart(
        181,
        stale_seconds=180,
        last_restart_at=699,
        restart_cooldown_seconds=300,
        now=now,
    )
