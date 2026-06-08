from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = Path(os.getenv("FINROBOT_RUNTIME_DIR", ROOT / ".runtime")).expanduser()
WINEPREFIX = Path(os.getenv("FINROBOT_WINEPREFIX", RUNTIME_DIR / "wineprefix")).expanduser()
MT5_DIR = Path(os.getenv("FINROBOT_MT5_DIR", RUNTIME_DIR / "mt5")).expanduser()
MT5_TERMINAL_DIR = MT5_DIR / "terminal" / "current"
MT5_TERMINAL = MT5_TERMINAL_DIR / "terminal64.exe"


def common_file_dirs() -> list[Path]:
    base = WINEPREFIX / "drive_c" / "users"
    candidates: list[Path] = []
    if base.exists():
        for user_dir in base.iterdir():
            candidates.append(user_dir / "AppData" / "Roaming" / "MetaQuotes" / "Terminal" / "Common" / "Files")
    return candidates


def common_dir() -> Path | None:
    for directory in common_file_dirs():
        if (directory / "finrobot_status.json").exists() or (directory / "finrobot_deals.csv").exists():
            return directory
    for path in WINEPREFIX.glob("**/finrobot_status.json"):
        return path.parent
    return None
