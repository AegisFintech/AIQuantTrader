from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Callable

from aiquanttrader.metrics import MetricsSnapshot


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Alert:
    name: str
    severity: AlertSeverity
    detail: str
    metric_path: str = ""
    metric_value: Any = None


Rule = Callable[[MetricsSnapshot], Alert | None]


def alert_heartbeat_stale(snap: MetricsSnapshot) -> Alert | None:
    """Alert when the live status heartbeat is older than the configured threshold."""
    if not snap.heartbeat_stale:
        return None
    return Alert(
        name="heartbeat_stale",
        severity=AlertSeverity.CRITICAL,
        detail="aiquanttrader_status.json heartbeat is stale",
        metric_path="heartbeat_age_seconds",
        metric_value=snap.heartbeat_age_seconds,
    )


def alert_daily_loss_limit(snap: MetricsSnapshot) -> Alert | None:
    """Alert when the EA reports that the daily loss limit has been reached."""
    if not snap.daily_loss_limit_reached:
        return None
    return Alert(
        name="daily_loss_limit_reached",
        severity=AlertSeverity.CRITICAL,
        detail="EA reports daily loss limit reached",
        metric_path="daily_loss_limit_reached",
        metric_value=snap.daily_loss_limit_reached,
    )


def alert_clock_skew_large(snap: MetricsSnapshot) -> Alert | None:
    """Alert when broker/server clock skew is more than one hour."""
    if snap.clock_skew_seconds is None or abs(snap.clock_skew_seconds) <= 3600:
        return None
    return Alert(
        name="clock_skew_large",
        severity=AlertSeverity.WARNING,
        detail="broker clock skew is greater than one hour",
        metric_path="clock_skew_seconds",
        metric_value=snap.clock_skew_seconds,
    )


def alert_no_status_yet(snap: MetricsSnapshot) -> Alert | None:
    """Alert when the warehouse has status rows but no live status file is available."""
    if snap.heartbeat_age_seconds is not None or snap.warehouse.get("status", 0) <= 0:
        return None
    return Alert(
        name="no_status_yet",
        severity=AlertSeverity.INFO,
        detail="warehouse has status rows but no live status heartbeat is available",
        metric_path="heartbeat_age_seconds",
        metric_value=snap.heartbeat_age_seconds,
    )


def alert_warehouse_empty(snap: MetricsSnapshot) -> Alert | None:
    """Alert when the warehouse has no rows in monitored tables."""
    if sum(snap.warehouse.values()) != 0:
        return None
    return Alert(
        name="warehouse_empty",
        severity=AlertSeverity.WARNING,
        detail="warehouse has no status, position, deal, or ack rows",
        metric_path="warehouse",
        metric_value=snap.warehouse,
    )


def alert_validator_errors(snap: MetricsSnapshot) -> Alert | None:
    """Alert when warehouse validation found hard errors."""
    errors = int(snap.validator_issues.get("errors", 0) or 0)
    if errors <= 0:
        return None
    return Alert(
        name="validator_errors",
        severity=AlertSeverity.CRITICAL,
        detail=f"warehouse validator found {errors} error(s)",
        metric_path="validator_issues.errors",
        metric_value=errors,
    )


def alert_high_restart_count(snap: MetricsSnapshot) -> Alert | None:
    """Alert when PM2 reports an elevated MT5 terminal restart count."""
    if snap.pm2_mt5_restarts is None or snap.pm2_mt5_restarts < 5:
        return None
    return Alert(
        name="high_restart_count",
        severity=AlertSeverity.WARNING,
        detail="aiquanttrader-mt5 PM2 restart count is high",
        metric_path="pm2_mt5_restarts",
        metric_value=snap.pm2_mt5_restarts,
    )


BUILTIN_RULES: tuple[Rule, ...] = (
    alert_heartbeat_stale,
    alert_daily_loss_limit,
    alert_clock_skew_large,
    alert_no_status_yet,
    alert_warehouse_empty,
    alert_validator_errors,
    alert_high_restart_count,
)


def evaluate_alerts(snap: MetricsSnapshot) -> list[Alert]:
    """Run all built-in alert rules and return the triggered alerts."""
    alerts: list[Alert] = []
    for rule in BUILTIN_RULES:
        alert = rule(snap)
        if alert is not None:
            alerts.append(alert)
    return alerts


def alerts_to_dict(alerts: list[Alert]) -> list[dict[str, Any]]:
    """Return JSON-serializable dictionaries for alerts."""
    payload: list[dict[str, Any]] = []
    for alert in alerts:
        data = asdict(alert)
        data["severity"] = alert.severity.value
        payload.append(data)
    return payload


def highest_severity(alerts: list[Alert]) -> AlertSeverity | None:
    """Return the highest alert severity, or None when no alerts fired."""
    if not alerts:
        return None
    order = {
        AlertSeverity.INFO: 0,
        AlertSeverity.WARNING: 1,
        AlertSeverity.CRITICAL: 2,
    }
    return max((alert.severity for alert in alerts), key=lambda item: order[item])


def exit_code_for(alerts: list[Alert]) -> int:
    """Return the process exit code implied by an alert set."""
    if any(alert.severity == AlertSeverity.CRITICAL for alert in alerts):
        return 1
    if any(alert.name == "warehouse_empty" for alert in alerts):
        return 2
    return 0
