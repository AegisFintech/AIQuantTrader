from __future__ import annotations

from finrobot.backtest import (
    Backtester,
    BacktestConfig,
    BtcGatedParams,
    BtcGatedStrategy,
    FillConfig,
    PositionSizer,
    Signal,
    Strategy,
    compare_decisions,
)


def test_btc_gated_passes_inner_signal_when_all_gates_pass():
    signal = _run_strategy_to_bar(
        BtcGatedStrategy(_SignalAtBar(59, "BUY"), BtcGatedParams(htf_trend=1)),
        _long_smc_two_bars(),
        59,
    )

    assert signal.action == "BUY"
    assert signal.strategy == "BtcGated"
    assert signal.smc_score == 2


def test_btc_gated_pda_long_rejects_above_0_45():
    signal = _run_strategy_to_bar(
        BtcGatedStrategy(
            _SignalAtBar(59, "BUY"),
            BtcGatedParams(htf_trend=1, min_smc_score=0),
        ),
        _long_pda_reject_bars(),
        59,
    )

    assert signal.action == "HOLD"
    assert signal.comment == "btc_direction_reject"


def test_btc_gated_pda_short_rejects_below_0_55():
    signal = _run_strategy_to_bar(
        BtcGatedStrategy(
            _SignalAtBar(59, "SELL"),
            BtcGatedParams(htf_trend=-1, min_smc_score=0),
        ),
        _short_pda_reject_bars(),
        59,
    )

    assert signal.action == "HOLD"
    assert signal.comment == "btc_direction_reject"


def test_btc_gated_direction_rejects_when_htf_trend_wrong():
    signal = _run_strategy_to_bar(
        BtcGatedStrategy(_SignalAtBar(59, "BUY"), BtcGatedParams(htf_trend=-1)),
        _long_smc_two_bars(),
        59,
    )

    assert signal.action == "HOLD"
    assert signal.comment == "btc_direction_reject"


def test_btc_gated_smc_min_2_allows_lower_score():
    signal = _run_strategy_to_bar(
        BtcGatedStrategy(_SignalAtBar(59, "BUY"), BtcGatedParams(htf_trend=1)),
        _long_smc_two_bars(),
        59,
    )

    assert signal.action == "BUY"
    assert signal.smc_score == 2


def test_btc_gated_smc_min_2_blocks_1():
    signal = _run_strategy_to_bar(
        BtcGatedStrategy(_SignalAtBar(59, "BUY"), BtcGatedParams(htf_trend=1)),
        _long_smc_one_bars(),
        59,
    )

    assert signal.action == "HOLD"
    assert signal.comment == "smc_reject"


def test_btc_gated_disable_direction_gate():
    signal = _run_strategy_to_bar(
        BtcGatedStrategy(
            _SignalAtBar(59, "BUY"),
            BtcGatedParams(htf_trend=-1, enable_direction_gate=False),
        ),
        _long_smc_two_bars(),
        59,
    )

    assert signal.action == "BUY"
    assert signal.strategy == "BtcGated"


def test_btc_gated_preserves_inner_comment():
    signal = _run_strategy_to_bar(
        BtcGatedStrategy(
            _SignalAtBar(59, "BUY", comment="Momentum_trend"),
            BtcGatedParams(htf_trend=1),
        ),
        _long_smc_two_bars(),
        59,
    )

    assert signal.action == "BUY"
    assert signal.comment == "Momentum_trend"


def test_btc_gated_runs_through_backtester():
    result = Backtester(_backtest_config()).run(
        strategy=BtcGatedStrategy(_SignalAtBar(59, "BUY"), BtcGatedParams(htf_trend=1)),
        bars=_long_smc_two_bars(),
    )

    assert len(result.trades) == 1
    assert result.trades[0]["side"] == "BUY"
    assert result.trades[0]["strategy"] == "BtcGated"


def test_btc_gated_synthesized_parity():
    bars = _long_smc_two_bars()
    result = Backtester(_backtest_config(risk_per_trade_fraction=0.0001)).run(
        strategy=BtcGatedStrategy(
            _SignalAtBar(59, "BUY", sl_distance=100.0),
            BtcGatedParams(htf_trend=1),
        ),
        bars=bars,
    )

    report = compare_decisions(
        result,
        [
            {
                "bar_idx": 59,
                "action": "BUY",
                "side": "BUY",
                "volume": 0.01,
                "price": bars[59]["close"],
            }
        ],
        bar_window=0,
        fill_tolerance_points=0.0,
    )

    assert report.match_rate == 1.0
    assert report.n_matched == 1


class _SignalAtBar(Strategy):
    name = "SignalAtBar"

    def __init__(
        self,
        bar_idx: int,
        action: str,
        *,
        comment: str = "inner_signal",
        sl_distance: float = 20.0,
    ):
        self.bar_idx = int(bar_idx)
        self.action = action
        self.comment = comment
        self.sl_distance = float(sl_distance)

    def on_bar(self, *, idx: int, **kwargs) -> Signal:
        if idx != self.bar_idx:
            return Signal(action="HOLD", strategy=self.name)
        if self.action.upper() == "HOLD":
            return Signal(action="HOLD", strategy=self.name)
        return Signal(
            action=self.action,
            sl_distance=self.sl_distance,
            tp_distance=self.sl_distance * 2.0,
            strategy=self.name,
            comment=self.comment,
        )


def _run_strategy_to_bar(strategy: Strategy, bars: list[dict], target_idx: int) -> Signal:
    signal = None
    history: list[dict] = []
    for idx, bar in enumerate(bars[: target_idx + 1]):
        history.append(bar)
        signal = strategy.on_bar(
            idx=idx,
            bar=bar,
            history=history,
            open_positions=[],
            equity=10000.0,
            day_closed_pnl=0.0,
        )
    assert signal is not None
    return signal


def _long_smc_two_bars() -> list[dict]:
    bars = _flat_range_prefix(length=59)
    bars.append(_bar(59, close=102.0, high=102.1, low=101.9))
    return bars


def _long_smc_one_bars() -> list[dict]:
    bars = _flat_range_prefix(length=59)
    bars.append(_bar(59, close=103.0, high=103.1, low=102.9))
    return bars


def _long_pda_reject_bars() -> list[dict]:
    bars = _flat_range_prefix(length=59)
    bars.append(_bar(59, close=105.0, high=105.1, low=104.9))
    return bars


def _short_pda_reject_bars() -> list[dict]:
    bars = _flat_range_prefix(length=59)
    bars.append(_bar(59, close=105.0, high=105.1, low=104.9))
    return bars


def _flat_range_prefix(*, length: int) -> list[dict]:
    return [
        _bar(idx, open_=105.0, close=105.0, high=110.0, low=100.0)
        for idx in range(length)
    ]


def _backtest_config(
    *,
    risk_per_trade_fraction: float = 0.001,
) -> BacktestConfig:
    return BacktestConfig(
        symbol="BTCUSD",
        fill_config=FillConfig(spread_points=0.0, slippage_points=0.0),
        sizer=PositionSizer(
            risk_per_trade_fraction=risk_per_trade_fraction,
            daily_loss_cap_fraction=0.01,
            max_lot_per_trade=0.25,
            max_positions_per_symbol=2,
        ),
    )


def _bar(
    idx: int,
    *,
    close: float,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
) -> dict:
    return {
        "time": 1_700_000_000 + idx * 300,
        "open": close if open_ is None else open_,
        "high": close + 0.5 if high is None else high,
        "low": close - 0.5 if low is None else low,
        "close": close,
        "volume": 1.0,
    }
