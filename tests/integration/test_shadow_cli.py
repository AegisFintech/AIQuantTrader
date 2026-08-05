from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from aiquanttrader.config import load_config
from aiquanttrader.market_data.io import atomic_replace_bytes
from aiquanttrader.market_data.protocol import ParsedFrame
from aiquanttrader.paper.journal import PaperJournal
from aiquanttrader.paper.models import (
    PaperAccountState,
    PaperEngineCheckpoint,
    PaperRunManifest,
)
from aiquanttrader.shadow import cli
from aiquanttrader.shadow.audit import ShadowAuditJournal
from aiquanttrader.shadow.config import load_shadow_artifacts
from aiquanttrader.shadow.ingress import ShadowIngressWriter
from aiquanttrader.shadow.models import ShadowEvidenceReport, ShadowRuntimeStatus

IMAGE = "sha256:" + "a" * 64


def _initialize_state(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[PaperRunManifest, Path]:
    state_root = (tmp_path / "state").resolve()
    monkeypatch.setenv("AQT_NATIVE__STORAGE__DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("AQT_NATIVE__STORAGE__STATE_ROOT", str(state_root))
    bundle = load_config(config_dir, "shadow")
    artifacts = load_shadow_artifacts(config_dir, bundle)
    now = time.time_ns()
    manifest = PaperRunManifest(
        run_id="shadow-cli-run",
        environment="shadow",
        started_ts_ns=now - 1_000_000,
        code_identity="cli-test-commit",
        image_identity=IMAGE,
        config_fingerprint=bundle.fingerprint,
        feature_config_sha256=artifacts.paper.feature_config_sha256,
        strategy_config_sha256=artifacts.paper.strategy_config_sha256,
        scenario_id=artifacts.paper.scenario.scenario_id,
        scenario_sha256=artifacts.paper.scenario.sha256(),
        evidence_policy_sha256=artifacts.paper.evidence_policy_sha256,
        strategy_id=artifacts.paper.strategy_config.strategy_id,
    )
    account = PaperAccountState(
        cash_usd=Decimal("100000"),
        mark_price=Decimal("100000"),
        equity_usd=Decimal("100000"),
        day_start_equity_usd=Decimal("100000"),
        high_water_equity_usd=Decimal("100000"),
        utc_day=now // 86_400_000_000_000,
        updated_ts_ns=now,
    )
    state = state_root / "shadow"
    journal = PaperJournal((state / "shadow-journal.sqlite3").resolve())
    journal.begin_run(manifest, account)
    journal.close()
    audit = ShadowAuditJournal((state / "shadow-audit.sqlite3").resolve())
    audit.begin_run(manifest, IMAGE)
    audit.close()
    status = ShadowRuntimeStatus(
        status="ready",
        run_id=manifest.run_id,
        heartbeat_ts_ns=time.time_ns(),
        last_public_data_ts_ns=now,
        last_ingress_sequence=1,
        ingress_lag_ns=1,
        feed_connected=True,
        feature_ready=True,
        operator_kill=False,
        strategy_id=manifest.strategy_id,
        scenario_id=manifest.scenario_id,
        scenario_sha256=manifest.scenario_sha256,
        calibration_state=artifacts.paper.scenario.calibration_state,
        config_fingerprint=bundle.fingerprint,
        image_identity=IMAGE,
        account=account,
        open_orders=0,
        decisions=0,
        commands=0,
        fills=0,
    )
    atomic_replace_bytes(state / "status.json", status.canonical_bytes() + b"\n")
    atomic_replace_bytes(state / "metrics.prom", b"aqt_shadow_network_egress_capability 0\n")
    return manifest, state_root


def test_shadow_cli_health_status_kill_drill_and_evidence(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest, state_root = _initialize_state(tmp_path, config_dir, monkeypatch)
    assert cli.main(["healthcheck", "--state-root", str(state_root)]) == 0
    assert cli.main(["status", "--state-root", str(state_root)]) == 0
    assert (
        cli.main(
            [
                "kill",
                "activate",
                "--state-root",
                str(state_root),
                "--actor",
                "operator",
                "--reason",
                "drill",
            ]
        )
        == 0
    )
    assert (
        cli.main(
            [
                "kill",
                "clear",
                "--state-root",
                str(state_root),
                "--actor",
                "operator",
                "--reason",
                "complete",
            ]
        )
        == 0
    )
    drill = tmp_path / "host-reboot.json"
    drill.write_text('{"host_reboot":"pass"}\n')
    assert (
        cli.main(
            [
                "record-drill",
                "host_reboot",
                "--state-root",
                str(state_root),
                "--evidence-file",
                str(drill),
            ]
        )
        == 0
    )
    output = tmp_path / "shadow-evidence.json"
    assert (
        cli.main(
            [
                "evidence",
                "--config-dir",
                str(config_dir),
                "--run-id",
                manifest.run_id,
                "--output",
                str(output),
            ]
        )
        == 1
    )
    report = ShadowEvidenceReport.model_validate_json(output.read_bytes())
    assert not report.awaiting_human_approval
    assert "shadow-cli-run" in capsys.readouterr().out

    stale = ShadowRuntimeStatus.model_validate_json(
        (state_root / "shadow" / "status.json").read_bytes()
    ).model_copy(update={"heartbeat_ts_ns": 0})
    atomic_replace_bytes(state_root / "shadow" / "status.json", stale.canonical_bytes() + b"\n")
    assert cli.main(["healthcheck", "--state-root", str(state_root)]) == 1
    assert cli.main(["status", "--state-root", str(tmp_path / "missing")]) == 2


def test_shadow_cli_compare_and_replay_durable_ingress(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_manifest, _ = _initialize_state(tmp_path, config_dir, monkeypatch)
    source_path = tmp_path / "state" / "shadow" / "shadow-journal.sqlite3"
    source_audit = tmp_path / "state" / "shadow" / "shadow-audit.sqlite3"
    replay_path = (tmp_path / "comparison-replay.sqlite3").resolve()
    source = PaperJournal(source_path.resolve())
    source_account = source.latest_account(source_manifest.run_id)
    assert source_account is not None
    source.record_checkpoint(
        PaperEngineCheckpoint(
            run_id=source_manifest.run_id,
            sequence=1,
            checkpoint_ts_ns=time.time_ns(),
            strategy_id=source_manifest.strategy_id,
            strategy_memory_json='{"inventory_base":"0","order_revision":0}',
            source_sequence=1,
        )
    )
    replay_manifest = source_manifest.model_copy(update={"run_id": "shadow-cli-replay"})
    replay = PaperJournal(replay_path)
    replay.begin_run(replay_manifest, source_account)
    source.close()
    replay.close()
    comparison = tmp_path / "comparison.json"
    assert (
        cli.main(
            [
                "compare",
                "--source-journal",
                str(source_path),
                "--replay-journal",
                str(replay_path),
                "--source-run-id",
                source_manifest.run_id,
                "--replay-run-id",
                replay_manifest.run_id,
                "--source-audit",
                str(source_audit),
                "--output",
                str(comparison),
            ]
        )
        == 0
    )
    assert comparison.is_file()

    ingress_path = (tmp_path / "replay-ingress.sqlite3").resolve()
    writer = ShadowIngressWriter(ingress_path)
    now = time.time_ns()
    from aiquanttrader.domain.market import BookLevel, EventHeader, L2BookSnapshot

    replay_frame = ParsedFrame(
        channel="l2Book",
        events=(
            L2BookSnapshot(
                header=EventHeader(
                    event_id="cli-replay-book",
                    event_ts_ns=now,
                    receive_ts_ns=now,
                    connection_id="cli-replay",
                ),
                bids=(BookLevel(price=Decimal("99999"), size=Decimal("1")),),
                asks=(BookLevel(price=Decimal("100001"), size=Decimal("1")),),
            ),
        ),
    )
    writer.append(replay_frame)
    writer.append(replay_frame)  # Gateway advanced after the source engine checkpoint.
    writer.close()
    replay_output = tmp_path / "replay-output"
    assert (
        cli.main(
            [
                "replay",
                "--config-dir",
                str(config_dir),
                "--code-identity",
                "cli-test-commit",
                "--image-identity",
                IMAGE,
                "--ingress-path",
                str(ingress_path),
                "--source-journal",
                str(source_path),
                "--source-run-id",
                source_manifest.run_id,
                "--output-state-root",
                str(replay_output),
            ]
        )
        == 0
    )
    assert (replay_output / "shadow-journal.sqlite3").is_file()
    replayed = PaperJournal((replay_output / "shadow-journal.sqlite3").resolve())
    replayed_manifest = replayed.latest_manifest()
    assert replayed_manifest is not None
    replayed_checkpoint = replayed.latest_checkpoint(replayed_manifest.run_id)
    assert replayed_checkpoint is not None
    assert replayed_checkpoint.source_sequence == 1
    replayed.close()
    assert (
        cli.main(
            [
                "replay",
                "--config-dir",
                str(config_dir),
                "--image-identity",
                IMAGE,
                "--ingress-path",
                str(ingress_path),
                "--source-journal",
                str(source_path),
                "--source-run-id",
                source_manifest.run_id,
                "--output-state-root",
                str(replay_output),
            ]
        )
        == 2
    )


def test_shadow_cli_constructs_split_services_and_observer(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AQT_NATIVE__STORAGE__DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("AQT_NATIVE__STORAGE__STATE_ROOT", str(tmp_path / "state"))
    ingress = (tmp_path / "ingress.sqlite3").resolve()
    writer = ShadowIngressWriter(ingress)
    writer.close()
    observed: dict[str, Any] = {}

    class FakeGateway:
        def __init__(self, **kwargs: Any) -> None:
            observed["gateway"] = kwargs

        async def run(self, stop: asyncio.Event) -> None:
            observed["gateway_run"] = True
            stop.set()

    class FakeEngine:
        def __init__(self, **kwargs: Any) -> None:
            observed["engine"] = kwargs

        async def run(self, stop: asyncio.Event) -> None:
            observed["engine_run"] = True
            stop.set()

    import asyncio

    monkeypatch.setattr(cli, "ShadowGatewayService", FakeGateway)
    monkeypatch.setattr(cli, "ShadowEngineService", FakeEngine)
    monkeypatch.setattr(cli, "start_http_server", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli, "assert_no_ip_egress", lambda: None)
    assert (
        cli.main(
            [
                "gateway",
                "--config-dir",
                str(config_dir),
                "--ingress-path",
                str(ingress),
            ]
        )
        == 0
    )
    assert (
        cli.main(
            [
                "run",
                "--config-dir",
                str(config_dir),
                "--code-identity",
                "cli-test-commit",
                "--image-identity",
                IMAGE,
                "--ingress-path",
                str(ingress),
            ]
        )
        == 0
    )
    monkeypatch.setattr(
        cli,
        "serve_observer",
        lambda observer, **kwargs: observed.update(observer=observer, observer_kwargs=kwargs),
    )
    assert (
        cli.main(
            [
                "observe",
                "--state-root",
                str(tmp_path / "state"),
                "--host",
                "127.0.0.1",
                "--port",
                "9999",
            ]
        )
        == 0
    )
    assert observed["gateway_run"] and observed["engine_run"]
    assert observed["observer_kwargs"] == {"host": "127.0.0.1", "port": 9999}
    assert (
        cli.main(
            [
                "run",
                "--config-dir",
                str(config_dir),
                "--image-identity",
                "mutable-tag",
                "--ingress-path",
                str(ingress),
            ]
        )
        == 2
    )
