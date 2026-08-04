# ADR 0005: Synchronous Risk Authority and Layered Kill Switches

Status: proposed; accepted when the migration blueprint PR is merged
Date: 2026-08-04

## Context

A 24/7 market maker can accumulate inventory and resting orders during stale
data, exchange disconnection, software failure, or strategy instability. Risk
must not be advisory or controlled by the strategy that it limits.

## Decision

The trading node contains a synchronous risk authority with states `ACTIVE`,
`REDUCE_ONLY`, `CANCEL_ONLY/HALTED`, and `FLATTENING`. Every exposure-increasing
command requires an approval token tied to the current risk snapshot. The most
restrictive rule wins.

Layered controls are:

1. strategy-local inventory targeting;
2. independent pre-trade order, position, inventory, leverage, margin, loss,
   drawdown, and open-order limits;
3. stale public/private data and reconciliation circuit breakers;
4. a persistent operator kill state;
5. exchange dead-man cancellation renewed by a separate sentinel;
6. audited reduce-only flattening when connectivity permits.

Limits are carried in a signed, expiring deployment policy and clamped by
application hard bounds. A missing or expired production policy prevents live
startup. The daily accounting boundary is 00:00 UTC, supplemented by rolling
and high-water drawdown controls so a calendar reset cannot erase risk.

## Alternatives considered

- Risk checks inside each strategy: rejected because implementation divergence
  permits bypasses.
- External-only risk microservice: rejected for the primary check because
  network failure would sit in the hot path; the sentinel remains external for
  failure independence.
- Dead-man switch alone: rejected because it cancels orders but cannot flatten
  inventory or detect economic limit breaches.

## Consequences

- Risk evaluation adds bounded in-process latency to every command.
- Stale or inconsistent state fails closed for new exposure.
- Cancel and reduce-only paths remain available in restrictive states.
- Mainnet cannot start until disconnect, stale-data, kill, and reconciliation
  chaos tests pass on testnet.

## Reference

- [Hyperliquid scheduled cancellation](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint)
