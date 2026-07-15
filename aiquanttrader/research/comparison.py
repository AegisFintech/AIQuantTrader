"""Challenger-versus-incumbent promotion comparison helpers."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from aiquanttrader.research.experiments import ExperimentRecord

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - Python 3.10 compatibility.

    class StrEnum(str, Enum):
        """Minimal Python 3.10-compatible StrEnum fallback."""

        def __str__(self) -> str:
            return str(self.value)


_EPSILON = 1e-9
_LOWER_IS_BETTER = {
    "mean_max_drawdown_pct",
    "fold_pnl_std",
}


class PromotionDecision(StrEnum):
    """Machine-readable promotion decision values."""

    ACCEPT = "accept"
    HOLD = "hold"
    REJECT = "reject"


@dataclass(frozen=True)
class SideBySideMetric:
    """One comparable metric across incumbent and challenger experiments."""

    metric: str
    incumbent: float
    challenger: float
    delta: float
    winner: str


@dataclass(frozen=True)
class StabilityComparison:
    """Side-by-side walk-forward stability summary."""

    incumbent_interpretation: str
    challenger_interpretation: str
    incumbent_consistency: float
    challenger_consistency: float
    notes: str


@dataclass(frozen=True)
class PromotionVerdict:
    """Promotion decision and rule-by-rule rationale."""

    decision: PromotionDecision
    rationale: list[str]
    headline: str


@dataclass(frozen=True)
class PromotionReport:
    """Complete promotion report payload."""

    report_id: str
    created_at: str
    symbol: str
    incumbent: ExperimentRecord
    challenger: ExperimentRecord
    side_by_side: list[SideBySideMetric]
    stability: StabilityComparison
    verdict: PromotionVerdict


def compare(
    experiment_incumbent: ExperimentRecord,
    experiment_challenger: ExperimentRecord,
    *,
    report_id: str = "",
) -> PromotionReport:
    """Compare incumbent and challenger walk-forward records."""

    resolved_report_id = report_id or _default_report_id()
    created_at = datetime.now(timezone.utc).isoformat()
    side_by_side = _side_by_side_metrics(experiment_incumbent, experiment_challenger)
    stability = _stability_comparison(experiment_incumbent, experiment_challenger)
    verdict = _promotion_verdict(
        incumbent=experiment_incumbent,
        challenger=experiment_challenger,
        side_by_side=side_by_side,
    )
    return PromotionReport(
        report_id=resolved_report_id,
        created_at=created_at,
        symbol=experiment_challenger.symbol or experiment_incumbent.symbol,
        incumbent=experiment_incumbent,
        challenger=experiment_challenger,
        side_by_side=side_by_side,
        stability=stability,
        verdict=verdict,
    )


def render_markdown(report: PromotionReport, *, notes: str = "") -> str:
    """Render a single-page markdown promotion report."""

    verdict = report.verdict.decision.value
    lines = [
        f"verdict: {verdict}",
        "",
        f"# Promotion Report: {_cell(report.report_id)}",
        "",
        "| Field | Value |",
        "|---|---|",
    ]
    header_rows = [
        ("report_id", report.report_id),
        ("created_at", report.created_at),
        ("symbol", report.symbol),
        (
            "incumbent",
            f"{report.incumbent.strategy_name} ({report.incumbent.run_id})",
        ),
        (
            "challenger",
            f"{report.challenger.strategy_name} ({report.challenger.run_id})",
        ),
        ("incumbent window", _record_window(report.incumbent)),
        ("challenger window", _record_window(report.challenger)),
    ]
    lines.extend(f"| {_cell(key)} | {_cell(value)} |" for key, value in header_rows)
    lines.extend(
        [
            "",
            "## Verdict",
            "",
            f"**Status:** `{verdict}`",
            "",
            report.verdict.headline,
            "",
        ]
    )
    lines.extend(f"- {item}" for item in report.verdict.rationale)
    lines.extend(
        [
            "",
            "## Side-by-Side Metrics",
            "",
            "| Metric | Incumbent | Challenger | Delta | Winner |",
            "|---|---:|---:|---:|---|",
        ]
    )
    if report.side_by_side:
        lines.extend(
            "| "
            + " | ".join(
                [
                    _cell(metric.metric),
                    _format_float(metric.incumbent),
                    _format_float(metric.challenger),
                    _format_float(metric.delta),
                    _cell(metric.winner),
                ]
            )
            + " |"
            for metric in report.side_by_side
        )
    else:
        lines.append("| No walk-forward metrics available |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Stability Comparison",
            "",
            "| Field | Incumbent | Challenger |",
            "|---|---:|---:|",
            (
                f"| interpretation | "
                f"{_cell(report.stability.incumbent_interpretation)} | "
                f"{_cell(report.stability.challenger_interpretation)} |"
            ),
            (
                f"| consistency_score | "
                f"{_format_float(report.stability.incumbent_consistency)} | "
                f"{_format_float(report.stability.challenger_consistency)} |"
            ),
            "",
            f"Notes: {_cell(report.stability.notes)}",
        ]
    )
    if notes:
        lines.extend(["", "## Notes", "", notes])
    lines.extend(
        [
            "",
            "---",
            (
                "Generated by M4 promotion comparison. Index at "
                f"state/research/reports/{report.report_id}.md."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def _side_by_side_metrics(
    incumbent: ExperimentRecord,
    challenger: ExperimentRecord,
) -> list[SideBySideMetric]:
    incumbent_metrics = _payload(incumbent.aggregated_metrics)
    challenger_metrics = _payload(challenger.aggregated_metrics)
    rows: list[SideBySideMetric] = []
    for metric_name in sorted(set(incumbent_metrics) | set(challenger_metrics)):
        incumbent_mean = _metric_stat(incumbent_metrics, metric_name, "mean")
        challenger_mean = _metric_stat(challenger_metrics, metric_name, "mean")
        if incumbent_mean is None or challenger_mean is None:
            continue
        comparable_name = f"mean_{metric_name}"
        rows.append(
            _side_by_side_metric(
                comparable_name,
                incumbent=incumbent_mean,
                challenger=challenger_mean,
            )
        )

    incumbent_stability = _payload(incumbent.walk_forward_stability)
    challenger_stability = _payload(challenger.walk_forward_stability)
    for metric_name in (
        "consistency_score",
        "worst_fold_pnl",
        "best_fold_pnl",
        "fold_pnl_std",
    ):
        incumbent_value = _float_or_none(incumbent_stability.get(metric_name))
        challenger_value = _float_or_none(challenger_stability.get(metric_name))
        if incumbent_value is None or challenger_value is None:
            continue
        rows.append(
            _side_by_side_metric(
                metric_name,
                incumbent=incumbent_value,
                challenger=challenger_value,
            )
        )
    return rows


def _side_by_side_metric(
    metric_name: str,
    *,
    incumbent: float,
    challenger: float,
) -> SideBySideMetric:
    delta = challenger - incumbent
    if metric_name in _LOWER_IS_BETTER:
        if challenger < incumbent - _EPSILON:
            winner = "challenger"
        elif incumbent < challenger - _EPSILON:
            winner = "incumbent"
        else:
            winner = "tie"
    elif challenger > incumbent + _EPSILON:
        winner = "challenger"
    elif incumbent > challenger + _EPSILON:
        winner = "incumbent"
    else:
        winner = "tie"
    return SideBySideMetric(
        metric=metric_name,
        incumbent=incumbent,
        challenger=challenger,
        delta=delta,
        winner=winner,
    )


def _stability_comparison(
    incumbent: ExperimentRecord,
    challenger: ExperimentRecord,
) -> StabilityComparison:
    incumbent_stability = _payload(incumbent.walk_forward_stability)
    challenger_stability = _payload(challenger.walk_forward_stability)
    incumbent_interpretation = str(incumbent_stability.get("interpretation") or "unknown")
    challenger_interpretation = str(challenger_stability.get("interpretation") or "unknown")
    incumbent_consistency = _float_or_none(
        incumbent_stability.get("consistency_score")
    ) or 0.0
    challenger_consistency = _float_or_none(
        challenger_stability.get("consistency_score")
    ) or 0.0

    notes = [
        f"incumbent interpretation: {incumbent_interpretation}",
        f"challenger interpretation: {challenger_interpretation}",
    ]
    if challenger_consistency > incumbent_consistency + _EPSILON:
        notes.append("challenger has higher consistency")
    elif incumbent_consistency > challenger_consistency + _EPSILON:
        notes.append("incumbent has higher consistency")
    else:
        notes.append("consistency is tied")

    incumbent_std = _float_or_none(incumbent_stability.get("fold_pnl_std"))
    challenger_std = _float_or_none(challenger_stability.get("fold_pnl_std"))
    if incumbent_std is not None and challenger_std is not None:
        if challenger_std < incumbent_std - _EPSILON:
            notes.append("challenger has lower variance")
        elif incumbent_std < challenger_std - _EPSILON:
            notes.append("incumbent has lower variance")
        else:
            notes.append("fold PnL variance is tied")
    elif not incumbent_stability or not challenger_stability:
        notes.append("walk-forward stability data unavailable for one or both records")

    return StabilityComparison(
        incumbent_interpretation=incumbent_interpretation,
        challenger_interpretation=challenger_interpretation,
        incumbent_consistency=incumbent_consistency,
        challenger_consistency=challenger_consistency,
        notes="; ".join(notes),
    )


def _promotion_verdict(
    *,
    incumbent: ExperimentRecord,
    challenger: ExperimentRecord,
    side_by_side: list[SideBySideMetric],
) -> PromotionVerdict:
    if not _has_walkforward_data(incumbent) or not _has_walkforward_data(challenger):
        return PromotionVerdict(
            decision=PromotionDecision.HOLD,
            headline="No walk-forward data available; human review required",
            rationale=[
                (
                    "HOLD no walk-forward data: one or both experiments are missing "
                    "M3 aggregated_metrics, so M4 cannot make a promotion decision."
                ),
                _fallback_verdict_note("incumbent", incumbent),
                _fallback_verdict_note("challenger", challenger),
            ],
        )

    incumbent_mean_pnl = _metric_value(incumbent, "total_pnl")
    challenger_mean_pnl = _metric_value(challenger, "total_pnl")
    incumbent_profit_factor = _metric_value(incumbent, "profit_factor")
    challenger_profit_factor = _metric_value(challenger, "profit_factor")
    incumbent_drawdown = _metric_value(incumbent, "max_drawdown_pct")
    challenger_drawdown = _metric_value(challenger, "max_drawdown_pct")
    incumbent_worst_fold = _stability_value(incumbent, "worst_fold_pnl")
    challenger_worst_fold = _stability_value(challenger, "worst_fold_pnl")
    incumbent_consistency = _stability_value(incumbent, "consistency_score")
    challenger_consistency = _stability_value(challenger, "consistency_score")
    incumbent_winners = sum(1 for row in side_by_side if row.winner == "incumbent")
    non_incumbent_winners = sum(
        1 for row in side_by_side if row.winner in {"challenger", "tie"}
    )
    metric_count = len(side_by_side)
    incumbent_win_rate = incumbent_winners / metric_count if metric_count else 0.0
    non_incumbent_rate = non_incumbent_winners / metric_count if metric_count else 0.0
    pnl_margin = max(abs(incumbent_mean_pnl or 0.0) * 0.05, 100.0)

    reject_rules = [
        (
            "reject: challenger mean_total_pnl < 0 while incumbent mean_total_pnl >= 0",
            _both_numbers(challenger_mean_pnl, incumbent_mean_pnl)
            and challenger_mean_pnl < 0
            and incumbent_mean_pnl >= 0,
            (
                f"challenger={_format_float(challenger_mean_pnl)}, "
                f"incumbent={_format_float(incumbent_mean_pnl)}"
            ),
        ),
        (
            "reject: challenger worst_fold_pnl < -2 * abs(incumbent worst_fold_pnl)",
            _both_numbers(challenger_worst_fold, incumbent_worst_fold)
            and challenger_worst_fold < -2.0 * abs(incumbent_worst_fold),
            (
                f"challenger={_format_float(challenger_worst_fold)}, "
                f"incumbent={_format_float(incumbent_worst_fold)}"
            ),
        ),
        (
            "reject: challenger consistency_score < 0.4 while incumbent consistency_score >= 0.6",
            _both_numbers(challenger_consistency, incumbent_consistency)
            and challenger_consistency < 0.4
            and incumbent_consistency >= 0.6,
            (
                f"challenger={_format_float(challenger_consistency)}, "
                f"incumbent={_format_float(incumbent_consistency)}"
            ),
        ),
        (
            "reject: more than 60% of side-by-side metrics favor incumbent",
            metric_count > 0 and incumbent_win_rate > 0.60,
            f"{incumbent_winners}/{metric_count} incumbent wins",
        ),
    ]
    accept_rules = [
        (
            "accept: challenger mean_total_pnl beats incumbent by promotion margin",
            _both_numbers(challenger_mean_pnl, incumbent_mean_pnl)
            and challenger_mean_pnl > incumbent_mean_pnl + pnl_margin,
            (
                f"challenger={_format_float(challenger_mean_pnl)}, "
                f"incumbent={_format_float(incumbent_mean_pnl)}, "
                f"margin={_format_float(pnl_margin)}"
            ),
        ),
        (
            "accept: challenger mean_profit_factor >= incumbent mean_profit_factor",
            _both_numbers(challenger_profit_factor, incumbent_profit_factor)
            and challenger_profit_factor >= incumbent_profit_factor,
            (
                f"challenger={_format_float(challenger_profit_factor)}, "
                f"incumbent={_format_float(incumbent_profit_factor)}"
            ),
        ),
        (
            "accept: challenger mean_max_drawdown_pct <= incumbent mean_max_drawdown_pct + 0.02",
            _both_numbers(challenger_drawdown, incumbent_drawdown)
            and challenger_drawdown <= incumbent_drawdown + 0.02,
            (
                f"challenger={_format_float(challenger_drawdown)}, "
                f"incumbent={_format_float(incumbent_drawdown)}"
            ),
        ),
        (
            "accept: challenger consistency_score >= incumbent consistency_score - 0.1",
            _both_numbers(challenger_consistency, incumbent_consistency)
            and challenger_consistency >= incumbent_consistency - 0.1,
            (
                f"challenger={_format_float(challenger_consistency)}, "
                f"incumbent={_format_float(incumbent_consistency)}"
            ),
        ),
        (
            "accept: at least 70% of side-by-side metrics favor challenger or tie",
            metric_count > 0 and non_incumbent_rate >= 0.70,
            f"{non_incumbent_winners}/{metric_count} challenger-or-tie metrics",
        ),
    ]

    rationale = [
        f"{'FAIL' if failed else 'PASS'} {rule} ({detail})"
        for rule, failed, detail in reject_rules
    ]
    rationale.extend(
        f"{'PASS' if passed else 'FAIL'} {rule} ({detail})"
        for rule, passed, detail in accept_rules
    )

    if any(failed for _, failed, _ in reject_rules):
        return PromotionVerdict(
            decision=PromotionDecision.REJECT,
            headline="Challenger fails one or more hard rejection gates",
            rationale=rationale,
        )
    if all(passed for _, passed, _ in accept_rules):
        return PromotionVerdict(
            decision=PromotionDecision.ACCEPT,
            headline="Challenger clears the promotion gates against incumbent",
            rationale=rationale,
        )
    return PromotionVerdict(
        decision=PromotionDecision.HOLD,
        headline="Challenger does not clearly beat the incumbent",
        rationale=rationale,
    )


def _has_walkforward_data(record: ExperimentRecord) -> bool:
    metrics = _payload(record.aggregated_metrics)
    return bool(metrics) and _metric_stat(metrics, "total_pnl", "mean") is not None


def _fallback_verdict_note(role: str, record: ExperimentRecord) -> str:
    status = ""
    verdict = record.verdict
    if isinstance(verdict, dict):
        status = str(verdict.get("status") or "")
    return f"{role} fallback verdict status: {status or 'unavailable'}"


def _metric_value(record: ExperimentRecord, metric_name: str) -> float | None:
    return _metric_stat(_payload(record.aggregated_metrics), metric_name, "mean")


def _metric_stat(
    metrics: dict[str, Any],
    metric_name: str,
    stat_name: str,
) -> float | None:
    metric = _payload(metrics.get(metric_name))
    if not metric and metric_name in metrics:
        return _float_or_none(metrics.get(metric_name))
    return _float_or_none(metric.get(stat_name))


def _stability_value(record: ExperimentRecord, metric_name: str) -> float | None:
    return _float_or_none(_payload(record.walk_forward_stability).get(metric_name))


def _payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, dict):
        return value
    if hasattr(value, "__dict__") and value.__class__.__module__.startswith("aiquanttrader."):
        return dict(vars(value))
    return {}


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number):
        return None
    return number


def _both_numbers(left: float | None, right: float | None) -> bool:
    return left is not None and right is not None


def _record_window(record: ExperimentRecord) -> str:
    starts: list[int] = []
    ends: list[int] = []
    for fold in record.fold_results:
        if not isinstance(fold, dict):
            continue
        start = _int_or_none(fold.get("test_start"))
        end = _int_or_none(fold.get("test_end"))
        if start is not None:
            starts.append(start)
        if end is not None:
            ends.append(end)
    if not starts or not ends:
        return "unknown"
    return f"{_epoch_to_iso(min(starts))} to {_epoch_to_iso(max(ends))}"


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _epoch_to_iso(value: int) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def _default_report_id() -> str:
    return datetime.now(timezone.utc).strftime("promotion-%Y%m%dT%H%M%SZ")


def _format_float(value: float | None) -> str:
    if value is None:
        return "n/a"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    if abs(value) >= 1000:
        return f"{value:.2f}"
    return f"{value:.6g}"


def _cell(value: Any) -> str:
    text = str(value)
    return text.replace("|", "\\|").replace("\n", "<br>")
