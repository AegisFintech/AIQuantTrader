#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean

from runtime_paths import common_dir

RETIRED_AUTO_STRATEGIES = {
    ('XAUUSD', 'RSI_reversion'),
}


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(errors='replace'))
    except Exception:
        return {}


def read_csv(path: Path) -> list[dict]:
    if not path.exists() or not path.stat().st_size:
        return []
    with path.open(errors='replace', newline='') as fh:
        return list(csv.DictReader(fh))


def money(v) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def summarize_deals(rows: list[dict]) -> dict:
    entries = defaultdict(list)
    exits = []
    for r in rows:
        if str(r.get('entry')) == '0':
            entries[r.get('position_id')].append(r)
        if str(r.get('entry')) in {'1', '3'} or money(r.get('profit')) != 0:
            exits.append(r)
    entry_costs: dict[str, float] = {}
    entry_volumes: dict[str, float] = {}
    for position_id, position_entries in entries.items():
        entry_costs[position_id] = sum(
            money(r.get('profit')) + money(r.get('commission')) + money(r.get('swap'))
            for r in position_entries
        )
        entry_volumes[position_id] = sum(money(r.get('volume')) for r in position_entries)
    by_symbol: dict[str, list[float]] = defaultdict(list)
    by_strategy: dict[str, list[float]] = defaultdict(list)
    by_day: dict[str, list[float]] = defaultdict(list)
    for r in exits:
        position_id = r.get('position_id')
        exit_pnl = money(r.get('profit')) + money(r.get('commission')) + money(r.get('swap'))
        entry_volume = entry_volumes.get(position_id, 0.0)
        entry_cost_share = 0.0
        if entry_volume > 0.0:
            entry_cost_share = entry_costs.get(position_id, 0.0) * min(1.0, money(r.get('volume')) / entry_volume)
        pnl = exit_pnl + entry_cost_share
        sym = r.get('symbol') or '?'
        by_symbol[sym].append(pnl)
        by_day[(r.get('time') or '')[:10]].append(pnl)
        entry = (entries.get(position_id) or [{}])[0]
        strategy = entry.get('comment') or 'UNKNOWN'
        by_strategy[f'{sym}:{strategy}'].append(pnl)
    def stats(pnls: list[float]) -> dict:
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        return {
            'n': len(pnls),
            'pnl': round(sum(pnls), 2),
            'win_rate': round(len(wins) / len(pnls), 4) if pnls else 0,
            'avg_win': round(mean(wins), 2) if wins else 0,
            'avg_loss': round(mean(losses), 2) if losses else 0,
            'expectancy': round(sum(pnls) / len(pnls), 4) if pnls else 0,
        }
    return {
        'closed_deals': len(exits),
        'total_pnl': round(sum(sum(v) for v in by_symbol.values()), 2),
        'by_symbol': {sym: stats(pnls) for sym, pnls in sorted(by_symbol.items())},
        'by_strategy': {name: stats(pnls) for name, pnls in sorted(by_strategy.items())},
        'by_day': {day: stats(pnls) for day, pnls in sorted(by_day.items()) if day},
    }


def retired_strategy_fills(lines: list[str], recent: int = 80) -> dict:
    counts: Counter[tuple[str, str]] = Counter()
    recent_hits: list[str] = []
    for row in csv.reader(lines[-recent:]):
        if len(row) < 8 or row[2] != 'AUTO_FILLED':
            continue
        symbol = row[4]
        detail = row[3]
        if ' strategy ' not in detail or ' smc=' not in detail:
            continue
        strategy = detail.split(' strategy ', 1)[1].split(' smc=', 1)[0].split()[-1]
        key = (symbol, strategy)
        if key in RETIRED_AUTO_STRATEGIES:
            counts[key] += 1
            recent_hits.append(','.join(row))
    return {
        'window_rows': recent,
        'counts': {f'{symbol}:{strategy}': n for (symbol, strategy), n in sorted(counts.items())},
        'recent': recent_hits[-10:],
    }


def main() -> None:
    d = common_dir()
    print(f'MT5 common dir: {d or "not found"}')
    if not d:
        return
    status_path = d / 'aiquanttrader_status.json'
    positions_path = d / 'aiquanttrader_positions.csv'
    deals_path = d / 'aiquanttrader_deals.csv'
    acks_path = d / 'aiquanttrader_acks.csv'

    status = read_json(status_path)
    if status:
        age = time.time() - status_path.stat().st_mtime
        print(f'Heartbeat age: {age:.1f}s')
        print(json.dumps(status, indent=2))
        mm = status.get('money_management') or {}
        if mm:
            print('Money management:', json.dumps(mm, indent=2))
    positions = read_csv(positions_path)
    print(f'Open managed positions: {len(positions)}')
    if positions:
        by_sym = defaultdict(float)
        for p in positions:
            by_sym[p.get('symbol') or '?'] += money(p.get('profit'))
        print('Open PnL by symbol:', dict(sorted((k, round(v, 2)) for k, v in by_sym.items())))
        for p in positions[-20:]:
            print('  POS', p)
    deals = read_csv(deals_path)
    print('Closed deal summary:', json.dumps(summarize_deals(deals), indent=2))
    if acks_path.exists():
        ack_lines = acks_path.read_text(errors='replace').splitlines()
        warnings = retired_strategy_fills(ack_lines)
        if warnings['counts']:
            print('Retired strategy fill warnings:', json.dumps(warnings, indent=2))
        print('Recent acknowledgements:')
        for line in ack_lines[-20:]:
            print(' ', line)


if __name__ == '__main__':
    main()
