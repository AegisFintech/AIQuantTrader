"""Explicit data-quality accounting without claiming unavailable sequence guarantees."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field

from pydantic import ValidationError

from aiquanttrader.domain.data import QualityIssue, QualityIssueKind, RawFrameMetadata
from aiquanttrader.domain.market import MarketEvent, TradeEvent
from aiquanttrader.market_data.protocol import ParsedFrame, ProtocolError


@dataclass(slots=True)
class IntegrityTracker:
    cadence_threshold_ns: int = 15_000_000_000
    duplicate_window: int = 1_000_000
    issues: list[QualityIssue] = field(default_factory=list)
    _last_event_ts: dict[str, int] = field(default_factory=dict)
    _last_receive_ts: dict[str, int] = field(default_factory=dict)
    _trade_keys: OrderedDict[str, None] = field(default_factory=OrderedDict)

    def observe_frame(
        self,
        frame: ParsedFrame,
        metadata: RawFrameMetadata,
    ) -> tuple[MarketEvent, ...]:
        last_receive = self._last_receive_ts.get(frame.channel)
        if (
            last_receive is not None
            and metadata.receive_ts_ns - last_receive > self.cadence_threshold_ns
        ):
            self.issues.append(
                QualityIssue(
                    kind=QualityIssueKind.CADENCE_ANOMALY,
                    receive_ts_ns=metadata.receive_ts_ns,
                    channel=frame.channel,
                    code="receive_gap_exceeded",
                    payload_sha256=metadata.payload_sha256,
                )
            )
        self._last_receive_ts[frame.channel] = metadata.receive_ts_ns

        accepted: list[MarketEvent] = []
        for event in frame.events:
            event_type = event.event_type
            event_ts_ns = event.header.event_ts_ns
            previous = self._last_event_ts.get(event_type)
            if previous is not None and event_ts_ns < previous:
                self.issues.append(
                    QualityIssue(
                        kind=QualityIssueKind.TIMESTAMP_REGRESSION,
                        receive_ts_ns=metadata.receive_ts_ns,
                        event_ts_ns=event_ts_ns,
                        channel=frame.channel,
                        code="event_time_regressed",
                        payload_sha256=metadata.payload_sha256,
                    )
                )
            self._last_event_ts[event_type] = max(previous or 0, event_ts_ns)
            if isinstance(event, TradeEvent) and self._is_duplicate_trade(event):
                self.issues.append(
                    QualityIssue(
                        kind=QualityIssueKind.DUPLICATE,
                        receive_ts_ns=metadata.receive_ts_ns,
                        event_ts_ns=event_ts_ns,
                        channel=frame.channel,
                        code="duplicate_trade_key",
                        payload_sha256=metadata.payload_sha256,
                    )
                )
                continue
            accepted.append(event)
        return tuple(accepted)

    def record_parse_failure(
        self,
        error: ProtocolError | ValidationError,
        metadata: RawFrameMetadata,
    ) -> QualityIssue:
        if isinstance(error, ProtocolError):
            channel = error.channel
            code = error.code
            kind = QualityIssueKind.SCHEMA_ERROR
        else:
            message = str(error).lower()
            channel = "unknown"
            if "locked or crossed" in message or "best bid must be below" in message:
                kind = QualityIssueKind.CROSSED_BOOK
                code = "crossed_book"
            else:
                kind = QualityIssueKind.SCHEMA_ERROR
                code = "domain_validation_failed"
        issue = QualityIssue(
            kind=kind,
            receive_ts_ns=metadata.receive_ts_ns,
            channel=channel,
            code=code,
            payload_sha256=metadata.payload_sha256,
        )
        self.issues.append(issue)
        return issue

    def _is_duplicate_trade(self, event: TradeEvent) -> bool:
        key = f"{event.trade_id}:{event.transaction_hash or ''}"
        if key in self._trade_keys:
            self._trade_keys.move_to_end(key)
            return True
        self._trade_keys[key] = None
        if len(self._trade_keys) > self.duplicate_window:
            self._trade_keys.popitem(last=False)
        return False
