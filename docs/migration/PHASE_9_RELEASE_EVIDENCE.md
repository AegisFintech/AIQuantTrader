# Phase 9: Exact-Release Evidence and Bundle Preparation

Status: implementation complete; final testnet observation, offline signature,
mainnet admission, funding, and live operation remain pending.

This increment closes the gap between a reviewed candidate and the existing
signed-admission boundary. It prepares evidence and an unsigned release bundle;
it cannot sign, admit, fund, or start a mainnet deployment.

## Delivered boundary

- a frozen final-testnet policy covering all 15 execution and safety scenarios;
- typed observations and hash-identified gate reports which stop at
  `awaiting_canary_approval`;
- an exact-image, testnet-only Compose rehearsal with process-separated trading
  and control wallets;
- a deterministic target-behavior fingerprint derived from a checked-in,
  execution-disabled canary or production environment plus bounded release
  inputs; the rendered target explicitly enables live alpha and derives its
  strategy ID from the exact release artifact;
- semantic validation across dependency lock, dataset, selected model, feature
  schema, strategy, risk policy, shadow evidence, testnet evidence, and, for
  production, canary evidence, including live feature-config parity with the
  shadow run and strategy bounds within the signed hard-risk policy;
- atomic preparation of a credential-free, unsigned release directory;
- schema, unit, CLI, security-boundary, Compose, and production-lineage tests.

Every checked-in TOML environment remains execution-disabled. The rehearsal is
available only through the explicit `release-rehearsal` profile and testnet
credentials.

## Release sequence

```text
frozen candidate + exact image digest
  -> render target behavior fingerprint
  -> run that exact image on testnet
  -> retain real venue, journal, metric, and drill evidence
  -> deterministically assemble and verify the canonical observation
  -> evaluate the frozen 15-scenario testnet policy
  -> semantically verify every release artifact
  -> atomically prepare unsigned bundle
  -> independent review
  -> offline signature outside the production host
  -> existing verify + explicit admit boundary
```

The evaluator never collects or invents observations. `aqt-acceptance` now
constructs the typed observation from a stopped, strict, hash-bound evidence
directory. It derives journal facts, verifies the two operational hash chains,
and rejects missing or extra evidence, but it does not query the venue or infer
externally unobservable facts. A scenario cannot pass because a unit test passed
or because a field was omitted.

The bundle preparer emits:

```text
artifact-manifest.json
behavior-configuration.json
deployment-approval.unsigned.json
release-bundle-receipt.json
artifacts/
  uv.lock
  dataset-manifest.json
  model-manifest.json
  feature-schema.json
  strategy-config.toml
  risk-policy.json
  shadow-evidence.json
  testnet-evidence.json
  canary-evidence.json       # production only
```

It does not emit a signature, private key, public trust root, final
`deployment-approval.json`, or admission ledger record. The offline signer must
sign the exact unsigned approval bytes and place the unchanged bytes at the
final approval path alongside the detached signature and reviewed public key.

## Integrity and failure behavior

Artifact sources must be absolute, unique, non-symlink regular files with a
bounded non-zero size. The preparer parses typed manifests and strategy TOML,
checks model/feature/strategy/dependency compatibility, regenerates the risk
policy from validated limits, and compares every evidence identity with the
proposed release. Canary capital cannot exceed USD 1,000; production cannot
exceed the application USD 100,000 hard cap; approved capital must cover the
inventory limit.

Production preparation additionally requires a passing canary report whose
deployment is the proposed rollback target. A canary artifact cannot be added
to a canary bundle, and a production bundle cannot omit it or the prior
approval ID.

The output path must be a new absolute directory. Files are written mode 0600
under a mode-0700 temporary directory, synchronized, and atomically renamed.
Any interrupted write removes the temporary directory. Reusing an existing
output directory fails rather than overwriting reviewed evidence.

## Design decisions

| Decision | Why | Alternatives and tradeoffs | Performance implication |
|---|---|---|---|
| Separate target fingerprint from the disabled checked-in overlay | The exact enabled behavior must be rehearsed and signed without committing an enabled environment. | Committing a live overlay is simpler but creates accidental execution risk; hashing only the disabled file would attest to the wrong behavior. | Startup/release-only canonicalization; no hot-path cost. |
| Typed no-model selection | A non-ML strategy still needs an explicit, hash-bound model decision. | An empty model file is ambiguous; requiring a trained model would misrepresent the scalper. | One small JSON parse during preparation. |
| Frozen complete scenario matrix | Missing or selectively reported lifecycle tests must fail closed. | Free-form reports are flexible but cannot prove coverage. | Evidence evaluation is linear in 15 scenarios and negligible. |
| Offline strict evidence assembly | Observation counts must be reproducible from retained bytes without giving the collector a wallet. | Direct live API collection is convenient but introduces credentials, mutable responses, and another principal. | Two bounded inventory scans and one read-only SQLite scan, all outside the hot path. |
| Credential-free unsigned preparation | Production hosts and automation must not gain approval authority. | Online signing is convenient but turns host compromise into release authority. | Offline review adds operational latency, intentionally outside trading. |
| Semantic checks in addition to SHA-256 | Correct hashes can still bind mutually incompatible artifacts. | Hash-only manifests are faster to implement but do not catch strategy/schema/model mismatch. | Bounded startup/release parsing; no order-path work. |
| Atomic new-directory output | Reviewers must never observe or sign a partial or overwritten bundle. | In-place writes require recovery rules and permit mixed generations. | A small amount of fsync and one same-filesystem rename per release. |

## Acceptance still required

- the exact release image completes the real testnet matrix with retained raw
  evidence and no mainnet credentials;
- two reviewers verify the observation, hashes, account roles, risk, capital,
  expiry, and rollback target;
- the offline approver signs the exact approval bytes;
- the credential-free controller verifies and a human explicitly admits the
  release;
- minimum-capital canary operation and its required drills pass before any
  separate production approval is considered.

Rollback before admission is deletion or archival of the unsigned output and a
return to the previous evidence stage. After admission, use the terminal
revoke/rollback process in the mainnet runbook. This increment does not alter or
restart the deployed MT5 demo runtime.

See the [mainnet canary runbook](../operations/MAINNET_CANARY_RUNBOOK.md),
[execution-risk runbook](../operations/EXECUTION_RISK_RUNBOOK.md), and
[release-evidence diagram](../architecture/diagrams/phase-9-release-evidence.mmd).
The exact evidence-directory contract is in
[`PHASE_4_TESTNET_ACCEPTANCE_EVIDENCE.md`](PHASE_4_TESTNET_ACCEPTANCE_EVIDENCE.md).
