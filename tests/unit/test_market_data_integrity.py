from __future__ import annotations

import hashlib
from decimal import Decimal

from aiquanttrader.domain.data import QualityIssueKind, RawFrameMetadata
from aiquanttrader.domain.market import AggressorSide, EventHeader, TradeEvent
from aiquanttrader.market_data.integrity import IntegrityTracker
from aiquanttrader.market_data.protocol import ParsedFrame, ProtocolError


def metadata(receive_ts_ns: int, payload: bytes = b"{}") -> RawFrameMetadata:
    return RawFrameMetadata(
        receive_ts_ns=receive_ts_ns,
        monotonic_ts_ns=receive_ts_ns,
        connection_id="ws-integrity",
        subscription_id="public-btc",
        transport="text",
        payload_size=len(payload),
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        recorder_version="test-v1",
    )


def trade(event_ts_ns: int, trade_id: str) -> TradeEvent:
    return TradeEvent(
        header=EventHeader(
            event_id=f"trade-{trade_id}-{event_ts_ns}",
            event_ts_ns=event_ts_ns,
            receive_ts_ns=event_ts_ns + 1,
            connection_id="ws-integrity",
        ),
        trade_id=trade_id,
        price=Decimal("100000"),
        size=Decimal("0.01"),
        aggressor=AggressorSide.BUYER,
        transaction_hash="0xabc",
    )


def test_integrity_tracker_observes_cadence_regression_and_duplicates() -> None:
    tracker = IntegrityTracker(cadence_threshold_ns=10, duplicate_window=10)
    first = ParsedFrame("trades", (trade(100, "1"),))
    second = ParsedFrame("trades", (trade(90, "1"),))

    assert len(tracker.observe_frame(first, metadata(101))) == 1
    assert tracker.observe_frame(second, metadata(120)) == ()
    assert [issue.kind for issue in tracker.issues] == [
        QualityIssueKind.CADENCE_ANOMALY,
        QualityIssueKind.TIMESTAMP_REGRESSION,
        QualityIssueKind.DUPLICATE,
    ]


def test_protocol_failure_is_counted_without_payload_content() -> None:
    tracker = IntegrityTracker()
    issue = tracker.record_parse_failure(
        ProtocolError("missing_field", "secret content must not be retained", channel="trades"),
        metadata(100, b"sensitive"),
    )

    assert issue.kind is QualityIssueKind.SCHEMA_ERROR
    assert issue.code == "missing_field"
    assert "sensitive" not in issue.model_dump_json()
