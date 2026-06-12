# BTC Data Sources

## TL;DR

Three viable sources can populate BTC M1 history for M2 backtests. MQL5 export, using the existing M1.5 path, is the best source for production parity because it comes from the same broker feed used by live trading. Synthetic data is useful for offline strategy development, CI smoke tests, and backfills where market realism is not required. Third-party APIs are a fallback for deeper history if the MT5 export is too sparse. Recommendation: use MQL5 export for the live XAU/BTC book once the MT5 export script runs for BTC; use synthetic for backfill on symbols we do not yet have MT5 history for.

## Source 1: MQL5 Export (M1.5 Path)

How it works: the EA writes `finrobot_export_BTCUSD_M1.tsv` to MT5 Common Files, and `scripts/harvest_mt5_export.py` ingests it into the warehouse.

Pros:

- Real broker bid/ask data.
- Exact same source as live trading.
- Strongest path for parity-friendly validation.

Cons:

- Requires the MT5 terminal and EA to be running.
- Export timing depends on the EA export path, either shutdown or on demand via `export_mt5_history`.
- Does not provide history before the EA was first attached.

Required for: live-parity validation, M3 walk-forward, and M4 challenger/incumbent comparison.

## Source 2: Synthetic (Random Walk)

How it works: `finrobot.prices.generate_synthetic_bars("BTCUSD", n_bars, ...)` creates deterministic OHLCV bars from a seeded random walk.

Pros:

- No broker dependency.
- Reproducible.
- Fast.
- Zero cost.

Cons:

- Does not reflect real market structure.
- Useless for parity checks.
- Only useful for offline strategy development and unit tests.

Required for: offline strategy development, CI smoke, and unit tests.

## Source 3: Third-Party API (TODO)

Options include Binance public kline API, CoinGecko, CryptoCompare, and similar market-data feeds.

Pros:

- Historical data can go back years.
- Feed formats are well known.

Cons:

- Adds third-party trust and rate-limit exposure.
- Bid/ask data is not always available.
- Not broker-aligned, so parity is weaker than MT5 export.

Required for: long-term walk-forward over more than one year, and strategy development when the MT5 export is sparse.

## Decision Matrix

| Source | Live parity? | Historical depth | Cost | Maintenance | Recommended for |
| --- | --- | --- | --- | --- | --- |
| MQL5 export | Yes | Limited to exported broker history | Existing MT5 runtime | Low, uses existing M1.5 path | Production parity, M3 walk-forward, M4 challenger/incumbent comparison |
| Synthetic | No | Arbitrary generated depth | Free | Low | Offline strategy development, CI smoke, unit tests |
| Third-party | Partial | Usually years | Usually free or low cost, with possible rate limits | Medium | Long-term walk-forward and sparse MT5-history fallback |

## Recommendation for #8

Use MQL5 export for production parity; keep synthetic as a CI / offline dev resource; defer third-party until the MT5 export proves insufficient, for example for year+ walk-forward.

## Procedure to Bootstrap BTC

Once the source is picked, run the matching CLI path:

```bash
python3 scripts/bootstrap_btc_history.py --source mt5-export
```

or:

```bash
python3 scripts/bootstrap_btc_history.py --source synthetic --n-bars 200000 --base-price 60000 --volatility 0.005 --seed 42
```

The CLI writes into the same `data/finrobot.duckdb` `prices` table used by the existing XAU history.

## Open Question for Aloy

The final data-source pick is still pending. The scaffolding is ready for all three paths; say the word and BTC M1 history can be bootstrapped with one CLI command.
