"""Minimal dependency-free health service for the native foundation image."""

from __future__ import annotations

import json
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from aiquanttrader.config.loader import ConfigBundle


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def create_health_server(
    bundle: ConfigBundle,
    *,
    host: str | None = None,
    port: int | None = None,
) -> ThreadingHTTPServer:
    """Create a health server bound to an already validated configuration."""

    settings = bundle.settings

    class Handler(BaseHTTPRequestHandler):
        server_version = "AIQuantTraderNativeHealth/1"

        def do_GET(self) -> None:
            if self.path == "/health/live":
                self._respond(HTTPStatus.OK, {"status": "live"})
                return
            if self.path == "/health/ready":
                self._respond(
                    HTTPStatus.OK,
                    {
                        "status": "ready",
                        "environment": settings.environment,
                        "mode": settings.mode.value,
                        "execution_enabled": settings.execution.enabled,
                        "config_fingerprint": bundle.fingerprint,
                    },
                )
                return
            if self.path == "/config/fingerprint":
                self._respond(
                    HTTPStatus.OK,
                    {"config_fingerprint": bundle.fingerprint},
                )
                return
            self._respond(HTTPStatus.NOT_FOUND, {"status": "not_found"})

        def _respond(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            body = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, message_format: str, *args: object) -> None:
            message = message_format % args
            print(
                json.dumps(
                    {
                        "component": "native-health",
                        "client": self.client_address[0],
                        "message": message,
                    },
                    sort_keys=True,
                ),
                file=sys.stdout,
                flush=True,
            )

    return ThreadingHTTPServer(
        (
            settings.observability.health_host if host is None else host,
            settings.observability.health_port if port is None else port,
        ),
        Handler,
    )
