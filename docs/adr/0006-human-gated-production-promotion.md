# ADR 0006: Human-Gated Production Promotion

Status: proposed; accepted when the migration blueprint PR is merged
Date: 2026-08-04

## Context

The platform must research and retrain continuously without allowing noisy,
leaked, overfit, or operationally unsafe results to replace production capital
automatically.

## Decision

Automation may create and advance challengers through backtest, walk-forward,
paper, and shadow gates, but it stops at `AWAITING_APPROVAL`. Only a signed human
approval can create `APPROVED_CANARY`. Scaling a canary to normal production
capital requires another approval.

An approval binds:

- code commit and container digest;
- dataset, feature schema, model, and configuration hashes;
- promotion report and threshold policy version;
- capital ceiling and risk limits;
- approver identity, timestamp, expiry, and rollback target.

Automated suspension and rollback to a previously approved artifact are
permitted because they reduce risk. Automated promotion, policy relaxation,
capital increase, and approval extension are prohibited.

## Alternatives considered

- Fully autonomous production replacement: rejected because statistical gates
  cannot cover data corruption, regime breaks, implementation defects, and
  operational context.
- Manual research and deployment: safer in one dimension but too slow and
  irreproducible for continuous challenger generation.
- Approval by editable environment variable: rejected because it is neither
  artifact-bound nor auditable.

## Consequences

- Research and deployment remain automated except for the explicit production
  authority boundary.
- Every running champion is reproducible from immutable artifacts.
- Emergency rollback remains fast and does not require approval to reduce risk.
