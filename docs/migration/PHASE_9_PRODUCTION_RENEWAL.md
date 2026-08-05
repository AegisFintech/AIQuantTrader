# Phase 9: Chained Production Authorization Renewal

Status: implementation complete; no renewal, admission, funding, or mainnet
activation has been performed

The initial Phase 9 admission expires no more than seven days after its signed
approval. Phase 10 requires at least 30 continuous days of stable native
production before MT5 may be stopped. A terminal admission with no renewal path
made that evidence window impossible and would also prevent sustained 24/7
production. This increment adds a narrow renewal authority without creating a
new deployment or permitting a release change.

## Safety boundary

A `DeploymentAuthorizationRenewal` can extend only an existing, unexpired,
active production admission. It binds:

- the original deployment, approval, and admission identities;
- the immediately preceding authorization identity;
- production stage, account, and optional vault;
- exact artifact manifest, configuration, and image identities;
- the unchanged capital ceiling;
- approver and a timezone-aware window of at most seven days.

The detached Ed25519 signature is verified against the same configured key ID
and public-key fingerprint as deployment admission. The controller re-verifies
the original signed release, runtime commit/image, dependency lock, and bound
artifact bytes before it verifies or applies a renewal. The ledger then repeats
the immutable-field and authorization-chain checks inside one `BEGIN IMMEDIATE`
transaction.

Renewal cannot:

- renew canary, rolled-back, revoked, superseded, or expired authority;
- change deployment, account/vault, artifacts, configuration, image, or capital;
- skip or reuse a prior authorization identity;
- create a strategy, model, risk, funding, wallet, or order authority;
- sign itself or promote a challenger.

## Authorization flow

See
[`phase-9-production-renewal.mmd`](../architecture/diagrams/phase-9-production-renewal.mmd).

```text
active production admission (authorization A0, expiry T0)
  -> offline review of unchanged release and native health
  -> signed renewal R1 binds A0 and expiry T1 > T0
  -> controller re-verifies original release + R1
  -> ledger atomically records authorization A1 and T1
  -> trading node and sentinel read T1 from the ledger
  -> next renewal must bind A1; expiry/revoke/rollback still fail closed
```

The original `deployment_id`, `approval_id`, and `admission_id` remain stable.
The ledger schema-v2 record adds `authorization_id` and `renewal_count`; each
accepted renewal is retained in a separate immutable history row. Existing
schema-v1 ledgers migrate transactionally with
`authorization_id=admission_id`, `renewal_count=0`, and no extension of expiry.
Because schema v1 did not retain the admitted public-key fingerprint, migrated
records remain renewal-ineligible; complete a fresh reviewed release/admission
sequence instead of trusting a mutable key setting.

## Decisions, alternatives, and tradeoffs

| Decision | Why chosen | Alternatives considered | Tradeoff and performance impact |
|---|---|---|---|
| Chained short-lived renewals | Preserves the seven-day human review cadence while permitting continuous production and a truthful Phase 10 observation. | A 30- or 365-day approval weakens expiry as a safety control; an indefinite admission removes periodic review. | One offline signature and controller transaction per renewal; no network or model hot-path work. |
| Preserve admission identity | Phase 10 can bind one deployment/admission across the complete native observation while authorization rotates independently. | Re-admitting a new deployment every week breaks observation lineage and the canary-to-production transition model. | One additional indexed ledger field and renewal-history row. |
| Exact immutable renewal | Capital, alpha, risk, image, configuration, and artifacts require the existing release/promotion process, not a renewal shortcut. | Allowing bounded changes during renewal reduces operator work but creates an unreviewed production-change channel. | Controller performs bounded startup hashing; order-path limits are unchanged. |
| Transactional prior-authorization chain | Concurrent or replayed renewals cannot both succeed, and reviewers can reconstruct every authority interval. | Last-write-wins expiry updates are simpler but permit replay and lost updates. | One `BEGIN IMMEDIATE` SQLite transaction per weekly renewal. |
| Ledger expiry is runtime authority | After the original approval window, processes may re-verify its immutable identity but must still find the same unexpired durable admission. | Treating approval expiry only as an admission deadline would permit indefinite runtime without review. | Existing indexed ledger read remains on exposure-changing checks; heartbeat/metrics read the renewed expiry. |

## Commands

Create a root-owned renewal draft outside the repository from the current
schema-v2 ledger record. Canonicalization does not sign or apply it:

```bash
aqt-governance canonicalize-renewal \
  --input /secure/release/renewal-draft.json \
  --output /secure/release/renewal.json
```

Sign the exact canonical bytes offline and return a
`DetachedApprovalSignature`. Verification is read-only and repeats original
release verification:

```bash
aqt-governance verify-renewal \
  --config-dir /etc/aiquanttrader-native --environment production \
  --code-identity "$AQT_MAINNET_COMMIT_SHA" \
  --image-identity "$AQT_MAINNET_IMAGE_DIGEST" \
  --dependency-lock-path /opt/aiquanttrader/release/uv.lock \
  --deployment-id "$AQT_MAINNET_DEPLOYMENT_ID" \
  --renewal /secure/release/renewal.json \
  --signature /secure/release/renewal.sig.json \
  --public-key /secure/release/approver-ed25519.pub.pem
```

After independent review, apply the same files with a named actor and reason:

```bash
aqt-governance renew \
  --config-dir /etc/aiquanttrader-native --environment production \
  --code-identity "$AQT_MAINNET_COMMIT_SHA" \
  --image-identity "$AQT_MAINNET_IMAGE_DIGEST" \
  --dependency-lock-path /opt/aiquanttrader/release/uv.lock \
  --deployment-id "$AQT_MAINNET_DEPLOYMENT_ID" \
  --renewal /secure/release/renewal.json \
  --signature /secure/release/renewal.sig.json \
  --public-key /secure/release/approver-ed25519.pub.pem \
  --actor "$AQT_RELEASE_ACTOR" --reason "$AQT_RELEASE_REASON"
```

No command accepts an approval private key. If the current authorization has
expired, remain halted and perform a new reviewed release/admission; renewal
cannot revive it.

## Evidence and Phase 10 binding

`NativeProductionObservation` now records the terminal authorization identity,
renewal count, and authorization expiry. Its expiry must follow the observation
end; an unrenewed observation must terminate at the original admission identity,
while a renewed observation must bind the final renewal. The retained native
evidence bundle must include the ordered renewal history and ledger transitions.

This enables but does not satisfy the 30-day Phase 10 gate. Mainnet activation,
the empirical observation, retirement readiness, and both retirement approvals
remain separate unfinished actions.

## Tests and rollback

Tests cover signature and trust-root tampering, expiry, immutable-field
mismatch, chain replay, atomic single use, runtime guard continuity, renewed
heartbeat expiry, CLI verification/application, and schema-v1 migration without
time extension. Revoke and rollback remain terminal. A bad or missed renewal
halts new exposure and dead-man renewal exactly as before; it never restarts MT5
or automatically creates replacement authority.
