# Native Migration Phase Acceptance Gates

These are minimum stage gates. A phase cannot be declared complete by code
existence alone. Each phase must include its architecture diagram, repository
tree delta, implementation, unit and integration tests, operational
documentation, forward migration, and rollback procedure.

Numeric strategy-promotion thresholds live in a versioned promotion policy and
must be frozen before an experiment begins. They are not weakened after seeing
results.

## Global gates

Every phase requires:

- clean CI on the pinned Debian/Python/Rust environment;
- no secrets in Git, images, logs, reports, fixtures, or artifacts;
- dependency lock and image provenance verification;
- typed configuration with unknown-key rejection;
- documentation links and diagrams validated;
- backward-compatible schemas or an explicit versioned migration;
- no unexpected modification to the deployed legacy runtime;
- recorded reviewer and acceptance evidence.

## Phase 1: Architecture

- BTC perpetual identity and single-venue scope are explicit.
- Service ownership and hot-path dependencies are unambiguous.
- Market-by-price queue limitations and liquidation-data gaps are documented.
- Production promotion requires signed human approval.
- File disposition covers every tracked repository group.
- Owner approves merge.

## Phase 2: Foundation

- A clean checkout builds and tests without untracked local dependencies.
- Python, Rust, OS packages, and images are exactly pinned or locked.
- Containers run non-root with read-only roots and explicit writable volumes.
- Production/live configuration fails closed when approval, limits, account,
  secrets, or artifact hashes are absent.
- CI includes lint, formatting, typing, unit tests, dependency audit, secret
  scan, image build, and documentation validation.

## Phase 3: Market data

- Every received frame is archived before normalization.
- A raw segment can be deterministically normalized twice to identical output.
- Partial/corrupt segments are quarantined and excluded from manifests.
- Reconnects, silence, duplicates, schema errors, timestamp regressions, crossed
  books, and disk pressure are observable and tested.
- Research loaders reject unexplained or policy-exceeding gaps.
- A sustained public-feed soak completes with all discontinuities classified.
- The platform reports market-wide liquidation availability honestly.

## Phase 4: Execution and risk

- Only Nautilus owns normal order submission; only the sentinel owns emergency
  SDK control actions.
- API wallets are separated by process and environment.
- Testnet covers post-only, IOC, cancel, cancel-replace, partial fill, reject,
  reduce-only, restart, and reconciliation paths.
- Unknown submission outcomes resolve by reconciliation without duplicate risk.
- Strategies cannot bypass hard position, inventory, leverage, order, loss,
  drawdown, stale-data, or kill controls.
- Reconciled startup orders drain before alpha, replacements wait for terminal
  cancel outcomes, and daily/high-water equity baselines survive restart.
- Exchange dead-man cancellation and local/operator kills are demonstrated.
- No mainnet key is available to the testnet deployment.
- The stopped evidence bundle has an exact declared inventory, complete
  scenario-to-artifact lineage, valid execution/sentinel hash chains, a healthy
  SQLite journal, and a reproducible canonical observation.

## Phase 5: Backtesting

- Event timestamps and transformations are causal and deterministic.
- Train, validation, embargo, walk-forward test, and final holdout windows are
  disjoint according to policy.
- Hyperparameter selection never observes its scored holdout.
- Queue, latency, fee, funding, and slippage assumptions are versioned.
- Candidates are tested under baseline and pessimistic execution assumptions.
- HftBacktest and Nautilus decision parity passes for the shared kernel.
- Known synthetic scenarios reproduce expected order and fill outcomes.

## Phase 6: Research

- Live and offline feature parity passes on identical event streams.
- Every experiment records code, data, schema, configuration, dependency, and
  model identities.
- Randomized-label and no-signal negative controls do not produce promotable
  candidates.
- Model loading rejects mismatched feature schemas and unsafe artifact formats.
- Post-cost performance, drawdown, tail loss, inventory, fills, markouts,
  latency, stability, and drift are reported.
- Automated workflows can advance only to `AWAITING_APPROVAL`.

## Phase 7: Paper

- The exact production strategy/risk code runs on live feeds.
- No exchange trading key is mounted.
- Paper fill assumptions are calibrated and sensitivity-tested.
- Required independent decisions/fills and regime coverage meet the frozen
  policy; elapsed days alone are insufficient.
- Restart, stale-data, loss, drawdown, kill, and observability drills pass.
- A minimum 14-day observation is recommended unless a stricter policy applies.

## Phase 8: Shadow

- The production image and configuration run on the intended host.
- The execution sink cannot emit an exchange order, verified by credentials and
  egress controls as well as unit tests.
- Recorded commands are complete enough for counterfactual execution analysis.
- Operational availability, latency, decision stability, markouts, and drift
  meet the frozen policy.
- Host reboot, disk pressure, clock, recorder, and monitoring failures have
  demonstrated responses.
- A minimum seven-day observation is recommended unless sample or regime
  requirements demand longer.

## Phase 9: Mainnet

Implementation status: automated admission controls are present; this phase is
not accepted and no mainnet release is authorized until every item below has
retained evidence and human approval.

- An active signed authorization—initial deployment approval or chained
  production renewal—binds the exact image, commit, dataset, model,
  configuration, account, capital, limits, and rollback target.
- Production operation beyond the initial approval window uses an unbroken,
  signed renewal chain. Each renewal preserves the exact admission and release,
  extends only an unexpired authorization, and is retained in ledger history.
- The exact release passes a final testnet dress rehearsal.
- The final-testnet report covers every frozen lifecycle scenario, binds the
  exact commit, image, lock, dataset, model selection, feature schema, strategy,
  risk policy, and target behavior, and records that no mainnet credential was
  present.
- A credential-free independent verification reproduces the observation from
  the retained bundle before policy evaluation and reviewer sign-off.
- Unsigned bundle preparation rejects any artifact/evidence mismatch and stops
  before signature or admission; a separate offline signature is mandatory.
- Account, subaccount, API wallet, and instrument are verified independently.
- Initial capital and hard limits equal the approved canary values.
- Dead-man cancellation, sentinel, reconciliation, alerts, backups, and operator
  access are healthy before order entry is enabled.
- A bounded live cancel/kill drill is completed.
- Increasing capital or replacing the champion requires a separate approval.

## Phase 10: Legacy retirement and cleanup

Implementation status: typed evidence, frozen baseline policy, deterministic
native-production, legacy-archive, final-state, cross-bundle readiness, and
disabled-window assembly/replay, exact cleanup-manifest validation, and offline
Ed25519 verification are present. The active MT5 runtime is not authorized for
shutdown or removal.

- The exact native production deployment and admission complete the frozen
  stable-observation window with zero critical incidents, reconciliation
  failures, or risk breaches.
- An independent credential-free reassembly verifies the externally pinned
  production signer, all nine release artifacts, schema-v2 ledger, complete
  signed renewal chain, and hash-linked operational evidence. Successful
  sentinel/dead-man samples span the interval with no gap over five minutes.
- Native rollback, backup/restore, alert-delivery, and operator-access drills
  pass and their evidence is retained.
- MT5 is entry-paused, demo-only, independently confirmed flat for managed and
  unmanaged positions and pending orders, and has no command-file writer.
- The complete credential-free final archive is hash-bound, restored from a
  separate destination, retained for the policy period, and tagged once as
  `mt5-final` at the archived commit.
- Independent `assemble-archive` and `verify-archive` replays require exactly
  eleven categories, byte-identical isolated restore results, the externally
  pinned recursive zero-finding credential scan, annotated tag evidence, and
  at least 365 days of remaining retention.
- Independent `assemble-final-state` and `verify-final-state` replays reconcile
  raw trade-report, broker-export, MT5-status, pause-file, and five-surface
  writer evidence inside that archive. Capture skew is at most five minutes,
  state age is at most one hour, and counts are derived rather than asserted.
- Independent `assemble-readiness` and `verify-readiness` replays bind the
  schema-v3 native retirement identity to the exact legacy case and recheck
  authority/freshness after both roots are read. `evaluate-readiness` repeats
  source replay before emitting a report.
- A non-expired signed `stop_and_observe` approval binds the exact readiness
  report, native identity, archive, source commit, and tag.
- Every PM2, cron, nginx, logrotate, autostart, Wine/MT5, and command-writer
  capability remains disabled with zero active instances for the frozen window.
- The disabled window has zero legacy broker orders, stable native operation,
  quarantined legacy credentials, a reverified archive, and no capability
  evidence gap over five minutes. `evaluate-disabled` replays every immutable
  root and external trust input before emitting a report.
- A separate non-expired signed `remove_and_clean` approval binds the exact
  disabled report and canonical cleanup manifest.
- The canonical cleanup manifest is schema v2, derived with
  `assemble-cleanup-manifest`, and reproduced by an independent
  `verify-cleanup-manifest` replay of the exact scope/state/ownership bundle;
  schema-only validation is insufficient.
- Cleanup targets contain no globs, traversal, unresolved variables, broad host
  roots, or unowned shared-package removals.
- The removal PR is mechanical, completes ADR 0008, preserves evidence and Git
  history, changes no strategy/risk behavior, and passes the entire native suite
  from a clean checkout.

## Promotion metrics

The policy must define at least:

- minimum independent trades, fills, quoted time, and market regimes;
- post-fee and post-funding PnL/expectancy and confidence bounds;
- maximum drawdown, tail loss, inventory exposure, and liquidation distance;
- minimum consistency across walk-forward folds and recent holdout;
- maker ratio, fill calibration error, cancel-to-fill ratio, and adverse markout;
- latency percentiles, rejection rate, reconnects, and reconciliation failures;
- feature/model drift bounds and behavior on missing or stale inputs.

No single metric, including prediction accuracy, Sharpe ratio, or total PnL, is
sufficient for promotion.
