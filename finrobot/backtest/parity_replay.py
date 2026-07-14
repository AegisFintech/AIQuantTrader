"""Replay live EA acknowledgement decisions through the backtester."""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import warnings
from dataclasses import dataclass, replace
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from finrobot.backtest.engine import Backtester, BacktestConfig
from finrobot.backtest.parity import ParityReport, compare_decisions
from finrobot.backtest.strategies.base import Strategy
from finrobot.backtest.strategies.stub_replay import StubReplayStrategy

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from mt5_trade_report import read_csv  # noqa: E402


ACK_FIELDS = ("id", "time", "status", "message", "symbol", "side", "volume", "price")
FILLED_ACTIONS = {"BUY", "SELL"}
DECISION_STATUSES = {"AUTO_FILLED", "AUTO_REJECTED"}
DEFAULT_REPLAY_SL_DISTANCE = 1.0


@dataclass(frozen=True)
class ParityReplayConfig:
    """Configuration for one EA acknowledgement parity replay."""

    from_date: str
    to_date: str
    symbol: str = "XAUUSD"
    fill_tolerance_points: float = 1.0
    bar_match_window: int = 1
    run_id: str = ""


def load_acked_decisions(
    acks_csv_path: Path,
    *,
    from_date: str,
    to_date: str,
    symbol: str,
    bars: list[dict] | None = None,
    bar_match_window: int = 1,
    timezone_name: str | None = None,
) -> list[dict]:
    """Read EA acks and return auto decisions for ``symbol`` in the date range.

    ``AUTO_FILLED`` rows become BUY or SELL decisions. ``AUTO_REJECTED`` rows
    are kept as REJECTED decisions. When ``bars`` are supplied, each decision
    is mapped to the closest M1 bar within ``bar_match_window`` minutes;
    otherwise ``bar_idx`` is left as ``None`` for later resolution.
    """

    path = Path(acks_csv_path)
    if not path.exists() or not path.stat().st_size:
        return []

    start, end = _date_bounds(from_date, to_date)
    target_symbol = symbol.upper()
    rows = _read_ack_rows(path)
    decisions: list[dict] = []
    for row in rows:
        decision = _ack_row_to_decision(
            row,
            start=start,
            end=end,
            symbol=target_symbol,
            timezone_name=timezone_name,
        )
        if decision is not None:
            decisions.append(decision)

    if bars is not None:
        decisions = _attach_bar_indices(
            decisions,
            bars=bars,
            bar_match_window=bar_match_window,
        )
    return decisions


def run_parity_replay(
    *,
    bars: list[dict],
    decisions: list[dict],
    config: ParityReplayConfig,
    backtest_config: BacktestConfig | None = None,
    strategy: Strategy | None = None,
    volume_sizer: Any | None = None,
) -> ParityReport:
    """Replay audited decisions and compare simulated trades with live EA acks."""

    resolved = _attach_bar_indices(
        [_copy_decision(decision) for decision in decisions],
        bars=bars,
        bar_match_window=config.bar_match_window,
    )
    run_id = config.run_id or _stable_run_id(config=config, bars=bars, decisions=resolved)

    filled_decisions = [d for d in resolved if d.get("action") in FILLED_ACTIONS]
    rejected_decisions = [d for d in resolved if d.get("action") == "REJECTED"]
    matched_bar_filled = [d for d in filled_decisions if d.get("bar_idx") is not None]
    matched_bar_rejected = [d for d in rejected_decisions if d.get("bar_idx") is not None]
    unmatched = [d for d in resolved if d.get("bar_idx") is None]

    replay_decisions = _prepare_replay_decisions(
        [d for d in resolved if d.get("bar_idx") is not None]
    )
    if volume_sizer is not None:
        replay_sizer = volume_sizer
    elif strategy is None:
        replay_sizer = _ReplayVolumeSizer(replay_decisions)
    else:
        replay_sizer = None
    replay_strategy = strategy or StubReplayStrategy(replay_decisions)
    result = Backtester(
        _replay_backtest_config(
            backtest_config,
            config,
            replay_decisions,
            volume_sizer=replay_sizer,
        )
    ).run(
        strategy=replay_strategy,
        bars=bars,
    )

    filled_report = compare_decisions(
        result,
        matched_bar_filled,
        bar_window=config.bar_match_window,
        fill_tolerance_points=config.fill_tolerance_points,
    )
    rejected_matched, rejected_mismatches = _compare_rejected_decisions(
        result.trades,
        matched_bar_rejected,
        bar_window=config.bar_match_window,
    )
    unmatched_mismatches = [_unmatched_mismatch(decision) for decision in unmatched]
    mismatches = (
        list(filled_report.mismatches)
        + rejected_mismatches
        + unmatched_mismatches
    )

    n_decisions = len(resolved)
    n_matched = filled_report.n_matched + rejected_matched
    n_mismatched = len(mismatches)
    match_rate = n_matched / n_decisions if n_decisions else 0.0
    n_filled_mismatched = sum(
        1 for mismatch in mismatches if mismatch.get("expected_action") in FILLED_ACTIONS
    )
    n_rejected_mismatched = sum(
        1 for mismatch in mismatches if mismatch.get("expected_action") == "REJECTED"
    )

    return ParityReport(
        n_decisions=n_decisions,
        n_matched=n_matched,
        n_mismatched=n_mismatched,
        match_rate=match_rate,
        mismatches=mismatches,
        run_id=run_id,
        n_filled=len(filled_decisions),
        n_filled_matched=filled_report.n_matched,
        n_filled_mismatched=n_filled_mismatched,
        n_rejected=len(rejected_decisions),
        n_rejected_matched=rejected_matched,
        n_rejected_mismatched=n_rejected_mismatched,
        n_unmatched=len(unmatched),
        config={
            "from_date": config.from_date,
            "to_date": config.to_date,
            "symbol": config.symbol,
            "fill_tolerance_points": float(config.fill_tolerance_points),
            "bar_match_window": int(config.bar_match_window),
        },
        summary={
            "bars": len(bars),
            "backtest_trades": len(result.trades),
            "backtest_rejected_signals": result.rejected_signals,
            "start_time": result.start_time,
            "end_time": result.end_time,
            "final_equity": result.final_equity,
        },
    )


class _ReplayVolumeSizer:
    """Return source EA volumes for replayed fill attempts."""

    def __init__(self, decisions: list[dict]):
        self._volumes = [
            float(decision.get("volume") or 0.0)
            for decision in decisions
            if decision.get("action") in FILLED_ACTIONS
        ]
        self._cursor = 0

    def size(
        self,
        *,
        symbol: str,
        equity: float,
        sl_distance: float,
        open_positions: list[Any],
        today_closed_pnl: float,
        smc_score: int = 0,
    ) -> float:
        """Return the next audited volume, ignoring risk caps for replay."""

        if self._cursor >= len(self._volumes):
            return 0.0
        volume = self._volumes[self._cursor]
        self._cursor += 1
        return volume


def _read_ack_rows(path: Path) -> list[dict]:
    rows = read_csv(path)
    if rows and _has_ack_header(rows[0]):
        return [dict(row) for row in rows]
    return _read_headerless_ack_rows(path)


def _has_ack_header(row: dict) -> bool:
    keys = {str(key).strip().lower() for key in row}
    return {"time", "status", "symbol"}.issubset(keys)


def _read_headerless_ack_rows(path: Path) -> list[dict]:
    parsed: list[dict] = []
    with path.open(errors="replace", newline="") as fh:
        reader = csv.reader(fh)
        for line_no, row in enumerate(reader, start=1):
            if not row or all(cell.strip() == "" for cell in row):
                continue
            if line_no == 1 and _is_ack_header_row(row):
                continue
            normalized = _headerless_row_to_dict(row)
            if normalized is None:
                warnings.warn(
                    f"{path}:{line_no}: malformed ack row skipped",
                    UserWarning,
                    stacklevel=2,
                )
                continue
            parsed.append(normalized)
    return parsed


def _is_ack_header_row(row: list[str]) -> bool:
    return [cell.strip().lower() for cell in row[: len(ACK_FIELDS)]] == list(ACK_FIELDS)


def _headerless_row_to_dict(row: list[str]) -> dict | None:
    if len(row) < len(ACK_FIELDS):
        return None
    if len(row) == len(ACK_FIELDS):
        values = row
    else:
        values = [*row[:3], ",".join(row[3:-4]), *row[-4:]]
    return {field: value.strip() for field, value in zip(ACK_FIELDS, values)}


def _ack_row_to_decision(
    row: dict,
    *,
    start: datetime,
    end: datetime,
    symbol: str,
    timezone_name: str | None,
) -> dict | None:
    normalized = {str(key).strip().lower(): value for key, value in row.items()}
    status = str(normalized.get("status", "")).strip().upper()
    if status not in DECISION_STATUSES:
        return None
    row_symbol = str(normalized.get("symbol", "")).strip().upper()
    if row_symbol != symbol:
        return None

    source_time = str(normalized.get("time", "")).strip()
    source_dt = _parse_datetime(source_time)
    if source_dt is None:
        warnings.warn(f"ack row has invalid time and was skipped: {row}", UserWarning)
        return None
    if not (start <= source_dt <= end):
        return None

    side = str(normalized.get("side", "")).strip().upper()
    volume = _float_or_none(normalized.get("volume"))
    price = _float_or_none(normalized.get("price"))
    if status == "AUTO_FILLED" and side not in FILLED_ACTIONS:
        warnings.warn(f"AUTO_FILLED ack has invalid side and was skipped: {row}", UserWarning)
        return None
    if volume is None:
        warnings.warn(f"ack row has invalid volume and was skipped: {row}", UserWarning)
        return None

    decision = {
        "bar_idx": None,
        "action": side if status == "AUTO_FILLED" else "REJECTED",
        "side": side,
        "volume": volume,
        "price": price,
        "source_time": source_time,
        "source_status": status,
        "source_message": str(normalized.get("message", "")).strip(),
        "source_id": str(normalized.get("id", "")).strip(),
    }
    if timezone_name:
        zoned = _parse_datetime(source_time, timezone_name=timezone_name)
        if zoned is not None:
            decision["source_epoch"] = int(zoned.timestamp())
    return decision


def _date_bounds(from_date: str, to_date: str) -> tuple[datetime, datetime]:
    start = datetime.combine(date.fromisoformat(from_date), time.min)
    end = datetime.combine(date.fromisoformat(to_date), time.max)
    if end < start:
        raise ValueError("to_date must be on or after from_date")
    return start, end


def _attach_bar_indices(
    decisions: list[dict],
    *,
    bars: list[dict],
    bar_match_window: int,
) -> list[dict]:
    if not decisions:
        return []
    bar_times = [_bar_epoch(bar) for bar in bars]
    for decision in decisions:
        if decision.get("bar_idx") is not None:
            continue
        source_epoch = _source_epoch(decision)
        if source_epoch is None:
            decision["bar_idx"] = None
            decision["bar_match_detail"] = "missing source_time"
            continue
        match = _closest_bar_idx(
            source_epoch,
            bar_times=bar_times,
            bar_match_window=bar_match_window,
        )
        decision["bar_idx"] = match
        if match is None:
            decision["bar_match_detail"] = "no bar within match window"
    return decisions


def _closest_bar_idx(
    source_epoch: int,
    *,
    bar_times: list[int],
    bar_match_window: int,
) -> int | None:
    if not bar_times:
        return None
    max_seconds = max(0, int(bar_match_window)) * 60
    candidates = [
        (abs(bar_time - source_epoch), idx)
        for idx, bar_time in enumerate(bar_times)
        if abs(bar_time - source_epoch) <= max_seconds
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][1]


def _prepare_replay_decisions(decisions: list[dict]) -> list[dict]:
    replay_decisions: list[dict] = []
    for decision in decisions:
        replay = _copy_decision(decision)
        if replay.get("action") in FILLED_ACTIONS and replay.get("sl_distance") is None:
            replay["sl_distance"] = DEFAULT_REPLAY_SL_DISTANCE
        replay_decisions.append(replay)
    replay_decisions.sort(key=lambda item: int(item.get("bar_idx") or 0))
    return replay_decisions


def _replay_backtest_config(
    backtest_config: BacktestConfig | None,
    config: ParityReplayConfig,
    replay_decisions: list[dict],
    *,
    volume_sizer: Any | None = None,
) -> BacktestConfig:
    base = backtest_config or BacktestConfig(symbol=config.symbol)
    if volume_sizer is None:
        return replace(base, symbol=config.symbol)
    return replace(
        base,
        symbol=config.symbol,
        sizer=volume_sizer,
    )


def _compare_rejected_decisions(
    trades: list[dict],
    decisions: list[dict],
    *,
    bar_window: int,
) -> tuple[int, list[dict]]:
    matched = 0
    mismatches: list[dict] = []
    for decision in decisions:
        trade = _trade_within_window(trades, decision, bar_window=0)
        if trade is None:
            matched += 1
            continue
        mismatches.append(
            {
                "bar_idx": decision.get("bar_idx"),
                "expected_action": "REJECTED",
                "expected_side": decision.get("side"),
                "expected_volume": decision.get("volume"),
                "expected_price": decision.get("price"),
                "got_action": trade.get("action", trade.get("side")),
                "got_side": trade.get("side"),
                "got_volume": trade.get("volume"),
                "got_price": trade.get("entry_price"),
                "detail": "EA rejected but backtester produced a trade",
            }
        )
    return matched, mismatches


def _trade_within_window(
    trades: list[dict],
    decision: dict,
    *,
    bar_window: int,
) -> dict | None:
    expected_idx = _int_or_none(decision.get("bar_idx"))
    if expected_idx is None:
        return None
    for trade in trades:
        trade_idx = _int_or_none(trade.get("bar_idx", trade.get("entry_bar_idx")))
        if trade_idx is not None and abs(trade_idx - expected_idx) <= int(bar_window):
            return trade
    return None


def _unmatched_mismatch(decision: dict) -> dict:
    return {
        "bar_idx": None,
        "expected_action": decision.get("action"),
        "expected_side": decision.get("side"),
        "expected_volume": decision.get("volume"),
        "expected_price": decision.get("price"),
        "got_action": None,
        "got_side": None,
        "got_volume": None,
        "got_price": None,
        "detail": decision.get("bar_match_detail", "no matching bar"),
        "source_time": decision.get("source_time"),
        "source_status": decision.get("source_status"),
    }


def _stable_run_id(
    *,
    config: ParityReplayConfig,
    bars: list[dict],
    decisions: list[dict],
) -> str:
    payload = {
        "config": {
            "from_date": config.from_date,
            "to_date": config.to_date,
            "symbol": config.symbol,
            "fill_tolerance_points": config.fill_tolerance_points,
            "bar_match_window": config.bar_match_window,
        },
        "bars": {
            "n": len(bars),
            "start": _bar_epoch(bars[0]) if bars else None,
            "end": _bar_epoch(bars[-1]) if bars else None,
        },
        "decisions": decisions,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return f"parity-{digest[:12]}"


def _copy_decision(decision: dict) -> dict:
    return dict(decision)


def _source_epoch(decision: dict) -> int | None:
    explicit = _int_or_none(decision.get("source_epoch"))
    if explicit is not None:
        return explicit
    source_time = decision.get("source_time")
    if source_time is None:
        return None
    source_dt = _parse_datetime(str(source_time))
    if source_dt is None:
        return None
    return int(source_dt.timestamp())


def _bar_epoch(bar: dict) -> int:
    value = bar.get("time", bar.get("ts", bar.get("ts_server")))
    if value is None or value == "":
        raise ValueError("bar time is required")
    try:
        return int(float(value))
    except (TypeError, ValueError):
        pass
    parsed = _parse_datetime(str(value))
    if parsed is None:
        raise ValueError(f"unsupported bar time: {value!r}")
    return int(parsed.timestamp())


def _parse_datetime(value: str, *, timezone_name: str | None = None) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromtimestamp(float(text))
    except (TypeError, ValueError, OSError):
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y.%m.%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
            if timezone_name:
                parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
            return parsed
        except ValueError:
            continue
    return None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
