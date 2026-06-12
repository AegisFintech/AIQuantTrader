from __future__ import annotations

from dataclasses import replace

import pytest

from finrobot.backtest import (
    BacktestConfig,
    BacktestResult,
    FillConfig,
    FoldResult,
    FoldSplit,
    MetricsReport,
    PositionSizer,
    Signal,
    Strategy,
    Verdict,
    WalkForwardConfig,
    aggregate_fold_metrics,
    build_folds,
    compute_stability,
    run_walkforward,
)


def test_walkforward_build_folds_basic():
    folds = build_folds(
        _bars(1000),
        WalkForwardConfig(n_folds=5, min_train_bars=1, min_test_bars=100),
    )

    assert [fold.fold_idx for fold in folds] == [0, 1, 2, 3, 4]
    assert [len(fold.train_bars) for fold in folds] == sorted(
        len(fold.train_bars) for fold in folds
    )
    previous_test_end = 0
    for fold in folds:
        assert fold.test_start >= previous_test_end
        assert fold.test_bars
        assert not _times(fold.train_bars) & _times(fold.test_bars)
        previous_test_end = fold.test_end


def test_walkforward_purge_drops_bars():
    folds = build_folds(
        _bars(1000),
        WalkForwardConfig(
            n_folds=5,
            n_purge_bars=50,
            min_train_bars=1,
            min_test_bars=100,
        ),
    )

    for fold in folds:
        assert fold.purge_bars == 50
        assert fold.train_end + 50 * 60 == fold.test_start
        assert not _times(fold.train_bars) & _times(fold.test_bars)


def test_walkforward_embargo_drops_bars():
    folds = build_folds(
        _bars(1000),
        WalkForwardConfig(
            n_folds=5,
            n_embargo_bars=30,
            min_train_bars=1,
            min_test_bars=100,
        ),
    )

    for previous, current in zip(folds, folds[1:]):
        assert current.test_start >= previous.test_end + 30 * 60
        assert current.train_end <= current.test_start
        assert previous.embargo_bars == 30


def test_walkforward_fixed_train_size():
    folds = build_folds(
        _bars(1000),
        WalkForwardConfig(
            n_folds=5,
            train_size_bars=500,
            min_train_bars=100,
            min_test_bars=100,
        ),
    )

    assert 2 <= len(folds) <= 3
    assert all(len(fold.train_bars) == 500 for fold in folds)


def test_walkforward_refuses_too_small_train():
    with pytest.raises(ValueError, match="min_train_bars"):
        build_folds(
            _bars(500),
            WalkForwardConfig(n_folds=5, min_train_bars=600, min_test_bars=50),
        )


def test_walkforward_factory_called_per_fold():
    instances: list[CountingStrategy] = []

    def factory() -> CountingStrategy:
        strategy = CountingStrategy()
        instances.append(strategy)
        return strategy

    result = run_walkforward(
        _bars(500),
        strategy_factory=factory,
        config=WalkForwardConfig(n_folds=3, min_train_bars=1, min_test_bars=50),
        backtest_config=_config(),
    )

    assert len(result.folds) == 3
    assert len(instances) == 3
    assert len({id(instance) for instance in instances}) == 3


def test_walkforward_per_fold_results_independent():
    result = run_walkforward(
        _bars(300),
        strategy_factory=OneShotStrategy,
        config=WalkForwardConfig(n_folds=2, min_train_bars=1, min_test_bars=50),
        backtest_config=_config(),
    )

    for fold_result in result.folds:
        for trade in fold_result.result.trades:
            assert fold_result.fold.test_start <= trade["entry_time"] < fold_result.fold.test_end
            assert fold_result.fold.test_start <= trade["exit_time"] <= fold_result.fold.test_end


def test_walkforward_aggregated_metrics():
    folds = [_fold_result(idx, pnl) for idx, pnl in enumerate([10, 20, 30, 40, 50])]

    aggregated = aggregate_fold_metrics(folds)

    assert aggregated.total_pnl.mean == pytest.approx(30)
    assert aggregated.total_pnl.min == pytest.approx(10)
    assert aggregated.total_pnl.max == pytest.approx(50)
    assert aggregated.total_pnl.worst_fold_idx == 0


def test_walkforward_stability_score():
    assert compute_stability(
        [_fold_result(idx, pnl) for idx, pnl in enumerate([1, 2, 3, 4, 5])]
    ).consistency_score == pytest.approx(1.0)
    assert compute_stability(
        [_fold_result(idx, pnl) for idx, pnl in enumerate([1, 2, -1, -2, -3])]
    ).consistency_score == pytest.approx(0.4)
    assert compute_stability(
        [_fold_result(idx, pnl) for idx, pnl in enumerate([-1, -2, -3, -4, -5])]
    ).consistency_score == pytest.approx(0.0)


def test_walkforward_verdict_pass():
    result = _walkforward_result_for_metrics(
        [
            _metrics(total_pnl=10, profit_factor=2.0, max_drawdown_pct=0.05),
            _metrics(total_pnl=11, profit_factor=1.8, max_drawdown_pct=0.04),
            _metrics(total_pnl=12, profit_factor=1.9, max_drawdown_pct=0.03),
            _metrics(total_pnl=13, profit_factor=1.7, max_drawdown_pct=0.06),
            _metrics(total_pnl=14, profit_factor=2.1, max_drawdown_pct=0.05),
        ]
    )

    assert result.verdict.status == "pass"


def test_walkforward_verdict_fail_catastrophic():
    result = _walkforward_result_for_metrics(
        [
            _metrics(total_pnl=10, profit_factor=2.0),
            _metrics(total_pnl=10, profit_factor=2.0),
            _metrics(total_pnl=-1000, profit_factor=1.0),
            _metrics(total_pnl=10, profit_factor=2.0),
            _metrics(total_pnl=10, profit_factor=2.0),
        ]
    )

    assert result.verdict.status == "fail"


def test_walkforward_verdict_marginal_mixed():
    result = _walkforward_result_for_metrics(
        [
            _metrics(total_pnl=10, profit_factor=1.0),
            _metrics(total_pnl=9, profit_factor=1.0),
            _metrics(total_pnl=8, profit_factor=1.0),
            _metrics(total_pnl=-1, profit_factor=1.0),
            _metrics(total_pnl=-2, profit_factor=1.0),
        ]
    )

    assert result.verdict.status == "marginal"


class CountingStrategy(Strategy):
    name = "Counting"

    def on_bar(self, **kwargs) -> Signal:
        return Signal(action="HOLD", strategy=self.name)


class OneShotStrategy(Strategy):
    name = "OneShot"

    def __init__(self):
        self.sent = False

    def on_bar(self, **kwargs) -> Signal:
        if self.sent:
            return Signal(action="HOLD", strategy=self.name)
        self.sent = True
        return Signal(
            action="BUY",
            sl_distance=50.0,
            tp_distance=50.0,
            strategy=self.name,
        )


def _walkforward_result_for_metrics(metrics: list[MetricsReport]):
    from finrobot.backtest.walkforward import (
        WalkForwardResult,
        aggregate_fold_metrics,
        compute_stability,
    )
    from finrobot.backtest.walkforward import _walk_forward_verdict

    folds = [
        _fold_result(idx, metric.total_pnl, metrics=metric)
        for idx, metric in enumerate(metrics)
    ]
    aggregated = aggregate_fold_metrics(folds)
    stability = compute_stability(folds)
    return WalkForwardResult(
        config=WalkForwardConfig(n_folds=len(metrics)),
        folds=folds,
        aggregated_metrics=aggregated,
        walk_forward_stability=stability,
        verdict=_walk_forward_verdict(aggregated=aggregated, stability=stability),
    )


def _fold_result(
    idx: int,
    pnl: float,
    *,
    metrics: MetricsReport | None = None,
) -> FoldResult:
    metric = metrics or _metrics(total_pnl=pnl)
    result = _result_for_pnl(pnl)
    return FoldResult(
        fold=FoldSplit(
            fold_idx=idx,
            train_bars=[],
            test_bars=[],
            train_start=idx * 100,
            train_end=idx * 100 + 50,
            test_start=idx * 100 + 50,
            test_end=idx * 100 + 100,
            purge_bars=0,
            embargo_bars=0,
        ),
        result=result,
        metrics=metric,
        verdict=Verdict(status="marginal", rationale=""),
    )


def _metrics(
    *,
    total_pnl: float,
    profit_factor: float = 2.0,
    max_drawdown_pct: float = 0.05,
) -> MetricsReport:
    return MetricsReport(
        n_trades=5,
        win_rate=0.60,
        avg_win=10.0,
        avg_loss=-5.0,
        profit_factor=profit_factor,
        expectancy=total_pnl / 5.0,
        total_pnl=total_pnl,
        max_drawdown=max_drawdown_pct * 10000.0,
        max_drawdown_pct=max_drawdown_pct,
        sharpe_ratio=1.0,
        avg_holding_time_seconds=60.0,
        final_equity=10000.0 + total_pnl,
    )


def _result_for_pnl(pnl: float) -> BacktestResult:
    config = _config()
    return BacktestResult(
        config=config,
        strategy_name="Synthetic",
        bars=2,
        start_time=0,
        end_time=60,
        initial_equity=config.initial_equity,
        final_equity=config.initial_equity + pnl,
        trades=[
            {
                "entry_time": 0,
                "exit_time": 60,
                "pnl": pnl,
                "strategy": "Synthetic",
            }
        ],
        equity_curve=[(0, config.initial_equity), (60, config.initial_equity + pnl)],
        open_positions_at_end=[],
        rejected_signals=0,
    )


def _config() -> BacktestConfig:
    return BacktestConfig(
        fill_config=FillConfig(spread_points=0.0, slippage_points=0.0),
        sizer=PositionSizer(
            risk_per_trade_fraction=0.001,
            daily_loss_cap_fraction=0.01,
            max_lot_per_trade=0.10,
            max_positions_per_symbol=2,
        ),
    )


def _bars(count: int, *, start: int = 1_700_000_000) -> list[dict]:
    bars = []
    for idx in range(count):
        close = 100.0 + idx * 0.01
        bars.append(
            {
                "time": start + idx * 60,
                "open": close - 0.1,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 100.0,
            }
        )
    return bars


def _times(bars: list[dict]) -> set[int]:
    return {int(bar["time"]) for bar in bars}
