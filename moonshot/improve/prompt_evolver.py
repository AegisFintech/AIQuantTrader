"""Prompt evolution.

Every N improvement cycles (default 12), look at recent decisions and
ask the LLM to refine the active system prompt. The new prompt is only
adopted if it actually changes meaningfully and parses cleanly. Old
prompts are archived as `system_vN.md` so we always have a rollback.

This is intentionally conservative: we never overwrite a known-good
prompt unless the LLM returns a non-empty new draft AND its diff size
is bounded (so it can't silently delete everything). A version pointer
lives in `state/moonshot/active_prompt.json`.
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from moonshot.improve.llm import LLMClient
from moonshot.improve.memory import ProposalMemory

logger = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).parent / "prompts"
ACTIVE_POINTER = "active_prompt.json"

EVOLUTION_INSTRUCTIONS = """You are reviewing and refining the system
prompt of an LLM strategist (`current_prompt` below). The strategist's
job is to propose JSON parameter overrides for a crypto trading bot.

Below you'll find:
  * the CURRENT prompt the strategist sees
  * RECENT decisions: each proposal, whether the offline backtest
    promoted or rejected it, and the delta_expectancy

Your job: return a REFINED prompt that should help the strategist make
fewer rejected proposals AND not repeat the same losing ideas. The
refined prompt MUST:
  1. preserve the original structure (sections, headings, the JSON
     output schema -- those are load-bearing for parsing)
  2. add or sharpen guidance about what NOT to do, based on the
     rejections you can see
  3. add or sharpen guidance about what DOES work, based on promoted
     proposals
  4. stay under 3500 words

Return STRICT JSON only, no markdown fences:
{
  "rationale": "short text explaining what you changed and why",
  "new_prompt": "the full refined system prompt as a single string"
}
"""


def load_active_pointer(state_dir: Path) -> Dict[str, Any]:
    p = state_dir / ACTIVE_POINTER
    if not p.exists():
        return {"active": "system_v1.md", "version": 1, "history": []}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {"active": "system_v1.md", "version": 1, "history": []}


def save_active_pointer(state_dir: Path, data: Dict[str, Any]) -> None:
    p = state_dir / ACTIVE_POINTER
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(p)


def active_prompt_text(state_dir: Path) -> str:
    ptr = load_active_pointer(state_dir)
    fname = ptr.get("active", "system_v1.md")
    fp = PROMPT_DIR / fname
    if not fp.exists():
        fp = PROMPT_DIR / "system_v1.md"
    return fp.read_text(encoding="utf-8")


def _safe_extract_json(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def evolve_prompt(
    state_dir: Path,
    memory: ProposalMemory,
    client: LLMClient,
    *,
    min_changed_chars: int = 80,
    max_chars: int = 30000,
) -> Dict[str, Any]:
    """Run one prompt-evolution cycle.

    Returns a dict describing what happened (so the orchestrator can log it).
    """
    ptr = load_active_pointer(state_dir)
    current = active_prompt_text(state_dir)

    if memory.stats().get("total", 0) < 4:
        return {"applied": False, "reason": "insufficient_memory"}

    recent = [e.short_dict() for e in memory.recent(20)]
    user_msg = (
        EVOLUTION_INSTRUCTIONS
        + "\n\n=== CURRENT PROMPT ===\n"
        + current
        + "\n\n=== RECENT DECISIONS (most recent last) ===\n"
        + json.dumps(recent, indent=2, default=str)
    )

    try:
        result = client.chat([
            {"role": "system", "content": "You refine LLM system prompts. Return strict JSON only."},
            {"role": "user", "content": user_msg},
        ], max_completion_tokens=6000)
    except Exception as e:
        return {"applied": False, "reason": f"llm_error: {e}"}

    parsed = _safe_extract_json(result.content)
    if not parsed:
        return {"applied": False, "reason": "unparseable_response", "raw_head": result.content[:200]}

    new_prompt = (parsed.get("new_prompt") or "").strip()
    rationale = (parsed.get("rationale") or "").strip()

    if not new_prompt:
        return {"applied": False, "reason": "empty_new_prompt"}
    if len(new_prompt) > max_chars:
        return {"applied": False, "reason": f"new_prompt_too_large ({len(new_prompt)})"}
    if abs(len(new_prompt) - len(current)) < min_changed_chars:
        return {"applied": False, "reason": "no_meaningful_change"}

    # Sanity: refuse to adopt if new prompt is missing critical markers
    required_markers = ["JSON", "proposals"]
    if not all(m in new_prompt for m in required_markers):
        return {"applied": False, "reason": "missing_required_markers"}

    new_version = int(ptr.get("version", 1)) + 1
    new_fname = f"system_v{new_version}.md"
    new_path = PROMPT_DIR / new_fname
    new_path.write_text(new_prompt, encoding="utf-8")

    ptr["history"] = (ptr.get("history") or []) + [{
        "ts": time.time(),
        "from": ptr.get("active"),
        "to": new_fname,
        "rationale": rationale[:500],
    }]
    ptr["active"] = new_fname
    ptr["version"] = new_version
    save_active_pointer(state_dir, ptr)

    logger.info(f"Prompt evolved: {ptr['history'][-1]['from']} -> {new_fname}  rationale={rationale[:140]}")
    return {
        "applied": True,
        "reason": "promoted",
        "from": ptr["history"][-1]["from"],
        "to": new_fname,
        "version": new_version,
        "rationale": rationale,
    }
