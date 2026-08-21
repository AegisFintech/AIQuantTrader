"""Reproducible operator CLI for Phase 6 research workflows."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from aiquanttrader.backtest.conversion import load_event_file
from aiquanttrader.backtest.kernel import iter_hft_market_states
from aiquanttrader.backtest.models import BacktestDatasetManifest, ValidationPlan
from aiquanttrader.backtest.scenarios import load_scenario, load_validation_policy
from aiquanttrader.domain.governance import ActorKind, PromotionStage
from aiquanttrader.features.models import (
    MODEL_FEATURE_SCHEMA,
    FeatureDatasetManifest,
    FeatureEngineConfig,
)
from aiquanttrader.features.storage import write_feature_dataset
from aiquanttrader.market_data.io import atomic_write_bytes, sha256_file
from aiquanttrader.research.artifacts import load_model_artifact, save_model_artifact
from aiquanttrader.research.controls import NO_SIGNAL_CONTROL_ID, run_no_signal_control
from aiquanttrader.research.feasibility import (
    audit_target_feasibility,
    require_viable_target_feasibility,
)
from aiquanttrader.research.governance import evaluate_challenger
from aiquanttrader.research.horizons import audit_horizon_family
from aiquanttrader.research.matrix import (
    build_forecast_matrix,
    load_forecast_matrix,
    require_development_matrix_plan,
)
from aiquanttrader.research.model_adapters import adapter_for
from aiquanttrader.research.models import (
    ForecastTarget,
    HorizonFamilyPolicy,
    ModelEngine,
    NegativeControlReport,
    NoSignalControlReport,
    PromotionMetrics,
    PromotionPolicy,
    ResearchControlPolicy,
    ResearchExperimentManifest,
    SearchPolicy,
    TargetFeasibilityReport,
)
from aiquanttrader.research.registry import ResearchRegistry
from aiquanttrader.research.search import randomized_label_control, run_fold_retraining
from aiquanttrader.strategies.config import load_scalper_config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aqt-research")
    commands = parser.add_subparsers(dest="command", required=True)

    feature = commands.add_parser("feature-replay")
    feature.add_argument("--events", type=Path, required=True)
    feature.add_argument("--dataset-manifest", type=Path, required=True)
    feature.add_argument("--config", type=Path, required=True)
    feature.add_argument("--output-root", type=Path, required=True)
    feature.add_argument("--relative-path", required=True)

    matrix = commands.add_parser("build-matrix")
    matrix.add_argument("--features", type=Path, required=True)
    matrix.add_argument("--feature-manifest", type=Path, required=True)
    matrix.add_argument("--output-root", type=Path, required=True)
    matrix.add_argument("--relative-path", required=True)
    matrix.add_argument(
        "--target",
        choices=[ForecastTarget.NEXT_MID_RETURN_BPS.value],
        required=True,
    )
    matrix.add_argument("--horizon-ns", type=int, required=True)
    matrix.add_argument("--sample-interval-ns", type=int, required=True)
    matrix.add_argument("--maximum-label-delay-ns", type=int, required=True)
    matrix.add_argument("--validation-plan", type=Path, required=True)

    feasibility = commands.add_parser("audit-target-feasibility")
    feasibility.add_argument("--matrix", type=Path, required=True)
    feasibility.add_argument("--matrix-manifest", type=Path, required=True)
    feasibility.add_argument("--validation-plan", type=Path, required=True)
    feasibility.add_argument("--control-policy", type=Path, required=True)
    feasibility.add_argument("--scenario", type=Path, required=True)
    feasibility.add_argument("--output", type=Path, required=True)

    horizon_family = commands.add_parser("audit-horizon-family")
    horizon_family.add_argument("--features", type=Path, required=True)
    horizon_family.add_argument("--feature-manifest", type=Path, required=True)
    horizon_family.add_argument("--artifact-root", type=Path, required=True)
    horizon_family.add_argument("--policy", type=Path, required=True)
    horizon_family.add_argument("--validation-template", type=Path, required=True)
    horizon_family.add_argument("--control-policy", type=Path, required=True)
    horizon_family.add_argument("--scenario", type=Path, required=True)
    horizon_family.add_argument("--output", type=Path, required=True)

    no_signal = commands.add_parser("run-no-signal-control")
    no_signal.add_argument("--features", type=Path, required=True)
    no_signal.add_argument("--feature-manifest", type=Path, required=True)
    no_signal.add_argument("--strategy-config", type=Path, required=True)
    no_signal.add_argument("--scenario", type=Path, required=True)
    no_signal.add_argument("--output", type=Path, required=True)

    search = commands.add_parser("run-search")
    search.add_argument("--matrix", type=Path, required=True)
    search.add_argument("--matrix-manifest", type=Path, required=True)
    search.add_argument("--validation-plan", type=Path, required=True)
    search.add_argument("--fold", type=int, required=True)
    search.add_argument("--policy", type=Path, required=True)
    search.add_argument("--engine", choices=[item.value for item in ModelEngine], required=True)
    search.add_argument("--target", choices=[item.value for item in ForecastTarget], required=True)
    search.add_argument("--artifact-root", type=Path, required=True)
    search.add_argument("--artifact-path", required=True)
    search.add_argument("--dependency-lock", type=Path, required=True)
    search.add_argument("--created-at", required=True)
    search.add_argument("--control-policy", type=Path, required=True)
    search.add_argument("--target-feasibility-report", type=Path, required=True)
    search.add_argument("--no-signal-report", type=Path, required=True)
    search.add_argument("--no-signal-feature-manifest", type=Path, required=True)
    search.add_argument("--no-signal-strategy-config", type=Path, required=True)
    search.add_argument("--no-signal-scenario", type=Path, required=True)
    search.add_argument("--output", type=Path)

    validate = commands.add_parser("validate-model")
    validate.add_argument("--artifact-root", type=Path, required=True)
    validate.add_argument("--manifest", type=Path, required=True)
    validate.add_argument("--target", choices=[item.value for item in ForecastTarget])

    registry = commands.add_parser("registry-init")
    registry.add_argument("--path", type=Path, required=True)

    register = commands.add_parser("registry-register-experiment")
    register.add_argument("--path", type=Path, required=True)
    register.add_argument("--manifest", type=Path, required=True)

    advance = commands.add_parser("registry-advance")
    advance.add_argument("--path", type=Path, required=True)
    advance.add_argument("--experiment-id", required=True)
    advance.add_argument("--target", choices=[item.value for item in PromotionStage], required=True)
    advance.add_argument(
        "--actor",
        choices=[ActorKind.AUTOMATION.value, ActorKind.SAFETY_CONTROLLER.value],
        required=True,
    )
    advance.add_argument("--actor-id", required=True)
    advance.add_argument("--evidence-sha256", required=True)
    advance.add_argument("--occurred-at", required=True)

    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--challenger-id", required=True)
    evaluate.add_argument("--challenger-metrics", type=Path, required=True)
    evaluate.add_argument("--champion-id")
    evaluate.add_argument("--champion-metrics", type=Path)
    evaluate.add_argument("--policy", type=Path, required=True)
    evaluate.add_argument("--negative-controls", type=Path, required=True)
    return parser


def _feature_config(path: Path) -> FeatureEngineConfig:
    with path.open("rb") as handle:
        return FeatureEngineConfig.model_validate(tomllib.load(handle))


def _write_output(path: Path | None, payload: dict[str, object]) -> None:
    content = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    if path is None:
        print(content, end="")
    else:
        atomic_write_bytes(path, content.encode("utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "feature-replay":
            dataset = BacktestDatasetManifest.model_validate_json(
                args.dataset_manifest.read_bytes()
            )
            if sha256_file(args.events) != dataset.event_file_sha256:
                raise ValueError("event file does not match dataset manifest")
            manifest_path, feature_manifest = write_feature_dataset(
                iter_hft_market_states(load_event_file(args.events)),
                config=_feature_config(args.config),
                source_dataset_sha256=dataset.dataset_id,
                output_root=args.output_root,
                relative_path=args.relative_path,
            )
            _write_output(
                None,
                {
                    "manifest": str(manifest_path),
                    "feature_dataset_id": feature_manifest.feature_dataset_id,
                    "rows": feature_manifest.row_count,
                    "stale_trade_exclusions": (feature_manifest.stale_trade_exclusion_count),
                    "stale_book_exclusions": feature_manifest.stale_book_exclusion_count,
                },
            )
            return 0
        if args.command == "build-matrix":
            manifest_path, forecast_manifest = build_forecast_matrix(
                feature_path=args.features,
                feature_manifest_path=args.feature_manifest,
                output_root=args.output_root,
                relative_path=args.relative_path,
                target=ForecastTarget(args.target),
                horizon_ns=args.horizon_ns,
                sample_interval_ns=args.sample_interval_ns,
                maximum_label_delay_ns=args.maximum_label_delay_ns,
                validation_plan=ValidationPlan.model_validate_json(
                    args.validation_plan.read_bytes()
                ),
            )
            _write_output(
                None,
                {
                    "manifest": str(manifest_path),
                    "matrix_id": forecast_manifest.matrix_id,
                    "rows": forecast_manifest.row_count,
                    "dropped_label_gaps": forecast_manifest.dropped_label_gap_count,
                    "dropped_tail": forecast_manifest.dropped_tail_count,
                    "excluded_holdout_candidates": (
                        forecast_manifest.excluded_holdout_candidate_count
                    ),
                },
            )
            return 0
        if args.command == "audit-target-feasibility":
            matrix, matrix_manifest = load_forecast_matrix(args.matrix, args.matrix_manifest)
            report = audit_target_feasibility(
                matrix=matrix,
                matrix_manifest=matrix_manifest,
                validation_plan=ValidationPlan.model_validate_json(
                    args.validation_plan.read_bytes()
                ),
                policy=ResearchControlPolicy.model_validate_json(args.control_policy.read_bytes()),
                scenario=load_scenario(args.scenario),
            )
            atomic_write_bytes(args.output, report.canonical_bytes() + b"\n")
            _write_output(
                None,
                {
                    "report": str(args.output),
                    "report_sha256": report.sha256(),
                    "selection_role": report.selection_role,
                    "opportunity_sufficient": report.opportunity_sufficient,
                    "calibration_state": report.calibration_state.value,
                    "passed": report.passed,
                    "folds": [
                        {
                            "fold": fold.fold_index,
                            "observations": fold.slices[0].observation_count,
                            "positive_net_labels": (fold.slices[0].positive_net_label_count),
                            "maximum_non_overlapping_observations": (
                                fold.slices[0].maximum_non_overlapping_observation_count
                            ),
                            "maximum_non_overlapping_positive_net_labels": (
                                fold.slices[0].maximum_non_overlapping_positive_net_count
                            ),
                            "necessary_conditions_possible": (
                                fold.necessary_conditions_possible(report.policy.forecast_economic)
                            ),
                        }
                        for fold in report.folds
                    ],
                },
            )
            return 0 if report.passed else 3
        if args.command == "audit-horizon-family":
            horizon_report = audit_horizon_family(
                feature_path=args.features,
                feature_manifest_path=args.feature_manifest,
                artifact_root=args.artifact_root,
                policy=HorizonFamilyPolicy.model_validate_json(args.policy.read_bytes()),
                validation_template=load_validation_policy(args.validation_template),
                control_policy=ResearchControlPolicy.model_validate_json(
                    args.control_policy.read_bytes()
                ),
                scenario=load_scenario(args.scenario),
            )
            atomic_write_bytes(args.output, horizon_report.canonical_bytes() + b"\n")
            _write_output(
                None,
                {
                    "report": str(args.output),
                    "report_sha256": horizon_report.sha256(),
                    "selection_role": horizon_report.selection_role,
                    "final_holdout_included": horizon_report.final_holdout_included,
                    "model_training_performed": horizon_report.model_training_performed,
                    "opportunity_sufficient_horizons_ns": (
                        horizon_report.opportunity_sufficient_horizons_ns
                    ),
                    "passed_horizons_ns": horizon_report.passed_horizons_ns,
                    "horizons": [
                        {
                            "horizon_ns": candidate.horizon_ns,
                            "opportunity_sufficient": (
                                candidate.target_feasibility.opportunity_sufficient
                            ),
                            "passed": candidate.target_feasibility.passed,
                            "folds_possible": [
                                fold.necessary_conditions_possible(
                                    horizon_report.control_policy.forecast_economic
                                )
                                for fold in candidate.target_feasibility.folds
                            ],
                        }
                        for candidate in horizon_report.candidates
                    ],
                },
            )
            return 0 if horizon_report.passed_horizons_ns else 3
        if args.command == "run-no-signal-control":
            no_signal_report = run_no_signal_control(
                feature_path=args.features,
                feature_manifest_path=args.feature_manifest,
                strategy=load_scalper_config(args.strategy_config),
                scenario=load_scenario(args.scenario),
            )
            atomic_write_bytes(args.output, no_signal_report.canonical_bytes() + b"\n")
            _write_output(
                None,
                {
                    "report": str(args.output),
                    "report_sha256": no_signal_report.sha256(),
                    "observations": no_signal_report.observation_count,
                    "ready_observations": no_signal_report.ready_observation_count,
                    "decisions": no_signal_report.decision_count,
                    "passed": no_signal_report.decision_count == 0,
                },
            )
            return 0 if no_signal_report.decision_count == 0 else 3
        if args.command == "run-search":
            matrix, matrix_manifest = load_forecast_matrix(args.matrix, args.matrix_manifest)
            plan = ValidationPlan.model_validate_json(args.validation_plan.read_bytes())
            require_development_matrix_plan(matrix_manifest, plan)
            if not 0 <= args.fold < len(plan.folds):
                raise ValueError("fold index is outside validation plan")
            policy = SearchPolicy.model_validate_json(args.policy.read_bytes())
            control_policy = ResearchControlPolicy.model_validate_json(
                args.control_policy.read_bytes()
            )
            engine = ModelEngine(args.engine)
            target = ForecastTarget(args.target)
            if matrix_manifest.target is not target:
                raise ValueError("forecast matrix target does not match requested research target")
            no_signal = NoSignalControlReport.model_validate_json(
                args.no_signal_report.read_bytes()
            )
            no_signal_features = FeatureDatasetManifest.model_validate_json(
                args.no_signal_feature_manifest.read_bytes()
            )
            no_signal_strategy = load_scalper_config(args.no_signal_strategy_config)
            no_signal_scenario = load_scenario(args.no_signal_scenario)
            target_feasibility = TargetFeasibilityReport.model_validate_json(
                args.target_feasibility_report.read_bytes()
            )
            require_viable_target_feasibility(
                report=target_feasibility,
                matrix=matrix,
                matrix_manifest=matrix_manifest,
                validation_plan=plan,
                policy=control_policy,
                scenario=no_signal_scenario,
            )
            if no_signal.control_id != NO_SIGNAL_CONTROL_ID:
                raise ValueError("no-signal control implementation is not supported")
            if (
                no_signal_features.feature_dataset_id
                != matrix_manifest.source_feature_dataset_sha256
            ):
                raise ValueError(
                    "no-signal feature manifest does not match forecast matrix lineage"
                )
            if no_signal.feature_dataset_sha256 != no_signal_features.feature_dataset_id:
                raise ValueError("no-signal feature dataset does not match its report")
            if no_signal.feature_file_sha256 != no_signal_features.file_sha256:
                raise ValueError("no-signal feature file does not match its report")
            if no_signal.feature_schema_sha256 != matrix_manifest.feature_schema_sha256:
                raise ValueError("no-signal feature schema does not match forecast matrix")
            if no_signal.strategy_configuration_sha256 != no_signal_strategy.sha256():
                raise ValueError("no-signal strategy configuration does not match report")
            if no_signal.scenario_sha256 != no_signal_scenario.sha256():
                raise ValueError("no-signal execution scenario does not match report")
            adapter = adapter_for(engine)
            result = run_fold_retraining(
                adapter=adapter,
                matrix=matrix,
                fold=plan.folds[args.fold],
                target=target,
                policy=policy,
                control_policy=control_policy,
                scenario=no_signal_scenario,
            )
            selected_trial = next(
                trial
                for trial in policy.trials
                if trial.trial_id == result.search.receipt.selected_trial_id
            )
            controls = randomized_label_control(
                adapter=adapter,
                training=matrix.window(plan.folds[args.fold].train),
                validation=matrix.window(plan.folds[args.fold].validation),
                target=target,
                selected_parameters=selected_trial.parameters,
                search_receipt=result.search.receipt,
                policy=control_policy,
                fold_index=args.fold,
                no_signal_decision_count=no_signal.decision_count,
                no_signal_report_sha256=no_signal.sha256(),
                target_feasibility_report_sha256=target_feasibility.sha256(),
                target_feasibility_passed=target_feasibility.passed,
                forecast_robustness=result.forecast_robustness,
                forecast_economic=result.forecast_economic,
            )
            manifest_path, model_manifest = save_model_artifact(
                result.search.selected_model,
                artifact_root=args.artifact_root,
                relative_path=args.artifact_path,
                training_dataset_sha256=matrix.sha256(),
                training_window_sha256=plan.folds[args.fold].train.sha256(),
                dependency_lock_sha256=sha256_file(args.dependency_lock),
                created_at=datetime.fromisoformat(args.created_at),
            )
            payload: dict[str, object] = {
                "model_manifest": str(manifest_path),
                "model_id": model_manifest.model_id,
                "target_feasibility_report": str(args.target_feasibility_report),
                "target_feasibility_report_sha256": target_feasibility.sha256(),
                "target_feasibility_passed": target_feasibility.passed,
                "search_receipt": result.search.receipt.model_dump(mode="json"),
                "walk_forward_test_mse": result.walk_forward_test_mse,
                "zero_prediction_test_mse": result.zero_prediction_test_mse,
                "training_mean_test_mse": result.training_mean_test_mse,
                "test_rows": result.test_rows,
                "forecast_robustness": result.forecast_robustness.model_dump(mode="json"),
                "forecast_robustness_passed": result.forecast_robustness.passed,
                "forecast_economic": result.forecast_economic.model_dump(mode="json"),
                "forecast_economic_performance_passed": (
                    result.forecast_economic.performance_passed
                ),
                "forecast_economic_passed": result.forecast_economic.passed,
                "negative_controls": controls.model_dump(mode="json"),
                "negative_controls_passed": controls.passed,
            }
            _write_output(args.output, payload)
            return 0
        if args.command == "validate-model":
            model = load_model_artifact(
                artifact_root=args.artifact_root,
                manifest_path=args.manifest,
                feature_schema=MODEL_FEATURE_SCHEMA,
                expected_target=None if args.target is None else ForecastTarget(args.target),
            )
            _write_output(
                None,
                {
                    "status": "valid",
                    "engine": model.engine.value,
                    "target": model.target.value,
                    "feature_schema_sha256": model.feature_schema.sha256(),
                },
            )
            return 0
        if args.command == "registry-init":
            with ResearchRegistry(args.path) as registry:
                count = registry.experiment_count()
            _write_output(None, {"status": "ready", "experiments": count})
            return 0
        if args.command == "registry-register-experiment":
            experiment = ResearchExperimentManifest.model_validate_json(args.manifest.read_bytes())
            with ResearchRegistry(args.path) as registry:
                registry.register_experiment(experiment)
                count = registry.experiment_count()
            _write_output(
                None,
                {
                    "status": "registered",
                    "experiment_id": experiment.experiment_id,
                    "experiments": count,
                },
            )
            return 0
        if args.command == "registry-advance":
            with ResearchRegistry(args.path) as registry:
                event_id = registry.advance_experiment(
                    experiment_id=args.experiment_id,
                    target=PromotionStage(args.target),
                    actor=ActorKind(args.actor),
                    actor_id=args.actor_id,
                    evidence_sha256=args.evidence_sha256,
                    occurred_at=datetime.fromisoformat(args.occurred_at),
                )
            _write_output(None, {"status": "advanced", "event_id": event_id})
            return 0
        if args.command == "evaluate":
            if (args.champion_id is None) != (args.champion_metrics is None):
                raise ValueError("champion ID and metrics must be supplied together")
            promotion_report = evaluate_challenger(
                challenger_experiment_id=args.challenger_id,
                challenger=PromotionMetrics.model_validate_json(
                    args.challenger_metrics.read_bytes()
                ),
                champion_experiment_id=args.champion_id,
                champion=(
                    None
                    if args.champion_metrics is None
                    else PromotionMetrics.model_validate_json(args.champion_metrics.read_bytes())
                ),
                policy=PromotionPolicy.model_validate_json(args.policy.read_bytes()),
                negative_controls=NegativeControlReport.model_validate_json(
                    args.negative_controls.read_bytes()
                ),
            )
            print(promotion_report.model_dump_json(indent=2))
            return 0 if promotion_report.passed else 3
        raise RuntimeError(f"unhandled command: {args.command}")
    except (KeyError, OSError, RuntimeError, StopIteration, ValueError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
