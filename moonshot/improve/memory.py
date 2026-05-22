"""Improver memory.

Tracks the fingerprint, summary, and outcome of every proposal so the
LLM can be shown what was already tried and is filtered against silly
repeats. Memory persists at `state/moonshot/improver_memory.json` and
is bounded to the last MAX_ENTRIES proposals (LRU by timestamp).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MAX_ENTRIES = 200
RECENT_FOR_PROMPT = 25
DEDUP_LOOKBACK = 60


def fingerprint(changes: Dict[str, Any]) -> str:
    """Stable hash of a proposal's `changes` dict (numeric values rounded)."""
    def _norm(v):
        if isinstance(v, float):
            return round(v, 4)
        if isinstance(v, bool):
            return bool(v)
        if isinstance(v, (int, str)):
            return v
        return str(v)
    canon = {str(k): _norm(v) for k, v in (changes or {}).items()}
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha1(blob).hexdigest()[:12]


@dataclass
class MemoryEntry:
    ts: float
    fingerprint: str
    changes: Dict[str, Any]
    rationale: str
    decision: str            # "promoted" | "rejected" | "skipped_duplicate" | "error"
    reason: str              # short reason text
    delta_expectancy: Optional[float] = None
    prompt_version: Optional[str] = None
    model: Optional[str] = None
    cycle_id: Optional[int] = None

    def short_dict(self) -> Dict[str, Any]:
        d = {
            "ts": self.ts,
            "decision": self.decision,
            "reason": self.reason,
            "delta_expectancy": self.delta_expectancy,
            "changes": self.changes,
            "rationale": self.rationale[:200] if self.rationale else "",
            "fingerprint": self.fingerprint,
        }
        return d


class ProposalMemory:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.entries: List[MemoryEntry] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            self.entries = [MemoryEntry(**e) for e in data.get("entries", [])]
        except Exception as e:
            logger.warning(f"Failed to load proposal memory at {self.path}: {e}")
            self.entries = []

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            payload = {"entries": [asdict(e) for e in self.entries[-MAX_ENTRIES:]]}
            tmp.write_text(json.dumps(payload, indent=2))
            tmp.replace(self.path)
        except Exception as e:
            logger.error(f"Failed to save proposal memory: {e}")

    def add(self, entry: MemoryEntry) -> None:
        self.entries.append(entry)
        if len(self.entries) > MAX_ENTRIES:
            self.entries = self.entries[-MAX_ENTRIES:]
        self.save()

    def recent(self, n: int = RECENT_FOR_PROMPT) -> List[MemoryEntry]:
        return self.entries[-n:]

    def is_recently_rejected(self, fp: str, lookback: int = DEDUP_LOOKBACK) -> Optional[MemoryEntry]:
        for e in reversed(self.entries[-lookback:]):
            if e.fingerprint == fp and e.decision in ("rejected", "skipped_duplicate", "error"):
                return e
        return None

    def stats(self) -> Dict[str, Any]:
        if not self.entries:
            return {"total": 0}
        promoted = sum(1 for e in self.entries if e.decision == "promoted")
        rejected = sum(1 for e in self.entries if e.decision == "rejected")
        dupes    = sum(1 for e in self.entries if e.decision == "skipped_duplicate")
        deltas   = [e.delta_expectancy for e in self.entries if e.delta_expectancy is not None]
        avg_delta = sum(deltas) / len(deltas) if deltas else None
        return {
            "total": len(self.entries),
            "promoted": promoted,
            "rejected": rejected,
            "skipped_duplicate": dupes,
            "promotion_rate": round(promoted / len(self.entries), 3),
            "avg_delta_expectancy": round(avg_delta, 5) if avg_delta is not None else None,
        }
