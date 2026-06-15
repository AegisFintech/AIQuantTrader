from __future__ import annotations

import pytest

from finrobot.backtest.parity_replay import (
    ParityReplayConfig,
    load_acked_decisions,
    run_parity_replay,
)
from scripts.runtime_paths import common_dir
from tests._xau_parity_support import (
    XAU_SYMBOL,
    build_xau_backtest_config,
    build_xau_strategy,
    load_xau_bars,
)


FROM = "2026-06-11"
TO = "2026-06-11"
BAR_MATCH_WINDOW = 2


def test_xau_parity_live():
    """Full XAU parity on live acks (closes #22)."""

    bars = load_xau_bars(FROM, TO)
    if not bars:
        pytest.xfail(
            "Live June 11+ non-null XAU OHLC bars are not yet exported; "
            "see docs/M2.3D_PARITY_REPORT.md"
        )

    directory = common_dir()
    if directory is None:
        pytest.xfail("MT5 Common Files directory is unavailable")

    decisions = load_acked_decisions(
        directory / "finrobot_acks.csv",
        from_date=FROM,
        to_date=TO,
        symbol=XAU_SYMBOL,
        bars=bars,
        bar_match_window=BAR_MATCH_WINDOW,
    )
    overlap_decisions = [
        decision for decision in decisions if decision.get("bar_idx") is not None
    ]
    filled_overlap = [
        decision
        for decision in overlap_decisions
        if decision.get("action") in {"BUY", "SELL"}
    ]
    if len(filled_overlap) < 2:
        pytest.xfail(
            f"Need at least 2 live filled acks with matching bars; "
            f"got {len(filled_overlap)}"
        )
    signal_decisions = [_without_volume(decision) for decision in overlap_decisions]

    report = run_parity_replay(
        bars=bars,
        decisions=signal_decisions,
        config=ParityReplayConfig(
            from_date=FROM,
            to_date=TO,
            symbol=XAU_SYMBOL,
            fill_tolerance_points=10.0,
            bar_match_window=BAR_MATCH_WINDOW,
            run_id="m23d-xau-parity",
        ),
        # Issue #22 is signal parity. Live exits are not replayed here, so the
        # simulator position cap would otherwise block later valid live acks.
        backtest_config=build_xau_backtest_config(max_positions_per_symbol=99),
        strategy=build_xau_strategy(),
        volume_sizer=None,
    )

    print(
        "live XAU parity: "
        f"{report.n_matched}/{report.n_decisions} matched "
        f"({report.match_rate:.2%})"
    )
    assert report.match_rate >= 0.95, (
        f"XAU parity {report.match_rate:.2%} below 95% target. "
        f"Mismatches: {report.mismatches[:3]}"
    )


def _without_volume(decision: dict) -> dict:
    return {key: value for key, value in decision.items() if key != "volume"}
