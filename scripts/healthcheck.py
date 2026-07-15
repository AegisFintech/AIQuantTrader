#!/usr/bin/env python3
"""AIQuantTrader healthcheck: surface stale heartbeats, missing files, and risk breaches.

Exits non-zero if any check fails so it can be wired into cron/systemd timers
or a simple alert. Designed to be importable so tests can call `check()` directly
without going through the CLI.

Checks (all read-only):
  1. Common Files directory exists and contains a fresh aiquanttrader_status.json.
  2. aiquanttrader_positions.csv exists; no managed position is missing both SL and TP
     unless the EA's auto-close-no-sl-tp safety is enabled.
  3. money_management.loss_limit_reached is 0 in aiquanttrader_status.json.
  4. Repository disk usage stays below the configured ceiling.
  5. Autonomous research has a recent successful strategy-lab record.
  6. All active PM2 services are online and below the restart threshold.

Usage:
  python3 scripts/healthcheck.py
  python3 scripts/healthcheck.py --heartbeat-stale-seconds 30
  python3 scripts/healthcheck.py --json
"""
from __future__ import annotations

import argparse
import json
import shutil
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
DEFAULT_DISK_MAX_USED_PERCENT = 85.0
DEFAULT_RESEARCH_MAX_AGE_HOURS = 14.0
DEFAULT_PM2_PROCESSES = (
    "aiquanttrader-mt5",
    "aiquanttrader-watchdog",
    "aiquanttrader-review",
    "aiquanttrader-dashboard",
)
DEFAULT_RESEARCH_JOURNAL = ROOT / "state" / "mt5" / "improver_journal.jsonl"


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    extra: dict = field(default_factory=dict)


def _read_status(common: Path) -> dict:
    path = common / "aiquanttrader_status.json"
    if not path.exists() or not path.stat().st_size:
        return {}
    try:
        return json.loads(path.read_text(errors="replace"))
    except Exception:
        return {}


def _read_positions(common: Path) -> list[dict]:
    path = common / "aiquanttrader_positions.csv"
    if not path.exists() or not path.stat().st_size:
        return []
    import csv

    with path.open(errors="replace", newline="") as fh:
        return list(csv.DictReader(fh))


def check_heartbeat(common: Path, stale_seconds: int) -> CheckResult:
    status_path = common / "aiquanttrader_status.json"
    if not status_path.exists():
        return CheckResult(
            name="heartbeat_present",
            ok=False,
            detail=f"aiquanttrader_status.json missing at {status_path}",
        )
    age = time.time() - status_path.stat().st_mtime
    if age > stale_seconds:
        return CheckResult(
            name="heartbeat_fresh",
            ok=False,
            detail=f"aiquanttrader_status.json is {age:.1f}s old (>{stale_seconds}s)",
            extra={"age_seconds": round(age, 1)},
        )
    return CheckResult(
        name="heartbeat_fresh",
        ok=True,
        detail=f"aiquanttrader_status.json age {age:.1f}s",
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


def check_disk_usage(
    path: Path = ROOT,
    max_used_percent: float = DEFAULT_DISK_MAX_USED_PERCENT,
) -> CheckResult:
    usage = shutil.disk_usage(path)
    used_percent = 0.0 if usage.total <= 0 else usage.used / usage.total * 100.0
    free_gib = usage.free / (1024**3)
    ok = used_percent < float(max_used_percent)
    return CheckResult(
        name="disk_usage",
        ok=ok,
        detail=(
            f"{path} used={used_percent:.1f}% free={free_gib:.2f}GiB "
            f"limit={float(max_used_percent):.1f}%"
        ),
        extra={
            "path": str(path),
            "used_percent": round(used_percent, 2),
            "free_gib": round(free_gib, 3),
            "max_used_percent": float(max_used_percent),
        },
    )


def check_research_freshness(
    journal: Path = DEFAULT_RESEARCH_JOURNAL,
    max_age_hours: float = DEFAULT_RESEARCH_MAX_AGE_HOURS,
) -> CheckResult:
    if not journal.exists() or not journal.stat().st_size:
        return CheckResult(
            name="research_cycle_pending",
            ok=True,
            detail=f"no autonomous research journal yet at {journal}",
        )

    last_record: dict | None = None
    for raw_line in reversed(journal.read_text(errors="replace").splitlines()):
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if record.get("event") == "autonomous_strategy_lab":
            last_record = record
            break
    if last_record is None:
        return CheckResult(
            name="research_cycle_present",
            ok=False,
            detail=f"no autonomous_strategy_lab record in {journal}",
        )

    timestamp = float(last_record.get("ts") or 0.0)
    age_hours = max(0.0, time.time() - timestamp) / 3600.0
    result = last_record.get("result") or {}
    if result.get("enabled") is False:
        return CheckResult(
            name="research_cycle_disabled",
            ok=True,
            detail=f"profile lab disabled; last record age={age_hours:.2f}h",
        )
    returncode = result.get("returncode")
    timed_out = bool(result.get("timed_out")) or returncode == 124
    fresh = age_hours <= float(max_age_hours)
    succeeded = returncode == 0 and not timed_out
    if not fresh:
        detail = (
            f"last strategy lab is {age_hours:.2f}h old "
            f"(>{float(max_age_hours):.2f}h)"
        )
    elif timed_out:
        detail = (
            "last strategy lab timed out "
            f"after {result.get('duration_seconds', result.get('timeout_seconds', '?'))}s"
        )
    elif not succeeded:
        detail = f"last strategy lab failed returncode={returncode!r}"
    else:
        detail = f"last strategy lab succeeded age={age_hours:.2f}h"
    return CheckResult(
        name="research_cycle_fresh",
        ok=fresh and succeeded,
        detail=detail,
        extra={
            "age_hours": round(age_hours, 3),
            "max_age_hours": float(max_age_hours),
            "returncode": returncode,
            "timed_out": timed_out,
        },
    )


def check_pm2(process_name: str = "aiquanttrader-mt5") -> CheckResult:
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
    pm2_process: str | None = None,
    disk_path: Path = ROOT,
    disk_max_used_percent: float = DEFAULT_DISK_MAX_USED_PERCENT,
    research_journal: Path = DEFAULT_RESEARCH_JOURNAL,
    research_max_age_hours: float = DEFAULT_RESEARCH_MAX_AGE_HOURS,
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
    results.append(check_disk_usage(disk_path, disk_max_used_percent))
    results.append(
        check_research_freshness(research_journal, research_max_age_hours)
    )
    pm2_processes = (pm2_process,) if pm2_process else DEFAULT_PM2_PROCESSES
    results.extend(check_pm2(name) for name in pm2_processes)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--heartbeat-stale-seconds",
        type=int,
        default=DEFAULT_HEARTBEAT_STALE_SECONDS,
        help="Max age in seconds for aiquanttrader_status.json to be considered fresh",
    )
    parser.add_argument(
        "--pm2-process",
        default="",
        help="Check only this PM2 process instead of all active AIQuantTrader services",
    )
    parser.add_argument(
        "--disk-max-used-percent",
        type=float,
        default=DEFAULT_DISK_MAX_USED_PERCENT,
        help="Fail when repository filesystem usage reaches this percentage",
    )
    parser.add_argument(
        "--research-max-age-hours",
        type=float,
        default=DEFAULT_RESEARCH_MAX_AGE_HOURS,
        help="Fail when the latest strategy-lab record is older than this",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print results as JSON",
    )
    args = parser.parse_args()

    results = check_all(
        heartbeat_stale_seconds=args.heartbeat_stale_seconds,
        pm2_process=args.pm2_process or None,
        disk_max_used_percent=args.disk_max_used_percent,
        research_max_age_hours=args.research_max_age_hours,
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
