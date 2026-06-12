from __future__ import annotations

import duckdb

from finrobot.research.comparison import PromotionDecision, compare
from finrobot.research.experiments import ExperimentRecord
from finrobot.research.registry import (
    index_promotion_report,
    init_promotion_registry,
    query_promotion_reports,
)


def test_init_promotion_registry_creates_table():
    con = duckdb.connect(":memory:")
    init_promotion_registry(con)

    rows = con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name = 'promotion_reports'"
    ).fetchall()

    assert rows == [("promotion_reports",)]


def test_index_and_query_promotion(tmp_path):
    con = duckdb.connect(":memory:")
    report = compare(
        _record("inc", total_pnl=50),
        _record("chal", total_pnl=200, profit_factor=2.0, consistency_score=0.8),
        report_id="promotion-one",
    )

    index_promotion_report(
        con,
        report,
        md_path=tmp_path / "promotion-one.md",
        json_path=tmp_path / "promotion-one.json",
    )
    rows = query_promotion_reports(con, symbol="XAUUSD")

    assert len(rows) == 1
    assert rows[0]["report_id"] == "promotion-one"
    assert rows[0]["decision"] == report.verdict.decision.value
    metric_rows = con.execute(
        "SELECT metric, winner FROM promotion_report_metrics WHERE report_id = ?",
        ["promotion-one"],
    ).fetchall()
    assert ("mean_total_pnl", "challenger") in metric_rows


def test_query_filters_by_decision(tmp_path):
    con = duckdb.connect(":memory:")
    accept = compare(
        _record("inc-a", total_pnl=50),
        _record("chal-a", total_pnl=200, profit_factor=2.0, consistency_score=0.8),
        report_id="accept-report",
    )
    reject = compare(
        _record("inc-r", total_pnl=50),
        _record("chal-r", total_pnl=-50),
        report_id="reject-report",
    )
    index_promotion_report(
        con,
        accept,
        md_path=tmp_path / "accept.md",
        json_path=tmp_path / "accept.json",
    )
    index_promotion_report(
        con,
        reject,
        md_path=tmp_path / "reject.md",
        json_path=tmp_path / "reject.json",
    )

    rows = query_promotion_reports(con, decision=PromotionDecision.REJECT)

    assert [row["report_id"] for row in rows] == ["reject-report"]


def _record(
    run_id: str,
    *,
    strategy_name: str = "XauAtrImpulse",
    symbol: str = "XAUUSD",
    total_pnl: float = 50.0,
    profit_factor: float = 1.2,
    consistency_score: float = 0.6,
) -> ExperimentRecord:
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
        fold_results=[],
        aggregated_metrics={
            "total_pnl": {"mean": total_pnl},
            "profit_factor": {"mean": profit_factor},
            "win_rate": {"mean": 0.5 if total_pnl >= 0 else 0.2},
            "max_drawdown_pct": {"mean": 0.10},
            "sharpe_ratio": {"mean": 1.0 if total_pnl >= 0 else -1.0},
            "n_trades": {"mean": 10},
            "expectancy": {"mean": total_pnl / 10},
        },
        walk_forward_stability={
            "consistency_score": consistency_score,
            "worst_fold_pnl": -10.0 if total_pnl >= 0 else -50.0,
            "best_fold_pnl": max(total_pnl, 0.0),
            "fold_pnl_std": 10.0,
            "interpretation": "3/5 positive, low variance",
        },
        verdict={"status": "pass", "rationale": "ok"},
        notes="",
        promotion_decision="pending",
    )
