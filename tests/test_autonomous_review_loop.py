from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import autonomous_review_loop as review  # noqa: E402


def test_strategy_lab_review_uses_bounded_low_priority_defaults(monkeypatch):
    captured = {}

    def fake_run(cmd, timeout, *, nice_level=None):
        captured.update(cmd=cmd, timeout=timeout, nice_level=nice_level)
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(review, "run", fake_run)
    monkeypatch.delenv("AUTOREVIEW_PROFILE_LAB_MAX_BARS", raising=False)
    monkeypatch.delenv("AUTOREVIEW_PROFILE_LAB_TIMEOUT", raising=False)
    monkeypatch.delenv("AUTOREVIEW_PROFILE_LAB_NICE_LEVEL", raising=False)
    monkeypatch.setenv("AUTOREVIEW_HARVEST_FIRST", "false")

    result = review.strategy_lab_review(deploy_profile=False)

    assert captured["cmd"][-2:] == ["--max-bars", "50000"]
    assert captured["timeout"] == 1800
    assert captured["nice_level"] == 10
    assert result["returncode"] == 0
    assert result["timed_out"] is False
    assert result["max_bars"] == 50000


def test_strategy_lab_review_records_timeout_instead_of_raising(monkeypatch):
    def fake_run(cmd, timeout, *, nice_level=None):
        raise subprocess.TimeoutExpired(
            cmd,
            timeout,
            output=b"partial stdout",
            stderr=b"partial stderr",
        )

    monkeypatch.setattr(review, "run", fake_run)
    monkeypatch.setenv("AUTOREVIEW_HARVEST_FIRST", "false")
    monkeypatch.setenv("AUTOREVIEW_PROFILE_LAB_TIMEOUT", "90")

    result = review.strategy_lab_review(deploy_profile=False)

    assert result["returncode"] == 124
    assert result["timed_out"] is True
    assert result["timeout_seconds"] == 90
    assert result["stdout"] == "partial stdout"
    assert result["stderr"] == "partial stderr"


def test_env_int_clamps_and_falls_back(monkeypatch):
    monkeypatch.setenv("VALUE", "not-an-int")
    assert review._env_int("VALUE", 7, minimum=3, maximum=9) == 7

    monkeypatch.setenv("VALUE", "99")
    assert review._env_int("VALUE", 7, minimum=3, maximum=9) == 9
