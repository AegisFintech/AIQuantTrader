"""Hyperliquid capture, validation, normalization, and historical import.

Raw archive classes remain available from this package while loading lazily so
dependency-light monitoring images do not need the Zstandard runtime.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiquanttrader.market_data.raw import RawSegmentReader, RawSegmentWriter

__all__ = ["RawSegmentReader", "RawSegmentWriter"]


def __getattr__(name: str) -> object:
    if name == "RawSegmentReader":
        from aiquanttrader.market_data.raw import RawSegmentReader

        return RawSegmentReader
    if name == "RawSegmentWriter":
        from aiquanttrader.market_data.raw import RawSegmentWriter

        return RawSegmentWriter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
