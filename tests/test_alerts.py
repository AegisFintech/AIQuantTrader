from __future__ import annotations

import json

from finrobot.alerts import (
    Alert,
    AlertSeverity,
    alert_clock_skew_large,
    alert_daily_loss_limit,
    alert_heartbeat_stale,
    alert_no_status_yet,
    alert_validator_errors,
    alert_warehouse_empty,
    alerts_to_dict,
    evaluate_alerts,
    exit_code_for,
    highest_severity,
)
from finrobot.metrics import MetricsSnapshot


def test_alert_heartbeat_stale_fires_when_stale():
    alert = alert_heartbeat_stale(_snapshot(heartbeat_stale=True, heartbeat_age_seconds=120.0))

    assert alert is not None
    assert alert.severity == AlertSeverity.CRITICAL
    assert alert.metric_value == 120.0


def test_alert_heartbeat_stale_does_not_fire_when_fresh():
    assert alert_heartbeat_stale(_snapshot(heartbeat_stale=False)) is None


def test_alert_daily_loss_limit_fires_when_true():
    alert = alert_daily_loss_limit(_snapshot(daily_loss_limit_reached=True))

    assert alert is not None
    assert alert.name == "daily_loss_limit_reached"
    assert alert.severity == AlertSeverity.CRITICAL


def test_alert_clock_skew_large_threshold():
    assert alert_clock_skew_large(_snapshot(clock_skew_seconds=1700)) is None

    alert = alert_clock_skew_large(_snapshot(clock_skew_seconds=3700))
    assert alert is not None
    assert alert.severity == AlertSeverity.WARNING
    assert alert.metric_value == 3700


def test_alert_warehouse_empty_fires_on_empty_snapshot():
    alert = alert_warehouse_empty(
        _snapshot(warehouse={"status": 0, "positions": 0, "deals": 0, "acks": 0})
    )

    assert alert is not None
    assert alert.name == "warehouse_empty"
    assert alert.severity == AlertSeverity.WARNING


def test_alert_no_status_yet_fires_when_warehouse_has_status_rows():
    alert = alert_no_status_yet(
        _snapshot(heartbeat_age_seconds=None, warehouse={"status": 2, "positions": 0, "deals": 0, "acks": 0})
    )

    assert alert is not None
    assert alert.severity == AlertSeverity.INFO


def test_alert_validator_errors_fires_when_errors_present():
    alert = alert_validator_errors(
        _snapshot(validator_issues={"errors": 1, "warnings": 4, "by_check": {"x": 1}})
    )

    assert alert is not None
    assert alert.name == "validator_errors"
    assert alert.severity == AlertSeverity.CRITICAL


def test_evaluate_alerts_returns_union_of_triggered_rules():
    snap = _snapshot(
        heartbeat_stale=True,
        heartbeat_age_seconds=90.0,
        daily_loss_limit_reached=True,
        clock_skew_seconds=3700,
        pm2_mt5_restarts=7,
        validator_issues={"errors": 2, "warnings": 0, "by_check": {"bad": 2}},
    )

    names = {alert.name for alert in evaluate_alerts(snap)}

    assert {
        "heartbeat_stale",
        "daily_loss_limit_reached",
        "clock_skew_large",
        "high_restart_count",
        "validator_errors",
    } <= names


def test_highest_severity_returns_worst():
    alerts = [
        Alert("info", AlertSeverity.INFO, "info"),
        Alert("warning", AlertSeverity.WARNING, "warning"),
        Alert("critical", AlertSeverity.CRITICAL, "critical"),
    ]

    assert highest_severity(alerts) == AlertSeverity.CRITICAL
    assert highest_severity([]) is None


def test_exit_code_for_right_combinations():
    assert exit_code_for([]) == 0
    assert exit_code_for([Alert("warn", AlertSeverity.WARNING, "warn")]) == 0
    assert exit_code_for([Alert("critical", AlertSeverity.CRITICAL, "critical")]) == 1
    assert exit_code_for([Alert("warehouse_empty", AlertSeverity.WARNING, "empty")]) == 2


def test_alerts_to_dict_round_trips_through_json():
    alerts = [
        Alert(
            name="clock_skew_large",
            severity=AlertSeverity.WARNING,
            detail="broker clock skew is greater than one hour",
            metric_path="clock_skew_seconds",
            metric_value=3700,
        )
    ]

    payload = alerts_to_dict(alerts)

    assert json.loads(json.dumps(payload))[0]["severity"] == "warning"


def _snapshot(**overrides) -> MetricsSnapshot:
    data = {
        "timestamp_local": 1781150000,
        "timestamp_iso": "2026-06-11T04:00:00+00:00",
        "heartbeat_age_seconds": 0.0,
        "heartbeat_stale": False,
        "daily_pnl": 0.0,
        "daily_loss_limit_reached": False,
        "open_managed_positions": 0,
        "balance": 1000.0,
        "equity": 1000.0,
        "margin": 0.0,
        "risk_lot_sizing_active": True,
        "per_symbol": {},
        "warehouse": {"status": 1, "positions": 0, "deals": 0, "acks": 0},
        "warehouse_freshness": {"status": 1, "positions": 0, "deals": 0, "acks": 0},
        "clock_skew_seconds": None,
        "pm2_mt5_restarts": None,
        "validator_issues": {"errors": 0, "warnings": 0, "by_check": {}},
    }
    data.update(overrides)
    return MetricsSnapshot(**data)
