#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from aiquanttrader import data_store  # noqa: E402
from aiquanttrader.alerts import (  # noqa: E402
    Alert,
    alerts_to_dict,
    evaluate_alerts,
    exit_code_for,
)
from aiquanttrader.metrics import (  # noqa: E402
    compute_snapshot,
    get_pm2_restarts,
    snapshot_to_dict,
    write_snapshot,
)
from runtime_paths import common_dir  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Export metrics and evaluate alert rules."""
    parser = argparse.ArgumentParser(description="Export AIQuantTrader MT5 metrics.")
    parser.add_argument(
        "--warehouse",
        type=Path,
        default=data_store.DEFAULT_WAREHOUSE,
        help="DuckDB warehouse path",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "metrics.json",
        help="Output JSON path",
    )
    parser.add_argument(
        "--heartbeat-stale-seconds",
        type=int,
        default=60,
        help="Max live heartbeat age before it is stale",
    )
    parser.add_argument(
        "--freshness-window-seconds",
        type=int,
        default=300,
        help="Warehouse freshness window in host-clock seconds",
    )
    parser.add_argument(
        "--clock-skew-window-seconds",
        type=int,
        default=600,
        help="Clock skew median window in host-clock seconds",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON output")
    args = parser.parse_args(argv)

    con = data_store.connect(args.warehouse)
    try:
        data_store.init_schema(con)
        snap = compute_snapshot(
            con=con,
            common_dir=common_dir(),
            pm2_restarts=get_pm2_restarts("aiquanttrader-mt5"),
            heartbeat_stale_seconds=args.heartbeat_stale_seconds,
            freshness_window_seconds=args.freshness_window_seconds,
            clock_skew_window_seconds=args.clock_skew_window_seconds,
        )
    finally:
        con.close()

    alerts = evaluate_alerts(snap)
    code = exit_code_for(alerts)
    if args.json:
        payload = {"snapshot": snapshot_to_dict(snap), "alerts": alerts_to_dict(alerts)}
        _write_payload(payload, args.out)
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        write_snapshot(snap, args.out)
        _print_human(alerts, code)
    return code


def _print_human(alerts: list[Alert], code: int) -> None:
    counts = {"critical": 0, "warning": 0, "info": 0}
    for alert in alerts:
        severity = alert.severity.value
        counts[severity] += 1
        metric = ""
        if alert.metric_path:
            metric = f" ({alert.metric_path}={alert.metric_value!r})"
        print(f"[{severity.upper()}] {alert.name}: {alert.detail}{metric}")
    print(
        "metrics: "
        f"alerts={len(alerts)} "
        f"critical={counts['critical']} "
        f"warning={counts['warning']} "
        f"info={counts['info']} "
        f"exit={code}"
    )


def _write_payload(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
