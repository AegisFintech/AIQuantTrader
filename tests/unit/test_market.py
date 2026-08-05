from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from aiquanttrader.domain.features import FeatureSnapshot
from aiquanttrader.domain.market import (
    BboEvent,
    BookLevel,
    DataCapabilities,
    EventHeader,
    L2BookSnapshot,
)


def header() -> EventHeader:
    return EventHeader(
        event_id="book-1",
        event_ts_ns=1_000_000_000,
        receive_ts_ns=1_000_000_100,
        connection_id="connection-1",
    )


def level(price: str, size: str = "1", order_count: int = 1) -> BookLevel:
    return BookLevel(price=Decimal(price), size=Decimal(size), order_count=order_count)


def test_valid_book_is_sorted_and_canonical() -> None:
    book = L2BookSnapshot(
        header=header(),
        bids=(level("99999"), level("99998")),
        asks=(level("100001"), level("100002")),
    )

    assert book.bids[0].price == Decimal("99999")
    assert book.canonical_bytes() == book.canonical_bytes()
    assert len(book.sha256()) == 64


@pytest.mark.parametrize(
    ("bids", "asks", "message"),
    [
        ((level("99998"), level("99999")), (level("100001"),), "bids must be sorted"),
        ((level("99999"),), (level("100002"), level("100001")), "asks must be sorted"),
        ((level("100001"),), (level("100001"),), "locked or crossed"),
        ((level("99999"), level("99999")), (level("100001"),), "unique prices"),
    ],
)
def test_invalid_book_is_rejected(
    bids: tuple[BookLevel, ...],
    asks: tuple[BookLevel, ...],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        L2BookSnapshot(header=header(), bids=bids, asks=asks)


def test_bbo_rejects_crossed_market() -> None:
    with pytest.raises(ValidationError, match="best bid"):
        BboEvent(
            header=header(),
            bid_price=Decimal("100"),
            bid_size=Decimal("1"),
            ask_price=Decimal("99"),
            ask_size=Decimal("1"),
        )


def test_header_rejects_implausible_future_exchange_time() -> None:
    with pytest.raises(ValidationError, match="more than 60 seconds"):
        EventHeader(
            event_id="future",
            event_ts_ns=61_000_000_001,
            receive_ts_ns=0,
            connection_id="connection-1",
        )


def test_market_wide_liquidations_cannot_be_claimed() -> None:
    assert not DataCapabilities().market_wide_liquidations_available
    with pytest.raises(ValidationError):
        DataCapabilities.model_validate({"market_wide_liquidations_available": True})


def test_feature_timestamps_must_be_causal() -> None:
    values = {
        "feature_set": "microstructure-v1",
        "event_ts_ns": 100,
        "receive_ts_ns": 110,
        "computed_ts_ns": 120,
        "max_input_age_ns": 20,
        "book_imbalance": "0.1",
        "microprice": "100000",
        "vamp": "100001",
        "weighted_midprice": "100000.5",
        "depth_imbalance": "0.2",
        "trade_flow_imbalance": "-0.1",
        "aggressor_ratio": "0.5",
        "volume_delta": "1.2",
        "realized_volatility": "0.001",
        "atr": "20",
        "spread_bps": "0.2",
        "inventory_base": "0",
        "inventory_notional_usd": "0",
        "fill_probability": "0.3",
        "adverse_selection_bps": "-0.1",
    }
    assert FeatureSnapshot.model_validate(values).computed_ts_ns == 120

    values["computed_ts_ns"] = 109
    with pytest.raises(ValidationError, match="computed timestamp"):
        FeatureSnapshot.model_validate(values)
