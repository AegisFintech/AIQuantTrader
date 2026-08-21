from __future__ import annotations

from typing import Any

import pytest

from aiquanttrader.paper.models import (
    PaperFeedBlockReason,
    PaperFeedFreshness,
)

_NOW_NS = 20_000_000_000
_FRESH_NS = _NOW_NS - 500_000_000
_STALE_NS = _NOW_NS - 2_000_000_000
_FUTURE_NS = _NOW_NS + 1_000_000


@pytest.mark.parametrize(
    ("updates", "reason"),
    (
        ({"socket_connected": False}, PaperFeedBlockReason.SOCKET_DISCONNECTED),
        ({"last_public_frame_wall_ns": None}, PaperFeedBlockReason.PUBLIC_FRAME_MISSING),
        (
            {"last_public_frame_wall_ns": _FUTURE_NS},
            PaperFeedBlockReason.PUBLIC_FRAME_CLOCK_REGRESSION,
        ),
        ({"last_public_frame_wall_ns": _STALE_NS}, PaperFeedBlockReason.PUBLIC_FRAME_STALE),
        ({"last_asset_context_wall_ns": None}, PaperFeedBlockReason.ASSET_CONTEXT_MISSING),
        (
            {"last_asset_context_wall_ns": _FUTURE_NS},
            PaperFeedBlockReason.ASSET_CONTEXT_CLOCK_REGRESSION,
        ),
        ({"last_asset_context_wall_ns": _STALE_NS}, PaperFeedBlockReason.ASSET_CONTEXT_STALE),
        ({"last_market_state_wall_ns": None}, PaperFeedBlockReason.MARKET_STATE_MISSING),
        (
            {"last_market_state_wall_ns": _FUTURE_NS},
            PaperFeedBlockReason.MARKET_STATE_CLOCK_REGRESSION,
        ),
        ({"last_market_state_wall_ns": _STALE_NS}, PaperFeedBlockReason.MARKET_STATE_STALE),
    ),
)
def test_feed_freshness_reports_the_first_exact_block(
    updates: dict[str, Any], reason: PaperFeedBlockReason
) -> None:
    inputs: dict[str, Any] = {
        "checked_ts_ns": _NOW_NS,
        "stale_after_ms": 1_500,
        "socket_connected": True,
        "last_public_frame_wall_ns": _FRESH_NS,
        "last_asset_context_wall_ns": _FRESH_NS,
        "last_market_state_wall_ns": _FRESH_NS,
    }
    inputs.update(updates)

    freshness = PaperFeedFreshness.from_observations(**inputs)

    assert not freshness.ready
    assert freshness.blocking_reason is reason


def test_feed_freshness_accepts_all_current_components() -> None:
    freshness = PaperFeedFreshness.from_observations(
        checked_ts_ns=_NOW_NS,
        stale_after_ms=1_500,
        socket_connected=True,
        last_public_frame_wall_ns=_FRESH_NS,
        last_asset_context_wall_ns=_FRESH_NS,
        last_market_state_wall_ns=_FRESH_NS,
    )

    assert freshness.ready
    assert freshness.blocking_reason is PaperFeedBlockReason.NONE
    assert freshness.public_frame_age_ms == 500
    assert freshness.asset_context_age_ms == 500
    assert freshness.market_state_age_ms == 500


def test_feed_freshness_rejects_tampered_verdicts_and_thresholds() -> None:
    with pytest.raises(ValueError, match="threshold must be positive"):
        PaperFeedFreshness.from_observations(
            checked_ts_ns=_NOW_NS,
            stale_after_ms=0,
            socket_connected=True,
            last_public_frame_wall_ns=_FRESH_NS,
            last_asset_context_wall_ns=_FRESH_NS,
            last_market_state_wall_ns=_FRESH_NS,
        )

    valid = PaperFeedFreshness.from_observations(
        checked_ts_ns=_NOW_NS,
        stale_after_ms=1_500,
        socket_connected=True,
        last_public_frame_wall_ns=_FRESH_NS,
        last_asset_context_wall_ns=_FRESH_NS,
        last_market_state_wall_ns=_FRESH_NS,
    ).model_dump(mode="json")
    with pytest.raises(ValueError, match="component freshness"):
        PaperFeedFreshness.model_validate({**valid, "market_state_fresh": False})
    with pytest.raises(ValueError, match="blocking reason"):
        PaperFeedFreshness.model_validate(
            {**valid, "blocking_reason": PaperFeedBlockReason.MARKET_STATE_STALE}
        )
