from __future__ import annotations

import json
from pathlib import Path

import pytest

from aiquanttrader import alert_delivery


def test_load_metrics_returns_empty_dict_on_missing_file(tmp_path):
    assert alert_delivery.load_metrics(tmp_path / "missing.json") == {}


def test_load_metrics_returns_empty_dict_on_invalid_json(tmp_path):
    path = tmp_path / "metrics.json"
    path.write_text("{not json", encoding="utf-8")

    assert alert_delivery.load_metrics(path) == {}


def test_last_state_path_returns_expected_repo_state_path():
    expected = Path(alert_delivery.__file__).resolve().parents[1] / "state" / "alerts" / "last_state.json"

    assert alert_delivery.last_state_path() == expected


def test_diff_alerts_first_run_fires_every_current_alert():
    current = [_alert("clock_skew_large"), _alert("high_restart_count")]

    transitions = alert_delivery.diff_alerts(current, previous=None)

    assert [item["kind"] for item in transitions] == ["fired", "fired"]
    assert [item["alert"]["name"] for item in transitions] == [
        "clock_skew_large",
        "high_restart_count",
    ]


def test_diff_alerts_same_current_and_previous_returns_no_transitions():
    current = [_alert("clock_skew_large", severity="warning")]
    previous = [{"name": "clock_skew_large", "severity": "warning"}]

    assert alert_delivery.diff_alerts(current, previous) == []


def test_diff_alerts_returns_fired_for_new_alerts_not_in_previous():
    current = [_alert("clock_skew_large"), _alert("high_restart_count")]
    previous = [{"name": "clock_skew_large", "severity": "warning"}]

    transitions = alert_delivery.diff_alerts(current, previous)

    assert transitions == [{"kind": "fired", "alert": current[1]}]


def test_diff_alerts_returns_resolved_for_previous_alerts_no_longer_current():
    current = [_alert("clock_skew_large")]
    previous = [
        {"name": "clock_skew_large", "severity": "warning"},
        {"name": "high_restart_count", "severity": "warning"},
    ]

    transitions = alert_delivery.diff_alerts(current, previous)

    assert transitions == [
        {"kind": "resolved", "name": "high_restart_count", "severity": "warning"}
    ]


def test_diff_alerts_returns_fired_for_changed_severity():
    current = [_alert("clock_skew_large", severity="critical")]
    previous = [{"name": "clock_skew_large", "severity": "warning"}]

    transitions = alert_delivery.diff_alerts(current, previous)

    assert transitions == [{"kind": "fired", "alert": current[0]}]


def test_should_send_to_telegram_true_for_any_critical_transition():
    assert alert_delivery.should_send_to_telegram(
        {"kind": "fired", "alert": _alert("heartbeat_stale", severity="critical")}
    )
    assert alert_delivery.should_send_to_telegram(
        {"kind": "resolved", "name": "heartbeat_stale", "severity": "critical"}
    )
    assert alert_delivery.should_send_to_telegram(
        {"kind": "still_firing", "alert": _alert("heartbeat_stale", severity="critical")}
    )


def test_should_send_to_telegram_true_for_warning_fired_or_resolved():
    assert alert_delivery.should_send_to_telegram(
        {"kind": "fired", "alert": _alert("clock_skew_large", severity="warning")}
    )
    assert alert_delivery.should_send_to_telegram(
        {"kind": "resolved", "name": "clock_skew_large", "severity": "warning"}
    )


def test_should_send_to_telegram_false_for_info_fired():
    assert not alert_delivery.should_send_to_telegram(
        {"kind": "fired", "alert": _alert("no_status_yet", severity="info")}
    )


def test_format_telegram_message_includes_alert_name_severity_and_detail():
    transition = {
        "kind": "fired",
        "alert": _alert(
            "clock_skew_large",
            severity="warning",
            detail="broker clock skew is greater than one hour",
            metric_path="clock_skew_seconds",
            metric_value=10799,
        ),
    }

    text, parse_mode = alert_delivery.format_telegram_message(transition, _snapshot())

    assert parse_mode == "Markdown"
    assert "clock_skew_large" in text
    assert "warning" in text
    assert "broker clock skew is greater than one hour" in text
    assert "10799" in text


def test_format_telegram_message_escapes_markdown_in_detail():
    transition = {
        "kind": "fired",
        "alert": _alert(
            "strategy_candidate_rejected",
            detail="macd_continuation_m1 [rejected]",
        ),
    }

    text, _ = alert_delivery.format_telegram_message(transition, _snapshot())

    assert "macd\\_continuation\\_m1 \\[rejected]" in text


def test_telegram_send_dry_run_does_not_make_http_call(monkeypatch, capsys):
    def fail_post(*args, **kwargs):
        raise AssertionError("requests.post should not be called")

    monkeypatch.setattr(alert_delivery.requests, "post", fail_post)

    result = alert_delivery.telegram_send(
        {"kind": "fired", "alert": _alert("clock_skew_large")},
        _snapshot(),
        bot_token="TOKEN",
        chat_id="CHAT",
        dry_run=True,
    )

    captured = capsys.readouterr()
    assert result is None
    assert "clock_skew_large" in captured.out


def test_telegram_send_success_returns_parsed_response(monkeypatch):
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True, "result": {"message_id": 123}}

    def fake_post(url, json, timeout):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return Response()

    monkeypatch.setattr(alert_delivery.requests, "post", fake_post)

    result = alert_delivery.telegram_send(
        {"kind": "fired", "alert": _alert("clock_skew_large")},
        _snapshot(),
        bot_token="TOKEN",
        chat_id="CHAT",
    )

    assert result == {"ok": True, "result": {"message_id": 123}}
    assert calls == [
        {
            "url": "https://api.telegram.org/botTOKEN/sendMessage",
            "json": {
                "chat_id": "CHAT",
                "text": calls[0]["json"]["text"],
                "parse_mode": "Markdown",
            },
            "timeout": 10,
        }
    ]
    assert "clock_skew_large" in calls[0]["json"]["text"]


def test_telegram_send_raises_on_4xx_response(monkeypatch):
    class Response:
        def raise_for_status(self):
            raise alert_delivery.requests.exceptions.HTTPError("403 Forbidden")

        def json(self):
            return {"ok": False}

    monkeypatch.setattr(alert_delivery.requests, "post", lambda *args, **kwargs: Response())

    with pytest.raises(alert_delivery.requests.exceptions.HTTPError):
        alert_delivery.telegram_send(
            {"kind": "fired", "alert": _alert("clock_skew_large")},
            _snapshot(),
            bot_token="TOKEN",
            chat_id="CHAT",
        )


def test_save_state_round_trip_diff_produces_no_next_run_transitions(tmp_path):
    path = tmp_path / "state" / "alerts" / "last_state.json"
    current = [_alert("clock_skew_large"), _alert("high_restart_count")]

    alert_delivery.save_state(path, current)
    previous = json.loads(path.read_text(encoding="utf-8"))

    assert previous == [
        {"name": "clock_skew_large", "severity": "warning"},
        {"name": "high_restart_count", "severity": "warning"},
    ]
    assert alert_delivery.diff_alerts(current, previous) == []


def _alert(
    name: str,
    *,
    severity: str = "warning",
    detail: str = "detail",
    metric_path: str = "metric.path",
    metric_value=1,
) -> dict:
    return {
        "name": name,
        "severity": severity,
        "detail": detail,
        "metric_path": metric_path,
        "metric_value": metric_value,
    }


def _snapshot() -> dict:
    return {
        "equity": 1007048.22,
        "daily_pnl": 38.91,
        "heartbeat_age_seconds": 0.5,
        "validator_issues": {"errors": 0, "warnings": 8, "by_check": {}},
    }
