#!/usr/bin/env python3
"""Run an EA acknowledgement parity replay."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from finrobot.backtest.parity import ParityReport
from finrobot.backtest.parity_replay import (
    ParityReplayConfig,
    load_acked_decisions,
    run_parity_replay,
)
from runtime_paths import common_dir


def main(argv: Sequence[str] | None = None) -> int:
    """Run the parity replay CLI."""

    parser = _parser()
    args = parser.parse_args(argv)

    data_path = Path(args.data_path)
    acks_path = _resolve_acks_path(args.acks_path)
    if acks_path is None:
        print("acks file missing: MT5 Common Files dir was not found", file=sys.stderr)
        return 1
    if not data_path.exists() or not data_path.is_file():
        print(f"bars file missing: {data_path}", file=sys.stderr)
        return 1
    if not acks_path.exists() or not acks_path.is_file():
        print(f"acks file missing: {acks_path}", file=sys.stderr)
        return 1

    try:
        from finrobot.prices import load_tsv_bars

        bars = list(load_tsv_bars(data_path))
        if not bars:
            print(f"bars file invalid or empty: {data_path}", file=sys.stderr)
            return 1
        run_id = args.run_id or _timestamp_run_id()
        config = ParityReplayConfig(
            from_date=args.from_date,
            to_date=args.to_date,
            symbol=args.symbol,
            fill_tolerance_points=args.fill_tolerance_points,
            bar_match_window=args.bar_match_window,
            run_id=run_id,
        )
        decisions = load_acked_decisions(
            acks_path,
            from_date=args.from_date,
            to_date=args.to_date,
            symbol=args.symbol,
            bars=bars,
            bar_match_window=args.bar_match_window,
            timezone_name=args.broker_time_zone,
        )
        report = run_parity_replay(bars=bars, decisions=decisions, config=config)
    except (OSError, ValueError) as exc:
        print(f"parity input invalid: {exc}", file=sys.stderr)
        return 1

    try:
        run_dir = Path(args.output_dir) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        report_json = _report_dict(report)
        (run_dir / f"{run_id}.json").write_text(
            json.dumps(report_json, indent=2, sort_keys=True) + "\n"
        )
        (run_dir / f"{run_id}.md").write_text(_markdown_summary(report))
    except OSError as exc:
        print(f"output dir not writable: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(_report_dict(report), indent=2, sort_keys=True))
    else:
        print(
            f"parity replay {run_id}: "
            f"{report.n_matched}/{report.n_decisions} matched "
            f"({report.match_rate:.2%})"
        )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="from_date", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--symbol", default="XAUUSD", help="Symbol to replay")
    parser.add_argument("--data-path", default="data/XAUUSD1.csv", help="No-header TSV bars path")
    parser.add_argument(
        "--acks-path",
        default=None,
        help="finrobot_acks.csv path (default: MT5 Common Files/finrobot_acks.csv)",
    )
    parser.add_argument(
        "--output-dir",
        default="state/research/parity/",
        help="Directory where run artifacts are written",
    )
    parser.add_argument(
        "--fill-tolerance-points",
        type=float,
        default=1.0,
        help="Maximum acceptable fill price difference",
    )
    parser.add_argument(
        "--bar-match-window",
        type=int,
        default=1,
        help="Maximum bar index distance for matching",
    )
    parser.add_argument(
        "--broker-time-zone",
        default="UTC",
        help="Timezone used to encode broker-wall acknowledgement timestamps",
    )
    parser.add_argument("--run-id", default="", help="Run id for output artifact names")
    parser.add_argument("--json", action="store_true", help="Print report JSON to stdout")
    return parser


def _resolve_acks_path(value: str | None) -> Path | None:
    if value:
        return Path(value)
    directory = common_dir()
    if directory is None:
        return None
    return directory / "finrobot_acks.csv"


def _timestamp_run_id() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H-%MZ")


def _report_dict(report: ParityReport) -> dict:
    return asdict(report)


def _markdown_summary(report: ParityReport) -> str:
    lines = [
        f"# Parity Replay {report.run_id}",
        "",
        "## Config",
        "",
        "```json",
        json.dumps(report.config, indent=2, sort_keys=True),
        "```",
        "",
        "## Summary",
        "",
        f"- Decisions: {report.n_decisions}",
        f"- Matched: {report.n_matched}",
        f"- Mismatched: {report.n_mismatched}",
        f"- Match rate: {report.match_rate:.4f}",
        f"- Filled: {report.n_filled} ({report.n_filled_matched} matched)",
        f"- Rejected: {report.n_rejected} ({report.n_rejected_matched} matched)",
        f"- Unmatched to bars: {report.n_unmatched}",
        "",
        "## Top Mismatches",
        "",
    ]
    if report.mismatches:
        for mismatch in report.mismatches[:10]:
            lines.append(
                "- "
                f"bar={mismatch.get('bar_idx')} "
                f"expected={mismatch.get('expected_action')} "
                f"got={mismatch.get('got_action')} "
                f"detail={mismatch.get('detail')}"
            )
    else:
        lines.append("- None")
    verdict = "PASS" if report.n_mismatched == 0 else "REVIEW"
    lines.extend(["", f"Verdict: {verdict}", ""])
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
