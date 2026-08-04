# AIQuantTrader

## Migration status

AIQuantTrader is migrating to a Linux-native BTC perpetual platform using
Hyperliquid, NautilusTrader, HftBacktest, Tardis data, Parquet/DuckDB, and
Prometheus/Grafana. The approved target and safety boundaries are documented in
[`docs/architecture/TARGET_ARCHITECTURE.md`](docs/architecture/TARGET_ARCHITECTURE.md)
and [`docs/migration/MT5_TO_HYPERLIQUID.md`](docs/migration/MT5_TO_HYPERLIQUID.md).

The migration is a parallel replacement. No native production trading is
authorized by the architecture approval alone, and automated research may not
promote a model into production. Until the Phase 9 cutover is separately
approved, the deployed runtime remains the MT5 demo system described below.

Phases 2-9 now provide the isolated native foundation, raw-first Hyperliquid
market-data path, fail-closed execution/risk path, causal BTC replay and
validation, BTC feature/strategy/research framework, and credential-free live
paper trading, and network-isolated shadow deployment. Phase 4 uses
NautilusTrader as the sole ordinary exchange-order
owner and a separately credentialed Hyperliquid SDK sentinel for exchange
dead-man and emergency cancellation. See
[`docs/migration/PHASE_4_EXECUTION_RISK.md`](docs/migration/PHASE_4_EXECUTION_RISK.md)
and [`docs/operations/EXECUTION_RISK_RUNBOOK.md`](docs/operations/EXECUTION_RISK_RUNBOOK.md).
All checked-in environments remain execution-disabled; credentialed testnet
acceptance evidence is still required. Phase 9 adds cryptographically signed,
artifact-bound, explicit canary/production admission, durable anti-replay
state, independent wallet-role verification, capital clamps, and an exact-image
mainnet topology. It does not activate mainnet or retire MT5. See
[`docs/migration/PHASE_9_PRODUCTION_ADMISSION.md`](docs/migration/PHASE_9_PRODUCTION_ADMISSION.md)
and
[`docs/operations/MAINNET_CANARY_RUNBOOK.md`](docs/operations/MAINNET_CANARY_RUNBOOK.md).

Phase 5 converts manifest-admitted Tardis and local captures into deterministic
HftBacktest events, runs versioned baseline/pessimistic execution assumptions,
normalizes both Hft and real Nautilus objects through a shared pure-kernel
contract, and guards the final holdout behind a frozen validation-only selection
receipt. The seed scenarios are explicitly uncalibrated and cannot support
promotion. See
[`docs/migration/PHASE_5_BACKTESTING.md`](docs/migration/PHASE_5_BACKTESTING.md)
and [`docs/operations/BACKTESTING_RUNBOOK.md`](docs/operations/BACKTESTING_RUNBOOK.md).

Phase 6 adds causal bounded microstructure features, pure Avellaneda-Stoikov
and order-flow-scalping kernels, native-format LightGBM/XGBoost/CatBoost
research adapters, bounded validation-only search, drift and negative controls,
and an immutable champion-challenger registry. These strategies are research
candidates and are not connected to the native execution node. Automation has
no human approval capability and stops at `AWAITING_APPROVAL`. See
[`docs/migration/PHASE_6_RESEARCH.md`](docs/migration/PHASE_6_RESEARCH.md) and
[`docs/operations/RESEARCH_RUNBOOK.md`](docs/operations/RESEARCH_RUNBOOK.md).

Phase 7 runs those exact feature/strategy kernels and the hard risk authority
on the live public feed, terminating approved intents in a deterministic
market-by-price simulator. Raw data, decisions, fills, PnL/inventory, restart
state, markouts, drift, and drills are retained. Paper configuration and its
container reject exchange accounts and wallet references. The checked-in fill
scenarios remain uncalibrated, so paper promotion must fail until retained
calibration, sensitivity, sample/regime, drill, and observation gates pass. See
[`docs/migration/PHASE_7_PAPER.md`](docs/migration/PHASE_7_PAPER.md) and
[`docs/operations/PAPER_TRADING_RUNBOOK.md`](docs/operations/PAPER_TRADING_RUNBOOK.md).

Phase 8 splits the live public gateway from a `network_mode: none` decision
engine. The engine verifies checksummed read-only ingress, runs the exact
production feature/strategy/risk path, records every counterfactual command,
and exports through a read-only observer. It has no wallet, signer, account
identity, execution client, or IP default route. See
[`docs/migration/PHASE_8_SHADOW.md`](docs/migration/PHASE_8_SHADOW.md) and the
[`shadow runbook`](docs/operations/SHADOW_DEPLOYMENT_RUNBOOK.md). Empirical
acceptance remains pending calibrated scenarios, production-host fault drills,
required samples/regimes, and the minimum seven-day observation.

## Current deployed runtime

The current runtime is MT5-first autonomous demo trading for exactly one symbol:

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
- `aiquanttrader_commands.csv`
- `aiquanttrader_strategy_profile.csv` (optional generated runtime profile)
- `aiquanttrader_entry_pause.flag` (operator-controlled new-entry kill switch)
- `aiquanttrader_export_XAUUSD_M1.tsv` (periodic bounded M1 research export)

Current auto-trading posture:

- XAUUSD lot sizing targets 1.00% planned stop risk per position from broker-day equity and SL distance. Score multipliers cannot exceed that hard effective-risk cap; the 50-lot demo ceiling and broker volume limits still apply.
- XAUUSD signals and indicators run on M1 for the active demo strategy; the strategy lab uses the profile timeframe when replaying M1 warehouse bars.
- XAUUSD scans Monday-Friday whenever the broker symbol is inside its configured trade session, while requiring premium/discount smart-money score 4+ entries.
- Entries require spread, smart-money, position-count, and daily-risk checks before any order is sent.
- Auto trades and command-file market trades require broker-side SL and TP values before the EA sends the order.
- When present, `aiquanttrader_strategy_profile.csv` may override bounded XAU-only strategy/risk settings such as ATR impulse threshold, PDA/SMC gates, cooldown, risk tier, XAU lot cap, and recovery controls. Missing or invalid profile data falls back to compiled defaults.
- Recovery controls can downshift bad-day risk, pause after a loss streak or recent drawdown threshold, reject abnormal ATR regimes, and honor scheduled blackout windows from `aiquanttrader_blackout.csv` when enabled.
- While `aiquanttrader_entry_pause.flag` exists, the EA keeps heartbeat, reporting, position risk management, and close commands active but rejects automatic entries and command-file `MARKET` actions. Use `python3 scripts/mt5_entry_pause.py pause|resume|status`.
- `aiquanttrader_status.json` exposes per-symbol `session_gated`, `weekday_market_hours`, `session_open`, and daily `signal_telemetry` counters for filled trades and major rejection reasons.

`scripts/start_mt5.sh` rewrites `Config\aiquanttrader-login.ini` from `.env` before each PM2-managed terminal start. `scripts/mt5_configure_profile.py` then updates the Default chart profile and startup config file so MT5 runs `MQL5\Experts\AIQuantTrader\AIQuantTraderBridgeEA.ex5` on the `AIQUANTTRADER_ATTACH_SYMBOL` chart at launch. By default it keeps one chart in the profile and does not ask MT5 to open an extra startup chart; set `AIQUANTTRADER_SINGLE_CHART_PROFILE=false` or `AIQUANTTRADER_STARTUP_OPEN_CHART=true` only when you intentionally want that behavior.

After EA edits, sync and compile when MetaEditor is available:

```bash
scripts/sync_mt5_ea.sh
python3 scripts/mt5_configure_profile.py
pm2 restart aiquanttrader-mt5 --update-env
```

## Strategy Lab

`scripts/xau_strategy_lab.py` evaluates bounded aggressive XAUUSD profiles with the deterministic walk-forward backtester and writes reports under `state/research/profile_lab/`.

The EA refreshes a bounded, epoch-timestamped XAUUSD M1 export every six hours. The autonomous
review harvests it by default, and the lab rejects data older than 72 hours.
Profile deployment remains separately disabled by default.

```bash
python3 scripts/xau_strategy_lab.py
python3 scripts/xau_strategy_lab.py --harvest-first
python3 scripts/xau_strategy_lab.py --write-profile
```

The lab writes a live profile only when `--write-profile` is passed and the winning candidate clears the promotion gates, unless `--force-profile` is also passed. The 6-hour `aiquanttrader-review` loop runs the lab over the latest 50,000 bars at low CPU priority by default and journals timeouts without aborting the cycle. Live profile deployment remains gated by `AUTOREVIEW_ENABLE_PROMOTION_DEPLOY=true`; LLM code edits remain separately gated by `AUTOREVIEW_ENABLE_LLM=true`.

The `macd_continuation_m1` challenger adds a strengthening MACD-histogram gate to the M1 ATR impulse entry. The EA supports the gate through runtime profiles, but the compiled default remains off and the lab will not deploy the challenger unless it clears every promotion gate.

`config/aiquanttrader.cron` serializes DuckDB ingestion and price snapshots through `scripts/mt5_minute_cycle.py`, then sequences metrics export, alert delivery, and the broader healthcheck every five minutes. The healthcheck covers heartbeat, risk, unprotected positions, disk usage, research freshness, and all four PM2 services.

Telegram warning and critical transitions use `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALERT_CHAT_ID` from the repo-local `.env`. `scripts/alert_delivery.py` loads that file directly so cron delivery does not depend on an interactive shell environment.

## Clean Reset

To rebuild MT5/Wine from scratch:

```bash
pm2 delete aiquanttrader-mt5 aiquanttrader-watchdog aiquanttrader-review aiquanttrader-dashboard
rm -rf .runtime
./install.sh
```

## Legacy runtime guardrails

- These guardrails remain authoritative for the deployed MT5 runtime until its
  Phase 9 retirement.
- Demo-only unless the owner explicitly says otherwise.
- Trade only `XAUUSD`.
- Keep PM2 as the service manager.
- Do not commit `.env`, `.runtime/`, `logs/`, or `state/`.

The native platform has separate scope, phase gates, and release controls in
[`docs/migration/PHASE_ACCEPTANCE_GATES.md`](docs/migration/PHASE_ACCEPTANCE_GATES.md)
and
[`docs/operations/NATIVE_RELEASE_CHECKLIST.md`](docs/operations/NATIVE_RELEASE_CHECKLIST.md).
