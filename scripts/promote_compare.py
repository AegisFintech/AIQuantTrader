#!/usr/bin/env python3
"""Compare incumbent and challenger experiments and write a promotion report."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _ensure_runtime_deps() -> None:
    try:
        import duckdb  # noqa: F401
    except ModuleNotFoundError:
        venv_python = ROOT / ".venv" / "bin" / "python"
        venv_root = ROOT / ".venv"
        if venv_python.exists() and Path(sys.prefix).resolve() != venv_root.resolve():
            os.execv(
                str(venv_python),
                [str(venv_python), str(Path(__file__)), *sys.argv[1:]],
            )
        raise


_ensure_runtime_deps()

from finrobot.backtest import (  # noqa: E402
    BacktestConfig,
    FillConfig,
    PositionSizer,
    WalkForwardConfig,
    run_walkforward,
)
from finrobot.data_store import connect  # noqa: E402
from finrobot.prices import load_tsv_bars  # noqa: E402
from finrobot.research.comparison import compare, render_markdown  # noqa: E402
from finrobot.research.experiments import (  # noqa: E402
    ExperimentRecord,
    experiment_path,
    git_sha,
    utc_now_iso,
)
from finrobot.research.registry import (  # noqa: E402
    index_promotion_report,
    init_registry,
)
from run_walkforward import (  # noqa: E402
    _bar_data_hash,
    _fold_results_payload,
    _load_bars,
    _strategy_factory,
)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    _validate_args(parser, args)

    try:
        report_id = args.report_id or _default_report_id()
        incumbent = _resolve_experiment(
            role="incumbent",
            run_id=args.incumbent_run_id,
            strategy=args.incumbent_strategy,
            report_id=report_id,
            args=args,
        )
        challenger = _resolve_experiment(
            role="challenger",
            run_id=args.challenger_run_id,
            strategy=args.challenger_strategy,
            report_id=report_id,
            args=args,
        )
        report = compare(incumbent, challenger, report_id=report_id)

        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        md_path = output_dir / f"{report.report_id}.md"
        json_path = output_dir / f"{report.report_id}.json"
        markdown = render_markdown(report, notes=args.notes)
        md_path.write_text(markdown, encoding="utf-8")
        json_path.write_text(
            json.dumps(_report_payload(report, notes=args.notes), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )

        with connect(Path(args.registry)) as con:
            index_promotion_report(
                con,
                report,
                md_path=md_path,
                json_path=json_path,
            )

        first_line = next(line for line in markdown.splitlines() if line.strip())
        print(first_line)
        print(
            f"{report.verdict.decision.value}: {report.verdict.headline} "
            f"({incumbent.strategy_name}/{incumbent.run_id} vs "
            f"{challenger.strategy_name}/{challenger.run_id})"
        )
        return 0
    except Exception as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--incumbent-run-id", default="")
    parser.add_argument("--incumbent-strategy", default="")
    parser.add_argument("--challenger-run-id", default="")
    parser.add_argument("--challenger-strategy", default="")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--n-folds", type=int, default=5)
    parser.add_argument("--purge-bars", type=int, default=50)
    parser.add_argument("--embargo-bars", type=int, default=50)
    parser.add_argument("--report-id", default="")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "state" / "research" / "reports",
    )
    parser.add_argument("--registry", type=Path, default=ROOT / "data" / "finrobot.duckdb")
    parser.add_argument("--notes", default="")
    parser.add_argument(
        "--data-source",
        type=Path,
        default=None,
        help=argparse.SUPPRESS,
    )
    return parser


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if not args.incumbent_run_id and not args.incumbent_strategy:
        parser.error("--incumbent-run-id or --incumbent-strategy is required")
    if not args.challenger_run_id and not args.challenger_strategy:
        parser.error("--challenger-run-id or --challenger-strategy is required")


def _resolve_experiment(
    *,
    role: str,
    run_id: str,
    strategy: str,
    report_id: str,
    args: argparse.Namespace,
) -> ExperimentRecord:
    if run_id:
        return _load_record_by_run_id(Path(args.registry), run_id)
    if not strategy:
        raise ValueError(f"{role} strategy is required when run ID is absent")
    return _run_fresh_walkforward(
        role=role,
        strategy=strategy,
        symbol=args.symbol,
        report_id=report_id,
        args=args,
    )


def _load_record_by_run_id(registry_path: Path, run_id: str) -> ExperimentRecord:
    json_path: Path | None = None
    with connect(registry_path) as con:
        init_registry(con)
        rows = con.execute(
            "SELECT json_path FROM experiments WHERE run_id = ?",
            [run_id],
        ).fetchall()
    if rows and rows[0][0]:
        json_path = Path(rows[0][0])
    fallback = experiment_path(run_id)
    if json_path is None and fallback.exists():
        json_path = fallback
    if json_path is None or not json_path.exists():
        raise ValueError(f"experiment run_id not found or JSON missing: {run_id}")
    return _experiment_from_path(json_path)


def _experiment_from_path(path: Path) -> ExperimentRecord:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"experiment JSON must contain an object: {path}")
    field_names = {field.name for field in fields(ExperimentRecord)}
    return ExperimentRecord(**{key: payload[key] for key in payload if key in field_names})


def _run_fresh_walkforward(
    *,
    role: str,
    strategy: str,
    symbol: str,
    report_id: str,
    args: argparse.Namespace,
) -> ExperimentRecord:
    strategy_factory, strategy_config = _strategy_factory(
        strategy,
        inner_strategy="",
        params={},
    )
    bars = _load_bars_with_fallback(args=args, symbol=symbol)
    if not bars:
        raise ValueError(f"no bars found for {symbol}")

    wf_config = WalkForwardConfig(
        n_folds=args.n_folds,
        n_purge_bars=args.purge_bars,
        n_embargo_bars=args.embargo_bars,
    )
    backtest_config = BacktestConfig(
        symbol=symbol,
        fill_config=FillConfig(),
        sizer=PositionSizer(
            risk_per_trade_fraction=0.001,
            daily_loss_cap_fraction=0.01,
            max_lot_per_trade=0.10,
            max_positions_per_symbol=2,
        ),
    )
    result = run_walkforward(
        bars,
        strategy_factory=strategy_factory,
        config=wf_config,
        backtest_config=backtest_config,
    )
    return ExperimentRecord(
        run_id=f"{report_id}-{role}",
        strategy_name=strategy_config["strategy_name"],
        symbol=symbol,
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
        notes=f"M4 fresh walk-forward for {role}",
        promotion_decision=_promotion_decision(result.verdict.status),
    )


def _load_bars_with_fallback(*, args: argparse.Namespace, symbol: str) -> list[dict]:
    data_sources = (
        [Path(args.data_source)]
        if args.data_source is not None
        else [Path(args.registry), ROOT / "data" / "finrobot.duckdb"]
    )
    for data_source in data_sources:
        try:
            bars = _load_bars(
                data_source=data_source,
                symbol=symbol,
                from_date="",
                to_date="",
            )
        except Exception:
            bars = []
        if bars:
            return bars
    if symbol.upper() == "XAUUSD":
        tsv_path = ROOT / "data" / "XAUUSD1.csv"
        if tsv_path.exists():
            return [dict(bar) for bar in load_tsv_bars(tsv_path)]
    return []


def _promotion_decision(status: str) -> str:
    if status == "pass":
        return "promote"
    if status == "fail":
        return "reject"
    return "needs_review"


def _report_payload(report: Any, *, notes: str) -> dict[str, Any]:
    payload = _json_safe(report)
    if isinstance(payload, dict) and notes:
        payload["notes"] = notes
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
    if hasattr(value, "value") and value.__class__.__module__.startswith("finrobot."):
        return value.value
    if hasattr(value, "__dict__") and value.__class__.__module__.startswith("finrobot."):
        return _json_safe(vars(value))
    return value


def _default_report_id() -> str:
    return datetime.now(timezone.utc).strftime("promotion-%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
