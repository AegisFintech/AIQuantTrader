#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finrobot import data_store, prices  # noqa: E402


DEFAULT_SYMBOLS = "XAUUSD,BTCUSD"
PRICE_FILE_RE = re.compile(r"^([A-Za-z0-9]+?)(?:1|_M1)\.csv$")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load historical OHLCV prices into DuckDB.")
    parser.add_argument("--data-dir", type=Path, default=_default_data_dir())
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    parser.add_argument("--warehouse", type=Path, default=_default_warehouse())
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    try:
        files = _matching_files(args.data_dir, _symbols(args.symbols))
        if not args.dry_run:
            con = data_store.connect(args.warehouse)
            prices.init_prices_schema(con)
        else:
            con = None
        try:
            for symbol, paths in files:
                if not paths:
                    print(f"[skip] no file found for {symbol}")
                    continue
                for path in paths:
                    parsed_symbol = parse_symbol(path)
                    bars = list(prices.load_tsv_bars(path))
                    if args.dry_run:
                        inserted = 0
                    else:
                        assert con is not None
                        inserted = prices.ingest_bars(con, parsed_symbol, bars)
                    skipped = len(bars) - inserted
                    suffix = " dry_run=1" if args.dry_run else ""
                    print(
                        f"[OK] {display_path(path)}: parsed={len(bars)} "
                        f"inserted={inserted} skipped={skipped}{suffix}"
                    )
        finally:
            if con is not None:
                con.close()
        return 0
    except Exception as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1


def parse_symbol(path: Path) -> str:
    """Parse XAUUSD from XAUUSD1.csv or BTCUSD from BTCUSD_M1.csv."""
    match = PRICE_FILE_RE.match(path.name)
    if not match:
        raise ValueError(f"unsupported price filename: {path.name}")
    return match.group(1).upper()


def display_path(path: Path) -> str:
    """Return a repo-relative path when possible."""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def _matching_files(data_dir: Path, symbols: list[str]) -> list[tuple[str, list[Path]]]:
    pairs: list[tuple[str, list[Path]]] = []
    for symbol in symbols:
        candidates = [data_dir / f"{symbol}1.csv", data_dir / f"{symbol}_M1.csv"]
        pairs.append((symbol, [path for path in candidates if path.exists()]))
    return pairs


def _symbols(value: str) -> list[str]:
    return [part.strip().upper() for part in value.split(",") if part.strip()]


def _default_data_dir() -> Path:
    value = os.getenv("FINROBOT_DATA_DIR")
    return Path(value) if value else ROOT / "data"


def _default_warehouse() -> Path:
    value = os.getenv("FINROBOT_WAREHOUSE")
    return Path(value) if value else data_store.DEFAULT_WAREHOUSE


if __name__ == "__main__":
    raise SystemExit(main())
