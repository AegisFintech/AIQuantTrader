#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aiquanttrader.prices import load_tsv_bars  # noqa: E402
from runtime_paths import common_dir as default_common_dir  # noqa: E402


EXPORT_FILE_RE = re.compile(r"^aiquanttrader_export_([A-Za-z0-9]+)_M1\.tsv$")
LOAD_OK_RE = re.compile(
    r"^\[OK\]\s+(?P<path>.+?):\s+parsed=(?P<parsed>\d+)\s+"
    r"inserted=(?P<inserted>\d+)\s+skipped=(?P<skipped>\d+)"
)


class ExportFilenameError(ValueError):
    """Raised when an MT5 history export filename does not match the contract."""


class HarvestError(RuntimeError):
    """Raised when harvesting fails after a valid export file is found."""


@dataclass(frozen=True)
class HarvestResult:
    """Result for one harvested MT5 export file."""

    symbol: str
    src_path: Path
    dest_path: Path
    bars: int
    inserted: int
    dry_run: bool = False


def parse_export_filename(filename: str | Path) -> tuple[str]:
    """Parse `aiquanttrader_export_<SYMBOL>_M1.tsv` and return the upper-case symbol."""
    match = EXPORT_FILE_RE.match(Path(filename).name)
    if not match:
        raise ExportFilenameError(f"unsupported MT5 export filename: {Path(filename).name}")
    return (match.group(1).upper(),)


def discover_exports(
    common_files_dir: Path | None,
    symbols: Iterable[str] | None = None,
) -> list[tuple[str, Path]]:
    """Return `(symbol, path)` pairs for valid MT5 M1 export files."""
    if common_files_dir is None:
        return []
    common_files_dir = Path(common_files_dir)
    if not common_files_dir.is_dir():
        return []

    wanted = normalize_symbols(symbols)
    exports: list[tuple[str, Path]] = []
    for path in sorted(common_files_dir.glob("aiquanttrader_export_*_M1.tsv")):
        try:
            (symbol,) = parse_export_filename(path.name)
        except ExportFilenameError:
            continue
        if wanted is not None and symbol not in wanted:
            continue
        exports.append((symbol, path))
    return exports


def copy_to_data_dir(src_path: Path, data_dir: Path, symbol: str) -> Path:
    """Copy one export file into `data/<SYMBOL>_M1.csv` and return the destination."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    dest_path = data_dir / f"{symbol.upper()}_M1.csv"
    shutil.copyfile(src_path, dest_path)
    return dest_path


def harvest_all(
    common_files_dir: Path,
    data_dir: Path,
    warehouse_path: Path | None = None,
    dry_run: bool = False,
    symbols: Iterable[str] | None = None,
) -> list[HarvestResult]:
    """Harvest all matching MT5 M1 exports and load copied files into DuckDB."""
    exports = discover_exports(common_files_dir, symbols)
    if not exports:
        print(f"[skip] no MT5 M1 export files found in {display_path(Path(common_files_dir))}")
        return []

    results: list[HarvestResult] = []
    for symbol, src_path in exports:
        bars = _count_parseable_bars(src_path)
        if bars is None:
            print(f"[skip] harvest: {symbol} file={display_path(src_path)} reason=unparseable")
            continue
        if bars == 0:
            print(f"[skip] harvest: {symbol} file={display_path(src_path)} reason=empty")
            continue

        dest_path = Path(data_dir) / f"{symbol}_M1.csv"
        if dry_run:
            inserted = 0
        else:
            dest_path = copy_to_data_dir(src_path, data_dir, symbol)
            inserted = _run_loader(data_dir, dest_path, warehouse_path, symbol)

        result = HarvestResult(
            symbol=symbol,
            src_path=src_path,
            dest_path=dest_path,
            bars=bars,
            inserted=inserted,
            dry_run=dry_run,
        )
        results.append(result)
        _print_result(result)
    return results


def normalize_symbols(symbols: Iterable[str] | None) -> set[str] | None:
    """Normalize optional symbol filters to an upper-case set."""
    if symbols is None:
        return None
    if isinstance(symbols, str):
        symbols = symbols.split(",")
    normalized = {symbol.strip().upper() for symbol in symbols if symbol and symbol.strip()}
    return normalized or None


def parse_symbol_filter(value: str | None) -> set[str] | None:
    """Parse a comma-separated CLI symbol filter."""
    if value is None:
        return None
    return normalize_symbols(value.split(","))


def display_path(path: Path) -> str:
    """Return a repo-relative path when possible."""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Harvest MT5 M1 history exports into AIQuantTrader data.")
    parser.add_argument("--common-dir", type=Path, default=None, help="Override MT5 Common Files discovery.")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--warehouse", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbol filter, e.g. XAUUSD,XAUUSD.")
    args = parser.parse_args(argv)

    common_files_dir = args.common_dir if args.common_dir is not None else default_common_dir()
    if common_files_dir is None or not Path(common_files_dir).is_dir():
        print("[ERR] MT5 Common Files directory not found", file=sys.stderr)
        return 2

    try:
        harvest_all(
            Path(common_files_dir),
            args.data_dir,
            warehouse_path=args.warehouse,
            dry_run=args.dry_run,
            symbols=parse_symbol_filter(args.symbols),
        )
    except Exception as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1
    return 0


def _count_parseable_bars(path: Path) -> int | None:
    if not Path(path).exists() or Path(path).stat().st_size == 0:
        return 0
    try:
        return sum(1 for _ in load_tsv_bars(Path(path)))
    except Exception:
        return None


def _run_loader(data_dir: Path, dest_path: Path, warehouse_path: Path | None, symbol: str) -> int:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "load_historical_prices.py"),
        "--data-dir",
        str(data_dir),
        "--symbols",
        symbol,
    ]
    if warehouse_path is not None:
        cmd.extend(["--warehouse", str(warehouse_path)])

    completed = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip()
        raise HarvestError(f"load_historical_prices.py failed for {symbol}: {details}")
    return _parse_loader_inserted(completed.stdout, dest_path)


def _parse_loader_inserted(stdout: str, dest_path: Path) -> int:
    for line in stdout.splitlines():
        match = LOAD_OK_RE.match(line)
        if match and Path(match.group("path")).name == dest_path.name:
            return int(match.group("inserted"))
    raise HarvestError(f"loader did not report inserted rows for {dest_path.name}")


def _print_result(result: HarvestResult) -> None:
    suffix = " dry_run=1" if result.dry_run else ""
    print(
        f"[OK] harvest: {result.symbol} bars={result.bars} \u2192 "
        f"{display_path(result.dest_path)} \u2192 inserted={result.inserted}{suffix}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
