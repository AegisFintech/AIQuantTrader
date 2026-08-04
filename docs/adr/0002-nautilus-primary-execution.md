# ADR 0002: NautilusTrader Owns Primary Execution

Status: proposed; accepted when the migration blueprint PR is merged
Date: 2026-08-04

## Context

The native platform needs typed market events, an order state machine,
reconciliation, portfolio state, and comparable research/live behavior.
Hyperliquid also provides an official Python SDK with low-level exchange access.

## Decision

NautilusTrader's Hyperliquid adapter is the sole normal order-entry path. The
official Hyperliquid SDK is restricted to an independent safety sentinel and
administrative verification. The trading node and sentinel use separate API
wallets while specifying the same approved master/subaccount address where
required.

Application reconciliation handles uncertain outcomes. It does not assume that
adapter retry configuration will retry an exchange request, and it does not
blindly resubmit a timed-out command.

## Alternatives considered

- Build directly on the SDK: maximum exchange control, but it duplicates order
  lifecycle, portfolio, reconciliation, backtest integration, and substantial
  failure handling.
- Use both SDK and Nautilus for routine orders: rejected because dual order
  ownership creates nonce, identity, rate-limit, and reconciliation ambiguity.
- Build a custom Rust connector immediately: lower theoretical overhead, but
  unnecessary for the documented publication cadence and one-instrument scope.

## Consequences

- Strategies and risk use Nautilus types and events.
- Exchange-specific extensions live behind a narrow adapter boundary.
- SDK credentials cannot place strategy orders.
- The pinned Nautilus release is a controlled dependency and requires contract
  tests before upgrades because the project evolves rapidly.

## References

- [NautilusTrader Hyperliquid integration](https://nautilustrader.io/docs/latest/integrations/hyperliquid/)
- [Hyperliquid nonces and API wallets](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/nonces-and-api-wallets)
