from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from prometheus_client import CollectorRegistry

from aiquanttrader.backtest.models import (
    CalibrationState,
    ExecutionScenario,
    QueueModel,
)
from aiquanttrader.config import load_config
from aiquanttrader.domain.market import (
    AggressorSide,
    BookLevel,
    EventHeader,
    FundingEvent,
    L2BookSnapshot,
    MarkPriceEvent,
    TradeEvent,
)
from aiquanttrader.features.models import FeatureEngineConfig
from aiquanttrader.market_data.protocol import ParsedFrame
from aiquanttrader.paper.config import PaperArtifacts
from aiquanttrader.paper.journal import PaperJournal
from aiquanttrader.paper.models import PaperEvidencePolicy
from aiquanttrader.risk.kill_switch import KillSwitchStore
from aiquanttrader.shadow.audit import ShadowAuditJournal
from aiquanttrader.shadow.config import ShadowArtifacts
from aiquanttrader.shadow.evidence import compare_shadow_runs
from aiquanttrader.shadow.gateway import ShadowGatewayService
from aiquanttrader.shadow.ingress import (
    ShadowIngressReader,
    ShadowIngressRecord,
    ShadowIngressWriter,
)
from aiquanttrader.shadow.metrics import ShadowMetrics
from aiquanttrader.shadow.models import (
    ShadowEvidencePolicy,
    ShadowIngressEnvelope,
    ShadowRuntimeStatus,
)
from aiquanttrader.shadow.service import ShadowClockError, ShadowEngineService
from aiquanttrader.strategies.scalper import (
    OrderFlowScalperConfig,
    ScalperEntryStyle,
)

IMAGE = "sha256:" + "a" * 64


def _artifacts() -> ShadowArtifacts:
    scenario = ExecutionScenario(
        scenario_id="shadow-calibrated-v1",
        calibration_state=CalibrationState.CALIBRATED,
        calibration_sha256="9" * 64,
        tick_size=Decimal("1"),
        lot_size=Decimal("0.001"),
        entry_latency_ns=10,
        response_latency_ns=20,
        feed_latency_offset_ns=0,
        maker_fee_bps=Decimal("1"),
        taker_fee_bps=Decimal("1"),
        queue_model=QueueModel.RISK_ADVERSE,
        taker_slippage_bps=Decimal("0"),
    )
    features = FeatureEngineConfig(
        depth_levels=1,
        flow_window_ns=10_000_000_000,
        volatility_window_ns=10_000_000_000,
        spread_window_ns=10_000_000_000,
        markout_horizon_ns=100,
        warmup_samples=2,
        maximum_input_age_ns=2_000_000_000,
        low_volatility_bps=Decimal("1"),
        high_volatility_bps=Decimal("1000"),
        inventory_limit_base=Decimal("0.05"),
    )
    strategy = OrderFlowScalperConfig(
        entry_style=ScalperEntryStyle.TAKER,
        order_quantity_base=Decimal("0.001"),
        max_abs_inventory_base=Decimal("0.01"),
        imbalance_weight_bps=Decimal("0"),
        flow_weight_bps=Decimal("10"),
        momentum_weight=Decimal("0"),
        safety_margin_bps=Decimal("0.01"),
        maximum_spread_bps=Decimal("200"),
        signal_threshold_bps=Decimal("0.1"),
        cooldown_ns=0,
        reject_high_volatility=False,
    )
    engine_policy = PaperEvidencePolicy(
        policy_id="shadow-engine-policy-v1",
        frozen_at_ns=1,
        minimum_observation_ns=1,
        minimum_independent_decisions=1,
        decision_independence_ns=1,
        minimum_fills=1,
        minimum_regimes=1,
        maximum_drawdown_fraction=Decimal("0.5"),
        maximum_denial_fraction=Decimal("1"),
        maximum_adverse_markout_bps=Decimal("100"),
        drift_baseline_samples=20,
        drift_window_samples=20,
        drift_evaluation_interval_samples=1,
        maximum_feature_psi=Decimal("100"),
        maximum_standardized_mean_shift=Decimal("1000000"),
        required_sensitivity_scenarios=("pessimistic-v1",),
    )
    paper = PaperArtifacts(
        scenario=scenario,
        sensitivity_scenarios=(),
        feature_config=features,
        strategy_config=strategy,
        evidence_policy=engine_policy,
        feature_config_sha256="1" * 64,
        strategy_config_sha256="2" * 64,
        evidence_policy_sha256="3" * 64,
    )
    evidence = ShadowEvidencePolicy(
        policy_id="shadow-test-policy-v1",
        frozen_at_ns=1,
        minimum_observation_ns=1,
        minimum_independent_decisions=1,
        minimum_fills=1,
        minimum_regimes=1,
        minimum_availability_fraction=Decimal("0.5"),
        maximum_ingress_latency_p99_ms=Decimal("1000"),
        maximum_cycle_latency_p99_ms=Decimal("1000"),
        maximum_drawdown_fraction=Decimal("0.5"),
        maximum_denial_fraction=Decimal("1"),
        maximum_adverse_markout_bps=Decimal("100"),
        maximum_feature_psi=Decimal("100"),
        maximum_standardized_mean_shift=Decimal("1000000"),
        minimum_determinism_decisions=1,
        required_sensitivity_scenarios=("pessimistic-v1",),
        required_drills=("operator_kill",),
    )
    return ShadowArtifacts(
        paper=paper,
        evidence_policy=evidence,
        evidence_policy_sha256="4" * 64,
        engine_policy_sha256="5" * 64,
    )


def _header(ts_ns: int, event_id: str) -> EventHeader:
    return EventHeader(
        event_id=event_id,
        event_ts_ns=ts_ns,
        receive_ts_ns=ts_ns,
        connection_id="shadow-integration",
    )


def _context(ts_ns: int) -> ParsedFrame:
    return ParsedFrame(
        channel="activeAssetCtx",
        events=(
            MarkPriceEvent(header=_header(ts_ns, f"mark-{ts_ns}"), mark_price=Decimal("100")),
            FundingEvent(
                header=_header(ts_ns, f"funding-{ts_ns}"),
                funding_rate=Decimal("0.00001"),
                next_funding_ts_ns=ts_ns + 3_600_000_000_000,
            ),
        ),
    )


def _trade(ts_ns: int) -> ParsedFrame:
    return ParsedFrame(
        channel="trades",
        events=(
            TradeEvent(
                header=_header(ts_ns, f"trade-{ts_ns}"),
                trade_id=f"trade-{ts_ns}",
                price=Decimal("101"),
                size=Decimal("1"),
                aggressor=AggressorSide.BUYER,
            ),
        ),
    )


def _book(ts_ns: int) -> ParsedFrame:
    return ParsedFrame(
        channel="l2Book",
        events=(
            L2BookSnapshot(
                header=_header(ts_ns, f"book-{ts_ns}"),
                bids=(BookLevel(price=Decimal("99"), size=Decimal("1")),),
                asks=(BookLevel(price=Decimal("101"), size=Decimal("1")),),
            ),
        ),
    )


def _service(
    *,
    tmp_path: Path,
    config_dir: Path,
    ingress: ShadowIngressReader,
    journal: PaperJournal,
    audit: ShadowAuditJournal,
    replay: bool = False,
    start_sequence: int | None = None,
) -> ShadowEngineService:
    bundle = load_config(
        config_dir,
        "shadow",
        environ={
            "AQT_NATIVE__STORAGE__DATA_ROOT": str(tmp_path / "data"),
            "AQT_NATIVE__STORAGE__STATE_ROOT": str(tmp_path / "state"),
            "AQT_NATIVE__SHADOW__MAXIMUM_INGRESS_LAG_MS": "30000",
        },
    )
    metrics_path = tmp_path / ("replay-metrics.prom" if replay else "metrics.prom")
    return ShadowEngineService(
        bundle=bundle,
        artifacts=_artifacts(),
        ingress=ingress,
        journal=journal,
        audit=audit,
        kill_switch=KillSwitchStore((tmp_path / "kill.json").resolve()),
        code_identity="test-commit",
        image_identity=IMAGE,
        metrics=ShadowMetrics(CollectorRegistry(), metrics_path.resolve()),
        replay_mode=replay,
        start_sequence=start_sequence,
        status_path=(tmp_path / ("replay-status.json" if replay else "status.json")).resolve(),
    )


def test_shadow_metrics_count_stale_trade_exclusions(tmp_path: Path) -> None:
    metrics_path = (tmp_path / "shadow-metrics.prom").resolve()
    metrics = ShadowMetrics(CollectorRegistry(), metrics_path)
    with pytest.raises(ValueError, match="cannot be negative"):
        metrics.observe_stale_trade_exclusions(-1)
    metrics.observe_stale_trade_exclusions(2)
    metrics.publish()
    assert b"aqt_shadow_stale_trades_excluded_total 2.0" in metrics_path.read_bytes()


def test_isolated_service_records_commands_and_replays_decisions_exactly(
    tmp_path: Path, config_dir: Path
) -> None:
    ingress_path = (tmp_path / "ingress.sqlite3").resolve()
    writer = ShadowIngressWriter(ingress_path)
    source_reader = ShadowIngressReader(ingress_path)
    base = time.time_ns() - 1_000_000
    writer.append(_context(base - 200))
    writer.append(_book(base - 100))
    source_journal = PaperJournal((tmp_path / "source.sqlite3").resolve())
    source_audit = ShadowAuditJournal((tmp_path / "source-audit.sqlite3").resolve())
    source = _service(
        tmp_path=tmp_path,
        config_dir=config_dir,
        ingress=source_reader,
        journal=source_journal,
        audit=source_audit,
    )
    assert source.cursor == 2
    frames = (
        _context(base),
        _book(base + 100),
        _trade(base + 200),
        _book(base + 300),
        _trade(base + 400),
        _book(base + 500),
    )
    for frame in frames:
        writer.append(frame)
        records = source_reader.read_after(source.cursor, limit=10)
        for record in records:
            asyncio.run(source.consume_record(record))

    assert source.engine is not None
    source_manifest = source.engine.manifest
    assert source_manifest.source_start_sequence == 2
    source_stats = source_journal.statistics(source_manifest.run_id)
    assert source_stats.approved_decisions >= 1
    assert source_stats.submit_commands == source_stats.approved_decisions
    assert all(
        command.sink == "counterfactual_only"
        for command in source_journal.commands(source_manifest.run_id)
    )
    assert (tmp_path / "metrics.prom").read_text().find("aqt_shadow_commands_total") >= 0
    status = ShadowRuntimeStatus.model_validate_json((tmp_path / "status.json").read_bytes())
    assert status.credential_capability == "none"
    assert status.ip_network_capability == "none"
    assert status.commands == source_stats.commands

    replay_reader = ShadowIngressReader(ingress_path)
    replay_journal = PaperJournal((tmp_path / "replay.sqlite3").resolve())
    replay_audit = ShadowAuditJournal((tmp_path / "replay-audit.sqlite3").resolve())
    replay = _service(
        tmp_path=tmp_path,
        config_dir=config_dir,
        ingress=replay_reader,
        journal=replay_journal,
        audit=replay_audit,
        replay=True,
        start_sequence=source_manifest.source_start_sequence,
    )
    for record in replay_reader.read_after(replay.cursor, limit=100):
        asyncio.run(replay.consume_record(record))
    assert replay.engine is not None
    comparison = compare_shadow_runs(
        source_journal,
        replay_journal,
        source_run_id=source_manifest.run_id,
        replay_run_id=replay.engine.manifest.run_id,
        generated_ts_ns=base + 1_000,
    )
    assert comparison.decision_mismatches == 0
    assert comparison.command_mismatches == 0
    assert comparison.compared_decisions >= 1

    replay_reader.close()
    replay_journal.close()
    replay_audit.close()
    source_reader.close()
    source_journal.close()
    source_audit.close()
    writer.close()


def test_service_rejects_clock_lead_and_gateway_is_credential_free(
    tmp_path: Path, config_dir: Path
) -> None:
    ingress_path = (tmp_path / "ingress.sqlite3").resolve()
    writer = ShadowIngressWriter(ingress_path)
    reader = ShadowIngressReader(ingress_path)
    journal = PaperJournal((tmp_path / "source.sqlite3").resolve())
    audit = ShadowAuditJournal((tmp_path / "audit.sqlite3").resolve())
    service = _service(
        tmp_path=tmp_path,
        config_dir=config_dir,
        ingress=reader,
        journal=journal,
        audit=audit,
    )
    future = time.time_ns() + 10_000_000_000
    envelope = ShadowIngressEnvelope(
        channel="l2Book",
        events=_book(future).events,
        receive_ts_ns=future,
        written_ts_ns=future,
    )
    with pytest.raises(ShadowClockError, match="gateway clock leads"):
        asyncio.run(service.consume_record(ShadowIngressRecord(1, envelope, envelope.sha256())))

    bundle = load_config(
        config_dir,
        "shadow",
        environ={
            "AQT_NATIVE__STORAGE__DATA_ROOT": str(tmp_path / "data"),
            "AQT_NATIVE__STORAGE__STATE_ROOT": str(tmp_path / "gateway-state"),
        },
    )
    gateway = ShadowGatewayService(
        bundle=bundle,
        ingress=writer,
        registry=CollectorRegistry(),
    )
    asyncio.run(gateway.consume_frame(_book(time.time_ns())))
    gateway_status = gateway.status_path.read_text()
    assert '"credential_capability":"none"' in gateway_status
    assert '"raw_first":true' in gateway_status

    reader.close()
    journal.close()
    audit.close()
    writer.close()


def test_shadow_service_watchdog_stop_and_failure_paths(tmp_path: Path, config_dir: Path) -> None:
    ingress_path = (tmp_path / "ingress.sqlite3").resolve()
    writer = ShadowIngressWriter(ingress_path)
    reader = ShadowIngressReader(ingress_path)
    journal = PaperJournal((tmp_path / "source.sqlite3").resolve())
    audit = ShadowAuditJournal((tmp_path / "audit.sqlite3").resolve())
    service = _service(
        tmp_path=tmp_path,
        config_dir=config_dir,
        ingress=reader,
        journal=journal,
        audit=audit,
    )
    base = time.time_ns() - 1_000_000
    for frame in (_context(base), _book(base + 100)):
        writer.append(frame)
        for record in reader.read_after(service.cursor, limit=10):
            asyncio.run(service.consume_record(record))
    assert service.engine is not None
    service._watchdog(time.time_ns())
    service.kill_switch.activate(actor="test", reason="watchdog branch")
    service._watchdog(time.time_ns() + 1_000_000)
    assert service._current_status() == "degraded"

    stopped = asyncio.Event()
    stopped.set()
    asyncio.run(service.run(stopped))
    status = ShadowRuntimeStatus.model_validate_json(service.status_path.read_bytes())
    assert status.status == "stopped"

    class FailingIngress:
        def read_after(self, sequence: int, *, limit: int) -> tuple[ShadowIngressRecord, ...]:
            raise OSError(f"injected ingress failure at {sequence}/{limit}")

    service.ingress = cast(ShadowIngressReader, FailingIngress())
    with pytest.raises(OSError, match="injected ingress failure"):
        asyncio.run(service.run(asyncio.Event()))
    failed = ShadowRuntimeStatus.model_validate_json(service.status_path.read_bytes())
    assert failed.status == "failed"
    assert (
        "service_failure"
        in audit.statistics(
            service.engine.manifest.run_id,
            observation_ns=1,
            health_interval_ns=1,
        ).invalidating_events
    )
    reader.close()
    journal.close()
    audit.close()
    writer.close()


def test_gateway_run_uses_raw_recorder_and_publishes_terminal_states(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle = load_config(
        config_dir,
        "shadow",
        environ={
            "AQT_NATIVE__STORAGE__DATA_ROOT": str(tmp_path / "data"),
            "AQT_NATIVE__STORAGE__STATE_ROOT": str(tmp_path / "gateway-state"),
        },
    )
    writer = ShadowIngressWriter((tmp_path / "ingress.sqlite3").resolve())

    class FakeCatalog:
        def __init__(self, path: Path) -> None:
            assert path.name == "raw-catalog.duckdb"

        def __enter__(self) -> FakeCatalog:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class FakeRecorder:
        fail = False

        def __init__(self, **kwargs: Any) -> None:
            self.consumer = kwargs["frame_consumer"]

        async def run(self, stop: asyncio.Event) -> None:
            if self.fail:
                raise OSError("injected recorder failure")
            await self.consumer(_book(time.time_ns()))
            stop.set()

    monkeypatch.setattr("aiquanttrader.shadow.gateway.ManifestCatalog", FakeCatalog)
    monkeypatch.setattr("aiquanttrader.shadow.gateway.MarketDataRecorder", FakeRecorder)
    gateway = ShadowGatewayService(
        bundle=bundle,
        ingress=writer,
        registry=CollectorRegistry(),
    )
    asyncio.run(gateway.run(asyncio.Event()))
    assert '"status":"stopped"' in gateway.status_path.read_text()
    assert writer.latest_sequence() == 1

    FakeRecorder.fail = True
    with pytest.raises(OSError, match="injected recorder failure"):
        asyncio.run(gateway.run(asyncio.Event()))
    assert '"status":"failed"' in gateway.status_path.read_text()
    writer.close()
