# MT5 Headless Bridge

Status: installed and running under PM2 as `mt5-terminal`.

- Server/account: ICMarketsSC-Demo demo account, loaded from `.env`.
- Wine prefix: `/home/openclaw/.wine-mt5`
- Terminal symlink: `/home/openclaw/mt5/terminal/current`
- Start script: `scripts/start_mt5.sh`
- Setup script: `scripts/setup_mt5_headless.sh`
- Status helper: `scripts/mt5_status.py`
- Bridge EA: `broker/mt5/FinRobotBridgeEA.mq5`

The EA polls MT5 common files:

- `finrobot_commands.csv` for commands
- `finrobot_acks.csv` for execution acks
- `finrobot_status.json` for account/bridge heartbeat

The terminal is logged in and visible through Xvfb. The next validation step is attaching `FinRobotBridgeEA` to a lightweight chart and enabling Algo Trading. Keep this demo-only until fills, spread, commission, and slippage reporting are confirmed.
