from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import cast

import pytest
from pydantic import ValidationError

from aiquanttrader_native.domain.data import RawFrameMetadata
from aiquanttrader_native.domain.market import (
    AccountLiquidationEvent,
    AccountOrderUpdateEvent,
    AggressorSide,
    BboEvent,
    FundingPaymentEvent,
    L2BookSnapshot,
    LiquidationFillEvent,
    TradeEvent,
)
from aiquanttrader_native.market_data.protocol import (
    ProtocolError,
    application_ping,
    parse_frame,
    subscription_messages,
)

NOW_MS = 1_700_000_000_000
NOW_NS = NOW_MS * 1_000_000


def encoded(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def metadata(payload: bytes, *, receive_ns: int = NOW_NS + 1_000) -> RawFrameMetadata:
    return RawFrameMetadata(
        receive_ts_ns=receive_ns,
        monotonic_ts_ns=123,
        connection_id="ws-test",
        subscription_id="public-btc",
        transport="text",
        payload_size=len(payload),
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        recorder_version="test-v1",
    )


def test_public_subscriptions_and_application_ping_are_canonical() -> None:
    messages = subscription_messages(("l2Book", "trades", "bbo", "activeAssetCtx"))

    assert len(messages) == 4
    assert all(isinstance(message, str) for message in messages)
    assert json.loads(messages[0]) == {
        "method": "subscribe",
        "subscription": {"coin": "BTC", "type": "l2Book"},
    }
    assert application_ping() == '{"method":"ping"}'
    with pytest.raises(ValueError, match="unsupported"):
        subscription_messages(("candle",))


def test_l2_and_bbo_frames_are_strictly_normalized() -> None:
    book_payload = encoded(
        {
            "channel": "l2Book",
            "data": {
                "coin": "BTC",
                "time": NOW_MS,
                "levels": [
                    [{"px": "99999", "sz": "2", "n": 3}],
                    [{"px": "100001", "sz": "4", "n": 5}],
                ],
            },
        }
    )
    event = parse_frame(book_payload, metadata(book_payload)).events[0]

    assert isinstance(event, L2BookSnapshot)
    assert event.bids[0].order_count == 3
    assert event.header.source_record_id == hashlib.sha256(book_payload).hexdigest()

    bbo_payload = encoded(
        {
            "channel": "bbo",
            "data": {
                "coin": "BTC",
                "time": NOW_MS,
                "bbo": [
                    {"px": "100", "sz": "1", "n": 1},
                    {"px": "101", "sz": "2", "n": 1},
                ],
            },
        }
    )
    bbo = parse_frame(bbo_payload, metadata(bbo_payload)).events[0]
    assert isinstance(bbo, BboEvent)
    assert bbo.ask_price - bbo.bid_price == Decimal("1")


def test_crossed_book_and_wrong_instrument_are_rejected() -> None:
    crossed = encoded(
        {
            "channel": "l2Book",
            "data": {
                "coin": "BTC",
                "time": NOW_MS,
                "levels": [
                    [{"px": "101", "sz": "1", "n": 1}],
                    [{"px": "100", "sz": "1", "n": 1}],
                ],
            },
        }
    )
    with pytest.raises(ValidationError, match="crossed"):
        parse_frame(crossed, metadata(crossed))

    wrong = encoded({"channel": "trades", "data": [{"coin": "ETH", "time": NOW_MS}]})
    with pytest.raises(ProtocolError, match="not for BTC"):
        parse_frame(wrong, metadata(wrong))


def test_trades_and_asset_context_expand_deterministically() -> None:
    trades = encoded(
        {
            "channel": "trades",
            "data": [
                {
                    "coin": "BTC",
                    "side": "B",
                    "px": "100000",
                    "sz": "0.01",
                    "hash": "0xabc",
                    "tid": 42,
                    "time": NOW_MS,
                },
                {
                    "coin": "BTC",
                    "side": "A",
                    "px": "99999",
                    "sz": "0.02",
                    "hash": "0xdef",
                    "time": NOW_MS + 1,
                },
            ],
        }
    )
    parsed = parse_frame(trades, metadata(trades, receive_ns=NOW_NS + 2_000_000))
    trade_events = [cast(TradeEvent, event) for event in parsed.events]
    assert all(isinstance(event, TradeEvent) for event in parsed.events)
    assert [event.aggressor for event in trade_events] == [
        AggressorSide.BUYER,
        AggressorSide.SELLER,
    ]
    assert trade_events[0].trade_id == "42"

    context = encoded(
        {
            "channel": "activeAssetCtx",
            "data": {
                "coin": "BTC",
                "ctx": {
                    "markPx": "100000",
                    "oraclePx": "100010",
                    "funding": "0.00001",
                    "openInterest": "1234.5",
                },
            },
        }
    )
    context_events = parse_frame(context, metadata(context)).events
    assert [event.event_type for event in context_events] == [
        "mark_price",
        "index_price",
        "funding",
        "open_interest",
    ]
    assert all(event.header.event_ts_source == "receive" for event in context_events)


def test_liquidations_are_account_scoped_and_fill_attributed() -> None:
    user = encoded(
        {
            "channel": "user",
            "data": {
                "liquidation": {
                    "lid": 7,
                    "liquidator": "0x" + "1" * 40,
                    "liquidated_user": "0x" + "2" * 40,
                    "liquidated_ntl_pos": "1000",
                    "liquidated_account_value": "250",
                }
            },
        }
    )
    liquidation = parse_frame(user, metadata(user)).events[0]
    assert isinstance(liquidation, AccountLiquidationEvent)
    assert not hasattr(liquidation, "price")

    fill = encoded(
        {
            "channel": "userFills",
            "data": {
                "fills": [
                    {
                        "coin": "BTC",
                        "time": NOW_MS,
                        "side": "A",
                        "tid": 8,
                        "oid": 9,
                        "px": "99000",
                        "sz": "0.5",
                        "crossed": True,
                        "fee": "1",
                        "feeToken": "USDC",
                        "closedPnl": "-20",
                        "hash": "0xfill",
                        "liquidation": {
                            "method": "backstop",
                            "liquidatedUser": "0x" + "3" * 40,
                        },
                    }
                ]
            },
        }
    )
    events = parse_frame(fill, metadata(fill)).events
    assert len(events) == 2
    assert isinstance(events[1], LiquidationFillEvent)
    assert events[1].method == "backstop"


def test_payload_digest_and_json_are_verified() -> None:
    payload = b"not-json"
    with pytest.raises(ProtocolError, match="invalid JSON"):
        parse_frame(payload, metadata(payload))
    mismatched = metadata(b"different")
    with pytest.raises(ProtocolError, match="does not match"):
        parse_frame(payload, mismatched)


def test_private_funding_and_order_updates_are_normalized() -> None:
    funding = encoded(
        {
            "channel": "user",
            "data": {
                "funding": {
                    "coin": "BTC",
                    "time": NOW_MS,
                    "usdc": "-1.25",
                    "szi": "0.5",
                    "fundingRate": "0.00001",
                }
            },
        }
    )
    funding_event = parse_frame(funding, metadata(funding)).events[0]
    assert isinstance(funding_event, FundingPaymentEvent)
    assert funding_event.amount_usdc == Decimal("-1.25")

    updates = encoded(
        {
            "channel": "orderUpdates",
            "data": [
                {
                    "status": "open",
                    "statusTimestamp": NOW_MS,
                    "order": {
                        "coin": "BTC",
                        "side": "B",
                        "limitPx": "99999",
                        "sz": "0.1",
                        "origSz": "0.2",
                        "oid": 10,
                        "cloid": "client-1",
                        "reduceOnly": False,
                    },
                }
            ],
        }
    )
    order = parse_frame(updates, metadata(updates)).events[0]
    assert isinstance(order, AccountOrderUpdateEvent)
    assert order.client_order_id == "client-1"
    assert order.remaining_size == Decimal("0.1")


def test_control_fallback_and_empty_user_frames() -> None:
    control = encoded({"channel": "subscriptionResponse", "data": {}})
    assert parse_frame(control, metadata(control)).is_control

    empty_user = encoded({"channel": "user", "data": {}})
    assert parse_frame(empty_user, metadata(empty_user)).events == ()

    trade = encoded(
        {
            "channel": "trades",
            "data": [
                {
                    "coin": "BTC",
                    "side": "B",
                    "px": "1",
                    "sz": "1",
                    "time": NOW_MS,
                }
            ],
        }
    )
    event = parse_frame(trade, metadata(trade)).events[0]
    assert isinstance(event, TradeEvent)
    assert event.trade_id.startswith("payload-")


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ([], "invalid_message"),
        ({"data": {}}, "missing_channel"),
        ({"channel": "unknown", "data": {}}, "unsupported_channel"),
        ({"channel": "trades", "data": {}}, "invalid_trades"),
        (
            {
                "channel": "bbo",
                "data": {"coin": "BTC", "time": NOW_MS, "bbo": [None, None]},
            },
            "incomplete_bbo",
        ),
        (
            {
                "channel": "trades",
                "data": [
                    {
                        "coin": "BTC",
                        "side": "X",
                        "px": "1",
                        "sz": "1",
                        "time": NOW_MS,
                    }
                ],
            },
            "invalid_side",
        ),
        (
            {
                "channel": "trades",
                "data": [
                    {
                        "coin": "BTC",
                        "side": "B",
                        "px": True,
                        "sz": "1",
                        "time": NOW_MS,
                    }
                ],
            },
            "invalid_decimal",
        ),
    ],
)
def test_protocol_shape_failures_have_stable_codes(message: object, code: str) -> None:
    frame = encoded(message)
    with pytest.raises(ProtocolError) as raised:
        parse_frame(frame, metadata(frame))
    assert raised.value.code == code
