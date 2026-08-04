# Phase 8: Network-Isolated BTC Shadow Deployment

Status: implementation complete; production-host soak, calibrated scenarios,
required samples/regimes, retained fault drills, and seven-day observation are
pending.

Phase 8 runs the exact Phase 6 feature/strategy kernels and Phase 4 hard-risk
authority on the live public Hyperliquid feed using the same native runtime
image intended for production. Approved commands terminate in an immutable
counterfactual journal and calibrated simulator. The decision process has no
IP network namespace, exchange identity, wallet, signer, secret mount,
Nautilus execution client, or SDK order method.

## Architecture

The source diagram is
[`phase-8-shadow.mmd`](../architecture/diagrams/phase-8-shadow.mmd).

```text
Hyperliquid -> public gateway -> raw fsync -> checksummed SQLite ingress
                                             |
                                             | read-only volume
                                             v
                              network_mode:none shadow engine
                              -> production feature/strategy/risk
                              -> atomic recorded-only commands
                              -> counterfactual simulator and audit
                                             |
                                             v
                              read-only observer -> Prometheus/Grafana
```

The split is a security boundary, not a scaling convenience. Only
`shadow-gateway` can reach Hyperliquid. `shadow-engine` proves at startup that
`/proc/net/route` and `/proc/net/ipv6_route` contain no non-loopback default
route. `shadow-observer` mounts engine state read-only and has no control path
back into the engine.

## Repository delta

```text
native/
|- compose.shadow.yaml
|- configs/shadow/evidence-v1.toml
|- schemas/shadow.schema.json
|- src/aiquanttrader_native/shadow/
|  |- {models,config,ingress,gateway}.py
|  |- {service,sink,security,metrics,observer}.py
|  `- {audit,evidence,cli}.py
|- observability/grafana/dashboards/shadow-trading.json
`- tests/{unit,integration}/test_shadow_*.py

docs/
|- architecture/diagrams/phase-8-shadow.mmd
|- migration/PHASE_8_SHADOW.md
`- operations/SHADOW_DEPLOYMENT_RUNBOOK.md
```

Phase 8 also extends the Phase 7 transactional journal with exact submit,
cancel, and cancel-all command contracts. Commands, risk decisions, simulator
updates, fills, features, and restart checkpoints commit in the same SQLite
transaction. This improves paper evidence as well as shadow evidence.

## One-way durable ingress

The gateway imports the Phase 3 `MarketDataRecorder`. Its consumer is called
only after the exact WebSocket payload has been appended to the raw segment;
the shadow overlay sets `sync_every_records = 1`, so the raw record is flushed
and `fdatasync`-protected before it can affect a decision.

The gateway then serializes only normalized public BTC event contracts into a
full-synchronous SQLite ingress. Each row has a monotonically increasing local
sequence, gateway receive/write times, canonical event bytes, and SHA-256.
The engine opens that database with `mode=ro` and `query_only=ON`, verifies
contiguous sequence and content hash, and fails on a gap or mutation. SQLite
rollback journaling was selected so a volume can remain physically read-only
in the engine container while the gateway writes concurrently.

The ingress is deliberately local and durable. An in-memory queue or socket
would lose the exact decision boundary during a process/host failure. Kafka or
another remote broker would add a network and operational dependency to the
hot path. Per-frame full commits cost storage latency; the frozen p99 gate
determines whether a later profiled implementation may batch without weakening
raw-first durability.

## Production-path parity and command sink

`ShadowEngineService` imports `LiveMarketStateAssembler`,
`IncrementalFeatureEngine`, the selected production strategy kernel,
`RiskAuthority`, and `PaperExchangeSimulator` directly. There is no shadow
copy of feature, alpha, or risk math.

For every risk-approved submit, the engine creates a versioned
`PaperExecutionCommand` containing the exact order intent, risk-decision
identity, feature hash, source ingress sequence, and timestamp. Strategy
cancels and risk/watchdog cancel-all actions are also explicit commands.
`ShadowCommandSink` verifies that approved-submit count and recorded-submit
count are identical and that every command declares
`counterfactual_only`. The journal schema enforces unique run-local command
sequences.

The immutable run manifest records the ingress start sequence selected when
the engine is admitted, and every atomic engine checkpoint records the latest
processed sequence. Retained-ingress replay derives both boundaries from the
source journal and refuses mismatched code, image, configuration, feature,
strategy, scenario, or evidence-policy lineage. Frames written before engine
admission or after its last committed cycle therefore cannot contaminate a
determinism comparison.

The simulator remains necessary for counterfactual inventory, PnL, fill,
funding, and markout evidence. It is not represented as venue truth. Scenario
promotion still requires immutable calibration observations, and the checked-in
baseline/pessimistic seeds remain uncalibrated.

## Restart, lag, clock, and failure semantics

- A restart resumes only when code, image digest, effective config, feature,
  strategy, scenario, and evidence identities match.
- The last durably committed ingress sequence is part of the strategy
  checkpoint. Recovery replays after that boundary; a commit/audit gap is
  visible because operational sample count no longer equals feature count.
- Restored counterfactual orders are cancel-requested before normal decisions.
- Gateway/engine clock lead beyond policy is fatal. Ingress lag or silence
  makes the feed disconnected and invokes the same stale-data risk path.
- Operator kill, stale ingress, loss, or drawdown cannot create exposure and
  initiates counterfactual cancel-all.
- Audit or command-sink failure invalidates the run rather than allowing an
  unrecorded decision.
- Observer failure cannot grant execution or block the in-process risk and
  cancel path. The engine writes an atomic Prometheus text handoff; the
  observer only reads it.

## Frozen evidence and determinism

`btc-shadow-evidence-v1` requires at least seven days, 1,000 independent
decisions, 500 fills, all volatility regimes, 99.9% sampled availability,
bounded ingress and cycle p99, calibrated fills, positive post-cost PnL,
complete markouts and commands, bounded drawdown/denial/adverse selection and
drift, pessimistic sensitivity, exact retained-ingress replay, a flat final
state, and retained host-reboot, disk-pressure, clock, recorder,
observability, and operator-kill drills.

Availability includes missing process samples: healthy samples contribute at
most one configured interval, so process downtime cannot disappear merely
because the process was unable to write `unhealthy`. Every drill record binds
a non-empty retained artifact by SHA-256. Determinism compares canonical
decision and command content while excluding random journal identities.

A fully passing report sets only `awaiting_human_approval = true`. There is no
approval, deployment, credential, or capital-changing operation in the shadow
CLI. Phase 9 must implement and separately review cryptographic human approval.

## Design decisions, alternatives, and tradeoffs

- Process/network isolation was chosen over an `execution=false` branch in a
  networked trading process. The extra gateway, ingress commit, and observer
  add latency and operational surface but turn a software flag into a kernel
  network boundary.
- A public-only gateway was chosen over mounting a wallet for private account
  events. This proves zero signing capability and follows the release
  checklist. Counterfactual account state is used until the separately
  approved Phase 9 canary performs real account reconciliation.
- The same runtime image is used for gateway, engine, and observer. This tests
  the exact dependency closure intended for production; container roles and
  mounts, rather than bespoke images, minimize privilege.
- SQLite rollback journaling was chosen for read-only cross-container access.
  WAL has better concurrency but commonly requires a writable shared-memory
  sidecar, weakening the read-only ingress mount.
- Full command capture was added inside the transactional engine instead of
  reconstructing commands from orders later. This increases journal work but
  preserves rejected/approved causality and cancel intent.
- Retained-ingress replay was chosen over comparing two simultaneous network
  clients. It guarantees identical admitted inputs and exposes decision
  instability, while operational reconnect decisions remain separate fault
  evidence.

## Performance implications

The gateway performs two durable writes per valid frame: raw archive then
ingress. The engine polls ingress in bounded batches and feature work remains
bounded by configured depth/windows. SQLite is not shared with DuckDB or
Parquet. Command and engine-cycle transactions are synchronous. Histograms and
the audit journal retain ingress/cycle p99 so any optimization must be driven
by measured production-host evidence rather than weakening durability by
assumption.

## Automated gates and empirical acceptance

Automated now:

- strict shadow configuration and immutable image-digest requirement;
- no account/wallet/sentinel/execution capability;
- no engine IP default route and a Compose `network_mode: none` contract;
- raw-first, checksummed, contiguous, read-only ingress;
- exact feature/strategy/risk reuse and atomic command completeness;
- deterministic retained-ingress replay and lineage comparison;
- counterfactual accounting, markouts, drift, latency, availability, drill,
  and integrity gates;
- restart, stale/kill, clock, corruption/gap, observer, schema, and CLI tests;
- non-root/read-only containers, bounded metrics, and Grafana dashboard.

Still required before Phase 8 acceptance:

- build, sign, and deploy the exact immutable image on the intended host;
- create new calibrated baseline/pessimistic scenario versions from retained
  evidence; never relabel the checked-in seeds;
- complete a minimum seven-day live observation, or longer until sample and
  regime gates pass;
- execute and retain every required production-host fault drill;
- retain raw/ingress/journal/audit backups, replay comparison, Prometheus data,
  image/provenance, effective config, incident notes, and reviewer decision.

## Migration and rollback

Deploy only `native/compose.shadow.yaml`. It is independent of PM2, MT5, Wine,
XAU configuration, and Common Files. Roll back by activating the shadow kill,
stopping the observer/engine/gateway in that order, retaining all four volumes,
and restoring the prior immutable native image. Shadow rollback never enables
an exchange wallet or changes the deployed legacy system.
