from __future__ import annotations

import csv
import json
import math
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

import pandas as pd
import streamlit as st


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from runtime_paths import common_dir  # noqa: E402


LIVE_HEARTBEAT_SECONDS = 60


st.set_page_config(page_title="FinRobot Dashboard", page_icon="chart_with_upwards_trend", layout="wide")
st.markdown(
    """
    <style>
      .block-container { padding-top: 1rem; padding-bottom: 2rem; max-width: 1500px; }
      [data-testid="stMetricValue"] { font-size: 1.45rem; }
      [data-testid="stMetricDelta"] { font-size: .8rem; }
      .fr-muted { color: #6b7280; font-size: .85rem; }
      .fr-ok { color: #15803d; font-weight: 700; }
      .fr-warn { color: #b45309; font-weight: 700; }
      .fr-bad { color: #b91c1c; font-weight: 700; }
    </style>
    """,
    unsafe_allow_html=True,
)


def read_json(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists() or path.stat().st_size == 0:
        return {}
    try:
        return json.loads(path.read_text(errors="replace"))
    except Exception:
        return {}


def read_csv_rows(path: Path | None) -> list[dict[str, str]]:
    if path is None or not path.exists() or not path.stat().st_size:
        return []
    try:
        with path.open(errors="replace", newline="") as handle:
            return list(csv.DictReader(handle))
    except Exception:
        return []


def read_log_tail(path: Path, lines: int = 220) -> str:
    if not path.exists():
        return ""
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except Exception as exc:
        return f"Unable to read log: {exc}"


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value not in (None, "") else default)
    except Exception:
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value if value not in (None, "") else default))
    except Exception:
        return default


def money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return "-"


def pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.2f}%"
    except Exception:
        return "-"


def number(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except Exception:
        return "-"


def status_label(status_path: Path | None, age_seconds: float | None) -> tuple[str, str]:
    if status_path is None:
        return "NO STATUS FILE", "bad"
    if age_seconds is not None and age_seconds <= LIVE_HEARTBEAT_SECONDS:
        return "LIVE", "ok"
    return "STALE", "warn"


def strategy_name(row: dict[str, str], entries: dict[str, list[dict[str, str]]]) -> str:
    entry = (entries.get(row.get("position_id") or "") or [{}])[0]
    comment = entry.get("comment") or row.get("comment") or "UNKNOWN"
    return str(comment).replace("FinRobot_", "")


def summarize_deals(rows: list[dict[str, str]]) -> dict[str, Any]:
    entries: dict[str, list[dict[str, str]]] = defaultdict(list)
    exits: list[dict[str, str]] = []
    for row in rows:
        if str(row.get("entry")) == "0":
            entries[row.get("position_id") or ""].append(row)
        if str(row.get("entry")) in {"1", "3"} or as_float(row.get("profit")) != 0:
            exits.append(row)

    by_symbol: dict[str, list[float]] = defaultdict(list)
    by_strategy: dict[str, list[float]] = defaultdict(list)
    by_day: dict[str, list[float]] = defaultdict(list)
    equity_curve: list[dict[str, Any]] = []
    cumulative = 0.0

    for row in exits:
        pnl = as_float(row.get("profit")) + as_float(row.get("commission")) + as_float(row.get("swap"))
        symbol = row.get("symbol") or "UNKNOWN"
        strategy = strategy_name(row, entries)
        day = (row.get("time") or "")[:10]
        cumulative += pnl
        by_symbol[symbol].append(pnl)
        by_strategy[f"{symbol}:{strategy}"].append(pnl)
        if day:
            by_day[day].append(pnl)
        equity_curve.append(
            {
                "time": row.get("time") or "",
                "symbol": symbol,
                "strategy": strategy,
                "pnl": pnl,
                "cumulative_pnl": cumulative,
            }
        )

    def stats(values: list[float]) -> dict[str, Any]:
        wins = [value for value in values if value > 0]
        losses = [value for value in values if value <= 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        return {
            "deals": len(values),
            "pnl": round(sum(values), 2),
            "win_rate": round(len(wins) / len(values) * 100, 2) if values else 0.0,
            "avg_win": round(mean(wins), 2) if wins else 0.0,
            "avg_loss": round(mean(losses), 2) if losses else 0.0,
            "expectancy": round(sum(values) / len(values), 4) if values else 0.0,
            "profit_factor": math.inf if gross_loss == 0 and gross_win > 0 else round(gross_win / gross_loss, 4) if gross_loss else 0.0,
        }

    all_pnls = [item["pnl"] for item in equity_curve]
    return {
        "total": stats(all_pnls),
        "by_symbol": {key: stats(values) for key, values in sorted(by_symbol.items())},
        "by_strategy": {key: stats(values) for key, values in sorted(by_strategy.items())},
        "by_day": {key: stats(values) for key, values in sorted(by_day.items())},
        "equity_curve": equity_curve,
    }


def rows_to_frame(rows: list[dict[str, str]], numeric: list[str] | None = None) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    for column in numeric or []:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def summary_frame(summary: dict[str, dict[str, Any]], name: str) -> pd.DataFrame:
    rows = []
    for key, values in summary.items():
        row = {name: key}
        row.update(values)
        rows.append(row)
    frame = pd.DataFrame(rows)
    if not frame.empty and "pnl" in frame:
        frame = frame.sort_values("pnl", ascending=False)
    return frame


def symbol_status_frame(status: dict[str, Any]) -> pd.DataFrame:
    rows = []
    for item in status.get("symbols") or []:
        telemetry = item.get("signal_telemetry") or {}
        row = {
            "symbol": item.get("symbol"),
            "bid": item.get("bid"),
            "ask": item.get("ask"),
            "spread_points": item.get("spread_points"),
            "managed_positions": item.get("auto_positions"),
            "session_open": item.get("session_open"),
            "session_gated": item.get("session_gated"),
            "last_signal": item.get("last_signal"),
        }
        row.update({f"telemetry_{key}": value for key, value in telemetry.items() if key != "day"})
        rows.append(row)
    return pd.DataFrame(rows)


def money_management_frame(status: dict[str, Any]) -> pd.DataFrame:
    mm = status.get("money_management") or {}
    rows = [
        ("Broker day", mm.get("day")),
        ("Daily equity snapshot", money(mm.get("daily_equity_snapshot"))),
        ("Today closed PnL", money(mm.get("today_closed_pnl"))),
        ("Risk per trade", pct(mm.get("daily_risk_per_trade_fraction"))),
        ("Daily loss limit", pct(mm.get("daily_loss_limit_fraction"))),
        ("Loss limit reached", "yes" if mm.get("loss_limit_reached") else "no"),
        ("Risk lot sizing", "enabled" if mm.get("risk_lot_sizing") else "disabled"),
        ("Auto-close no SL/TP", "enabled" if mm.get("auto_close_no_sl_tp") else "disabled"),
    ]
    return pd.DataFrame(rows, columns=["field", "value"]).astype(str)


def pm2_status() -> str:
    try:
        result = subprocess.run(["pm2", "list"], text=True, capture_output=True, timeout=5)
        output = result.stdout.strip() or result.stderr.strip()
        return output[-6000:]
    except Exception as exc:
        return f"pm2 unavailable: {exc}"


def pgrep_status() -> str:
    try:
        result = subprocess.run(
            ["pgrep", "-af", "terminal64.exe|start_mt5|wineserver|streamlit"],
            text=True,
            capture_output=True,
            timeout=5,
        )
        return result.stdout.strip() or "none"
    except Exception as exc:
        return f"process check unavailable: {exc}"


common = common_dir()
status_path = common / "finrobot_status.json" if common else None
positions_path = common / "finrobot_positions.csv" if common else None
deals_path = common / "finrobot_deals.csv" if common else None
acks_path = common / "finrobot_acks.csv" if common else None

status = read_json(status_path)
status_age = time.time() - status_path.stat().st_mtime if status_path and status_path.exists() else None
positions = read_csv_rows(positions_path)
deals = read_csv_rows(deals_path)
acks = read_csv_rows(acks_path)
deal_summary = summarize_deals(deals)
label, label_class = status_label(status_path, status_age)


with st.sidebar:
    st.title("FinRobot")
    st.caption("MT5 demo monitor for XAUUSD")
    st.markdown(f"<span class='fr-{label_class}'>{label}</span>", unsafe_allow_html=True)
    st.write(f"Heartbeat age: {status_age:.1f}s" if status_age is not None else "Heartbeat unavailable")
    st.write(f"Common files: `{common or 'not found'}`")
    if st.button("Refresh now", width="stretch"):
        st.rerun()
    st.caption("Auto-refresh is disabled.")


st.title("FinRobot Trading Dashboard")
st.caption("Read-only live status from MT5 Common Files and PM2 logs.")

balance = as_float(status.get("balance"))
equity = as_float(status.get("equity"))
floating = equity - balance
mm = status.get("money_management") or {}
total = deal_summary["total"]

top = st.columns(6)
top[0].metric("Equity", money(equity), delta=money(floating))
top[1].metric("Balance", money(balance))
top[2].metric("Open Positions", as_int(status.get("positions")))
top[3].metric("Today PnL", money(mm.get("today_closed_pnl")))
top[4].metric("Closed PnL", money(total.get("pnl")))
top[5].metric("Closed Deals", as_int(total.get("deals")))

tabs = st.tabs(["Overview", "Symbols", "Trades", "Runtime"])

with tabs[0]:
    left, right = st.columns([1.1, 1])
    with left:
        st.subheader("Account")
        account_rows = [
            ("Login", status.get("login", "-")),
            ("Server", status.get("server", "-")),
            ("EA version", status.get("ea_version", "-")),
            ("Git SHA", status.get("git_sha", "-")),
            ("Terminal trading", "enabled" if status.get("trade_allowed_terminal") else "disabled"),
            ("EA trading", "enabled" if status.get("trade_allowed_ea") else "disabled"),
            ("Last command id", status.get("last_command_id", "-")),
            ("Last signal", status.get("last_auto_signal", "-")),
        ]
        st.dataframe(pd.DataFrame(account_rows, columns=["field", "value"]).astype(str), hide_index=True, width="stretch")
    with right:
        st.subheader("Money Management")
        st.dataframe(money_management_frame(status), hide_index=True, width="stretch")

    st.subheader("Cumulative Closed PnL")
    curve = pd.DataFrame(deal_summary["equity_curve"])
    if curve.empty:
        st.info("No closed managed deals exported yet.")
    else:
        curve["time"] = pd.to_datetime(curve["time"], errors="coerce")
        st.line_chart(curve.set_index("time")["cumulative_pnl"], width="stretch")

with tabs[1]:
    st.subheader("Symbol Status And Telemetry")
    symbols = symbol_status_frame(status)
    if symbols.empty:
        st.info("No per-symbol heartbeat data exported yet.")
    else:
        st.dataframe(symbols, hide_index=True, width="stretch")

    st.subheader("Signal Rejections Today")
    if not symbols.empty:
        telemetry_cols = [col for col in symbols.columns if col.startswith("telemetry_")]
        if telemetry_cols:
            chart = symbols[["symbol", *telemetry_cols]].set_index("symbol")
            chart.columns = [col.replace("telemetry_", "") for col in chart.columns]
            st.bar_chart(chart, width="stretch")

with tabs[2]:
    left, right = st.columns([1, 1])
    with left:
        st.subheader("Open Managed Positions")
        position_frame = rows_to_frame(
            positions,
            numeric=["volume", "open_price", "current_price", "profit", "sl", "tp"],
        )
        if position_frame.empty:
            st.info("No open managed positions.")
        else:
            st.dataframe(position_frame, hide_index=True, width="stretch")
    with right:
        st.subheader("Recent Acknowledgements")
        ack_frame = rows_to_frame(acks, numeric=["volume", "price"]).tail(30)
        if ack_frame.empty:
            st.info("No acknowledgements exported yet.")
        else:
            st.dataframe(ack_frame, hide_index=True, width="stretch")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.subheader("By Symbol")
        frame = summary_frame(deal_summary["by_symbol"], "symbol")
        st.dataframe(frame, hide_index=True, width="stretch")
    with c2:
        st.subheader("By Day")
        frame = summary_frame(deal_summary["by_day"], "day")
        st.dataframe(frame, hide_index=True, width="stretch")
    with c3:
        st.subheader("By Strategy")
        frame = summary_frame(deal_summary["by_strategy"], "strategy")
        st.dataframe(frame.head(20), hide_index=True, width="stretch")

    st.subheader("Recent Deals")
    deal_frame = rows_to_frame(
        deals,
        numeric=["volume", "price", "profit", "commission", "swap"],
    ).tail(80)
    if deal_frame.empty:
        st.info("No managed deals exported yet.")
    else:
        st.dataframe(deal_frame, hide_index=True, width="stretch")

with tabs[3]:
    left, right = st.columns([1, 1])
    with left:
        st.subheader("PM2")
        st.code(pm2_status(), language="text")
    with right:
        st.subheader("Processes")
        st.code(pgrep_status(), language="text")

    st.subheader("Combined Log")
    st.code(read_log_tail(ROOT / "logs" / "combined.log", 260), language="text")
