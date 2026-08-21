from __future__ import annotations

from typing import Any

import pytest

from aiquanttrader.paper.models import (
    PaperFeedBlockReason,
    PaperFeedFreshness,
    PaperL2DepthState,
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
        ({"last_bbo_wall_ns": None}, PaperFeedBlockReason.BBO_MISSING),
        (
            {"last_bbo_wall_ns": _FUTURE_NS},
            PaperFeedBlockReason.BBO_CLOCK_REGRESSION,
        ),
        ({"last_bbo_wall_ns": _STALE_NS}, PaperFeedBlockReason.BBO_STALE),
    ),
)
def test_feed_freshness_reports_the_first_exact_executable_feed_block(
    updates: dict[str, Any], reason: PaperFeedBlockReason
) -> None:
    inputs: dict[str, Any] = {
        "checked_ts_ns": _NOW_NS,
        "stale_after_ms": 1_500,
        "depth_stale_after_ms": 2_000,
        "socket_connected": True,
        "last_public_frame_wall_ns": _FRESH_NS,
        "last_asset_context_wall_ns": _FRESH_NS,
        "last_bbo_wall_ns": _FRESH_NS,
        "last_l2_depth_wall_ns": _FRESH_NS,
    }
    inputs.update(updates)

    freshness = PaperFeedFreshness.from_observations(**inputs)

    assert not freshness.ready
    assert freshness.blocking_reason is reason


def test_feed_freshness_accepts_current_bbo_and_reports_depth_independently() -> None:
    freshness = PaperFeedFreshness.from_observations(
        checked_ts_ns=_NOW_NS,
        stale_after_ms=1_500,
        depth_stale_after_ms=1_000,
        socket_connected=True,
        last_public_frame_wall_ns=_FRESH_NS,
        last_asset_context_wall_ns=_FRESH_NS,
        last_bbo_wall_ns=_FRESH_NS,
        last_l2_depth_wall_ns=_STALE_NS,
    )

    assert freshness.ready
    assert freshness.blocking_reason is PaperFeedBlockReason.NONE
    assert freshness.public_frame_age_ms == 500
    assert freshness.asset_context_age_ms == 500
    assert freshness.bbo_age_ms == 500
    assert freshness.l2_depth_age_ms == 2_000
    assert freshness.bbo_fresh
    assert not freshness.l2_depth_fresh
    assert freshness.l2_depth_state is PaperL2DepthState.STALE


@pytest.mark.parametrize(
    ("depth_wall_ns", "state"),
    (
        (None, PaperL2DepthState.MISSING),
        (_FUTURE_NS, PaperL2DepthState.CLOCK_REGRESSION),
        (_STALE_NS, PaperL2DepthState.STALE),
        (_FRESH_NS, PaperL2DepthState.FRESH),
    ),
)
def test_feed_freshness_classifies_l2_depth_without_changing_readiness(
    depth_wall_ns: int | None, state: PaperL2DepthState
) -> None:
    freshness = PaperFeedFreshness.from_observations(
        checked_ts_ns=_NOW_NS,
        stale_after_ms=1_500,
        depth_stale_after_ms=1_500,
        socket_connected=True,
        last_public_frame_wall_ns=_FRESH_NS,
        last_asset_context_wall_ns=_FRESH_NS,
        last_bbo_wall_ns=_FRESH_NS,
        last_l2_depth_wall_ns=depth_wall_ns,
    )

    assert freshness.ready
    assert freshness.l2_depth_state is state


def test_feed_freshness_rejects_tampered_verdicts_and_thresholds() -> None:
    common: dict[str, Any] = {
        "checked_ts_ns": _NOW_NS,
        "stale_after_ms": 1_500,
        "depth_stale_after_ms": 2_000,
        "socket_connected": True,
        "last_public_frame_wall_ns": _FRESH_NS,
        "last_asset_context_wall_ns": _FRESH_NS,
        "last_bbo_wall_ns": _FRESH_NS,
        "last_l2_depth_wall_ns": _FRESH_NS,
    }
    with pytest.raises(ValueError, match="thresholds must be positive"):
        PaperFeedFreshness.from_observations(**{**common, "depth_stale_after_ms": 0})

    valid = PaperFeedFreshness.from_observations(**common).model_dump(mode="json")
    with pytest.raises(ValueError, match="component freshness"):
        PaperFeedFreshness.model_validate({**valid, "bbo_fresh": False})
    with pytest.raises(ValueError, match="L2 depth state"):
        PaperFeedFreshness.model_validate({**valid, "l2_depth_state": PaperL2DepthState.STALE})
    with pytest.raises(ValueError, match="blocking reason"):
        PaperFeedFreshness.model_validate(
            {**valid, "blocking_reason": PaperFeedBlockReason.BBO_STALE}
        )
