"""Strict Hyperliquid WebSocket normalization for the BTC perpetual."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

from aiquanttrader.domain.data import RawFrameMetadata
from aiquanttrader.domain.market import (
    AccountEventHeader,
    AccountFillEvent,
    AccountLiquidationEvent,
    AccountOrderUpdateEvent,
    AggressorSide,
    BboEvent,
    BookLevel,
    EventHeader,
    FundingEvent,
    FundingPaymentEvent,
    IndexPriceEvent,
    L2BookSnapshot,
    LiquidationFillEvent,
    MarketEvent,
    MarkPriceEvent,
    OpenInterestEvent,
    OrderSide,
    TimestampSource,
    TradeEvent,
)


class ProtocolError(ValueError):
    def __init__(self, code: str, message: str, *, channel: str = "unknown") -> None:
        super().__init__(message)
        self.code = code
        self.channel = channel


@dataclass(frozen=True, slots=True)
class ParsedFrame:
    channel: str
    events: tuple[MarketEvent, ...]
    is_control: bool = False


def subscription_messages(channels: Sequence[str], *, coin: str = "BTC") -> tuple[str, ...]:
    allowed = {"l2Book", "trades", "bbo", "activeAssetCtx"}
    messages: list[str] = []
    for channel in channels:
        if channel not in allowed:
            raise ValueError(f"unsupported public subscription: {channel}")
        payload = {"method": "subscribe", "subscription": {"type": channel, "coin": coin}}
        messages.append(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return tuple(messages)


def application_ping() -> str:
    return '{"method":"ping"}'


def _mapping(value: Any, *, code: str, channel: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProtocolError(code, "expected a JSON object", channel=channel)
    return cast(Mapping[str, Any], value)


def _sequence(value: Any, *, code: str, channel: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ProtocolError(code, "expected a JSON array", channel=channel)
    return value


def _required(mapping: Mapping[str, Any], key: str, *, channel: str) -> Any:
    try:
        return mapping[key]
    except KeyError as exc:
        raise ProtocolError(
            "missing_field", f"missing required field: {key}", channel=channel
        ) from exc


def _coin(mapping: Mapping[str, Any], *, channel: str) -> None:
    if _required(mapping, "coin", channel=channel) != "BTC":
        raise ProtocolError("unexpected_instrument", "frame is not for BTC", channel=channel)


def _decimal(value: Any, *, field: str, channel: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ProtocolError("invalid_decimal", f"{field} is not numeric", channel=channel)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ProtocolError(
            "invalid_decimal", f"{field} is not a decimal", channel=channel
        ) from exc


def _integer(value: Any, *, field: str, channel: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ProtocolError("invalid_integer", f"{field} is not an integer", channel=channel)
    try:
        return int(value)
    except ValueError as exc:
        raise ProtocolError(
            "invalid_integer", f"{field} is not an integer", channel=channel
        ) from exc


def _side(value: Any, *, channel: str) -> OrderSide:
    if value == "B":
        return OrderSide.BUY
    if value == "A":
        return OrderSide.SELL
    raise ProtocolError("invalid_side", f"unsupported Hyperliquid side: {value!r}", channel=channel)


def _boolean(value: Any, *, field: str, channel: str) -> bool:
    if not isinstance(value, bool):
        raise ProtocolError("invalid_boolean", f"{field} is not a boolean", channel=channel)
    return value


def _liquidation_method(value: Any, *, channel: str) -> Literal["market", "backstop"]:
    if value == "market" or value == "backstop":
        return cast(Literal["market", "backstop"], value)
    raise ProtocolError(
        "invalid_liquidation_method",
        f"unsupported liquidation method: {value!r}",
        channel=channel,
    )


def _header(
    metadata: RawFrameMetadata,
    *,
    event_id: str,
    event_ts_ns: int,
    exchange_timestamp: bool,
) -> EventHeader:
    return EventHeader(
        event_id=event_id,
        event_ts_ns=event_ts_ns,
        receive_ts_ns=metadata.receive_ts_ns,
        connection_id=metadata.connection_id,
        event_ts_source=(
            TimestampSource.EXCHANGE if exchange_timestamp else TimestampSource.RECEIVE
        ),
        source_record_id=metadata.payload_sha256,
    )


def _event_id(channel: str, identity: str, payload_sha256: str) -> str:
    return f"{channel}:{identity}:{payload_sha256[:16]}"


def _parse_level(value: Any, *, channel: str) -> BookLevel:
    level = _mapping(value, code="invalid_book_level", channel=channel)
    order_count = _integer(_required(level, "n", channel=channel), field="n", channel=channel)
    return BookLevel(
        price=_decimal(_required(level, "px", channel=channel), field="px", channel=channel),
        size=_decimal(_required(level, "sz", channel=channel), field="sz", channel=channel),
        order_count=order_count,
    )


def _parse_book(data: Mapping[str, Any], metadata: RawFrameMetadata) -> L2BookSnapshot:
    channel = "l2Book"
    _coin(data, channel=channel)
    time_ms = _integer(_required(data, "time", channel=channel), field="time", channel=channel)
    levels = _sequence(
        _required(data, "levels", channel=channel), code="invalid_levels", channel=channel
    )
    if len(levels) != 2:
        raise ProtocolError(
            "invalid_levels", "book must contain bid and ask arrays", channel=channel
        )
    bids = tuple(
        _parse_level(value, channel=channel)
        for value in _sequence(levels[0], code="invalid_bids", channel=channel)
    )
    asks = tuple(
        _parse_level(value, channel=channel)
        for value in _sequence(levels[1], code="invalid_asks", channel=channel)
    )
    return L2BookSnapshot(
        header=_header(
            metadata,
            event_id=_event_id(channel, str(time_ms), metadata.payload_sha256),
            event_ts_ns=time_ms * 1_000_000,
            exchange_timestamp=True,
        ),
        bids=bids,
        asks=asks,
    )


def _parse_bbo(data: Mapping[str, Any], metadata: RawFrameMetadata) -> BboEvent:
    channel = "bbo"
    _coin(data, channel=channel)
    time_ms = _integer(_required(data, "time", channel=channel), field="time", channel=channel)
    bbo = _sequence(_required(data, "bbo", channel=channel), code="invalid_bbo", channel=channel)
    if len(bbo) != 2 or bbo[0] is None or bbo[1] is None:
        raise ProtocolError("incomplete_bbo", "BTC BBO must contain bid and ask", channel=channel)
    bid = _parse_level(bbo[0], channel=channel)
    ask = _parse_level(bbo[1], channel=channel)
    return BboEvent(
        header=_header(
            metadata,
            event_id=_event_id(channel, str(time_ms), metadata.payload_sha256),
            event_ts_ns=time_ms * 1_000_000,
            exchange_timestamp=True,
        ),
        bid_price=bid.price,
        bid_size=bid.size,
        ask_price=ask.price,
        ask_size=ask.size,
    )


def _parse_trade(value: Any, metadata: RawFrameMetadata, index: int) -> TradeEvent:
    channel = "trades"
    trade = _mapping(value, code="invalid_trade", channel=channel)
    _coin(trade, channel=channel)
    time_ms = _integer(_required(trade, "time", channel=channel), field="time", channel=channel)
    side = _side(_required(trade, "side", channel=channel), channel=channel)
    trade_id_value = trade.get("tid", trade.get("hash"))
    if trade_id_value is None:
        trade_id_value = f"payload-{metadata.payload_sha256}-{index}"
    trade_id = str(trade_id_value)
    return TradeEvent(
        header=_header(
            metadata,
            event_id=_event_id(channel, f"{trade_id}-{index}", metadata.payload_sha256),
            event_ts_ns=time_ms * 1_000_000,
            exchange_timestamp=True,
        ),
        trade_id=trade_id,
        price=_decimal(_required(trade, "px", channel=channel), field="px", channel=channel),
        size=_decimal(_required(trade, "sz", channel=channel), field="sz", channel=channel),
        aggressor=(AggressorSide.BUYER if side is OrderSide.BUY else AggressorSide.SELLER),
        transaction_hash=str(trade["hash"]) if trade.get("hash") is not None else None,
    )


def _parse_asset_context(
    data: Mapping[str, Any], metadata: RawFrameMetadata
) -> tuple[MarketEvent, ...]:
    channel = "activeAssetCtx"
    _coin(data, channel=channel)
    context = _mapping(
        _required(data, "ctx", channel=channel), code="invalid_context", channel=channel
    )
    timestamp = metadata.receive_ts_ns

    def header(event_type: str) -> EventHeader:
        return _header(
            metadata,
            event_id=_event_id(event_type, str(timestamp), metadata.payload_sha256),
            event_ts_ns=timestamp,
            exchange_timestamp=False,
        )

    next_funding_ts_ns = (timestamp // 3_600_000_000_000 + 1) * 3_600_000_000_000
    return (
        MarkPriceEvent(
            header=header("mark_price"),
            mark_price=_decimal(
                _required(context, "markPx", channel=channel), field="markPx", channel=channel
            ),
        ),
        IndexPriceEvent(
            header=header("index_price"),
            index_price=_decimal(
                _required(context, "oraclePx", channel=channel), field="oraclePx", channel=channel
            ),
        ),
        FundingEvent(
            header=header("funding"),
            funding_rate=_decimal(
                _required(context, "funding", channel=channel), field="funding", channel=channel
            ),
            next_funding_ts_ns=next_funding_ts_ns,
        ),
        OpenInterestEvent(
            header=header("open_interest"),
            open_interest_base=_decimal(
                _required(context, "openInterest", channel=channel),
                field="openInterest",
                channel=channel,
            ),
        ),
    )


def _parse_fill(value: Any, metadata: RawFrameMetadata, index: int) -> tuple[MarketEvent, ...]:
    channel = "userFills"
    fill = _mapping(value, code="invalid_fill", channel=channel)
    _coin(fill, channel=channel)
    time_ms = _integer(_required(fill, "time", channel=channel), field="time", channel=channel)
    side = _side(_required(fill, "side", channel=channel), channel=channel)
    trade_id = str(_required(fill, "tid", channel=channel))
    header = _header(
        metadata,
        event_id=_event_id(channel, f"{trade_id}-{index}", metadata.payload_sha256),
        event_ts_ns=time_ms * 1_000_000,
        exchange_timestamp=True,
    )
    events: list[MarketEvent] = [
        AccountFillEvent(
            header=header,
            venue_order_id=str(_required(fill, "oid", channel=channel)),
            trade_id=trade_id,
            side=side,
            price=_decimal(_required(fill, "px", channel=channel), field="px", channel=channel),
            size=_decimal(_required(fill, "sz", channel=channel), field="sz", channel=channel),
            liquidity=(
                "taker"
                if _boolean(
                    _required(fill, "crossed", channel=channel), field="crossed", channel=channel
                )
                else "maker"
            ),
            fee=_decimal(_required(fill, "fee", channel=channel), field="fee", channel=channel),
            fee_token=str(_required(fill, "feeToken", channel=channel)),
            closed_pnl=_decimal(
                _required(fill, "closedPnl", channel=channel), field="closedPnl", channel=channel
            ),
            transaction_hash=str(_required(fill, "hash", channel=channel)),
        )
    ]
    if fill.get("liquidation") is not None:
        liquidation = _mapping(
            fill["liquidation"], code="invalid_liquidation_fill", channel=channel
        )
        events.append(
            LiquidationFillEvent(
                header=header.model_copy(
                    update={
                        "event_id": _event_id("liquidation_fill", trade_id, metadata.payload_sha256)
                    }
                ),
                side=side,
                price=_decimal(_required(fill, "px", channel=channel), field="px", channel=channel),
                size=_decimal(_required(fill, "sz", channel=channel), field="sz", channel=channel),
                trade_id=trade_id,
                method=_liquidation_method(
                    _required(liquidation, "method", channel=channel), channel=channel
                ),
                liquidated_user_address=(
                    str(liquidation["liquidatedUser"])
                    if liquidation.get("liquidatedUser") is not None
                    else None
                ),
            )
        )
    return tuple(events)


def _parse_user_event(
    data: Mapping[str, Any], metadata: RawFrameMetadata
) -> tuple[MarketEvent, ...]:
    channel = "user"
    if data.get("liquidation") is not None:
        liquidation = _mapping(data["liquidation"], code="invalid_liquidation", channel=channel)
        liquidation_id = str(_required(liquidation, "lid", channel=channel))
        return (
            AccountLiquidationEvent(
                header=AccountEventHeader(
                    event_id=_event_id(
                        "account_liquidation", liquidation_id, metadata.payload_sha256
                    ),
                    event_ts_ns=metadata.receive_ts_ns,
                    receive_ts_ns=metadata.receive_ts_ns,
                    connection_id=metadata.connection_id,
                    event_ts_source=TimestampSource.RECEIVE,
                    source_record_id=metadata.payload_sha256,
                ),
                liquidation_id=liquidation_id,
                liquidator_address=str(_required(liquidation, "liquidator", channel=channel)),
                liquidated_user_address=str(
                    _required(liquidation, "liquidated_user", channel=channel)
                ),
                liquidated_notional_usd=_decimal(
                    _required(liquidation, "liquidated_ntl_pos", channel=channel),
                    field="liquidated_ntl_pos",
                    channel=channel,
                ),
                liquidated_account_value_usd=_decimal(
                    _required(liquidation, "liquidated_account_value", channel=channel),
                    field="liquidated_account_value",
                    channel=channel,
                ),
            ),
        )
    if data.get("funding") is not None:
        funding = _mapping(data["funding"], code="invalid_funding_payment", channel=channel)
        _coin(funding, channel=channel)
        time_ms = _integer(
            _required(funding, "time", channel=channel), field="time", channel=channel
        )
        return (
            FundingPaymentEvent(
                header=_header(
                    metadata,
                    event_id=_event_id("funding_payment", str(time_ms), metadata.payload_sha256),
                    event_ts_ns=time_ms * 1_000_000,
                    exchange_timestamp=True,
                ),
                amount_usdc=_decimal(
                    _required(funding, "usdc", channel=channel), field="usdc", channel=channel
                ),
                position_size_base=_decimal(
                    _required(funding, "szi", channel=channel), field="szi", channel=channel
                ),
                funding_rate=_decimal(
                    _required(funding, "fundingRate", channel=channel),
                    field="fundingRate",
                    channel=channel,
                ),
            ),
        )
    fills = data.get("fills")
    if fills is not None:
        return tuple(
            event
            for index, fill in enumerate(_sequence(fills, code="invalid_fills", channel=channel))
            for event in _parse_fill(fill, metadata, index)
        )
    return ()


def _parse_order_updates(data: Any, metadata: RawFrameMetadata) -> tuple[MarketEvent, ...]:
    channel = "orderUpdates"
    events: list[MarketEvent] = []
    for index, value in enumerate(_sequence(data, code="invalid_order_updates", channel=channel)):
        update = _mapping(value, code="invalid_order_update", channel=channel)
        order = _mapping(
            _required(update, "order", channel=channel), code="invalid_order", channel=channel
        )
        _coin(order, channel=channel)
        timestamp_ms = _integer(
            order.get("timestamp", update.get("statusTimestamp")),
            field="timestamp",
            channel=channel,
        )
        order_id = str(_required(order, "oid", channel=channel))
        events.append(
            AccountOrderUpdateEvent(
                header=_header(
                    metadata,
                    event_id=_event_id(channel, f"{order_id}-{index}", metadata.payload_sha256),
                    event_ts_ns=timestamp_ms * 1_000_000,
                    exchange_timestamp=True,
                ),
                venue_order_id=order_id,
                client_order_id=(str(order["cloid"]) if order.get("cloid") else None),
                side=_side(_required(order, "side", channel=channel), channel=channel),
                limit_price=_decimal(
                    _required(order, "limitPx", channel=channel), field="limitPx", channel=channel
                ),
                remaining_size=_decimal(
                    _required(order, "sz", channel=channel), field="sz", channel=channel
                ),
                original_size=_decimal(
                    _required(order, "origSz", channel=channel), field="origSz", channel=channel
                ),
                status=str(_required(update, "status", channel=channel)),
                reduce_only=_boolean(
                    _required(order, "reduceOnly", channel=channel),
                    field="reduceOnly",
                    channel=channel,
                ),
            )
        )
    return tuple(events)


def parse_frame(payload: bytes, metadata: RawFrameMetadata) -> ParsedFrame:
    if hashlib.sha256(payload).hexdigest() != metadata.payload_sha256:
        raise ProtocolError("payload_digest_mismatch", "payload does not match raw metadata")
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("invalid_json", f"invalid JSON frame: {exc}") from exc
    message = _mapping(decoded, code="invalid_message", channel="unknown")
    channel_value = message.get("channel")
    if not isinstance(channel_value, str):
        raise ProtocolError("missing_channel", "frame has no string channel")
    channel = channel_value
    if channel in {"pong", "subscriptionResponse"}:
        return ParsedFrame(channel, (), is_control=True)
    data = _required(message, "data", channel=channel)
    if channel == "l2Book":
        return ParsedFrame(
            channel, (_parse_book(_mapping(data, code="invalid_data", channel=channel), metadata),)
        )
    if channel == "bbo":
        return ParsedFrame(
            channel, (_parse_bbo(_mapping(data, code="invalid_data", channel=channel), metadata),)
        )
    if channel == "trades":
        trades = _sequence(data, code="invalid_trades", channel=channel)
        return ParsedFrame(
            channel,
            tuple(_parse_trade(value, metadata, index) for index, value in enumerate(trades)),
        )
    if channel == "activeAssetCtx":
        return ParsedFrame(
            channel,
            _parse_asset_context(_mapping(data, code="invalid_data", channel=channel), metadata),
        )
    if channel == "user":
        return ParsedFrame(
            channel,
            _parse_user_event(_mapping(data, code="invalid_data", channel=channel), metadata),
        )
    if channel == "userFills":
        wrapper = _mapping(data, code="invalid_data", channel=channel)
        fills = _sequence(
            _required(wrapper, "fills", channel=channel), code="invalid_fills", channel=channel
        )
        return ParsedFrame(
            channel,
            tuple(
                event
                for index, fill in enumerate(fills)
                for event in _parse_fill(fill, metadata, index)
            ),
        )
    if channel == "orderUpdates":
        return ParsedFrame(channel, _parse_order_updates(data, metadata))
    raise ProtocolError("unsupported_channel", f"unsupported channel: {channel}", channel=channel)
