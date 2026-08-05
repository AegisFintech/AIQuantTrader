from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from aiquanttrader.acceptance.audit import OperationalEvidenceLog
from aiquanttrader.acceptance.models import AcceptanceComponent
from aiquanttrader.config import load_config
from aiquanttrader.execution.cli import main as execution_main
from aiquanttrader.execution.heartbeat import HeartbeatPublisher
from aiquanttrader.risk import KillSwitchStore
from aiquanttrader.sentinel.cli import main as sentinel_main

ACCOUNT = "0x" + "1" * 40


def test_operator_kill_cli_is_persistent(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = (tmp_path / "state").resolve()
    monkeypatch.setenv("AQT_NATIVE__STORAGE__STATE_ROOT", str(state))
    monkeypatch.setenv("AQT_NATIVE__STORAGE__DATA_ROOT", str((tmp_path / "data").resolve()))
    common = ["--config-dir", str(config_dir), "--environment", "testnet"]

    assert execution_main(["kill", *common, "--actor", "ops", "--reason", "drill"]) == 0
    assert json.loads(capsys.readouterr().out)["active"] is True
    assert execution_main(["kill-status", *common]) == 0
    assert json.loads(capsys.readouterr().out)["active"] is True
    assert execution_main(["clear-kill", *common, "--actor", "ops", "--reason", "complete"]) == 0
    assert json.loads(capsys.readouterr().out)["active"] is False


def _enable_testnet(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AQT_NATIVE__EXCHANGE__ACCOUNT_ADDRESS", ACCOUNT)
    monkeypatch.setenv(
        "AQT_NATIVE__EXCHANGE__TRADING_WALLET_SECRET_PATH",
        "/run/secrets/testnet-trading-wallet",
    )
    monkeypatch.setenv(
        "AQT_NATIVE__EXCHANGE__CONTROL_WALLET_SECRET_PATH",
        "/run/secrets/testnet-control-wallet",
    )
    monkeypatch.setenv("AQT_NATIVE__EXECUTION__ENABLED", "true")
    monkeypatch.setenv("AQT_NATIVE__LIVE_STRATEGY__ENABLED", "true")
    monkeypatch.setenv("AQT_NATIVE__SENTINEL__ENABLED", "true")
    monkeypatch.setenv("AQT_NATIVE__STORAGE__STATE_ROOT", str((tmp_path / "state").resolve()))
    monkeypatch.setenv("AQT_NATIVE__STORAGE__DATA_ROOT", str((tmp_path / "data").resolve()))


def test_execution_cli_wires_lifecycle_without_exposing_secret(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_testnet(monkeypatch, tmp_path)
    monkeypatch.setattr("aiquanttrader.execution.cli.start_http_server", Mock())
    journal = Mock()
    monkeypatch.setattr("aiquanttrader.execution.cli.ExecutionJournal", lambda path: journal)
    monkeypatch.setattr("aiquanttrader.execution.cli.mark_stale_submissions", Mock())
    monkeypatch.setattr("aiquanttrader.execution.cli.read_private_key", lambda path: "opaque-key")
    built = SimpleNamespace()
    builder = Mock(return_value=built)
    runner = Mock()
    monkeypatch.setattr("aiquanttrader.execution.cli.build_trading_node", builder)
    monkeypatch.setattr("aiquanttrader.execution.cli.run_trading_node", runner)

    assert execution_main(["run", "--config-dir", str(config_dir), "--environment", "testnet"]) == 0
    builder.assert_called_once()
    operational_log = builder.call_args.kwargs["operational_log"]
    assert isinstance(operational_log, OperationalEvidenceLog)
    assert operational_log.component is AcceptanceComponent.EXECUTION
    assert operational_log.path == tmp_path / "state" / "execution" / "acceptance-events.jsonl"
    runner.assert_called_once()
    journal.close.assert_called_once()


def test_execution_cli_rejects_disabled_run(
    config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert execution_main(["run", "--config-dir", str(config_dir), "--environment", "testnet"]) == 2
    assert "requires trading wallet" in capsys.readouterr().err


def test_execution_healthcheck_requires_exact_fresh_healthy_state(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _enable_testnet(monkeypatch, tmp_path)
    bundle = load_config(config_dir, "testnet")
    kill = KillSwitchStore(bundle.settings.storage.state_root / "execution" / "operator-kill.json")
    publisher = HeartbeatPublisher(
        bundle.settings.storage.state_root / "execution" / "heartbeat.json",
        environment="testnet",
        account_address=ACCOUNT,
        config_fingerprint=bundle.fingerprint,
        kill_switch=kill,
    )
    publisher.set_health(execution_healthy=True, reconciliation_complete=True)
    publisher.publish()
    command = [
        "healthcheck",
        "--config-dir",
        str(config_dir),
        "--environment",
        "testnet",
    ]
    assert execution_main(command) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"
    kill.activate(actor="ops", reason="test")
    publisher.publish()
    assert execution_main(command) == 1
    assert json.loads(capsys.readouterr().err)["status"] == "unhealthy"


def test_sentinel_cli_builds_independent_control_process(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_testnet(monkeypatch, tmp_path)
    monkeypatch.setattr("aiquanttrader.sentinel.cli.start_http_server", Mock())
    monkeypatch.setattr("aiquanttrader.sentinel.cli.read_private_key", lambda path: "control-key")
    client = object()
    monkeypatch.setattr(
        "aiquanttrader.sentinel.cli.HyperliquidControlClient",
        Mock(return_value=client),
    )
    sentinel = Mock()
    sentinel_builder = Mock(return_value=sentinel)
    monkeypatch.setattr("aiquanttrader.sentinel.cli.SafetySentinel", sentinel_builder)

    class ImmediateStop:
        def wait(self, timeout: float) -> bool:
            return True

        def set(self) -> None:
            pass

    monkeypatch.setattr("aiquanttrader.sentinel.cli.threading.Event", ImmediateStop)
    evidence_path = (tmp_path / "sentinel-audit" / "events.jsonl").resolve()
    assert (
        sentinel_main(
            [
                "--config-dir",
                str(config_dir),
                "--environment",
                "testnet",
                "--operational-evidence-path",
                str(evidence_path),
            ]
        )
        == 0
    )
    operational_log = sentinel_builder.call_args.kwargs["operational_log"]
    assert isinstance(operational_log, OperationalEvidenceLog)
    assert operational_log.component is AcceptanceComponent.SENTINEL
    assert operational_log.path == evidence_path
    sentinel.step.assert_not_called()


def test_sentinel_cli_rejects_disabled_config(
    config_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert sentinel_main(["--config-dir", str(config_dir), "--environment", "testnet"]) == 2
    assert "must be enabled" in capsys.readouterr().err


def test_sentinel_metrics_healthcheck(
    config_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _enable_testnet(monkeypatch, tmp_path)
    response = Mock()
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    response.status = 200
    response.read.return_value = b"# HELP aqt_sentinel_trading_node_healthy test"
    monkeypatch.setattr(
        "aiquanttrader.sentinel.cli.urllib.request.urlopen",
        Mock(return_value=response),
    )
    assert (
        sentinel_main(
            [
                "--healthcheck",
                "--config-dir",
                str(config_dir),
                "--environment",
                "testnet",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "ready"
