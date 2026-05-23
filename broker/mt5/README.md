# MT5 Bridge

FinRobot uses Python as the strategy brain and MT5 as the broker execution layer.

- `FinRobotBridgeEA.mq5` polls `finrobot_commands.csv` from the MT5 common files directory.
- It executes demo MT5 market/close commands and writes `finrobot_acks.csv` plus `finrobot_status.json`.
- Keep this EA attached to a lightweight chart in the headless Wine/Xvfb MT5 terminal.

This is intentionally a thin bridge: strategy logic, memory, model review, and code changes stay in Python/Opencode.
