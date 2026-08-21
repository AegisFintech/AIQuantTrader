from __future__ import annotations

import asyncio
import json
import time
from decimal import Decimal
from pathlib import Path
from types import TracebackType

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from aiquanttrader.config import load_config
from aiquanttrader.domain.market import (
    AggressorSide,
    BboEvent,
    BookLevel,
    EventHeader,
    FundingEvent,
    L2BookSnapshot,
    MarkPriceEvent,
    TradeEvent,
)
from aiquanttrader.market_data.protocol import ParsedFrame
from aiquanttrader.paper.config import load_paper_artifacts
from aiquanttrader.paper.journal import PaperJournal
from aiquanttrader.paper.models import PaperRuntimeStatus
from aiquanttrader.paper.service import PaperLiveService
from aiquanttrader.risk.kill_switch import KillSwitchStore


class OneFrameSocket:
    def __init__(self, payload: bytes | tuple[bytes, ...], stop: asyncio.Event) -> None:
        self.payloads = payload if isinstance(payload, tuple) else (payload,)
        self.index = 0
        self.stop = stop

    async def send(self, _message: str) -> None:
        return None

    async def recv(self, *, decode: bool | None = None) -> bytes:
        assert decode is False
        payload = self.payloads[self.index]
        self.index += 1
        if self.index == len(self.payloads):
            self.stop.set()
        return payload


class OneFrameContext:
    def __init__(self, socket: OneFrameSocket) -> None:
        self.socket = socket

    async def __aenter__(self) -> OneFrameSocket:
        return self.socket

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


def test_live_service_creates_credential_free_manifest_status_and_metrics(
    tmp_path: Path, config_dir: Path
) -> None:
    bundle = load_config(
        config_dir,
        "paper",
        environ={
            "AQT_NATIVE__STORAGE__DATA_ROOT": str(tmp_path / "data"),
            "AQT_NATIVE__STORAGE__STATE_ROOT": str(tmp_path / "state"),
        },
    )
    artifacts = load_paper_artifacts(config_dir, bundle)
    journal = PaperJournal((tmp_path / "state" / "paper" / "paper.sqlite3").resolve())
    registry = CollectorRegistry()
    service = PaperLiveService(
        bundle=bundle,
        artifacts=artifacts,
        journal=journal,
        kill_switch=KillSwitchStore((tmp_path / "state" / "paper" / "kill.json").resolve()),
        code_identity="integration-commit",
        registry=registry,
    )
    receive = 1_800_000_000_000_000_000
    step = 400_000_000
    header = EventHeader(
        event_id="context",
        event_ts_ns=receive,
        receive_ts_ns=receive,
        connection_id="paper-service",
    )
    context = ParsedFrame(
        channel="activeAssetCtx",
        events=(
            MarkPriceEvent(
                header=header.model_copy(update={"event_id": "mark"}),
                mark_price=Decimal("100"),
            ),
            FundingEvent(
                header=header.model_copy(update={"event_id": "funding"}),
                funding_rate=Decimal("0.00001"),
                next_funding_ts_ns=receive + 3_600_000_000_000,
            ),
        ),
    )
    book = ParsedFrame(
        channel="l2Book",
        events=(
            L2BookSnapshot(
                header=header.model_copy(update={"event_id": "book", "receive_ts_ns": receive + 1}),
                bids=(BookLevel(price=Decimal("99"), size=Decimal("1")),),
                asks=(BookLevel(price=Decimal("101"), size=Decimal("1")),),
            ),
        ),
    )
    asyncio.run(service.consume_frame(context))
    asyncio.run(service.consume_frame(book))
    trades = ParsedFrame(
        channel="trades",
        events=(
            TradeEvent(
                header=header.model_copy(
                    update={
                        "event_id": "stale-bootstrap-trade",
                        "event_ts_ns": receive - 43_000_000_000,
                        "receive_ts_ns": receive + 2 * step,
                    }
                ),
                trade_id="stale-bootstrap-trade",
                price=Decimal("100"),
                size=Decimal("0.2"),
                aggressor=AggressorSide.BUYER,
            ),
            TradeEvent(
                header=header.model_copy(
                    update={
                        "event_id": "fresh-trade",
                        "event_ts_ns": receive + 2 * step,
                        "receive_ts_ns": receive + 2 * step,
                    }
                ),
                trade_id="fresh-trade",
                price=Decimal("100"),
                size=Decimal("0.1"),
                aggressor=AggressorSide.SELLER,
            ),
        ),
    )
    next_book = ParsedFrame(
        channel="l2Book",
        events=(
            book.events[0].model_copy(
                update={
                    "header": header.model_copy(
                        update={
                            "event_id": "book-next",
                            "event_ts_ns": receive + 3 * step,
                            "receive_ts_ns": receive + 3 * step,
                        }
                    )
                }
            ),
        ),
    )
    asyncio.run(service.consume_frame(trades))
    asyncio.run(service.consume_frame(next_book))
    accepted_depth_wall_ns = service._last_l2_depth_wall_ns
    assert accepted_depth_wall_ns is not None
    stale_book = ParsedFrame(
        channel="l2Book",
        events=(
            book.events[0].model_copy(
                update={
                    "header": header.model_copy(
                        update={
                            "event_id": "stale-book",
                            "event_ts_ns": receive - 7_000_000_000,
                            "receive_ts_ns": receive + 4 * step,
                        }
                    )
                }
            ),
        ),
    )
    recovery_book = ParsedFrame(
        channel="l2Book",
        events=(
            book.events[0].model_copy(
                update={
                    "header": header.model_copy(
                        update={
                            "event_id": "recovery-book",
                            "event_ts_ns": receive + 5 * step,
                            "receive_ts_ns": receive + 5 * step,
                        }
                    )
                }
            ),
        ),
    )
    asyncio.run(service.consume_frame(stale_book))
    assert service._last_l2_depth_wall_ns == accepted_depth_wall_ns
    asyncio.run(service.consume_frame(recovery_book))
    assert service._last_l2_depth_wall_ns is not None
    assert service._last_l2_depth_wall_ns >= accepted_depth_wall_ns
    live_bbo = ParsedFrame(
        channel="bbo",
        events=(
            BboEvent(
                header=header.model_copy(
                    update={
                        "event_id": "live-bbo",
                        "event_ts_ns": receive + 6 * step,
                        "receive_ts_ns": receive + 6 * step,
                    }
                ),
                bid_price=Decimal("99.5"),
                bid_size=Decimal("1.2"),
                ask_price=Decimal("100.5"),
                ask_size=Decimal("0.8"),
            ),
        ),
    )
    refreshed_context = ParsedFrame(
        channel="activeAssetCtx",
        events=(
            MarkPriceEvent(
                header=header.model_copy(
                    update={
                        "event_id": "mark-refresh",
                        "event_ts_ns": receive + 6 * step,
                        "receive_ts_ns": receive + 6 * step,
                    }
                ),
                mark_price=Decimal("100"),
            ),
        ),
    )
    asyncio.run(service.consume_frame(refreshed_context))
    asyncio.run(service.consume_frame(live_bbo))
    assert service._last_bbo_wall_ns is not None

    manifest = journal.latest_manifest()
    assert manifest is not None
    assert manifest.credential_capability == "none"
    assert manifest.code_identity == "integration-commit"
    status = PaperRuntimeStatus.model_validate_json(service.status_path.read_bytes())
    assert status.status == "warming"
    assert status.feed_connected
    assert status.feed_freshness.ready
    assert status.feed_freshness.socket_connected
    with pytest.raises(ValueError, match="projection does not match"):
        PaperRuntimeStatus.model_validate(
            {**status.model_dump(mode="json"), "feed_connected": False}
        )
    assert status.account is not None
    metrics = generate_latest(registry)
    assert b"aqt_paper_market_states_total 3.0" in metrics
    assert b"aqt_paper_market_state_l2_depth_used 1.0" in metrics
    assert b"aqt_paper_market_state_depth_levels 2.0" in metrics
    assert b"aqt_paper_stale_trades_excluded_total 1.0" in metrics
    assert b"aqt_paper_stale_books_excluded_total 1.0" in metrics
    assert b'aqt_paper_feed_component_fresh{component="socket"} 1.0' in metrics
    assert b'aqt_paper_feed_component_fresh{component="bbo"} 1.0' in metrics
    assert b'aqt_paper_feed_component_fresh{component="l2_depth"} 1.0' in metrics
    assert b"aqt_paper_feed_stale_after_seconds 1.5" in metrics
    assert b"aqt_paper_feed_depth_stale_after_seconds 2.0" in metrics
    assert b'aqt_paper_feed_blocked{reason="none"} 1.0' in metrics
    assert b"aqt_paper_equity_usd 100000.0" in metrics
    assert b"aqt_paper_drawdown_fraction 0.0" in metrics
    assert b"aqt_paper_daily_loss_fraction 0.0" in metrics
    journal.close()


def test_service_run_archives_live_frame_and_stops_cleanly(
    tmp_path: Path, config_dir: Path
) -> None:
    now_ms = time.time_ns() // 1_000_000
    payload = json.dumps(
        {
            "channel": "l2Book",
            "data": {
                "coin": "BTC",
                "time": now_ms,
                "levels": [
                    [{"px": "99999", "sz": "1", "n": 1}],
                    [{"px": "100001", "sz": "1", "n": 1}],
                ],
            },
        }
    ).encode()

    async def run() -> tuple[PaperLiveService, PaperJournal]:
        stop = asyncio.Event()
        socket = OneFrameSocket(payload, stop)
        bundle = load_config(
            config_dir,
            "paper",
            environ={
                "AQT_NATIVE__STORAGE__DATA_ROOT": str(tmp_path / "data"),
                "AQT_NATIVE__STORAGE__STATE_ROOT": str(tmp_path / "state"),
                "AQT_NATIVE__MARKET_DATA__MINIMUM_FREE_BYTES": "67108864",
                "AQT_NATIVE__MARKET_DATA__MINIMUM_FREE_FRACTION": "0.01",
            },
        )
        artifacts = load_paper_artifacts(config_dir, bundle)
        journal = PaperJournal((tmp_path / "state" / "paper" / "paper.sqlite3").resolve())
        service = PaperLiveService(
            bundle=bundle,
            artifacts=artifacts,
            journal=journal,
            kill_switch=KillSwitchStore((tmp_path / "state" / "paper" / "kill.json").resolve()),
            code_identity="service-run-test",
            registry=CollectorRegistry(),
            socket_factory=lambda _url, _size: OneFrameContext(socket),
        )
        await service.run(stop)
        return service, journal

    service, journal = asyncio.run(run())
    assert service.engine is not None
    status = PaperRuntimeStatus.model_validate_json(service.status_path.read_bytes())
    assert status.status == "stopped"
    assert not status.feed_connected
    assert status.feed_freshness.blocking_reason.value == "socket_disconnected"
    assert service.engine is not None and not service.engine.feed_connected
    raw_files = list((tmp_path / "data" / "raw").rglob("*.raw.zst"))
    assert len(raw_files) == 1

    async def watchdog_tick() -> None:
        now_ns = time.time_ns()
        service._socket_connected = True
        service._last_frame_wall_ns = now_ns
        service._last_context_wall_ns = now_ns
        service._last_bbo_wall_ns = now_ns
        service._last_l2_depth_wall_ns = now_ns
        stop = asyncio.Event()
        task = asyncio.create_task(service._watchdog(stop))
        await asyncio.sleep(0.3)
        stop.set()
        await task

    asyncio.run(watchdog_tick())
    refreshed = PaperRuntimeStatus.model_validate_json(service.status_path.read_bytes())
    assert refreshed.status in {"warming", "ready"}
    assert refreshed.feed_connected

    async def stale_watchdog_tick() -> None:
        service._socket_connected = True
        service._last_frame_wall_ns = (
            time.time_ns() - service.bundle.settings.risk.public_data_stale_after_ms * 1_000_000 - 1
        )
        service._last_context_wall_ns = time.time_ns()
        service._last_bbo_wall_ns = service._last_frame_wall_ns
        service._last_l2_depth_wall_ns = service._last_frame_wall_ns
        stop = asyncio.Event()
        task = asyncio.create_task(service._watchdog(stop))
        await asyncio.sleep(0.3)
        stop.set()
        await task

    asyncio.run(stale_watchdog_tick())
    stale = PaperRuntimeStatus.model_validate_json(service.status_path.read_bytes())
    assert stale.status == "degraded"
    assert not stale.feed_connected
    assert stale.feed_freshness.blocking_reason.value == "public_frame_stale"
    journal.close()


def test_service_projects_exchange_source_age_into_bbo_and_depth_freshness(
    tmp_path: Path, config_dir: Path
) -> None:
    wall_ns = [20_000_000_000]
    bundle = load_config(
        config_dir,
        "paper",
        environ={
            "AQT_NATIVE__STORAGE__DATA_ROOT": str(tmp_path / "data"),
            "AQT_NATIVE__STORAGE__STATE_ROOT": str(tmp_path / "state"),
        },
    )
    journal = PaperJournal((tmp_path / "state" / "paper.sqlite3").resolve())
    service = PaperLiveService(
        bundle=bundle,
        artifacts=load_paper_artifacts(config_dir, bundle),
        journal=journal,
        kill_switch=KillSwitchStore((tmp_path / "state" / "kill.json").resolve()),
        code_identity="source-age-test",
        registry=CollectorRegistry(),
        clock_ns=lambda: wall_ns[0],
    )
    context_header = EventHeader(
        event_id="source-age-context",
        event_ts_ns=wall_ns[0],
        receive_ts_ns=wall_ns[0],
        connection_id="source-age-test",
    )
    asyncio.run(
        service.consume_frame(
            ParsedFrame(
                channel="activeAssetCtx",
                events=(MarkPriceEvent(header=context_header, mark_price=Decimal("100")),),
            )
        )
    )
    delayed_book_receive = wall_ns[0] + 1
    asyncio.run(
        service.consume_frame(
            ParsedFrame(
                channel="l2Book",
                events=(
                    L2BookSnapshot(
                        header=context_header.model_copy(
                            update={
                                "event_id": "source-age-book",
                                "event_ts_ns": delayed_book_receive - 1_800_000_000,
                                "receive_ts_ns": delayed_book_receive,
                            }
                        ),
                        bids=(BookLevel(price=Decimal("99"), size=Decimal("1")),),
                        asks=(BookLevel(price=Decimal("101"), size=Decimal("1")),),
                    ),
                ),
            )
        )
    )

    wall_ns[0] += 1_100_000_000
    current_header = context_header.model_copy(
        update={
            "event_id": "source-age-context-refresh",
            "event_ts_ns": wall_ns[0],
            "receive_ts_ns": wall_ns[0],
        }
    )
    asyncio.run(
        service.consume_frame(
            ParsedFrame(
                channel="activeAssetCtx",
                events=(MarkPriceEvent(header=current_header, mark_price=Decimal("100")),),
            )
        )
    )
    asyncio.run(
        service.consume_frame(
            ParsedFrame(
                channel="bbo",
                events=(
                    BboEvent(
                        header=current_header.model_copy(
                            update={
                                "event_id": "source-age-bbo",
                                "event_ts_ns": wall_ns[0] - 1_800_000_000,
                            }
                        ),
                        bid_price=Decimal("99.5"),
                        bid_size=Decimal("1"),
                        ask_price=Decimal("100.5"),
                        ask_size=Decimal("1"),
                    ),
                ),
            )
        )
    )

    status = PaperRuntimeStatus.model_validate_json(service.status_path.read_bytes())
    assert not status.feed_connected
    assert status.feed_freshness.blocking_reason.value == "bbo_stale"
    assert status.feed_freshness.bbo_age_ms == 1_800
    assert status.feed_freshness.l2_depth_state.value == "stale"
    assert service.engine is not None
    journal.close()


def test_service_treats_consumer_clock_failure_as_fatal(tmp_path: Path, config_dir: Path) -> None:
    now_ms = time.time_ns() // 1_000_000

    def book_payload(timestamp_ms: int) -> bytes:
        return json.dumps(
            {
                "channel": "l2Book",
                "data": {
                    "coin": "BTC",
                    "time": timestamp_ms,
                    "levels": [
                        [{"px": "99999", "sz": "1", "n": 1}],
                        [{"px": "100001", "sz": "1", "n": 1}],
                    ],
                },
            }
        ).encode()

    payloads = (book_payload(now_ms), book_payload(now_ms + 10_000))

    async def run() -> tuple[PaperLiveService, tuple[str, ...]]:
        stop = asyncio.Event()
        socket = OneFrameSocket(payloads, stop)
        bundle = load_config(
            config_dir,
            "paper",
            environ={
                "AQT_NATIVE__STORAGE__DATA_ROOT": str(tmp_path / "data"),
                "AQT_NATIVE__STORAGE__STATE_ROOT": str(tmp_path / "state"),
                "AQT_NATIVE__MARKET_DATA__MINIMUM_FREE_BYTES": "67108864",
                "AQT_NATIVE__MARKET_DATA__MINIMUM_FREE_FRACTION": "0.01",
            },
        )
        journal = PaperJournal((tmp_path / "state" / "paper" / "paper.sqlite3").resolve())
        service = PaperLiveService(
            bundle=bundle,
            artifacts=load_paper_artifacts(config_dir, bundle),
            journal=journal,
            kill_switch=KillSwitchStore((tmp_path / "state" / "paper" / "kill.json").resolve()),
            code_identity="service-failure-test",
            registry=CollectorRegistry(),
            socket_factory=lambda _url, _size: OneFrameContext(socket),
        )
        with pytest.raises(RuntimeError, match="consumer"):
            await service.run(stop)
        assert service.engine is not None
        invalidating = journal.statistics(service.engine.manifest.run_id).invalidating_events
        journal.close()
        return service, invalidating

    service, invalidating = asyncio.run(run())
    status = PaperRuntimeStatus.model_validate_json(service.status_path.read_bytes())
    assert status.status == "failed"
    assert status.last_error_code == "frameconsumererror"
    assert invalidating == ("service_failure",)
