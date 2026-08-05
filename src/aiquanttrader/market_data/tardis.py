"""Authenticated, checksummed Tardis historical-file acquisition."""

from __future__ import annotations

import csv
import gzip
import os
import secrets
import urllib.request
from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from urllib.parse import quote

from aiquanttrader.domain.data import TardisFileManifest
from aiquanttrader.market_data.io import atomic_write_bytes, fsync_directory, sha256_file

TardisDataType = Literal[
    "incremental_book_L2",
    "book_snapshot_25",
    "quotes",
    "trades",
    "derivative_ticker",
]

REQUIRED_COLUMNS: dict[TardisDataType, frozenset[str]] = {
    "incremental_book_L2": frozenset(
        {
            "exchange",
            "symbol",
            "timestamp",
            "local_timestamp",
            "is_snapshot",
            "side",
            "price",
            "amount",
        }
    ),
    "book_snapshot_25": frozenset(
        {
            "exchange",
            "symbol",
            "timestamp",
            "local_timestamp",
            "is_snapshot",
            "side",
            "price",
            "amount",
        }
    ),
    "quotes": frozenset(
        {
            "exchange",
            "symbol",
            "timestamp",
            "local_timestamp",
            "ask_amount",
            "ask_price",
            "bid_price",
            "bid_amount",
        }
    ),
    "trades": frozenset(
        {"exchange", "symbol", "timestamp", "local_timestamp", "id", "side", "price", "amount"}
    ),
    "derivative_ticker": frozenset(
        {"exchange", "symbol", "timestamp", "local_timestamp", "funding_rate", "open_interest"}
    ),
}


class DownloadResponse(Protocol):
    def read(self, size: int = -1) -> bytes: ...


OpenRequest = Callable[[urllib.request.Request, float], AbstractContextManager[DownloadResponse]]


def _default_open(
    request: urllib.request.Request, timeout: float
) -> AbstractContextManager[DownloadResponse]:
    response = urllib.request.urlopen(request, timeout=timeout)
    return cast(AbstractContextManager[DownloadResponse], cast(Any, response))


def _api_key(path: Path | None) -> str | None:
    if path is None:
        return None
    if not path.is_file() or path.stat().st_size > 4_096:
        raise ValueError("Tardis API-key secret must be a regular file no larger than 4096 bytes")
    value = path.read_text(encoding="utf-8").strip()
    if not value or "\n" in value or "\r" in value:
        raise ValueError("Tardis API-key secret must contain one non-empty line")
    return value


def _validate_gzip_csv(path: Path, data_type: TardisDataType) -> int:
    try:
        with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            columns = frozenset(reader.fieldnames or ())
            missing = REQUIRED_COLUMNS[data_type] - columns
            if missing:
                raise ValueError(f"Tardis CSV is missing required columns: {sorted(missing)}")
            count = sum(1 for _ in reader)
    except (OSError, EOFError, UnicodeError, csv.Error) as exc:
        raise ValueError(f"invalid Tardis gzip CSV: {exc}") from exc
    return count


def download_file(
    *,
    root: Path,
    data_type: TardisDataType,
    day: date,
    symbol: Literal["BTC"] = "BTC",
    api_key_secret_path: Path | None = None,
    timeout_seconds: float = 60,
    open_request: OpenRequest = _default_open,
) -> tuple[Path, Path, TardisFileManifest]:
    if day > datetime.now(UTC).date():
        raise ValueError("cannot download a future Tardis dataset")
    if timeout_seconds <= 0 or timeout_seconds > 300:
        raise ValueError("download timeout must be in (0, 300] seconds")
    root = root.resolve()
    relative = Path(
        "historical",
        "source=tardis",
        "exchange=hyperliquid",
        f"data_type={data_type}",
        f"date={day.isoformat()}",
        f"{symbol}.csv.gz",
    )
    target = root / relative
    manifest_path = target.with_suffix(target.suffix + ".manifest.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or manifest_path.exists():
        if not target.is_file() or not manifest_path.is_file():
            raise FileExistsError("incomplete immutable Tardis target already exists")
        manifest = TardisFileManifest.model_validate_json(manifest_path.read_bytes())
        if manifest.compressed_sha256 != sha256_file(target):
            raise ValueError("existing Tardis file does not match its manifest")
        return target, manifest_path, manifest

    url = (
        "https://datasets.tardis.dev/v1/hyperliquid/"
        f"{quote(data_type, safe='')}/{day:%Y/%m/%d}/{quote(symbol, safe='')}.csv.gz"
    )
    headers = {"Accept-Encoding": "identity", "User-Agent": "AIQuantTrader/market-data-v1"}
    key = _api_key(api_key_secret_path)
    if key is not None:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, headers=headers)
    partial = target.with_name(f".{target.name}.{secrets.token_hex(8)}.partial")
    try:
        with open_request(request, timeout_seconds) as response:
            descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o640)
            with os.fdopen(descriptor, "wb") as output:
                while chunk := response.read(1_048_576):
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        if partial.stat().st_size == 0:
            raise ValueError("Tardis returned an empty file")
        row_count = _validate_gzip_csv(partial, data_type)
        digest = sha256_file(partial)
        partial.rename(target)
        fsync_directory(target.parent)
        manifest = TardisFileManifest(
            data_type=data_type,
            symbol=symbol,
            date=day.isoformat(),
            relative_path=target.relative_to(root).as_posix(),
            byte_count=target.stat().st_size,
            compressed_sha256=digest,
            row_count=row_count,
            source_url=url,
            created_at=datetime.now(UTC),
        )
        atomic_write_bytes(manifest_path, manifest.canonical_bytes() + b"\n")
        return target, manifest_path, manifest
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
