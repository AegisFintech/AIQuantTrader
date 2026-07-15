from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import requests
except ModuleNotFoundError:  # pragma: no cover - exercised only when requests is absent.
    import json as _json
    import urllib.error
    import urllib.request

    class _RequestsHTTPError(RuntimeError):
        pass

    class _RequestsExceptions:
        HTTPError = _RequestsHTTPError

    class _RequestsResponse:
        def __init__(self, status_code: int, body: bytes):
            self.status_code = status_code
            self._body = body

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise _RequestsHTTPError(f"HTTP {self.status_code}")

        def json(self) -> Any:
            return _json.loads(self._body.decode("utf-8"))

    class _RequestsCompat:
        exceptions = _RequestsExceptions
        HTTPError = _RequestsHTTPError

        @staticmethod
        def post(url: str, json: dict[str, Any], timeout: int) -> _RequestsResponse:
            data = _json.dumps(json).encode("utf-8")
            request = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return _RequestsResponse(
                        int(getattr(response, "status", response.getcode())),
                        response.read(),
                    )
            except urllib.error.HTTPError as exc:
                return _RequestsResponse(int(exc.code), exc.read())

    requests = _RequestsCompat()


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "state" / "alerts" / "last_state.json"
PARSE_MODE = "Markdown"

SEVERITY_EMOJI = {
    "critical": "🚨",
    "warning": "⚠️",
    "info": "ℹ️",
}


def load_metrics(path: Path) -> dict:
    """Read data/metrics.json. Returns empty dict if missing or invalid."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def last_state_path() -> Path:
    """Return the path to state/alerts/last_state.json (the de-dup file)."""
    return STATE_PATH


def diff_alerts(
    current: list[dict], previous: list[dict] | None
) -> list[dict[str, Any]]:
    """Diff current vs previous alerts and return fired/resolved transitions."""
    current_alerts = _valid_alerts(current)
    if previous is None:
        return [{"kind": "fired", "alert": alert} for alert in current_alerts]

    previous_alerts = _valid_alerts(previous)
    previous_by_name = {str(alert["name"]): alert for alert in previous_alerts}
    current_by_name = {str(alert["name"]): alert for alert in current_alerts}

    transitions: list[dict[str, Any]] = []
    for alert in current_alerts:
        name = str(alert["name"])
        previous_alert = previous_by_name.get(name)
        if previous_alert is None or previous_alert.get("severity") != alert.get("severity"):
            transitions.append({"kind": "fired", "alert": alert})

    for alert in previous_alerts:
        name = str(alert["name"])
        if name not in current_by_name:
            transitions.append(
                {
                    "kind": "resolved",
                    "name": name,
                    "severity": str(alert["severity"]),
                }
            )
    return transitions


def should_send_to_telegram(transition: dict) -> bool:
    """Return True when the transition warrants a Telegram message."""
    severity = _transition_severity(transition)
    kind = str(transition.get("kind") or "")
    if severity == "critical":
        return True
    if severity == "warning" and kind in {"fired", "resolved"}:
        return True
    return False


def format_telegram_message(
    transition: dict, current_snapshot_excerpt: dict
) -> tuple[str, str]:
    """Return a compact Markdown Telegram message for an alert transition."""
    kind = str(transition.get("kind") or "fired")
    alert = transition.get("alert") if isinstance(transition.get("alert"), dict) else {}
    name = str(alert.get("name") or transition.get("name") or "unknown_alert")
    severity = str(alert.get("severity") or transition.get("severity") or "info").lower()
    emoji = SEVERITY_EMOJI.get(severity, "•")
    detail = str(
        alert.get("detail")
        or transition.get("detail")
        or ("alert cleared" if kind == "resolved" else "no detail provided")
    )
    metric_path = str(alert.get("metric_path") or "n/a")
    metric_value = alert.get("metric_value", "n/a")
    snapshot = _snapshot_payload(current_snapshot_excerpt)

    validator = snapshot.get("validator_issues")
    validator_text = _format_validator_issues(validator)
    heartbeat_age = _format_seconds(snapshot.get("heartbeat_age_seconds"))

    lines = [
        f"{emoji} *FinRobot alert {kind}*",
        f"*Alert:* `{_escape_code(name)}`",
        f"*Severity:* {severity}",
        f"*Detail:* {_escape_text(detail)}",
        f"*Metric:* `{_escape_code(metric_path)}` = `{_escape_code(metric_value)}`",
        f"*Equity:* {_format_money(snapshot.get('equity'))} | *Daily PnL:* {_format_money(snapshot.get('daily_pnl'))}",
        f"*Heartbeat:* {heartbeat_age} | *Validator:* {validator_text}",
    ]
    return "\n".join(lines), PARSE_MODE


def save_state(path: Path, current_alerts: list[dict]) -> None:
    """Persist current alert names and severities for the next diff run."""
    payload = [
        {"name": str(alert["name"]), "severity": str(alert["severity"]).lower()}
        for alert in _valid_alerts(current_alerts)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def telegram_send(
    transition: dict,
    snapshot: dict,
    *,
    bot_token: str,
    chat_id: str,
    dry_run: bool = False,
) -> dict | None:
    """Send one alert transition to Telegram, or print it when dry_run is true."""
    text, parse_mode = format_telegram_message(transition, snapshot)
    if dry_run:
        print(text)
        return None

    response = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {"response": payload}


def _valid_alerts(alerts: list[dict]) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        name = alert.get("name")
        severity = alert.get("severity")
        if not name or not severity:
            continue
        item = dict(alert)
        item["name"] = str(name)
        item["severity"] = str(severity).lower()
        valid.append(item)
    return valid


def _transition_severity(transition: dict) -> str:
    alert = transition.get("alert")
    if isinstance(alert, dict):
        return str(alert.get("severity") or "").lower()
    return str(transition.get("severity") or "").lower()


def _snapshot_payload(snapshot: dict) -> dict:
    nested = snapshot.get("snapshot") if isinstance(snapshot, dict) else None
    if isinstance(nested, dict):
        return nested
    return snapshot if isinstance(snapshot, dict) else {}


def _format_validator_issues(value: Any) -> str:
    if not isinstance(value, dict):
        return "n/a"
    errors = _int_or_none(value.get("errors"))
    warnings = _int_or_none(value.get("warnings"))
    if errors is None and warnings is None:
        return "n/a"
    return f"{errors or 0} errors, {warnings or 0} warnings"


def _format_seconds(value: Any) -> str:
    try:
        return f"{float(value):.1f}s"
    except (TypeError, ValueError):
        return "n/a"


def _format_money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "n/a"


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _escape_code(value: Any) -> str:
    return str(value).replace("`", "'")


def _escape_text(value: Any) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("[", "\\[")
        .replace("`", "'")
    )
