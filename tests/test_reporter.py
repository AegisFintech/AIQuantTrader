from __future__ import annotations

import json
from datetime import datetime

import pytest

from finrobot.backtest.engine import BacktestConfig, BacktestResult
from finrobot.backtest.metrics import MetricsReport
from finrobot.backtest.reporter import (
    ReportMetadata,
    generate_report,
    render_markdown,
    verdict_for,
    write_json,
)


def test_reporter_empty_result():
    result = _result([])

    report = generate_report(result, metadata=ReportMetadata(run_id="empty"))

    assert report.metrics.n_trades == 0
    assert report.metrics.total_pnl == 0
    assert report.per_strategy == []
    assert report.verdict.status == "marginal"


def test_reporter_single_strategy():
    trades = _trades("XauGated", [10, 10, 10, 10, 10, 10, -2.5, -2.5, -2.5, -2.5])
    result = _result(trades)

    report = generate_report(result, metadata=ReportMetadata(run_id="single"))

    assert len(report.per_strategy) == 1
    row = report.per_strategy[0]
    assert row.strategy == "XauGated"
    assert row.n_trades == report.metrics.n_trades == 10
    assert row.win_rate == pytest.approx(report.metrics.win_rate)
    assert row.expectancy == pytest.approx(report.metrics.expectancy)
    assert row.total_pnl == pytest.approx(report.metrics.total_pnl)
    assert report.metrics.total_pnl == pytest.approx(50)


def test_reporter_multi_strategy_attribution():
    trades = _trades("A", [10, 10, 20, -5, -5], start=1)
    trades += _trades("B", [5, 5, -5, -5, -10], start=20)
    result = _result(trades)

    report = generate_report(result, metadata=ReportMetadata(run_id="multi"))
    rows = {row.strategy: row for row in report.per_strategy}

    assert set(rows) == {"A", "B"}
    assert report.metrics.total_pnl == pytest.approx(20)
    assert rows["A"].n_trades == 5
    assert rows["A"].win_rate == pytest.approx(3 / 5)
    assert rows["A"].total_pnl == pytest.approx(30)
    assert rows["A"].share_of_pnl == pytest.approx(1.5)
    assert rows["B"].n_trades == 5
    assert rows["B"].win_rate == pytest.approx(2 / 5)
    assert rows["B"].total_pnl == pytest.approx(-10)
    assert rows["B"].share_of_pnl == pytest.approx(-0.5)


def test_reporter_per_strategy_metrics_independent():
    trades = [
        _trade("A", 20, exit_time=10),
        _trade("B", -50, exit_time=20),
        _trade("A", -10, exit_time=30),
        _trade("B", 100, exit_time=40),
        _trade("A", 30, exit_time=50),
    ]
    result = _result(trades)

    report = generate_report(result, metadata=ReportMetadata(run_id="independent"))
    rows = {row.strategy: row for row in report.per_strategy}

    assert rows["A"].profit_factor == pytest.approx(5.0)
    assert rows["A"].max_drawdown == pytest.approx(10)
    assert rows["B"].profit_factor == pytest.approx(2.0)
    assert rows["B"].max_drawdown == pytest.approx(50)


def test_reporter_drawdown_windows_top_5():
    equity_curve = [
        (1, 100),
        (2, 90),
        (3, 100),
        (4, 80),
        (5, 100),
        (6, 95),
        (7, 101),
        (8, 70),
        (9, 101),
        (10, 99),
        (11, 102),
        (12, 60),
        (13, 102),
        (14, 101),
        (15, 103),
        (16, 50),
    ]
    report = generate_report(
        _result([], equity_curve=equity_curve),
        metadata=ReportMetadata(run_id="drawdowns"),
    )

    assert len(report.drawdown_windows) <= 5
    depths = [window.depth_abs for window in report.drawdown_windows]
    assert depths == sorted(depths, reverse=True)
    assert depths == pytest.approx([53, 42, 31, 20, 10])


def test_reporter_json_round_trip(tmp_path):
    report = generate_report(
        _result(_trades("A", [10, -5, 20])),
        metadata=ReportMetadata(run_id="round-trip", params={"risk": 0.001}),
    )
    path = tmp_path / "report.json"

    write_json(report, path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["metadata"]["run_id"] == "round-trip"
    assert payload["metadata"]["params"]["risk"] == pytest.approx(0.001)
    assert payload["metrics"]["total_pnl"] == pytest.approx(25)
    assert payload["per_strategy"][0]["strategy"] == "A"
    assert isinstance(payload["equity_curve"][0], list)
    assert "drawdown_windows" in payload
    assert payload["walk_forward_stability"] is None
    assert payload["verdict"]["status"] in {"pass", "fail", "marginal"}


def test_reporter_markdown_contains_sections():
    report = generate_report(
        _result(_trades("A", [10, -5])),
        metadata=ReportMetadata(run_id="markdown"),
    )

    markdown = render_markdown(report)

    assert "## Configuration" in markdown
    assert "## Overall Metrics" in markdown
    assert "## Per-Strategy Attribution" in markdown
    assert "## Equity Curve Summary" in markdown
    assert "## Drawdown Windows" in markdown
    assert "## Walk-Forward Stability" in markdown
    assert "## Verdict" in markdown


def test_reporter_verdict_pass():
    pnls = [200 / 6] * 6 + [-20] * 5
    result = _result(
        _trades("A", pnls),
        equity_curve=[
            (1, 10000),
            (2, 11111.111111),
            (3, 10000),
            (4, 10100),
        ],
    )

    report = generate_report(result, metadata=ReportMetadata(run_id="pass"))

    assert report.metrics.total_pnl == pytest.approx(100)
    assert report.metrics.profit_factor == pytest.approx(2.0)
    assert report.metrics.win_rate >= 0.45
    assert report.metrics.max_drawdown_pct == pytest.approx(0.10)
    assert report.verdict.status == "pass"


def test_reporter_verdict_fail():
    pnls = [50 / 3] * 3 + [-(100 / 7)] * 7
    result = _result(
        _trades("A", pnls),
        equity_curve=[
            (1, 10000),
            (2, 10000),
            (3, 5000),
            (4, 9950),
        ],
    )

    report = generate_report(result, metadata=ReportMetadata(run_id="fail"))

    assert report.metrics.total_pnl == pytest.approx(-50)
    assert report.metrics.profit_factor == pytest.approx(0.5)
    assert report.metrics.max_drawdown_pct == pytest.approx(0.5)
    assert report.verdict.status == "fail"


def test_reporter_verdict_marginal():
    metrics = MetricsReport(
        n_trades=10,
        win_rate=0.40,
        avg_win=10.0,
        avg_loss=-10.0,
        profit_factor=1.0,
        expectancy=2.0,
        total_pnl=20.0,
        max_drawdown=2500.0,
        max_drawdown_pct=0.25,
        max_drawdown_duration_bars=0,
        sharpe_ratio=0.0,
        sortino_ratio=0.0,
        calmar_ratio=0.0,
        avg_holding_time_seconds=60.0,
        final_equity=10020.0,
    )

    verdict = verdict_for(metrics, [])

    assert verdict.status == "marginal"


def test_reporter_metadata_creates_at():
    report = generate_report(
        _result(_trades("A", [10])),
        metadata=ReportMetadata(run_id="created-at"),
    )

    assert report.metadata.created_at
    assert datetime.fromisoformat(report.metadata.created_at)


def test_reporter_includes_params_in_markdown():
    report = generate_report(
        _result(_trades("A", [10])),
        metadata=ReportMetadata(
            run_id="params",
            params={"risk_per_trade": 0.001, "mode": "gated"},
        ),
    )

    markdown = render_markdown(report)

    assert "risk_per_trade" in markdown
    assert "0.001" in markdown
    assert "mode" in markdown
    assert "gated" in markdown


def test_reporter_walk_forward_stability_placeholder():
    report = generate_report(
        _result(_trades("A", [10])),
        metadata=ReportMetadata(run_id="wf"),
    )

    assert report.walk_forward_stability is None


def test_reporter_share_of_pnl_signs():
    trades = _trades("Winner", [50, 50], start=1)
    trades += _trades("Loser", [-30], start=10)
    report = generate_report(
        _result(trades),
        metadata=ReportMetadata(run_id="shares"),
    )
    rows = {row.strategy: row for row in report.per_strategy}

    assert report.metrics.total_pnl == pytest.approx(70)
    assert rows["Winner"].share_of_pnl == pytest.approx(100 / 70)
    assert rows["Loser"].share_of_pnl == pytest.approx(-30 / 70)


def _result(
    trades: list[dict],
    *,
    equity_curve: list[tuple[int, float]] | None = None,
    initial_equity: float = 10000.0,
    strategy_name: str = "Composite",
) -> BacktestResult:
    curve = equity_curve
    if curve is None and trades:
        curve = _curve_from_trades(trades, initial_equity=initial_equity)
    if curve is None:
        curve = []

    total_pnl = sum(float(trade["pnl"]) for trade in trades)
    start_time = int(curve[0][0]) if curve else 0
    end_time = int(curve[-1][0]) if curve else 0
    return BacktestResult(
        config=BacktestConfig(symbol="XAUUSD", initial_equity=initial_equity),
        strategy_name=strategy_name,
        bars=len(curve),
        start_time=start_time,
        end_time=end_time,
        initial_equity=initial_equity,
        final_equity=initial_equity + total_pnl,
        trades=trades,
        equity_curve=curve,
        open_positions_at_end=[],
        rejected_signals=0,
    )


def _trades(strategy: str, pnls: list[float], *, start: int = 1) -> list[dict]:
    return [
        _trade(strategy, pnl, exit_time=start + index * 60)
        for index, pnl in enumerate(pnls)
    ]


def _trade(strategy: str, pnl: float, *, exit_time: int) -> dict:
    return {
        "strategy": strategy,
        "entry_time": exit_time - 30,
        "exit_time": exit_time,
        "pnl": float(pnl),
    }


def _curve_from_trades(
    trades: list[dict], *, initial_equity: float
) -> list[tuple[int, float]]:
    curve: list[tuple[int, float]] = [(0, initial_equity)]
    cumulative = 0.0
    for trade in sorted(trades, key=lambda item: item["exit_time"]):
        cumulative += float(trade["pnl"])
        curve.append((int(trade["exit_time"]), initial_equity + cumulative))
    return curve
