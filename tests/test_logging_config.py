from __future__ import annotations

import json
import logging

import pytest

from finrobot.utils import logging_config


@pytest.fixture
def isolated_logging(tmp_path, monkeypatch):
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level
    monkeypatch.setattr(logging_config, "LOG_DIR", tmp_path)
    monkeypatch.setattr(logging_config, "LOG_FILE", tmp_path / "finrobot.log")
    monkeypatch.delenv("JSON_LOGS", raising=False)
    logging_config._logging_configured = False
    yield tmp_path / "finrobot.log"
    for handler in list(root_logger.handlers):
        if handler not in original_handlers:
            root_logger.removeHandler(handler)
            handler.close()
    root_logger.setLevel(original_level)
    logging_config._logging_configured = False


def test_setup_logging_json_format_writes_json_lines(isolated_logging):
    logging_config.setup_logging(json_format=True)
    logging.getLogger("finrobot.test").info("json hello")

    records = _json_records(isolated_logging)

    assert records[-1]["message"] == "json hello"


def test_json_lines_have_required_keys(isolated_logging):
    logging_config.setup_logging(json_format=True)
    logging.getLogger("finrobot.test").warning("required keys")

    record = _json_records(isolated_logging)[-1]

    assert {"ts", "level", "logger", "message"} <= set(record)
    assert record["level"] == "WARNING"
    assert record["logger"] == "finrobot.test"


def test_setup_logging_plain_text_keeps_existing_format(isolated_logging):
    logging_config.setup_logging(json_format=False)
    logging.getLogger("finrobot.test").info("plain hello")
    _flush_handlers()

    text = isolated_logging.read_text()

    assert " | INFO     | finrobot.test" in text
    assert "plain hello" in text
    assert not text.splitlines()[-1].startswith("{")


def test_json_logs_env_var_enables_json_format(isolated_logging, monkeypatch):
    monkeypatch.setenv("JSON_LOGS", "1")
    logging_config.setup_logging()
    logging.getLogger("finrobot.test").info("env json")

    assert _json_records(isolated_logging)[-1]["message"] == "env json"


def test_custom_extras_show_up_in_json_output(isolated_logging):
    logging_config.setup_logging(json_format=True)
    logging.getLogger("finrobot.test").info("with extras", extra={"key": "value", "count": 2})

    record = _json_records(isolated_logging)[-1]

    assert record["key"] == "value"
    assert record["count"] == 2


def test_exceptions_are_captured_in_json_output(isolated_logging):
    logging_config.setup_logging(json_format=True)
    logger = logging.getLogger("finrobot.test")

    try:
        raise ValueError("bad value")
    except ValueError:
        logger.exception("exception happened")

    record = _json_records(isolated_logging)[-1]

    assert record["message"] == "exception happened"
    assert "ValueError: bad value" in record["exception"]


def _json_records(path):
    _flush_handlers()
    lines = [line for line in path.read_text().splitlines() if line]
    return [json.loads(line) for line in lines]


def _flush_handlers():
    for handler in logging.getLogger().handlers:
        handler.flush()
