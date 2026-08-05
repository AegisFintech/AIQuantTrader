from __future__ import annotations

import sqlite3
import time
from decimal import Decimal
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from aiquanttrader.backtest.models import CalibrationState
from aiquanttrader.config import ConfigLoadError, load_config
from aiquanttrader.domain.base import canonical_sha256
from aiquanttrader.domain.market import (
    BookLevel,
    EventHeader,
    L2BookSnapshot,
    PositionSnapshotEvent,
)
from aiquanttrader.market_data.protocol import ParsedFrame
from aiquanttrader.paper.engine import PaperEngineCycle
from aiquanttrader.paper.models import (
    PaperCommandKind,
    PaperExecutionCommand,
    PaperRunManifest,
)
from aiquanttrader.shadow.audit import ShadowAuditJournal
from aiquanttrader.shadow.config import load_shadow_artifacts
from aiquanttrader.shadow.ingress import ShadowIngressReader, ShadowIngressWriter
from aiquanttrader.shadow.models import (
    ShadowDeterminismReport,
    ShadowGatewayStatus,
    ShadowIngressEnvelope,
    ShadowRuntimeStatus,
)
from aiquanttrader.shadow.observer import ShadowObserver, _write
from aiquanttrader.shadow.security import assert_no_ip_egress
from aiquanttrader.shadow.sink import ShadowCommandSink


def _header(ts_ns: int) -> EventHeader:
    return EventHeader(
        event_id=f"book-{ts_ns}",
        event_ts_ns=ts_ns,
        receive_ts_ns=ts_ns,
        connection_id="shadow-test",
    )


def _frame(ts_ns: int) -> ParsedFrame:
    return ParsedFrame(
        channel="l2Book",
        events=(
            L2BookSnapshot(
                header=_header(ts_ns),
                bids=(BookLevel(price=Decimal("99"), size=Decimal("1")),),
                asks=(BookLevel(price=Decimal("101"), size=Decimal("1")),),
            ),
        ),
    )


def _manifest(run_id: str = "shadow-test-run") -> PaperRunManifest:
    return PaperRunManifest(
        run_id=run_id,
        environment="shadow",
        started_ts_ns=1,
        code_identity="test-commit",
        image_identity="sha256:" + "a" * 64,
        config_fingerprint="1" * 64,
        feature_config_sha256="2" * 64,
        strategy_config_sha256="3" * 64,
        scenario_id="scenario-v1",
        scenario_sha256="4" * 64,
        evidence_policy_sha256="5" * 64,
        strategy_id="order-flow-scalper-v1",
    )


def test_shadow_config_and_artifacts_are_strict(config_dir: Path) -> None:
    bundle = load_config(config_dir, "shadow", environ={})
    artifacts = load_shadow_artifacts(config_dir, bundle)
    assert bundle.settings.shadow.enabled
    assert not bundle.settings.execution.enabled
    assert artifacts.evidence_policy.policy_id == "btc-shadow-evidence-v1"
    assert artifacts.paper.strategy_config.strategy_id == "order-flow-scalper-v1"
    assert len(artifacts.paper.evidence_policy_sha256) == 64

    with pytest.raises(ConfigLoadError, match="shadow mode forbids"):
        load_config(
            config_dir,
            "shadow",
            environ={"AQT_NATIVE__EXCHANGE__ACCOUNT_ADDRESS": "0x" + "1" * 40},
        )
    with pytest.raises(ConfigLoadError, match="only in shadow mode"):
        load_config(
            config_dir,
            "research",
            environ={"AQT_NATIVE__SHADOW__ENABLED": "true"},
        )
    with pytest.raises(ConfigLoadError, match="cannot be enabled together"):
        load_config(
            config_dir,
            "shadow",
            environ={"AQT_NATIVE__PAPER__ENABLED": "true"},
        )


def test_durable_ingress_round_trip_is_read_only_and_gap_checked(tmp_path: Path) -> None:
    path = (tmp_path / "ingress" / "frames.sqlite3").resolve()
    writer = ShadowIngressWriter(path, clock_ns=lambda: 1_100)
    first = writer.append(_frame(1_000))
    second = writer.append(ParsedFrame(channel="subscriptionResponse", events=(), is_control=True))
    assert first.sequence == 1
    assert second.sequence == 2
    assert writer.latest_sequence() == 2

    reader = ShadowIngressReader(path)
    records = reader.read_after(0, limit=10)
    assert [record.sequence for record in records] == [1, 2]
    assert isinstance(records[0].envelope.events[0], L2BookSnapshot)
    assert records[1].envelope.is_control
    assert reader.latest_sequence() == 2
    with pytest.raises(sqlite3.OperationalError):
        reader._connection.execute("DELETE FROM frames")
    reader.close()
    writer.close()

    connection = sqlite3.connect(path)
    connection.execute("DELETE FROM frames WHERE sequence = 1")
    connection.commit()
    connection.close()
    gap_reader = ShadowIngressReader(path)
    with pytest.raises(ValueError, match="sequence gap"):
        gap_reader.read_after(0, limit=10)
    gap_reader.close()
    with pytest.raises(ValueError, match="must be absolute"):
        ShadowIngressWriter(Path("relative.sqlite3"))
    with pytest.raises(ValueError, match="must be absolute"):
        ShadowIngressReader(Path("relative.sqlite3"))


def test_ingress_checksum_and_public_event_boundary_fail_closed(tmp_path: Path) -> None:
    path = (tmp_path / "frames.sqlite3").resolve()
    writer = ShadowIngressWriter(path, clock_ns=lambda: 1_100)
    writer.append(_frame(1_000))
    writer.close()
    connection = sqlite3.connect(path)
    connection.execute("UPDATE frames SET envelope_json = ?", (b"{}",))
    connection.commit()
    connection.close()
    reader = ShadowIngressReader(path)
    with pytest.raises(ValueError, match="checksum mismatch"):
        reader.read_after(0, limit=1)
    reader.close()

    private = PositionSnapshotEvent(
        header=_header(2_000),
        size_base=Decimal("0"),
        unrealized_pnl_usd=Decimal("0"),
        leverage=Decimal("1"),
    )
    with pytest.raises(ValueError, match="public BTC events only"):
        ShadowIngressEnvelope(
            channel="webData2",
            events=(private,),
            receive_ts_ns=2_000,
            written_ts_ns=2_000,
        )
    with pytest.raises(ValueError, match="control ingress"):
        ShadowIngressEnvelope(
            channel="control",
            events=_frame(2_000).events,
            is_control=True,
            receive_ts_ns=2_000,
            written_ts_ns=2_000,
        )


def test_network_isolation_checks_ipv4_ipv6_and_missing_procfs(tmp_path: Path) -> None:
    ipv4 = tmp_path / "route"
    ipv6 = tmp_path / "ipv6_route"
    ipv4.write_text("Iface Destination Gateway Flags\nlo 00000000 00000000 0001\n")
    ipv6.write_text("0" * 32 + " 00000000 " + "0 " * 7 + "lo\n")
    assert_no_ip_egress(ipv4, ipv6)

    ipv4.write_text("Iface Destination Gateway Flags\neth0 00000000 00000000 0003\n")
    with pytest.raises(RuntimeError, match="IPv4 default route"):
        assert_no_ip_egress(ipv4, ipv6)
    ipv4.write_text("Iface Destination Gateway Flags\nlo 00000000 00000000 0001\n")
    ipv6.write_text("0" * 32 + " 00000000 " + "0 " * 7 + "eth0\n")
    with pytest.raises(RuntimeError, match="IPv6 default route"):
        assert_no_ip_egress(ipv4, ipv6)
    with pytest.raises(RuntimeError, match="cannot prove"):
        assert_no_ip_egress(tmp_path / "missing", ipv6)


def test_shadow_audit_tracks_latency_health_drills_failures_and_comparison(
    tmp_path: Path,
) -> None:
    audit = ShadowAuditJournal((tmp_path / "audit.sqlite3").resolve())
    manifest = _manifest()
    assert not audit.begin_run(manifest, manifest.image_identity or "")
    assert audit.begin_run(manifest, manifest.image_identity or "")
    with pytest.raises(ValueError, match="image identity"):
        audit.begin_run(manifest, "sha256:" + "b" * 64)
    audit.record_cycle(
        manifest.run_id,
        source_sequence=1,
        completed_ts_ns=2_000,
        ingress_latency_ns=2_000_000,
        cycle_latency_ns=1_000_000,
        feature_sha256="6" * 64,
        decisions=1,
        commands=1,
    )
    with pytest.raises(ValueError, match="cannot be negative"):
        audit.record_cycle(
            manifest.run_id,
            source_sequence=2,
            completed_ts_ns=3_000,
            ingress_latency_ns=-1,
            cycle_latency_ns=1,
            feature_sha256="6" * 64,
            decisions=0,
            commands=0,
        )
    audit.record_health(
        manifest.run_id,
        sample_ts_ns=2_000,
        healthy=True,
        ingress_sequence=1,
        ingress_lag_ns=1,
    )
    audit.record_health(
        manifest.run_id,
        sample_ts_ns=3_000,
        healthy=False,
        ingress_sequence=1,
        ingress_lag_ns=2,
    )
    evidence = tmp_path / "drill.json"
    evidence.write_text('{"result":"pass"}\n')
    assert (
        len(
            audit.record_drill(
                manifest.run_id,
                "host_reboot",
                completed_ts_ns=3_000,
                evidence_path=evidence,
            )
        )
        == 64
    )
    audit.record_failure(
        manifest.run_id,
        failed_ts_ns=4_000,
        kind="clock_failure",
        detail="injected skew",
    )
    comparison_payload = {
        "schema_version": 1,
        "source_run_id": manifest.run_id,
        "replay_run_id": "shadow-replay",
        "source_manifest_sha256": manifest.sha256(),
        "replay_manifest_sha256": "7" * 64,
        "compared_decisions": 10,
        "decision_mismatches": 0,
        "compared_commands": 10,
        "command_mismatches": 0,
        "generated_ts_ns": 5_000,
    }
    comparison = ShadowDeterminismReport.model_validate(
        {"report_id": canonical_sha256(comparison_payload), **comparison_payload}
    )
    audit.record_comparison(manifest.run_id, comparison)
    assert audit.latest_comparison(manifest.run_id) == comparison
    stats = audit.statistics(
        manifest.run_id,
        observation_ns=2_000,
        health_interval_ns=1_000,
    )
    assert stats.cycle_samples == 1
    assert stats.health_samples == 2
    assert stats.availability_fraction == Decimal("0.5")
    assert stats.ingress_latency_p99_ms == Decimal("2")
    assert stats.completed_drills == ("host_reboot",)
    assert stats.invalidating_events == ("clock_failure",)
    audit.close()


def test_read_only_observer_validates_status_and_metrics(tmp_path: Path) -> None:
    state = tmp_path / "state"
    shadow = state / "shadow"
    shadow.mkdir(parents=True)
    now = time.time_ns()
    status = ShadowRuntimeStatus(
        status="ready",
        run_id="shadow-run",
        heartbeat_ts_ns=now,
        last_public_data_ts_ns=now,
        last_ingress_sequence=10,
        ingress_lag_ns=1,
        feed_connected=True,
        feature_ready=True,
        operator_kill=False,
        strategy_id="order-flow-scalper-v1",
        scenario_id="scenario-v1",
        scenario_sha256="1" * 64,
        calibration_state=CalibrationState.CALIBRATED,
        config_fingerprint="2" * 64,
        image_identity="sha256:" + "3" * 64,
        open_orders=0,
        decisions=1,
        commands=1,
        fills=0,
    )
    (shadow / "status.json").write_bytes(status.canonical_bytes())
    (shadow / "metrics.prom").write_bytes(b"aqt_shadow_network_egress_capability 0\n")
    observer = ShadowObserver(state, stale_after_ms=5_000)
    observed, ready, age = observer.status()
    assert observed.run_id == "shadow-run"
    assert ready and age >= 0
    assert observer.metrics().startswith(b"aqt_shadow")

    stale = status.model_copy(update={"heartbeat_ts_ns": now - 10_000_000_000})
    (shadow / "status.json").write_bytes(stale.canonical_bytes())
    assert not observer.status()[1]
    with pytest.raises(ValueError, match="positive"):
        ShadowObserver(state, stale_after_ms=0)


def test_gateway_status_contract_is_always_credential_free() -> None:
    status = ShadowGatewayStatus(
        status="ready",
        heartbeat_ts_ns=1,
        last_ingress_sequence=2,
        last_receive_ts_ns=1,
    )
    assert status.raw_first
    assert status.credential_capability == "none"


def test_shadow_sink_and_observer_writer_fail_closed() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        ShadowCommandSink(restored_commands=-1)
    sink = ShadowCommandSink()
    missing = cast(
        PaperEngineCycle,
        SimpleNamespace(
            decisions=(SimpleNamespace(risk_decision=SimpleNamespace(allowed=True)),),
            commands=(),
        ),
    )
    with pytest.raises(ValueError, match="incomplete approved-submit"):
        sink.accept(missing)
    invalid_command = PaperExecutionCommand.model_construct(
        command_id="invalid-command",
        sequence=0,
        command_ts_ns=1,
        kind=PaperCommandKind.CANCEL,
        intent_id="intent-1",
        strategy_id="strategy-1",
        sink=cast(Any, "exchange"),
    )
    escaping = cast(
        PaperEngineCycle,
        SimpleNamespace(decisions=(), commands=(invalid_command,)),
    )
    with pytest.raises(ValueError, match="escape"):
        sink.accept(escaping)

    class FakeHandler:
        def __init__(self) -> None:
            self.status: HTTPStatus | None = None
            self.headers: dict[str, str] = {}
            self.wfile = BytesIO()

        def send_response(self, status: HTTPStatus) -> None:
            self.status = status

        def send_header(self, name: str, value: str) -> None:
            self.headers[name] = value

        def end_headers(self) -> None:
            return None

    handler = FakeHandler()
    _write(cast(Any, handler), HTTPStatus.OK, b"ready", "text/plain")
    assert handler.status is HTTPStatus.OK
    assert handler.headers["Content-Length"] == "5"
    assert handler.wfile.getvalue() == b"ready"
