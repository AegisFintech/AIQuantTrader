"""Operator CLI for deterministic conversion and validation planning."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from aiquanttrader_native.backtest.conversion import (
    convert_normalized_dataset,
    convert_tardis_day,
    load_event_file,
)
from aiquanttrader_native.backtest.scenarios import load_scenario, load_validation_policy
from aiquanttrader_native.backtest.validation import plan_walk_forward


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aqt-backtest")
    commands = parser.add_subparsers(dest="command", required=True)

    scenario = commands.add_parser("validate-scenario")
    scenario.add_argument("--scenario", type=Path, required=True)

    tardis = commands.add_parser("convert-tardis")
    tardis.add_argument("--source-root", type=Path, required=True)
    tardis.add_argument("--input", type=Path, action="append", required=True)
    tardis.add_argument("--output-root", type=Path, required=True)
    tardis.add_argument("--event-path", type=Path, required=True)

    normalized = commands.add_parser("convert-normalized")
    normalized.add_argument("--data-root", type=Path, required=True)
    normalized.add_argument("--dataset-manifest", type=Path, required=True)
    normalized.add_argument("--normalized-manifest", type=Path, action="append", required=True)
    normalized.add_argument("--output-root", type=Path, required=True)
    normalized.add_argument("--event-path", type=Path, required=True)

    plan = commands.add_parser("plan-validation")
    plan.add_argument("--events", type=Path, required=True)
    plan.add_argument("--dataset-sha256", required=True)
    plan.add_argument("--policy", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "validate-scenario":
            scenario = load_scenario(args.scenario)
            print(
                json.dumps(
                    {
                        "scenario_id": scenario.scenario_id,
                        "scenario_sha256": scenario.sha256(),
                        "calibration_state": scenario.calibration_state,
                        "promotion_eligible": scenario.calibration_sha256 is not None,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "convert-tardis":
            manifest_path, manifest = convert_tardis_day(
                source_root=args.source_root,
                input_files=args.input,
                output_root=args.output_root,
                event_path=args.event_path,
            )
        elif args.command == "convert-normalized":
            manifest_path, manifest = convert_normalized_dataset(
                data_root=args.data_root,
                dataset_manifest_path=args.dataset_manifest,
                normalized_manifest_paths=args.normalized_manifest,
                output_root=args.output_root,
                event_path=args.event_path,
            )
        elif args.command == "plan-validation":
            events = load_event_file(args.events)
            plan = plan_walk_forward(
                dataset_sha256=args.dataset_sha256,
                start_ts_ns=int(events["local_ts"].min()),
                end_ts_ns=int(events["local_ts"].max()) + 1,
                policy=load_validation_policy(args.policy),
            )
            print(plan.model_dump_json(indent=2))
            return 0
        else:
            raise RuntimeError(f"unhandled command: {args.command}")
        print(
            json.dumps(
                {
                    "manifest": str(manifest_path),
                    "dataset_id": manifest.dataset_id,
                    "event_count": manifest.event_count,
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
