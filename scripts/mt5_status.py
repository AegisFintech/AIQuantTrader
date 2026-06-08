#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from runtime_paths import WINEPREFIX, common_file_dirs


def latest_status_path() -> Path | None:
    for directory in common_file_dirs():
        path = directory / "finrobot_status.json"
        if path.exists():
            return path
    for path in WINEPREFIX.glob("**/finrobot_status.json"):
        return path
    return None


path = latest_status_path()
print("MT5 status file:", path or "not found")
if path:
    try:
        data = json.loads(path.read_text(errors="replace"))
        age = time.time() - path.stat().st_mtime
        print(json.dumps(data, indent=2))
        print(f"age_seconds={age:.1f}")
        common = path.parent
        for name in ("finrobot_positions.csv", "finrobot_deals.csv", "finrobot_acks.csv"):
            file_path = common / name
            if file_path.exists():
                file_age = time.time() - file_path.stat().st_mtime
                print(f"{name}: {file_path} ({file_path.stat().st_size} bytes, age={file_age:.1f}s)")
    except Exception as exc:
        print("read_error:", exc)

print("terminal_processes:")
cp = subprocess.run(["pgrep", "-af", "terminal64.exe|start_mt5|xvfb|wineserver"], text=True, capture_output=True)
print(cp.stdout.strip() or "none")
