# Phase 9: Production Admission Boundary

Status: implementation complete; empirical acceptance and mainnet activation
remain pending.

This phase creates the Linux-native mainnet admission boundary. It does not
approve a strategy, fund an account, enable a checked-in overlay, place an
order, retire MT5, or claim that Phases 3-8 have passed their empirical gates.

## Delivered boundary

- schema-v2 human approval for one exact canary or production deployment;
- Ed25519 verification against a configured public-key fingerprint and key ID;
- binding of commit, image digest, dependency lock, dataset, model, feature
  schema, strategy configuration, risk policy, shadow evidence, testnet
  evidence, account/vault, wallet roles, capital, expiry, and rollback target;
- a SQLite `FULL`-synchronous anti-replay ledger with one active admission per
  account/vault;
- an explicit controller action between successful verification and runtime
  authority;
- independent bundle, derived-wallet, and ledger checks in the trading node and
  safety sentinel;
- an admission check immediately before every submit or replace. Cancellation
  remains available when admission fails;
- immutable canary ceilings of 1x leverage, 0.002 BTC/order, 0.01 BTC position,
  USD 100/order, USD 500 inventory, two open orders, and two submissions/second;
- a USD 1,000 canary capital hard cap and USD 100,000 application hard cap for
  a separately approved production deployment;
- frozen canary evidence gates which can produce only
  `awaiting_production_approval`;
- an exact-digest Compose topology with process-separated trading and control
  wallets and a credential-free admission controller.

Every checked-in environment still has `execution.enabled = false`.

## Admission sequence

1. Complete and review every preceding phase gate and the exact-image testnet
   dress rehearsal.
2. Assemble immutable evidence artifacts under one read-only root.
3. Produce the manifest and schema-v2 approval. Canonicalize the approval, then
   sign those exact bytes with the offline Ed25519 approval key.
4. A credential-free controller verifies the signature, all artifact bytes,
   configured risk, image, commit, dependency lock, account, capital, expiry,
   and rollback identity.
5. A human operator explicitly writes the verified identity to the admission
   ledger. Verification alone has no execution authority.
6. The trading node derives its public address from the mounted trading key;
   the sentinel independently derives the control-wallet address. Each verifies
   its assigned role and requires the same active ledger identity.
7. Every exposure-increasing command checks the ledger again. Expiry, revoke,
   rollback, file tampering, identity drift, wallet mismatch, excess capital,
   or reconciliation/freshness failure closes the order path.
8. Canary evidence is evaluated against the policy frozen before observation.
   Passing evidence cannot scale capital. It must be bound into a new production
   manifest and separately signed production approval.

## Design decisions

| Decision | Why | Alternatives and tradeoffs | Performance implication |
|---|---|---|---|
| Ed25519 detached approval | Small deterministic signatures and an offline trust root. | Online KMS signing was rejected for this boundary because compromise or policy error could create deployment authority remotely. A future HSM can hold the same offline role. | Verification is startup-only and negligible. |
| Canonical JSON plus content hashes | Review and signatures bind exact, portable bytes. | Signing mutable paths or registry tags permits substitution. Sigstore could add supply-chain attestations later but does not replace account/capital approval. | Artifact hashing is intentionally outside the hot path. |
| SQLite admission ledger | Durable, auditable, local anti-replay state with transactional canary-to-production replacement. | DuckDB is unsuitable for the online authority; a remote database adds a network dependency. | One indexed local read per submit/replace; no network round trip. |
| Independent runtime verification | A controller compromise or stale startup result cannot silently authorize the wallets. | Passing a controller token would reduce work but create a bearer capability and single point of failure. | Full artifact verification occurs once at process start; ledger checks are small indexed reads. |
| Separate control wallet | Sentinel can cancel and renew dead-man protection without normal order APIs in its code surface. | One shared wallet reduces operations but defeats process and credential separation. | One additional lightweight process and SDK session. |
| Capital clamp plus vault support | A canary cannot inherit a large master-account balance accidentally. | Policy-only notional limits do not bound all account-level failure modes. | One decimal comparison in synchronous risk evaluation. |
| No automatic production promotion | Live economics and safety evidence still require human judgment. | Automatic scale-up conflicts with the governing mandate. | No hot-path cost. |

## Failure behavior

An invalid or missing approval prevents process startup. An admission which
becomes inactive marks the heartbeat unhealthy, denies submit/replace, stops
dead-man renewal, and triggers repeated sentinel cancel-all attempts. It does
not automatically flatten a position because the sentinel deliberately has no
order-placement capability. The account owner performs a reviewed emergency
flatten through the venue control plane when required.

## Acceptance still required

- reviewed Phase 3-8 evidence and calibrated scenarios;
- exact-image final testnet dress rehearsal;
- two-person verification of master account, optional vault, both wallets, BTC
  perpetual, limits, funding, and rollback target;
- minimum-capital canary funding and observation;
- retained fee, funding, fill, markout, drawdown, alert, backup/restore,
  restart/reconciliation, credential-rotation, operator-kill, and dead-man
  evidence;
- a separate signed approval before production scale;
- explicit owner approval for the final MT5 archival and retirement runbook.

See the [mainnet runbook](../operations/MAINNET_CANARY_RUNBOOK.md),
[retirement runbook](../operations/LEGACY_RETIREMENT_RUNBOOK.md), and
[architecture diagram](../architecture/diagrams/phase-9-production-admission.mmd).
