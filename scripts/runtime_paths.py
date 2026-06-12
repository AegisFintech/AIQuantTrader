from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def env_path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    path = Path(value).expanduser() if value else default
    if not path.is_absolute():
        path = ROOT / path
    return path


RUNTIME_DIR = env_path("FINROBOT_RUNTIME_DIR", ROOT / ".runtime")
WINEPREFIX = env_path("FINROBOT_WINEPREFIX", RUNTIME_DIR / "wineprefix")
MT5_DIR = env_path("FINROBOT_MT5_DIR", RUNTIME_DIR / "mt5")
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
    override = os.getenv("FINROBOT_COMMON_DIR")
    if override:
        directory = env_path("FINROBOT_COMMON_DIR", ROOT)
        return directory if directory.is_dir() else None

    for directory in common_file_dirs():
        if (directory / "finrobot_status.json").exists() or (directory / "finrobot_deals.csv").exists():
            return directory
    for path in WINEPREFIX.glob("**/finrobot_status.json"):
        return path.parent
    return None
