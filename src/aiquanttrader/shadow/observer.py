"""Read-only HTTP observer for a network-isolated shadow engine."""

from __future__ import annotations

import json
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from aiquanttrader.shadow.models import ShadowRuntimeStatus


class ShadowObserver:
    def __init__(self, state_root: Path, *, stale_after_ms: int) -> None:
        if stale_after_ms <= 0:
            raise ValueError("shadow observer stale threshold must be positive")
        self.status_path = state_root.resolve() / "shadow" / "status.json"
        self.metrics_path = state_root.resolve() / "shadow" / "metrics.prom"
        self.stale_after_ns = stale_after_ms * 1_000_000

    def status(self) -> tuple[ShadowRuntimeStatus, bool, int]:
        status = ShadowRuntimeStatus.model_validate_json(self.status_path.read_bytes())
        age = max(0, time.time_ns() - status.heartbeat_ts_ns)
        ready = (
            status.status in {"warming", "ready"}
            and status.feed_connected
            and not status.operator_kill
            and status.credential_capability == "none"
            and status.ip_network_capability == "none"
            and age <= self.stale_after_ns
        )
        return status, ready, age

    def metrics(self) -> bytes:
        return self.metrics_path.read_bytes()


def serve_observer(observer: ShadowObserver, *, host: str, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            try:
                if self.path == "/metrics":
                    _write(self, HTTPStatus.OK, observer.metrics(), "text/plain; version=0.0.4")
                    return
                if self.path in {"/health/live", "/health/ready"}:
                    status, ready, age = observer.status()
                    live = age <= observer.stale_after_ns
                    passed = live if self.path.endswith("live") else ready
                    body = json.dumps(
                        {
                            "status": "ready" if passed else "not_ready",
                            "run_id": status.run_id,
                            "heartbeat_age_ms": age / 1_000_000,
                        },
                        sort_keys=True,
                    ).encode()
                    _write(
                        self,
                        HTTPStatus.OK if passed else HTTPStatus.SERVICE_UNAVAILABLE,
                        body,
                        "application/json",
                    )
                    return
                _write(self, HTTPStatus.NOT_FOUND, b'{"error":"not_found"}', "application/json")
            except (OSError, ValueError) as exc:
                body = json.dumps(
                    {"status": "not_ready", "error": type(exc).__name__.lower()},
                    sort_keys=True,
                ).encode()
                _write(self, HTTPStatus.SERVICE_UNAVAILABLE, body, "application/json")

        def log_message(self, _format: str, *args: Any) -> None:
            return None

    server = ThreadingHTTPServer((host, port), Handler)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


def _write(
    handler: BaseHTTPRequestHandler,
    status: HTTPStatus,
    body: bytes,
    content_type: str,
) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)
