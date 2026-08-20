# Phase 7: Credential-Free BTC Paper Trading

Status: implementation complete; calibrated fill evidence, sensitivity runs,
required samples/regimes, operational drills, and observation time pending.

Phase 7 connects the live Hyperliquid public feed to the exact Phase 6 feature
and strategy kernels and the exact Phase 4 synchronous risk authority. Approved
intents terminate in a deterministic paper exchange. No exchange account,
trading wallet, control wallet, private subscription, SDK order method, or
Nautilus execution client is present in the paper service.

## Architecture

The source diagram is
[`phase-7-paper.mmd`](../architecture/diagrams/phase-7-paper.mmd).

```text
public WebSocket -> raw append/flush -> strict normalized event -> causal state
  -> microstructure + closed 15m/5m/1m structure -> production strategy
  -> hard risk -> paper simulator
  -> atomic SQLite cycle + status + Prometheus

operator kill / stale feed -----------------------> cancel-only + cancel-all
initial feature window -> frozen baseline -> rolling drift windows -> evidence
```

The paper service owns the public recorder connection. Do not run a second
`market-data-recorder` against the same state volume: DuckDB catalog locking
will reject the second writer. The independent normalizer may process completed
segments from the data volume.

## Repository delta

```text
native/
|- configs/paper/{baseline-v1,pessimistic-v1,evidence-v1}.toml
|- schemas/paper.schema.json
|- src/aiquanttrader_native/paper/
|  |- {models,config,market,drift}.py
|  |- {simulator,journal,engine,service}.py
|  `- {metrics,evidence,cli}.py
|- observability/grafana/dashboards/paper-trading.json
|- compose.yaml (credential-free `paper-trader` profile)
`- tests/{unit,integration}/test_paper_*.py

docs/
|- architecture/diagrams/phase-7-paper.mmd
|- migration/PHASE_7_PAPER.md
`- operations/PAPER_TRADING_RUNBOOK.md
```

## Live production-path parity

`LiveMarketStateAssembler` buffers normalized trades until the next full L2
snapshot, excludes exchange-stale trades using the feature contract's maximum
input age, and emits the same `KernelMarketState` consumed by HftBacktest and
actual Nautilus objects in Phase 5/6 parity tests. Exclusions are counted in
Prometheus; a stale L2 book, future timestamp, or non-monotonic receipt remains
fatal. `IncrementalFeatureEngine`,
`AvellanedaStoikovKernel`, `OrderFlowScalperKernel`, or the bounded
`SmartMoneyScalperKernel`, `OrderIntent`,
`RiskSnapshot`, and `RiskAuthority` are imported directly. Phase 7 does not
fork or approximate strategy and risk logic.

The optional OpenAI observer is outside that path. It receives only approved
setup evidence asynchronously and journals a typed retrospective verdict. Its
key is the sole permitted paper secret, and neither the verdict nor provider
availability can affect strategy, risk, or simulator state.

Nautilus remains the sole normal exchange-order owner in execution modes. Paper
mode deliberately does not instantiate its live execution client because a
simulated sink must be incapable of emitting an order. The shared kernel parity
contract is the boundary between representation-specific live data and alpha.

## Paper execution and accounting

The simulator consumes the versioned Phase 5 `ExecutionScenario`, so paper and
backtest use the same tick, lot, entry/response latency, maker/taker fee,
liquidity, trade-flow, slippage, funding, partial-fill, and calibration
identities. Paper scenarios are separate immutable artifacts because the live
market-by-price simulator fail-closes unless `queue_model = "risk_adverse"` and
`feed_latency_offset_ns = 0`; probability queues and synthetic feed delay need
retained event replay and cannot be silently approximated on a live callback.
It implements:

- latency-delayed activation and cancellation;
- post-only cross rejection, IOC cancellation, and rejection of unsupported
  non-post-only GTC limits;
- visible-level market/IOC sweeps with limit-bounded adverse tick rounding;
- market-by-price queue ahead from public displayed depth;
- seller-aggressor fills of passive bids and buyer-aggressor fills of asks;
- deterministic partial fills and fee attribution;
- cash, average entry, realized trading PnL, fees, funding, inventory, marked
  equity, day-start equity, high water, and drawdown;
- signed post-fill markouts and maker/taker attribution.

Public market-by-price depth cannot reveal private queue position, cancellations
ahead, or hidden liquidity. The simulator therefore records an estimate, not a
ground-truth fill. A scenario cannot pass the paper promotion report until its
`calibration_state` is `calibrated` and its immutable calibration hash binds
retained testnet or later shadow observations.

## Restart and failure semantics

One SQLite transaction commits each feature, strategy evaluation and exact gate
reason, risk decisions, order changes, fills, account snapshot, markouts, drift
report, and strategy checkpoint. Strategy evaluations include warmup and blocked
outcomes that emit no intent, plus bounded adaptive-forecast diagnostics, so a
zero-trade replay remains explainable. On restart,
the service resumes only when code, effective config, feature config, strategy
config, scenario, and evidence-policy identities match. It restores account,
orders, strategy memory, funding state, independent-decision clock, pending
markouts, and drift windows, then cancel-requests every restored open order.
Features warm up again rather than trusting unavailable process memory.

The watchdog uses the same risk authority for stale, disconnect, loss,
drawdown, leverage, and operator-kill state. Readiness requires both current L2
traffic and fresh mark/funding asset context; active books cannot conceal a
stale risk mark. Stale/disconnected/killed operation cannot submit a new intent
and initiates cancel-all. A corrupt kill file is active by default. Paper status
is atomically replaced and the service is not healthy while stale or killed.

## Frozen paper evidence

`btc-paper-evidence-v1` requires at least 14 days, 1,000 independent decisions,
500 fills, low/normal/high volatility coverage, positive post-cost PnL,
complete fill-markout coverage, drawdown/denial/markout limits, calibrated
fills, a flat final account with no open orders, a pessimistic sensitivity
report, bounded feature drift, and restart/stale/loss/drawdown/kill/observability
drills. Independence requires at least one second between counted decisions;
multiple quote legs at one timestamp do not inflate the sample.
Funding gaps, fatal service failures, or excluded frames during retained replay
are journaled as invalidating events and make the run-integrity gate fail.
Drift gates use the worst PSI and standardized mean shift observed across the
entire run, not only the final recovered window.

Evidence thresholds are evaluated without mutation. Sensitivity reports must
share the immutable code, feature, strategy, and policy hashes, bind the exact
configured scenario hash, cover the identical observation interval, and pass
every non-recursive economic/operational gate. Merely reusing a required
scenario name is insufficient. Every report has a canonical identity and
cannot claim promotion eligibility when any gate fails. Phase 7 evidence can
qualify a candidate for the next governance review; it cannot approve
production.

## Design decisions, alternatives, and tradeoffs

- The raw recorder callback was chosen over a second unarchived WebSocket
  client. Paper config flushes and `fdatasync`s every frame before its consumer,
  at the cost of storage latency and feature work sharing the recorder event
  loop. Cycle p99 is measured; split through a durable bounded local IPC only
  after load evidence.
- A deterministic in-process market-by-price simulator was chosen over an
  exchange testnet for paper. This proves zero credential capability and gives
  reproducible assumptions, but it requires empirical calibration and cannot
  establish private queue truth.
- Retained raw replay uses the identical live consumer path for sensitivity
  runs. This makes scenario comparisons share an exact admitted interval and
  avoids a second network sample, at the cost of separate state storage and
  offline replay time.
- SQLite WAL with full synchronous commits was chosen over DuckDB on the hot
  path. It provides transactional restart state and simple local recovery.
  Per-state durability adds latency; the measured p99 gate decides whether a
  later dedicated journal thread is justified.
- Strategy memory and risk state remain explicit rather than serializing a
  framework object graph. This makes restart reviewable and versioned, but the
  feature engine intentionally rewarms after restart.
- The run's first ready feature window is frozen as its drift baseline. This
  yields immediate online drift detection without an unsafe external model
  artifact. Cross-run promotion still requires the research baseline and model
  lineage in Phase 6 governance.
- Funding gaps are not backfilled with a stale current rate. Skipping an
  unverifiable cash flow makes a run incomplete rather than fabricating PnL.

## Performance implications

Feature work is bounded by ten levels and configured time windows. Queue fills
scan only open orders, hard-capped by risk configuration. Drift evaluation runs
at a configured interval over bounded baseline/current matrices. SQLite stores
the small model vector per ready feature so drift can restart exactly. Raw
normalization, Parquet, DuckDB analytics, reports, and Grafana remain outside
the decision calculation.

## Acceptance evidence

Automated now:

- durable raw archive before live consumer callback and fatal consumer isolation;
- causal event assembly, production feature/strategy/risk wiring, single-use
  approvals, deterministic fills/accounting/funding, and queue limitations;
- atomic per-cycle strategy gate evidence plus deterministic live/replay
  diagnostics, including non-order outcomes;
- restart recovery, cancel-on-resume, stale and kill drills, immutable evidence,
  hash/window/economic sensitivity binding, online drift, schemas, metrics,
  and dashboards;
- static and runtime proof that paper config/container has no account or wallet;
- strict typing, unit/integration tests, and existing native quality gates.

Still required before Phase 7 acceptance:

- create new calibrated baseline and pessimistic scenario versions from
  immutable testnet/shadow evidence; never relabel the checked-in seeds;
- run both scenarios with identical code/features/strategy/policy lineage;
- meet sample, regime, economic, drift, drill, and observation gates;
- retain raw archives, journal backup/restore evidence, metrics, incident drill
  output, paper reports, image digest, commit, lock, and reviewer decision.

## Migration and rollback

Start only the isolated Docker paper profile described in the runbook. It does
not alter PM2, MT5, Wine, XAU settings, or Common Files. Roll back by activating
the paper kill, stopping `paper-trader`, retaining raw/state volumes, and
restoring the prior native image/config. Paper rollback never authorizes an
exchange credential, an uncalibrated promotion, or a legacy runtime change.
