"""Build the LLM prompt and parse its proposals safely."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from moonshot.improve.analyzer import PerformanceReport
from moonshot.improve.llm import LLMClient
from moonshot.improve.prompts import load as load_prompt

logger = logging.getLogger(__name__)


WHITELIST: Dict[str, Dict[str, Any]] = {
    "min_confidence":             {"type": float, "min": 0.50, "max": 0.95},
    "max_open_positions":         {"type": int,   "min": 1,    "max": 4},
    "trade_cooldown_seconds":     {"type": int,   "min": 0,    "max": 300},
    "chandelier_atr_mult":        {"type": float, "min": 1.5,  "max": 6.0},
    "partial_tp_rr":              {"type": float, "min": 0.8,  "max": 2.5},
    "partial_tp_pct":             {"type": float, "min": 0.10, "max": 0.80},
    "atr_sl_mult":                {"type": float, "min": 3.0,  "max": 12.0},
    "atr_tp_mult":                {"type": float, "min": 4.0,  "max": 18.0},
    "atr_trail_mult":             {"type": float, "min": 2.0,  "max": 10.0},
    "regime_gate_enabled":        {"type": bool},
    "regime_gate_min_expected_r": {"type": float, "min": 0.0,  "max": 0.5},
    "regime_gate_min_wr":         {"type": float, "min": 0.30, "max": 0.65},
    "adaptive_exits_enabled":     {"type": bool},
    "early_profit_exit":          {"type": bool},
    "use_atr_sl_tp":              {"type": bool},
}


@dataclass
class Proposal:
    overrides: List[Dict[str, Any]]
    diagnosis: Dict[str, Any]
    research_questions: List[str]
    next_strategy_ideas: List[Dict[str, Any]]
    raw: Dict[str, Any]
    model: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overrides": self.overrides,
            "diagnosis": self.diagnosis,
            "research_questions": self.research_questions,
            "next_strategy_ideas": self.next_strategy_ideas,
            "model": self.model,
        }


def _coerce(value: Any, spec: Dict[str, Any]) -> Any:
    t = spec["type"]
    if t is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        raise ValueError(f"cannot coerce {value!r} to bool")
    val = t(value)
    if "min" in spec and val < spec["min"]:
        raise ValueError(f"value {val} below min {spec['min']}")
    if "max" in spec and val > spec["max"]:
        raise ValueError(f"value {val} above max {spec['max']}")
    return val


def _validate_overrides(raw_overrides: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    clean: List[Dict[str, Any]] = []
    for entry in raw_overrides or []:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        if key not in WHITELIST:
            logger.info("Dropping override: unknown key %r", key)
            continue
        spec = WHITELIST[key]
        try:
            value = _coerce(entry.get("value"), spec)
        except Exception as e:
            logger.info("Dropping override %s: %s", key, e)
            continue
        clean.append({
            "key": key,
            "value": value,
            "rationale": (entry.get("rationale") or "")[:500],
        })
    return clean


def propose(
    report: PerformanceReport,
    current_overrides: Dict[str, Any],
    client: Optional[LLMClient] = None,
    *,
    extra_context: Optional[str] = None,
    recent_history: Optional[List[Dict[str, Any]]] = None,
    active_prompt_text: Optional[str] = None,
) -> Proposal:
    """Ask the LLM for a fresh set of overrides given a perf report."""
    client = client or LLMClient()
    system_prompt = active_prompt_text or load_prompt("system_v1.md")

    user_payload: Dict[str, Any] = {
        "performance_report": report.to_dict(),
        "current_runtime_overrides": current_overrides,
        "recent_attempts": recent_history or [],
    }
    if extra_context:
        user_payload["operator_notes"] = extra_context

    avoid_fps = [
        h.get("fingerprint")
        for h in (recent_history or [])
        if h.get("decision") not in ("promoted",) and h.get("fingerprint")
    ]
    avoid_clause = ""
    if avoid_fps:
        avoid_clause = (
            "\n\nIMPORTANT: do NOT re-propose any change-set whose fingerprint "
            "appears in `recent_attempts` with decision != 'promoted'. Those "
            "fingerprints are: " + ", ".join(avoid_fps[:30]) + "."
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                "Here is the latest performance snapshot AND the most recent "
                "things we have already tried. Reply with the JSON object "
                "described in the system prompt. Do NOT include any prose "
                "outside the JSON." + avoid_clause + "\n\n```json\n"
                + json.dumps(user_payload, indent=2, default=str)
                + "\n```"
            ),
        },
    ]
    raw = client.chat_json(messages, max_completion_tokens=4096)
    overrides = _validate_overrides(raw.get("overrides") or [])
    diagnosis = raw.get("diagnosis") or {}
    if not isinstance(diagnosis, dict):
        diagnosis = {"summary": str(diagnosis)}
    return Proposal(
        overrides=overrides,
        diagnosis=diagnosis,
        research_questions=list(raw.get("research_questions") or []),
        next_strategy_ideas=list(raw.get("next_strategy_ideas") or []),
        raw=raw,
        model=client.model,
    )


__all__ = ["Proposal", "WHITELIST", "propose"]
