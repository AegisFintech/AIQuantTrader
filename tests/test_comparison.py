from __future__ import annotations

from aiquanttrader.research.comparison import PromotionDecision, compare, render_markdown
from aiquanttrader.research.experiments import ExperimentRecord


def test_compare_accept_when_challenger_dominates():
    incumbent = _record(
        "inc",
        total_pnl=50,
        profit_factor=1.2,
        max_drawdown_pct=0.10,
        consistency_score=0.6,
        worst_fold_pnl=-20,
    )
    challenger = _record(
        "chal",
        strategy_name="XauQuickMomentum",
        total_pnl=200,
        profit_factor=2.0,
        win_rate=0.70,
        max_drawdown_pct=0.08,
        sharpe_ratio=1.4,
        n_trades=18,
        expectancy=11.0,
        consistency_score=0.8,
        worst_fold_pnl=10,
        best_fold_pnl=250,
        fold_pnl_std=5,
    )

    report = compare(incumbent, challenger, report_id="accept")

    assert report.verdict.decision == PromotionDecision.ACCEPT


def test_compare_reject_when_challenger_loses_and_incumbent_wins():
    incumbent = _record("inc", total_pnl=50, worst_fold_pnl=-10)
    challenger = _record("chal", total_pnl=-50, worst_fold_pnl=-20)

    report = compare(incumbent, challenger)

    assert report.verdict.decision == PromotionDecision.REJECT


def test_compare_reject_catastrophic_fold():
    incumbent = _record("inc", total_pnl=50, worst_fold_pnl=-100)
    challenger = _record(
        "chal",
        total_pnl=200,
        profit_factor=2.0,
        max_drawdown_pct=0.08,
        consistency_score=0.8,
        worst_fold_pnl=-1000,
    )

    report = compare(incumbent, challenger)

    assert report.verdict.decision == PromotionDecision.REJECT


def test_compare_reject_consistency_drop():
    incumbent = _record("inc", consistency_score=0.8, worst_fold_pnl=-100)
    challenger = _record(
        "chal",
        total_pnl=200,
        profit_factor=2.0,
        max_drawdown_pct=0.08,
        consistency_score=0.2,
        worst_fold_pnl=-100,
    )

    report = compare(incumbent, challenger)

    assert report.verdict.decision == PromotionDecision.REJECT


def test_compare_hold_when_marginal():
    incumbent = _record(
        "inc",
        total_pnl=0,
        profit_factor=1.0,
        max_drawdown_pct=0.10,
        consistency_score=0.5,
        worst_fold_pnl=-10,
    )
    challenger = _record(
        "chal",
        total_pnl=5,
        profit_factor=1.0,
        max_drawdown_pct=0.10,
        consistency_score=0.5,
        worst_fold_pnl=-10,
    )

    report = compare(incumbent, challenger)

    assert report.verdict.decision == PromotionDecision.HOLD


def test_compare_accept_requires_dominance():
    incumbent = _record(
        "inc",
        total_pnl=100,
        profit_factor=1.2,
        win_rate=0.55,
        max_drawdown_pct=0.10,
        sharpe_ratio=1.0,
        n_trades=20,
        expectancy=5.0,
        consistency_score=0.8,
        worst_fold_pnl=-25,
        best_fold_pnl=180,
        fold_pnl_std=15,
    )
    challenger = _record(
        "chal",
        total_pnl=250,
        profit_factor=1.2,
        win_rate=0.50,
        max_drawdown_pct=0.11,
        sharpe_ratio=0.9,
        n_trades=18,
        expectancy=4.0,
        consistency_score=0.8,
        worst_fold_pnl=-25,
        best_fold_pnl=180,
        fold_pnl_std=15,
    )

    report = compare(incumbent, challenger)

    assert report.verdict.decision == PromotionDecision.HOLD
    assert "5/11 incumbent wins" in " ".join(report.verdict.rationale)


def test_compare_side_by_side_winner_assignment():
    incumbent = _record(
        "inc",
        total_pnl=100,
        profit_factor=2.0,
        max_drawdown_pct=0.05,
        consistency_score=0.7,
        worst_fold_pnl=-20,
    )
    challenger = _record(
        "chal",
        total_pnl=150,
        profit_factor=1.5,
        max_drawdown_pct=0.04,
        consistency_score=0.7,
        worst_fold_pnl=-30,
    )

    report = compare(incumbent, challenger)
    winners = {row.metric: row.winner for row in report.side_by_side}

    assert winners["mean_total_pnl"] == "challenger"
    assert winners["mean_profit_factor"] == "incumbent"
    assert winners["mean_max_drawdown_pct"] == "challenger"
    assert winners["consistency_score"] == "tie"
    assert winners["worst_fold_pnl"] == "incumbent"


def test_compare_stability_notes():
    incumbent = _record(
        "inc",
        interpretation="3/5 positive, high variance",
        consistency_score=0.6,
        fold_pnl_std=20,
    )
    challenger = _record(
        "chal",
        interpretation="4/5 positive, low variance",
        consistency_score=0.8,
        fold_pnl_std=10,
    )

    report = compare(incumbent, challenger)

    assert "incumbent interpretation: 3/5 positive, high variance" in report.stability.notes
    assert "challenger interpretation: 4/5 positive, low variance" in report.stability.notes
    assert "challenger has higher consistency" in report.stability.notes
    assert "challenger has lower variance" in report.stability.notes


def test_compare_verdict_first_line():
    report = compare(_record("inc"), _record("chal"), report_id="first-line")

    first_line = next(
        line for line in render_markdown(report).splitlines() if line.strip()
    )

    assert first_line in {"verdict: accept", "verdict: hold", "verdict: reject"}


def test_compare_includes_rationale_bullets():
    report = compare(_record("inc"), _record("chal"))
    rationale = "\n".join(report.verdict.rationale)

    assert "challenger mean_total_pnl < 0" in rationale
    assert "challenger worst_fold_pnl < -2" in rationale
    assert "challenger consistency_score < 0.4" in rationale
    assert "more than 60% of side-by-side metrics" in rationale
    assert "challenger mean_total_pnl beats incumbent" in rationale
    assert "challenger mean_profit_factor >= incumbent" in rationale
    assert "challenger mean_max_drawdown_pct <=" in rationale
    assert "challenger consistency_score >= incumbent" in rationale
    assert "at least 70% of side-by-side metrics" in rationale


def test_compare_handles_missing_walkforward_gracefully():
    incumbent = _record("inc", aggregated_metrics={}, walk_forward_stability={})
    challenger = _record("chal", aggregated_metrics={}, walk_forward_stability={})

    report = compare(incumbent, challenger)

    assert report.verdict.decision == PromotionDecision.HOLD
    assert "no walk-forward data" in " ".join(report.verdict.rationale)


def _record(
    run_id: str,
    *,
    strategy_name: str = "XauAtrImpulse",
    symbol: str = "XAUUSD",
    total_pnl: float = 50.0,
    profit_factor: float = 1.2,
    win_rate: float = 0.50,
    max_drawdown_pct: float = 0.10,
    sharpe_ratio: float = 1.0,
    n_trades: float = 10.0,
    expectancy: float = 5.0,
    consistency_score: float = 0.6,
    worst_fold_pnl: float = -10.0,
    best_fold_pnl: float = 100.0,
    fold_pnl_std: float = 10.0,
    interpretation: str = "3/5 positive, low variance",
    aggregated_metrics: dict | None = None,
    walk_forward_stability: dict | None = None,
) -> ExperimentRecord:
    if aggregated_metrics is None:
        aggregated_metrics = {
            "total_pnl": {"mean": total_pnl},
            "profit_factor": {"mean": profit_factor},
            "win_rate": {"mean": win_rate},
            "max_drawdown_pct": {"mean": max_drawdown_pct},
            "sharpe_ratio": {"mean": sharpe_ratio},
            "n_trades": {"mean": n_trades},
            "expectancy": {"mean": expectancy},
        }
    if walk_forward_stability is None:
        walk_forward_stability = {
            "consistency_score": consistency_score,
            "worst_fold_pnl": worst_fold_pnl,
            "best_fold_pnl": best_fold_pnl,
            "fold_pnl_std": fold_pnl_std,
            "interpretation": interpretation,
        }
    return ExperimentRecord(
        run_id=run_id,
        strategy_name=strategy_name,
        symbol=symbol,
        created_at="2026-01-01T00:00:00+00:00",
        git_sha="abc123",
        data_hash="hash123",
        config={"params": {}},
        walk_forward_config={"n_folds": 5},
        backtest_config={"symbol": symbol},
        fold_results=[
            {"fold_idx": 0, "test_start": 1_700_000_000, "test_end": 1_700_001_000}
        ],
        aggregated_metrics=aggregated_metrics,
        walk_forward_stability=walk_forward_stability,
        verdict={"status": "pass", "rationale": "ok"},
        notes="",
        promotion_decision="pending",
    )
