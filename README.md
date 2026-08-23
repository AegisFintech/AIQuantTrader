# AIQuantTrader

AIQuantTrader is an MT5-first autonomous demo-trading repo for exactly one symbol:

- `XAUUSD`

The active runtime is simple: MetaTrader 5 runs under Wine/Xvfb, the AIQuantTrader EA trades inside MT5, and PM2 keeps MT5 plus the watchdog, autonomous review loop, and read-only dashboard alive.

The AIQuantTrader rename preserves MT5 `MagicNumber=20260522`, broker deal history, the existing DuckDB warehouse, and demo-account credentials. New runtime files and trade comments use the AIQuantTrader name.

For code ownership, runtime data flow, and targeted change/test paths, start with
[`docs/REPOSITORY_MAP.md`](docs/REPOSITORY_MAP.md).

## Install

Use Debian or Ubuntu on **x86_64** for the standard MT5/Wine path.

```bash
cp .env.sample .env
./install.sh
```

`install.sh` installs required OS packages, creates `.venv`, installs Python dependencies, installs global PM2 if missing, downloads MT5, creates a repo-local Wine prefix, syncs the EA, configures MT5 to start the AIQuantTrader EA, and starts PM2.

On **arm64** (e.g. Apple M-series, Raspberry Pi, AWS Graviton), MT5 requires x86_64 emulation. The preferred experimental local path is Hangover Wine for Debian/Ubuntu ARM64. With `AIQUANTTRADER_ALLOW_EMULATED_MT5=true`, the scripts auto-use Hangover's `wine` when installed. The fallback path is Box64 plus a new-WoW64 x86_64 Wine build extracted under `.runtime/wine-x86_64/wine-11.10-amd64-wow64`, then:

```env
AIQUANTTRADER_ALLOW_EMULATED_MT5=true
AIQUANTTRADER_WINE_CMD=/absolute/path/to/AIQuantTrader/scripts/wine_box64.sh
```

When `AIQUANTTRADER_SKIP_MT5_INSTALL=true`, the installer skips MT5/Wine and installs only Python and PM2 tooling. The non-MT5 scripts work, but `aiquanttrader-mt5` will remain stopped. Use `AIQUANTTRADER_SKIP_MT5_INSTALL=true` on x86_64 to skip MT5 for CI or development-only setups.

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
AIQUANTTRADER_ATTACH_SYMBOL=XAUUSD
AIQUANTTRADER_ATTACH_PERIOD=M1
```

If these MT5 account fields are empty, MT5 starts but opens the account setup wizard and the bridge EA will not emit `aiquanttrader_status.json` until a trading account is configured.

For a fresh generic MT5 install, the IC Markets server list may need to be seeded once through MT5: `File` -> `Open an Account`, search for `Raw Trading Ltd`, select `ICMarketsSC-Demo`, and connect the existing account. The discovered broker server data is stored under `.runtime/` and survives normal PM2 restarts.

## Run

```bash
pm2 list
pm2 restart aiquanttrader-mt5 aiquanttrader-watchdog aiquanttrader-review aiquanttrader-dashboard --update-env
python3 scripts/mt5_status.py
python3 scripts/mt5_trade_report.py
```

Active PM2 processes:

| Process | Purpose |
|---|---|
| `aiquanttrader-mt5` | Starts repo-local MT5 under Wine/Xvfb. |
| `aiquanttrader-watchdog` | Restarts only `aiquanttrader-mt5` when the bridge heartbeat is stale. |
| `aiquanttrader-review` | Reviews MT5 trade performance every 6 hours and records analysis. |
| `aiquanttrader-dashboard` | Serves the read-only Streamlit trade/status dashboard on `127.0.0.1:8501`. |

All PM2 output goes to `logs/combined.log`.

The dashboard reads MT5 Common Files and PM2 logs only; it does not expose a command form or trading controls. When nginx is configured for the host, proxy `trading.aims-sg.com` to `127.0.0.1:8501`.

## MT5 Bridge

Active EA source:

- `broker/mt5/AIQuantTraderBridgeEA.mq5`
- `broker/mt5/BridgeIO.mqh`
- `broker/mt5/RiskManagement.mqh`
- `broker/mt5/SmartMoney.mqh`

The EA writes MT5 Common Files:

- `aiquanttrader_status.json`
- `aiquanttrader_positions.csv`
- `aiquanttrader_deals.csv`
- `aiquanttrader_acks.csv`
- `aiquanttrader_shadow_signals.csv` (qualified paused-entry setups; never broker orders)
- `aiquanttrader_commands.csv`
- `aiquanttrader_strategy_profile.csv` (optional generated runtime profile)
- `aiquanttrader_entry_pause.flag` (operator-controlled new-entry kill switch)
- `aiquanttrader_export_XAUUSD_M1.tsv` (periodic bounded M1 research export)

Current auto-trading posture:

- Autonomous XAUUSD demo entries were explicitly re-enabled by the owner on 2026-07-29. The compiled defaults remain active because no challenger cleared promotion; normal SMC/PDA gates and all hard risk controls still apply.
- XAUUSD lot sizing targets at most 1.00% planned stop risk per position from broker-day equity and SL distance. New volume is also clipped to the remaining 1.00% broker-day loss budget after closed PnL and the adverse stop risk reserved by open managed positions; the 50-lot demo ceiling and broker volume limits still apply.
- XAUUSD signals and indicators run on M1 for the active demo strategy. The EA evaluates only completed M1 bars and latches each setup once per bar; the strategy lab uses the profile timeframe when replaying M1 warehouse bars.
- XAUUSD scans Monday-Friday whenever the broker symbol is inside its configured trade session, while requiring premium/discount smart-money score 4+ entries.
- Entries require spread, smart-money, position-count, and daily-risk checks before any order is sent.
- After an EA/terminal restart, new entries remain fail-closed for 90 seconds while MT5 account and deal history repopulate; monitoring and position management continue.
- Auto trades and command-file market trades require broker-side SL and TP values before the EA sends the order.
- When present, `aiquanttrader_strategy_profile.csv` may override bounded XAU-only strategy/risk settings such as ATR impulse threshold, PDA/SMC gates, cooldown, trend alignment, dynamic break-even, risk tier, XAU lot cap, and recovery controls. Missing or invalid profile data falls back to compiled defaults.
- Recovery controls can downshift bad-day risk, pause after a loss streak or recent drawdown threshold, reject abnormal ATR regimes, and honor scheduled blackout windows from `aiquanttrader_blackout.csv` when enabled.
- While `aiquanttrader_entry_pause.flag` exists, the EA keeps heartbeat, reporting, position risk management, and close commands active and rejects all broker entries and command-file `MARKET` actions. With `EnableEntryPauseShadowSignals=true`, the normal closed-bar entry gates continue in shadow mode and fully-qualified setups are appended to `aiquanttrader_shadow_signals.csv` before the order boundary. No `CTrade` entry method is called. Use `python3 scripts/mt5_entry_pause.py pause|resume|status`.
- `python3 scripts/mt5_trade_report.py` resolves shadow setups against later exported M1 bars. It uses conservative stop-first ordering when one bar touches both exits, activates the logged dynamic break-even only after a bar survives its existing exits, and includes the calibrated IC Markets demo commission; signals are evaluated independently and are not broker fills.
- `aiquanttrader_status.json` exposes global `shadow_mode` plus per-symbol `session_gated`, `weekday_market_hours`, `session_open`, and daily `signal_telemetry` counters for real fills, qualified shadow signals, and major rejection reasons.

`scripts/start_mt5.sh` rewrites `Config\aiquanttrader-login.ini` from `.env` before each PM2-managed terminal start. `scripts/mt5_configure_profile.py` then updates the Default chart profile and startup config file so MT5 runs `MQL5\Experts\AIQuantTrader\AIQuantTraderBridgeEA.ex5` on the `AIQUANTTRADER_ATTACH_SYMBOL` chart at launch. By default it keeps one chart in the profile and does not ask MT5 to open an extra startup chart; set `AIQUANTTRADER_SINGLE_CHART_PROFILE=false` or `AIQUANTTRADER_STARTUP_OPEN_CHART=true` only when you intentionally want that behavior.

After EA edits, sync and compile when MetaEditor is available:

```bash
scripts/sync_mt5_ea.sh
python3 scripts/mt5_configure_profile.py
pm2 restart aiquanttrader-mt5 --update-env
```

## Strategy Lab

`scripts/xau_strategy_lab.py` evaluates bounded aggressive XAUUSD profiles with the deterministic walk-forward backtester and writes reports under `state/research/profile_lab/`. It mirrors all enabled live XAU entry paths (ATR impulse, quick momentum, and three-bar momentum) before applying the shared PDA/SMC and regime gates.

The EA refreshes a bounded, epoch-timestamped XAUUSD M1 export every six hours. The autonomous
review harvests it by default, and the lab rejects data older than 72 hours. When retired and
current exports overlap in DuckDB, the lab selects one canonical row per symbol/timestamp from
the source with the freshest coverage before applying its bar limit or indicators.
Profile deployment remains separately disabled by default.

```bash
python3 scripts/xau_strategy_lab.py
python3 scripts/xau_strategy_lab.py --harvest-first
python3 scripts/xau_strategy_lab.py --write-profile
```

The lab writes a live profile only when `--write-profile` is passed and the winning candidate clears the promotion gates, unless `--force-profile` is also passed. The 6-hour `aiquanttrader-review` loop runs the lab over the latest 100,000 bars at low CPU priority by default and journals timeouts without aborting the cycle. Autonomous runs index experiments in the dedicated ignored `state/research/profile_lab_registry.duckdb`, avoiding writer collisions with the live warehouse. Lab fills default to 8 spread points plus 2 adverse-slippage points. Live profile deployment remains gated by `AUTOREVIEW_ENABLE_PROMOTION_DEPLOY=true`; LLM code edits remain separately gated by `AUTOREVIEW_ENABLE_LLM=true`.

The `macd_continuation_m1` challenger adds a strengthening MACD-histogram gate to every enabled M1 entry path. The EA supports the gate through runtime profiles, but the compiled default remains off and the lab will not deploy the challenger unless it clears every promotion gate.
`macd_guarded_m1` evaluates MACD-confirmed entries at 0.15% planned risk with score multiplication disabled and a 0.15% bad-day entry pause, so one full stop ends entries for that broker day while the unchanged 1.00% hard daily-loss ceiling remains in force.
Its 2026-07-20 corrected-data run was positive but marginal (mean PnL `5,296.46`, PF `1.36`, consistency `0.80`, recent PnL `6,594.39`/PF `1.42`, worst fold `-5,051.63`), so it remains undeployed.
The more aggressive `m5_trend_attack_m1` challenger uses an M5 EMA regime filter, 0.50% planned risk, and score-5 scaling up to the unchanged 1.00% hard cap. Its 2026-07-22 cost-stressed full-history run remained non-promotable: mean PnL `5,040.50`, PF `1.02`, recent PnL `-10,740.32`, and worst fold `-63,015.01`. It is research-only and does not override the active score-4 compiled posture.
After the 2026-07-29 full-signal parity repair, a fresh 100,000-bar run rejected every tested profile. `m5_trend_attack_m1` averaged 46.0 trades per 16,626-bar fold (about four per active market day) with positive mean PnL `20,335.86`/PF `1.25`, but only `0.60` consistency, recent PnL `-37,860.73`/PF `0.533`, and worst-fold PnL `-53,427.50`. No challenger was deployed; the owner separately resumed compiled-default autonomous demo entries on 2026-07-29.

`config/aiquanttrader.cron` serializes DuckDB ingestion and price snapshots through `scripts/mt5_minute_cycle.py`, then sequences metrics export, alert delivery, and the broader healthcheck every five minutes. The healthcheck covers heartbeat, risk, unprotected positions, disk usage, research freshness, and all four PM2 services.

Telegram warning and critical transitions use `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALERT_CHAT_ID` from the repo-local `.env`. `scripts/alert_delivery.py` loads that file directly so cron delivery does not depend on an interactive shell environment.

## Clean Reset

To rebuild MT5/Wine from scratch:

```bash
pm2 delete aiquanttrader-mt5 aiquanttrader-watchdog aiquanttrader-review aiquanttrader-dashboard
rm -rf .runtime
./install.sh
```

## Guardrails

- Demo-only unless the owner explicitly says otherwise.
- Trade only `XAUUSD`.
- Keep PM2 as the service manager.
- Do not commit `.env`, `.runtime/`, `logs/`, or `state/`.
