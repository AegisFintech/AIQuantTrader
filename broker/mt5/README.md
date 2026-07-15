# MT5 Bridge

FinRobot uses MT5 as the active demo execution layer.

- `FinRobotBridgeEA.mq5` polls `finrobot_commands.csv` from the MT5 common files directory.
- It executes demo MT5 market/close commands and writes `finrobot_acks.csv` plus `finrobot_status.json`.
- Keep this EA attached to a lightweight chart in the headless Wine/Xvfb MT5 terminal installed by `./install.sh` under `.runtime/`.

Runtime management is intentionally simple: PM2 starts MT5 and the autonomous review loop, and the EA writes status/deal files for Python reports.

As of `FinRobotBridgeEA` v1.41, XAUUSD is the only managed auto-trading symbol and the active signal timeframe is M1. It scans Monday-Friday when the broker symbol is inside its configured trade session. Entries still require spread, premium/discount smart-money score 4+, position-count, and daily-risk checks before any order is sent. An optional strengthening MACD-histogram alignment gate is runtime-profile controlled and defaults off until walk-forward evidence supports promotion. Planned stop risk is hard-capped at 1.00% of the broker-day equity snapshot per position, including score multipliers. Per-symbol `signal_telemetry` in `finrobot_status.json` records daily fills and major rejection reasons. While `finrobot_entry_pause.flag` exists in Common Files, new automatic and command-file market entries are rejected without disabling heartbeat, position management, or close commands. The EA also refreshes `finrobot_export_XAUUSD_M1.tsv` with epoch timestamps every six hours for the offline research pipeline.
