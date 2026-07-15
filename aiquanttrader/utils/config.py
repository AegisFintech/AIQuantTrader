from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    mt5_login: int | None = int(os.getenv("MT5_LOGIN") or "0") or None
    mt5_password: str | None = os.getenv("MT5_PASSWORD")
    mt5_server: str | None = os.getenv("MT5_SERVER")
    mt5_mode: str = os.getenv("MT5_MODE", "demo")


settings = Settings()
