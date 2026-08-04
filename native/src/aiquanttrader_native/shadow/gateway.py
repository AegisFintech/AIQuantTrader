"""Public-only raw-first gateway for one-way shadow ingress."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Literal

from prometheus_client import CollectorRegistry

from aiquanttrader_native.config.loader import ConfigBundle
from aiquanttrader_native.config.models import DeploymentMode, ExchangeNetwork
from aiquanttrader_native.market_data.catalog import ManifestCatalog
from aiquanttrader_native.market_data.io import atomic_replace_bytes
from aiquanttrader_native.market_data.metrics import RecorderMetrics
from aiquanttrader_native.market_data.protocol import ParsedFrame
from aiquanttrader_native.market_data.recorder import (
    MarketDataRecorder,
    SocketFactory,
    default_socket_factory,
)
from aiquanttrader_native.shadow.ingress import ShadowIngressWriter
from aiquanttrader_native.shadow.models import ShadowGatewayStatus


class ShadowGatewayService:
    def __init__(
        self,
        *,
        bundle: ConfigBundle,
        ingress: ShadowIngressWriter,
        registry: CollectorRegistry,
        socket_factory: SocketFactory | None = None,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        settings = bundle.settings
        if settings.mode is not DeploymentMode.SHADOW or not settings.shadow.enabled:
            raise ValueError("shadow gateway requires the enabled shadow environment")
        if (
            settings.execution.enabled
            or settings.sentinel.enabled
            or any(
                value is not None
                for value in (
                    settings.exchange.account_address,
                    settings.exchange.trading_wallet_secret_path,
                    settings.exchange.control_wallet_secret_path,
                )
            )
        ):
            raise ValueError("shadow gateway refuses exchange execution capability")
        self.bundle = bundle
        self.ingress = ingress
        self.clock_ns = clock_ns
        self.status_path = settings.storage.state_root / "shadow-gateway" / "status.json"
        self._metrics = RecorderMetrics.create(registry)
        self._socket_factory = socket_factory
        self._last_sequence = ingress.latest_sequence()
        self._last_receive_ts_ns: int | None = None
        self._last_error_code: str | None = None

    async def consume_frame(self, frame: ParsedFrame) -> None:
        record = self.ingress.append(frame)
        self._last_sequence = record.sequence
        self._last_receive_ts_ns = record.envelope.receive_ts_ns
        self._write_status("ready")

    async def run(self, stop: asyncio.Event) -> None:
        settings = self.bundle.settings
        self._write_status("starting")
        catalog_path = settings.storage.state_root / "market-data" / "raw-catalog.duckdb"
        network: Literal["mainnet", "testnet"] = (
            "mainnet" if settings.exchange.network is ExchangeNetwork.MAINNET else "testnet"
        )
        with ManifestCatalog(catalog_path) as catalog:
            recorder = MarketDataRecorder(
                websocket_url=str(settings.exchange.websocket_url),
                network=network,
                environment=settings.environment,
                config=settings.market_data,
                data_root=settings.storage.data_root,
                state_root=settings.storage.state_root,
                catalog=catalog,
                metrics=self._metrics,
                frame_consumer=self.consume_frame,
                socket_factory=self._socket_factory or default_socket_factory,
            )
            try:
                await recorder.run(stop)
            except Exception as exc:
                self._last_error_code = type(exc).__name__.lower()
                self._write_status("failed")
                raise
        self._write_status("stopped")

    def _write_status(
        self,
        status: Literal["starting", "ready", "reconnecting", "stopped", "failed"],
    ) -> None:
        payload = ShadowGatewayStatus(
            status=status,
            heartbeat_ts_ns=self.clock_ns(),
            last_ingress_sequence=self._last_sequence,
            last_receive_ts_ns=self._last_receive_ts_ns,
            last_error_code=self._last_error_code,
        )
        atomic_replace_bytes(self.status_path, payload.canonical_bytes() + b"\n")
