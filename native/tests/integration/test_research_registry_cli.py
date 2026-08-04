from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aiquanttrader_native.domain.governance import ActorKind, PromotionStage
from aiquanttrader_native.research.cli import main
from aiquanttrader_native.research.governance import evaluate_challenger
from aiquanttrader_native.research.models import (
    ChampionChallengerReport,
    NegativeControlReport,
    PromotionMetrics,
    PromotionPolicy,
    ResearchExperimentManifest,
)
from aiquanttrader_native.research.registry import ResearchRegistry

CREATED_AT = datetime(2026, 8, 4, tzinfo=UTC)


def metrics() -> PromotionMetrics:
    return PromotionMetrics(
        post_cost_pnl_usd=500.0,
        maximum_drawdown_usd=100.0,
        tail_loss_99_usd=50.0,
        maximum_abs_inventory_base=0.01,
        fill_count=1_500,
        maker_ratio=0.8,
        adverse_selection_bps=1.0,
        decision_latency_p99_ms=3.0,
        fold_consistency=0.9,
        drift_psi_max=0.1,
        operational_failure_count=0,
    )


def controls() -> NegativeControlReport:
    return NegativeControlReport(
        randomized_label_score=10.0,
        randomized_label_minimum_mse=1.0,
        no_signal_decision_count=0,
        no_signal_report_sha256="f" * 64,
        randomized_seed=3,
    )


def promotion_policy() -> PromotionPolicy:
    return PromotionPolicy(
        policy_id="registry-policy",
        minimum_post_cost_pnl_usd=1.0,
        maximum_drawdown_usd=500.0,
        maximum_tail_loss_99_usd=100.0,
        maximum_abs_inventory_base=0.05,
        minimum_fill_count=100,
        minimum_maker_ratio=0.5,
        maximum_adverse_selection_bps=3.0,
        maximum_decision_latency_p99_ms=10.0,
        minimum_fold_consistency=0.7,
        maximum_drift_psi=0.2,
        minimum_champion_improvement_usd=10.0,
    )


def promotion_report(
    experiment_id: str, *, challenger: PromotionMetrics | None = None
) -> ChampionChallengerReport:
    return evaluate_challenger(
        challenger_experiment_id=experiment_id,
        challenger=metrics() if challenger is None else challenger,
        champion_experiment_id=None,
        champion=None,
        policy=promotion_policy(),
        negative_controls=controls(),
    )


def experiment(
    experiment_id: str = "experiment-001",
    *,
    stage: PromotionStage = PromotionStage.DRAFT,
    report: ChampionChallengerReport | None = None,
    challenger_metrics: PromotionMetrics | None = None,
) -> ResearchExperimentManifest:
    bound_metrics = metrics() if challenger_metrics is None else challenger_metrics
    bound_report = (
        promotion_report(experiment_id, challenger=bound_metrics) if report is None else report
    )
    return ResearchExperimentManifest(
        experiment_id=experiment_id,
        created_at=CREATED_AT,
        stage=stage,
        strategy_id="avellaneda-stoikov-v1",
        code_sha256="1" * 64,
        dataset_sha256="2" * 64,
        feature_schema_sha256="3" * 64,
        configuration_sha256="4" * 64,
        dependency_lock_sha256="5" * 64,
        model_sha256="6" * 64,
        search_receipt_sha256="7" * 64,
        validation_plan_sha256="8" * 64,
        scenario_sha256s=("9" * 64, "a" * 64),
        parameters={"risk_aversion": "0.00001"},
        metrics=bound_metrics,
        negative_controls=controls(),
        report_sha256=bound_report.sha256(),
    )


def advance_to_shadow(registry: ResearchRegistry, experiment_id: str) -> None:
    for offset, stage in enumerate(
        (
            PromotionStage.CANDIDATE,
            PromotionStage.BACKTEST_PASSED,
            PromotionStage.WALK_FORWARD_PASSED,
            PromotionStage.PAPER_PASSED,
            PromotionStage.SHADOW_PASSED,
        ),
        start=1,
    ):
        registry.advance_experiment(
            experiment_id=experiment_id,
            target=stage,
            actor=ActorKind.AUTOMATION,
            actor_id="research-orchestrator",
            evidence_sha256=f"{offset:x}" * 64,
            occurred_at=CREATED_AT + timedelta(seconds=offset),
        )


def advance_to_approval_boundary(registry: ResearchRegistry, experiment_id: str) -> None:
    report = promotion_report(experiment_id)
    registry.register_artifact(
        "report",
        f"{experiment_id}-promotion-report",
        report,
        created_at=CREATED_AT,
    )
    advance_to_shadow(registry, experiment_id)
    registry.advance_experiment(
        experiment_id=experiment_id,
        target=PromotionStage.AWAITING_APPROVAL,
        actor=ActorKind.AUTOMATION,
        actor_id="research-orchestrator",
        evidence_sha256=report.sha256(),
        occurred_at=CREATED_AT + timedelta(seconds=6),
    )


def test_registry_is_immutable_single_writer_and_owner_thread_only(tmp_path: Path) -> None:
    path = tmp_path / "research.duckdb"
    registry = ResearchRegistry(path)
    registry.register_artifact(
        "report",
        "promotion-policy",
        promotion_policy(),
        created_at=CREATED_AT,
    )
    registry.register_artifact(
        "report",
        "promotion-policy",
        promotion_policy(),
        created_at=CREATED_AT,
    )
    changed = promotion_policy().model_copy(update={"minimum_fill_count": 999})
    with pytest.raises(ValueError, match="different content"):
        registry.register_artifact(
            "report",
            "promotion-policy",
            changed,
            created_at=CREATED_AT,
        )

    registry.register_experiment(experiment())
    registry.register_experiment(experiment())
    with pytest.raises(ValueError, match="different content"):
        registry.register_experiment(
            experiment().model_copy(update={"parameters": {"risk_aversion": "changed"}})
        )
    with pytest.raises(ValueError, match="draft stage"):
        registry.register_experiment(experiment("invalid-entry", stage=PromotionStage.CANDIDATE))
    with pytest.raises(RuntimeError, match="already has a writer"):
        ResearchRegistry(path)

    errors: list[BaseException] = []

    def wrong_thread() -> None:
        try:
            registry.experiment_count()
        except BaseException as exc:  # pragma: no branch - assertion captures the exact failure
            errors.append(exc)

    worker = threading.Thread(target=wrong_thread)
    worker.start()
    worker.join()
    assert len(errors) == 1
    assert "owning process and thread" in str(errors[0])
    registry.close()
    with pytest.raises(RuntimeError, match="closed"):
        registry.experiment_count()


def test_registry_stage_history_is_monotonic_and_stops_at_human_boundary(tmp_path: Path) -> None:
    path = tmp_path / "research.duckdb"
    with ResearchRegistry(path) as registry:
        registry.register_experiment(experiment())
        first_event = registry.advance_experiment(
            experiment_id="experiment-001",
            target=PromotionStage.CANDIDATE,
            actor=ActorKind.AUTOMATION,
            actor_id="worker",
            evidence_sha256="c" * 64,
            occurred_at=CREATED_AT + timedelta(seconds=1),
        )
        assert len(first_event) == 64
        with pytest.raises(ValueError, match="monotonically"):
            registry.advance_experiment(
                experiment_id="experiment-001",
                target=PromotionStage.BACKTEST_PASSED,
                actor=ActorKind.AUTOMATION,
                actor_id="worker",
                evidence_sha256="d" * 64,
                occurred_at=CREATED_AT + timedelta(seconds=1),
            )

    with ResearchRegistry(path) as registry:
        assert registry.current_stage("experiment-001") is PromotionStage.CANDIDATE
        report = promotion_report("experiment-001")
        registry.register_artifact(
            "report",
            "experiment-001-promotion-report",
            report,
            created_at=CREATED_AT,
        )
        for offset, stage in enumerate(
            (
                PromotionStage.BACKTEST_PASSED,
                PromotionStage.WALK_FORWARD_PASSED,
                PromotionStage.PAPER_PASSED,
                PromotionStage.SHADOW_PASSED,
                PromotionStage.AWAITING_APPROVAL,
            ),
            start=2,
        ):
            registry.advance_experiment(
                experiment_id="experiment-001",
                target=stage,
                actor=ActorKind.AUTOMATION,
                actor_id="worker",
                evidence_sha256=(
                    report.sha256()
                    if stage is PromotionStage.AWAITING_APPROVAL
                    else f"{offset:x}" * 64
                ),
                occurred_at=CREATED_AT + timedelta(seconds=offset),
            )
        with pytest.raises(ValueError, match="human approver"):
            registry.advance_experiment(
                experiment_id="experiment-001",
                target=PromotionStage.APPROVED_CANARY,
                actor=ActorKind.AUTOMATION,
                actor_id="worker",
                evidence_sha256="e" * 64,
                occurred_at=CREATED_AT + timedelta(seconds=8),
            )
        with pytest.raises(ValueError, match="Phase 9 signed"):
            registry.advance_experiment(
                experiment_id="experiment-001",
                target=PromotionStage.APPROVED_CANARY,
                actor=ActorKind.HUMAN_APPROVER,
                actor_id="human-reviewer",
                evidence_sha256="f" * 64,
                occurred_at=CREATED_AT + timedelta(seconds=8),
            )
        assert registry.current_stage("experiment-001") is PromotionStage.AWAITING_APPROVAL


def test_approval_boundary_requires_the_registered_passing_bound_report(tmp_path: Path) -> None:
    path = tmp_path / "research.duckdb"
    passing = promotion_report("missing-report")
    failed_metrics = metrics().model_copy(update={"maximum_drawdown_usd": 999.0})
    failed = promotion_report("failed-report", challenger=failed_metrics)
    assert not failed.passed
    with ResearchRegistry(path) as registry:
        registry.register_experiment(experiment("missing-report", report=passing))
        advance_to_shadow(registry, "missing-report")
        with pytest.raises(ValueError, match="not registered"):
            registry.advance_experiment(
                experiment_id="missing-report",
                target=PromotionStage.AWAITING_APPROVAL,
                actor=ActorKind.AUTOMATION,
                actor_id="worker",
                evidence_sha256=passing.sha256(),
                occurred_at=CREATED_AT + timedelta(seconds=6),
            )

        registry.register_experiment(
            experiment("failed-report", report=failed, challenger_metrics=failed_metrics)
        )
        registry.register_artifact(
            "report",
            "failed-report-promotion-report",
            failed,
            created_at=CREATED_AT,
        )
        advance_to_shadow(registry, "failed-report")
        with pytest.raises(ValueError, match="failed promotion report"):
            registry.advance_experiment(
                experiment_id="failed-report",
                target=PromotionStage.AWAITING_APPROVAL,
                actor=ActorKind.AUTOMATION,
                actor_id="worker",
                evidence_sha256=failed.sha256(),
                occurred_at=CREATED_AT + timedelta(seconds=6),
            )


def test_research_cli_evaluates_gates_and_cannot_issue_human_approval(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "research.duckdb"
    assert main(["registry-init", "--path", str(database)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ready"
    experiment_path = tmp_path / "experiment.json"
    experiment_path.write_text(experiment().model_dump_json(), encoding="utf-8")
    assert (
        main(
            [
                "registry-register-experiment",
                "--path",
                str(database),
                "--manifest",
                str(experiment_path),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "registered"
    with ResearchRegistry(database) as registry:
        advance_to_approval_boundary(registry, "experiment-001")

    result = main(
        [
            "registry-advance",
            "--path",
            str(database),
            "--experiment-id",
            "experiment-001",
            "--target",
            PromotionStage.APPROVED_CANARY.value,
            "--actor",
            ActorKind.AUTOMATION.value,
            "--actor-id",
            "automation",
            "--evidence-sha256",
            "f" * 64,
            "--occurred-at",
            (CREATED_AT + timedelta(seconds=9)).isoformat(),
        ]
    )
    assert result == 2
    assert "human approver" in capsys.readouterr().err

    challenger_path = tmp_path / "challenger.json"
    policy_path = tmp_path / "policy.json"
    controls_path = tmp_path / "controls.json"
    challenger_path.write_text(metrics().model_dump_json(), encoding="utf-8")
    policy_path.write_text(promotion_policy().model_dump_json(), encoding="utf-8")
    controls_path.write_text(controls().model_dump_json(), encoding="utf-8")
    assert (
        main(
            [
                "evaluate",
                "--challenger-id",
                "experiment-001",
                "--challenger-metrics",
                str(challenger_path),
                "--policy",
                str(policy_path),
                "--negative-controls",
                str(controls_path),
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["passed"] is True
    assert report["maximum_automation_stage"] == "awaiting_approval"
