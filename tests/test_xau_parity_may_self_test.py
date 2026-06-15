from __future__ import annotations

import pytest

from finrobot.backtest import Backtester
from finrobot.backtest.parity_replay import ParityReplayConfig, run_parity_replay
from tests._xau_parity_support import (
    XAU_SYMBOL,
    build_xau_backtest_config,
    build_xau_strategy,
    decisions_from_trades,
    load_xau_bars,
)


def test_xau_parity_may_self_test():
    """Real >=95% match on May 2026 acks requires the EA to export the
    June 11+ bars; this self-test verifies the parity harness on real M1 data.
    """

    bars = load_xau_bars("2026-05-01", "2026-05-01")
    if not bars:
        pytest.skip("May 2026 XAU bars are unavailable in data/finrobot.duckdb")

    config = build_xau_backtest_config()
    source_result = Backtester(config).run(
        strategy=build_xau_strategy(),
        bars=bars,
    )
    decisions = decisions_from_trades(source_result.trades)
    assert decisions, "self-test needs at least one synthetic ack decision"

    report = run_parity_replay(
        bars=bars,
        decisions=decisions,
        config=ParityReplayConfig(
            from_date="2026-05-01",
            to_date="2026-05-01",
            symbol=XAU_SYMBOL,
            fill_tolerance_points=0.0,
            bar_match_window=0,
            run_id="m23d-xau-may-self-test",
        ),
        backtest_config=config,
        strategy=build_xau_strategy(),
        volume_sizer=None,
    )

    print(
        "May XAU self-test parity: "
        f"{report.n_matched}/{report.n_decisions} matched "
        f"({report.match_rate:.2%})"
    )
    assert report.match_rate == 1.0
    assert report.n_matched == len(decisions)
