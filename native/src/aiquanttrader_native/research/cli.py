"""Reproducible operator CLI for Phase 6 research workflows."""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import numpy as np

from aiquanttrader_native.backtest.conversion import load_event_file
from aiquanttrader_native.backtest.kernel import hft_market_states
from aiquanttrader_native.backtest.models import BacktestDatasetManifest, ValidationPlan
from aiquanttrader_native.domain.governance import ActorKind, PromotionStage
from aiquanttrader_native.features.models import MODEL_FEATURE_SCHEMA, FeatureEngineConfig
from aiquanttrader_native.features.storage import write_feature_dataset
from aiquanttrader_native.market_data.io import atomic_write_bytes, sha256_file
from aiquanttrader_native.research.artifacts import load_model_artifact, save_model_artifact
from aiquanttrader_native.research.governance import evaluate_challenger
from aiquanttrader_native.research.model_adapters import adapter_for
from aiquanttrader_native.research.models import (
    CausalTrainingMatrix,
    ForecastTarget,
    ModelEngine,
    NegativeControlReport,
    NoSignalControlReport,
    PromotionMetrics,
    PromotionPolicy,
    ResearchExperimentManifest,
    SearchPolicy,
)
from aiquanttrader_native.research.registry import ResearchRegistry
from aiquanttrader_native.research.search import randomized_label_control, run_fold_retraining


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aqt-research")
    commands = parser.add_subparsers(dest="command", required=True)

    feature = commands.add_parser("feature-replay")
    feature.add_argument("--events", type=Path, required=True)
    feature.add_argument("--dataset-manifest", type=Path, required=True)
    feature.add_argument("--config", type=Path, required=True)
    feature.add_argument("--output-root", type=Path, required=True)
    feature.add_argument("--relative-path", required=True)

    search = commands.add_parser("run-search")
    search.add_argument("--matrix", type=Path, required=True)
    search.add_argument("--validation-plan", type=Path, required=True)
    search.add_argument("--fold", type=int, required=True)
    search.add_argument("--policy", type=Path, required=True)
    search.add_argument("--engine", choices=[item.value for item in ModelEngine], required=True)
    search.add_argument("--target", choices=[item.value for item in ForecastTarget], required=True)
    search.add_argument("--artifact-root", type=Path, required=True)
    search.add_argument("--artifact-path", required=True)
    search.add_argument("--dependency-lock", type=Path, required=True)
    search.add_argument("--created-at", required=True)
    search.add_argument("--randomized-label-minimum-mse", type=float, required=True)
    search.add_argument("--randomized-seed", type=int, default=0)
    search.add_argument("--no-signal-report", type=Path, required=True)
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


def _training_matrix(path: Path) -> CausalTrainingMatrix:
    with np.load(path, allow_pickle=False) as archive:
        expected = {
            "features",
            "labels",
            "sample_ts_ns",
            "label_end_ts_ns",
            "feature_schema_sha256",
            "source_dataset_sha256",
        }
        if set(archive.files) != expected:
            raise ValueError("training archive has missing or unexpected arrays")
        schema_hash = str(archive["feature_schema_sha256"].item())
        if schema_hash != MODEL_FEATURE_SCHEMA.sha256():
            raise ValueError("training archive feature schema mismatch")
        return CausalTrainingMatrix(
            features=np.asarray(archive["features"], dtype=np.float64),
            labels=np.asarray(archive["labels"], dtype=np.float64),
            sample_ts_ns=np.asarray(archive["sample_ts_ns"], dtype=np.int64),
            label_end_ts_ns=np.asarray(archive["label_end_ts_ns"], dtype=np.int64),
            feature_schema=MODEL_FEATURE_SCHEMA,
            source_dataset_sha256=str(archive["source_dataset_sha256"].item()),
        )


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
            states = hft_market_states(load_event_file(args.events))
            manifest_path, manifest = write_feature_dataset(
                states,
                config=_feature_config(args.config),
                source_dataset_sha256=dataset.dataset_id,
                output_root=args.output_root,
                relative_path=args.relative_path,
            )
            _write_output(
                None,
                {
                    "manifest": str(manifest_path),
                    "feature_dataset_id": manifest.feature_dataset_id,
                    "rows": manifest.row_count,
                },
            )
            return 0
        if args.command == "run-search":
            matrix = _training_matrix(args.matrix)
            plan = ValidationPlan.model_validate_json(args.validation_plan.read_bytes())
            if plan.dataset_sha256 != matrix.source_dataset_sha256:
                raise ValueError("training matrix source does not match validation plan")
            if not 0 <= args.fold < len(plan.folds):
                raise ValueError("fold index is outside validation plan")
            policy = SearchPolicy.model_validate_json(args.policy.read_bytes())
            engine = ModelEngine(args.engine)
            target = ForecastTarget(args.target)
            adapter = adapter_for(engine)
            result = run_fold_retraining(
                adapter=adapter,
                matrix=matrix,
                fold=plan.folds[args.fold],
                target=target,
                policy=policy,
            )
            selected_trial = next(
                trial
                for trial in policy.trials
                if trial.trial_id == result.search.receipt.selected_trial_id
            )
            no_signal = NoSignalControlReport.model_validate_json(
                args.no_signal_report.read_bytes()
            )
            controls = randomized_label_control(
                adapter=adapter,
                training=matrix.window(plan.folds[args.fold].train),
                validation=matrix.window(plan.folds[args.fold].validation),
                target=target,
                selected_parameters=selected_trial.parameters,
                minimum_mse=args.randomized_label_minimum_mse,
                no_signal_decision_count=no_signal.decision_count,
                no_signal_report_sha256=no_signal.sha256(),
                seed=args.randomized_seed,
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
                "search_receipt": result.search.receipt.model_dump(mode="json"),
                "walk_forward_test_mse": result.walk_forward_test_mse,
                "test_rows": result.test_rows,
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
            report = evaluate_challenger(
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
            print(report.model_dump_json(indent=2))
            return 0 if report.passed else 3
        raise RuntimeError(f"unhandled command: {args.command}")
    except (KeyError, OSError, RuntimeError, StopIteration, ValueError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
