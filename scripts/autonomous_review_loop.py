#!/usr/bin/env python3
"""6-hour autonomous FinRobot review loop.

Policy:
- Review MT5 XAUUSD/BTCUSD performance and strategy memory every 6 hours.
- Require enough fresh closed trades before changing code/parameters.
- Ask Opencode to patch the repo directly when evidence supports an improvement.
- Keep the system MT5-demo-only until explicitly changed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
except Exception:
    pass
STATE = ROOT / 'state' / 'mt5'
MODEL = os.getenv('OPENCODE_REVIEW_MODEL', 'openai/gpt-5.5')


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {msg}"
    print(line, flush=True)


def fingerprint(payload: dict) -> str:
    import hashlib

    body = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(body.encode('utf-8')).hexdigest()[:16]


def append_journal(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(record, sort_keys=True) + '\n')


class ReviewMemory:
    def __init__(self, path: Path, limit: int = 500) -> None:
        self.path = path
        self.limit = limit
        self.entries = self._load()

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def add(self, entry: dict) -> None:
        self.entries.append(entry)
        self.entries = self.entries[-self.limit:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries, indent=2, sort_keys=True), encoding='utf-8')

    def recent(self, n: int) -> list[dict]:
        return self.entries[-n:]


def opencode_bin() -> str:
    candidates = [
        shutil.which('opencode'),
        '/home/openclaw/.npm-global/bin/opencode',
        '/home/openclaw/.npm-global/lib/node_modules/opencode-ai/node_modules/opencode-linux-x64/bin/opencode',
        '/home/openclaw/.npm-global/lib/node_modules/opencode-ai/node_modules/opencode-linux-x64-baseline/bin/opencode',
        '/home/openclaw/.npm-global/lib/node_modules/opencode-ai/node_modules/opencode-linux-x64-musl/bin/opencode',
        '/home/openclaw/.npm-global/lib/node_modules/opencode-ai/node_modules/opencode-linux-x64-baseline-musl/bin/opencode',
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return 'opencode'


def run(cmd: list[str], timeout: int = 1200) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=timeout, env={**os.environ, 'PATH': '/home/openclaw/.npm-global/bin:/home/openclaw/.npm-global/lib/node_modules/pm2/bin:' + os.environ.get('PATH', '')})


def mt5_report_text() -> str:
    cp = run([sys.executable, 'scripts/mt5_trade_report.py'], timeout=120)
    text = cp.stdout + '\n' + cp.stderr
    # Keep the head: the 'Closed deal summary:' marker sits near the start;
    # a trailing [-20000:] slice dropped it, so closed_deals always parsed as 0.
    return text if len(text) <= 200000 else text[:200000]


def opencode_review(memory: list[dict], mt5_report: str, dry_run: bool) -> dict:
    prompt = f"""
You are a senior HFT/quant trading engineer reviewing FinRobot.

Current mandate:
- Trade only two broker/demo MT5 symbols: XAUUSD and BTCUSD.
- Stay strictly on MT5 demo execution for XAUUSD and BTCUSD only.
- MT5 is demo-only unless the human explicitly changes that.
- Preserve the simple PM2 process layout.
- Use memory: keep what works, stop what does not, and do not repeat rejected ideas.

MT5 report:
{mt5_report[:20000]}

Recent strategy memory:
{json.dumps(memory, indent=2)[:12000]}

Task:
1. Inspect the repo.
2. Modify code/config/docs directly if there is a clear improvement for MT5 XAUUSD/BTCUSD.
3. Prefer simple parameter gates and symbol-specific rules before adding complexity.
4. Keep docs current for future agents.
5. Run syntax/tests or dry checks.
6. Respond with exactly what changed and why.
"""
    if dry_run:
        prompt = "DRY RUN: do not edit files. Review only.\n\n" + prompt
    cp = run([opencode_bin(), 'run', '--dir', str(ROOT), '--model', MODEL, '--dangerously-skip-permissions', prompt], timeout=7200)
    return {'returncode': cp.returncode, 'stdout': cp.stdout[-12000:], 'stderr': cp.stderr[-12000:]}


def cycle(args: argparse.Namespace) -> dict:
    mt5_report = mt5_report_text()
    closed = 0
    try:
        marker = 'Closed deal summary:'
        if marker in mt5_report:
            summary_text = mt5_report.split(marker, 1)[1]
            for next_marker in ('\nRetired strategy fill warnings:', '\nRecent acknowledgements:'):
                summary_text = summary_text.split(next_marker, 1)[0]
            summary = json.loads(summary_text.strip())
            closed = int(summary.get('closed_deals') or 0)
    except Exception:
        closed = 0
    n = closed
    memory = ReviewMemory(STATE / 'improver_memory.json')
    log(f"window={args.window_hours}h mt5_closed_deals={closed} min={args.min_trades}")
    if n < args.min_trades:
        rec = {'ts': time.time(), 'event': 'autonomous_review_skipped', 'reason': f'insufficient_trades {n}<{args.min_trades}', 'mt5_report': mt5_report[-8000:]}
        append_journal(STATE / 'improver_journal.jsonl', rec)
        memory.add({
            'ts': time.time(),
            'fingerprint': fingerprint({'event': 'mt5_review_skipped', 'closed_deals': closed}),
            'changes': {'event': 'mt5_review_skipped', 'closed_deals': closed},
            'rationale': rec['reason'],
            'decision': 'rejected',
            'reason': rec['reason'],
            'model': 'autonomous-review',
        })
        return {'applied': False, 'skipped': True, 'reason': rec['reason']}

    if os.getenv('AUTOREVIEW_ENABLE_LLM', '').strip().lower() not in ('1', 'true', 'yes', 'on'):
        log(f"llm_editing_disabled=1 closed_deals={n} (analysis-only; set AUTOREVIEW_ENABLE_LLM=true to allow edits)")
        rec = {'ts': time.time(), 'event': 'autonomous_review_analysis_only', 'closed_deals': n, 'llm_editing': 'disabled', 'mt5_report': mt5_report[-8000:]}
        append_journal(STATE / 'improver_journal.jsonl', rec)
        memory.add({
            'ts': time.time(),
            'fingerprint': fingerprint({'event': 'mt5_review_analysis_only', 'closed_deals': closed}),
            'changes': {'event': 'mt5_review_analysis_only', 'closed_deals': closed},
            'rationale': 'LLM editing disabled by AUTOREVIEW_ENABLE_LLM; recorded analysis only',
            'decision': 'rejected',
            'reason': 'llm_editing_disabled',
            'model': 'autonomous-review',
        })
        return {'applied': False, 'analysis_only': True, 'closed_deals': n}

    result = opencode_review(memory.recent(30), mt5_report, args.dry_run)
    append_journal(STATE / 'improver_journal.jsonl', {'ts': time.time(), 'event': 'autonomous_opencode_review', 'result': result})
    memory.add({
        'ts': time.time(),
        'fingerprint': fingerprint({'event': 'mt5_opencode_review', 'returncode': result['returncode']}),
        'changes': {'event': 'mt5_opencode_review', 'returncode': result['returncode']},
        'rationale': (result.get('stdout') or result.get('stderr') or '')[:500],
        'decision': 'promoted' if result['returncode'] == 0 else 'error',
        'reason': 'opencode_returncode=' + str(result['returncode']),
        'model': MODEL,
    })
    log(f"opencode_returncode={result['returncode']}")
    if result['returncode'] == 0 and not args.dry_run:
        checks = [
            run([sys.executable, '-m', 'compileall', '-q', 'finrobot', 'scripts'], timeout=300),
            run([sys.executable, 'scripts/mt5_trade_report.py'], timeout=120),
        ]
        ok = all(c.returncode == 0 for c in checks)
        log(f"post_checks_ok={ok}")
        if ok:
            pm2_bin = shutil.which('pm2') or '/home/openclaw/.npm-global/lib/node_modules/pm2/bin/pm2'
            run([pm2_bin, 'restart', 'mt5-terminal', 'autonomous-review', '--update-env'], timeout=300)
        return {'applied': ok, 'opencode': result}
    return {'applied': False, 'opencode': result}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--window-hours', type=float, default=float(os.getenv('AUTOREVIEW_WINDOW_HOURS', '6')))
    ap.add_argument('--interval-hours', type=float, default=float(os.getenv('AUTOREVIEW_INTERVAL_HOURS', '6')))
    ap.add_argument('--min-trades', type=int, default=int(os.getenv('AUTOREVIEW_MIN_TRADES', '12')))
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()
    run_on_start = os.getenv('AUTOREVIEW_RUN_ON_START', 'false').lower() in ('1', 'true', 'yes', 'on')
    if not run_on_start and not args.once:
        sleep_s = max(3600, args.interval_hours * 3600)
        log(f'startup_delay_seconds={sleep_s:.0f}')
        time.sleep(sleep_s)
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
