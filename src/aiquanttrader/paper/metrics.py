"""Bounded-cardinality Prometheus metrics for paper trading."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

from aiquanttrader.paper.engine import PaperEngineCycle, PaperTradingEngine


class PaperMetrics:
    def __init__(self, registry: CollectorRegistry) -> None:
        self.registry = registry
        self.market_states = Counter(
            "aqt_paper_market_states_total",
            "Causal L2 market states processed by the paper engine",
            registry=registry,
        )
        self.decisions = Counter(
            "aqt_paper_risk_decisions_total",
            "Paper intents evaluated by the production risk authority",
            ("result", "reason"),
            registry=registry,
        )
        self.order_updates = Counter(
            "aqt_paper_order_updates_total",
            "Paper order lifecycle updates",
            ("state",),
            registry=registry,
        )
        self.fills = Counter(
            "aqt_paper_fills_total",
            "Paper fills by liquidity role",
            ("liquidity",),
            registry=registry,
        )
        self.stale_trades_excluded = Counter(
            "aqt_paper_stale_trades_excluded_total",
            "Trades excluded before feature generation because their exchange time is stale",
            registry=registry,
        )
        self.markouts = Histogram(
            "aqt_paper_fill_markout_bps",
            "Signed post-fill markout in basis points",
            buckets=(-20, -10, -5, -2, -1, 0, 1, 2, 5, 10, 20),
            registry=registry,
        )
        self.cycle_latency = Histogram(
            "aqt_paper_cycle_latency_seconds",
            "Wall time for feature, strategy, risk, simulation, and durable journal",
            buckets=(0.0001, 0.00025, 0.0005, 0.001, 0.0025, 0.005, 0.01, 0.05, 0.1),
            registry=registry,
        )
        self.feed_connected = Gauge(
            "aqt_paper_feed_connected",
            "Whether the raw-first public feed is current",
            registry=registry,
        )
        self.feature_ready = Gauge(
            "aqt_paper_feature_ready",
            "Whether the causal feature warmup is complete",
            registry=registry,
        )
        self.equity = Gauge(
            "aqt_paper_equity_usd",
            "Marked paper account equity",
            registry=registry,
        )
        self.pnl = Gauge(
            "aqt_paper_pnl_usd",
            "Marked paper PnL from initial run equity",
            registry=registry,
        )
        self.daily_pnl = Gauge(
            "aqt_paper_daily_pnl_usd",
            "Marked paper PnL from the current UTC day-start equity",
            registry=registry,
        )
        self.realized_trading_pnl = Gauge(
            "aqt_paper_realized_trading_pnl_usd",
            "Gross realized paper trading PnL before fees and funding",
            registry=registry,
        )
        self.fees = Gauge(
            "aqt_paper_fees_usd",
            "Cumulative paper execution fees",
            registry=registry,
        )
        self.funding_pnl = Gauge(
            "aqt_paper_funding_pnl_usd",
            "Cumulative paper funding PnL",
            registry=registry,
        )
        self.drawdown_fraction = Gauge(
            "aqt_paper_drawdown_fraction",
            "Current marked drawdown from the paper account high-water equity",
            registry=registry,
        )
        self.daily_loss_fraction = Gauge(
            "aqt_paper_daily_loss_fraction",
            "Current non-negative marked loss from UTC day-start equity",
            registry=registry,
        )
        self.position = Gauge(
            "aqt_paper_position_base",
            "Paper BTC base position",
            registry=registry,
        )
        self.open_orders = Gauge(
            "aqt_paper_open_orders",
            "Current non-terminal paper orders",
            registry=registry,
        )
        self.operator_kill = Gauge(
            "aqt_paper_operator_kill",
            "Whether the shared operator kill is active",
            registry=registry,
        )
        self.drift_ready = Gauge(
            "aqt_paper_drift_ready",
            "Whether baseline and current feature drift windows are populated",
            registry=registry,
        )
        self.drift_maximum_psi = Gauge(
            "aqt_paper_drift_maximum_psi",
            "Maximum population stability index in the latest drift report",
            registry=registry,
        )
        self.drift_maximum_mean_shift = Gauge(
            "aqt_paper_drift_maximum_standardized_mean_shift",
            "Maximum standardized feature mean shift in the latest drift report",
            registry=registry,
        )

    def observe_stale_trade_exclusions(self, count: int) -> None:
        if count < 0:
            raise ValueError("paper stale-trade exclusion count cannot be negative")
        if count:
            self.stale_trades_excluded.inc(count)

    def observe_cycle(
        self,
        engine: PaperTradingEngine,
        cycle: PaperEngineCycle,
        *,
        latency_seconds: float,
        initial_equity_usd: float,
    ) -> None:
        self.market_states.inc()
        self.cycle_latency.observe(latency_seconds)
        for record in cycle.decisions:
            result = "approved" if record.risk_decision.allowed else "denied"
            for reason in record.risk_decision.reasons:
                self.decisions.labels(result=result, reason=reason.value).inc()
        for order in cycle.orders:
            self.order_updates.labels(state=order.state.value).inc()
        for fill in cycle.fills:
            self.fills.labels(liquidity="maker" if fill.maker else "taker").inc()
        for markout in cycle.markouts:
            self.markouts.observe(float(markout.signed_markout_bps))
        if cycle.drift_report is not None:
            self.drift_ready.set(1)
            self.drift_maximum_psi.set(cycle.drift_report.maximum_psi)
            self.drift_maximum_mean_shift.set(cycle.drift_report.maximum_standardized_mean_shift)
        self.update_state(engine, initial_equity_usd=initial_equity_usd)

    def update_state(self, engine: PaperTradingEngine, *, initial_equity_usd: float) -> None:
        account = engine.simulator.account
        equity = float(account.equity_usd)
        day_start_equity = float(account.day_start_equity_usd)
        high_water_equity = float(account.high_water_equity_usd)
        self.feed_connected.set(1 if engine.feed_connected else 0)
        self.feature_ready.set(1 if engine.feature_ready else 0)
        self.equity.set(equity)
        self.pnl.set(equity - initial_equity_usd)
        self.daily_pnl.set(equity - day_start_equity)
        self.realized_trading_pnl.set(float(account.realized_trading_pnl_usd))
        self.fees.set(float(account.fees_usd))
        self.funding_pnl.set(float(account.funding_pnl_usd))
        self.drawdown_fraction.set(max(0.0, (high_water_equity - equity) / high_water_equity))
        self.daily_loss_fraction.set(max(0.0, (day_start_equity - equity) / day_start_equity))
        self.position.set(float(account.position_base))
        self.open_orders.set(len(engine.simulator.open_orders))
        self.operator_kill.set(1 if engine.kill_switch.read().active else 0)
