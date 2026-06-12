"""BTC gate helpers for MT5 bridge parity backtests."""

from __future__ import annotations


def btc_cost_filter_rejects(
    spread: float | None,
    atr: float | None,
    tp_distance: float | None,
) -> tuple[bool, str]:
    """Return whether the BTC cost filter rejects a candidate trade.

    This is a deliberate M2.3d placeholder. The live EA's
    ``BtcCostFilterReject`` implementation is separate from the SMC gate path
    and depends on spread feed semantics not yet ported into the Python
    backtester; M2.4+ can replace this no-op with the real filter.
    """

    return False, ""
