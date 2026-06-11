#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from finrobot import data_store, prices  # noqa: E402
from runtime_paths import common_dir as default_common_dir  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Snapshot live MT5 bid/ask prices into DuckDB.")
    parser.add_argument("--warehouse", type=Path, default=_default_warehouse())
    parser.add_argument("--common-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        common = args.common_dir or default_common_dir()
        snapshots = prices.load_status_bidask(common) if common is not None else []
        if args.dry_run:
            inserted = 0
        else:
            con = data_store.connect(args.warehouse)
            try:
                prices.init_prices_schema(con)
                inserted = prices.ingest_bidask_snapshots(con, snapshots)
            finally:
                con.close()
        symbols = ", ".join(snapshot["symbol"] for snapshot in snapshots)
        print(f"[OK] snapshot: inserted={inserted} symbols=[{symbols}]")
        return 0
    except Exception as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1


def _default_warehouse() -> Path:
    value = os.getenv("FINROBOT_WAREHOUSE")
    return Path(value) if value else data_store.DEFAULT_WAREHOUSE


if __name__ == "__main__":
    raise SystemExit(main())
