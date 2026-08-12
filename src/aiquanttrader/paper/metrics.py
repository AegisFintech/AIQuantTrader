"""Bounded-cardinality Prometheus metrics for paper trading."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, Info

from aiquanttrader.features.market_structure import StructureDirection
from aiquanttrader.paper.engine import (
    PaperEngineCycle,
    PaperTradingEngine,
    PaperWatchdogUpdate,
)
from aiquanttrader.paper.llm_models import LlmConfirmation


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
        self.strategy_actions = Counter(
            "aqt_paper_strategy_actions_total",
            "Causal strategy evaluations by bounded action and reason",
            ("action", "reason"),
            registry=registry,
        )
        self.strategy_decision = Info(
            "aqt_paper_strategy_decision",
            "Latest human-readable strategy decision",
            registry=registry,
        )
        self.llm_confirmations = Counter(
            "aqt_paper_llm_confirmations_total",
            "Shadow-only LLM setup confirmations by verdict",
            ("verdict",),
            registry=registry,
        )
        self.llm_errors = Counter(
            "aqt_paper_llm_errors_total",
            "Bounded LLM observer errors by safe error code",
            ("code",),
            registry=registry,
        )
        self.llm_confidence = Gauge(
            "aqt_paper_llm_confidence",
            "Confidence of the latest non-authoritative LLM assessment",
            registry=registry,
        )
        self.llm_latency = Histogram(
            "aqt_paper_llm_latency_seconds",
            "Latency of shadow-only LLM confirmation requests",
            buckets=(0.25, 0.5, 1, 2, 5, 10, 20, 60),
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
        self.market_price = Gauge(
            "aqt_paper_market_price_usd",
            "Latest causal BTC midprice",
            registry=registry,
        )
        self.best_bid = Gauge(
            "aqt_paper_best_bid_usd",
            "Latest causal BTC best bid",
            registry=registry,
        )
        self.best_ask = Gauge(
            "aqt_paper_best_ask_usd",
            "Latest causal BTC best ask",
            registry=registry,
        )
        self.microprice = Gauge(
            "aqt_paper_microprice_usd",
            "Latest causal BTC microprice",
            registry=registry,
        )
        self.spread_bps = Gauge(
            "aqt_paper_spread_bps",
            "Latest BTC bid/ask spread in basis points",
            registry=registry,
        )
        self.book_imbalance = Gauge(
            "aqt_paper_book_imbalance",
            "Latest depth-weighted order-book imbalance",
            registry=registry,
        )
        self.trade_flow_imbalance = Gauge(
            "aqt_paper_trade_flow_imbalance",
            "Latest causal aggressive trade-flow imbalance",
            registry=registry,
        )
        self.structure_ready = Gauge(
            "aqt_paper_structure_ready",
            "Whether causal 1m, 5m, and 15m structure has warmed",
            registry=registry,
        )
        self.structure_direction = Gauge(
            "aqt_paper_structure_direction",
            "One-hot causal structure direction",
            ("timeframe", "direction"),
            registry=registry,
        )
        self.structure_level = Gauge(
            "aqt_paper_structure_level_usd",
            "Latest causal support or resistance level",
            ("timeframe", "kind"),
            registry=registry,
        )
        self.confluence = Gauge(
            "aqt_paper_smc_confluence_score",
            "Current multi-timeframe smart-money confluence score",
            ("side",),
            registry=registry,
        )
        self.expected_edge = Gauge(
            "aqt_paper_expected_edge_bps",
            "Latest strategy expected edge or open-position gross move",
            registry=registry,
        )
        self.required_edge = Gauge(
            "aqt_paper_required_edge_bps",
            "Latest cost-aware minimum edge",
            registry=registry,
        )
        self.position_age = Gauge(
            "aqt_paper_position_age_seconds",
            "Age of the current bounded scalping position",
            registry=registry,
        )
        self.exit_level = Gauge(
            "aqt_paper_exit_level_usd",
            "Current strategy stop or target level",
            ("kind",),
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
        decision = cycle.strategy_decision
        self.strategy_actions.labels(
            action=decision.action.value,
            reason=decision.reason,
        ).inc()
        self.strategy_decision.info(
            {
                "action": decision.action.value,
                "reason": decision.reason,
            }
        )
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
        self.market_price.set(float(cycle.features.midprice))
        self.best_bid.set(float(cycle.features.best_bid))
        self.best_ask.set(float(cycle.features.best_ask))
        self.microprice.set(float(cycle.features.microprice))
        self.spread_bps.set(float(cycle.features.spread_bps))
        self.book_imbalance.set(float(cycle.features.book_imbalance))
        self.trade_flow_imbalance.set(float(cycle.features.trade_flow_imbalance))
        self.expected_edge.set(float(decision.expected_edge_bps))
        self.required_edge.set(float(decision.required_edge_bps))
        self.position_age.set(float(decision.position_age_seconds))
        if decision.stop_price is not None:
            self.exit_level.labels(kind="stop").set(float(decision.stop_price))
        if decision.target_price is not None:
            self.exit_level.labels(kind="target").set(float(decision.target_price))
        self._observe_structure(cycle)
        if cycle.drift_report is not None:
            self.drift_ready.set(1)
            self.drift_maximum_psi.set(cycle.drift_report.maximum_psi)
            self.drift_maximum_mean_shift.set(cycle.drift_report.maximum_standardized_mean_shift)
        self.update_state(engine, initial_equity_usd=initial_equity_usd)

    def observe_watchdog(self, update: PaperWatchdogUpdate) -> None:
        for order in update.orders:
            self.order_updates.labels(state=order.state.value).inc()
        for fill in update.fills:
            self.fills.labels(liquidity="maker" if fill.maker else "taker").inc()
        for markout in update.markouts:
            self.markouts.observe(float(markout.signed_markout_bps))

    def observe_llm_confirmation(self, confirmation: LlmConfirmation) -> None:
        self.llm_confirmations.labels(verdict=confirmation.assessment.verdict.value).inc()
        self.llm_confidence.set(float(confirmation.assessment.confidence))
        self.llm_latency.observe(float(confirmation.latency_ms) / 1_000)

    def observe_llm_error(self, code: str) -> None:
        self.llm_errors.labels(code=code).inc()

    def _observe_structure(self, cycle: PaperEngineCycle) -> None:
        structure = cycle.market_structure
        if structure is None:
            self.structure_ready.set(0)
            return
        self.structure_ready.set(1 if structure.ready else 0)
        self.confluence.labels(side="long").set(structure.long_confluence)
        self.confluence.labels(side="short").set(structure.short_confluence)
        timeframes = {
            "1m": structure.one_minute,
            "5m": structure.five_minute,
            "15m": structure.fifteen_minute,
        }
        for timeframe, snapshot in timeframes.items():
            for direction in StructureDirection:
                self.structure_direction.labels(
                    timeframe=timeframe,
                    direction=direction.value,
                ).set(1 if snapshot.direction is direction else 0)
            if snapshot.support is not None:
                self.structure_level.labels(timeframe=timeframe, kind="support").set(
                    float(snapshot.support)
                )
            if snapshot.resistance is not None:
                self.structure_level.labels(timeframe=timeframe, kind="resistance").set(
                    float(snapshot.resistance)
                )

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
