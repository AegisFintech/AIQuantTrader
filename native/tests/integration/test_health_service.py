from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from aiquanttrader_native.cli import main
from aiquanttrader_native.config import load_config
from aiquanttrader_native.service import create_health_server


def test_health_service_reports_validated_configuration(
    config_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bundle = load_config(config_dir, "paper", environ={})
    server = create_health_server(bundle, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/ready") as response:
            payload = json.loads(response.read())
        assert payload == {
            "config_fingerprint": bundle.fingerprint,
            "environment": "paper",
            "execution_enabled": False,
            "mode": "paper",
            "status": "ready",
        }

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/config/fingerprint") as response:
            assert json.loads(response.read())["config_fingerprint"] == bundle.fingerprint

        assert (
            main(
                [
                    "healthcheck",
                    "--url",
                    f"http://127.0.0.1:{port}/health/ready",
                ]
            )
            == 0
        )
        assert json.loads(capsys.readouterr().out.splitlines()[-1])["status"] == "ready"

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/missing")
        assert exc_info.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_cli_validation_and_healthcheck(
    config_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "validate-config",
                "--config-dir",
                str(config_dir),
                "--environment",
                "paper",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["execution_enabled"] is False

    assert main(["healthcheck", "--url", "http://127.0.0.1:1/health/ready"]) == 1
    assert json.loads(capsys.readouterr().err)["status"] == "unhealthy"

    assert main(["healthcheck", "--timeout", "0"]) == 2
    assert json.loads(capsys.readouterr().err)["status"] == "invalid"


def test_cli_show_config_and_schema_export(
    config_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "show-config",
                "--config-dir",
                str(config_dir),
                "--environment",
                "shadow",
            ]
        )
        == 0
    )
    shown = json.loads(capsys.readouterr().out)
    assert shown["mode"] == "shadow"
    assert shown["execution"]["enabled"] is False
    assert len(shown["config_fingerprint"]) == 64

    assert main(["export-schemas", "--output", str(tmp_path)]) == 0
    exported = json.loads(capsys.readouterr().out)
    assert exported["status"] == "valid"
    assert len(exported["schemas"]) == 10


def test_cli_reports_invalid_configuration(
    config_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "validate-config",
                "--config-dir",
                str(config_dir),
                "--environment",
                "missing",
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().err)["status"] == "invalid"
