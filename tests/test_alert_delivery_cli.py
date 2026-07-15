from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_alert_delivery_cli_loads_repo_dotenv(monkeypatch, tmp_path):
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "TELEGRAM_BOT_TOKEN=test-token-from-dotenv\n"
        "TELEGRAM_ALERT_CHAT_ID=test-chat-from-dotenv\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALERT_CHAT_ID", raising=False)

    spec = importlib.util.spec_from_file_location(
        "alert_delivery_cli_dotenv_test",
        ROOT / "scripts" / "alert_delivery.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALERT_CHAT_ID", raising=False)

    module._load_repo_dotenv(dotenv_path)

    assert module.os.getenv("TELEGRAM_BOT_TOKEN") == "test-token-from-dotenv"
    assert module.os.getenv("TELEGRAM_ALERT_CHAT_ID") == "test-chat-from-dotenv"
