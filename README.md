# FinRobot

FinRobot is an MT5-first autonomous demo-trading repo for exactly two symbols:

- `XAUUSD`
- `BTCUSD`

The active runtime is simple: MetaTrader 5 runs under Wine/Xvfb, the FinRobot EA trades inside MT5, and PM2 keeps MT5 plus the autonomous review loop alive.

## Install

Use Debian or Ubuntu.

```bash
cp .env.sample .env
./install.sh
```

`install.sh` installs required OS packages, creates `.venv`, installs Python dependencies, installs global PM2 if missing, downloads MT5, creates a repo-local Wine prefix, syncs the EA, and starts PM2.

Runtime files are stored in this repo under `.runtime/` and are gitignored:

```text
.runtime/wineprefix/        Wine prefix
.runtime/mt5/               MT5 terminal install/link
.runtime/downloads/         MT5 installer cache
```

Set demo account values in `.env` before starting live services:

```env
MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=ICMarketsSC-Demo
MT5_MODE=demo
```

## Run

```bash
pm2 list
pm2 restart mt5-terminal autonomous-review --update-env
python3 scripts/mt5_status.py
python3 scripts/mt5_trade_report.py
```

Active PM2 processes:

| Process | Purpose |
|---|---|
| `mt5-terminal` | Starts repo-local MT5 under Wine/Xvfb. |
| `autonomous-review` | Reviews MT5 trade performance every 6 hours and records analysis. |

All PM2 output goes to `logs/combined.log`.

## MT5 Bridge

Active EA source:

- `broker/mt5/FinRobotBridgeEA.mq5`
- `broker/mt5/BridgeIO.mqh`
- `broker/mt5/RiskManagement.mqh`
- `broker/mt5/SmartMoney.mqh`

The EA writes MT5 Common Files:

- `finrobot_status.json`
- `finrobot_positions.csv`
- `finrobot_deals.csv`
- `finrobot_acks.csv`
- `finrobot_commands.csv`

After EA edits, sync and compile when MetaEditor is available:

```bash
scripts/sync_mt5_ea.sh
pm2 restart mt5-terminal --update-env
```

## Clean Reset

To rebuild MT5/Wine from scratch:

```bash
pm2 delete mt5-terminal autonomous-review
rm -rf .runtime
./install.sh
```

## Guardrails

- Demo-only unless the owner explicitly says otherwise.
- Trade only `XAUUSD` and `BTCUSD`.
- Keep PM2 as the service manager.
- Do not commit `.env`, `.runtime/`, `logs/`, or `state/`.
