#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import time
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from runtime_paths import common_dir

RETIRED_AUTO_STRATEGIES = {
    ('XAUUSD', 'RSI_reversion'),
}
SHADOW_POINT_SIZE = 0.01
SHADOW_TICK_SIZE = 0.01
SHADOW_TICK_VALUE = 1.0
SHADOW_COMMISSION_PER_SIDE_LOT = 3.5


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


def read_shadow_bars(path: Path) -> list[dict]:
    """Read the EA's no-header XAUUSD M1 export for shadow outcome resolution."""
    if not path.exists() or not path.stat().st_size:
        return []
    bars: list[dict] = []
    with path.open(errors='replace', newline='') as fh:
        for raw in csv.reader(fh, delimiter='\t'):
            if len(raw) < 6:
                continue
            timestamp = shadow_epoch(raw[0])
            if timestamp is None:
                continue
            try:
                bars.append(
                    {
                        'time': timestamp,
                        'open': float(raw[1]),
                        'high': float(raw[2]),
                        'low': float(raw[3]),
                        'close': float(raw[4]),
                        'volume': float(raw[5]),
                    }
                )
            except (TypeError, ValueError):
                continue
    return sorted(bars, key=lambda bar: bar['time'])


def shadow_epoch(value) -> int | None:
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        pass
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S', '%Y.%m.%d %H:%M:%S'):
        try:
            return int(datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return None


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


def resolve_shadow_signals(rows: list[dict], bars: list[dict]) -> list[dict]:
    """Resolve qualified paused-entry signals against later M1 SL/TP touches.

    Signals are evaluated independently. When one M1 bar touches both SL and TP,
    the stop is chosen to avoid optimistic intrabar ordering. Dynamic break-even
    activates only after a bar survives its existing exits, so ambiguous intrabar
    paths remain conservative. Logged entries already include the live spread;
    SELL exit triggers add the logged spread to bid-based MT5 bars. Round-trip
    ICMarkets demo commission is included.
    """
    ordered_bars = sorted(bars, key=lambda bar: int(bar['time']))
    bar_times = [int(bar['time']) for bar in ordered_bars]
    seen: set[str] = set()
    outcomes: list[dict] = []
    for row in sorted(rows, key=lambda item: money(item.get('ts_server'))):
        signal_id = str(row.get('signal_id') or '').strip()
        if not signal_id or signal_id in seen:
            continue
        seen.add(signal_id)
        side = str(row.get('side') or '').upper()
        signal_bar_time = shadow_epoch(row.get('signal_bar_time'))
        volume = money(row.get('volume'))
        entry = money(row.get('entry'))
        sl = money(row.get('sl'))
        tp = money(row.get('tp'))
        if side not in {'BUY', 'SELL'} or signal_bar_time is None or min(volume, entry, sl, tp) <= 0:
            continue

        spread_price = max(0.0, money(row.get('spread_points'))) * SHADOW_POINT_SIZE
        dynamic_break_even = str(row.get('dynamic_break_even') or '').strip().lower() in {
            '1',
            'true',
            'yes',
            'on',
        }
        break_even_rr_ratio = max(0.0, money(row.get('break_even_rr_ratio')))
        break_even_extra = max(0.0, money(row.get('break_even_extra_points'))) * SHADOW_POINT_SIZE
        initial_sl = sl
        active_sl = initial_sl
        initial_risk_distance = abs(entry - initial_sl)
        exit_price: float | None = None
        exit_time: int | None = None
        outcome = 'open'
        start = bisect_right(bar_times, signal_bar_time)
        for bar in ordered_bars[start:]:
            high = float(bar['high'])
            low = float(bar['low'])
            if side == 'SELL':
                high += spread_price
                low += spread_price
            stop_hit = low <= active_sl if side == 'BUY' else high >= active_sl
            target_hit = high >= tp if side == 'BUY' else low <= tp
            if stop_hit or target_hit:
                if stop_hit:
                    exit_price = active_sl
                    outcome = 'be' if abs(active_sl - initial_sl) > 1e-12 else 'sl'
                else:
                    exit_price = tp
                    outcome = 'tp'
                exit_time = int(bar['time'])
                break
            if dynamic_break_even and initial_risk_distance > 0 and break_even_rr_ratio > 0:
                activation = (
                    high >= entry + initial_risk_distance * break_even_rr_ratio
                    if side == 'BUY'
                    else low <= entry - initial_risk_distance * break_even_rr_ratio
                )
                if activation:
                    active_sl = (
                        max(active_sl, entry + break_even_extra)
                        if side == 'BUY'
                        else min(active_sl, entry - break_even_extra)
                    )

        result = dict(row)
        result.update({'outcome': outcome, 'exit_time': exit_time, 'exit_price': exit_price})
        if exit_price is None:
            result.update({'gross_pnl': None, 'commission': None, 'net_pnl': None, 'r_multiple': None})
        else:
            direction = 1.0 if side == 'BUY' else -1.0
            price_value_per_lot = SHADOW_TICK_VALUE / SHADOW_TICK_SIZE
            gross_pnl = (exit_price - entry) * direction * price_value_per_lot * volume
            commission = SHADOW_COMMISSION_PER_SIDE_LOT * 2.0 * volume
            net_pnl = gross_pnl - commission
            initial_risk = initial_risk_distance * price_value_per_lot * volume
            result.update(
                {
                    'gross_pnl': round(gross_pnl, 2),
                    'commission': round(commission, 2),
                    'net_pnl': round(net_pnl, 2),
                    'r_multiple': round(net_pnl / initial_risk, 4) if initial_risk > 0 else None,
                }
            )
        outcomes.append(result)
    return outcomes


def summarize_shadow_signals(rows: list[dict], bars: list[dict]) -> dict:
    outcomes = resolve_shadow_signals(rows, bars)

    def stats(items: list[dict]) -> dict:
        closed = [item for item in items if item.get('net_pnl') is not None]
        pnls = [float(item['net_pnl']) for item in closed]
        wins = [pnl for pnl in pnls if pnl > 0]
        losses = [pnl for pnl in pnls if pnl <= 0]
        if losses:
            profit_factor: float | str = round(sum(wins) / abs(sum(losses)), 4)
        elif wins:
            profit_factor = 'inf'
        else:
            profit_factor = 0.0
        return {
            'signals': len(items),
            'resolved': len(closed),
            'open': len(items) - len(closed),
            'wins': len(wins),
            'losses': len(losses),
            'win_rate': round(len(wins) / len(closed), 4) if closed else 0.0,
            'net_pnl': round(sum(pnls), 2),
            'profit_factor': profit_factor,
            'expectancy': round(sum(pnls) / len(closed), 4) if closed else 0.0,
        }

    by_strategy: dict[str, list[dict]] = defaultdict(list)
    for outcome in outcomes:
        key = f"{outcome.get('symbol') or '?'}:{outcome.get('profile') or 'compiled_defaults'}:{outcome.get('strategy') or '?'}"
        by_strategy[key].append(outcome)
    return {
        'total': stats(outcomes),
        'by_strategy': {key: stats(items) for key, items in sorted(by_strategy.items())},
        'assumptions': {
            'independent_signals': True,
            'both_hit_rule': 'stop_first',
            'commission_per_side_lot': SHADOW_COMMISSION_PER_SIDE_LOT,
            'slippage_points': 0.0,
            'dynamic_break_even_ordering': 'activate_after_surviving_bar',
        },
        'outcomes': outcomes,
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
    shadow_path = d / 'aiquanttrader_shadow_signals.csv'
    shadow_bars_path = d / 'aiquanttrader_export_XAUUSD_M1.tsv'

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
    shadow_rows = read_csv(shadow_path)
    shadow_bars = read_shadow_bars(shadow_bars_path)
    shadow = summarize_shadow_signals(shadow_rows, shadow_bars)
    print('Shadow trade summary:', json.dumps({key: value for key, value in shadow.items() if key != 'outcomes'}, indent=2))
    if shadow['outcomes']:
        print('Recent shadow outcomes:')
        for outcome in shadow['outcomes'][-10:]:
            print(' ', json.dumps(outcome, sort_keys=True))
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
