from __future__ import annotations

import json
import time
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from aiquanttrader.config import load_config
from aiquanttrader.config.loader import ConfigBundle
from aiquanttrader.domain.data import SegmentFinalizationReason
from aiquanttrader.market_data.io import atomic_replace_bytes
from aiquanttrader.market_data.raw import RawSegmentWriter
from aiquanttrader.paper import cli
from aiquanttrader.paper.config import load_paper_artifacts
from aiquanttrader.paper.journal import PaperJournal
from aiquanttrader.paper.models import (
    PaperAccountState,
    PaperEvidenceReport,
    PaperFeedFreshness,
    PaperRunManifest,
    PaperRuntimeStatus,
)


def initialize_state(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[PaperRunManifest, Path]:
    data_root = (tmp_path / "data").resolve()
    state_root = (tmp_path / "state").resolve()
    monkeypatch.setenv("AQT_NATIVE__STORAGE__DATA_ROOT", str(data_root))
    monkeypatch.setenv("AQT_NATIVE__STORAGE__STATE_ROOT", str(state_root))
    bundle = load_config(config_dir, "paper")
    artifacts = load_paper_artifacts(config_dir, bundle)
    now = time.time_ns()
    run = PaperRunManifest(
        run_id="paper-cli-run",
        environment="paper",
        started_ts_ns=now - 1_000_000,
        code_identity="cli-test-commit",
        config_fingerprint=bundle.fingerprint,
        feature_config_sha256=artifacts.feature_config_sha256,
        strategy_config_sha256=artifacts.strategy_config_sha256,
        scenario_id=artifacts.scenario.scenario_id,
        scenario_sha256=artifacts.scenario.sha256(),
        evidence_policy_sha256=artifacts.evidence_policy_sha256,
        strategy_id=artifacts.strategy_config.strategy_id,
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
    journal_path = state_root / "paper" / "paper-journal.sqlite3"
    journal = PaperJournal(journal_path)
    journal.begin_run(run, account)
    journal.close()
    status_now = time.time_ns()
    status = PaperRuntimeStatus(
        status="ready",
        run_id=run.run_id,
        environment="paper",
        heartbeat_ts_ns=status_now,
        last_public_data_ts_ns=now,
        feed_connected=True,
        feed_freshness=PaperFeedFreshness.from_observations(
            checked_ts_ns=status_now,
            stale_after_ms=bundle.settings.risk.public_data_stale_after_ms,
            depth_stale_after_ms=artifacts.feature_config.maximum_input_age_ns // 1_000_000,
            socket_connected=True,
            last_public_frame_wall_ns=status_now,
            last_asset_context_wall_ns=status_now,
            last_bbo_wall_ns=status_now,
            last_l2_depth_wall_ns=status_now,
        ),
        feature_ready=True,
        operator_kill=False,
        scenario_id=artifacts.scenario.scenario_id,
        scenario_sha256=artifacts.scenario.sha256(),
        calibration_state=artifacts.scenario.calibration_state,
        strategy_id=artifacts.strategy_config.strategy_id,
        config_fingerprint=bundle.fingerprint,
        account=account,
        open_orders=0,
        decisions=0,
        fills=0,
    )
    atomic_replace_bytes(state_root / "paper" / "status.json", status.canonical_bytes() + b"\n")
    return run, state_root


def test_paper_cli_health_status_kill_and_nonpromotable_evidence(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run, state_root = initialize_state(tmp_path, config_dir, monkeypatch)
    assert (
        cli.main(
            [
                "healthcheck",
                "--state-root",
                str(state_root),
                "--record-observability",
            ]
        )
        == 0
    )
    assert cli.main(["status", "--state-root", str(state_root)]) == 0
    assert cli.main(["diagnostics", "--state-root", str(state_root)]) == 0
    assert (
        cli.main(
            [
                "kill",
                "activate",
                "--state-root",
                str(state_root),
                "--actor",
                "test-operator",
                "--reason",
                "CLI kill test",
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
                "test-operator",
                "--reason",
                "CLI kill test complete",
            ]
        )
        == 0
    )

    output = tmp_path / "paper-evidence.json"
    result = cli.main(
        [
            "evidence",
            "--config-dir",
            str(config_dir),
            "--environment",
            "paper",
            "--run-id",
            run.run_id,
            "--output",
            str(output),
        ]
    )
    assert result == 1
    report = PaperEvidenceReport.model_validate_json(output.read_bytes())
    assert not report.promotion_eligible
    assert not next(gate for gate in report.gates if gate.gate == "calibrated_fill_model").passed

    journal = PaperJournal(state_root / "paper" / "paper-journal.sqlite3")
    assert "observability" in journal.statistics(run.run_id).completed_drills
    journal.close()
    assert "paper-cli-run" in capsys.readouterr().out


def test_paper_cli_rejects_stale_health_missing_state_and_wrong_run(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, state_root = initialize_state(tmp_path, config_dir, monkeypatch)
    status_path = state_root / "paper" / "status.json"
    status = PaperRuntimeStatus.model_validate_json(status_path.read_bytes())
    monkeypatch.setattr("aiquanttrader.paper.cli.time.time_ns", lambda: status.heartbeat_ts_ns - 1)
    assert cli.main(["healthcheck", "--state-root", str(state_root)]) == 1
    stale = status.model_copy(
        update={
            "heartbeat_ts_ns": 0,
            "feed_freshness": status.feed_freshness.model_copy(update={"checked_ts_ns": 0}),
        }
    )
    atomic_replace_bytes(status_path, stale.canonical_bytes() + b"\n")
    assert cli.main(["healthcheck", "--state-root", str(state_root)]) == 1
    assert (
        cli.main(
            [
                "healthcheck",
                "--state-root",
                str(state_root),
                "--record-observability",
            ]
        )
        == 2
    )


def test_paper_cli_run_builds_service_without_credentials(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AQT_NATIVE__STORAGE__DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("AQT_NATIVE__STORAGE__STATE_ROOT", str(tmp_path / "state"))
    observed: dict[str, object] = {}

    class FakeService:
        def __init__(self, **kwargs: object) -> None:
            observed.update(kwargs)

        async def run(self, stop: object) -> None:
            observed["run"] = True
            stop.set()  # type: ignore[attr-defined]

    monkeypatch.setattr(cli, "PaperLiveService", FakeService)
    monkeypatch.setattr(cli, "start_http_server", lambda *_args, **_kwargs: None)
    assert (
        cli.main(
            [
                "run",
                "--config-dir",
                str(config_dir),
                "--environment",
                "paper",
                "--code-identity",
                "cli-run-test",
            ]
        )
        == 0
    )
    assert observed["code_identity"] == "cli-run-test"
    assert observed["run"] is True
    bundle = cast(ConfigBundle, observed["bundle"])
    assert not bundle.settings.execution.enabled
    assert bundle.settings.exchange.trading_wallet_secret_path is None
    assert cli.main(["status", "--state-root", str(tmp_path / "missing")]) == 2
    assert (
        cli.main(
            [
                "evidence",
                "--config-dir",
                str(config_dir),
                "--run-id",
                "wrong-run",
                "--output",
                str(tmp_path / "wrong.json"),
            ]
        )
        == 2
    )


def test_paper_cli_replays_verified_raw_segment_without_network(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    started = time.time_ns()
    receive = started + 1_000_000
    payload = json.dumps(
        {
            "channel": "l2Book",
            "data": {
                "coin": "BTC",
                "time": receive // 1_000_000,
                "levels": [
                    [{"px": "99999", "sz": "1", "n": 1}],
                    [{"px": "100001", "sz": "1", "n": 1}],
                ],
            },
        }
    ).encode()
    writer = RawSegmentWriter(
        (tmp_path / "archive").resolve(),
        network="mainnet",
        connection_id="paper-replay-test",
        started_at_ns=started,
        sync_every_records=1,
    )
    writer.append(payload, receive_ts_ns=receive, monotonic_ts_ns=1)
    finalized = writer.finalize(SegmentFinalizationReason.SHUTDOWN)

    state_root = (tmp_path / "replay-state").resolve()
    monkeypatch.setenv("AQT_NATIVE__STORAGE__DATA_ROOT", str(tmp_path / "replay-data"))
    monkeypatch.setenv("AQT_NATIVE__STORAGE__STATE_ROOT", str(state_root))
    assert (
        cli.main(
            [
                "replay",
                "--config-dir",
                str(config_dir),
                "--code-identity",
                "b" * 40,
                "--raw-segment",
                str(finalized.segment_path),
            ]
        )
        == 0
    )
    journal = PaperJournal(state_root / "paper" / "paper-journal.sqlite3")
    manifest = journal.latest_manifest()
    assert manifest is not None
    assert manifest.code_identity == "b" * 40
    summary = journal.strategy_evaluation_summary(manifest.run_id)
    assert summary.evaluations == 1
    assert summary.action_counts[0].action.value == "warmup"
    journal.close()
    status = PaperRuntimeStatus.model_validate_json(
        (state_root / "paper" / "status.json").read_bytes()
    )
    assert status.status == "stopped"
    output = capsys.readouterr().out
    assert '"consumed_frames": 1' in output
    assert '"evaluations": 1' in output
