# MT5 Bridge

FinRobot uses MT5 as the active demo execution layer.

- `FinRobotBridgeEA.mq5` polls `finrobot_commands.csv` from the MT5 common files directory.
- It executes demo MT5 market/close commands and writes `finrobot_acks.csv` plus `finrobot_status.json`.
- Keep this EA attached to a lightweight chart in the headless Wine/Xvfb MT5 terminal installed by `./install.sh` under `.runtime/`.

Runtime management is intentionally simple: PM2 starts MT5 and the autonomous review loop, and the EA writes status/deal files for Python reports.

As of `FinRobotBridgeEA` v1.29, BTCUSD bypasses the London/NY session gate by default for continuous scanning, while XAUUSD scans Monday-Friday when the broker symbol is inside its configured trade session. Both symbols still require spread, smart-money, position-count, and daily-risk checks before any order is sent; BTCUSD also has ATR/target-distance cost filters. Per-symbol `signal_telemetry` in `finrobot_status.json` records daily fills and major rejection reasons.
