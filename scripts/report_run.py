#!/usr/bin/env python3
"""Generate markdown and JSON reports for a serialized BacktestResult."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from finrobot.backtest import (  # noqa: E402
    BacktestConfig,
    BacktestResult,
    BreakEvenConfig,
    FillConfig,
    PositionSizer,
    ReportMetadata,
    generate_report,
    write_json,
    write_markdown,
)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    try:
        result = (
            _load_backtest_result(args.backtest_json)
            if args.backtest_json is not None
            else _empty_result(symbol=args.symbol)
        )
        params = _parse_params(args.params)
        data_hash = _sha256_file(args.bars) if args.bars is not None else ""
        run_id = args.run_id or _default_run_id()
        symbol = args.symbol or str(getattr(result.config, "symbol", "") or "")
        strategy_name = args.strategy_name or str(getattr(result, "strategy_name", "") or "")
        metadata = ReportMetadata(
            run_id=run_id,
            experiment_id=args.experiment_id,
            git_sha=args.git_sha or _git_sha(),
            data_hash=data_hash,
            from_date=args.from_date,
            to_date=args.to_date,
            symbol=symbol,
            strategy_name=strategy_name,
            params=params,
        )
        report = generate_report(result, metadata=metadata)

        args.output_dir.mkdir(parents=True, exist_ok=True)
        json_path = args.output_dir / f"{run_id}.json"
        markdown_path = args.output_dir / f"{run_id}.md"
        write_json(report, json_path)
        write_markdown(report, markdown_path)

        print(f"Verdict: {report.verdict.status}")
        first_line = report.verdict.rationale.splitlines()[0]
        print(first_line)
        print(f"JSON: {json_path}")
        print(f"Markdown: {markdown_path}")
        return 0
    except Exception as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backtest-json", type=Path, default=None)
    parser.add_argument("--bars", type=Path, default=None)
    parser.add_argument("--run-id", default=_default_run_id())
    parser.add_argument("--experiment-id", default="")
    parser.add_argument("--git-sha", default="")
    parser.add_argument("--symbol", default="")
    parser.add_argument("--from-date", default="")
    parser.add_argument("--to-date", default="")
    parser.add_argument("--strategy-name", default="")
    parser.add_argument("--params", default="{}")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "reports")
    return parser


def _empty_result(*, symbol: str = "") -> BacktestResult:
    config = BacktestConfig(symbol=symbol)
    initial = float(config.initial_equity)
    return BacktestResult(
        config=config,
        strategy_name="",
        bars=0,
        start_time=0,
        end_time=0,
        initial_equity=initial,
        final_equity=initial,
        trades=[],
        equity_curve=[],
        open_positions_at_end=[],
        rejected_signals=0,
    )


def _load_backtest_result(path: Path) -> BacktestResult:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("backtest JSON must contain an object")

    config_payload = payload.get("config")
    initial_equity = float(payload.get("initial_equity", 10000.0) or 10000.0)
    config = _config_from_payload(config_payload, initial_equity=initial_equity)
    trades = payload.get("trades", [])
    if not isinstance(trades, list):
        raise ValueError("backtest JSON trades field must be a list")
    equity_curve = _equity_curve_from_payload(payload.get("equity_curve", []))
    final_equity = float(
        payload.get(
            "final_equity",
            equity_curve[-1][1] if equity_curve else initial_equity,
        )
    )
    return BacktestResult(
        config=config,
        strategy_name=str(payload.get("strategy_name", "") or ""),
        bars=int(payload.get("bars", len(equity_curve)) or 0),
        start_time=int(payload.get("start_time", equity_curve[0][0] if equity_curve else 0) or 0),
        end_time=int(payload.get("end_time", equity_curve[-1][0] if equity_curve else 0) or 0),
        initial_equity=initial_equity,
        final_equity=final_equity,
        trades=trades,
        equity_curve=equity_curve,
        open_positions_at_end=[],
        rejected_signals=int(payload.get("rejected_signals", 0) or 0),
    )


def _config_from_payload(payload: Any, *, initial_equity: float) -> BacktestConfig:
    if not isinstance(payload, dict):
        return BacktestConfig(initial_equity=initial_equity)

    fill_config = _fill_config_from_payload(payload.get("fill_config"))
    sizer = _sizer_from_payload(payload.get("sizer"))
    break_even = _break_even_from_payload(payload.get("break_even"))
    return BacktestConfig(
        symbol=str(payload.get("symbol", "XAUUSD") or "XAUUSD"),
        fill_config=fill_config,
        sizer=sizer,
        initial_equity=initial_equity,
        magic=int(payload.get("magic", 20260522) or 20260522),
        point_value=float(payload.get("point_value", 1.0) or 1.0),
        min_seconds_between_trades=int(payload.get("min_seconds_between_trades", 0) or 0),
        break_even=break_even,
    )


def _fill_config_from_payload(payload: Any) -> FillConfig:
    if not isinstance(payload, dict):
        return FillConfig()
    return FillConfig(
        point_size=float(payload.get("point_size", 1.0) or 1.0),
        spread_points=float(payload.get("spread_points", 5.0) or 0.0),
        slippage_points=float(payload.get("slippage_points", 0.0) or 0.0),
        commission_per_lot=float(payload.get("commission_per_lot", 0.0) or 0.0),
        swap_per_lot_per_day=float(payload.get("swap_per_lot_per_day", 0.0) or 0.0),
    )


def _sizer_from_payload(payload: Any) -> PositionSizer:
    if not isinstance(payload, dict):
        return PositionSizer(
            risk_per_trade_fraction=0.001,
            daily_loss_cap_fraction=0.01,
            max_lot_per_trade=0.10,
            max_positions_per_symbol=2,
        )
    return PositionSizer(
        risk_per_trade_fraction=float(payload.get("risk_per_trade_fraction", 0.001) or 0.0),
        daily_loss_cap_fraction=float(payload.get("daily_loss_cap_fraction", 0.01) or 0.0),
        max_lot_per_trade=float(payload.get("max_lot_per_trade", 0.10) or 0.0),
        max_positions_per_symbol=int(payload.get("max_positions_per_symbol", 2) or 0),
    )


def _break_even_from_payload(payload: Any) -> BreakEvenConfig:
    if not isinstance(payload, dict):
        return BreakEvenConfig()
    return BreakEvenConfig(
        enabled=bool(payload.get("enabled", False)),
        rr_ratio=float(payload.get("rr_ratio", 1.0) or 1.0),
        extra_points=float(payload.get("extra_points", 10.0) or 10.0),
    )


def _equity_curve_from_payload(payload: Any) -> list[tuple[int, float]]:
    if not isinstance(payload, list):
        raise ValueError("backtest JSON equity_curve field must be a list")
    curve: list[tuple[int, float]] = []
    for point in payload:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("each equity_curve point must be [time, equity]")
        curve.append((int(float(point[0])), float(point[1])))
    return curve


def _parse_params(value: str) -> dict[str, Any]:
    params = json.loads(value)
    if not isinstance(params, dict):
        raise ValueError("--params must decode to a JSON object")
    return params


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_sha() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
