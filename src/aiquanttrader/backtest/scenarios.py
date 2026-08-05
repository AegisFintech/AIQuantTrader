"""Strict loading and HftBacktest construction for versioned scenarios."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import numpy as np
from hftbacktest import (
    BUY_EVENT,
    DEPTH_EVENT,
    SELL_EVENT,
    TRADE_EVENT,
    BacktestAsset,
    event_dtype,
)

from aiquanttrader.backtest.models import ExecutionScenario, QueueModel, ValidationPolicy


def load_scenario(path: Path) -> ExecutionScenario:
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    return ExecutionScenario.model_validate(payload)


def load_validation_policy(path: Path) -> ValidationPolicy:
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    return ValidationPolicy.model_validate(payload)


def stressed_events(
    events: np.ndarray[Any, np.dtype[Any]], scenario: ExecutionScenario
) -> np.ndarray[Any, np.dtype[Any]]:
    """Apply scenario liquidity assumptions without mutating the admitted dataset."""

    if events.dtype != event_dtype:
        raise ValueError("events do not use the pinned HftBacktest dtype")
    stressed = events.copy()
    event_flags = stressed["ev"]
    depth = event_flags & DEPTH_EVENT == DEPTH_EVENT
    trades = event_flags & TRADE_EVENT == TRADE_EVENT
    stressed["qty"][depth] *= float(scenario.book_liquidity_multiplier)
    stressed["qty"][trades] *= float(scenario.trade_flow_multiplier)
    if np.any(stressed["qty"] < 0):
        raise ValueError("stressed event quantities cannot be negative")
    return stressed


def build_hft_asset(
    events: np.ndarray[Any, np.dtype[Any]], scenario: ExecutionScenario
) -> BacktestAsset:
    """Build one linear BTC asset with every material assumption explicit."""

    asset = (
        BacktestAsset()
        .data(stressed_events(events, scenario))
        .linear_asset(1.0)
        .constant_order_latency(scenario.entry_latency_ns, scenario.response_latency_ns)
        .latency_offset(scenario.feed_latency_offset_ns)
        .trading_value_fee_model(scenario.maker_fee_rate, scenario.taker_fee_rate)
        .tick_size(float(scenario.tick_size))
        .lot_size(float(scenario.lot_size))
        .last_trades_capacity(10_000)
    )
    if scenario.queue_model is QueueModel.LOG_PROBABILITY:
        asset.log_prob_queue_model()
    elif scenario.queue_model is QueueModel.POWER_PROBABILITY:
        asset.power_prob_queue_model(float(scenario.queue_power))
    else:
        asset.risk_adverse_queue_model()
    if scenario.allow_partial_fills:
        asset.partial_fill_exchange()
    else:
        asset.no_partial_fill_exchange()
    return asset


def scenario_event_counts(events: np.ndarray[Any, np.dtype[Any]]) -> dict[str, int]:
    flags = events["ev"]
    return {
        "depth": int(np.sum(flags & DEPTH_EVENT == DEPTH_EVENT)),
        "trade": int(np.sum(flags & TRADE_EVENT == TRADE_EVENT)),
        "bid": int(np.sum(flags & BUY_EVENT == BUY_EVENT)),
        "ask": int(np.sum(flags & SELL_EVENT == SELL_EVENT)),
    }
