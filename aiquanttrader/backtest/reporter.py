"""Backtest reporting helpers for research promotion decisions."""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field, fields, is_dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aiquanttrader.backtest.engine import BacktestResult
from aiquanttrader.backtest.metrics import MetricsReport, compute_metrics


@dataclass(frozen=True)
class ReportMetadata:
    """Metadata that identifies the dataset, code, and strategy under review."""

    run_id: str
    experiment_id: str = ""
    git_sha: str = ""
    data_hash: str = ""
    from_date: str = ""
    to_date: str = ""
    symbol: str = ""
    strategy_name: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""


@dataclass(frozen=True)
class StrategyAttribution:
    """Per-strategy realized PnL attribution and standalone metrics."""

    strategy: str
    n_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    expectancy: float
    total_pnl: float
    max_drawdown: float
    max_drawdown_pct: float
    avg_holding_time_seconds: float
    share_of_pnl: float


@dataclass(frozen=True)
class DrawdownWindow:
    """One peak-to-trough drawdown segment from the equity curve."""

    peak_time: int
    trough_time: int
    recovery_time: int | None
    depth_abs: float
    depth_pct: float


@dataclass(frozen=True)
class Verdict:
    """Promotion verdict and markdown rationale."""

    status: str
    rationale: str


@dataclass(frozen=True)
class BacktestReport:
    """Complete report payload.

    ``walk_forward_stability`` is intentionally ``None`` in M2.4. M3 will
    replace this placeholder with purged walk-forward stability results.
    """

    metadata: ReportMetadata
    metrics: MetricsReport
    per_strategy: list[StrategyAttribution]
    equity_curve: list[tuple[int, float]]
    drawdown_windows: list[DrawdownWindow]
    walk_forward_stability: None
    verdict: Verdict


def generate_report(
    result: BacktestResult, *, metadata: ReportMetadata
) -> BacktestReport:
    """Build a structured report from a completed backtest result."""

    resolved_metadata = _resolve_metadata(result, metadata)
    metrics = compute_metrics(result)
    attribution = _per_strategy_attribution(result, metrics)
    return BacktestReport(
        metadata=resolved_metadata,
        metrics=metrics,
        per_strategy=attribution,
        equity_curve=list(getattr(result, "equity_curve", [])),
        drawdown_windows=drawdown_windows(list(getattr(result, "equity_curve", []))),
        walk_forward_stability=None,
        verdict=verdict_for(metrics, attribution),
    )


def verdict_for(
    metrics: MetricsReport, attribution: list[StrategyAttribution]
) -> Verdict:
    """Return the M2.4 promotion verdict for the supplied metrics."""

    dominant = _dominant_strategy(attribution) if len(attribution) > 1 else None
    dominant_ok = dominant is None or dominant.profit_factor >= 1.2
    pass_checks = [
        (
            "total_pnl > 0",
            metrics.total_pnl > 0,
            _format_number(metrics.total_pnl),
        ),
        (
            "profit_factor >= 1.5",
            metrics.profit_factor >= 1.5,
            _format_number(metrics.profit_factor),
        ),
        ("win_rate >= 0.45", metrics.win_rate >= 0.45, _format_pct(metrics.win_rate)),
        (
            "0 < max_drawdown_pct <= 0.20",
            0 < metrics.max_drawdown_pct <= 0.20,
            _format_pct(metrics.max_drawdown_pct),
        ),
    ]
    if dominant is not None:
        pass_checks.append(
            (
                f"dominant strategy profit_factor >= 1.2 ({dominant.strategy})",
                dominant_ok,
                _format_number(dominant.profit_factor),
            )
        )

    no_trade_flat = metrics.n_trades == 0 and metrics.total_pnl == 0
    fail_checks = [
        (
            "total_pnl < 0",
            metrics.total_pnl < 0,
            _format_number(metrics.total_pnl),
        ),
        (
            "profit_factor < 0.7",
            metrics.profit_factor < 0.7,
            _format_number(metrics.profit_factor),
        ),
        ("win_rate < 0.30", metrics.win_rate < 0.30, _format_pct(metrics.win_rate)),
        (
            "max_drawdown_pct > 0.35",
            metrics.max_drawdown_pct > 0.35,
            _format_pct(metrics.max_drawdown_pct),
        ),
    ]

    if all(passed for _, passed, _ in pass_checks):
        status = "pass"
        summary = "Summary: PASS because all promotion gates passed."
    elif not no_trade_flat and any(failed for _, failed, _ in fail_checks):
        status = "fail"
        summary = "Summary: FAIL because at least one hard rejection gate fired."
    else:
        status = "marginal"
        summary = "Summary: MARGINAL because the run avoided hard failure but missed promotion gates."

    lines = [summary, "", "Promotion gates:"]
    lines.extend(
        f"- {'PASS' if passed else 'FAIL'} `{rule}` ({value})"
        for rule, passed, value in pass_checks
    )
    lines.append("")
    lines.append("Hard rejection gates:")
    lines.extend(
        f"- {'FAIL' if failed else 'PASS'} `{rule}` ({value})"
        for rule, failed, value in fail_checks
    )
    if no_trade_flat:
        lines.append("")
        lines.append("- NOTE no closed trades and flat PnL are treated as marginal evidence.")
    return Verdict(status=status, rationale="\n".join(lines))


def write_json(report: BacktestReport, path: Path) -> None:
    """Write a structured JSON sidecar for ``report``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, cls=ReportJSONEncoder, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_markdown(report: BacktestReport, path: Path) -> None:
    """Write a markdown rendering of ``report``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: BacktestReport) -> str:
    """Render a human-readable markdown summary for ``report``."""

    metadata = report.metadata
    lines = [
        f"# Backtest Report: {_cell(metadata.run_id)}",
        "",
        "| Field | Value |",
        "|---|---:|",
    ]
    header_rows = [
        ("run_id", metadata.run_id),
        ("experiment_id", metadata.experiment_id or "TBD M3"),
        ("symbol", metadata.symbol),
        ("strategy", metadata.strategy_name),
        ("from_date", metadata.from_date),
        ("to_date", metadata.to_date),
        ("git_sha", metadata.git_sha),
        ("data_hash", metadata.data_hash),
        ("created_at", metadata.created_at),
    ]
    lines.extend(f"| {_cell(name)} | {_cell(value)} |" for name, value in header_rows)

    lines.extend(["", "## Configuration"])
    if metadata.params:
        lines.extend(["", "| Parameter | Value |", "|---|---:|"])
        for key in sorted(metadata.params):
            lines.append(f"| {_cell(key)} | {_cell(_param_value(metadata.params[key]))} |")
    else:
        lines.extend(["", "_No parameters supplied._"])

    lines.extend(["", "## Overall Metrics", "", "| Metric | Value |", "|---|---:|"])
    for item in fields(MetricsReport):
        value = getattr(report.metrics, item.name)
        lines.append(f"| {_cell(item.name)} | {_cell(_format_value(value))} |")

    lines.extend(["", "## Per-Strategy Attribution"])
    if report.per_strategy:
        lines.extend(
            [
                "",
                "| Strategy | Trades | Win Rate | Avg Win | Avg Loss | Profit Factor | Expectancy | Total PnL | Max DD | Max DD % | Avg Hold Sec | Share of PnL |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in report.per_strategy:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _cell(row.strategy),
                        str(row.n_trades),
                        _format_pct(row.win_rate),
                        _format_number(row.avg_win),
                        _format_number(row.avg_loss),
                        _format_number(row.profit_factor),
                        _format_number(row.expectancy),
                        _format_number(row.total_pnl),
                        _format_number(row.max_drawdown),
                        _format_pct(row.max_drawdown_pct),
                        _format_number(row.avg_holding_time_seconds),
                        _format_number(row.share_of_pnl),
                    ]
                )
                + " |"
            )
    else:
        lines.extend(["", "_No closed trades._"])

    lines.extend(["", "## Equity Curve Summary", ""])
    lines.extend(_equity_curve_summary(report))

    lines.extend(["", "## Drawdown Windows"])
    if report.drawdown_windows:
        lines.extend(
            [
                "",
                "| Rank | Peak Time | Trough Time | Recovery Time | Depth | Depth % |",
                "|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for index, window in enumerate(report.drawdown_windows, start=1):
            recovery = (
                str(window.recovery_time)
                if window.recovery_time is not None
                else "Unrecovered"
            )
            lines.append(
                f"| {index} | {window.peak_time} | {window.trough_time} | "
                f"{_cell(recovery)} | {_format_number(window.depth_abs)} | "
                f"{_format_pct(window.depth_pct)} |"
            )
    else:
        lines.extend(["", "_No drawdown windows._"])

    lines.extend(["", "## Walk-Forward Stability", "", "Pending M3."])
    lines.extend(["", "## Verdict", "", f"**Status:** {report.verdict.status}", ""])
    lines.append(report.verdict.rationale)
    lines.extend(
        [
            "",
            "---",
            f"Linked experiment: {metadata.experiment_id or 'TBD M3'} (TBD M3)",
            "",
        ]
    )
    return "\n".join(lines)


class ReportJSONEncoder(json.JSONEncoder):
    """JSON encoder for report dataclasses and tuple equity points."""

    def default(self, obj: Any) -> Any:
        if is_dataclass(obj) and not isinstance(obj, type):
            return _json_safe(asdict(obj))
        if isinstance(obj, tuple):
            return [_json_safe(item) for item in obj]
        if isinstance(obj, Path):
            return str(obj)
        return super().default(obj)


def drawdown_windows(
    equity_curve: list[tuple[int, float]], *, limit: int = 5
) -> list[DrawdownWindow]:
    """Return the deepest peak-to-trough drawdown windows in ``equity_curve``."""

    if len(equity_curve) < 2:
        return []

    normalized = [(int(time), float(equity)) for time, equity in equity_curve]
    peak_time, peak_value = normalized[0]
    in_drawdown = False
    trough_time = peak_time
    trough_value = peak_value
    windows: list[DrawdownWindow] = []

    for current_time, equity in normalized[1:]:
        if not in_drawdown:
            if equity >= peak_value:
                peak_time = current_time
                peak_value = equity
                continue
            in_drawdown = True
            trough_time = current_time
            trough_value = equity
            continue

        if equity < trough_value:
            trough_time = current_time
            trough_value = equity
        if equity >= peak_value:
            windows.append(
                _drawdown_window(
                    peak_time=peak_time,
                    peak_value=peak_value,
                    trough_time=trough_time,
                    trough_value=trough_value,
                    recovery_time=current_time,
                )
            )
            in_drawdown = False
            peak_time = current_time
            peak_value = equity
            trough_time = current_time
            trough_value = equity

    if in_drawdown:
        windows.append(
            _drawdown_window(
                peak_time=peak_time,
                peak_value=peak_value,
                trough_time=trough_time,
                trough_value=trough_value,
                recovery_time=None,
            )
        )

    return sorted(windows, key=lambda window: window.depth_abs, reverse=True)[:limit]


def _resolve_metadata(
    result: BacktestResult, metadata: ReportMetadata
) -> ReportMetadata:
    created_at = metadata.created_at or datetime.now(timezone.utc).isoformat()
    config = getattr(result, "config", None)
    symbol = metadata.symbol or str(getattr(config, "symbol", "") or "")
    strategy_name = metadata.strategy_name or str(getattr(result, "strategy_name", "") or "")
    return replace(
        metadata,
        created_at=created_at,
        symbol=symbol,
        strategy_name=strategy_name,
    )


def _per_strategy_attribution(
    result: BacktestResult, overall_metrics: MetricsReport
) -> list[StrategyAttribution]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for trade in getattr(result, "trades", []):
        strategy = "UNKNOWN"
        if isinstance(trade, dict):
            strategy = str(trade.get("strategy") or "UNKNOWN")
        grouped[strategy].append(trade)

    initial_equity = float(getattr(result, "initial_equity", 0.0))
    start_time = int(getattr(result, "start_time", 0) or 0)
    rows: list[StrategyAttribution] = []
    for strategy in sorted(grouped):
        trades = sorted(
            grouped[strategy],
            key=lambda trade: (
                _trade_time(trade, "exit_time", start_time),
                _trade_time(trade, "entry_time", start_time),
            ),
        )
        curve = _strategy_equity_curve(
            trades=trades,
            initial_equity=initial_equity,
            start_time=start_time,
        )
        final_equity = curve[-1][1] if curve else initial_equity
        synthetic = replace(
            result,
            trades=trades,
            equity_curve=curve,
            initial_equity=initial_equity,
            final_equity=final_equity,
            open_positions_at_end=[],
            rejected_signals=0,
        )
        metrics = compute_metrics(synthetic)
        share_of_pnl = (
            metrics.total_pnl / overall_metrics.total_pnl
            if overall_metrics.total_pnl != 0
            else 0.0
        )
        rows.append(
            StrategyAttribution(
                strategy=strategy,
                n_trades=metrics.n_trades,
                win_rate=metrics.win_rate,
                avg_win=metrics.avg_win,
                avg_loss=metrics.avg_loss,
                profit_factor=metrics.profit_factor,
                expectancy=metrics.expectancy,
                total_pnl=metrics.total_pnl,
                max_drawdown=metrics.max_drawdown,
                max_drawdown_pct=metrics.max_drawdown_pct,
                avg_holding_time_seconds=metrics.avg_holding_time_seconds,
                share_of_pnl=share_of_pnl,
            )
        )
    return rows


def _strategy_equity_curve(
    *, trades: list[dict], initial_equity: float, start_time: int
) -> list[tuple[int, float]]:
    if not trades:
        return []

    first_time = _trade_time(trades[0], "exit_time", start_time)
    curve: list[tuple[int, float]] = [(start_time or first_time, initial_equity)]
    cumulative_pnl = 0.0
    for trade in trades:
        cumulative_pnl += _trade_pnl(trade)
        curve.append(
            (
                _trade_time(trade, "exit_time", start_time),
                initial_equity + cumulative_pnl,
            )
        )
    return curve


def _trade_pnl(trade: Any) -> float:
    if isinstance(trade, dict):
        return float(trade.get("pnl", trade.get("profit", 0.0)) or 0.0)
    return float(getattr(trade, "pnl", getattr(trade, "current_pnl", 0.0)) or 0.0)


def _trade_time(trade: Any, key: str, fallback: int) -> int:
    if isinstance(trade, dict):
        value = trade.get(key)
        if value is not None:
            return int(float(value))
    return int(fallback)


def _drawdown_window(
    *,
    peak_time: int,
    peak_value: float,
    trough_time: int,
    trough_value: float,
    recovery_time: int | None,
) -> DrawdownWindow:
    depth_abs = max(0.0, float(peak_value) - float(trough_value))
    depth_pct = depth_abs / float(peak_value) if peak_value > 0 else 0.0
    return DrawdownWindow(
        peak_time=int(peak_time),
        trough_time=int(trough_time),
        recovery_time=recovery_time,
        depth_abs=depth_abs,
        depth_pct=depth_pct,
    )


def _dominant_strategy(
    attribution: list[StrategyAttribution],
) -> StrategyAttribution | None:
    if not attribution:
        return None
    return max(attribution, key=lambda row: abs(row.share_of_pnl))


def _equity_curve_summary(report: BacktestReport) -> list[str]:
    curve = report.equity_curve
    rows = [
        "| Item | Value |",
        "|---|---:|",
        f"| points | {len(curve)} |",
    ]
    if not curve:
        rows.extend(
            [
                "| first | N/A |",
                "| last | N/A |",
                "| min_equity | N/A |",
                "| max_equity | N/A |",
                f"| max_drawdown | {_format_number(report.metrics.max_drawdown)} |",
                f"| max_drawdown_pct | {_format_pct(report.metrics.max_drawdown_pct)} |",
            ]
        )
        return rows

    equities = [float(equity) for _, equity in curve]
    first_time, first_equity = curve[0]
    last_time, last_equity = curve[-1]
    rows.extend(
        [
            f"| first | {first_time} / {_format_number(first_equity)} |",
            f"| last | {last_time} / {_format_number(last_equity)} |",
            f"| min_equity | {_format_number(min(equities))} |",
            f"| max_equity | {_format_number(max(equities))} |",
            f"| max_drawdown | {_format_number(report.metrics.max_drawdown)} |",
            f"| max_drawdown_pct | {_format_pct(report.metrics.max_drawdown_pct)} |",
        ]
    )
    return rows


def _json_safe(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _param_value(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(_json_safe(value), sort_keys=True)
    return _format_value(value)


def _format_value(value: Any) -> str:
    if isinstance(value, float):
        return _format_number(value)
    if value is None:
        return "N/A"
    return str(value)


def _format_number(value: float) -> str:
    number = float(value)
    if math.isinf(number):
        return "inf" if number > 0 else "-inf"
    if math.isnan(number):
        return "nan"
    return f"{number:.6g}"


def _format_pct(value: float) -> str:
    number = float(value)
    if not math.isfinite(number):
        return _format_number(number)
    return f"{number:.2%}"


def _cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")
