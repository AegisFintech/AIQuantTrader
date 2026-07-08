# FinRobot

FinRobot is an MT5-first autonomous demo-trading repo for exactly one symbol:

- `XAUUSD`

The active runtime is simple: MetaTrader 5 runs under Wine/Xvfb, the FinRobot EA trades inside MT5, and PM2 keeps MT5 plus the watchdog, autonomous review loop, and read-only dashboard alive.

## Install

Use Debian or Ubuntu on **x86_64** for the standard MT5/Wine path.

```bash
cp .env.sample .env
./install.sh
```

`install.sh` installs required OS packages, creates `.venv`, installs Python dependencies, installs global PM2 if missing, downloads MT5, creates a repo-local Wine prefix, syncs the EA, configures MT5 to start the FinRobot EA, and starts PM2.

On **arm64** (e.g. Apple M-series, Raspberry Pi, AWS Graviton), MT5 requires x86_64 emulation. The preferred experimental local path is Hangover Wine for Debian/Ubuntu ARM64. With `FINROBOT_ALLOW_EMULATED_MT5=true`, the scripts auto-use Hangover's `wine` when installed. The fallback path is Box64 plus a new-WoW64 x86_64 Wine build extracted under `.runtime/wine-x86_64/wine-11.10-amd64-wow64`, then:

```env
FINROBOT_ALLOW_EMULATED_MT5=true
FINROBOT_WINE_CMD=/absolute/path/to/FinRobot/scripts/wine_box64.sh
```

When `FINROBOT_SKIP_MT5_INSTALL=true`, the installer skips MT5/Wine and installs only Python and PM2 tooling. The non-MT5 scripts work, but `mt5-terminal` will remain stopped. Use `FINROBOT_SKIP_MT5_INSTALL=true` on x86_64 to skip MT5 for CI or development-only setups.

The installer handles NodeSource-installed `nodejs` gracefully — if Debian's `nodejs`/`npm` conflict with an existing NodeSource node, it falls back to `sudo npm install -g npm`.

Runtime files are stored in this repo under `.runtime/` and are gitignored:

```text
.runtime/wineprefix/        Wine prefix
.runtime/wine-x86_64/       Optional new-WoW64 x86_64 Wine build for arm64 + Box64
.runtime/mt5/               MT5 terminal install/link
.runtime/downloads/         MT5 installer cache
```

Set demo account values in `.env` before starting live services:

```env
MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=ICMarketsSC-Demo
MT5_MODE=demo
MT5_AUTOTRADING_ENABLED=true
FINROBOT_ATTACH_SYMBOL=XAUUSD
FINROBOT_ATTACH_PERIOD=M1
```

If these MT5 account fields are empty, MT5 starts but opens the account setup wizard and the bridge EA will not emit `finrobot_status.json` until a trading account is configured.

For a fresh generic MT5 install, the IC Markets server list may need to be seeded once through MT5: `File` -> `Open an Account`, search for `Raw Trading Ltd`, select `ICMarketsSC-Demo`, and connect the existing account. The discovered broker server data is stored under `.runtime/` and survives normal PM2 restarts.

## Run

```bash
pm2 list
pm2 restart mt5-terminal mt5-watchdog autonomous-review finrobot-dashboard --update-env
python3 scripts/mt5_status.py
python3 scripts/mt5_trade_report.py
```

Active PM2 processes:

| Process | Purpose |
|---|---|
| `mt5-terminal` | Starts repo-local MT5 under Wine/Xvfb. |
| `mt5-watchdog` | Restarts only `mt5-terminal` when the bridge heartbeat is stale. |
| `autonomous-review` | Reviews MT5 trade performance every 6 hours and records analysis. |
| `finrobot-dashboard` | Serves the read-only Streamlit trade/status dashboard on `127.0.0.1:8501`. |

All PM2 output goes to `logs/combined.log`.

The dashboard reads MT5 Common Files and PM2 logs only; it does not expose a command form or trading controls. When nginx is configured for the host, proxy `trading.aims-sg.com` to `127.0.0.1:8501`.

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
- `finrobot_strategy_profile.csv` (optional generated runtime profile)

Current auto-trading posture:

- XAUUSD lot sizing is proportional to broker-day equity, configured risk fraction, and SL distance, with a high demo compounding ceiling.
- XAUUSD scans Monday-Friday whenever the broker symbol is inside its configured trade session, while requiring premium/discount smart-money score 4+ entries.
- Entries require spread, smart-money, position-count, and daily-risk checks before any order is sent.
- Auto trades and command-file market trades require broker-side SL and TP values before the EA sends the order.
- When present, `finrobot_strategy_profile.csv` may override bounded XAU-only strategy/risk settings such as ATR impulse threshold, PDA/SMC gates, cooldown, risk tier, XAU lot cap, and recovery controls. Missing or invalid profile data falls back to compiled defaults.
- Recovery controls can downshift bad-day risk, pause after a loss streak or recent drawdown threshold, reject abnormal ATR regimes, and honor scheduled blackout windows from `finrobot_blackout.csv` when enabled.
- `finrobot_status.json` exposes per-symbol `session_gated`, `weekday_market_hours`, `session_open`, and daily `signal_telemetry` counters for filled trades and major rejection reasons.

`scripts/start_mt5.sh` rewrites `Config\finrobot-login.ini` from `.env` before each PM2-managed terminal start. `scripts/mt5_configure_profile.py` then updates the Default chart profile and startup config file so MT5 runs `MQL5\Experts\FinRobot\FinRobotBridgeEA.ex5` on the `FINROBOT_ATTACH_SYMBOL` chart at launch. By default it keeps one chart in the profile and does not ask MT5 to open an extra startup chart; set `FINROBOT_SINGLE_CHART_PROFILE=false` or `FINROBOT_STARTUP_OPEN_CHART=true` only when you intentionally want that behavior.

After EA edits, sync and compile when MetaEditor is available:

```bash
scripts/sync_mt5_ea.sh
python3 scripts/mt5_configure_profile.py
pm2 restart mt5-terminal --update-env
```

## Strategy Lab

`scripts/xau_strategy_lab.py` evaluates bounded aggressive XAUUSD profiles with the deterministic walk-forward backtester and writes reports under `state/research/profile_lab/`.

```bash
python3 scripts/xau_strategy_lab.py
python3 scripts/xau_strategy_lab.py --harvest-first
python3 scripts/xau_strategy_lab.py --write-profile
```

The lab writes a live profile only when `--write-profile` is passed and the winning candidate clears the promotion gates, unless `--force-profile` is also passed. The 6-hour `autonomous-review` loop runs the lab by default for analysis; live profile deployment remains gated by `AUTOREVIEW_ENABLE_PROMOTION_DEPLOY=true`. LLM code edits remain separately gated by `AUTOREVIEW_ENABLE_LLM=true`.

## Clean Reset

To rebuild MT5/Wine from scratch:

```bash
pm2 delete mt5-terminal mt5-watchdog autonomous-review finrobot-dashboard
rm -rf .runtime
./install.sh
```

## Guardrails

- Demo-only unless the owner explicitly says otherwise.
- Trade only `XAUUSD`.
- Keep PM2 as the service manager.
- Do not commit `.env`, `.runtime/`, `logs/`, or `state/`.
