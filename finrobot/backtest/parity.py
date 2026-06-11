"""Decision parity harness for later EA-equivalent strategy ports."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from finrobot.backtest.engine import BacktestResult

DEFAULT_BAR_WINDOW = 1


@dataclass(frozen=True)
class ParityReport:
    """Comparison summary between expected and backtested decisions."""

    n_decisions: int
    n_matched: int
    n_mismatched: int
    match_rate: float
    mismatches: list[dict]


def compare_decisions(
    backtest_result: BacktestResult,
    expected_decisions: list[dict],
) -> ParityReport:
    """Compare backtest trades with expected decisions within a small bar window."""

    trades = list(getattr(backtest_result, "trades", []))
    mismatches: list[dict] = []
    matched = 0
    for expected in expected_decisions:
        trade = _closest_trade(expected, trades)
        if trade is None:
            mismatches.append(_mismatch(expected, None, "no backtester trade within window"))
            continue
        detail = _decision_mismatch_detail(expected, trade)
        if detail:
            mismatches.append(_mismatch(expected, trade, detail))
            continue
        matched += 1

    n_decisions = len(expected_decisions)
    n_mismatched = len(mismatches)
    match_rate = matched / n_decisions if n_decisions else 0.0
    return ParityReport(
        n_decisions=n_decisions,
        n_matched=matched,
        n_mismatched=n_mismatched,
        match_rate=match_rate,
        mismatches=mismatches,
    )


def _closest_trade(expected: dict, trades: list[dict]) -> dict | None:
    expected_idx = _as_int(expected.get("bar_idx"))
    if expected_idx is None:
        return trades[0] if trades else None
    candidates: list[tuple[int, dict]] = []
    for trade in trades:
        trade_idx = _trade_bar_idx(trade)
        if trade_idx is None:
            continue
        distance = abs(trade_idx - expected_idx)
        if distance <= DEFAULT_BAR_WINDOW:
            candidates.append((distance, trade))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]


def _decision_mismatch_detail(expected: dict, trade: dict) -> str:
    expected_action = str(expected.get("action", "")).upper()
    expected_side = str(expected.get("side", "")).upper()
    got_action = str(trade.get("action") or trade.get("side") or "").upper()
    got_side = str(trade.get("side", "")).upper()
    if expected_side and expected_side != got_side:
        return f"side mismatch: expected {expected_side}, got {got_side}"
    if expected_action and not _action_matches(expected_action, got_action, got_side):
        return f"action mismatch: expected {expected_action}, got {got_action or got_side}"
    expected_volume = _as_float(expected.get("volume"))
    got_volume = _as_float(trade.get("volume"))
    if expected_volume is not None and got_volume is not None:
        if not math.isclose(expected_volume, got_volume, rel_tol=1e-6, abs_tol=1e-6):
            return f"volume mismatch: expected {expected_volume}, got {got_volume}"
    return ""


def _action_matches(expected_action: str, got_action: str, got_side: str) -> bool:
    if expected_action == got_action:
        return True
    if expected_action == "OPEN" and got_side in {"BUY", "SELL"}:
        return True
    return expected_action in {"BUY", "SELL"} and expected_action == got_side


def _mismatch(expected: dict, trade: dict | None, detail: str) -> dict:
    return {
        "bar_idx": expected.get("bar_idx"),
        "expected_action": expected.get("action"),
        "expected_side": expected.get("side"),
        "expected_volume": expected.get("volume"),
        "got_action": None if trade is None else trade.get("action", trade.get("side")),
        "got_side": None if trade is None else trade.get("side"),
        "got_volume": None if trade is None else trade.get("volume"),
        "detail": detail,
    }


def _trade_bar_idx(trade: dict) -> int | None:
    return _as_int(trade.get("bar_idx", trade.get("entry_bar_idx")))


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
