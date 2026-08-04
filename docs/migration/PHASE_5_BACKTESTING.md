# Phase 5: Causal BTC Backtesting

Status: implementation complete; calibration and production-kernel acceptance evidence pending.

Phase 5 replaces the legacy XAU bar simulator for all native BTC promotion
decisions. It does not change, import, or restart the deployed MT5 runtime.

## Architecture

The source diagram is
[`phase-5-backtesting.mmd`](../architecture/diagrams/phase-5-backtesting.mmd).

```text
admitted Tardis/local data -> deterministic HftBacktest events + lineage hash
  -> baseline and pessimistic queue/latency/fee/liquidity scenarios
  -> fills + fees + explicit taker slippage + hourly funding cashflows

Hft local-arrival events ----> shared pure kernel ----> decision trace
Nautilus market-data objects -> shared pure kernel ----> decision trace

dataset -> purged walk-forward -> validation-only selection receipt
  -> one selected candidate may access the untouched final holdout
```

HftBacktest `2.4.4` is now a core native dependency because simulator adapters
and their tests are part of the production research package. It supplies the
Rust replay engine, market-by-price queue models, order latency, partial fills,
and trading-value fee accounting. NautilusTrader `1.230.0` supplies the actual
market-data object types used by the second parity path.

## Repository delta

```text
native/
|- configs/backtest/{baseline,pessimistic,validation-v1}.toml
|- schemas/backtest.schema.json
|- src/aiquanttrader_native/backtest/
|  |- {models,conversion,scenarios,replay}.py
|  |- {kernel,validation,statistics}.py
|  `- cli.py
`- tests/
   |- integration/test_{backtest_conversion,backtest_kernel_parity,hft_replay}.py
   `- unit/test_backtest_validation.py

docs/
|- architecture/diagrams/phase-5-backtesting.mmd
|- migration/PHASE_5_BACKTESTING.md
`- operations/BACKTESTING_RUNBOOK.md
```

## Causality and lineage invariants

- Exchange time records when an event occurred at Hyperliquid. Local time
  records when the strategy could observe it. Kernel replay consumes only
  HftBacktest events carrying the local-processing flag.
- Conversion rejects negative timestamps, non-finite values, negative
  quantities, and local timestamps earlier than exchange timestamps. Clock
  skew must be calibrated before admission; the converter does not silently
  rewrite it.
- Phase 3 Parquet conversion requires every normalized segment named by the
  admitted quality manifest and rejects extra or missing segments. Every input
  Parquet hash becomes part of the backtest identity.
- Tardis conversion requires one checksummed `trades` file and one checksummed
  `incremental_book_L2` file for the same UTC day. Trades are passed first so a
  trade and its subsequent depth reduction do not consume queue twice.
- NPZ ZIP metadata is fixed, NumPy pickles are forbidden, and identical logical
  inputs produce identical bytes. The manifest binds converter version, input
  hashes, output hash, row count, instrument, and exchange/local time ranges.
- Hyperliquid L2 data is market-by-price. Queue position remains an estimate;
  no report may label it observed ground truth.

## Execution scenarios

Every material assumption is schema-validated and hashed. The checked-in
values are conservative starting points, not calibration evidence.

| Assumption | Baseline v1 | Pessimistic v1 |
|---|---:|---:|
| Entry / response latency | 50 ms / 50 ms | 250 ms / 250 ms |
| Additional feed latency | 0 ms | 100 ms |
| Queue model | log probability | risk adverse |
| Book liquidity retained | 100% | 50% |
| Queue-advancing trade flow retained | 100% | 75% |
| Maker / taker fee | 1.5 / 4.5 bps | 2.0 / 6.0 bps |
| Extra taker slippage | 0.5 bps | 3.0 bps |
| Funding-rate multiplier | 1.0x | 1.5x |

The baseline fee seed corresponds to the published Hyperliquid tier-zero perp
rates at implementation time. Actual research must use the account's fee
response and a dated calibration artifact because volume, staking, referral,
and maker tiers can change the effective rate. Positive funding debits a long
and credits a short. Funding uses the documented hourly formula
`position * oracle price * funding rate`; the pessimistic scenario scales its
magnitude.

Both checked-in scenarios say `calibration_state = "uncalibrated"` and omit a
calibration hash. `require_promotion_eligible()` therefore fails closed. A
human-readable config edit alone cannot make a candidate promotable: calibrated
status also requires a SHA-256 identity for retained acknowledgement, fill,
book-evolution, fee, and funding evidence.

## Shared-kernel parity

The kernel interface makes strategy memory an explicit input and output. It
receives an immutable L2/trade state and returns typed `OrderIntent` submissions
and cancellation identities. It has no exchange, storage, clock, or network
handle.

The Hft adapter groups all locally received rows at one timestamp before
calling the kernel and ignores exchange-side events that have not arrived
locally. The Nautilus adapter accepts real `QuoteTick`, `TradeTick`, and
`OrderBookDepth10` objects. Exact decision JSON and final kernel memory must
match. This establishes the representation boundary now; Phase 6 must run each
production strategy kernel through the same parity gate.

## Walk-forward and selection controls

The versioned policy creates, per fold, contiguous train, purge, validation,
embargo, and walk-forward-test windows. Purge must cover the full prediction
label horizon. Test windows cannot overlap, and all folds end before the final
holdout.

`select_candidate()` accepts validation scores only and emits a receipt binding
the candidate set, plan, metric, direction, and complete score payload. Final
holdout access rejects any candidate other than the frozen selection. Reports
also provide seeded moving-block bootstrap intervals and a one-sided
Bonferroni selection-family lower bound; neither is presented as a guarantee of
future profitability.

## Alternatives and tradeoffs

- A bespoke matching simulator was rejected. HftBacktest already provides a
  tested Rust event loop and multiple explicit market-by-price queue models.
- Nautilus-only backtesting was rejected because its matching model does not
  replace queue-assumption sensitivity for passive market making. Hft-only
  validation was rejected because it would not exercise production data types.
- One optimistic scenario was rejected. Promotion evidence must include both
  baseline and pessimistic results under the same dataset and kernel identity.
- Random train/test splitting was rejected because labels, book state, and
  parameter selection are time-dependent.
- Compressed NPZ reduces retained artifact size and is accepted directly by
  HftBacktest, but it cannot be memory-mapped. Conversion and replay are split
  by UTC day to bound peak memory; large studies pass multiple day artifacts to
  the engine.

## Performance implications

- Tardis conversion uses the pinned library converter and preallocates no more
  than twice the source rows plus bounded snapshot overhead for one day.
- Normalized Parquet conversion currently materializes the selected segment set
  to establish deterministic cross-channel ordering. Research jobs should use
  hour/day batches; a streaming external sort requires benchmark evidence
  before replacing this simpler path.
- Scenario stress copies the event array once so the admitted artifact remains
  immutable. The matching loop itself runs in HftBacktest's Rust core.
- Kernel parity groups local events and retains only bounded L2 depth. It is a
  validation path, not the live hot path.

## Forward migration

1. Retain testnet/live acknowledgements, order response timestamps, quote
   lifetimes, book evolution, fills, effective account fees, and hourly funding.
2. Produce reviewed calibration artifacts and new scenario versions that bind
   their SHA-256 hashes. Do not mutate the v1 seed scenarios.
3. Implement Phase 6 market-making, scalping, and forecasting kernels and run
   each through Hft/Nautilus parity.
4. Execute baseline and pessimistic walk-forward studies over admitted Tardis
   and local-capture data, then freeze selection before opening final holdout.
5. Retain commands, lockfile, commit, dataset/scenario hashes, reports, and
   reviewer decision. This evidence is required before Phase 5 acceptance.

## Rollback

Stop native research jobs and keep their immutable inputs/results for audit.
Revert to the prior native image and dependency lock. Do not reinterpret legacy
XAU results as native BTC evidence, and do not change or restart the MT5 demo
runtime as part of a Phase 5 rollback.

## Acceptance evidence

Automated now:

- deterministic Tardis and admitted-Parquet conversion with exact hashes;
- timestamp/order validation and an exchange-before-local no-lookahead test;
- synthetic queue, latency, passive fill, fee, slippage, and funding outcomes;
- deterministic replay and pessimistic fill sensitivity;
- exact shared-kernel decisions from Hft events and real Nautilus objects;
- purged/embargoed disjoint windows, validation-only selection, guarded final
  holdout, bootstrap uncertainty, and selection-family penalties;
- schema export, strict typing, pinned dependencies, and over 90% native test
  coverage.

Still required before Phase 5 is declared accepted:

- reviewed latency, queue, fee/rebate, slippage, and fill calibration artifacts;
- retained full-dataset baseline and pessimistic reports;
- parity evidence for the Phase 6 production kernels, not only the diagnostic
  contract kernel;
- reviewer sign-off that the final holdout was opened only after selection.
