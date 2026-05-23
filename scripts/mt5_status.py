#!/usr/bin/env python3
from __future__ import annotations
import json, os, time
from pathlib import Path

ROOT = Path('/home/openclaw/FinRobot')
COMMON_CANDIDATES = [
    Path('/home/openclaw/.wine-mt5/drive_c/users/openclaw/AppData/Roaming/MetaQuotes/Terminal/Common/Files'),
    Path('/home/openclaw/.wine-mt5/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/Common/Files'),
]

def latest_status_path() -> Path | None:
    for d in COMMON_CANDIDATES:
        p = d / 'finrobot_status.json'
        if p.exists(): return p
    for p in Path('/home/openclaw/.wine-mt5').glob('**/finrobot_status.json'):
        return p
    return None

p = latest_status_path()
print('MT5 status file:', p or 'not found')
if p:
    try:
        data = json.loads(p.read_text(errors='replace'))
        age = time.time() - p.stat().st_mtime
        print(json.dumps(data, indent=2))
        print(f'age_seconds={age:.1f}')
    except Exception as e:
        print('read_error:', e)
print('terminal_processes:')
os.system("pgrep -af 'terminal64.exe|start_mt5|xvfb|wineserver' || true")
