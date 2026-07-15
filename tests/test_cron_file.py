from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRON_PATH = ROOT / "config" / "aiquanttrader.cron"
EXPECTED_SCRIPTS = {
    "scripts/mt5_minute_cycle.py",
    "scripts/mt5_validate_warehouse.py",
    "scripts/mt5_metrics_export.py",
    "scripts/archive_common_files.py",
    "scripts/alert_delivery.py",
    "scripts/healthcheck.py",
}
LOG_REDIRECT = ">> /root/AIQuantTrader/logs/cron.log 2>&1"


def test_cron_file_has_top_level_operator_comments():
    comment_block = "\n".join(CRON_PATH.read_text().splitlines()[:8]).lower()

    assert "install" in comment_block
    assert "uninstall" in comment_block
    assert "inspect" in comment_block


def test_cron_lines_are_system_crontab_syntax():
    for line in _cron_lines():
        fields = line.split()
        assert len(fields) >= 7, line
        minute, hour, day, month, weekday, user, *command = fields

        assert _cron_field(minute)
        assert _cron_field(hour)
        assert _cron_field(day)
        assert _cron_field(month)
        assert _cron_field(weekday)
        assert user == "root"
        assert command


def test_expected_scripts_are_scheduled():
    scripts = {_script_for(line) for line in _cron_lines()}

    assert scripts == EXPECTED_SCRIPTS


def test_script_lines_append_to_cron_log():
    for line in _cron_lines():
        script = _script_for(line)

        assert script in EXPECTED_SCRIPTS
        assert line.endswith(LOG_REDIRECT)


def test_metrics_export_writes_alert_payload_for_delivery():
    line = next(
        line
        for line in _cron_lines()
        if _script_for(line) == "scripts/mt5_metrics_export.py"
    )

    assert "--json" in line.split()


def test_duckdb_jobs_use_shared_lock():
    locked_scripts = {
        "scripts/mt5_minute_cycle.py",
        "scripts/mt5_metrics_export.py",
        "scripts/mt5_validate_warehouse.py",
    }
    for line in _cron_lines():
        if _script_for(line) in locked_scripts:
            assert "/usr/bin/flock" in line.split()
            assert "/tmp/aiquanttrader-duckdb.lock" in line.split()


def _cron_lines() -> list[str]:
    return [
        line
        for line in CRON_PATH.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _cron_field(value: str) -> bool:
    allowed = set("0123456789*/,-")
    return bool(value) and all(char in allowed for char in value)


def _script_for(line: str) -> str:
    for part in line.split():
        if part.startswith("scripts/") and part.endswith(".py"):
            return part
    raise AssertionError(f"cron line does not reference a script: {line}")
