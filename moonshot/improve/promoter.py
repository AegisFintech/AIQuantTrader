"""Promote proposals to the live `runtime_overrides.json` file.

The daemon hot-reloads this file each iteration.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _atomic_write(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(path)


def load_current(overrides_path: Path) -> Dict[str, Any]:
    if not overrides_path.exists():
        return {}
    try:
        data = json.loads(overrides_path.read_text())
        return data.get("values", {}) if "values" in data else data
    except Exception as e:
        logger.warning("Failed to read overrides file %s: %s", overrides_path, e)
        return {}


def write_overrides(
    overrides_path: Path,
    values: Dict[str, Any],
    *,
    source: str = "self_improver",
    rationale: Optional[Dict[str, str]] = None,
) -> None:
    payload = {
        "updated_at": time.time(),
        "source": source,
        "values": values,
        "rationale": rationale or {},
    }
    _atomic_write(overrides_path, payload)


def append_journal(journal_path: Path, record: Dict[str, Any]) -> None:
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with journal_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def promote(
    overrides_path: Path,
    journal_path: Path,
    proposal: Dict[str, Any],
    backtest: Dict[str, Any],
    *,
    min_delta_expectancy: float = 0.0,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Apply proposed overrides if the backtest is acceptable."""
    overrides: List[Dict[str, Any]] = proposal.get("overrides", [])
    current = load_current(overrides_path)
    decision = {
        "applied": False,
        "reason": "no_overrides",
        "before": current,
        "proposal": proposal,
        "backtest": backtest,
        "timestamp": time.time(),
    }
    if not overrides:
        append_journal(journal_path, decision)
        return decision

    delta = backtest.get("delta_expectancy")
    if backtest.get("ran") and delta is not None and delta < min_delta_expectancy:
        decision["reason"] = f"backtest_regressed (delta={delta:+.4f})"
        append_journal(journal_path, decision)
        return decision

    new_values = dict(current)
    rationale: Dict[str, str] = {}
    for o in overrides:
        new_values[o["key"]] = o["value"]
        if o.get("rationale"):
            rationale[o["key"]] = o["rationale"]

    if dry_run:
        decision["applied"] = False
        decision["reason"] = "dry_run"
        decision["after"] = new_values
        append_journal(journal_path, decision)
        return decision

    write_overrides(overrides_path, new_values, rationale=rationale)
    decision["applied"] = True
    decision["reason"] = "promoted"
    decision["after"] = new_values
    append_journal(journal_path, decision)
    return decision


__all__ = ["promote", "load_current", "write_overrides", "append_journal"]
