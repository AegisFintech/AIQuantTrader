#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aiquanttrader import data_store  # noqa: E402
from aiquanttrader.release_manifest import load_release_manifest  # noqa: E402
from mt5_trade_report import read_csv, read_json  # noqa: E402
from runtime_paths import common_dir  # noqa: E402


ACK_FIELDS = ("id", "time", "status", "message", "symbol", "side", "volume", "price")


def read_acks(path: Path) -> list[dict]:
    """Read live ack files with or without a header row."""
    if not path.exists() or not path.stat().st_size:
        return []
    with path.open(errors="replace", newline="") as fh:
        raw_rows = list(csv.reader(fh))
    if not raw_rows:
        return []
    header = [cell.strip() for cell in raw_rows[0]]
    lower = [cell.lower() for cell in header]
    if "status" in lower and (lower[0:1] == ["id"] or lower[0:1] == ["command_id"]):
        return [dict(zip(header, row)) for row in raw_rows[1:]]
    return [dict(zip(ACK_FIELDS, row)) for row in raw_rows]


def ingest_common_files(common: Path, warehouse: Path | None = None) -> dict:
    """Ingest the current MT5 Common Files snapshot into DuckDB."""
    con = data_store.connect(warehouse)
    try:
        data_store.init_schema(con)
        status = read_json(common / "aiquanttrader_status.json")
        positions = read_csv(common / "aiquanttrader_positions.csv")
        deals = read_csv(common / "aiquanttrader_deals.csv")
        acks = read_acks(common / "aiquanttrader_acks.csv")
        manifest = load_release_manifest()
        # Prefer the live aiquanttrader_status.json over the static release manifest.
        # The release manifest is generated from current HEAD, but the deployed .ex5
        # was compiled at a (possibly older) commit; status.json reflects what's
        # actually running. Fall back to the manifest only when status lacks these
        # fields (e.g. v1.30 or earlier which predate the manifest reader).
        ea_version = status.get("ea_version") or manifest.get("ea_version") or ""
        git_sha = status.get("git_sha") or manifest.get("git_sha") or ""
        inserted = {
            "status": data_store.ingest_status(con, status, ea_version, git_sha),
            "positions": data_store.ingest_positions(
                con, positions, ts_server=status.get("ts"), ea_version=ea_version, git_sha=git_sha
            ),
            "deals": data_store.ingest_deals(con, deals, ea_version, git_sha),
            "acks": data_store.ingest_acks(con, acks, ea_version, git_sha),
        }
        return {
            "inserted": inserted,
            "summary": data_store.query_summary(con),
            "warehouse": str((warehouse or data_store.DEFAULT_WAREHOUSE).resolve()),
        }
    finally:
        con.close()


def display_path(path: str) -> str:
    """Return a repo-relative path when possible."""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(data_store.ROOT))
    except ValueError:
        return str(resolved)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest MT5 Common Files into DuckDB.")
    parser.add_argument("--warehouse", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        common = common_dir()
        if common is None:
            print("[ERR] MT5 Common Files dir not found", file=sys.stderr)
            return 2
        result = ingest_common_files(common, args.warehouse)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            summary = result["summary"]
            for table in ("status", "positions", "deals", "acks"):
                inserted = result["inserted"][table]
                print(f"[OK] {table}: inserted={inserted} total={summary[table]}")
            print(f"warehouse: {display_path(result['warehouse'])}")
        return 0
    except Exception as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
