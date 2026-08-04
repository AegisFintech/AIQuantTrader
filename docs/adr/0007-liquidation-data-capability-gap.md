# ADR 0007: Liquidation Data Capability Boundary

Status: proposed; accepted when the migration blueprint PR is merged
Date: 2026-08-04

## Context

The target requirements include liquidation ingestion. Hyperliquid documents
private user liquidation and ADL events, but its public subscription set and
Tardis Hyperliquid coverage do not document a market-wide liquidation stream.
Open interest changes or large trades are not proof of a liquidation.

## Decision

Version one implements a typed `LiquidationSource` boundary with:

- an account source for authenticated liquidation and ADL events;
- a mandatory `market_wide_liquidations_available=false` capability metric and
  dataset field when no approved source exists;
- no inferred or synthetic events labeled as observed liquidations;
- no production strategy whose required feature set depends on market-wide
  liquidation data.

Adding a third-party feed or on-chain decoder requires a new ADR, licensing and
retention review, timestamp and completeness analysis, historical/live parity,
and failure-mode tests. Until that source passes validation, its features remain
research-only and nullable.

## Alternatives considered

- Infer liquidations from price, volume, or open-interest shocks: rejected
  because it creates unverified labels and look-alike events.
- Scrape an undocumented endpoint: rejected because reliability, terms, and
  schemas cannot be governed.
- Block the entire migration: rejected because market making and order-flow
  strategies do not require this optional feature for a safe baseline.

## Consequences

- The capability gap is visible rather than hidden.
- Account safety events are still captured and monitored.
- Research cannot accidentally train on fabricated liquidation truth.

## References

- [Hyperliquid WebSocket subscriptions](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions)
- [Tardis Hyperliquid coverage](https://docs.tardis.dev/historical-data-details/hyperliquid)
