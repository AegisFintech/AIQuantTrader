# MT5 Bridge

FinRobot uses MT5 as the active demo execution layer.

- `FinRobotBridgeEA.mq5` polls `finrobot_commands.csv` from the MT5 common files directory.
- It executes demo MT5 market/close commands and writes `finrobot_acks.csv` plus `finrobot_status.json`.
- Keep this EA attached to a lightweight chart in the headless Wine/Xvfb MT5 terminal installed by `./install.sh` under `.runtime/`.

Runtime management is intentionally simple: PM2 starts MT5 and the autonomous review loop, and the EA writes status/deal files for Python reports.
