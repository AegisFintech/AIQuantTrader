# ADR 0001: BTC Perpetual Scope

Status: proposed; accepted when the migration blueprint PR is merged
Date: 2026-08-04

## Context

The legacy platform is coupled to broker-specific `XAUUSD` naming, sessions,
contract values, and MQL execution. The migration request names `BTCUSD`, but
Hyperliquid's standard product is a USDC-margined perpetual rather than a spot
BTC/USD pair.

## Decision

Version one supports only `BTC-USD-PERP.HYPERLIQUID`. Internal domain objects
use an instrument identifier and never rely on the display string `BTCUSD` for
contract semantics. The platform operates continuously subject to exchange
status, funding, maintenance, risk state, and operator controls.

Spot, additional perpetuals, options, multi-venue routing, and portfolio risk
netting are excluded until a later ADR explicitly introduces them.

## Alternatives considered

- Support spot and perpetual simultaneously: rejected because it introduces
  different inventory, custody, fee, and funding semantics before the core
  execution path is validated.
- Preserve a generic multi-symbol abstraction from day one: domain types remain
  instrument-aware, but runtime configuration is deliberately allowlisted to
  one instrument to reduce operational risk.
- Translate the XAU strategy: rejected because bar-based SMC logic does not
  provide a defensible baseline for crypto order-book market making.

## Consequences

- Risk limits, features, datasets, and promotion reports are unambiguous.
- BTC-specific contract metadata is loaded from the venue and reconciled at
  startup rather than hard-coded.
- Adding another product is a governed architecture change, not a configuration
  typo.
