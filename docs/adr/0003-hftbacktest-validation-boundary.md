# ADR 0003: Dual Backtest and Validation Boundary

Status: proposed; accepted when the migration blueprint PR is merged
Date: 2026-08-04

## Context

Market-making research needs latency, partial-fill, fee, and queue modeling.
Production validation also needs the exact strategy and risk code used by the
live NautilusTrader node. Hyperliquid publishes market-by-price snapshots, so
individual order queue position is not observable.

## Decision

Use two complementary validation paths:

1. HftBacktest for event replay, probabilistic queue models, latency, fees,
   funding, slippage, and adverse execution scenarios.
2. NautilusTrader backtest/sandbox for production strategy, portfolio, risk,
   order lifecycle, and event parity.

Strategies expose a shared, side-effect-free decision kernel. Simulator
adapters provide state and translate decisions. Promotion requires deterministic
reruns, purged walk-forward testing, an untouched final out-of-sample period,
stress scenarios, and parity between both paths within a versioned tolerance.

Queue position is always labeled as an estimate. Calibration uses our own
order acknowledgements, quote lifetime, book evolution, and fills. Candidates
must survive multiple queue assumptions, including deliberately pessimistic
ones.

## Alternatives considered

- HftBacktest only: stronger microstructure simulation but weaker production
  framework parity.
- NautilusTrader only: strong live parity but insufficient queue-model
  experimentation for passive BTC quoting.
- Treat displayed depth as known queue position: rejected as unsupported by the
  public market-by-price feed.

## Performance and validity consequences

- Research costs more compute because candidates run through two engines.
- Results are less sensitive to one simulator's optimistic assumptions.
- Large liquidity-taking orders require additional slippage stress because a
  replay simulator cannot change the historical market response.

## References

- [HftBacktest order fill models](https://hftbacktest.readthedocs.io/en/py-v2.1.0/order_fill.html)
- [Hyperliquid WebSocket subscriptions](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions)
