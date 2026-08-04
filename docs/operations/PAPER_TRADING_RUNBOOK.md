# Native BTC Paper Trading Runbook

This runbook operates the Phase 7 public-feed paper service. It cannot place a
Hyperliquid order. It does not start, stop, compile, modify, or replace MT5/Wine.

## 1. Preflight and credential proof

From `native/`:

```bash
uv sync --frozen --extra research --group dev
uv run aqt-native validate-config --config-dir configs --environment paper
docker compose --profile paper config > /tmp/aqt-paper-compose.yaml
```

Review the rendered `paper-trader` service. It must have no `secrets`,
`/run/secrets`, account address, wallet path, execution client, or sentinel.
`execution.enabled` must be false and `paper.enabled` true. Stop if any exchange
identity or secret reference is present; paper configuration rejects it.

Do not run `market-data-recorder` against the same state volume. `paper-trader`
owns raw capture for this profile. The normalizer may run separately after raw
segments finalize.

## 2. Start and verify

Bind the run to an immutable commit or image digest:

```bash
export AQT_NATIVE_CODE_IDENTITY="$(git rev-parse HEAD)"
docker compose --profile paper up --build -d paper-trader
docker compose ps paper-trader
docker compose logs --tail 100 paper-trader
docker compose exec paper-trader \
  aqt-paper healthcheck --state-root /var/lib/aiquanttrader/state
```

Record a successful independent status-contract check once per run:

```bash
docker compose exec paper-trader \
  aqt-paper healthcheck --state-root /var/lib/aiquanttrader/state \
  --record-observability
```

The status must show the expected code/config/scenario hashes, BTC strategy,
fresh L2 and mark/funding context, zero credential capability in the journal
manifest, and `warming` or `ready`. L2 updates without fresh asset context must
remain degraded. The checked-in scenario is `uncalibrated`; that is expected
and must make the promotion report fail.

## 3. Monitor

Scrape `paper-trader:9112` through Prometheus and provision
`paper-trading.json`. Alert on:

- `aqt_paper_feed_connected != 1` or feature readiness dropping after warmup;
- operator kill, stale recorder heartbeat, reconnect/error growth, or raw disk
  pressure;
- risk denials, loss/drawdown state, inventory/open-order limits, and leverage;
- cycle p99, fill rate, maker ratio, PnL, adverse markouts, and drawdown;
- drift readiness, maximum PSI, and standardized mean shift;
- journal/state filesystem errors or a funding-gap event.

The service writes raw segments below the data volume and its WAL journal,
kill audit, and atomic status below `state/paper/`. Back up SQLite with its
online backup API or stop the service before copying the DB, WAL, and SHM.

## 4. Operator kill drill

```bash
docker compose exec paper-trader aqt-paper kill activate \
  --state-root /var/lib/aiquanttrader/state \
  --actor operator-id --reason "scheduled Phase 7 kill drill"
```

Within cancel latency, open paper orders must be zero, new approvals must stop,
health must be not-ready, and the journal must record the kill drill. Inspect
status and metrics before clearing:

```bash
docker compose exec paper-trader aqt-paper status \
  --state-root /var/lib/aiquanttrader/state
docker compose exec paper-trader aqt-paper kill clear \
  --state-root /var/lib/aiquanttrader/state \
  --actor operator-id --reason "kill drill reconciled"
```

Never delete or hand-edit the kill file. An unreadable file fails closed.

## 5. Stale, loss, drawdown, and restart drills

Run on an isolated paper host while retaining metrics and journal evidence:

1. Block the public WebSocket or inject the tested stalled socket condition.
   Confirm cancel-only, cancel-all, no approvals, not-ready health, reconnect,
   raw segment finalization reason, and `stale_data` drill evidence.
2. Use a dedicated calibrated fault scenario/account state to cross the frozen
   daily-loss threshold. Confirm reduce-only risk and cancel-all; do not alter
   the production threshold to manufacture a pass.
3. Cross the drawdown threshold independently and confirm its stable reason and
   drill record.
4. Restart with resting orders and a non-flat simulated account. Confirm the
   same run/artifact hashes, restored cash/inventory/PnL/memory/funding/drift,
   cancel-on-resume, feature rewarm, and zero unreconciled orders.
5. Stop during a journal transaction fault test. SQLite must expose either the
   complete prior cycle or complete next cycle, never a partial fill/account.

Elapsed time without these state transitions is not drill evidence.

## 6. Calibration and sensitivity

The paper v1 baseline and pessimistic scenarios are intentionally uncalibrated,
use the conservative risk-adverse queue, and do not claim synthetic live feed
delay. Probability-queue or nonzero feed-offset sensitivity belongs in retained
Phase 5 replay; the live paper process rejects those unsupported semantics.
Follow the backtesting runbook to produce immutable testnet/shadow calibration
evidence, a reviewer identity, and new scenario versions with
`calibration_state = "calibrated"` plus `calibration_sha256`.
Before observation starts, create a new frozen paper-policy version whose
required sensitivity ID and `sensitivity_scenario_paths` reference the new
calibrated pessimistic scenario. Point the primary paper scenario at the new
calibrated baseline. Do not edit these bindings after seeing results.

Run baseline and every required sensitivity scenario in separate state/data
volumes with identical code, feature, strategy, and evidence policy hashes.
After the live baseline stops and its raw segments finalize, replay the exact
retained segments through the pessimistic scenario. Repeat `--raw-segment` in
chronological scope for every segment in the baseline observation:

```bash
AQT_NATIVE__PAPER__SCENARIO_PATH=paper/pessimistic-calibrated-v2.toml \
  AQT_NATIVE__STORAGE__STATE_ROOT=/var/lib/aiquanttrader/pessimistic-state \
  AQT_NATIVE__STORAGE__DATA_ROOT=/var/lib/aiquanttrader/pessimistic-data \
  uv run aqt-paper replay \
    --config-dir configs --environment paper \
    --code-identity "$AQT_NATIVE_CODE_IDENTITY" \
    --raw-segment data/raw/.../segment-001.raw.zst \
    --raw-segment data/raw/.../segment-002.raw.zst
```

The replay verifies each segment and network, rejects duplicates/overlap, and
uses the same parser, state assembler, production features, strategy, risk,
simulator, journal, and status contracts without opening a socket. A sensitivity
report is admissible only when its exact start/end window and every
non-recursive gate match the baseline requirements. Never run two paper
services against one SQLite or raw-catalog state volume.

## 7. Generate immutable evidence

Generate the sensitivity report first, then bind it into the baseline report.
The report must cover the exact same observation start/end, pass every gate
other than its recursive sensitivity-presence gate, and bind the configured
sensitivity scenario hash:

```bash
uv run aqt-paper evidence \
  --config-dir configs --environment paper \
  --output state/paper/reports/pessimistic.json

uv run aqt-paper evidence \
  --config-dir configs --environment paper \
  --sensitivity-report state/paper/reports/pessimistic.json \
  --output state/paper/reports/baseline.json
```

Exit `0` means every frozen paper gate passed. Exit `1` means a valid but
non-promotable report. Exit `2` means invalid input/operation. With checked-in
v1 scenarios, exit `1` is mandatory. Do not weaken thresholds or edit a report
after observing results.

Any `funding_gap`, `service_failure`, or `replay_exclusions` journal event fails
the run-integrity gate. Repair the cause and start a new immutable run; never
delete the event or splice evidence across runs.

Retain the report, journal backup, raw manifests, code/image/lock hashes,
effective config fingerprint, scenario calibration, strategy/feature hashes,
metrics, drift reports, drill output, gaps, and reviewer decision. Passing
paper authorizes at most review for Phase 8 shadow; it cannot authorize
mainnet.

Do not generate final evidence until every fill has reached its configured
markout horizon and the simulator is flat with no open orders; unresolved tail
fills or residual exposure make the corresponding evidence gate fail.

## Incident response and rollback

1. Activate the paper kill and confirm no open simulated orders.
2. Stop `paper-trader`; preserve raw data, journal/WAL, status, kill audit, and
   metrics snapshots.
3. Classify feed, clock, disk, simulator, accounting, drift, or configuration
   failure. Do not delete evidence or backfill unverifiable funding/fills.
4. Restore the prior immutable native image/config and start a new run if any
   artifact identity changed.
5. Clear the kill only after health, raw capture, journal reconciliation, and
   operator review pass.

Rollback does not interact with the deployed MT5 runtime and never adds an
exchange key to paper mode.
