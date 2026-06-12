from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from finrobot.backtest.engine import BacktestConfig
from finrobot.backtest.fills import FillConfig
from finrobot.backtest.position import PositionSizer
from finrobot.backtest.parity_replay import (
    ParityReplayConfig,
    load_acked_decisions,
    run_parity_replay,
)
from finrobot.backtest.strategies.base import Signal, Strategy
from finrobot.backtest.strategies.stub_replay import StubReplayStrategy


ROOT = Path(__file__).resolve().parents[1]


def test_load_acked_decisions_reads_auto_filled_rows(tmp_path: Path):
    acks = _acks_file(
        tmp_path,
        [
            "id,time,status,message,symbol,side,volume,price",
            "1,2026-05-01 10:00:00,AUTO_FILLED,filled,XAUUSD,BUY,0.10,100.0",
        ],
    )

    decisions = load_acked_decisions(
        acks,
        from_date="2026-05-01",
        to_date="2026-05-01",
        symbol="XAUUSD",
    )

    assert decisions == [
        {
            "bar_idx": None,
            "action": "BUY",
            "side": "BUY",
            "volume": 0.10,
            "price": 100.0,
            "source_time": "2026-05-01 10:00:00",
            "source_status": "AUTO_FILLED",
            "source_message": "filled",
            "source_id": "1",
        }
    ]


def test_load_acked_decisions_filters_by_date_range(tmp_path: Path):
    acks = _acks_file(
        tmp_path,
        [
            "id,time,status,message,symbol,side,volume,price",
            "1,2026-05-01 10:00:00,AUTO_FILLED,old,XAUUSD,BUY,0.10,100.0",
            "2,2026-05-02 10:00:00,AUTO_FILLED,kept,XAUUSD,SELL,0.20,101.0",
        ],
    )

    decisions = load_acked_decisions(
        acks,
        from_date="2026-05-02",
        to_date="2026-05-02",
        symbol="XAUUSD",
    )

    assert len(decisions) == 1
    assert decisions[0]["source_id"] == "2"
    assert decisions[0]["action"] == "SELL"


def test_load_acked_decisions_keeps_auto_rejected_rows(tmp_path: Path):
    acks = _acks_file(
        tmp_path,
        [
            "id,time,status,message,symbol,side,volume,price",
            "1,2026-05-01 10:00:00,AUTO_REJECTED,smc_reject,XAUUSD,SELL,0.10,0",
        ],
    )

    decisions = load_acked_decisions(
        acks,
        from_date="2026-05-01",
        to_date="2026-05-01",
        symbol="XAUUSD",
    )

    assert decisions[0]["action"] == "REJECTED"
    assert decisions[0]["side"] == "SELL"
    assert decisions[0]["source_status"] == "AUTO_REJECTED"


def test_load_acked_decisions_handles_missing_files(tmp_path: Path):
    decisions = load_acked_decisions(
        tmp_path / "missing.csv",
        from_date="2026-05-01",
        to_date="2026-05-01",
        symbol="XAUUSD",
    )

    assert decisions == []


def test_load_acked_decisions_handles_malformed_rows(tmp_path: Path):
    acks = _acks_file(
        tmp_path,
        [
            "id,time,status,message,symbol,side,volume,price",
            "1,2026-05-01 10:00:00,AUTO_FILLED,bad,XAUUSD,BUY,not-a-lot,100.0",
            "2,2026-05-01 10:01:00,AUTO_FILLED,good,XAUUSD,SELL,0.10,100.0",
        ],
    )

    with pytest.warns(UserWarning):
        decisions = load_acked_decisions(
            acks,
            from_date="2026-05-01",
            to_date="2026-05-01",
            symbol="XAUUSD",
        )

    assert len(decisions) == 1
    assert decisions[0]["source_id"] == "2"


def test_load_acked_decisions_reads_headerless_ea_rows(tmp_path: Path):
    acks = _acks_file(
        tmp_path,
        [
            "42,2026-05-01 10:00:00,AUTO_FILLED,QuickMomentum,XAUUSD,BUY,0.01,4072.69",
        ],
    )

    decisions = load_acked_decisions(
        acks,
        from_date="2026-05-01",
        to_date="2026-05-01",
        symbol="XAUUSD",
    )

    assert len(decisions) == 1
    assert decisions[0]["source_id"] == "42"
    assert decisions[0]["action"] == "BUY"


def test_stub_replay_strategy_emits_hold_without_decision():
    strategy = StubReplayStrategy([])

    signal = strategy.on_bar(
        idx=0,
        bar=_bar(0),
        history=[],
        open_positions=[],
        equity=10000.0,
        day_closed_pnl=0.0,
    )

    assert signal.action == "HOLD"
    assert signal.strategy == "StubReplay"


def test_stub_replay_strategy_emits_matching_signal_at_decision_bar():
    strategy = StubReplayStrategy(
        [{"bar_idx": 2, "action": "SELL", "sl_distance": 5.0, "tp_distance": 10.0}]
    )

    signal = strategy.on_bar(
        idx=2,
        bar=_bar(2),
        history=[],
        open_positions=[],
        equity=10000.0,
        day_closed_pnl=0.0,
    )

    assert signal.action == "SELL"
    assert signal.sl_distance == 5.0
    assert signal.tp_distance == 10.0
    assert signal.comment == "replay idx=2"


def test_run_parity_replay_perfect_match_scenario():
    bars = [_bar(idx, close=100.0 + idx) for idx in range(4)]
    decisions = [
        {"bar_idx": 1, "action": "BUY", "side": "BUY", "volume": 0.10, "price": 101.0},
        {"bar_idx": 2, "action": "SELL", "side": "SELL", "volume": 0.20, "price": 102.0},
    ]

    report = run_parity_replay(
        bars=bars,
        decisions=decisions,
        config=_config(),
        backtest_config=_backtest_config(),
    )

    assert report.match_rate == 1.0
    assert report.n_matched == 2
    assert report.n_mismatched == 0
    assert report.mismatches == []


def test_run_parity_replay_partial_mismatch_scenario():
    bars = [_bar(idx, close=100.0 + idx) for idx in range(3)]
    decisions = [
        {"bar_idx": 1, "action": "BUY", "side": "BUY", "volume": 0.10, "price": 999.0},
    ]

    report = run_parity_replay(
        bars=bars,
        decisions=decisions,
        config=_config(fill_tolerance_points=1.0),
        backtest_config=_backtest_config(),
    )

    assert report.match_rate < 1.0
    assert report.n_mismatched == 1
    assert "fill price mismatch" in report.mismatches[0]["detail"]


def test_run_parity_replay_counts_auto_rejected_as_matched_when_no_trade():
    bars = [_bar(idx) for idx in range(3)]
    decisions = [
        {"bar_idx": 1, "action": "REJECTED", "side": "BUY", "volume": 0.10, "price": 0.0},
    ]

    report = run_parity_replay(
        bars=bars,
        decisions=decisions,
        config=_config(),
        backtest_config=_backtest_config(),
    )

    assert report.match_rate == 1.0
    assert report.n_rejected == 1
    assert report.n_rejected_matched == 1
    assert report.n_rejected_mismatched == 0


def test_run_parity_replay_records_acks_with_no_matching_bar(tmp_path: Path):
    bars = [_bar(idx) for idx in range(2)]
    acks = _acks_file(
        tmp_path,
        [
            "id,time,status,message,symbol,side,volume,price",
            "1,2026-05-01 12:00:00,AUTO_FILLED,out-of-range,XAUUSD,BUY,0.10,100.0",
        ],
    )
    decisions = load_acked_decisions(
        acks,
        from_date="2026-05-01",
        to_date="2026-05-01",
        symbol="XAUUSD",
        bars=bars,
        bar_match_window=1,
    )

    report = run_parity_replay(
        bars=bars,
        decisions=decisions,
        config=_config(),
        backtest_config=_backtest_config(),
    )

    assert report.n_unmatched == 1
    assert report.n_mismatched == 1
    assert report.mismatches[0]["source_time"] == "2026-05-01 12:00:00"


def test_run_parity_replay_is_deterministic_for_same_inputs():
    bars = [_bar(idx, close=100.0 + idx) for idx in range(4)]
    decisions = [
        {"bar_idx": 1, "action": "BUY", "side": "BUY", "volume": 0.10, "price": 101.0},
        {"bar_idx": 3, "action": "REJECTED", "side": "SELL", "volume": 0.10, "price": 0.0},
    ]
    config = _config()

    first = run_parity_replay(
        bars=bars,
        decisions=decisions,
        config=config,
        backtest_config=_backtest_config(),
    )
    second = run_parity_replay(
        bars=bars,
        decisions=decisions,
        config=config,
        backtest_config=_backtest_config(),
    )

    assert first == second


def test_run_parity_replay_uses_config_sizer_with_real_strategy():
    bars = [_bar(idx, close=100.0 + idx) for idx in range(3)]
    decisions = [
        {"bar_idx": 1, "action": "BUY", "side": "BUY", "volume": 0.50, "price": 101.0},
    ]

    report = run_parity_replay(
        bars=bars,
        decisions=decisions,
        config=_config(),
        backtest_config=BacktestConfig(
            fill_config=FillConfig(spread_points=0.0, slippage_points=0.0),
            sizer=PositionSizer(
                risk_per_trade_fraction=0.001,
                daily_loss_cap_fraction=0.01,
                max_lot_per_trade=1.0,
                max_positions_per_symbol=2,
            ),
        ),
        strategy=_OneShotAtBar(1),
        volume_sizer=None,
    )

    assert report.match_rate == 1.0
    assert report.n_matched == 1


def test_cli_smoke_writes_json_and_markdown(tmp_path: Path):
    bars_path = tmp_path / "bars.tsv"
    bars_path.write_text(
        "\n".join(
            [
                "2026-05-01 10:00:00\t100\t101\t99\t100\t1",
                "2026-05-01 10:01:00\t101\t102\t100\t101\t1",
            ]
        )
        + "\n"
    )
    acks_path = _acks_file(
        tmp_path,
        [
            "id,time,status,message,symbol,side,volume,price",
            "1,2026-05-01 10:00:00,AUTO_FILLED,filled,XAUUSD,BUY,0.10,101.0",
        ],
    )
    output_dir = tmp_path / "out"

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_parity.py",
            "--from",
            "2026-05-01",
            "--to",
            "2026-05-02",
            "--acks-path",
            str(acks_path),
            "--data-path",
            str(bars_path),
            "--output-dir",
            str(output_dir),
            "--run-id",
            "test",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert (output_dir / "test" / "test.json").exists()
    assert (output_dir / "test" / "test.md").exists()


class _OneShotAtBar(Strategy):
    name = "OneShotAtBar"

    def __init__(self, bar_idx: int):
        self.bar_idx = int(bar_idx)

    def on_bar(self, *, idx: int, **kwargs) -> Signal:
        if idx != self.bar_idx:
            return Signal(action="HOLD", strategy=self.name)
        return Signal(
            action="BUY",
            sl_distance=20.0,
            tp_distance=40.0,
            strategy=self.name,
        )


def _config(
    *,
    fill_tolerance_points: float = 1.0,
    run_id: str = "test-run",
) -> ParityReplayConfig:
    return ParityReplayConfig(
        from_date="2026-05-01",
        to_date="2026-05-02",
        fill_tolerance_points=fill_tolerance_points,
        bar_match_window=1,
        run_id=run_id,
    )


def _backtest_config() -> BacktestConfig:
    return BacktestConfig(fill_config=FillConfig(spread_points=0.0, slippage_points=0.0))


def _bar(idx: int, *, close: float = 100.0) -> dict:
    return {
        "time": f"2026-05-01 10:{idx:02d}:00",
        "open": close,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": 1.0,
    }


def _acks_file(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "acks.csv"
    path.write_text("\n".join(lines) + "\n")
    return path
