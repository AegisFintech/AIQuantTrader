#!/usr/bin/env python3
"""Restart MT5 through PM2 when the bridge heartbeat goes stale."""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from runtime_paths import common_dir  # noqa: E402

DEFAULT_CHECK_INTERVAL_SECONDS = 30
DEFAULT_STALE_SECONDS = 180
DEFAULT_RESTART_COOLDOWN_SECONDS = 300


def status_age_seconds(now: float | None = None, common: Path | None = None) -> float | None:
    directory = common if common is not None else common_dir()
    if directory is None:
        return None
    status_path = directory / "aiquanttrader_status.json"
    if not status_path.exists():
        return None
    return (time.time() if now is None else now) - status_path.stat().st_mtime


def should_restart(
    age: float | None,
    *,
    stale_seconds: int,
    last_restart_at: float | None,
    restart_cooldown_seconds: int,
    now: float,
) -> bool:
    if age is None or age <= stale_seconds:
        return False
    if last_restart_at is None:
        return True
    return now - last_restart_at >= restart_cooldown_seconds


def restart_mt5(process_name: str) -> int:
    proc = subprocess.run(
        ["pm2", "restart", process_name, "--update-env"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    if proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.stderr.strip():
        print(proc.stderr.strip(), file=sys.stderr)
    return proc.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--process-name", default="aiquanttrader-mt5")
    parser.add_argument(
        "--check-interval-seconds",
        type=int,
        default=DEFAULT_CHECK_INTERVAL_SECONDS,
    )
    parser.add_argument("--stale-seconds", type=int, default=DEFAULT_STALE_SECONDS)
    parser.add_argument(
        "--restart-cooldown-seconds",
        type=int,
        default=DEFAULT_RESTART_COOLDOWN_SECONDS,
    )
    parser.add_argument("--once", action="store_true", help="Run one check and exit")
    args = parser.parse_args()

    last_restart_at: float | None = None
    check_interval = max(5, args.check_interval_seconds)
    stale_seconds = max(10, args.stale_seconds)
    cooldown = max(stale_seconds, args.restart_cooldown_seconds)

    print(
        "mt5_watchdog started "
        f"process={args.process_name} stale_seconds={stale_seconds} "
        f"check_interval_seconds={check_interval} cooldown_seconds={cooldown}",
        flush=True,
    )
    missing_since: float | None = None
    while True:
        now = time.time()
        age = status_age_seconds(now=now)
        if age is None:
            if missing_since is None:
                missing_since = now
            effective_age = now - missing_since
        else:
            missing_since = None
            effective_age = age
        if should_restart(
            effective_age,
            stale_seconds=stale_seconds,
            last_restart_at=last_restart_at,
            restart_cooldown_seconds=cooldown,
            now=now,
        ):
            detail = "missing" if age is None else f"{age:.1f}s old"
            print(f"mt5_watchdog restarting {args.process_name}: heartbeat {detail}", flush=True)
            rc = restart_mt5(args.process_name)
            if rc == 0:
                last_restart_at = now
            else:
                print(f"mt5_watchdog restart failed exit={rc}", file=sys.stderr, flush=True)
        elif age is not None:
            print(f"mt5_watchdog heartbeat age={age:.1f}s", flush=True)
        else:
            print(f"mt5_watchdog heartbeat missing for {effective_age:.1f}s", flush=True)

        if args.once:
            return 0
        time.sleep(check_interval)


if __name__ == "__main__":
    raise SystemExit(main())
