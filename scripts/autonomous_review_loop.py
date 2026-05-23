#!/usr/bin/env python3
"""6-hour autonomous FinRobot review loop.

Policy:
- Require at least 20 closed trades in the last window before changing code/parameters.
- Preserve memory of promoted/rejected changes.
- Ask Opencode using the configured GPT-5.5 model to modify code locally.
- Restart only the simple PM2-managed processes after successful checks.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'state' / 'moonshot'
LOG = ROOT / 'logs' / 'autonomous_review.log'
MODEL = os.getenv('OPENCODE_REVIEW_MODEL', 'openai/gpt-5.5')

sys.path.insert(0, str(ROOT))
from moonshot.improve.analyzer import build_report
from moonshot.improve.memory import ProposalMemory
from moonshot.improve.promoter import append_journal


def log(msg: str) -> None:
    LOG.parent.mkdir(exist_ok=True)
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {msg}"
    print(line, flush=True)
    with LOG.open('a') as f:
        f.write(line + '\n')


def run(cmd: list[str], timeout: int = 1200) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=timeout)


def opencode_review(report: dict, memory: list[dict], dry_run: bool) -> dict:
    prompt = f"""
You are a senior HFT/quant trading engineer reviewing FinRobot.

Goal: improve aggressive-growth paper trading, but do not overfit. Current universe: XAUUSD plus crypto. Architecture target: Python brain + MT5 execution bridge for brokerage-realistic spread/commission/slippage; Hyperliquid paper trading remains active until MT5 bridge is fully validated.

Hard rules:
- If a strategy or strategy+coin/regime has bad expectancy/profit factor/drawdown over enough trades, stop or reduce it.
- Good strategies continue or get slightly more allocation.
- Do not reintroduce ideas that memory says were rejected.
- Favor simple, observable patches. Update docs when behavior changes.
- Keep safety guards: no live-real-money execution; MT5 is demo-only until explicitly changed.
- Preserve process simplicity: PM2 processes only.

Recent memory:
{json.dumps(memory, indent=2)[:12000]}

Performance report:
{json.dumps(report, indent=2)[:20000]}

Task:
1. Inspect the repo.
2. Modify code/config/docs if there is a clear improvement.
3. Prefer parameter gating/strategy disable lists before adding complex new strategies.
4. Run relevant syntax/tests or dry checks.
5. Respond with exactly what changed and why.
"""
    if dry_run:
        prompt = "DRY RUN: do not edit files. Review only.\n\n" + prompt
    cp = run(['opencode', 'run', '--dir', str(ROOT), '--model', MODEL, '--dangerously-skip-permissions', prompt], timeout=3600)
    return {'returncode': cp.returncode, 'stdout': cp.stdout[-12000:], 'stderr': cp.stderr[-12000:]}


def cycle(args: argparse.Namespace) -> dict:
    report = build_report(STATE, window_hours=args.window_hours).to_dict()
    n = int((report.get('overall') or {}).get('n') or 0)
    memory = ProposalMemory(STATE / 'improver_memory.json')
    log(f"window={args.window_hours}h closed_trades={n} min={args.min_trades}")
    if n < args.min_trades:
        rec = {'ts': time.time(), 'event': 'autonomous_review_skipped', 'reason': f'insufficient_trades {n}<{args.min_trades}', 'report': report}
        append_journal(STATE / 'improver_journal.jsonl', rec)
        return {'applied': False, 'skipped': True, 'reason': rec['reason']}

    result = opencode_review(report, [e.short_dict() for e in memory.recent(30)], args.dry_run)
    append_journal(STATE / 'improver_journal.jsonl', {'ts': time.time(), 'event': 'autonomous_opencode_review', 'result': result})
    log(f"opencode_returncode={result['returncode']}")
    if result['returncode'] == 0 and not args.dry_run:
        checks = [
            run([sys.executable, '-m', 'compileall', '-q', 'moonshot', 'finrobot', 'scripts'], timeout=300),
            run([sys.executable, 'scripts/moonshot_health_check.py'], timeout=300),
        ]
        ok = all(c.returncode == 0 for c in checks)
        log(f"post_checks_ok={ok}")
        if ok:
            run(['runuser', '-l', 'openclaw', '-c', 'cd /home/openclaw/FinRobot && pm2 restart moonshot-daemon moonshot-dashboard'], timeout=300)
        return {'applied': ok, 'opencode': result}
    return {'applied': False, 'opencode': result}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--window-hours', type=float, default=float(os.getenv('AUTOREVIEW_WINDOW_HOURS', '6')))
    ap.add_argument('--interval-hours', type=float, default=float(os.getenv('AUTOREVIEW_INTERVAL_HOURS', '6')))
    ap.add_argument('--min-trades', type=int, default=int(os.getenv('AUTOREVIEW_MIN_TRADES', '20')))
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    while True:
        try:
            cycle(args)
        except Exception as e:
            log(f"cycle_error={e!r}")
        if args.once:
            break
        sleep_s = max(3600, args.interval_hours * 3600)
        log(f"sleeping_seconds={sleep_s:.0f}")
        time.sleep(sleep_s)

if __name__ == '__main__':
    main()
