from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from aiquanttrader.backtest.kernel import KernelBookLevel, KernelMarketState, KernelTrade
from aiquanttrader.backtest.models import CalibrationState, ExecutionScenario, QueueModel
from aiquanttrader.config.models import ExecutionConfig, RiskLimits
from aiquanttrader.domain.execution import RiskReason
from aiquanttrader.domain.market import AggressorSide
from aiquanttrader.features.models import FeatureEngineConfig
from aiquanttrader.paper.config import PaperArtifacts
from aiquanttrader.paper.engine import PaperTradingEngine
from aiquanttrader.paper.journal import PaperJournal
from aiquanttrader.paper.metrics import PaperMetrics
from aiquanttrader.paper.models import PaperEvidencePolicy, PaperRunManifest
from aiquanttrader.risk.kill_switch import KillSwitchStore
from aiquanttrader.strategies.scalper import (
    OrderFlowScalperConfig,
    ScalperEntryStyle,
)


def artifacts() -> PaperArtifacts:
    execution = ExecutionScenario(
        scenario_id="paper-engine-v1",
        calibration_state=CalibrationState.UNCALIBRATED,
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
        maximum_input_age_ns=1_000,
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
    policy = PaperEvidencePolicy(
        policy_id="paper-engine-policy-v1",
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
    return PaperArtifacts(
        scenario=execution,
        sensitivity_scenarios=(),
        feature_config=features,
        strategy_config=strategy,
        evidence_policy=policy,
        feature_config_sha256="1" * 64,
        strategy_config_sha256="2" * 64,
        evidence_policy_sha256="3" * 64,
    )


def state(sequence: int, *, buyer_trade: bool = False) -> KernelMarketState:
    exchange_ts = 1_000 + sequence * 100
    trades = (
        (
            KernelTrade(
                exchange_ts_ns=exchange_ts,
                observed_ts_ns=exchange_ts + 10,
                price=Decimal("101"),
                size=Decimal("1"),
                aggressor=AggressorSide.BUYER,
            ),
        )
        if buyer_trade
        else ()
    )
    return KernelMarketState(
        exchange_ts_ns=exchange_ts,
        book_exchange_ts_ns=exchange_ts,
        observed_ts_ns=exchange_ts + 10,
        sequence=sequence,
        bids=(KernelBookLevel(price=Decimal("100"), size=Decimal("1")),),
        asks=(KernelBookLevel(price=Decimal("101"), size=Decimal("1")),),
        trades=trades,
    )


def build_engine(
    tmp_path: Path, journal: PaperJournal
) -> tuple[PaperTradingEngine, KillSwitchStore]:
    configured = artifacts()
    manifest = PaperRunManifest(
        run_id="paper-engine-run",
        environment="paper",
        started_ts_ns=1_010,
        code_identity="test-commit",
        config_fingerprint="4" * 64,
        feature_config_sha256=configured.feature_config_sha256,
        strategy_config_sha256=configured.strategy_config_sha256,
        scenario_id=configured.scenario.scenario_id,
        scenario_sha256=configured.scenario.sha256(),
        evidence_policy_sha256=configured.evidence_policy_sha256,
        strategy_id=configured.strategy_config.strategy_id,
    )
    kill = KillSwitchStore((tmp_path / "kill.json").resolve())
    engine = PaperTradingEngine(
        manifest=manifest,
        artifacts=configured,
        risk_limits=RiskLimits(
            public_data_stale_after_ms=500,
            private_data_stale_after_ms=1_000,
        ),
        execution_config=ExecutionConfig(),
        initial_equity_usd=Decimal("1000"),
        initial_mark_price=Decimal("100.5"),
        journal=journal,
        kill_switch=kill,
        started_ts_ns=1_010,
        markout_horizon_ns=100,
    )
    return engine, kill


def test_live_feature_strategy_risk_simulation_and_restart_are_one_path(tmp_path: Path) -> None:
    path = (tmp_path / "paper.sqlite3").resolve()
    journal = PaperJournal(path)
    engine, _ = build_engine(tmp_path, journal)

    warmup = engine.on_market(state(0))
    assert not warmup.features.ready
    assert warmup.decisions == ()
    first_evaluation = journal.strategy_evaluations(engine.manifest.run_id)[0]
    assert first_evaluation.sequence == 0
    assert first_evaluation.decision.reason == "no_action"
    signal = engine.on_market(state(1, buyer_trade=True))
    assert signal.features.ready
    assert len(signal.decisions) == 1
    assert signal.decisions[0].risk_decision.allowed
    assert len(engine.simulator.open_orders) == 1

    filled = engine.on_market(state(2, buyer_trade=True))
    assert len(filled.fills) == 1
    assert engine.simulator.account.position_base == Decimal("0.001")
    assert engine.decision_count >= 2
    marked = engine.on_market(state(3))
    assert marked.markouts
    evaluations = journal.strategy_evaluations(engine.manifest.run_id)
    assert len(evaluations) == 4
    summary = journal.strategy_evaluation_summary(engine.manifest.run_id)
    assert summary.evaluations == 4
    assert sum(item.count for item in summary.action_counts) == 4
    assert summary.latest_forecast is None
    registry = CollectorRegistry()
    metrics = PaperMetrics(registry)
    metrics.observe_cycle(
        engine,
        filled,
        latency_seconds=0.001,
        initial_equity_usd=1_000,
    )
    metrics.observe_cycle(
        engine,
        marked,
        latency_seconds=0.001,
        initial_equity_usd=1_000,
    )
    with pytest.raises(ValueError, match="cannot be negative"):
        metrics.observe_stale_trade_exclusions(-1)
    with pytest.raises(ValueError, match="cannot be negative"):
        metrics.observe_stale_book_exclusions(-1)
    with pytest.raises(ValueError, match="cannot be negative"):
        metrics.observe_stale_bbo_exclusions(-1)
    with pytest.raises(ValueError, match="depth must be positive"):
        metrics.observe_market_state(depth_levels=0, used_l2_depth=False)
    payload = generate_latest(registry)
    assert b'aqt_paper_risk_decisions_total{reason="approved",result="approved"}' in payload
    assert b'aqt_paper_fills_total{liquidity="taker"}' in payload
    account_before_restart = engine.simulator.account
    journal.close()

    restored_journal = PaperJournal(path)
    restored, _ = build_engine(tmp_path, restored_journal)
    assert restored.resumed
    assert restored.simulator.account == account_before_restart
    assert restored.decision_count == engine.decision_count
    restored.on_market(state(4))
    assert restored_journal.strategy_evaluations(engine.manifest.run_id)[-1].sequence == 4
    restored_journal.close()


def test_stale_data_and_operator_kill_cancel_orders_and_create_drill_evidence(
    tmp_path: Path,
) -> None:
    journal = PaperJournal((tmp_path / "paper.sqlite3").resolve())
    engine, kill = build_engine(tmp_path, journal)
    engine.on_market(state(0))
    engine.on_market(state(1, buyer_trade=True))
    assert engine.simulator.open_orders

    stale_at = state(1).observed_ts_ns + 500_000_001
    reasons = engine.watchdog(stale_at, recorder_connected=True)
    assert RiskReason.PUBLIC_DATA_STALE in reasons
    engine.watchdog(stale_at + 21, recorder_connected=True)
    assert not engine.simulator.open_orders

    kill.activate(actor="test", reason="operator kill drill")
    reasons = engine.watchdog(stale_at + 30, recorder_connected=True)
    assert RiskReason.OPERATOR_KILL in reasons
    stats = journal.statistics(engine.manifest.run_id)
    assert "stale_data" in stats.completed_drills
    assert "operator_kill" in stats.completed_drills
    journal.close()


def test_watchdog_fills_approved_market_order_before_stale_cancellation(tmp_path: Path) -> None:
    journal = PaperJournal((tmp_path / "paper.sqlite3").resolve())
    engine, _ = build_engine(tmp_path, journal)
    engine.on_market(state(0))
    signal_market = state(1, buyer_trade=True)
    engine.on_market(signal_market)
    assert engine.simulator.open_orders

    reasons = engine.watchdog(
        signal_market.observed_ts_ns + engine.artifacts.scenario.entry_latency_ns,
        recorder_connected=True,
    )
    assert RiskReason.PUBLIC_DATA_STALE not in reasons
    assert len(engine.last_watchdog_update.fills) == 1
    assert not engine.simulator.open_orders
    assert engine.simulator.account.position_base == Decimal("0.001")
    journal.close()


def test_live_feature_drift_windows_are_persisted_and_restartable(tmp_path: Path) -> None:
    journal = PaperJournal((tmp_path / "paper.sqlite3").resolve())
    engine, _ = build_engine(tmp_path, journal)
    latest = None
    for sequence in range(42):
        latest = engine.on_market(state(sequence))
    assert latest is not None
    assert latest.drift_report is not None
    registry = CollectorRegistry()
    PaperMetrics(registry).observe_cycle(
        engine,
        latest,
        latency_seconds=0.001,
        initial_equity_usd=1_000,
    )
    assert b"aqt_paper_drift_ready 1.0" in generate_latest(registry)
    stats = journal.statistics(engine.manifest.run_id)
    assert stats.drift_evaluated
    assert (
        len(
            journal.feature_vectors(
                engine.manifest.run_id,
                baseline_samples=20,
                current_samples=20,
            )
        )
        == 40
    )
    journal.close()
