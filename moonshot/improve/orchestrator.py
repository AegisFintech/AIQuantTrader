"""Self-improvement orchestrator loop.

Runs forever (or for --cycles N) doing:
  analyze -> propose -> backtest -> promote -> sleep
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from moonshot.improve.analyzer import build_report
from moonshot.improve.backtest import evaluate as backtest_evaluate
from moonshot.improve.llm import LLMClient
from moonshot.improve.memory import ProposalMemory, MemoryEntry, fingerprint
from moonshot.improve.promoter import append_journal, load_current, promote
from moonshot.improve.proposer import propose
from moonshot.improve.prompt_evolver import (
    active_prompt_text, evolve_prompt, load_active_pointer,
)
from typing import Optional, Dict, Any

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "improver.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("improver")


def cycle(
    state_dir: Path,
    overrides_path: Path,
    journal_path: Path,
    proposals_dir: Path,
    *,
    model: str = "",
    window_hours: float = 24.0,
    min_trades: int = 8,
    dry_run: bool = False,
    memory: Optional[ProposalMemory] = None,
    cycle_id: Optional[int] = None,
) -> Dict[str, Any]:
    proposals_dir.mkdir(parents=True, exist_ok=True)
    if memory is None:
        memory = ProposalMemory(state_dir / "improver_memory.json")
    logger.info("--- improver cycle %s start ---", cycle_id if cycle_id is not None else "?")
    report = build_report(state_dir, window_hours=window_hours)
    overall_n = (report.overall or {}).get("n", 0)
    logger.info(
        "Report: window=%.1fh n=%s win_rate=%s expectancy=%s",
        window_hours, overall_n,
        (report.overall or {}).get("win_rate"),
        (report.overall or {}).get("expectancy"),
    )
    if overall_n < min_trades:
        logger.info("Skipping: not enough trades (%s < %s)", overall_n, min_trades)
        append_journal(journal_path, {
            "ts": time.time(),
            "skipped": True,
            "reason": f"min_trades_not_met ({overall_n}<{min_trades})",
            "report": report.to_dict(),
        })
        return {"applied": False, "skipped": True, "reason": "insufficient_data"}

    current_overrides = load_current(overrides_path)
    client = LLMClient(model=model or None)
    ptr = load_active_pointer(state_dir)
    sys_prompt = active_prompt_text(state_dir)
    recent_hist = [e.short_dict() for e in memory.recent()]
    logger.info(
        "Asking %s for proposals | prompt=%s recent_attempts=%d",
        client.model, ptr.get("active"), len(recent_hist),
    )
    proposal = propose(
        report, current_overrides, client=client,
        recent_history=recent_hist,
        active_prompt_text=sys_prompt,
    )
    logger.info("Proposal: %d overrides, diagnosis=%s",
                len(proposal.overrides),
                (proposal.diagnosis or {}).get("summary", "")[:200])

    # Fingerprint the proposed change-set as a flat key->value dict
    changes_flat = {o["key"]: o["value"] for o in proposal.overrides}
    fp = fingerprint(changes_flat)
    duplicate = memory.is_recently_rejected(fp)
    if duplicate and not dry_run:
        logger.info(
            "Proposal fingerprint %s was already rejected at %s (%s). Skipping.",
            fp, duplicate.ts, duplicate.reason,
        )
        memory.add(MemoryEntry(
            ts=time.time(), fingerprint=fp, changes=changes_flat,
            rationale=(proposal.diagnosis or {}).get("summary", ""),
            decision="skipped_duplicate",
            reason=f"matches_recent_rejected:{duplicate.fingerprint}",
            delta_expectancy=None,
            prompt_version=ptr.get("active"),
            model=client.model,
            cycle_id=cycle_id,
        ))
        append_journal(journal_path, {
            "ts": time.time(), "applied": False, "reason": "skipped_duplicate",
            "fingerprint": fp, "matches": duplicate.fingerprint,
        })
        return {"applied": False, "reason": "skipped_duplicate", "fingerprint": fp}

    # Backtest
    bt = backtest_evaluate(
        trades_path=state_dir / "trades.jsonl",
        proposed_overrides=proposal.overrides,
        current_overrides=current_overrides,
        lookback_hours=int(max(24.0, window_hours)),
    )
    logger.info("Backtest: %s", json.dumps(bt))

    # Save proposal artifact
    ts_tag = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    artifact = proposals_dir / f"proposal_{ts_tag}.json"
    artifact.write_text(json.dumps({
        "report": report.to_dict(),
        "proposal": proposal.to_dict(),
        "backtest": bt,
        "current_overrides": current_overrides,
        "fingerprint": fp,
        "prompt_version": ptr.get("active"),
    }, indent=2, default=str))
    logger.info("Wrote proposal artifact %s", artifact)

    decision = promote(
        overrides_path=overrides_path,
        journal_path=journal_path,
        proposal=proposal.to_dict(),
        backtest=bt,
        dry_run=dry_run,
    )
    logger.info("Decision: %s (%s)", decision.get("applied"), decision.get("reason"))

    memory.add(MemoryEntry(
        ts=time.time(),
        fingerprint=fp,
        changes=changes_flat,
        rationale=(proposal.diagnosis or {}).get("summary", ""),
        decision="promoted" if decision.get("applied") else "rejected",
        reason=str(decision.get("reason", ""))[:200],
        delta_expectancy=bt.get("delta_expectancy"),
        prompt_version=ptr.get("active"),
        model=client.model,
        cycle_id=cycle_id,
    ))

    return decision


def main() -> None:
    parser = argparse.ArgumentParser(description="Moonshot self-improver loop")
    parser.add_argument("--state-dir", default=str(ROOT / "state" / "moonshot"))
    parser.add_argument("--overrides", default=str(ROOT / "state" / "moonshot" / "runtime_overrides.json"))
    parser.add_argument("--journal", default=str(ROOT / "state" / "moonshot" / "improver_journal.jsonl"))
    parser.add_argument("--proposals", default=str(ROOT / "state" / "moonshot" / "proposals"))
    parser.add_argument("--window-hours", type=float, default=float(os.getenv("IMPROVER_WINDOW_HOURS", "24")))
    parser.add_argument("--interval-minutes", type=float, default=float(os.getenv("IMPROVER_INTERVAL_MINUTES", "60")))
    parser.add_argument("--min-trades", type=int, default=int(os.getenv("IMPROVER_MIN_TRADES", "8")))
    parser.add_argument("--cycles", type=int, default=0, help="0 = run forever")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--model", default=os.getenv("IMPROVER_MODEL", "gpt-5.5"))
    parser.add_argument(
        "--prompt-evolve-every", type=int,
        default=int(os.getenv("IMPROVER_PROMPT_EVOLVE_EVERY", "12")),
        help="Run prompt evolution every N cycles (0 = never)",
    )
    args = parser.parse_args()

    state_dir = Path(args.state_dir)
    overrides_path = Path(args.overrides)
    journal_path = Path(args.journal)
    proposals_dir = Path(args.proposals)

    memory = ProposalMemory(state_dir / "improver_memory.json")
    logger.info(
        "Improver starting | model=%s window=%.1fh interval=%.1fmin memory=%s prompt_evolve_every=%d",
        args.model, args.window_hours, args.interval_minutes,
        memory.stats(), args.prompt_evolve_every,
    )

    n = 0
    while True:
        n += 1
        try:
            cycle(
                state_dir, overrides_path, journal_path, proposals_dir,
                model=args.model,
                window_hours=args.window_hours,
                min_trades=args.min_trades,
                dry_run=args.dry_run,
                memory=memory,
                cycle_id=n,
            )
        except Exception as e:
            logger.exception("Cycle failed: %s", e)
            append_journal(journal_path, {
                "ts": time.time(),
                "error": str(e),
            })

        if args.prompt_evolve_every and n % args.prompt_evolve_every == 0:
            try:
                logger.info("Triggering prompt evolution (cycle %d)", n)
                ev = evolve_prompt(state_dir, memory, LLMClient(model=args.model))
                logger.info("Prompt evolution result: %s", json.dumps(ev, default=str))
                append_journal(journal_path, {
                    "ts": time.time(),
                    "event": "prompt_evolution",
                    "result": ev,
                })
            except Exception as e:
                logger.exception("Prompt evolution failed: %s", e)

        if args.once or args.cycles and n >= args.cycles:
            break
        sleep_s = max(60.0, args.interval_minutes * 60.0)
        logger.info("Sleeping %.0fs until next cycle…", sleep_s)
        time.sleep(sleep_s)


if __name__ == "__main__":
    main()
