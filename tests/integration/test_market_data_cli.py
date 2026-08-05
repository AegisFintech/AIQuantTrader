from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aiquanttrader.domain.data import (
    RecorderState,
    SegmentFinalizationReason,
    TardisFileManifest,
)
from aiquanttrader.market_data import cli
from aiquanttrader.market_data.raw import FinalizedSegment, RawSegmentWriter


def raw_segment(root: Path) -> FinalizedSegment:
    now = time.time_ns()
    writer = RawSegmentWriter(
        root,
        network="mainnet",
        connection_id="ws-cli",
        started_at_ns=now,
        sync_every_records=1,
    )
    writer.append(b'{"channel":"pong"}', receive_ts_ns=now + 1, monotonic_ts_ns=1)
    return writer.finalize(SegmentFinalizationReason.SHUTDOWN)


def test_verify_normalize_dataset_and_health_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    segment = raw_segment(tmp_path / "capture")
    assert cli.main(["verify", str(segment.segment_path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "valid"

    data = tmp_path / "data"
    assert cli.main(["normalize", str(segment.segment_path), "--data-root", str(data)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "valid"

    output = tmp_path / "dataset.json"
    assert (
        cli.main(
            [
                "build-dataset",
                "--data-root",
                str(data),
                "--raw-manifest",
                str(segment.manifest_path),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    assert json.loads(output.read_text())["market_wide_liquidations_available"] is False
    capsys.readouterr()

    state_root = tmp_path / "state"
    state_path = state_root / "market-data" / "recorder-state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_bytes(
        RecorderState(
            status="connected",
            environment="test",
            network="mainnet",
            heartbeat_ts_ns=time.time_ns(),
            reconnect_count=0,
        ).canonical_bytes()
    )
    assert cli.main(["healthcheck", "--state-root", str(state_root)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"


def test_recover_and_pending_normalization_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "data"
    raw_segment(data)
    partial = data / "raw" / "orphan.partial"
    partial.parent.mkdir(parents=True, exist_ok=True)
    partial.write_bytes(b"broken")

    assert cli.main(["recover", "--data-root", str(data)]) == 0
    recovered = json.loads(capsys.readouterr().out)
    assert len(recovered["quarantined"]) == 1

    assert (
        cli.main(
            [
                "normalize-pending",
                "--data-root",
                str(data),
                "--state-root",
                str(tmp_path / "state"),
            ]
        )
        == 0
    )
    batch = json.loads(capsys.readouterr().out)
    assert batch["normalized"] == 1


def test_cli_validation_failures_return_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(
            [
                "healthcheck",
                "--state-root",
                str(tmp_path),
                "--stale-after-seconds",
                "0",
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().err)["status"] == "invalid"

    assert (
        cli.main(
            [
                "build-dataset",
                "--data-root",
                str(tmp_path),
                "--raw-manifest",
                str(tmp_path / "missing.json"),
                "--output",
                str(tmp_path / "out.json"),
                "--max-classified-gap-seconds",
                "-1",
            ]
        )
        == 2
    )


def test_record_dispatch_runs_async_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_record(_args: object) -> int:
        return 7

    monkeypatch.setattr(cli, "_record", fake_record)
    assert (
        cli.main(
            [
                "record",
                "--config-dir",
                "/does/not/matter",
                "--environment",
                "paper",
            ]
        )
        == 7
    )


def test_record_runtime_wires_metrics_catalog_and_recorder(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    class FakeCatalog:
        def __init__(self, _path: Path) -> None:
            calls.append("catalog")

        def __enter__(self) -> FakeCatalog:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class FakeRecorder:
        def __init__(self, **_kwargs: object) -> None:
            calls.append("recorder")

        async def run(self, stop: asyncio.Event) -> None:
            assert not stop.is_set()
            calls.append("run")

    monkeypatch.setattr(cli, "start_http_server", lambda *_args, **_kwargs: calls.append("metrics"))
    monkeypatch.setattr(cli, "ManifestCatalog", FakeCatalog)
    monkeypatch.setattr(cli, "MarketDataRecorder", FakeRecorder)
    args = argparse.Namespace(
        config_dir=config_dir,
        environment="paper",
        duration_seconds=None,
    )

    assert asyncio.run(cli._record(args)) == 0
    assert calls == ["metrics", "catalog", "recorder", "run"]

    args.duration_seconds = 0
    with pytest.raises(ValueError, match="duration"):
        asyncio.run(cli._record(args))


def test_download_tardis_dispatch_registers_manifest(
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registered: list[TardisFileManifest] = []
    manifest = TardisFileManifest(
        data_type="trades",
        date="2024-10-29",
        relative_path="historical/BTC.csv.gz",
        byte_count=10,
        compressed_sha256="a" * 64,
        row_count=1,
        source_url="https://datasets.tardis.dev/v1/hyperliquid/trades/2024/10/29/BTC.csv.gz",
        created_at=datetime.now(UTC),
    )

    class FakeCatalog:
        def __init__(self, _path: Path) -> None:
            pass

        def __enter__(self) -> FakeCatalog:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def register_tardis(self, value: TardisFileManifest) -> None:
            registered.append(value)

    monkeypatch.setattr(cli, "ManifestCatalog", FakeCatalog)
    monkeypatch.setattr(
        cli,
        "download_file",
        lambda **_kwargs: (Path("/data/BTC.csv.gz"), Path("/data/manifest.json"), manifest),
    )
    result = cli.main(
        [
            "download-tardis",
            "--config-dir",
            str(config_dir),
            "--environment",
            "paper",
            "--data-type",
            "trades",
            "--date",
            "2024-10-29",
        ]
    )
    assert result == 0
    assert registered == [manifest]
    assert json.loads(capsys.readouterr().out)["status"] == "valid"
