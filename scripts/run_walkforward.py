#!/usr/bin/env python3
"""Run purged walk-forward validation and track the experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ensure_runtime_deps() -> None:
    try:
        import duckdb  # noqa: F401
    except ModuleNotFoundError:
        venv_python = ROOT / ".venv" / "bin" / "python"
        venv_root = ROOT / ".venv"
        if venv_python.exists() and Path(sys.prefix).resolve() != venv_root.resolve():
            os.execv(str(venv_python), [str(venv_python), str(Path(__file__)), *sys.argv[1:]])
        raise


_ensure_runtime_deps()

from finrobot.backtest import (  # noqa: E402
    BacktestConfig,
    XAUUSD_ICMARKETS_DEMO,
    PositionSizer,
    WalkForwardConfig,
    XauAtrImpulseParams,
    XauAtrImpulseStrategy,
    XauGatedParams,
    XauGatedStrategy,
    XauQuickMomentumParams,
    XauQuickMomentumStrategy,
    run_walkforward,
)
from finrobot.backtest.strategies.base import Strategy  # noqa: E402
from finrobot.data_store import connect  # noqa: E402
from finrobot.research.experiments import (  # noqa: E402
    ExperimentRecord,
    git_sha,
    save_experiment,
    utc_now_iso,
)
from finrobot.research.registry import init_registry, index_experiment  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    try:
        strategy_params = _parse_json_object(args.strategy_params)
        strategy_factory, strategy_config = _strategy_factory(
            args.strategy,
            inner_strategy=args.inner_strategy,
            params=strategy_params,
        )
        bars = _load_bars(
            data_source=args.data_source,
            symbol=args.symbol,
            from_date=args.from_date,
            to_date=args.to_date,
        )
        if not bars:
            raise ValueError(f"no bars found for {args.symbol} in {args.data_source}")

        run_id = args.run_id or _default_run_id()
        wf_config = WalkForwardConfig(
            n_folds=args.folds,
            n_purge_bars=args.purge_bars,
            n_embargo_bars=args.embargo_bars,
            train_size_bars=args.train_size_bars,
            min_train_bars=args.min_train_bars,
            min_test_bars=args.min_test_bars,
        )
        backtest_config = BacktestConfig(
            symbol=args.symbol,
            fill_config=XAUUSD_ICMARKETS_DEMO.fill_config(),
            sizer=PositionSizer(
                risk_per_trade_fraction=args.risk_per_trade_fraction,
                daily_loss_cap_fraction=args.daily_loss_cap_fraction,
                max_lot_per_trade=args.max_lot_per_trade,
                max_positions_per_symbol=2,
            ),
            point_value=XAUUSD_ICMARKETS_DEMO.price_value_per_lot,
        )

        result = run_walkforward(
            bars,
            strategy_factory=strategy_factory,
            config=wf_config,
            backtest_config=backtest_config,
        )
        record = ExperimentRecord(
            run_id=run_id,
            strategy_name=strategy_config["strategy_name"],
            symbol=args.symbol,
            created_at=utc_now_iso(),
            git_sha=git_sha(),
            data_hash=_bar_data_hash(bars),
            config=strategy_config,
            walk_forward_config=asdict(wf_config),
            backtest_config=_json_safe(backtest_config),
            fold_results=_fold_results_payload(result.folds),
            aggregated_metrics=_json_safe(result.aggregated_metrics),
            walk_forward_stability=_json_safe(result.walk_forward_stability),
            verdict=_json_safe(result.verdict),
            notes=args.notes,
            promotion_decision=_promotion_decision(result.verdict.status),
        )

        json_path = save_experiment(record, root=args.output_dir)
        with connect(args.registry) as con:
            init_registry(con)
            index_experiment(con, record, json_path)

        _print_summary(record)
        return 0
    except Exception as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", default="XauAtrImpulse")
    parser.add_argument("--inner-strategy", default="")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--purge-bars", type=int, default=50)
    parser.add_argument("--embargo-bars", type=int, default=50)
    parser.add_argument("--train-size-bars", type=int, default=None)
    parser.add_argument("--min-train-bars", type=int, default=1000)
    parser.add_argument("--min-test-bars", type=int, default=100)
    parser.add_argument("--data-source", type=Path, default=ROOT / "data" / "finrobot.duckdb")
    parser.add_argument("--from-date", default="")
    parser.add_argument("--to-date", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--notes", default="")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "state" / "research" / "experiments",
    )
    parser.add_argument("--registry", type=Path, default=ROOT / "data" / "finrobot.duckdb")
    parser.add_argument("--strategy-params", default="{}")
    parser.add_argument("--max-lot-per-trade", type=float, default=0.10)
    parser.add_argument("--risk-per-trade-fraction", type=float, default=0.001)
    parser.add_argument("--daily-loss-cap-fraction", type=float, default=0.01)
    return parser


def _load_bars(
    *,
    data_source: Path,
    symbol: str,
    from_date: str,
    to_date: str,
) -> list[dict]:
    where = [
        "symbol = ?",
        "open IS NOT NULL",
        "high IS NOT NULL",
        "low IS NOT NULL",
        "close IS NOT NULL",
    ]
    params: list[Any] = [symbol]
    start = _date_epoch(from_date) if from_date else None
    end = _date_epoch(to_date, end_of_day=True) if to_date else None
    if start is not None:
        where.append("ts_server >= ?")
        params.append(start)
    if end is not None:
        where.append("ts_server <= ?")
        params.append(end)

    sql = (
        "SELECT ts_server, open, high, low, close, volume "
        "FROM prices WHERE "
        + " AND ".join(where)
        + " ORDER BY ts_server"
    )
    with connect(data_source) as con:
        rows = con.execute(sql, params).fetchall()
    return [
        {
            "time": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5] or 0.0),
        }
        for row in rows
    ]


def _strategy_factory(
    name: str,
    *,
    inner_strategy: str,
    params: dict[str, Any],
) -> tuple[Callable[[], Strategy], dict[str, Any]]:
    resolved = _normalize_strategy_name(name)
    if resolved == "XauGated":
        inner_name = _normalize_strategy_name(inner_strategy or "XauAtrImpulse")
        inner_class, inner_params_class = _strategy_class(inner_name)
        gate_payload = params.get("gate", params)
        inner_payload = params.get("inner", {})
        if not isinstance(gate_payload, dict) or not isinstance(inner_payload, dict):
            raise ValueError("XauGated strategy params must contain JSON objects")
        diff = {
            "gate": _config_diff(XauGatedParams(), gate_payload),
            "inner": _config_diff(inner_params_class(), inner_payload),
        }

        def factory() -> Strategy:
            return XauGatedStrategy(
                inner_class(**inner_payload),
                **gate_payload,
            )

        return factory, {
            "strategy_name": "XauGated",
            "requested_strategy": name,
            "inner_strategy": inner_name,
            "params": params,
            "strategy_config_diff": diff,
        }

    strategy_class, params_class = _strategy_class(resolved)
    diff = _config_diff(params_class(), params)

    def factory() -> Strategy:
        return strategy_class(**params)

    return factory, {
        "strategy_name": resolved,
        "requested_strategy": name,
        "inner_strategy": "",
        "params": params,
        "strategy_config_diff": diff,
    }


def _normalize_strategy_name(name: str) -> str:
    normalized = str(name or "").strip()
    aliases = {
        "QuickMomentum_EMA_cross": "XauQuickMomentum",
        "XauQuickMomentumStrategy": "XauQuickMomentum",
        "XauQuickMomentum": "XauQuickMomentum",
        "ATR_impulse": "XauAtrImpulse",
        "XauAtrImpulseStrategy": "XauAtrImpulse",
        "XauAtrImpulse": "XauAtrImpulse",
        "XauGatedStrategy": "XauGated",
        "XauGated": "XauGated",
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported strategy: {name}") from exc


def _strategy_class(name: str):
    if name == "XauQuickMomentum":
        return XauQuickMomentumStrategy, XauQuickMomentumParams
    if name == "XauAtrImpulse":
        return XauAtrImpulseStrategy, XauAtrImpulseParams
    raise ValueError(f"unsupported inner strategy: {name}")


def _config_diff(defaults: Any, overrides: dict[str, Any]) -> dict[str, Any]:
    default_payload = _json_safe(defaults)
    diff: dict[str, Any] = {}
    for key, value in sorted(overrides.items()):
        default_value = default_payload.get(key) if isinstance(default_payload, dict) else None
        if value != default_value:
            diff[key] = {"default": default_value, "value": value}
    return diff


def _fold_results_payload(folds) -> list[dict[str, Any]]:
    rows = []
    for fold in folds:
        rows.append(
            {
                "fold_idx": fold.fold.fold_idx,
                "train_start": fold.fold.train_start,
                "train_end": fold.fold.train_end,
                "test_start": fold.fold.test_start,
                "test_end": fold.fold.test_end,
                "train_bars": len(fold.fold.train_bars),
                "test_bars": len(fold.fold.test_bars),
                "purge_bars": fold.fold.purge_bars,
                "embargo_bars": fold.fold.embargo_bars,
                "metrics": _json_safe(fold.metrics),
                "verdict": _json_safe(fold.verdict),
            }
        )
    return rows


def _print_summary(record: ExperimentRecord) -> None:
    metrics = record.aggregated_metrics
    stability = record.walk_forward_stability
    print("Walk-forward summary")
    print(f"  run_id: {record.run_id}")
    print(f"  strategy: {record.strategy_name}")
    print(f"  symbol: {record.symbol}")
    print(f"  verdict: {record.verdict.get('status')}")
    print(f"  promotion_decision: {record.promotion_decision}")
    print(f"  mean_pnl: {_fmt(metrics['total_pnl']['mean'])}")
    print(f"  mean_profit_factor: {_fmt(metrics['profit_factor']['mean'])}")
    print(f"  consistency_score: {_fmt(stability['consistency_score'])}")
    print("")
    print("| Fold | Trades | Total PnL | Win Rate | Profit Factor | Max DD % | Verdict |")
    print("|---:|---:|---:|---:|---:|---:|---|")
    for row in record.fold_results:
        fold_metrics = row["metrics"]
        print(
            "| "
            + " | ".join(
                [
                    str(row["fold_idx"]),
                    str(fold_metrics["n_trades"]),
                    _fmt(fold_metrics["total_pnl"]),
                    _fmt(fold_metrics["win_rate"]),
                    _fmt(fold_metrics["profit_factor"]),
                    _fmt(fold_metrics["max_drawdown_pct"]),
                    str(row["verdict"]["status"]),
                ]
            )
            + " |"
        )
    print("")
    print(record.verdict.get("rationale", "").splitlines()[0])


def _promotion_decision(status: str) -> str:
    if status == "pass":
        return "promote"
    if status == "fail":
        return "reject"
    return "needs_review"


def _bar_data_hash(bars: list[dict]) -> str:
    digest = hashlib.sha256()
    for bar in sorted(bars, key=lambda item: int(item["time"])):
        digest.update(f"{int(bar['time'])},{float(bar['close']):.10f}\n".encode())
    return digest.hexdigest()


def _date_epoch(value: str, *, end_of_day: bool = False) -> int:
    text = str(value).strip()
    if not text:
        raise ValueError("date value cannot be empty")
    try:
        return int(float(text))
    except ValueError:
        pass
    if len(text) == 10:
        suffix = "T23:59:59+00:00" if end_of_day else "T00:00:00+00:00"
        text = text + suffix
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _parse_json_object(value: str) -> dict[str, Any]:
    payload = json.loads(value or "{}")
    if not isinstance(payload, dict):
        raise ValueError("--strategy-params must decode to a JSON object")
    return payload


def _json_safe(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dict__") and value.__class__.__module__.startswith("finrobot."):
        return _json_safe(vars(value))
    return value


def _fmt(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isinf(number):
        return "inf" if number > 0 else "-inf"
    if math.isnan(number):
        return "nan"
    return f"{number:.6g}"


def _default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
