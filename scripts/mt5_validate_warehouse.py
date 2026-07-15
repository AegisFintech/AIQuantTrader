#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aiquanttrader import data_store  # noqa: E402
from aiquanttrader.validators import Issue, Severity, validate_warehouse  # noqa: E402


def issue_dict(issue: Issue) -> dict:
    """Return a JSON-serializable issue dictionary."""
    data = asdict(issue)
    data["severity"] = issue.severity.value
    return data


def print_human(issues: list[Issue], warehouse: Path) -> None:
    """Print validation issues grouped by severity and check name."""
    errors = [issue for issue in issues if issue.severity == Severity.ERROR]
    warnings = [issue for issue in issues if issue.severity == Severity.WARNING]
    for severity, group in ((Severity.ERROR, errors), (Severity.WARNING, warnings)):
        if not group:
            continue
        print(severity.value.upper())
        by_check: dict[str, list[Issue]] = defaultdict(list)
        for issue in group:
            by_check[issue.check].append(issue)
        for check in sorted(by_check):
            check_issues = by_check[check]
            print(f"  {check}: count={len(check_issues)}")
            for issue in check_issues[:5]:
                line = f"    - {issue.location}: {issue.detail}"
                if issue.suggestion:
                    line += f" suggestion={issue.suggestion}"
                print(line)
    state = "clean" if not issues else "dirty"
    print(f"issues: errors={len(errors)} warnings={len(warnings)} warehouse={state}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the AIQuantTrader DuckDB warehouse.")
    parser.add_argument("--warehouse", type=Path, default=data_store.DEFAULT_WAREHOUSE)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    warehouse = args.warehouse
    if not warehouse.exists():
        message = f"warehouse file does not exist: {warehouse}"
        if args.json:
            print(json.dumps({"error": message}))
        else:
            print(message, file=sys.stderr)
        return 2

    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        issues = validate_warehouse(con)
    finally:
        con.close()

    if args.json:
        print(json.dumps([issue_dict(issue) for issue in issues], indent=2, sort_keys=True))
    else:
        print_human(issues, warehouse)
    return 1 if any(issue.severity == Severity.ERROR for issue in issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
