from __future__ import annotations

import math

import pytest

from aiquanttrader.backtest import (
    Backtester,
    BacktestConfig,
    FillConfig,
    PositionSizer,
    Signal,
    Strategy,
    XauAtrImpulseStrategy,
    XauGatedParams,
    XauGatedStrategy,
    XauQuickMomentumStrategy,
    compare_decisions,
)


_NO_ADX = XauGatedParams(enable_adx_gate=False)
_NO_ADX_SMC3 = XauGatedParams(enable_adx_gate=False, min_smc_score=3)


def test_xau_gated_passes_inner_signal_when_pda_and_smc_pass():
    signal = _run_strategy_to_bar(
        XauGatedStrategy(_SignalAtBar(_signal_idx(), "BUY"), _NO_ADX_SMC3),
        _long_gate_pass_bars(),
        _signal_idx(),
    )

    assert signal.action == "BUY"
    assert signal.strategy == "XauGated"
    assert signal.smc_score is not None
    assert signal.smc_score >= 3


def test_xau_gated_default_smc4_blocks_legacy_score3_setup():
    signal = _run_strategy_to_bar(
        XauGatedStrategy(_SignalAtBar(_signal_idx(), "BUY"), _NO_ADX),
        _long_gate_pass_bars(),
        _signal_idx(),
    )

    assert signal.action == "HOLD"
    assert signal.strategy == "XauGated"
    assert signal.comment == "smc_reject"


def test_xau_gated_pda_gate_blocks_long_in_premium():
    signal = _run_strategy_to_bar(
        XauGatedStrategy(_SignalAtBar(_signal_idx(), "BUY"), _NO_ADX_SMC3),
        _long_pda_reject_bars(),
        _signal_idx(),
    )

    assert signal.action == "HOLD"
    assert signal.strategy == "XauGated"
    assert signal.comment == "xau_pda_reject"


def test_xau_gated_pda_gate_blocks_short_in_discount():
    signal = _run_strategy_to_bar(
        XauGatedStrategy(_SignalAtBar(_signal_idx(), "SELL"), _NO_ADX_SMC3),
        _short_pda_reject_bars(),
        _signal_idx(),
    )

    assert signal.action == "HOLD"
    assert signal.strategy == "XauGated"
    assert signal.comment == "xau_pda_reject"


def test_xau_gated_smc_gate_blocks_low_confluence():
    signal = _run_strategy_to_bar(
        XauGatedStrategy(_SignalAtBar(_signal_idx(), "BUY"), _NO_ADX),
        _long_low_smc_bars(),
        _signal_idx(),
    )

    assert signal.action == "HOLD"
    assert signal.strategy == "XauGated"
    assert signal.comment == "smc_reject"


def test_xau_gated_disable_smc_gate():
    signal = _run_strategy_to_bar(
        XauGatedStrategy(
            _SignalAtBar(_signal_idx(), "BUY"),
            XauGatedParams(enable_smc_gate=False, enable_adx_gate=False),
        ),
        _long_low_smc_bars(),
        _signal_idx(),
    )

    assert signal.action == "BUY"
    assert signal.strategy == "XauGated"


def test_xau_gated_disable_pda_gate():
    signal = _run_strategy_to_bar(
        XauGatedStrategy(
            _SignalAtBar(_signal_idx(), "BUY"),
            XauGatedParams(enable_pda_gate=False, enable_adx_gate=False, min_smc_score=0),
        ),
        _long_pda_reject_bars(),
        _signal_idx(),
    )

    assert signal.action == "BUY"
    assert signal.strategy == "XauGated"


def test_xau_gated_adx_gate_blocks_low_adx():
    signal = _run_strategy_to_bar(
        XauGatedStrategy(_SignalAtBar(_signal_idx(), "BUY")),
        _long_gate_pass_bars(),
        _signal_idx(),
    )
    assert signal.action == "HOLD"
    assert signal.comment == "adx_regime_reject"


def test_xau_gated_disable_adx_gate():
    signal = _run_strategy_to_bar(
        XauGatedStrategy(
            _SignalAtBar(_signal_idx(), "BUY"),
            _NO_ADX_SMC3,
        ),
        _long_gate_pass_bars(),
        _signal_idx(),
    )
    assert signal.action == "BUY"


def test_xau_gated_macd_alignment_blocks_countermomentum_buy():
    bars = [_m1_bar(idx, close=200.0 - idx) for idx in range(60)]
    signal = _run_strategy_to_bar(
        XauGatedStrategy(
            _SignalAtBar(59, "BUY"),
            XauGatedParams(
                enable_pda_gate=False,
                enable_smc_gate=False,
                enable_adx_gate=False,
                enable_macd_histogram_alignment=True,
            ),
        ),
        bars,
        59,
    )

    assert signal.action == "HOLD"
    assert signal.comment == "direction_reject"


def test_xau_gated_blackout_hook_blocks_signal():
    bars = _long_gate_pass_bars()
    bars[_signal_idx()]["blackout"] = True

    signal = _run_strategy_to_bar(
        XauGatedStrategy(
            _SignalAtBar(_signal_idx(), "BUY"),
            XauGatedParams(
                enable_pda_gate=False,
                enable_smc_gate=False,
                enable_adx_gate=False,
                blackout_enabled=True,
            ),
        ),
        bars,
        _signal_idx(),
    )

    assert signal.action == "HOLD"
    assert signal.comment == "blackout_reject"


def test_xau_gated_inner_hold_means_outer_hold():
    signal = _run_strategy_to_bar(
        XauGatedStrategy(_SignalAtBar(_signal_idx(), "HOLD"), _NO_ADX),
        _long_gate_pass_bars(),
        _signal_idx(),
    )

    assert signal.action == "HOLD"
    assert signal.strategy == "XauGated"
    assert signal.comment == ""


def test_xau_gated_preserves_inner_comment():
    signal = _run_strategy_to_bar(
        XauGatedStrategy(_SignalAtBar(_signal_idx(), "BUY", comment="ATR_impulse"), _NO_ADX_SMC3),
        _long_gate_pass_bars(),
        _signal_idx(),
    )

    assert signal.action == "BUY"
    assert signal.comment == "ATR_impulse"


def test_xau_gated_runs_through_backtester():
    result = Backtester(_backtest_config()).run(
        strategy=XauGatedStrategy(_SignalAtBar(_signal_idx(), "BUY"), _NO_ADX_SMC3),
        bars=_long_gate_pass_bars(),
    )

    assert len(result.trades) == 1
    assert result.trades[0]["side"] == "BUY"
    assert result.trades[0]["strategy"] == "XauGated"


def test_xau_gated_synthesized_parity():
    bars = _long_gate_pass_bars()
    result = Backtester(_backtest_config(risk_per_trade_fraction=0.0001)).run(
        strategy=XauGatedStrategy(
            _SignalAtBar(_signal_idx(), "BUY", sl_distance=100.0), _NO_ADX_SMC3
        ),
        bars=bars,
    )

    report = compare_decisions(
        result,
        [
            {
                "bar_idx": _signal_idx(),
                "action": "BUY",
                "side": "BUY",
                "volume": 0.01,
                "price": bars[_signal_idx()]["close"],
            }
        ],
        bar_window=0,
        fill_tolerance_points=0.0,
    )

    assert report.match_rate == 1.0
    assert report.n_matched == 1


def test_xau_gated_min_bars_between_signals_blocks_second_signal():
    strategy = XauGatedStrategy(
        _SignalOnBars({_signal_idx(): "BUY", _signal_idx() + 1: "BUY"}),
        XauGatedParams(
            enable_pda_gate=False,
            enable_smc_gate=False,
            enable_adx_gate=False,
            min_bars_between_signals=2,
        ),
    )
    bars = _long_gate_pass_bars() + _m1_bars_from_m5(
        [_bar(60, close=101.2, high=101.7, low=100.7)]
    )

    first = _run_strategy_to_bar(strategy, bars, _signal_idx())
    second = _run_strategy_to_bar(strategy, bars, _signal_idx() + 1)

    assert first.action == "BUY"
    assert second.action == "HOLD"
    assert second.comment == "min_interval_reject"


def test_backtester_min_seconds_between_trades_rejects_second_open():
    result = Backtester(
        _backtest_config(max_positions_per_symbol=5, min_seconds_between_trades=120)
    ).run(
        strategy=_SignalOnBars({0: "BUY", 1: "BUY"}),
        bars=[
            _m1_bar(0, close=100.0, high=101.0, low=99.0),
            _m1_bar(1, close=100.2, high=101.2, low=99.2),
            _m1_bar(2, close=100.4, high=101.4, low=99.4),
        ],
    )

    assert len(result.trades) == 1
    assert result.rejected_signals == 1


def test_backtester_loss_streak_pause_rejects_new_signal():
    config = _backtest_config(max_positions_per_symbol=5)
    config.loss_streak_pause_count = 1

    result = Backtester(config).run(
        strategy=_SignalOnBars({0: "BUY", 2: "BUY"}),
        bars=[
            _m1_bar(0, close=100.0, high=101.0, low=99.0),
            _m1_bar(1, close=100.0, high=101.0, low=70.0),
            _m1_bar(2, close=100.0, high=101.0, low=99.0),
        ],
    )

    assert len(result.trades) == 1
    assert result.trades[0]["pnl"] < 0
    assert result.rejected_signals == 1


@pytest.mark.parametrize(
    "inner_cls",
    [XauQuickMomentumStrategy, XauAtrImpulseStrategy],
)
def test_xau_gated_with_disabled_inner_strategies(inner_cls):
    bars = (
        _quick_momentum_long_bars()
        if inner_cls is XauQuickMomentumStrategy
        else _atr_impulse_long_bars()
    )
    signal = _run_strategy_to_bar(
        XauGatedStrategy(
            inner_cls(),
            XauGatedParams(enable_pda_gate=False, enable_smc_gate=False, enable_adx_gate=False),
        ),
        bars,
        len(bars) - 1,
    )

    assert signal.action == "BUY"
    assert signal.strategy == "XauGated"


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


class _SignalOnBars(Strategy):
    name = "SignalOnBars"

    def __init__(self, signals: dict[int, str]):
        self.signals = signals

    def on_bar(self, *, idx: int, **kwargs) -> Signal:
        action = self.signals.get(idx, "HOLD")
        if action == "HOLD":
            return Signal(action="HOLD", strategy=self.name)
        return Signal(
            action=action,
            sl_distance=20.0,
            tp_distance=40.0,
            strategy=self.name,
            comment="sequence",
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


def _long_gate_pass_bars() -> list[dict]:
    return _m1_bars_from_m5(_long_gate_pass_m5_bars())


def _long_gate_pass_m5_bars() -> list[dict]:
    bars = _flat_range_prefix()
    bars.extend(
        [
            _bar(56, close=99.5, high=100.0, low=99.0),
            _bar(57, close=101.0, high=101.5, low=100.5),
            _bar(58, close=103.5, high=104.0, low=103.0),
            _bar(59, close=101.0, high=101.5, low=100.5),
        ]
    )
    return bars


def _long_pda_reject_bars() -> list[dict]:
    bars = _long_gate_pass_m5_bars()
    bars[-1] = _bar(59, close=108.0, high=108.5, low=107.5)
    return _m1_bars_from_m5(bars)


def _long_low_smc_bars() -> list[dict]:
    bars = _flat_range_prefix(length=59)
    bars.append(_bar(59, close=103.0, high=103.1, low=102.9))
    return _m1_bars_from_m5(bars)


def _short_gate_pass_bars() -> list[dict]:
    return _m1_bars_from_m5(_short_gate_pass_m5_bars())


def _short_gate_pass_m5_bars() -> list[dict]:
    bars = _flat_range_prefix()
    bars.extend(
        [
            _bar(56, close=110.5, high=111.0, low=110.0),
            _bar(57, close=108.5, high=109.0, low=108.0),
            _bar(58, close=106.5, high=107.0, low=106.0),
            _bar(59, close=109.0, high=109.5, low=108.5),
        ]
    )
    return bars


def _short_pda_reject_bars() -> list[dict]:
    bars = _short_gate_pass_m5_bars()
    bars[-1] = _bar(59, close=102.0, high=102.5, low=101.5)
    return _m1_bars_from_m5(bars)


def _flat_range_prefix(*, length: int = 56) -> list[dict]:
    return [
        _bar(idx, open_=105.0, close=105.0, high=110.0, low=100.0)
        for idx in range(length)
    ]


def _quick_momentum_long_bars() -> list[dict]:
    closes = []
    for idx in range(55):
        closes.append(2000.0 + math.sin(idx / 7.0) * 0.2)
    base = closes[-1]
    closes.extend(
        [
            base - 0.5,
            base - 0.575,
            base - 0.425,
            base - 0.275,
            base + 0.2,
        ]
    )
    m5_bars = [_bar(idx, close=close) for idx, close in enumerate(closes)]
    m5_bars[-1] = _bar(len(closes) - 1, open_=closes[-2], close=closes[-1])
    return _m1_bars_from_m5(m5_bars)


def _atr_impulse_long_bars() -> list[dict]:
    closes = [2000.0 + (0.4 if idx % 2 else -0.4) for idx in range(59)]
    bars = []
    final_close = closes[-1] + 1.0
    for idx, close in enumerate(closes):
        if idx == len(closes) - 1:
            bars.append(_bar(idx, close=close, high=close + 0.1))
        else:
            bars.append(_bar(idx, close=close))
    bars.append(
        _bar(
            len(closes),
            open_=closes[-1],
            close=final_close,
            high=final_close + 0.1,
            low=final_close - 0.1,
        )
    )
    return _m1_bars_from_m5(bars)


def _backtest_config(
    *,
    risk_per_trade_fraction: float = 0.001,
    max_positions_per_symbol: int = 2,
    min_seconds_between_trades: int = 0,
) -> BacktestConfig:
    return BacktestConfig(
        fill_config=FillConfig(spread_points=0.0, slippage_points=0.0),
        min_seconds_between_trades=min_seconds_between_trades,
        sizer=PositionSizer(
            risk_per_trade_fraction=risk_per_trade_fraction,
            daily_loss_cap_fraction=0.01,
            max_lot_per_trade=0.10,
            max_positions_per_symbol=max_positions_per_symbol,
        ),
    )


def _signal_idx(m5_idx: int = 59) -> int:
    return int(m5_idx) * 5 + 4


def _m1_bars_from_m5(m5_bars: list[dict]) -> list[dict]:
    bars: list[dict] = []
    for m5_bar in m5_bars:
        start = int(m5_bar["time"])
        neutral = float(m5_bar["open"])
        for minute in range(5):
            is_close_minute = minute == 4
            close = float(m5_bar["close"]) if is_close_minute else neutral
            high = float(m5_bar["high"]) if is_close_minute else max(neutral, close)
            low = float(m5_bar["low"]) if is_close_minute else min(neutral, close)
            bars.append(
                {
                    "time": start + minute * 60,
                    "open": neutral,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": 1.0,
                }
            )
    return bars


def _m1_bar(
    idx: int,
    *,
    close: float,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
) -> dict:
    open_value = close if open_ is None else open_
    return {
        "time": 1_700_000_000 + idx * 60,
        "open": open_value,
        "high": close + 0.5 if high is None else high,
        "low": close - 0.5 if low is None else low,
        "close": close,
        "volume": 1.0,
    }


def _bar(
    idx: int,
    *,
    close: float,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
) -> dict:
    open_value = close if open_ is None else open_
    return {
        "time": 1_700_000_100 + idx * 300,
        "open": open_value,
        "high": close + 0.5 if high is None else high,
        "low": close - 0.5 if low is None else low,
        "close": close,
        "volume": 1.0,
    }
