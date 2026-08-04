"""Frozen shadow gates and exact replay comparison without promotion authority."""

from __future__ import annotations

import time
from decimal import Decimal

from pydantic_core import to_jsonable_python

from aiquanttrader_native.domain.base import canonical_sha256
from aiquanttrader_native.paper.journal import PaperJournal, PaperJournalStatistics
from aiquanttrader_native.paper.models import (
    PaperDecisionRecord,
    PaperExecutionCommand,
    PaperRunManifest,
)
from aiquanttrader_native.shadow.audit import ShadowAuditStatistics
from aiquanttrader_native.shadow.config import ShadowArtifacts
from aiquanttrader_native.shadow.models import (
    ShadowDeterminismReport,
    ShadowEvidenceReport,
    ShadowGateResult,
)


def compare_shadow_runs(
    source: PaperJournal,
    replay: PaperJournal,
    *,
    source_run_id: str,
    replay_run_id: str,
    generated_ts_ns: int | None = None,
) -> ShadowDeterminismReport:
    source_manifest = _manifest_for(source, source_run_id)
    replay_manifest = _manifest_for(replay, replay_run_id)
    if (
        source_manifest.code_identity != replay_manifest.code_identity
        or source_manifest.config_fingerprint != replay_manifest.config_fingerprint
        or source_manifest.feature_config_sha256 != replay_manifest.feature_config_sha256
        or source_manifest.strategy_config_sha256 != replay_manifest.strategy_config_sha256
        or source_manifest.scenario_sha256 != replay_manifest.scenario_sha256
        or source_manifest.evidence_policy_sha256 != replay_manifest.evidence_policy_sha256
        or source_manifest.source_start_sequence != replay_manifest.source_start_sequence
    ):
        raise ValueError("shadow determinism comparison requires identical immutable lineage")
    source_decisions = tuple(
        _decision_signature(value) for value in source.decisions(source_run_id)
    )
    replay_decisions = tuple(
        _decision_signature(value) for value in replay.decisions(replay_run_id)
    )
    source_commands = tuple(_command_signature(value) for value in source.commands(source_run_id))
    replay_commands = tuple(_command_signature(value) for value in replay.commands(replay_run_id))
    payload = {
        "schema_version": 1,
        "source_run_id": source_run_id,
        "replay_run_id": replay_run_id,
        "source_manifest_sha256": source_manifest.sha256(),
        "replay_manifest_sha256": replay_manifest.sha256(),
        "compared_decisions": min(len(source_decisions), len(replay_decisions)),
        "decision_mismatches": _mismatches(source_decisions, replay_decisions),
        "compared_commands": min(len(source_commands), len(replay_commands)),
        "command_mismatches": _mismatches(source_commands, replay_commands),
        "generated_ts_ns": time.time_ns() if generated_ts_ns is None else generated_ts_ns,
    }
    return ShadowDeterminismReport.model_validate(
        {"report_id": canonical_sha256(payload), **payload}
    )


def evaluate_shadow_evidence(
    *,
    manifest: PaperRunManifest,
    statistics: PaperJournalStatistics,
    audit: ShadowAuditStatistics,
    artifacts: ShadowArtifacts,
    determinism: ShadowDeterminismReport | None,
    sensitivity_reports: tuple[ShadowEvidenceReport, ...] = (),
    generated_ts_ns: int | None = None,
) -> ShadowEvidenceReport:
    policy = artifacts.evidence_policy
    scenario = artifacts.paper.scenario
    generated = time.time_ns() if generated_ts_ns is None else generated_ts_ns
    observation_ns = max(0, statistics.ended_ts_ns - statistics.started_ts_ns)
    total_decisions = statistics.approved_decisions + statistics.denied_decisions
    denial_fraction = (
        Decimal(statistics.denied_decisions) / Decimal(total_decisions)
        if total_decisions
        else Decimal("1")
    )
    post_cost_pnl = statistics.ending_equity_usd - statistics.starting_equity_usd
    required_sensitivity = set(policy.required_sensitivity_scenarios)
    valid_sensitivity = {
        report.scenario_id
        for report in sensitivity_reports
        if all(gate.passed for gate in report.gates if gate.gate != "sensitivity")
        and report.image_identity == manifest.image_identity
        and report.code_identity == manifest.code_identity
        and report.config_fingerprint == manifest.config_fingerprint
        and report.feature_config_sha256 == manifest.feature_config_sha256
        and report.strategy_config_sha256 == manifest.strategy_config_sha256
        and report.engine_policy_sha256 == artifacts.engine_policy_sha256
        and report.policy_sha256 == artifacts.evidence_policy_sha256
        and report.observation_ns == observation_ns
    }
    determinism_ok = (
        determinism is not None
        and determinism.source_run_id == manifest.run_id
        and determinism.source_manifest_sha256 == manifest.sha256()
        and determinism.compared_decisions >= policy.minimum_determinism_decisions
        and determinism.decision_mismatches == 0
        and determinism.command_mismatches == 0
    )
    gates = (
        _gate(
            "observation",
            observation_ns >= policy.minimum_observation_ns,
            observation_ns,
            policy.minimum_observation_ns,
        ),
        _gate(
            "independent_decisions",
            statistics.independent_decisions >= policy.minimum_independent_decisions,
            statistics.independent_decisions,
            policy.minimum_independent_decisions,
        ),
        _gate(
            "fills",
            statistics.fills >= policy.minimum_fills,
            statistics.fills,
            policy.minimum_fills,
        ),
        _gate(
            "regimes",
            len(statistics.regimes) >= policy.minimum_regimes,
            len(statistics.regimes),
            policy.minimum_regimes,
        ),
        _gate(
            "availability",
            audit.availability_fraction >= policy.minimum_availability_fraction,
            audit.availability_fraction,
            policy.minimum_availability_fraction,
        ),
        _gate(
            "ingress_latency_p99_ms",
            audit.ingress_latency_p99_ms <= policy.maximum_ingress_latency_p99_ms,
            audit.ingress_latency_p99_ms,
            policy.maximum_ingress_latency_p99_ms,
        ),
        _gate(
            "cycle_latency_p99_ms",
            audit.cycle_latency_p99_ms <= policy.maximum_cycle_latency_p99_ms,
            audit.cycle_latency_p99_ms,
            policy.maximum_cycle_latency_p99_ms,
        ),
        _gate(
            "operational_sample_completeness",
            audit.cycle_samples == statistics.feature_samples,
            audit.cycle_samples,
            statistics.feature_samples,
        ),
        _gate(
            "command_completeness",
            statistics.submit_commands == statistics.approved_decisions,
            statistics.submit_commands,
            statistics.approved_decisions,
        ),
        _gate(
            "calibrated_scenario",
            not policy.require_calibrated_scenario
            or scenario.calibration_state.value == "calibrated",
            scenario.calibration_state.value,
            "calibrated",
        ),
        _gate(
            "positive_post_cost_pnl",
            not policy.require_positive_post_cost_pnl or post_cost_pnl > 0,
            post_cost_pnl,
            "> 0",
        ),
        _gate(
            "drawdown",
            statistics.maximum_drawdown_fraction <= policy.maximum_drawdown_fraction,
            statistics.maximum_drawdown_fraction,
            policy.maximum_drawdown_fraction,
        ),
        _gate(
            "denial_fraction",
            denial_fraction <= policy.maximum_denial_fraction,
            denial_fraction,
            policy.maximum_denial_fraction,
        ),
        _gate(
            "markout_coverage",
            statistics.markouts == statistics.fills,
            statistics.markouts,
            statistics.fills,
        ),
        _gate(
            "adverse_markout",
            statistics.mean_signed_markout_bps >= -policy.maximum_adverse_markout_bps,
            statistics.mean_signed_markout_bps,
            f">= {-policy.maximum_adverse_markout_bps}",
        ),
        _gate("drift_evaluated", statistics.drift_evaluated, statistics.drift_evaluated, True),
        _gate(
            "feature_psi",
            statistics.maximum_feature_psi <= policy.maximum_feature_psi,
            statistics.maximum_feature_psi,
            policy.maximum_feature_psi,
        ),
        _gate(
            "feature_mean_shift",
            statistics.maximum_standardized_mean_shift <= policy.maximum_standardized_mean_shift,
            statistics.maximum_standardized_mean_shift,
            policy.maximum_standardized_mean_shift,
        ),
        _gate(
            "determinism",
            determinism_ok,
            "exact" if determinism_ok else "missing_or_mismatch",
            "exact replay",
        ),
        _gate(
            "sensitivity",
            valid_sensitivity == required_sensitivity,
            sorted(valid_sensitivity),
            sorted(required_sensitivity),
        ),
        _gate(
            "drills",
            set(audit.completed_drills) >= set(policy.required_drills),
            sorted(audit.completed_drills),
            sorted(policy.required_drills),
        ),
        _gate(
            "run_integrity",
            not statistics.invalidating_events and not audit.invalidating_events,
            sorted((*statistics.invalidating_events, *audit.invalidating_events)),
            [],
        ),
        _gate(
            "flat_final_state",
            statistics.ending_position_base == 0 and statistics.open_orders == 0,
            f"position={statistics.ending_position_base},orders={statistics.open_orders}",
            "position=0,orders=0",
        ),
    )
    payload = {
        "schema_version": 1,
        "run_id": manifest.run_id,
        "run_manifest_sha256": manifest.sha256(),
        "generated_ts_ns": generated,
        "policy_id": policy.policy_id,
        "policy_sha256": artifacts.evidence_policy_sha256,
        "image_identity": manifest.image_identity or "missing-image-identity",
        "code_identity": manifest.code_identity,
        "config_fingerprint": manifest.config_fingerprint,
        "feature_config_sha256": manifest.feature_config_sha256,
        "strategy_config_sha256": manifest.strategy_config_sha256,
        "engine_policy_sha256": artifacts.engine_policy_sha256,
        "scenario_id": scenario.scenario_id,
        "scenario_sha256": scenario.sha256(),
        "calibration_state": scenario.calibration_state,
        "observation_ns": observation_ns,
        "independent_decisions": statistics.independent_decisions,
        "approved_decisions": statistics.approved_decisions,
        "denied_decisions": statistics.denied_decisions,
        "commands": statistics.commands,
        "submit_commands": statistics.submit_commands,
        "fills": statistics.fills,
        "markouts": statistics.markouts,
        "regimes": statistics.regimes,
        "availability_fraction": audit.availability_fraction,
        "ingress_latency_p99_ms": audit.ingress_latency_p99_ms,
        "cycle_latency_p99_ms": audit.cycle_latency_p99_ms,
        "post_cost_pnl_usd": post_cost_pnl,
        "maximum_drawdown_fraction": statistics.maximum_drawdown_fraction,
        "mean_adverse_markout_bps": statistics.mean_signed_markout_bps,
        "maximum_feature_psi": statistics.maximum_feature_psi,
        "maximum_standardized_mean_shift": statistics.maximum_standardized_mean_shift,
        "determinism_report_id": None if determinism is None else determinism.report_id,
        "sensitivity_scenarios": tuple(sorted(valid_sensitivity)),
        "completed_drills": audit.completed_drills,
        "invalidating_events": tuple(
            sorted((*statistics.invalidating_events, *audit.invalidating_events))
        ),
        "gates": gates,
    }
    return ShadowEvidenceReport.model_validate(
        {
            "report_id": canonical_sha256(to_jsonable_python(payload)),
            "awaiting_human_approval": all(gate.passed for gate in gates),
            **payload,
        }
    )


def _manifest_for(journal: PaperJournal, run_id: str) -> PaperRunManifest:
    manifest = journal.latest_manifest()
    if manifest is None or manifest.run_id != run_id:
        raise ValueError(f"journal does not contain latest requested run {run_id}")
    return manifest


def _decision_signature(record: PaperDecisionRecord) -> str:
    return canonical_sha256(
        {
            "feature_snapshot_sha256": record.feature_snapshot_sha256,
            "strategy_id": record.strategy_id,
            "intent": record.intent.model_dump(mode="json"),
            "allowed": record.risk_decision.allowed,
            "reasons": [reason.value for reason in record.risk_decision.reasons],
            "independent": record.independent,
        }
    )


def _command_signature(command: PaperExecutionCommand) -> str:
    return canonical_sha256(
        {
            "sequence": command.sequence,
            "kind": command.kind.value,
            "intent_id": command.intent_id,
            "strategy_id": command.strategy_id,
            "intent": None if command.intent is None else command.intent.model_dump(mode="json"),
            "feature_snapshot_sha256": command.feature_snapshot_sha256,
            "source_sequence": command.source_sequence,
            "sink": command.sink,
        }
    )


def _mismatches(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    return abs(len(left) - len(right)) + sum(a != b for a, b in zip(left, right, strict=False))


def _gate(name: str, passed: bool, actual: object, required: object) -> ShadowGateResult:
    return ShadowGateResult(
        gate=name,
        passed=passed,
        actual=str(actual),
        required=str(required),
    )
