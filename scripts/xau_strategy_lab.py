#!/usr/bin/env python3
"""Evaluate bounded aggressive XAUUSD profiles and optionally deploy a winner."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, is_dataclass, replace
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
            os.execv(str(venv_python), [str(venv_python), str(Path(__file__)), *sys.argv[1:]])
        raise


_ensure_runtime_deps()


from finrobot.backtest import (  # noqa: E402
    BacktestConfig,
    Backtester,
    BreakEvenConfig,
    DailyRiskSizer,
    compute_metrics,
    WalkForwardConfig,
    XAUUSD_ICMARKETS_DEMO,
    XauAtrImpulseParams,
    XauAtrImpulseStrategy,
    XauGatedParams,
    XauGatedStrategy,
    run_walkforward,
)
from finrobot.data_store import connect  # noqa: E402
from finrobot.research.experiments import (  # noqa: E402
    ExperimentRecord,
    git_sha,
    save_experiment,
    utc_now_iso,
)
from finrobot.research.registry import init_registry, index_experiment  # noqa: E402
from finrobot.xau_profiles import (  # noqa: E402
    DEFAULT_PROFILE,
    PROFILE_CANDIDATES,
    PROFILE_FILENAME,
    XauStrategyProfile,
    profile_by_name,
    write_profile_csv,
)
from runtime_paths import common_dir as default_common_dir  # noqa: E402


@dataclass(frozen=True)
class CandidateResult:
    profile: dict[str, Any]
    run_id: str
    experiment_json: str
    verdict_status: str
    promotable: bool
    score: float
    mean_total_pnl: float
    mean_profit_factor: float
    mean_max_drawdown_pct: float
    mean_n_trades: float
    consistency_score: float
    worst_fold_pnl: float
    recent_total_pnl: float
    recent_profit_factor: float
    incumbent_delta_pnl: float
    notes: str


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.harvest_first:
            _harvest_latest(args)

        profiles = _profiles(args)
        bars = _load_bars(
            data_source=args.data_source,
            symbol=args.symbol,
            from_date=args.from_date,
            to_date=args.to_date,
            max_bars=args.max_bars,
        )
        if not bars:
            raise ValueError(f"no bars found for {args.symbol}")
        _validate_data_freshness(bars, max_age_hours=args.max_data_age_hours)

        run_id = args.run_id or _default_run_id()
        output_dir = Path(args.output_dir)
        experiment_dir = Path(args.experiment_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        experiment_dir.mkdir(parents=True, exist_ok=True)

        raw_results = [
            _evaluate_profile(
                profile=profile,
                bars=bars,
                args=args,
                run_id=f"{run_id}-{profile.profile_name}",
                experiment_dir=experiment_dir,
            )
            for profile in profiles
        ]
        results = _apply_relative_promotion_gates(raw_results, args)
        winner = _winner(results)
        profile_path = ""
        if args.write_profile:
            if winner.promotable or args.force_profile:
                profile_path = str(_write_winner_profile(winner, args))
            else:
                print(
                    "[skip] no profile deployed: winner did not clear promotion gates",
                    file=sys.stderr,
                )

        report = {
            "run_id": run_id,
            "created_at": utc_now_iso(),
            "symbol": args.symbol,
            "git_sha": git_sha(),
            "data_source": str(args.data_source),
            "data_hash": _bar_data_hash(bars),
            "data_window": {
                "bars": len(bars),
                "start": _epoch_to_iso(int(bars[0]["time"])),
                "end": _epoch_to_iso(int(bars[-1]["time"])),
            },
            "walk_forward_config": _json_safe(_walk_forward_config(args)),
            "backtest_defaults": {
                "initial_equity": args.initial_equity,
                "min_trades": args.min_trades,
                "min_consistency": args.min_consistency,
                "recent_bars": args.recent_bars,
                "min_recent_pnl": args.min_recent_pnl,
                "min_recent_profit_factor": args.min_recent_profit_factor,
                "min_challenger_pnl_delta": args.min_challenger_pnl_delta,
                "min_challenger_pf_delta": args.min_challenger_pf_delta,
                "max_data_age_hours": args.max_data_age_hours,
            },
            "winner": _json_safe(winner),
            "deployed_profile_path": profile_path,
            "candidates": [_json_safe(result) for result in results],
        }
        report_path = output_dir / f"{run_id}.json"
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _print_summary(report_path, winner, results, profile_path)
        return 0
    except Exception as exc:
        print(f"[ERR] {exc}", file=sys.stderr)
        return 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--candidate", action="append", default=[])
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--purge-bars", type=int, default=50)
    parser.add_argument("--embargo-bars", type=int, default=50)
    parser.add_argument("--min-train-bars", type=int, default=1000)
    parser.add_argument("--min-test-bars", type=int, default=100)
    parser.add_argument("--train-size-bars", type=int, default=None)
    parser.add_argument("--recent-bars", type=int, default=10000)
    parser.add_argument("--min-recent-pnl", type=float, default=0.0)
    parser.add_argument("--min-recent-profit-factor", type=float, default=1.05)
    parser.add_argument("--min-challenger-pnl-delta", type=float, default=250.0)
    parser.add_argument("--min-challenger-pf-delta", type=float, default=0.05)
    parser.add_argument("--from-date", default="")
    parser.add_argument("--to-date", default="")
    parser.add_argument("--max-bars", type=int, default=0)
    parser.add_argument(
        "--max-data-age-hours",
        type=float,
        default=72.0,
        help="Reject stale research bars; use 0 only for controlled historical tests",
    )
    parser.add_argument("--initial-equity", type=float, default=1_000_000.0)
    parser.add_argument("--min-trades", type=float, default=8.0)
    parser.add_argument("--min-consistency", type=float, default=0.60)
    parser.add_argument("--data-source", type=Path, default=ROOT / "data" / "finrobot.duckdb")
    parser.add_argument("--registry", type=Path, default=ROOT / "data" / "finrobot.duckdb")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "state" / "research" / "profile_lab")
    parser.add_argument("--experiment-dir", type=Path, default=ROOT / "state" / "research" / "experiments")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--harvest-first", action="store_true")
    parser.add_argument("--write-profile", action="store_true")
    parser.add_argument("--force-profile", action="store_true")
    parser.add_argument("--common-dir", type=Path, default=None)
    parser.add_argument("--profile-output", type=Path, default=None)
    return parser


def _profiles(args: argparse.Namespace) -> list[XauStrategyProfile]:
    if not args.candidate:
        return list(PROFILE_CANDIDATES)
    return [profile_by_name(name).bounded() for name in args.candidate]


def _evaluate_profile(
    *,
    profile: XauStrategyProfile,
    bars: list[dict],
    args: argparse.Namespace,
    run_id: str,
    experiment_dir: Path,
) -> CandidateResult:
    bounded = profile.bounded()
    wf_config = _walk_forward_config(args)
    backtest_config = _backtest_config(args, bounded)
    result = run_walkforward(
        bars,
        strategy_factory=lambda: _strategy(bounded),
        config=wf_config,
        backtest_config=backtest_config,
    )
    aggregated = _json_safe(result.aggregated_metrics)
    stability = _json_safe(result.walk_forward_stability)
    verdict = _json_safe(result.verdict)
    mean_total_pnl = _metric_mean(aggregated, "total_pnl")
    mean_profit_factor = _metric_mean(aggregated, "profit_factor")
    mean_max_drawdown_pct = _metric_mean(aggregated, "max_drawdown_pct")
    mean_n_trades = _metric_mean(aggregated, "n_trades")
    consistency = float(stability.get("consistency_score") or 0.0)
    worst_fold_pnl = float(stability.get("worst_fold_pnl") or 0.0)
    recent_metrics = _recent_metrics(
        profile=bounded,
        bars=bars,
        args=args,
        backtest_config=backtest_config,
    )
    recent_total_pnl = float(recent_metrics.total_pnl)
    recent_profit_factor = float(recent_metrics.profit_factor)
    score = _score(
        mean_total_pnl=mean_total_pnl,
        mean_profit_factor=mean_profit_factor,
        mean_max_drawdown_pct=mean_max_drawdown_pct,
        consistency=consistency,
        worst_fold_pnl=worst_fold_pnl,
        recent_total_pnl=recent_total_pnl,
    )
    promotable = (
        str(verdict.get("status")) == "pass"
        and mean_total_pnl > 0.0
        and mean_n_trades >= float(args.min_trades)
        and consistency >= float(args.min_consistency)
        and worst_fold_pnl > -float(args.initial_equity) * bounded.daily_loss_limit_fraction
        and recent_total_pnl >= float(args.min_recent_pnl)
        and recent_profit_factor >= float(args.min_recent_profit_factor)
    )
    record = ExperimentRecord(
        run_id=run_id,
        strategy_name="XauGated",
        symbol=args.symbol,
        created_at=utc_now_iso(),
        git_sha=git_sha(),
        data_hash=_bar_data_hash(bars),
        config={
            "profile": bounded.to_dict(),
            "strategy_name": "XauGated",
            "inner_strategy": "XauAtrImpulse",
        },
        walk_forward_config=_json_safe(wf_config),
        backtest_config=_json_safe(backtest_config),
        fold_results=_fold_results_payload(result.folds),
        aggregated_metrics=aggregated,
        walk_forward_stability=stability,
        verdict=verdict,
        notes="xau_strategy_lab bounded profile evaluation",
        promotion_decision="promote" if promotable else "reject",
    )
    json_path = save_experiment(record, root=experiment_dir)
    with connect(args.registry) as con:
        init_registry(con)
        index_experiment(con, record, json_path)
    return CandidateResult(
        profile=bounded.to_dict(),
        run_id=run_id,
        experiment_json=str(json_path),
        verdict_status=str(verdict.get("status") or ""),
        promotable=promotable,
        score=score,
        mean_total_pnl=mean_total_pnl,
        mean_profit_factor=mean_profit_factor,
        mean_max_drawdown_pct=mean_max_drawdown_pct,
        mean_n_trades=mean_n_trades,
        consistency_score=consistency,
        worst_fold_pnl=worst_fold_pnl,
        recent_total_pnl=recent_total_pnl,
        recent_profit_factor=recent_profit_factor,
        incumbent_delta_pnl=0.0,
        notes=str(verdict.get("rationale") or "").splitlines()[0],
    )


def _strategy(profile: XauStrategyProfile) -> XauGatedStrategy:
    inner = XauAtrImpulseStrategy(
        XauAtrImpulseParams(
            impulse_atr_mult=profile.impulse_atr_multiplier,
            stop_atr_mult=profile.stop_atr_multiplier,
            tp_atr_mult=profile.take_profit_atr_multiplier,
        )
    )
    return XauGatedStrategy(
        inner,
        gate_params=XauGatedParams(
            pda_long_ceiling=profile.pda_long_ceiling,
            pda_short_floor=profile.pda_short_floor,
            min_smc_score=profile.min_smc_confluence_score_xauusd,
            enable_smc_gate=profile.enable_smart_money_gates,
            enable_pda_gate=True,
            enable_adx_gate=profile.enable_adx_regime_filter,
            adx_min_threshold=profile.adx_min_threshold,
            min_seconds_between_trades=profile.min_seconds_between_trades_xauusd,
            blackout_enabled=profile.blackout_enabled,
            max_atr_regime_multiplier=profile.max_atr_regime_multiplier,
        ),
    )


def _walk_forward_config(args: argparse.Namespace) -> WalkForwardConfig:
    return WalkForwardConfig(
        n_folds=args.folds,
        n_purge_bars=args.purge_bars,
        n_embargo_bars=args.embargo_bars,
        train_size_bars=args.train_size_bars,
        min_train_bars=args.min_train_bars,
        min_test_bars=args.min_test_bars,
    )


def _backtest_config(
    args: argparse.Namespace,
    profile: XauStrategyProfile,
) -> BacktestConfig:
    return BacktestConfig(
        symbol=args.symbol,
        fill_config=XAUUSD_ICMARKETS_DEMO.fill_config(),
        sizer=DailyRiskSizer(
            risk_per_trade_fraction=profile.daily_risk_per_trade_fraction,
            daily_loss_cap_fraction=profile.daily_loss_limit_fraction,
            max_lot_per_trade=profile.max_lot_per_trade_xauusd,
            max_positions_per_symbol=profile.max_auto_positions_xauusd,
            max_lot_per_symbol={args.symbol: profile.max_lot_per_trade_xauusd},
            high_confluence_lot_multiplier=profile.high_confluence_lot_multiplier,
            high_confluence_score=profile.high_confluence_score,
            bad_day_downshift_fraction=profile.bad_day_downshift_fraction,
        ),
        initial_equity=args.initial_equity,
        point_value=XAUUSD_ICMARKETS_DEMO.price_value_per_lot,
        min_seconds_between_trades=profile.min_seconds_between_trades_xauusd,
        loss_streak_pause_count=profile.loss_streak_pause_count,
        max_recent_drawdown_fraction=profile.max_recent_drawdown_fraction,
        break_even=BreakEvenConfig(enabled=True),
    )


def _recent_metrics(
    *,
    profile: XauStrategyProfile,
    bars: list[dict],
    args: argparse.Namespace,
    backtest_config: BacktestConfig,
):
    recent_count = max(1, int(args.recent_bars))
    recent_bars = bars[-recent_count:] if len(bars) > recent_count else bars
    result = Backtester(backtest_config).run(
        strategy=_strategy(profile),
        bars=recent_bars,
    )
    return compute_metrics(result)


def _load_bars(
    *,
    data_source: Path,
    symbol: str,
    from_date: str,
    to_date: str,
    max_bars: int,
) -> list[dict]:
    where = [
        "symbol = ?",
        "open IS NOT NULL",
        "high IS NOT NULL",
        "low IS NOT NULL",
        "close IS NOT NULL",
    ]
    params: list[Any] = [symbol]
    if from_date:
        where.append("ts_server >= ?")
        params.append(_date_epoch(from_date))
    if to_date:
        where.append("ts_server <= ?")
        params.append(_date_epoch(to_date, end_of_day=True))
    limit = ""
    if max_bars > 0:
        limit = " LIMIT ?"
        params.append(int(max_bars))
    sql = (
        "SELECT ts_server, open, high, low, close, volume "
        "FROM ("
        "  SELECT ts_server, open, high, low, close, volume "
        "  FROM prices WHERE "
        + " AND ".join(where)
        + " ORDER BY ts_server DESC"
        + limit
        + ") ORDER BY ts_server"
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


def _validate_data_freshness(
    bars: list[dict],
    *,
    max_age_hours: float,
    now_epoch: float | None = None,
) -> None:
    if max_age_hours <= 0.0 or not bars:
        return
    latest = int(bars[-1]["time"])
    now = time.time() if now_epoch is None else float(now_epoch)
    age_hours = max(0.0, now - latest) / 3600.0
    if age_hours > max_age_hours:
        raise ValueError(
            "research data is stale: "
            f"latest={_epoch_to_iso(latest)} age_hours={age_hours:.1f} "
            f"limit_hours={max_age_hours:.1f}; harvest fresh XAUUSD bars first"
        )


def _winner(results: list[CandidateResult]) -> CandidateResult:
    if not results:
        raise ValueError("no candidate results")
    promotable = [result for result in results if result.promotable]
    pool = promotable or results
    return max(pool, key=lambda result: result.score)


def _apply_relative_promotion_gates(
    results: list[CandidateResult],
    args: argparse.Namespace,
) -> list[CandidateResult]:
    incumbent = next(
        (
            result
            for result in results
            if result.profile.get("profile_name") == DEFAULT_PROFILE.profile_name
        ),
        None,
    )
    if incumbent is None:
        return results

    adjusted: list[CandidateResult] = []
    for result in results:
        profile_name = str(result.profile.get("profile_name") or "")
        delta_pnl = result.mean_total_pnl - incumbent.mean_total_pnl
        notes = result.notes
        promotable = bool(result.promotable)
        if profile_name == DEFAULT_PROFILE.profile_name:
            promotable = False
            notes = _append_note(notes, "incumbent baseline, not a challenger deployment")
        else:
            min_delta = float(args.min_challenger_pnl_delta)
            min_pf_delta = float(args.min_challenger_pf_delta)
            pf_delta = _finite_pf(result.mean_profit_factor) - _finite_pf(
                incumbent.mean_profit_factor
            )
            if delta_pnl < min_delta:
                promotable = False
                notes = _append_note(
                    notes,
                    f"challenger pnl delta {delta_pnl:.2f} < {min_delta:.2f}",
                )
            if pf_delta < min_pf_delta:
                promotable = False
                notes = _append_note(
                    notes,
                    f"challenger pf delta {pf_delta:.3g} < {min_pf_delta:.3g}",
                )
        adjusted.append(
            replace(
                result,
                promotable=promotable,
                incumbent_delta_pnl=delta_pnl,
                notes=notes,
            )
        )
    return adjusted


def _write_winner_profile(winner: CandidateResult, args: argparse.Namespace) -> Path:
    profile = profile_by_name(str(winner.profile["profile_name"]))
    if args.profile_output is not None:
        path = args.profile_output
    else:
        common = args.common_dir if args.common_dir is not None else default_common_dir()
        if common is None:
            raise ValueError("MT5 Common Files directory not found for profile deployment")
        path = Path(common) / PROFILE_FILENAME
    return write_profile_csv(profile, path)


def _harvest_latest(args: argparse.Namespace) -> None:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "harvest_mt5_export.py"),
        "--symbols",
        args.symbol,
        "--warehouse",
        str(args.data_source),
    ]
    completed = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, check=False)
    text = (completed.stdout + "\n" + completed.stderr).strip()
    if text:
        print(text)
    if completed.returncode != 0:
        raise RuntimeError(f"harvest_mt5_export.py failed with {completed.returncode}")


def _score(
    *,
    mean_total_pnl: float,
    mean_profit_factor: float,
    mean_max_drawdown_pct: float,
    consistency: float,
    worst_fold_pnl: float,
    recent_total_pnl: float,
) -> float:
    pf = 5.0 if math.isinf(mean_profit_factor) else max(0.0, mean_profit_factor)
    drawdown_penalty = max(0.0, mean_max_drawdown_pct) * 25_000.0
    worst_penalty = abs(min(0.0, worst_fold_pnl)) * 0.20
    return (
        mean_total_pnl
        + recent_total_pnl * 0.25
        + min(pf, 5.0) * 250.0
        + consistency * 1_000.0
        - drawdown_penalty
        - worst_penalty
    )


def _metric_mean(metrics: dict[str, Any], name: str) -> float:
    value = metrics.get(name, {})
    if isinstance(value, dict):
        value = value.get("mean", 0.0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _finite_pf(value: float) -> float:
    return 5.0 if math.isinf(float(value)) else float(value)


def _append_note(existing: str, addition: str) -> str:
    text = str(existing or "").strip()
    extra = str(addition or "").strip()
    if not text:
        return extra
    if not extra:
        return text
    return f"{text}; {extra}"


def _fold_results_payload(folds) -> list[dict[str, Any]]:
    return [
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
        for fold in folds
    ]


def _bar_data_hash(bars: list[dict]) -> str:
    digest = hashlib.sha256()
    for bar in bars:
        digest.update(
            f"{int(bar['time'])},{float(bar['open']):.5f},{float(bar['close']):.5f}\n".encode()
        )
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
        text += "T23:59:59+00:00" if end_of_day else "T00:00:00+00:00"
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _epoch_to_iso(value: int) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


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
    if isinstance(value, float) and math.isinf(value):
        return "inf" if value > 0 else "-inf"
    return value


def _print_summary(
    report_path: Path,
    winner: CandidateResult,
    results: list[CandidateResult],
    profile_path: str,
) -> None:
    print("XAU strategy lab")
    print(f"  report: {report_path}")
    print(
        "  winner: "
        f"{winner.profile['profile_name']} score={winner.score:.2f} "
        f"promotable={int(winner.promotable)} pnl={winner.mean_total_pnl:.2f} "
        f"pf={winner.mean_profit_factor:.3g} consistency={winner.consistency_score:.2f}"
    )
    if profile_path:
        print(f"  deployed_profile: {profile_path}")
    print("")
    print("| Profile | Promote | Score | Mean PnL | Recent PnL | PF | Recent PF | Trades | Consistency | Inc Delta | Worst Fold |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for result in sorted(results, key=lambda item: item.score, reverse=True):
        print(
            "| "
            + " | ".join(
                [
                    str(result.profile["profile_name"]),
                    str(int(result.promotable)),
                    f"{result.score:.2f}",
                    f"{result.mean_total_pnl:.2f}",
                    f"{result.recent_total_pnl:.2f}",
                    f"{result.mean_profit_factor:.3g}",
                    f"{result.recent_profit_factor:.3g}",
                    f"{result.mean_n_trades:.1f}",
                    f"{result.consistency_score:.2f}",
                    f"{result.incumbent_delta_pnl:.2f}",
                    f"{result.worst_fold_pnl:.2f}",
                ]
            )
            + " |"
        )


def _default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("xau-profile-lab-%Y%m%dT%H%M%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
