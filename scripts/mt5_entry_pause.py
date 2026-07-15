#!/usr/bin/env python3
"""Manage the MT5 Common Files entry-pause flag."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from runtime_paths import common_dir as resolve_common_dir


FLAG_NAME = "aiquanttrader_entry_pause.flag"


def flag_path(common: Path) -> Path:
    return Path(common) / FLAG_NAME


def entries_paused(common: Path) -> bool:
    return flag_path(common).is_file()


def pause_entries(common: Path) -> Path:
    path = flag_path(common)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("entry_pause\n", encoding="ascii")
    temporary.replace(path)
    return path


def resume_entries(common: Path) -> Path:
    path = flag_path(common)
    path.unlink(missing_ok=True)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("pause", "resume", "status"))
    parser.add_argument("--common-dir", type=Path)
    args = parser.parse_args(argv)

    common = args.common_dir or resolve_common_dir()
    if common is None:
        print("[ERR] MT5 Common Files directory not found", file=sys.stderr)
        return 2

    if args.action == "pause":
        path = pause_entries(common)
    elif args.action == "resume":
        path = resume_entries(common)
    else:
        path = flag_path(common)

    state = "paused" if entries_paused(common) else "enabled"
    print(f"entry_trading={state} flag={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
