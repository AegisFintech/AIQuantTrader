from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from aiquanttrader.backtest.models import CalibrationState, ExecutionScenario, QueueModel
from aiquanttrader.domain.execution import OrderIntent, OrderKind, TimeInForce
from aiquanttrader.domain.market import OrderSide
from aiquanttrader.features.models import VolatilityRegime
from aiquanttrader.paper.evidence import evaluate_paper_evidence
from aiquanttrader.paper.journal import PaperJournal, PaperJournalStatistics
from aiquanttrader.paper.models import (
    PaperAccountState,
    PaperEngineCheckpoint,
    PaperEvidencePolicy,
    PaperOrder,
    PaperOrderState,
    PaperRunManifest,
)

HASH = "a" * 64


def scenario(
    scenario_id: str, *, calibrated: bool = True, calibration_hash: str | None = HASH
) -> ExecutionScenario:
    return ExecutionScenario(
        scenario_id=scenario_id,
        calibration_state=(
            CalibrationState.CALIBRATED if calibrated else CalibrationState.UNCALIBRATED
        ),
        calibration_sha256=calibration_hash if calibrated else None,
        tick_size=Decimal("1"),
        lot_size=Decimal("0.001"),
        entry_latency_ns=10,
        response_latency_ns=10,
        feed_latency_offset_ns=0,
        maker_fee_bps=Decimal("1"),
        taker_fee_bps=Decimal("5"),
        queue_model=QueueModel.RISK_ADVERSE,
    )


def manifest(run_id: str, execution: ExecutionScenario) -> PaperRunManifest:
    return PaperRunManifest(
        run_id=run_id,
        environment="paper",
        started_ts_ns=1_000,
        code_identity="a" * 40,
        config_fingerprint="1" * 64,
        feature_config_sha256="2" * 64,
        strategy_config_sha256="3" * 64,
        scenario_id=execution.scenario_id,
        scenario_sha256=execution.sha256(),
        evidence_policy_sha256="4" * 64,
        strategy_id="order-flow-scalper-v1",
    )


def account(ts_ns: int, equity: str = "1000") -> PaperAccountState:
    value = Decimal(equity)
    return PaperAccountState(
        cash_usd=value,
        mark_price=Decimal("100"),
        equity_usd=value,
        day_start_equity_usd=Decimal("1000"),
        high_water_equity_usd=Decimal("1000"),
        utc_day=0,
        updated_ts_ns=ts_ns,
    )


def policy() -> PaperEvidencePolicy:
    return PaperEvidencePolicy(
        policy_id="paper-policy-v1",
        frozen_at_ns=1,
        minimum_observation_ns=100,
        minimum_independent_decisions=10,
        decision_independence_ns=10,
        minimum_fills=5,
        minimum_regimes=3,
        maximum_drawdown_fraction=Decimal("0.05"),
        maximum_denial_fraction=Decimal("0.25"),
        maximum_adverse_markout_bps=Decimal("2"),
        required_sensitivity_scenarios=("pessimistic-v1",),
    )


def statistics() -> PaperJournalStatistics:
    return PaperJournalStatistics(
        started_ts_ns=1_000,
        ended_ts_ns=1_200,
        independent_decisions=10,
        approved_decisions=8,
        denied_decisions=2,
        fills=5,
        markouts=5,
        ending_position_base=Decimal("0"),
        open_orders=0,
        regimes=(VolatilityRegime.HIGH, VolatilityRegime.LOW, VolatilityRegime.NORMAL),
        ending_equity_usd=Decimal("1010"),
        starting_equity_usd=Decimal("1000"),
        maximum_drawdown_fraction=Decimal("0.02"),
        mean_signed_markout_bps=Decimal("-1"),
        drift_evaluated=True,
        maximum_feature_psi=Decimal("0.1"),
        maximum_standardized_mean_shift=Decimal("0.5"),
        completed_drills=(
            "restart",
            "stale_data",
            "daily_loss",
            "drawdown",
            "operator_kill",
            "observability",
        ),
        invalidating_events=(),
    )


def test_paper_journal_restores_account_orders_and_strategy_checkpoint(tmp_path: Path) -> None:
    execution = scenario("baseline-v1")
    run = manifest("run-1", execution)
    journal = PaperJournal((tmp_path / "paper.sqlite3").resolve())
    assert not journal.begin_run(run, account(1_000))
    assert journal.begin_run(run, account(1_000))
    order_intent = OrderIntent(
        intent_id="intent-1",
        strategy_id="order-flow-scalper-v1",
        side=OrderSide.BUY,
        kind=OrderKind.LIMIT,
        quantity_base=Decimal("0.001"),
        limit_price=Decimal("100"),
        time_in_force=TimeInForce.GTC,
        post_only=True,
        created_ts_ns=1_000,
        rationale="journal restart test",
    )
    order = PaperOrder(
        paper_order_id="paper-order-1",
        intent=order_intent,
        state=PaperOrderState.RESTING,
        accepted_ts_ns=1_000,
        effective_ts_ns=1_010,
        updated_ts_ns=1_010,
        queue_ahead_base=Decimal("1"),
    )
    journal.record_cycle("run-1", orders=(order,), fills=(), account=account(1_010))
    checkpoint = PaperEngineCheckpoint(
        run_id="run-1",
        sequence=2,
        checkpoint_ts_ns=1_010,
        strategy_id="order-flow-scalper-v1",
        strategy_memory_json='{"inventory_base":"0","order_revision":2}',
    )
    journal.record_checkpoint(checkpoint)
    journal.close()

    restored = PaperJournal((tmp_path / "paper.sqlite3").resolve())
    assert restored.latest_manifest() == run
    assert restored.latest_account("run-1") == account(1_010)
    assert restored.restore_open_orders("run-1") == (order,)
    assert restored.latest_checkpoint("run-1") == checkpoint
    with pytest.raises(ValueError, match="identity changed"):
        restored.begin_run(run.model_copy(update={"code_identity": "different"}), account(1_000))
    restored.close()


def test_evidence_requires_calibration_sensitivity_samples_regimes_and_drills() -> None:
    baseline = scenario("baseline-v1")
    pessimistic = scenario("pessimistic-v1")
    frozen = policy()
    sensitivity = evaluate_paper_evidence(
        manifest=manifest("sensitivity", pessimistic),
        statistics=statistics(),
        scenario=pessimistic,
        policy=frozen,
        required_scenarios=(pessimistic,),
        generated_ts_ns=2_000,
    )
    assert not sensitivity.promotion_eligible

    report = evaluate_paper_evidence(
        manifest=manifest("baseline", baseline),
        statistics=statistics(),
        scenario=baseline,
        policy=frozen,
        required_scenarios=(pessimistic,),
        sensitivity_reports=(sensitivity,),
        generated_ts_ns=2_001,
    )
    assert report.promotion_eligible
    assert all(gate.passed for gate in report.gates)
    assert report.sensitivity_scenarios == ("pessimistic-v1",)

    uncalibrated = scenario("baseline-v1", calibrated=False, calibration_hash=None)
    blocked = evaluate_paper_evidence(
        manifest=manifest("uncalibrated", uncalibrated),
        statistics=statistics(),
        scenario=uncalibrated,
        policy=frozen,
        required_scenarios=(pessimistic,),
        sensitivity_reports=(sensitivity,),
        generated_ts_ns=2_002,
    )
    assert not blocked.promotion_eligible
    calibration_gate = next(gate for gate in blocked.gates if gate.gate == "calibrated_fill_model")
    assert not calibration_gate.passed


def test_sensitivity_report_must_share_code_features_strategy_and_policy() -> None:
    baseline = scenario("baseline-v1")
    pessimistic = scenario("pessimistic-v1")
    frozen = policy()
    wrong_identity = manifest("wrong", pessimistic).model_copy(
        update={"feature_config_sha256": "9" * 64}
    )
    sensitivity = evaluate_paper_evidence(
        manifest=wrong_identity,
        statistics=statistics(),
        scenario=pessimistic,
        policy=frozen,
        required_scenarios=(pessimistic,),
        generated_ts_ns=3_000,
    )
    report = evaluate_paper_evidence(
        manifest=manifest("baseline", baseline),
        statistics=statistics(),
        scenario=baseline,
        policy=frozen,
        required_scenarios=(pessimistic,),
        sensitivity_reports=(sensitivity,),
        generated_ts_ns=3_001,
    )
    gate = next(item for item in report.gates if item.gate == "sensitivity_scenarios")
    assert not gate.passed


def test_sensitivity_requires_exact_scenario_window_and_non_recursive_gates() -> None:
    baseline = scenario("baseline-v1")
    pessimistic = scenario("pessimistic-v1")
    frozen = policy()
    losing = evaluate_paper_evidence(
        manifest=manifest("losing", pessimistic),
        statistics=replace(statistics(), ending_equity_usd=Decimal("900")),
        scenario=pessimistic,
        policy=frozen,
        required_scenarios=(pessimistic,),
        generated_ts_ns=4_000,
    )
    report = evaluate_paper_evidence(
        manifest=manifest("baseline", baseline),
        statistics=statistics(),
        scenario=baseline,
        policy=frozen,
        required_scenarios=(pessimistic,),
        sensitivity_reports=(losing,),
        generated_ts_ns=4_001,
    )
    sensitivity_gate = next(item for item in report.gates if item.gate == "sensitivity_scenarios")
    assert not sensitivity_gate.passed

    unreviewed = evaluate_paper_evidence(
        manifest=manifest("unreviewed", baseline).model_copy(
            update={"code_identity": "unreviewed-local"}
        ),
        statistics=statistics(),
        scenario=baseline,
        policy=frozen,
        required_scenarios=(pessimistic,),
        generated_ts_ns=4_002,
    )
    identity_gate = next(
        item for item in unreviewed.gates if item.gate == "immutable_code_identity"
    )
    assert not identity_gate.passed

    invalid = evaluate_paper_evidence(
        manifest=manifest("invalid", baseline),
        statistics=replace(statistics(), invalidating_events=("funding_gap",)),
        scenario=baseline,
        policy=frozen,
        required_scenarios=(pessimistic,),
        generated_ts_ns=4_003,
    )
    integrity_gate = next(item for item in invalid.gates if item.gate == "run_integrity")
    assert not integrity_gate.passed
