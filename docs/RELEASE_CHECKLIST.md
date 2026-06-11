# FinRobot Release Checklist

Run these steps **in order** before any change to the MT5 EA source, the
risk model, or the bridge protocol. The order matters: an early
`pm2 restart` on broken code breaks the live demo.

```bash
cd /root/FinRobot
```

## 1. Pre-flight (no live impact)

- [ ] `git status` — confirm you are on the right branch and the working tree is clean of unrelated edits.
- [ ] `git log --oneline -5` — confirm the commit you intend to ship is the head.
- [ ] `cat broker/mt5/FinRobotBridgeEA.mq5 | grep '^#property version'` — confirm the version string is bumped.
- [ ] `.venv/bin/python -m pytest -q -p no:cacheprovider` — all tests pass (target: 26 passing).

## 2. Compile the EA

- [ ] `./scripts/sync_mt5_ea.sh` — copies the EA source into the MT5 Experts directory and compiles via MetaEditor.
- [ ] `tail -3 .runtime/mt5/terminal/current/compile.log` — last line must be `Result: 0 errors, ...`. The same two pre-existing `RiskManagement.mqh` name-shadowing warnings (lines 15 and 49) are expected and accepted.
- [ ] `ls -la .runtime/mt5/terminal/current/MQL5/Experts/FinRobot/FinRobotBridgeEA.ex5` — confirm mtime is fresh (just-compiled) and size > 100KB.

## 3. Live status before deploy

- [ ] `python3 scripts/mt5_trade_report.py` — capture open positions, daily PnL, and the `money_management` block. If you are deploying the risk-semantics change, the daily_equity_snapshot is what the new per-trade / daily-cap math uses.
- [ ] `python3 scripts/healthcheck.py` — all checks should be `OK`. If anything is `FAIL`, fix the underlying issue before deploying the new EA on top of it.

## 4. Deploy

- [ ] `pm2 restart mt5-terminal --update-env` — restart MT5 to load the freshly-compiled `.ex5`.
- [ ] `sleep 35` — wait for the EA to attach, the first timer tick, and a fresh heartbeat write to MT5 Common Files.

## 5. Post-deploy verification

- [ ] `pm2 list` — `mt5-terminal` is `online`, no new restart loop (restarts should not jump from 13 to 100s).
- [ ] `python3 -c "import json; d=json.load(open('/root/FinRobot/.runtime/wineprefix/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/Common/Files/finrobot_status.json')); print(d.get('money_management'))"` — confirm the new `daily_risk_per_trade_fraction` and `daily_loss_limit_fraction` values match what you intend.
- [ ] `python3 scripts/healthcheck.py` — all checks `OK` after the restart. Exit code 0.
- [ ] `python3 scripts/mt5_trade_report.py` — re-run and diff against the pre-deploy snapshot. No new orders, no new rejected positions, no change in the symbol's last_signal unless expected.

## 6. Cleanup (optional but recommended)

- [ ] `git tag -a v1.30 -m "Phase 1 hardening + risk semantics"` — tag the released commit.
- [ ] `python3 scripts/archive_common_files.py` — manual snapshot under `state/mt5/archive/<date>/<ts>/` for the deploy moment.

## 7. Cron wiring (one-time, after first deploy)

- [ ] `sudo scripts/install_cron.sh` to drop `config/finrobot.cron` into `/etc/cron.d/finrobot`
- [ ] `cat /etc/cron.d/finrobot` to confirm the install
- [ ] Wait ~3 minutes, then `tail -20 logs/cron.log` to see fresh entries from the 1-minute jobs
- [ ] Wait ~24 hours, then `ls -la state/mt5/archive/` to confirm daily archives are landing

## 8. If you broke it

- [ ] `pm2 logs mt5-terminal --lines 200` — inspect the live journal for the failure mode.
- [ ] `pm2 restart mt5-terminal --update-env` — restart to roll back to whatever `.ex5` is on disk.
- [ ] If the issue is in the source: `git revert HEAD`, then re-run this checklist from step 1.
