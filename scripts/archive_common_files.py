#!/usr/bin/env python3
"""Archive the MT5 Common Files snapshot to state/mt5/archive/YYYY-MM-DD/HHMMSS/.

Each run copies:
  - finrobot_status.json
  - finrobot_positions.csv
  - finrobot_deals.csv
  - finrobot_acks.csv

from the live Common Files directory into a timestamped sub-directory under
`state/mt5/archive/`. Files that do not exist (or are empty) are skipped
without erroring, so this is safe to run even when the EA is not running.

Exit code:
  0  - archive succeeded (at least one file was copied, or nothing to archive)
  2  - could not locate Common Files directory

Cron recommendation (run once per day, e.g. 23:55 local):
  55 23 * * *  cd /root/FinRobot && .venv/bin/python scripts/archive_common_files.py
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from runtime_paths import common_dir  # noqa: E402

STATE_DIR = ROOT / "state" / "mt5" / "archive"
FILES = ("finrobot_status.json", "finrobot_positions.csv", "finrobot_deals.csv", "finrobot_acks.csv")


def archive_now(common: Path | None = None) -> Path:
    if common is None:
        common = common_dir()
    if common is None:
        raise SystemExit(2)
    ts = time.strftime("%Y-%m-%d/%H%M%S")
    target = STATE_DIR / ts
    target.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in FILES:
        src = common / name
        if not src.exists() or not src.stat().st_size:
            continue
        shutil.copy2(src, target / name)
        copied.append(name)
    if not copied:
        # Leave the empty dir so the operator can see we tried.
        (target / "EMPTY").write_text(
            f"archive at {ts} found no Common Files at {common}\n"
        )
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    args = parser.parse_args()
    try:
        target = archive_now()
    except SystemExit as exc:
        print(f"archive_failed code={exc.code} (no common dir)", file=sys.stderr)
        return int(exc.code)
    print(f"archive_ok -> {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
