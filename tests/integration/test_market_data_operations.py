from __future__ import annotations

import asyncio
import gzip
import io
import json
import urllib.request
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import TracebackType

import pytest
from prometheus_client import generate_latest

from aiquanttrader.config.models import MarketDataConfig
from aiquanttrader.domain.data import RecorderState
from aiquanttrader.market_data.catalog import CatalogLockedError, ManifestCatalog
from aiquanttrader.market_data.metrics import RecorderMetrics
from aiquanttrader.market_data.raw import RawSegmentReader
from aiquanttrader.market_data.recorder import DiskPressureError, MarketDataRecorder
from aiquanttrader.market_data.tardis import download_file


class Response(io.BytesIO):
    def __enter__(self) -> Response:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


def trades_csv() -> bytes:
    text = (
        "exchange,symbol,timestamp,local_timestamp,id,side,price,amount\n"
        "hyperliquid,BTC,1700000000000000,1700000000001000,1,buy,100000,0.01\n"
    )
    return gzip.compress(text.encode(), mtime=0)


def test_tardis_download_is_validated_immutable_and_secret_safe(tmp_path: Path) -> None:
    secret = tmp_path / "api-key"
    secret.write_text("super-secret\n", encoding="utf-8")
    observed: dict[str, object] = {}

    def opener(request: urllib.request.Request, timeout: float) -> Response:
        observed["request"] = request
        observed["timeout"] = timeout
        return Response(trades_csv())

    target, manifest_path, manifest = download_file(
        root=tmp_path / "data",
        data_type="trades",
        day=date(2024, 10, 29),
        api_key_secret_path=secret,
        open_request=opener,
    )

    request = observed["request"]
    assert isinstance(request, urllib.request.Request)
    assert request.get_header("Authorization") == "Bearer super-secret"
    assert manifest.row_count == 1
    assert manifest.compressed_sha256
    assert manifest_path.is_file()
    assert target.read_bytes() == trades_csv()
    again = download_file(
        root=tmp_path / "data",
        data_type="trades",
        day=date(2024, 10, 29),
        api_key_secret_path=secret,
        open_request=lambda *_: pytest.fail("immutable file should not download twice"),
    )
    assert again[2] == manifest


def test_tardis_rejects_truncated_gzip_and_removes_partial(tmp_path: Path) -> None:
    def opener(_request: urllib.request.Request, _timeout: float) -> Response:
        return Response(trades_csv()[:-4])

    with pytest.raises(ValueError, match="invalid Tardis"):
        download_file(
            root=tmp_path,
            data_type="trades",
            day=date(2024, 10, 29),
            open_request=opener,
        )
    assert not list(tmp_path.rglob("*.partial"))
    assert not list(tmp_path.rglob("*.csv.gz"))


def test_tardis_rejects_invalid_requests_secrets_and_content(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="future"):
        download_file(root=tmp_path, data_type="trades", day=date(2999, 1, 1))
    with pytest.raises(ValueError, match="timeout"):
        download_file(root=tmp_path, data_type="trades", day=date(2024, 10, 29), timeout_seconds=0)
    with pytest.raises(ValueError, match="secret"):
        download_file(
            root=tmp_path,
            data_type="trades",
            day=date(2024, 10, 29),
            api_key_secret_path=tmp_path / "missing-secret",
        )

    secret = tmp_path / "bad-secret"
    secret.write_text("line-one\nline-two\n", encoding="utf-8")
    with pytest.raises(ValueError, match="one non-empty line"):
        download_file(
            root=tmp_path,
            data_type="trades",
            day=date(2024, 10, 29),
            api_key_secret_path=secret,
        )

    missing_columns = gzip.compress(b"exchange,symbol\nhyperliquid,BTC\n", mtime=0)
    with pytest.raises(ValueError, match="missing required columns"):
        download_file(
            root=tmp_path / "columns",
            data_type="trades",
            day=date(2024, 10, 29),
            open_request=lambda _request, _timeout: Response(missing_columns),
        )
    with pytest.raises(ValueError, match="empty"):
        download_file(
            root=tmp_path / "empty",
            data_type="trades",
            day=date(2024, 10, 29),
            open_request=lambda _request, _timeout: Response(),
        )


def test_tardis_existing_target_must_be_complete_and_match_manifest(tmp_path: Path) -> None:
    root = tmp_path / "data"
    target, _, _ = download_file(
        root=root,
        data_type="trades",
        day=date(2024, 10, 29),
        open_request=lambda _request, _timeout: Response(trades_csv()),
    )
    target.write_bytes(b"changed")
    with pytest.raises(ValueError, match="does not match"):
        download_file(root=root, data_type="trades", day=date(2024, 10, 29))

    incomplete_root = tmp_path / "incomplete"
    incomplete = incomplete_root / Path(
        "historical",
        "source=tardis",
        "exchange=hyperliquid",
        "data_type=trades",
        "date=2024-10-29",
        "BTC.csv.gz",
    )
    incomplete.parent.mkdir(parents=True)
    incomplete.write_bytes(b"orphan")
    with pytest.raises(FileExistsError, match="incomplete"):
        download_file(root=incomplete_root, data_type="trades", day=date(2024, 10, 29))


def test_catalog_enforces_single_writer_and_idempotent_registration(tmp_path: Path) -> None:
    _, _, manifest = download_file(
        root=tmp_path / "data",
        data_type="trades",
        day=date(2024, 10, 29),
        open_request=lambda _request, _timeout: Response(trades_csv()),
    )
    path = tmp_path / "state" / "catalog.duckdb"
    with ManifestCatalog(path) as catalog:
        catalog.register_tardis(manifest)
        catalog.register_tardis(manifest)
        assert catalog.connection.execute("SELECT count(*) FROM tardis_files").fetchone() == (1,)
        collision = manifest.model_copy(update={"compressed_sha256": "f" * 64})
        with pytest.raises(ValueError, match="identity collision"):
            catalog.register_tardis(collision)
        with pytest.raises(CatalogLockedError):
            ManifestCatalog(path)


class FakeSocket:
    def __init__(self, payload: bytes, stop: asyncio.Event) -> None:
        self.payload = payload
        self.stop = stop
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self, *, decode: bool | None = None) -> bytes:
        assert decode is False
        self.stop.set()
        return self.payload


class FakeSocketContext:
    def __init__(self, socket: FakeSocket) -> None:
        self.socket = socket

    async def __aenter__(self) -> FakeSocket:
        return self.socket

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class FailingSocketContext:
    async def __aenter__(self) -> FakeSocket:
        raise OSError("simulated disconnect")

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class StalledSocket(FakeSocket):
    async def recv(self, *, decode: bool | None = None) -> bytes:
        assert decode is False
        self.stop.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def test_recorder_archives_before_parse_and_catalogs_exclusion(tmp_path: Path) -> None:
    async def scenario() -> None:
        stop = asyncio.Event()
        socket = FakeSocket(b"not-json", stop)
        metrics = RecorderMetrics.create()
        config = MarketDataConfig(
            enabled=True,
            sync_every_records=1,
            minimum_free_bytes=67_108_864,
            minimum_free_fraction=Decimal("0.01"),
        )
        with ManifestCatalog(tmp_path / "state" / "catalog.duckdb") as catalog:
            recorder = MarketDataRecorder(
                websocket_url="wss://api.hyperliquid.xyz/ws",
                network="mainnet",
                environment="test",
                config=config,
                data_root=tmp_path / "data",
                state_root=tmp_path / "state",
                catalog=catalog,
                metrics=metrics,
                socket_factory=lambda _url, _size: FakeSocketContext(socket),
            )
            await recorder.run(stop)
        assert b"schema_error" in generate_latest(metrics.registry)

    asyncio.run(scenario())

    raw_path = next((tmp_path / "data" / "raw").rglob("*.raw.zst"))
    reader = RawSegmentReader(raw_path)
    reader.verify()
    assert [record.payload for record in reader.records()] == [b"not-json"]
    assert not (tmp_path / "data" / "normalized").exists()
    state = RecorderState.model_validate_json(
        (tmp_path / "state" / "market-data" / "recorder-state.json").read_bytes()
    )
    assert state.status == "stopped"


def test_live_consumer_runs_only_after_raw_frame_is_flushed(tmp_path: Path) -> None:
    async def scenario() -> None:
        stop = asyncio.Event()
        socket = FakeSocket(b'{"channel":"pong"}', stop)
        observed: list[str] = []
        connection_states: list[bool] = []

        async def consume(frame: object) -> None:
            partials = list((tmp_path / "data" / "raw").rglob("*.partial"))
            assert len(partials) == 1
            assert partials[0].stat().st_size > 0
            observed.append(type(frame).__name__)

        with ManifestCatalog(tmp_path / "state" / "catalog.duckdb") as catalog:
            recorder = MarketDataRecorder(
                websocket_url="wss://api.hyperliquid.xyz/ws",
                network="mainnet",
                environment="paper",
                config=MarketDataConfig(
                    enabled=True,
                    sync_every_records=1,
                    minimum_free_bytes=67_108_864,
                    minimum_free_fraction=Decimal("0.01"),
                ),
                data_root=tmp_path / "data",
                state_root=tmp_path / "state",
                catalog=catalog,
                metrics=RecorderMetrics.create(),
                socket_factory=lambda _url, _size: FakeSocketContext(socket),
                frame_consumer=consume,
                connection_observer=connection_states.append,
            )
            await recorder.run(stop)
        assert observed == ["ParsedFrame"]
        assert connection_states == [False, True, False, False]

    asyncio.run(scenario())


def test_disk_pressure_is_fail_closed(tmp_path: Path) -> None:
    config = MarketDataConfig(
        enabled=True,
        minimum_free_bytes=10**30,
        minimum_free_fraction=Decimal("0.01"),
    )
    with ManifestCatalog(tmp_path / "catalog.duckdb") as catalog:
        recorder = MarketDataRecorder(
            websocket_url="wss://api.hyperliquid.xyz/ws",
            network="mainnet",
            environment="test",
            config=config,
            data_root=tmp_path / "data",
            state_root=tmp_path / "state",
            catalog=catalog,
            metrics=RecorderMetrics.create(),
        )
        recorder.data_root.mkdir(parents=True)
        with pytest.raises(DiskPressureError):
            recorder._check_disk()


def test_connection_observer_failure_is_bounded_and_does_not_escape(tmp_path: Path) -> None:
    metrics = RecorderMetrics.create()

    def fail(_connected: bool) -> None:
        raise RuntimeError("observer failure")

    with ManifestCatalog(tmp_path / "catalog.duckdb") as catalog:
        recorder = MarketDataRecorder(
            websocket_url="wss://api.hyperliquid.xyz/ws",
            network="mainnet",
            environment="test",
            config=MarketDataConfig(enabled=True),
            data_root=tmp_path / "data",
            state_root=tmp_path / "state",
            catalog=catalog,
            metrics=metrics,
            connection_observer=fail,
        )
        recorder._set_connection_state(True)
        recorder._set_connection_state(False)

    output = generate_latest(metrics.registry)
    assert b'code="connection_observer_error"' in output
    assert b"aqt_market_data_connected 0.0" in output


def test_disabled_and_runtime_disk_pressure_stop_recorder(tmp_path: Path) -> None:
    async def disabled() -> None:
        with ManifestCatalog(tmp_path / "disabled.duckdb") as catalog:
            recorder = MarketDataRecorder(
                websocket_url="wss://api.hyperliquid.xyz/ws",
                network="mainnet",
                environment="test",
                config=MarketDataConfig(enabled=False),
                data_root=tmp_path / "disabled-data",
                state_root=tmp_path / "disabled-state",
                catalog=catalog,
                metrics=RecorderMetrics.create(),
            )
            with pytest.raises(ValueError, match="disabled"):
                await recorder.run(asyncio.Event())

    async def pressured() -> None:
        stop = asyncio.Event()
        socket = FakeSocket(b'{"channel":"pong"}', stop)
        with ManifestCatalog(tmp_path / "pressure.duckdb") as catalog:
            recorder = MarketDataRecorder(
                websocket_url="wss://api.hyperliquid.xyz/ws",
                network="mainnet",
                environment="test",
                config=MarketDataConfig(
                    enabled=True,
                    minimum_free_bytes=10**30,
                    minimum_free_fraction=Decimal("0.01"),
                ),
                data_root=tmp_path / "pressure-data",
                state_root=tmp_path / "pressure-state",
                catalog=catalog,
                metrics=RecorderMetrics.create(),
                socket_factory=lambda _url, _size: FakeSocketContext(socket),
            )
            with pytest.raises(DiskPressureError):
                await recorder.run(stop)

    asyncio.run(disabled())
    asyncio.run(pressured())
    state = RecorderState.model_validate_json(
        (tmp_path / "pressure-state" / "market-data" / "recorder-state.json").read_bytes()
    )
    assert state.status == "failed"
    assert state.last_error_code == "disk_pressure"


def test_recorder_reconnects_and_classifies_disconnect(tmp_path: Path) -> None:
    async def scenario() -> RecorderMetrics:
        stop = asyncio.Event()
        socket = FakeSocket(b'{"channel":"pong"}', stop)
        attempts = 0

        def factory(_url: str, _size: int) -> FailingSocketContext | FakeSocketContext:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return FailingSocketContext()
            return FakeSocketContext(socket)

        metrics = RecorderMetrics.create()
        config = MarketDataConfig(
            enabled=True,
            reconnect_initial_ms=250,
            reconnect_max_ms=250,
            reconnect_jitter_fraction=Decimal("0"),
            minimum_free_bytes=67_108_864,
            minimum_free_fraction=Decimal("0.01"),
        )
        with ManifestCatalog(tmp_path / "state" / "raw-catalog.duckdb") as catalog:
            recorder = MarketDataRecorder(
                websocket_url="wss://api.hyperliquid.xyz/ws",
                network="mainnet",
                environment="test",
                config=config,
                data_root=tmp_path / "data",
                state_root=tmp_path / "state",
                catalog=catalog,
                metrics=metrics,
                socket_factory=factory,
            )
            await recorder.run(stop)
            assert recorder.reconnect_count == 1
        return metrics

    metrics = asyncio.run(scenario())
    manifests = sorted((tmp_path / "data" / "raw").rglob("*.manifest.json"))
    reasons = [json.loads(path.read_text())["finalization_reason"] for path in manifests]
    assert reasons == ["disconnect", "shutdown"]
    assert b"connection_error" in generate_latest(metrics.registry)


def test_recorder_observes_stale_feed_silence(tmp_path: Path) -> None:
    async def scenario() -> RecorderMetrics:
        stop = asyncio.Event()
        socket = StalledSocket(b"", stop)
        ticks = iter((0, 5_000_000_000, 5_000_000_000))
        metrics = RecorderMetrics.create()
        config = MarketDataConfig(
            enabled=True,
            stale_after_seconds=5,
            ping_interval_seconds=30,
            reconnect_initial_ms=250,
            reconnect_max_ms=250,
            reconnect_jitter_fraction=Decimal("0"),
            minimum_free_bytes=67_108_864,
            minimum_free_fraction=Decimal("0.01"),
        )
        with ManifestCatalog(tmp_path / "state" / "raw-catalog.duckdb") as catalog:
            recorder = MarketDataRecorder(
                websocket_url="wss://api.hyperliquid.xyz/ws",
                network="mainnet",
                environment="test",
                config=config,
                data_root=tmp_path / "data",
                state_root=tmp_path / "state",
                catalog=catalog,
                metrics=metrics,
                socket_factory=lambda _url, _size: FakeSocketContext(socket),
                monotonic_ns=lambda: next(ticks),
            )
            await recorder.run(stop)
        return metrics

    output = generate_latest(asyncio.run(scenario()).registry)
    assert b"stale_feed" in output
    assert b"silence" in output
