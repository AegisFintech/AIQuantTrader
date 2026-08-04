# Legacy MT5 Retirement Runbook

Status: procedure only. MT5 retirement is not authorized by the Phase 9
admission implementation.

Use this runbook only after a separately approved native stable-observation
period. Rollback never silently restarts MT5 trading.

## Entry criteria

- production admission and native observation have passed the agreed gates;
- account ownership, backups, alerts, on-call access, and native rollback have
  been exercised;
- MT5 has no unmanaged position or pending order and its final report is
  reviewed;
- the owner explicitly approves the retirement window and retention plan.

## Archive

1. Run `python3 scripts/mt5_trade_report.py` and save the report with its hash.
2. Archive MT5 Common Files, final configuration, EA source and compiled hash,
   deal/order exports, strategy profiles, research registry, logs, and the
   operator timeline without committing credentials.
3. Verify the archive on a separate destination and perform a restore test.
4. Record broker account, XAUUSD-only scope, open-position count, pending-order
   count, final PnL, and retention expiry.
5. Create the annotated `mt5-final` tag only after the archive review. Never
   move or reuse that tag.

## Disable and observe

1. Activate the MT5 entry pause and confirm no new entries.
2. Close or transfer responsibility for every broker position under explicit
   owner direction; do not infer flatten authority from this document.
3. Stop and disable only the four documented PM2 legacy processes.
4. Leave files intact through the rollback observation window.
5. Verify the native system remains healthy and that no MT5/Wine process or
   command-file writer can trade.

## Removal PR

After the observation window, create a dedicated mechanical PR which removes
legacy code and service definitions according to `FILE_DISPOSITION.md`, updates
the package layout from ADR 0008, and contains no strategy or native-risk
changes. Preserve the `mt5-final` tag and evidence archive. Review deletion
targets exactly; do not delete runtime or evidence roots with broad globs.

## Failure

If native operation is unsafe, revoke native admission, cancel orders, and
leave the account halted. Restoring MT5 requires a new explicit owner decision,
credential validation, archive verification, demo-first rehearsal, and a new
deployment record. It is not the automatic rollback target.
