#!/usr/bin/env python3
from __future__ import annotations

import os
import re
from pathlib import Path

from runtime_paths import MT5_TERMINAL_DIR


CHART_ENCODING = "utf-16"
DEFAULT_SYMBOL = "XAUUSD"
DEFAULT_PERIOD = "M1"
DEFAULT_PERIOD_TYPE = "0"
DEFAULT_PERIOD_SIZE = "1"
STARTUP_EXPERT = "FinRobot\\FinRobotBridgeEA"


def replace_line(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    replacement = f"{key}={value}"
    if pattern.search(text):
        return pattern.sub(replacement, text, count=1)
    return text.replace("<chart>\n", f"<chart>\n{replacement}\n", 1)


def remove_expert_block(text: str) -> str:
    return re.sub(r"\r?\n?<expert>\r?\n.*?\r?\n</expert>\r?\n?", "\r\n", text, flags=re.DOTALL)


def configure_chart(chart_path: Path, symbol: str, period_type: str, period_size: str) -> None:
    text = chart_path.read_text(encoding=CHART_ENCODING).replace("\r\n", "\n")
    text = remove_expert_block(text)
    text = replace_line(text, "symbol", symbol)
    text = replace_line(text, "period_type", period_type)
    text = replace_line(text, "period_size", period_size)

    expert = (
        "<expert>\n"
        "name=FinRobotBridgeEA\n"
        "path=Experts\\FinRobot\\FinRobotBridgeEA.ex5\n"
        "expertmode=1\n"
        "<inputs>\n"
        "</inputs>\n"
        "</expert>\n"
    )
    if "<window>\n" in text:
        text = text.replace("<window>\n", expert + "\n<window>\n", 1)
    elif "</chart>" in text:
        text = text.replace("</chart>", expert + "\n</chart>", 1)
    else:
        text = text.rstrip() + "\n" + expert
    chart_path.write_text(text.replace("\n", "\r\n"), encoding=CHART_ENCODING, newline="")


def bool_ini(value: str | None, default: bool = True) -> str:
    if value is None or value == "":
        return "1" if default else "0"
    return "1" if value.strip().lower() in {"1", "true", "yes", "on"} else "0"


def set_ini_value(text: str, section: str, key: str, value: str) -> str:
    section_header = f"[{section}]"
    lines = text.replace("\r\n", "\n").split("\n")
    section_start = None
    section_end = len(lines)

    for i, line in enumerate(lines):
        if line.strip().lower() == section_header.lower():
            section_start = i
            break

    if section_start is None:
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend([section_header, f"{key}={value}"])
        return "\r\n".join(lines).rstrip("\r\n") + "\r\n"

    for i in range(section_start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section_end = i
            break

    key_prefix = f"{key.lower()}="
    for i in range(section_start + 1, section_end):
        if lines[i].strip().lower().startswith(key_prefix):
            lines[i] = f"{key}={value}"
            return "\r\n".join(lines).rstrip("\r\n") + "\r\n"

    lines.insert(section_end, f"{key}={value}")
    return "\r\n".join(lines).rstrip("\r\n") + "\r\n"


def delete_ini_value(text: str, section: str, key: str) -> str:
    section_header = f"[{section}]"
    lines = text.replace("\r\n", "\n").split("\n")
    section_start = None
    section_end = len(lines)

    for i, line in enumerate(lines):
        if line.strip().lower() == section_header.lower():
            section_start = i
            break

    if section_start is None:
        return text

    for i in range(section_start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section_end = i
            break

    key_prefix = f"{key.lower()}="
    lines = [
        line
        for i, line in enumerate(lines)
        if not (section_start < i < section_end and line.strip().lower().startswith(key_prefix))
    ]
    return "\r\n".join(lines).rstrip("\r\n") + "\r\n"


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def configure_startup(config_path: Path, symbol: str, period: str) -> None:
    text = config_path.read_text(encoding="utf-8") if config_path.exists() else "[Common]\r\n"
    text = set_ini_value(text, "Charts", "ProfileLast", "Default")
    text = set_ini_value(text, "Experts", "Enabled", "1")
    text = set_ini_value(text, "Experts", "AllowLiveTrading", bool_ini(os.getenv("MT5_AUTOTRADING_ENABLED"), True))
    text = set_ini_value(text, "Experts", "AllowDllImport", "0")
    text = set_ini_value(text, "Experts", "Account", "0")
    text = set_ini_value(text, "Experts", "Profile", "0")
    text = set_ini_value(text, "StartUp", "Expert", STARTUP_EXPERT)
    if env_bool("FINROBOT_STARTUP_OPEN_CHART", False):
        text = set_ini_value(text, "StartUp", "Symbol", symbol)
        text = set_ini_value(text, "StartUp", "Period", period)
    else:
        text = delete_ini_value(text, "StartUp", "Symbol")
        text = delete_ini_value(text, "StartUp", "Period")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text, encoding="utf-8", newline="")


def profile_dirs() -> list[Path]:
    return [
        MT5_TERMINAL_DIR / "MQL5" / "Profiles" / "Charts" / "Default",
        MT5_TERMINAL_DIR / "Profiles" / "Charts" / "Default",
    ]


def configure_profile_dir(profile_dir: Path, symbol: str, period_type: str, period_size: str) -> bool:
    chart_path = profile_dir / "chart01.chr"
    order_path = profile_dir / "order.wnd"
    if not chart_path.exists():
        return False

    configure_chart(chart_path, symbol, period_type, period_size)
    if env_bool("FINROBOT_SINGLE_CHART_PROFILE", True):
        for extra_chart in sorted(profile_dir.glob("chart*.chr")):
            if extra_chart.name != "chart01.chr":
                extra_chart.unlink()
    order_path.write_text("chart01.chr\r\n", encoding=CHART_ENCODING, newline="")
    return True


def main() -> int:
    config_path = MT5_TERMINAL_DIR / "Config" / "finrobot-login.ini"

    symbol = os.getenv("FINROBOT_ATTACH_SYMBOL", DEFAULT_SYMBOL)
    period = os.getenv("FINROBOT_ATTACH_PERIOD", DEFAULT_PERIOD)
    period_type = os.getenv("FINROBOT_ATTACH_PERIOD_TYPE", DEFAULT_PERIOD_TYPE)
    period_size = os.getenv("FINROBOT_ATTACH_PERIOD_SIZE", DEFAULT_PERIOD_SIZE)

    configured = [path for path in profile_dirs() if configure_profile_dir(path, symbol, period_type, period_size)]
    if not configured:
        searched = ", ".join(str(path / "chart01.chr") for path in profile_dirs())
        raise SystemExit(f"chart file not found; searched: {searched}")

    configure_startup(config_path, symbol, period)
    paths = ", ".join(str(path / "chart01.chr") for path in configured)
    print(f"Configured MT5 startup profile: {paths} -> {symbol} {period}, expert={STARTUP_EXPERT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
