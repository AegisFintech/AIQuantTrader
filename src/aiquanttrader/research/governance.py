"""Frozen champion-challenger gates with an explicit automation ceiling."""

from __future__ import annotations

from aiquanttrader.research.models import (
    ChampionChallengerReport,
    GateResult,
    NegativeControlReport,
    PromotionMetrics,
    PromotionPolicy,
)


def evaluate_challenger(
    *,
    challenger_experiment_id: str,
    challenger: PromotionMetrics,
    champion_experiment_id: str | None,
    champion: PromotionMetrics | None,
    policy: PromotionPolicy,
    negative_controls: NegativeControlReport,
) -> ChampionChallengerReport:
    if (champion_experiment_id is None) != (champion is None):
        raise ValueError("champion identity and metrics must be supplied together")
    gates = [
        GateResult(
            gate="post_cost_pnl",
            passed=challenger.post_cost_pnl_usd >= policy.minimum_post_cost_pnl_usd,
            observed=challenger.post_cost_pnl_usd,
            threshold=policy.minimum_post_cost_pnl_usd,
        ),
        GateResult(
            gate="maximum_drawdown",
            passed=challenger.maximum_drawdown_usd <= policy.maximum_drawdown_usd,
            observed=challenger.maximum_drawdown_usd,
            threshold=policy.maximum_drawdown_usd,
        ),
        GateResult(
            gate="tail_loss_99",
            passed=challenger.tail_loss_99_usd <= policy.maximum_tail_loss_99_usd,
            observed=challenger.tail_loss_99_usd,
            threshold=policy.maximum_tail_loss_99_usd,
        ),
        GateResult(
            gate="maximum_inventory",
            passed=(challenger.maximum_abs_inventory_base <= policy.maximum_abs_inventory_base),
            observed=challenger.maximum_abs_inventory_base,
            threshold=policy.maximum_abs_inventory_base,
        ),
        GateResult(
            gate="fill_count",
            passed=challenger.fill_count >= policy.minimum_fill_count,
            observed=challenger.fill_count,
            threshold=policy.minimum_fill_count,
        ),
        GateResult(
            gate="maker_ratio",
            passed=challenger.maker_ratio >= policy.minimum_maker_ratio,
            observed=challenger.maker_ratio,
            threshold=policy.minimum_maker_ratio,
        ),
        GateResult(
            gate="adverse_selection",
            passed=(challenger.adverse_selection_bps <= policy.maximum_adverse_selection_bps),
            observed=challenger.adverse_selection_bps,
            threshold=policy.maximum_adverse_selection_bps,
        ),
        GateResult(
            gate="decision_latency_p99",
            passed=(challenger.decision_latency_p99_ms <= policy.maximum_decision_latency_p99_ms),
            observed=challenger.decision_latency_p99_ms,
            threshold=policy.maximum_decision_latency_p99_ms,
        ),
        GateResult(
            gate="fold_consistency",
            passed=challenger.fold_consistency >= policy.minimum_fold_consistency,
            observed=challenger.fold_consistency,
            threshold=policy.minimum_fold_consistency,
        ),
        GateResult(
            gate="drift",
            passed=challenger.drift_psi_max <= policy.maximum_drift_psi,
            observed=challenger.drift_psi_max,
            threshold=policy.maximum_drift_psi,
        ),
        GateResult(
            gate="operational_failures",
            passed=challenger.operational_failure_count == 0,
            observed=challenger.operational_failure_count,
            threshold=0,
        ),
    ]
    if champion is not None:
        improvement = challenger.post_cost_pnl_usd - champion.post_cost_pnl_usd
        gates.append(
            GateResult(
                gate="champion_improvement",
                passed=improvement >= policy.minimum_champion_improvement_usd,
                observed=improvement,
                threshold=policy.minimum_champion_improvement_usd,
            )
        )
    controls_passed = negative_controls.passed or not policy.require_negative_controls
    gates.append(
        GateResult(
            gate="negative_controls",
            passed=controls_passed,
            observed=negative_controls.passed,
            threshold=policy.require_negative_controls,
        )
    )
    return ChampionChallengerReport(
        challenger_experiment_id=challenger_experiment_id,
        champion_experiment_id=champion_experiment_id,
        policy_sha256=policy.sha256(),
        challenger_metrics_sha256=challenger.sha256(),
        champion_metrics_sha256=None if champion is None else champion.sha256(),
        negative_controls_sha256=negative_controls.sha256(),
        gates=tuple(gates),
        passed=all(gate.passed for gate in gates),
    )
