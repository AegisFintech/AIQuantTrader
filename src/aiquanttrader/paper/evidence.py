"""Frozen, sample-based paper evidence evaluation and sensitivity binding."""

from __future__ import annotations

import re
import time
from decimal import Decimal

from aiquanttrader.backtest.models import CalibrationState, ExecutionScenario
from aiquanttrader.domain.base import canonical_sha256
from aiquanttrader.paper.journal import PaperJournalStatistics
from aiquanttrader.paper.models import (
    PaperEvidencePolicy,
    PaperEvidenceReport,
    PaperGateResult,
    PaperRunManifest,
)


def evaluate_paper_evidence(
    *,
    manifest: PaperRunManifest,
    statistics: PaperJournalStatistics,
    scenario: ExecutionScenario,
    policy: PaperEvidencePolicy,
    required_scenarios: tuple[ExecutionScenario, ...],
    sensitivity_reports: tuple[PaperEvidenceReport, ...] = (),
    generated_ts_ns: int | None = None,
) -> PaperEvidenceReport:
    if manifest.scenario_sha256 != scenario.sha256():
        raise ValueError("paper run manifest does not match execution scenario")
    expected_scenarios = {item.scenario_id: item.sha256() for item in required_scenarios}
    if set(policy.required_sensitivity_scenarios) != set(expected_scenarios):
        raise ValueError("paper policy and loaded sensitivity scenarios do not match exactly")
    observation_ns = max(0, statistics.ended_ts_ns - statistics.started_ts_ns)
    decisions = statistics.approved_decisions + statistics.denied_decisions
    denial_fraction = (
        Decimal(statistics.denied_decisions) / decisions if decisions else Decimal("0")
    )
    post_cost_pnl = statistics.ending_equity_usd - statistics.starting_equity_usd
    mean_adverse_markout = max(Decimal("0"), -statistics.mean_signed_markout_bps)

    valid_sensitivity = tuple(
        report
        for report in sensitivity_reports
        if report.code_identity == manifest.code_identity
        and report.feature_config_sha256 == manifest.feature_config_sha256
        and report.strategy_config_sha256 == manifest.strategy_config_sha256
        and report.strategy_id == manifest.strategy_id
        and report.policy_sha256 == policy.sha256()
        and report.scenario_id != manifest.scenario_id
        and expected_scenarios.get(report.scenario_id) == report.scenario_sha256
        and report.observation_started_ts_ns == statistics.started_ts_ns
        and report.observation_ended_ts_ns == statistics.ended_ts_ns
        and all(gate.passed for gate in report.gates if gate.gate != "sensitivity_scenarios")
    )
    sensitivity_ids = tuple(sorted({report.scenario_id for report in valid_sensitivity}))
    completed_drills = tuple(sorted(statistics.completed_drills))
    gates = (
        _gate(
            "immutable_code_identity",
            _immutable_code_identity(manifest.code_identity),
            manifest.code_identity,
            "40-character Git SHA or sha256:<64 hex>",
        ),
        _gate(
            "calibrated_fill_model",
            scenario.calibration_state is CalibrationState.CALIBRATED,
            scenario.calibration_state.value,
            CalibrationState.CALIBRATED.value,
        ),
        _gate(
            "observation_window",
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
            "markout_coverage",
            statistics.markouts == statistics.fills,
            statistics.markouts,
            statistics.fills,
        ),
        _gate(
            "flat_final_state",
            statistics.ending_position_base == 0 and statistics.open_orders == 0,
            f"position={statistics.ending_position_base},open_orders={statistics.open_orders}",
            "position=0,open_orders=0",
        ),
        _gate(
            "regime_coverage",
            len(statistics.regimes) >= policy.minimum_regimes,
            len(statistics.regimes),
            policy.minimum_regimes,
        ),
        _gate(
            "post_cost_pnl",
            not policy.require_positive_post_cost_pnl or post_cost_pnl > 0,
            post_cost_pnl,
            "> 0" if policy.require_positive_post_cost_pnl else "not gated",
        ),
        _gate(
            "maximum_drawdown",
            statistics.maximum_drawdown_fraction <= policy.maximum_drawdown_fraction,
            statistics.maximum_drawdown_fraction,
            policy.maximum_drawdown_fraction,
        ),
        _gate(
            "risk_denial_fraction",
            denial_fraction <= policy.maximum_denial_fraction,
            denial_fraction,
            policy.maximum_denial_fraction,
        ),
        _gate(
            "adverse_markout",
            mean_adverse_markout <= policy.maximum_adverse_markout_bps,
            mean_adverse_markout,
            policy.maximum_adverse_markout_bps,
        ),
        _gate(
            "drift_evaluated",
            statistics.drift_evaluated,
            statistics.drift_evaluated,
            True,
        ),
        _gate(
            "feature_psi",
            statistics.drift_evaluated
            and statistics.maximum_feature_psi <= policy.maximum_feature_psi,
            statistics.maximum_feature_psi,
            policy.maximum_feature_psi,
        ),
        _gate(
            "feature_mean_shift",
            statistics.drift_evaluated
            and statistics.maximum_standardized_mean_shift
            <= policy.maximum_standardized_mean_shift,
            statistics.maximum_standardized_mean_shift,
            policy.maximum_standardized_mean_shift,
        ),
        _gate(
            "sensitivity_scenarios",
            set(policy.required_sensitivity_scenarios).issubset(sensitivity_ids),
            ",".join(sensitivity_ids) or "none",
            ",".join(policy.required_sensitivity_scenarios),
        ),
        _gate(
            "operational_drills",
            set(policy.required_drills).issubset(completed_drills),
            ",".join(completed_drills) or "none",
            ",".join(policy.required_drills),
        ),
        _gate(
            "run_integrity",
            not statistics.invalidating_events,
            ",".join(statistics.invalidating_events) or "clean",
            "clean",
        ),
    )
    generated = time.time_ns() if generated_ts_ns is None else generated_ts_ns
    identity = {
        "run_id": manifest.run_id,
        "run_manifest_sha256": manifest.sha256(),
        "generated_ts_ns": generated,
        "policy_sha256": policy.sha256(),
        "scenario_sha256": scenario.sha256(),
        "observation_started_ts_ns": statistics.started_ts_ns,
        "observation_ended_ts_ns": statistics.ended_ts_ns,
        "observation_ns": observation_ns,
        "independent_decisions": statistics.independent_decisions,
        "approved_decisions": statistics.approved_decisions,
        "denied_decisions": statistics.denied_decisions,
        "fills": statistics.fills,
        "markouts": statistics.markouts,
        "ending_position_base": str(statistics.ending_position_base),
        "open_orders": statistics.open_orders,
        "regimes": [regime.value for regime in statistics.regimes],
        "post_cost_pnl_usd": str(post_cost_pnl),
        "maximum_drawdown_fraction": str(statistics.maximum_drawdown_fraction),
        "mean_adverse_markout_bps": str(mean_adverse_markout),
        "drift_evaluated": statistics.drift_evaluated,
        "maximum_feature_psi": str(statistics.maximum_feature_psi),
        "maximum_standardized_mean_shift": str(statistics.maximum_standardized_mean_shift),
        "sensitivity_scenarios": list(sensitivity_ids),
        "completed_drills": list(completed_drills),
        "invalidating_events": list(statistics.invalidating_events),
        "gates": [gate.model_dump(mode="json") for gate in gates],
    }
    return PaperEvidenceReport(
        report_id=canonical_sha256(identity),
        run_id=manifest.run_id,
        run_manifest_sha256=manifest.sha256(),
        generated_ts_ns=generated,
        policy_id=policy.policy_id,
        policy_sha256=policy.sha256(),
        code_identity=manifest.code_identity,
        config_fingerprint=manifest.config_fingerprint,
        feature_config_sha256=manifest.feature_config_sha256,
        strategy_config_sha256=manifest.strategy_config_sha256,
        strategy_id=manifest.strategy_id,
        scenario_id=scenario.scenario_id,
        scenario_sha256=scenario.sha256(),
        calibration_state=scenario.calibration_state,
        observation_started_ts_ns=statistics.started_ts_ns,
        observation_ended_ts_ns=statistics.ended_ts_ns,
        observation_ns=observation_ns,
        independent_decisions=statistics.independent_decisions,
        approved_decisions=statistics.approved_decisions,
        denied_decisions=statistics.denied_decisions,
        fills=statistics.fills,
        markouts=statistics.markouts,
        ending_position_base=statistics.ending_position_base,
        open_orders=statistics.open_orders,
        regimes=statistics.regimes,
        post_cost_pnl_usd=post_cost_pnl,
        maximum_drawdown_fraction=statistics.maximum_drawdown_fraction,
        mean_adverse_markout_bps=mean_adverse_markout,
        drift_evaluated=statistics.drift_evaluated,
        maximum_feature_psi=statistics.maximum_feature_psi,
        maximum_standardized_mean_shift=statistics.maximum_standardized_mean_shift,
        sensitivity_scenarios=sensitivity_ids,
        completed_drills=completed_drills,
        invalidating_events=statistics.invalidating_events,
        gates=gates,
        promotion_eligible=all(gate.passed for gate in gates),
    )


def _gate(gate: str, passed: bool, actual: object, required: object) -> PaperGateResult:
    return PaperGateResult(
        gate=gate,
        passed=passed,
        actual=str(actual),
        required=str(required),
    )


def _immutable_code_identity(value: str) -> bool:
    return re.fullmatch(r"(?:[0-9a-f]{40}|sha256:[0-9a-f]{64})", value) is not None
