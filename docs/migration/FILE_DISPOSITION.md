# Repository File Disposition

This document classifies the repository as it exists before the native
migration. `Delete` means removal in the Phase 9 legacy-retirement PR after the
MT5 system is tagged and archived, not deletion during architecture work.

## Keep or carry forward

| Existing path | Disposition | Native destination or rationale |
|---|---|---|
| `.gitignore`, `.gitattributes` | Refactor | Add native artifacts, data, models, Rust, and tooling rules while preserving secret/runtime exclusions. |
| `.github/CONTRIBUTING.md` | Refactor | Add native testing, security, data, and release workflow. |
| `LICENSE` | Keep | Project license remains authoritative. |
| `aiquanttrader/research/experiments.py` | Refactor | Immutable experiment records under `src/aiquanttrader/research/`. |
| `aiquanttrader/research/registry.py` | Refactor | Dataset/model/deployment-aware governance registry. |
| `aiquanttrader/research/comparison.py` | Refactor | Champion-challenger reports with stage and approval policy. |
| `aiquanttrader/risk/limits.py` | Concepts only | Reimplemented synchronous risk state and hard bounds. |
| `aiquanttrader/monitoring/alpha_decay.py` | Concepts only | Reimplemented with post-cost, execution-quality, and drift inputs. |
| `aiquanttrader/alerts.py`, `alert_delivery.py` | Concepts only | Alert state transitions move to metrics/Alertmanager integration. |
| `scripts/check_secrets.sh` | Keep/refactor | Run in native CI and extend for native secret names/artifacts. |
| `tests/` | Selective history | Existing tests remain until their owned legacy component retires. |

Keeping a concept does not imply carrying its implementation unchanged. New
code must satisfy the native schemas, typing, causality, and tests.

## Replace during Phases 2-8

| Existing group | Replacement |
|---|---|
| `aiquanttrader/data_store.py`, `prices.py`, `validators.py` | Raw recorder, Parquet catalog, manifests, integrity service, and isolated DuckDB analytical stores. |
| `aiquanttrader/metrics.py`, `logging_config.py`, monitoring/report scripts | Prometheus metrics, structured stdout, Grafana dashboards, Alertmanager, and audited event journals. |
| `aiquanttrader/execution/` | Nautilus Hyperliquid execution, reconciliation, and SDK sentinel boundaries. |
| `aiquanttrader/backtest/` | HftBacktest event simulation plus Nautilus release-parity harness. |
| `aiquanttrader/research/features.py`, `models.py`, `regime.py`, `optimizer.py`, `significance.py` | Causal order-book research pipeline with corrected validation and immutable lineage. |
| `aiquanttrader/hft.py`, `indicators.py`, `backtesting.py` | Incremental microstructure feature engine and event-driven backtesting. |
| `aiquanttrader/strategies/` | BTC market maker, order-flow scalper, and tabular ML strategies. |
| `aiquanttrader/xau_profiles.py` | Signed BTC deployment and promotion policies. |
| `aiquanttrader/release_manifest.py` | Native artifact manifest binding commit, image, model, schema, configuration, and approval. |
| `scripts/release_manifest.py`, reporting/promotion CLIs | Native governance and release CLIs. |
| `dashboard/app.py` and `dashboard/` | Prometheus, Grafana, and Alertmanager. |
| `.env.sample` | Non-secret typed configuration examples plus runtime secret mounts. |
| `pyproject.toml`, `requirements.txt` | Python 3.12 metadata and an exact lockfile with separated runtime/research groups. |
| `install.sh` | Reproducible Docker build/deploy/bootstrap procedure. |
| `ecosystem.config.js` | Docker Compose service definitions and health policies. |
| `config/` operational files | Native Prometheus/Grafana/Alertmanager/Docker policies. |
| `.github/workflows/jekyll-gh-pages.yml` | Native CI, image build, security, and documentation checks. |

## Retire after Phase 9 cutover

| Existing group | Reason |
|---|---|
| `broker/mt5/` | MQL5 execution, bridge, and export tooling are not used by the native venue. |
| `scripts/start_mt5.sh`, `sync_mt5_ea.sh`, `mt5_configure_profile.py`, `wine_box64.sh` | MT5/Wine lifecycle is eliminated. |
| `scripts/mt5_*.py`, `xau_*.py`, `harvest_mt5_export.py`, `archive_common_files.py` | Broker Common Files and XAU research paths are retired. |
| `scripts/autonomous_review_loop.py`, `run_backtest.py`, `run_walkforward.py`, `run_quant_pipeline.py`, `run_parity.py`, `promote_compare.py` | Replaced by governed native research and validation workers. |
| `scripts/install_cron.sh`, `install_logrotate.sh`, `xau_parity_watch.sh` | Native scheduling, container logging, and replay checks replace them. |
| XAU/MT5-specific files under `tests/` | Removed with their implementation after archival test evidence is retained in Git. |
| `data/XAUUSD1.csv` | Historical XAU input is outside native BTC scope; retained in the final MT5 tag. |
| `config/aiquanttrader.cron`, `logrotate-aiquanttrader`, `nginx-trading.aims-sg.com.conf` | PM2/cron/Streamlit host integration is retired. |
| `CLAUDE.md`, `QUANT_ROADMAP.md`, `Autonomous-plan.pdf` | Point-in-time planning is superseded; history remains in Git. |
| MT5-focused sections of `README.md`, `AGENTS.md`, `docs/REPOSITORY_MAP.md`, and `docs/RELEASE_CHECKLIST.md` | Removed only when the active runtime is native. |

## Directory-level coverage

The following table ensures every current tracked top-level group has an
explicit migration owner.

| Top-level group | Disposition owner |
|---|---|
| `.github/` | Phase 2 CI replacement; contributor guidance refactor. |
| `aiquanttrader/` | Phases 2-6 native `src/` replacement, with selected research concepts retained. |
| `broker/` | Phase 9 MT5 retirement. |
| `config/` | Phases 2 and 8 native infrastructure replacement; Phase 9 legacy cleanup. |
| `dashboard/` | Phase 3-4 observability replacement; Phase 9 removal. |
| `data/` | Phase 3 BTC lake; XAU file retired in Phase 9. |
| `docs/` | Phase 1 ratification, then phase-specific native docs; legacy procedures retained until cutover. |
| `scripts/` | Replaced incrementally by native operational and governance commands; legacy scripts retained until owners retire. |
| `tests/` | Native test taxonomy added alongside legacy tests, then legacy tests removed with owned code. |
| root build/runtime files | Phase 2 Python/Rust/Docker replacement; PM2/MT5 files removed in Phase 9. |

## Legacy archival procedure

Before deletion:

1. capture final status, trade report, deployed EA manifest, configuration
   fingerprints without secrets, and process state;
2. archive required broker/Common Files evidence under the retention policy;
3. create and push an annotated `mt5-final` tag;
4. verify native rollback and incident procedures;
5. disable legacy services and confirm they cannot restart;
6. remove legacy code in a dedicated, reviewable PR.
