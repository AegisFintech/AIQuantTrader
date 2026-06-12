#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for path in (ROOT, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from finrobot.data_store import connect  # noqa: E402
from finrobot.prices import generate_synthetic_bars, ingest_bars  # noqa: E402
import harvest_mt5_export  # noqa: E402
from runtime_paths import common_dir as default_common_dir  # noqa: E402


DEFAULT_SYNTHETIC_START_TS = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())


@dataclass(frozen=True)
class BootstrapResult:
    """Summary for one BTC history bootstrap run."""

    source: str
    symbol: str
    bars: int = 0
    inserted: int = 0
    dry_run: bool = False
    message: str | None = None


def bootstrap_history(args: argparse.Namespace) -> BootstrapResult:
    """Route one bootstrap request to the selected source."""
    symbol = args.symbol.strip().upper()
    if args.source == "mt5-export":
        return _bootstrap_mt5_export(args, symbol)
    if args.source == "synthetic":
        return _bootstrap_synthetic(args, symbol)
    if args.source == "third-party":
        return BootstrapResult(
            source=args.source,
            symbol=symbol,
            dry_run=args.dry_run,
            message="TODO: third-party source not yet implemented; see docs/BTC_DATA_SOURCES.md for the options",
        )
    raise ValueError(f"unsupported source: {args.source}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bootstrap BTC M1 history into the FinRobot DuckDB prices table.")
    parser.add_argument("--source", choices=("mt5-export", "synthetic", "third-party"), default="mt5-export")
    parser.add_argument("--symbol", default="BTCUSD")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--warehouse", type=Path, default=ROOT / "data" / "finrobot.duckdb")
    parser.add_argument("--common-dir", type=Path, default=None)
    parser.add_argument("--n-bars", type=int, default=200000)
    parser.add_argument("--start-ts", type=int, default=DEFAULT_SYNTHETIC_START_TS)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--base-price", type=float, default=60000.0)
    parser.add_argument("--volatility", type=float, default=0.005)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = bootstrap_history(args)
    except Exception as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1
    _print_summary(result)
    return 0


def _bootstrap_mt5_export(args: argparse.Namespace, symbol: str) -> BootstrapResult:
    common_files_dir = args.common_dir if args.common_dir is not None else default_common_dir()
    if common_files_dir is None or not Path(common_files_dir).is_dir():
        return BootstrapResult(
            source=args.source,
            symbol=symbol,
            dry_run=args.dry_run,
            message="[skip] no MT5 Common Files directory found",
        )

    results = harvest_mt5_export.harvest_all(
        Path(common_files_dir),
        args.data_dir,
        warehouse_path=args.warehouse,
        dry_run=args.dry_run,
        symbols=[symbol],
    )
    return BootstrapResult(
        source=args.source,
        symbol=symbol,
        bars=sum(result.bars for result in results),
        inserted=sum(result.inserted for result in results),
        dry_run=args.dry_run,
    )


def _bootstrap_synthetic(args: argparse.Namespace, symbol: str) -> BootstrapResult:
    bars = generate_synthetic_bars(
        symbol,
        args.n_bars,
        start_ts=args.start_ts,
        interval_seconds=args.interval_seconds,
        base_price=args.base_price,
        volatility=args.volatility,
        seed=args.seed,
    )
    inserted = 0
    if not args.dry_run:
        con = connect(args.warehouse)
        try:
            inserted = ingest_bars(con, symbol, bars)
        finally:
            con.close()
    return BootstrapResult(
        source=args.source,
        symbol=symbol,
        bars=len(bars),
        inserted=inserted,
        dry_run=args.dry_run,
    )


def _print_summary(result: BootstrapResult) -> None:
    if result.message:
        print(result.message)
    suffix = " dry_run=1" if result.dry_run else ""
    print(
        f"[OK] bootstrap_btc_history: source={result.source} symbol={result.symbol} "
        f"bars={result.bars} inserted={result.inserted}{suffix}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
