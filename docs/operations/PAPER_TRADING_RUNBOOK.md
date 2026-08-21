# Native BTC Paper Trading Runbook

This runbook operates the Phase 7 public-feed paper service. It cannot place a
Hyperliquid order.

## 1. Preflight and credential proof

From the repository root:

```bash
uv sync --frozen --extra research --group dev
uv run aqt-native validate-config --config-dir configs --environment paper
docker compose --profile paper --profile monitoring config > /tmp/aqt-paper-compose.yaml
```

Review the rendered `paper-trader` service. It must have no exchange account,
wallet path, execution client, or sentinel. The only permitted secret mount is
the optional read-only `/run/secrets/openai_api_key`; it is `/dev/null` while
the observer is disabled. `execution.enabled` must be false and `paper.enabled`
true. Stop if any exchange identity or wallet secret is present.

The service must render `target: paper` and
`image: aiquanttrader-native-paper:0.1.0`. That image retains the public-feed,
paper, observability, and optional OpenAI dependencies but excludes
HftBacktest, NautilusTrader, Hyperliquid SDK, PyArrow, and approval
cryptography. A missing excluded package is intentional; do not add the general
dependency set to work around an unrelated operational failure.

Do not run `market-data-recorder` against the same state volume. `paper-trader`
owns raw capture for this profile. The normalizer may run separately after raw
segments finalize.

## 2. Start and verify

Bind the run to an immutable commit or image digest:

```bash
export AQT_NATIVE_CODE_IDENTITY="$(git rev-parse HEAD)"
docker compose --profile paper --profile monitoring up --build -d \
  paper-trader node-exporter prometheus grafana
docker compose ps paper-trader
docker compose logs --tail 100 paper-trader
docker compose exec paper-trader \
  aqt-paper healthcheck --state-root /var/lib/aiquanttrader/state
```

For a release that changes the Docker boundary, record the image size and
verify the paper entry points before replacement:

```bash
docker image inspect aiquanttrader-native-paper:0.1.0 --format '{{.Size}}'
docker run --rm --entrypoint aqt-paper \
  aiquanttrader-native-paper:0.1.0 --help
docker run --rm --entrypoint aqt-paper-healthcheck \
  aiquanttrader-native-paper:0.1.0 --help
```

Size is a regression diagnostic, not a safety or promotion gate. The build
cache may be pruned after the exact image is running; never prune active images,
volumes, journals, or captured data.

Record a successful independent status-contract check once per run:

```bash
docker compose exec paper-trader \
  aqt-paper healthcheck --state-root /var/lib/aiquanttrader/state \
  --record-observability
```

The schema-v3 status must show the expected code/config/scenario hashes, BTC
strategy, zero credential capability in the journal manifest, and `warming` or
`ready`. Its `feed_freshness` block separates the WebSocket, latest public
frame, mark/funding asset context, executable BBO, and full L2 depth. It reports
signed ages, the unchanged 1.5-second executable risk threshold, the independent
two-second depth limit, and the first exact blocking reason. Stale L2 depth is
reported explicitly but cannot conceal a fresh BBO or be relabeled as current;
missing/stale BBO or asset context remains degraded. The checked-in
scenario is `uncalibrated`; that is expected and must make the promotion report
fail.

Docker uses the standard-library-only `aqt-paper-healthcheck --mode liveness`
entry point for its frequent process probe. It requires a valid, fresh atomic
status in a non-terminal lifecycle, but an intentionally active operator kill
or a degraded feed does not falsely report the process as dead. Operators
should continue using the full
`aqt-paper healthcheck` command above for typed contract validation and drill
recording; it remains the fail-closed operational-readiness check. The
lightweight probe always validates the lifecycle, heartbeat, feed, feature, and
operator-kill projection without loading the trading dependency graph on every
probe. `liveness` is never permission to trade; readiness, strategy, risk, and
the durable kill remain independent.

`smart-money-scalper-v2` requires causal closed bars on 1m, 5m, and 15m, so a
fresh run remains in structure warmup for roughly one hour. Its causal forecast
also needs at least 500 resolved 1 Hz samples with 30-second labels. It allows
only one position, reviews no-progress exposure at 60 seconds, and must issue a
reduce-only exit by 180 seconds. A position age above 180 seconds is an incident.

## 3. Monitor

Grafana is available only on the local loopback interface:

```text
http://127.0.0.1:3000/d/aqt-paper-trading/aiquanttrader-btc-paper-trading
http://127.0.0.1:3000/d/aqt-platform-health/aiquanttrader-server-live-status
```

It opens the provisioned BTC scalping command center as an anonymous read-only
viewer. The top rows show the exact action and gate reason, BTC bid/ask/mid,
expected-versus-required edge, SMC confluence, 15m/5m/1m direction,
support/resistance, stop/target, order flow, and position age.
It also shows online forecast readiness, resolved labels, forecast bps,
directional accuracy, MAE, and the cost hurdle. The feed row shows the exact
executable blocker, each component's current state, BBO/context/frame ages
against the hard 1.5-second risk limit, and L2 depth against its independent
two-second limit. It also states whether the latest engine state used full L2
or safely degraded to BBO only. The platform dashboard shows service live state, CPU,
memory, root-disk capacity/use, disk I/O, network in/out, uptime, load, and the
same decomposed paper-feed freshness.
Prometheus is available at `http://127.0.0.1:9090`. Neither endpoint is exposed
to the LAN or internet. Do not publish or reverse-proxy Grafana without adding
operator authentication and TLS.

Verify the monitor and paper scrape before relying on the dashboard:

```bash
curl --fail --silent http://127.0.0.1:3000/api/health
curl --fail --silent \
  'http://127.0.0.1:9090/api/v1/query?query=up%7Bjob%3D%22aiquanttrader-paper%22%7D'
curl --fail --silent \
  'http://127.0.0.1:9090/api/v1/query?query=up%7Bjob%3D%22aiquanttrader-node%22%7D'
```

Inspect the durable strategy gate distribution independently of Prometheus:

```bash
docker compose exec paper-trader \
  aqt-paper diagnostics --state-root /var/lib/aiquanttrader/state
```

The summary counts every causal strategy evaluation, including warmup,
model-quality, spread, cost, volatility, confluence, cooldown, and inventory
blocks that produced no order intent. It also reports feature/structure/feed
readiness and the latest bounded adaptive-forecast sample count, accuracy, MAE,
and prediction. Counts are evidence, not permission to relax a gate.

Scrape `paper-trader:9112` through Prometheus and provision
`paper-trading.json`. Alert on:

- `aqt_paper_feed_connected != 1`, any required
  `aqt_paper_feed_component_fresh{component!="l2_depth"} != 1`, or feature
  readiness dropping after warmup. Inspect `aqt_paper_feed_blocked == 1` before
  recovery; investigate L2 depth separately when its freshness remains zero;
- operator kill, stale recorder heartbeat, reconnect/error growth, or raw disk
  pressure;
- risk denials, loss/drawdown state, inventory/open-order limits, and leverage;
- cycle p99, fill rate, maker ratio, PnL, adverse markouts, and drawdown;
- drift readiness, maximum PSI, and standardized mean shift;
- position age below 180 seconds, structure and adaptive-forecast readiness
  after warmup, forecast accuracy/MAE, and bounded
  LLM observer errors if that optional observer is enabled;
- journal/state filesystem errors or a funding-gap event.

The dashboard also reports cumulative stale-trade, stale-book, and stale-BBO
exclusions.
Hyperliquid's initial subscriptions may contain bounded historical events, so a
small startup increase is expected. The live assembler archives but excludes
those inputs before feature generation using the configured maximum input age.
Continued growth after startup indicates delayed exchange events or host/feed
trouble and requires operator review. Every valid BBO refreshes executable-feed
freshness, while engine/feature/journal states are bounded to the configured
one-second cadence and retain intervening trades. Full L2 levels are merged only
while they remain inside the existing feature input-age limit; otherwise the
state contains current BBO only. Future exchange timestamps and non-monotonic
book receipt remain fatal integrity failures.

`aqt_market_data_connected` means only that the public WebSocket is open. It is
not sufficient for paper readiness. `aqt_paper_feed_connected` requires socket,
public frame, asset context, and executable BBO to be current. `l2_depth` is an
independent feature-quality series and does not change that combined verdict.
Missing ages are exported as Prometheus `NaN`; a negative age is an explicit
clock-regression state rather than being treated as fresh.

The service writes raw segments below the data volume and its WAL journal,
kill audit, and atomic status below `state/paper/`. Back up SQLite with its
online backup API or stop the service before copying the DB, WAL, and SHM.

## 3.1 Optional OpenAI setup confirmation

The LLM observer is not required for trading and is disabled by default. It is
strictly shadow-only: a response cannot submit, cancel, resize, delay, or veto an
order and cannot bypass risk. It runs asynchronously only for already-approved
entry setups, no more frequently than once per minute.

Provision an OpenAI project key through the host secret manager; never put the
key in Git, TOML, an environment variable, a CLI argument, a log, or chat. Mount
the resulting host file read-only for container UID `65532`, then enable the
observer:

```bash
sudo install -d -o root -g root -m 0755 /etc/aiquanttrader
sudo install -o 65532 -g 65532 -m 0400 /secure/secret-source/openai_api_key \
  /etc/aiquanttrader/openai_api_key
export AQT_OPENAI_API_KEY_FILE=/etc/aiquanttrader/openai_api_key
export AQT_NATIVE__PAPER__LLM_CONFIRMATION__ENABLED=true
docker compose --profile paper up --build -d paper-trader
```

The default model is `gpt-5.6-terra`. Confirm `llm_confirmation_enabled=true`
in status and monitor confirmation verdicts, confidence, latency, and safe error
codes. Provider failure is non-fatal to the deterministic paper path and never
becomes implicit approval.

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

The paper baseline and pessimistic scenarios are intentionally uncalibrated,
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

Replay completion JSON includes a `strategy` object with the same persisted
gate distribution returned by `aqt-paper diagnostics`. Review it before
changing a strategy threshold; zero intents or fills without the dominant gate
counts are insufficient evidence for a parameter change.

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
2. Stop `paper-trader`; Prometheus and Grafana may remain up while preserving
   raw data, journal/WAL, status, kill audit, and metrics snapshots.
3. Classify feed, clock, disk, simulator, accounting, drift, or configuration
   failure. Do not delete evidence or backfill unverifiable funding/fills.
4. Restore the prior immutable native image/config and start a new run if any
   artifact identity changed.
5. Clear the kill only after health, raw capture, journal reconciliation, and
   operator review pass.

Rollback never adds an exchange key to paper mode.
