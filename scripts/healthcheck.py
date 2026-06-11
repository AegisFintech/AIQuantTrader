#!/usr/bin/env python3
"""FinRobot healthcheck: surface stale heartbeats, missing files, and risk breaches.

Exits non-zero if any check fails so it can be wired into cron/systemd timers
or a simple alert. Designed to be importable so tests can call `check()` directly
without going through the CLI.

Checks (all read-only):
  1. Common Files directory exists and contains a fresh finrobot_status.json.
  2. finrobot_positions.csv exists; no managed position is missing both SL and TP
     unless the EA's auto-close-no-sl-tp safety is enabled.
  3. money_management.loss_limit_reached is 0 in finrobot_status.json.

Usage:
  python3 scripts/healthcheck.py
  python3 scripts/healthcheck.py --heartbeat-stale-seconds 30
  python3 scripts/healthcheck.py --json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from runtime_paths import common_dir  # noqa: E402

DEFAULT_HEARTBEAT_STALE_SECONDS = 60
DEFAULT_PM2_STALE_RESTARTS = 20  # mirror ecosystem.config.js max_restarts


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    extra: dict = field(default_factory=dict)


def _read_status(common: Path) -> dict:
    path = common / "finrobot_status.json"
    if not path.exists() or not path.stat().st_size:
        return {}
    try:
        return json.loads(path.read_text(errors="replace"))
    except Exception:
        return {}


def _read_positions(common: Path) -> list[dict]:
    path = common / "finrobot_positions.csv"
    if not path.exists() or not path.stat().st_size:
        return []
    import csv

    with path.open(errors="replace", newline="") as fh:
        return list(csv.DictReader(fh))


def check_heartbeat(common: Path, stale_seconds: int) -> CheckResult:
    status_path = common / "finrobot_status.json"
    if not status_path.exists():
        return CheckResult(
            name="heartbeat_present",
            ok=False,
            detail=f"finrobot_status.json missing at {status_path}",
        )
    age = time.time() - status_path.stat().st_mtime
    if age > stale_seconds:
        return CheckResult(
            name="heartbeat_fresh",
            ok=False,
            detail=f"finrobot_status.json is {age:.1f}s old (>{stale_seconds}s)",
            extra={"age_seconds": round(age, 1)},
        )
    return CheckResult(
        name="heartbeat_fresh",
        ok=True,
        detail=f"finrobot_status.json age {age:.1f}s",
        extra={"age_seconds": round(age, 1)},
    )


def check_loss_limit(status: dict) -> CheckResult:
    mm = status.get("money_management") or {}
    reached = int(mm.get("loss_limit_reached", 0) or 0)
    if reached:
        return CheckResult(
            name="daily_loss_limit",
            ok=False,
            detail="EA reports loss_limit_reached=1; new auto orders blocked",
            extra={"money_management": mm},
        )
    return CheckResult(
        name="daily_loss_limit",
        ok=True,
        detail=f"loss_limit_reached=0 (today_closed_pnl={mm.get('today_closed_pnl', '?')})",
    )


def check_unprotected_positions(common: Path, status: dict) -> CheckResult:
    positions = _read_positions(common)
    auto_close_enabled = bool(
        (status.get("money_management") or {}).get("auto_close_no_sl_tp", 0)
    )
    bad: list[dict] = []
    for p in positions:
        try:
            sl = float(p.get("sl") or 0.0)
            tp = float(p.get("tp") or 0.0)
        except (TypeError, ValueError):
            sl = tp = 0.0
        if sl <= 0.0 or tp <= 0.0:
            bad.append(
                {
                    "ticket": p.get("ticket"),
                    "symbol": p.get("symbol"),
                    "type": p.get("type"),
                    "sl": sl,
                    "tp": tp,
                }
            )
    if bad and not auto_close_enabled:
        return CheckResult(
            name="unprotected_positions",
            ok=False,
            detail=(
                f"{len(bad)} managed position(s) missing SL or TP and "
                "auto_close_no_sl_tp is disabled"
            ),
            extra={"positions": bad, "auto_close_no_sl_tp": False},
        )
    if bad and auto_close_enabled:
        return CheckResult(
            name="unprotected_positions",
            ok=True,
            detail=(
                f"{len(bad)} managed position(s) missing SL/TP; "
                "auto_close_no_sl_tp is enabled, EA will close them"
            ),
            extra={"positions": bad, "auto_close_no_sl_tp": True},
        )
    return CheckResult(
        name="unprotected_positions",
        ok=True,
        detail="0 unmanaged managed positions",
        extra={"count": len(positions)},
    )


def check_pm2(process_name: str = "mt5-terminal") -> CheckResult:
    try:
        proc = subprocess.run(
            ["pm2", "jlist"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except FileNotFoundError:
        return CheckResult(
            name="pm2_installed",
            ok=False,
            detail="pm2 not found on PATH",
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="pm2_responsive",
            ok=False,
            detail="pm2 jlist timed out after 5s",
        )
    if proc.returncode != 0:
        return CheckResult(
            name="pm2_responsive",
            ok=False,
            detail=f"pm2 jlist exit={proc.returncode} stderr={proc.stderr.strip()[:200]}",
        )
    try:
        apps = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        return CheckResult(
            name="pm2_parse",
            ok=False,
            detail=f"pm2 jlist returned non-JSON: {exc}",
        )
    match = next((a for a in apps if a.get("name") == process_name), None)
    if not match:
        return CheckResult(
            name=f"pm2_{process_name}_present",
            ok=False,
            detail=f"pm2 process '{process_name}' not found",
        )
    status = match.get("pm2_env", {}).get("status")
    restarts = int(match.get("pm2_env", {}).get("restart_time", 0) or 0)
    if status != "online":
        return CheckResult(
            name=f"pm2_{process_name}_online",
            ok=False,
            detail=f"pm2 process '{process_name}' status={status!r}",
            extra={"status": status, "restarts": restarts},
        )
    if restarts >= DEFAULT_PM2_STALE_RESTARTS:
        return CheckResult(
            name=f"pm2_{process_name}_stable",
            ok=False,
            detail=(
                f"pm2 process '{process_name}' has restarted {restarts} times "
                f"(>= {DEFAULT_PM2_STALE_RESTARTS}); likely crash loop"
            ),
            extra={"status": status, "restarts": restarts},
        )
    return CheckResult(
        name=f"pm2_{process_name}_online",
        ok=True,
        detail=f"pm2 process '{process_name}' online, restarts={restarts}",
        extra={"status": status, "restarts": restarts},
    )


def check_all(
    common: Optional[Path] = None,
    heartbeat_stale_seconds: int = DEFAULT_HEARTBEAT_STALE_SECONDS,
    pm2_process: str = "mt5-terminal",
) -> list[CheckResult]:
    results: list[CheckResult] = []
    if common is None:
        common = common_dir()
    if common is None:
        results.append(
            CheckResult(
                name="common_dir_present",
                ok=False,
                detail="Could not locate MT5 Common Files directory",
            )
        )
        return results
    results.append(CheckResult(name="common_dir_present", ok=True, detail=str(common)))
    results.append(check_heartbeat(common, heartbeat_stale_seconds))
    status = _read_status(common)
    results.append(check_loss_limit(status))
    results.append(check_unprotected_positions(common, status))
    results.append(check_pm2(pm2_process))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--heartbeat-stale-seconds",
        type=int,
        default=DEFAULT_HEARTBEAT_STALE_SECONDS,
        help="Max age in seconds for finrobot_status.json to be considered fresh",
    )
    parser.add_argument(
        "--pm2-process",
        default="mt5-terminal",
        help="PM2 process name to check (default: mt5-terminal)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print results as JSON",
    )
    args = parser.parse_args()

    results = check_all(
        heartbeat_stale_seconds=args.heartbeat_stale_seconds,
        pm2_process=args.pm2_process,
    )
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "name": r.name,
                        "ok": r.ok,
                        "detail": r.detail,
                        "extra": r.extra,
                    }
                    for r in results
                ],
                indent=2,
            )
        )
    else:
        for r in results:
            marker = "OK  " if r.ok else "FAIL"
            print(f"[{marker}] {r.name}: {r.detail}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
