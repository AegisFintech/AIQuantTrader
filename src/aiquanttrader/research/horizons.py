"""Predeclared multi-horizon feasibility audits without model selection."""

from __future__ import annotations

from pathlib import Path

from aiquanttrader.backtest.models import ExecutionScenario, ValidationPolicy
from aiquanttrader.backtest.validation import plan_walk_forward
from aiquanttrader.features.models import FeatureDatasetManifest
from aiquanttrader.market_data.io import atomic_write_bytes, sha256_file
from aiquanttrader.research.feasibility import audit_target_feasibility
from aiquanttrader.research.matrix import build_forecast_matrix, load_forecast_matrix
from aiquanttrader.research.models import (
    HorizonFamilyFeasibilityReport,
    HorizonFamilyPolicy,
    HorizonFeasibilityCandidateReport,
    ResearchControlPolicy,
)


def _write_immutable(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise FileExistsError(f"immutable horizon-audit artifact differs: {path}")
        return
    atomic_write_bytes(path, content)


def validation_policy_for_horizon(template: ValidationPolicy, horizon_ns: int) -> ValidationPolicy:
    """Derive a fully validated policy while preserving every non-horizon duration."""

    values = template.model_dump(mode="python")
    values.update(
        {
            "policy_id": f"{template.policy_id}.h{horizon_ns}",
            "label_horizon_ns": horizon_ns,
            "purge_ns": max(template.purge_ns, horizon_ns),
        }
    )
    return ValidationPolicy.model_validate(values)


def audit_horizon_family(
    *,
    feature_path: Path,
    feature_manifest_path: Path,
    artifact_root: Path,
    policy: HorizonFamilyPolicy,
    validation_template: ValidationPolicy,
    control_policy: ResearchControlPolicy,
    scenario: ExecutionScenario,
) -> HorizonFamilyFeasibilityReport:
    """Seal and audit every declared horizon; never rank or select a candidate."""

    feature_manifest = FeatureDatasetManifest.model_validate_json(
        feature_manifest_path.read_bytes()
    )
    if sha256_file(feature_path) != feature_manifest.file_sha256:
        raise ValueError("feature dataset does not match its immutable manifest")

    derived = tuple(
        validation_policy_for_horizon(validation_template, horizon_ns)
        for horizon_ns in policy.horizons_ns
    )
    plans = tuple(
        plan_walk_forward(
            dataset_sha256=feature_manifest.source_dataset_sha256,
            start_ts_ns=feature_manifest.first_receive_ts_ns,
            end_ts_ns=feature_manifest.last_receive_ts_ns + 1,
            policy=item,
        )
        for item in derived
    )
    holdout_hashes = {plan.final_holdout.sha256() for plan in plans}
    if len(holdout_hashes) != 1:
        raise ValueError("derived horizon plans do not share one final holdout")

    candidates: list[HorizonFeasibilityCandidateReport] = []
    for horizon_ns, validation_policy, validation_plan in zip(
        policy.horizons_ns, derived, plans, strict=True
    ):
        candidate_root = artifact_root / f"horizon_ns={horizon_ns}"
        plan_path = candidate_root / "validation-plan.json"
        _write_immutable(plan_path, validation_plan.canonical_bytes() + b"\n")
        manifest_path, _ = build_forecast_matrix(
            feature_path=feature_path,
            feature_manifest_path=feature_manifest_path,
            output_root=candidate_root,
            relative_path="next-mid-return-development.npz",
            target=policy.target,
            horizon_ns=horizon_ns,
            sample_interval_ns=policy.sample_interval_ns,
            maximum_label_delay_ns=policy.maximum_label_delay_ns,
            validation_plan=validation_plan,
        )
        matrix_path = candidate_root / "next-mid-return-development.npz"
        matrix, matrix_manifest = load_forecast_matrix(matrix_path, manifest_path)
        feasibility = audit_target_feasibility(
            matrix=matrix,
            matrix_manifest=matrix_manifest,
            validation_plan=validation_plan,
            policy=control_policy,
            scenario=scenario,
        )
        feasibility_path = candidate_root / "target-feasibility.json"
        _write_immutable(feasibility_path, feasibility.canonical_bytes() + b"\n")
        candidates.append(
            HorizonFeasibilityCandidateReport(
                horizon_ns=horizon_ns,
                validation_policy=validation_policy,
                validation_plan=validation_plan,
                matrix_manifest=matrix_manifest,
                target_feasibility=feasibility,
            )
        )

    return HorizonFamilyFeasibilityReport(
        policy=policy,
        validation_template=validation_template,
        feature_manifest=feature_manifest,
        control_policy=control_policy,
        scenario=scenario,
        candidates=tuple(candidates),
    )
