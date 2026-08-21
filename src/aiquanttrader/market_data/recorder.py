"""Reconnect-safe Hyperliquid public WebSocket recorder."""

from __future__ import annotations

import asyncio
import random
import shutil
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from types import TracebackType
from typing import Literal, Protocol

import websockets
from pydantic import ValidationError

from aiquanttrader.config.models import MarketDataConfig
from aiquanttrader.domain.data import RecorderState, SegmentFinalizationReason
from aiquanttrader.market_data.catalog import ManifestCatalog
from aiquanttrader.market_data.integrity import IntegrityTracker
from aiquanttrader.market_data.io import atomic_replace_bytes
from aiquanttrader.market_data.metrics import RecorderMetrics
from aiquanttrader.market_data.protocol import (
    ParsedFrame,
    ProtocolError,
    application_ping,
    parse_frame,
)
from aiquanttrader.market_data.raw import RawSegmentWriter


class WebSocketConnection(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self, *, decode: bool | None = None) -> str | bytes: ...


class SocketContext(Protocol):
    async def __aenter__(self) -> WebSocketConnection: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


SocketFactory = Callable[[str, int], SocketContext]
FrameConsumer = Callable[[ParsedFrame], Awaitable[None]]
ConnectionObserver = Callable[[bool], None]


class StaleFeedError(TimeoutError):
    pass


class DiskPressureError(OSError):
    pass


class FrameConsumerError(RuntimeError):
    pass


class OutboundRateLimiter:
    def __init__(self, maximum: int, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.maximum = maximum
        self.clock = clock
        self._sent: deque[float] = deque()

    async def acquire(self) -> None:
        while True:
            now = self.clock()
            while self._sent and now - self._sent[0] >= 60:
                self._sent.popleft()
            if len(self._sent) < self.maximum:
                self._sent.append(now)
                return
            await asyncio.sleep(max(0.001, 60 - (now - self._sent[0])))


def default_socket_factory(url: str, max_frame_bytes: int) -> SocketContext:
    return websockets.connect(
        url,
        max_size=max_frame_bytes,
        ping_interval=None,
        ping_timeout=None,
        close_timeout=5,
        open_timeout=10,
    )


class MarketDataRecorder:
    def __init__(
        self,
        *,
        websocket_url: str,
        network: Literal["testnet", "mainnet"],
        environment: str,
        config: MarketDataConfig,
        data_root: Path,
        state_root: Path,
        catalog: ManifestCatalog,
        metrics: RecorderMetrics,
        socket_factory: SocketFactory = default_socket_factory,
        wall_clock_ns: Callable[[], int] = time.time_ns,
        monotonic_ns: Callable[[], int] = time.monotonic_ns,
        rng: random.Random | None = None,
        frame_consumer: FrameConsumer | None = None,
        connection_observer: ConnectionObserver | None = None,
    ) -> None:
        if network not in {"mainnet", "testnet"}:
            raise ValueError(f"unsupported Hyperliquid network: {network}")
        self.websocket_url = websocket_url
        self.network = network
        self.environment = environment
        self.config = config
        self.data_root = data_root.resolve()
        self.state_root = state_root.resolve()
        self.catalog = catalog
        self.metrics = metrics
        self.socket_factory = socket_factory
        self.wall_clock_ns = wall_clock_ns
        self.monotonic_ns = monotonic_ns
        self.rng = rng or random.SystemRandom()
        self.frame_consumer = frame_consumer
        self.connection_observer = connection_observer
        self.state_path = self.state_root / "market-data" / "recorder-state.json"
        self.reconnect_count = 0
        self.last_frame_ts_ns: int | None = None
        self._connection_id: str | None = None
        self._segment_id: str | None = None
        self._rate_limiter = OutboundRateLimiter(config.outbound_messages_per_minute)

    async def run(self, stop: asyncio.Event) -> None:
        if not self.config.enabled:
            raise ValueError("market-data recorder is disabled by configuration")
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.state_root.mkdir(parents=True, exist_ok=True)
        self._set_connection_state(False)
        self._write_state("starting")
        backoff_ms = self.config.reconnect_initial_ms
        while not stop.is_set():
            try:
                await self._record_connection(stop)
                backoff_ms = self.config.reconnect_initial_ms
            except DiskPressureError:
                self._write_state("failed", "disk_pressure")
                raise
            except FrameConsumerError:
                self._write_state("failed", "frame_consumer_error")
                raise
            except asyncio.CancelledError:
                self._write_state("stopped")
                raise
            except Exception as exc:
                reason = "stale_feed" if isinstance(exc, StaleFeedError) else "connection_error"
                self.reconnect_count += 1
                self.metrics.reconnects.labels(reason=reason).inc()
                self._write_state("reconnecting", reason)
                if stop.is_set():
                    break
                jitter = float(self.config.reconnect_jitter_fraction)
                multiplier = 1 + self.rng.uniform(-jitter, jitter)
                await asyncio.sleep(max(0, backoff_ms * multiplier) / 1_000)
                backoff_ms = min(backoff_ms * 2, self.config.reconnect_max_ms)
        self._set_connection_state(False)
        self._write_state("stopped")

    async def _record_connection(self, stop: asyncio.Event) -> None:
        connection_id = f"ws-{uuid.uuid4().hex}"
        self._connection_id = connection_id
        started_at_ns = self.wall_clock_ns()
        writer = RawSegmentWriter(
            self.data_root,
            network=self.network,
            connection_id=connection_id,
            started_at_ns=started_at_ns,
            sync_every_records=self.config.sync_every_records,
            max_frame_bytes=self.config.max_frame_bytes,
        )
        self._segment_id = writer.segment_id
        tracker = IntegrityTracker(
            cadence_threshold_ns=self.config.stale_after_seconds * 1_000_000_000
        )
        reason = SegmentFinalizationReason.DISCONNECT
        try:
            async with self.socket_factory(
                self.websocket_url, self.config.max_frame_bytes
            ) as socket:
                self._set_connection_state(True)
                for message in _subscriptions(self.config.public_channels):
                    await self._rate_limiter.acquire()
                    await socket.send(message)
                self._write_state("connected")
                last_frame_monotonic = self.monotonic_ns()
                next_ping = last_frame_monotonic + self.config.ping_interval_seconds * 1_000_000_000
                rotation_at = started_at_ns + self.config.segment_duration_seconds * 1_000_000_000
                while not stop.is_set():
                    now_mono = self.monotonic_ns()
                    stale_at = (
                        last_frame_monotonic + self.config.stale_after_seconds * 1_000_000_000
                    )
                    timeout = max(0.001, (min(next_ping, stale_at) - now_mono) / 1e9)
                    try:
                        async with asyncio.timeout(timeout):
                            received = await socket.recv(decode=False)
                    except TimeoutError:
                        now_mono = self.monotonic_ns()
                        if now_mono >= stale_at:
                            reason = SegmentFinalizationReason.STALE_FEED
                            self.metrics.issues.labels(
                                kind="silence", code="stale_feed_timeout"
                            ).inc()
                            raise StaleFeedError(
                                "no frame received within stale-data window"
                            ) from None
                        await self._rate_limiter.acquire()
                        await socket.send(application_ping())
                        next_ping = now_mono + self.config.ping_interval_seconds * 1_000_000_000
                        continue

                    payload = received.encode("utf-8") if isinstance(received, str) else received
                    receive_ts_ns = self.wall_clock_ns()
                    monotonic_ts_ns = self.monotonic_ns()
                    transport: Literal["text", "binary"] = (
                        "text" if isinstance(received, str) else "binary"
                    )
                    metadata = writer.append(
                        payload,
                        receive_ts_ns=receive_ts_ns,
                        monotonic_ts_ns=monotonic_ts_ns,
                        transport=transport,
                    )
                    self.last_frame_ts_ns = receive_ts_ns
                    last_frame_monotonic = monotonic_ts_ns
                    self.metrics.frames.labels(transport=transport).inc()
                    self.metrics.bytes.inc(len(payload))
                    self.metrics.last_frame_seconds.set(receive_ts_ns / 1e9)
                    self._check_disk()
                    try:
                        frame = parse_frame(payload, metadata)
                        previous_count = len(tracker.issues)
                        tracker.observe_frame(frame, metadata)
                        for issue in tracker.issues[previous_count:]:
                            self.metrics.issues.labels(kind=issue.kind.value, code=issue.code).inc()
                        if self.frame_consumer is not None:
                            try:
                                await self.frame_consumer(frame)
                            except Exception as exc:
                                raise FrameConsumerError(
                                    f"live frame consumer failed: {type(exc).__name__}"
                                ) from exc
                    except (ProtocolError, ValidationError) as exc:
                        issue = tracker.record_parse_failure(exc, metadata)
                        self.metrics.issues.labels(kind=issue.kind.value, code=issue.code).inc()
                    self._write_state("connected")
                    if receive_ts_ns >= rotation_at:
                        reason = SegmentFinalizationReason.ROTATION
                        break
                if stop.is_set():
                    reason = SegmentFinalizationReason.SHUTDOWN
        except DiskPressureError:
            reason = SegmentFinalizationReason.DISK_PRESSURE
            raise
        except StaleFeedError:
            raise
        except asyncio.CancelledError:
            reason = SegmentFinalizationReason.SHUTDOWN
            raise
        except (OSError, websockets.ConnectionClosed):
            reason = SegmentFinalizationReason.DISCONNECT
            raise
        except BaseException:
            reason = SegmentFinalizationReason.ERROR
            raise
        finally:
            self._set_connection_state(False)
            finalized = writer.finalize(reason)
            self.metrics.segments.labels(reason=reason.value).inc()
            self.catalog.register_raw(finalized.manifest)

    def _set_connection_state(self, connected: bool) -> None:
        self.metrics.connected.set(int(connected))
        if self.connection_observer is not None:
            try:
                self.connection_observer(connected)
            except Exception:
                self.metrics.issues.labels(kind="consumer", code="connection_observer_error").inc()
                self.connection_observer = None

    def _check_disk(self) -> None:
        usage = shutil.disk_usage(self.data_root)
        self.metrics.free_bytes.set(usage.free)
        if usage.free < self.config.minimum_free_bytes or (
            usage.free / usage.total < float(self.config.minimum_free_fraction)
        ):
            self.metrics.issues.labels(kind="disk_pressure", code="free_space_below_limit").inc()
            raise DiskPressureError("market-data filesystem is below its free-space threshold")

    def _write_state(
        self,
        status: Literal["starting", "connected", "reconnecting", "stopped", "failed"],
        error: str | None = None,
    ) -> None:
        state = RecorderState(
            status=status,
            environment=self.environment,
            network=self.network,
            connection_id=self._connection_id,
            heartbeat_ts_ns=self.wall_clock_ns(),
            last_frame_ts_ns=self.last_frame_ts_ns,
            current_segment_id=self._segment_id,
            reconnect_count=self.reconnect_count,
            last_error_code=error,
        )
        atomic_replace_bytes(self.state_path, state.canonical_bytes() + b"\n")


def _subscriptions(channels: Sequence[str]) -> tuple[str, ...]:
    from aiquanttrader.market_data.protocol import subscription_messages

    return subscription_messages(channels)
