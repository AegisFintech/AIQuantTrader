import json
import math
import os
import re
import csv
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state" / "moonshot"
LOG_DIR = ROOT / "logs"
REFRESH_SECONDS = int(os.getenv("DASHBOARD_REFRESH_SECONDS", "15"))
AUTO_REFRESH = os.getenv("DASHBOARD_AUTO_REFRESH", "true").lower() in {"1", "true", "yes", "on"}
MYFXBOOK_URL = "https://www.myfxbook.com/members/AloysiusChan/trending/11809640"

st.set_page_config(page_title="FinRobot MT5", page_icon="📈", layout="wide")
st.markdown(
    """
    <style>
      .block-container { padding-top: 1rem; padding-bottom: 2rem; max-width: 1500px; }
      [data-testid="stMetricValue"] { font-size: 1.55rem; }
      .small-muted { color: #888; font-size: 0.85rem; }
      .hero {
        border: 1px solid rgba(120,120,120,.25);
        border-radius: 18px;
        padding: 1rem 1.25rem;
        background: linear-gradient(135deg, rgba(28,42,80,.85), rgba(12,16,24,.92));
        margin-bottom: .85rem;
      }
      .hero-title { color: #e8eefc; font-size: .95rem; letter-spacing: .08em; text-transform: uppercase; }
      .hero-value { color: white; font-size: 2.5rem; line-height: 1.05; font-weight: 750; }
      .hero-sub { color: #aeb8cf; font-size: .92rem; margin-top: .35rem; }
      .pill { display: inline-block; padding: .2rem .55rem; border-radius: 999px; font-size: .82rem; font-weight: 650; }
      .pill-live { color: #062b17; background: #72f0a3; }
      .pill-stale { color: #331e00; background: #ffcc66; }
      .section-card {
        border: 1px solid rgba(120,120,120,.22);
        border-radius: 14px;
        padding: .8rem 1rem;
        background: rgba(120,120,120,.06);
        margin-bottom: .7rem;
      }
      .card-label { color: #8d96a8; font-size: .78rem; text-transform: uppercase; letter-spacing: .06em; }
      .card-value { font-size: 1.35rem; font-weight: 750; margin-top: .12rem; }
      .card-sub { color: #8d96a8; font-size: .84rem; margin-top: .25rem; }
      .ok { color: #24c46b; font-weight: 700; }
      .warn { color: #ffb84d; font-weight: 700; }
      .bad { color: #ff5c77; font-weight: 700; }
    </style>
    """,
    unsafe_allow_html=True,
)


def read_json(path: Path, default):
    try:
        if not path.exists() or path.stat().st_size == 0:
            return default
        return json.loads(path.read_text())
    except Exception:
        return default


def read_jsonl(path: Path) -> pd.DataFrame:
    rows = []
    try:
        if path.exists():
            for line in path.read_text(errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        pass
    return pd.DataFrame(rows)


def fmt_money(value):
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return "—"


def as_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def fmt_pct(value):
    try:
        return f"{float(value):+.2f}%"
    except Exception:
        return "—"


def fmt_rate(value):
    try:
        return f"{float(value):.2f}%"
    except Exception:
        return "—"


def fmt_number(value, digits=2):
    try:
        return f"{float(value):,.{digits}f}"
    except Exception:
        return "—"


def fmt_volume(value):
    try:
        return f"{float(value):.4f}"
    except Exception:
        return "—"


def pnl_class(value):
    amount = as_float(value)
    if amount > 0:
        return "ok"
    if amount < 0:
        return "bad"
    return "warn"


def html_card(label, value, sub="", cls=""):
    klass = f"card-value {cls}".strip()
    return f"""
    <div class='section-card'>
      <div class='card-label'>{label}</div>
      <div class='{klass}'>{value}</div>
      <div class='card-sub'>{sub}</div>
    </div>
    """


def ts_to_dt(series):
    return pd.to_datetime(series, unit="s", errors="coerce")


def summarize_trades(df: pd.DataFrame):
    if df.empty or "pnl" not in df:
        return {"trades": 0, "win_rate": 0, "profit_factor": 0, "total_pnl": 0}
    d = df.copy()
    d["pnl"] = pd.to_numeric(d["pnl"], errors="coerce").fillna(0.0)
    wins = d[d["pnl"] > 0]
    losses = d[d["pnl"] <= 0]
    gross_win = wins["pnl"].sum()
    gross_loss = abs(losses["pnl"].sum())
    return {
        "trades": len(d),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(d) * 100 if len(d) else 0,
        "total_pnl": d["pnl"].sum(),
        "avg_win": wins["pnl"].mean() if len(wins) else 0,
        "avg_loss": losses["pnl"].mean() if len(losses) else 0,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else math.inf,
        "expectancy": d["pnl"].mean() if len(d) else 0,
    }


def recent_log(path: Path, lines=160):
    try:
        if not path.exists():
            return ""
        data = path.read_text(errors="replace").splitlines()
        return "\n".join(data[-lines:])
    except Exception as exc:
        return f"Could not read log: {exc}"


def find_mt5_status_path() -> Path | None:
    candidates = [
        Path("/home/openclaw/.wine-mt5/drive_c/users/openclaw/AppData/Roaming/MetaQuotes/Terminal/Common/Files/finrobot_status.json"),
        Path("/home/openclaw/mt5/terminal/current/MQL5/Files/finrobot_status.json"),
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def read_mt5_status():
    path = find_mt5_status_path()
    if not path:
        return {}, None, None
    status = read_json(path, {})
    ts = status.get("ts")
    age = time.time() - float(ts or 0) if ts else None
    return status, path, age


def bool_badge(value):
    return "🟢 Enabled" if bool(value) else "🔴 Disabled"


def live_badge(live: bool) -> str:
    cls = "pill-live" if live else "pill-stale"
    text = "LIVE" if live else "STALE"
    return f"<span class='pill {cls}'>MT5 {text}</span>"


def shell_lines(cmd: list[str], limit=80):
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT, timeout=6)
        return "\n".join(out.splitlines()[-limit:])
    except Exception as exc:
        return f"Unavailable: {exc}"


def recent_utf16_log(path: Path | None, lines=120):
    try:
        if not path or not path.exists():
            return ""
        text = path.read_bytes().decode("utf-16", errors="replace")
        return "\n".join(text.splitlines()[-lines:])
    except Exception as exc:
        return f"Could not read MT5 log: {exc}"


def extract_decision_lines(text: str, source: str, limit: int = 80) -> pd.DataFrame:
    keywords = [
        "signal", "decision", "reject", "rejected", "accepted", "filled", "order",
        "max positions", "funding rates", "rolling backtest", "summary", "promoted",
        "rollback", "pause", "blacklist", "disabled", "enabled", "auto_", "no_signal",
    ]
    rows = []
    for line in (text or "").splitlines():
        low = line.lower()
        if not any(k in low for k in keywords):
            continue
        ts = ""
        event = line
        m = re.match(r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:,\d+)?)\s*(?:\|\s*)?(.*)$", line)
        if m:
            ts, event = m.group(1), m.group(2)
        rows.append({"source": source, "time": ts, "decision / signal": event[-500:]})
    return pd.DataFrame(rows[-limit:])


def mt5_common_path(name: str) -> Path | None:
    candidates = [
        Path("/home/openclaw/.wine-mt5/drive_c/users/openclaw/AppData/Roaming/MetaQuotes/Terminal/Common/Files") / name,
        Path("/home/openclaw/.wine-mt5/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/Common/Files") / name,
        Path("/home/openclaw/mt5/terminal/current/MQL5/Files") / name,
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def mt5_ack_table(limit: int = 80) -> pd.DataFrame:
    path = mt5_common_path("finrobot_acks.csv")
    if path and path.stat().st_size:
        rows = []
        for parts in csv.reader(path.read_text(errors="replace").splitlines()):
            if not parts or str(parts[0]).lower() == "id":
                continue
            rows.append({
                "id": parts[0] if len(parts) > 0 else "",
                "time": parts[1] if len(parts) > 1 else "",
                "status": parts[2] if len(parts) > 2 else "",
                "message": parts[3] if len(parts) > 3 else "",
                "symbol": parts[4] if len(parts) > 4 else "",
                "side": parts[5] if len(parts) > 5 else "",
                "volume": parts[6] if len(parts) > 6 else "",
                "price": parts[7] if len(parts) > 7 else "",
            })
        return pd.DataFrame(rows[-limit:])
    return pd.DataFrame()


def mt5_positions_table() -> pd.DataFrame:
    path = mt5_common_path("finrobot_positions.csv")
    if path and path.stat().st_size:
        return pd.read_csv(path)
    return pd.DataFrame()


def mt5_deals_table() -> pd.DataFrame:
    path = mt5_common_path("finrobot_deals.csv")
    if path and path.stat().st_size:
        return pd.read_csv(path)
    return pd.DataFrame()


def clean_strategy(comment: str) -> str:
    text = str(comment or "").strip()
    text = text.replace("FinRobot_", "")
    text = text.replace("BTCUSD_", "").replace("XAUUSD_", "")
    return text or "manual / close"


def normalize_trade_frames(positions: pd.DataFrame, deals: pd.DataFrame):
    pos = positions.copy()
    if not pos.empty:
        for col in ["volume", "open_price", "current_price", "profit", "sl", "tp"]:
            if col in pos:
                pos[col] = pd.to_numeric(pos[col], errors="coerce")
        if "time" in pos:
            pos["time"] = pd.to_datetime(pos["time"], errors="coerce")
        if "comment" in pos:
            pos["strategy"] = pos["comment"].map(clean_strategy)

    d = deals.copy()
    if not d.empty:
        for col in ["volume", "price", "profit", "commission", "swap"]:
            if col in d:
                d[col] = pd.to_numeric(d[col], errors="coerce").fillna(0.0)
        if "time" in d:
            d["time"] = pd.to_datetime(d["time"], errors="coerce")
        if "comment" in d:
            d["strategy"] = d["comment"].map(clean_strategy)
    return pos, d


def open_exposure_summary(positions: pd.DataFrame) -> pd.DataFrame:
    if positions.empty or "symbol" not in positions:
        return pd.DataFrame(columns=["symbol", "positions", "net lots", "floating pnl", "long lots", "short lots"])
    rows = []
    for symbol, group in positions.groupby("symbol", dropna=False):
        types = group.get("type", pd.Series(dtype=str)).astype(str).str.upper()
        volume = pd.to_numeric(group.get("volume", 0), errors="coerce").fillna(0.0)
        profit = pd.to_numeric(group.get("profit", 0), errors="coerce").fillna(0.0)
        long_lots = volume[types == "BUY"].sum()
        short_lots = volume[types == "SELL"].sum()
        rows.append({
            "symbol": symbol,
            "positions": len(group),
            "net lots": long_lots - short_lots,
            "floating pnl": profit.sum(),
            "long lots": long_lots,
            "short lots": short_lots,
        })
    return pd.DataFrame(rows)


def closed_summary_by(df: pd.DataFrame, column: str) -> pd.DataFrame:
    if df.empty or column not in df or "profit" not in df:
        return pd.DataFrame()
    closed = df.copy()
    if "entry" in closed:
        closed = closed[pd.to_numeric(closed["entry"], errors="coerce").fillna(0).astype(int) == 1]
    if closed.empty:
        return pd.DataFrame()
    grouped = closed.groupby(column, dropna=False)["profit"]
    out = grouped.agg(deals="count", pnl="sum", expectancy="mean").reset_index()
    wins = closed[closed["profit"] > 0].groupby(column)["profit"].count()
    out["win rate"] = out[column].map(lambda item: wins.get(item, 0)) / out["deals"] * 100
    return out.sort_values("pnl", ascending=False)


def recent_fills_table(acks: pd.DataFrame, limit=20) -> pd.DataFrame:
    if acks.empty:
        return pd.DataFrame()
    fills = acks[acks.get("status", pd.Series(dtype=str)).astype(str).str.contains("FILLED|REJECTED", case=False, na=False)].copy()
    if fills.empty:
        fills = acks.copy()
    cols = [c for c in ["time", "status", "symbol", "side", "volume", "price", "message"] if c in fills]
    return fills.tail(limit)[cols]


def style_money_dataframe(df: pd.DataFrame):
    money_cols = [c for c in df.columns if c in {"profit", "floating pnl", "pnl", "expectancy", "avg_win", "avg_loss"}]
    formats = {c: "${:,.2f}" for c in money_cols}
    for col in ["volume", "net lots", "long lots", "short lots"]:
        if col in df:
            formats[col] = "{:.4f}"
    if "win rate" in df:
        formats["win rate"] = "{:.1f}%"
    return df.style.format(formats, na_rep="—")


def latest_signal_snapshot(mt5_status: dict, mt5_age: float | None, positions: dict, overrides: dict) -> pd.DataFrame:
    symbols = mt5_status.get("symbols") if isinstance(mt5_status, dict) else None
    rows = []
    if isinstance(symbols, list) and symbols:
        for item in symbols:
            rows.append({
                "system": "MT5",
                "decision": item.get("last_signal", "—"),
                "instrument": item.get("symbol", "—"),
                "price / spread": f"bid {item.get('bid', '—')} / ask {item.get('ask', '—')} / spread {item.get('spread_points', '—')} pts",
                "positions": f"{item.get('auto_positions', 0)} managed",
                "status": f"heartbeat {mt5_age:.0f}s" if mt5_age is not None else "no heartbeat",
            })
    else:
        rows.append({
            "system": "MT5",
            "decision": mt5_status.get("last_auto_signal", "—"),
            "instrument": mt5_status.get("symbol", "XAUUSD / BTCUSD"),
            "price / spread": "waiting for v1.20 heartbeat",
            "positions": str(mt5_status.get("positions", 0)),
            "status": f"heartbeat {mt5_age:.0f}s" if mt5_age is not None else "no heartbeat",
        })
    return pd.DataFrame(rows)


def latest_mt5_journal():
    roots = [
        Path("/home/openclaw/mt5/terminal/current/logs"),
        Path("/home/openclaw/.wine-mt5/drive_c/ICMarketsSCOfficialMT5/logs"),
        Path("/home/openclaw/.wine-mt5/drive_c/FinRobotMT5/logs"),
    ]
    logs = []
    for root in roots:
        if root.exists():
            logs.extend(root.glob("*.log"))
    return max(logs, key=lambda x: x.stat().st_mtime) if logs else None


overrides = read_json(STATE_DIR / "runtime_overrides.json", {})
journal = read_jsonl(STATE_DIR / "improver_journal.jsonl")
mt5_status, mt5_status_path, mt5_age = read_mt5_status()
mt5_deals = mt5_deals_table()
mt5_positions = mt5_positions_table()
mt5_positions, mt5_deals = normalize_trade_frames(mt5_positions, mt5_deals)
mt5_acks = mt5_ack_table()
mt5_live = mt5_age is not None and mt5_age < 30


def summarize_mt5_deals(df: pd.DataFrame):
    if df.empty or "profit" not in df:
        return {"deals": 0, "win_rate": 0, "profit_factor": 0, "total_pnl": 0}
    d = df.copy()
    if "entry" in d:
        d = d[pd.to_numeric(d["entry"], errors="coerce").fillna(0).astype(int) == 1]
    if d.empty:
        return {"deals": 0, "win_rate": 0, "profit_factor": 0, "total_pnl": 0}
    d["profit"] = pd.to_numeric(d["profit"], errors="coerce").fillna(0.0)
    wins = d[d["profit"] > 0]
    losses = d[d["profit"] <= 0]
    gross_win = wins["profit"].sum()
    gross_loss = abs(losses["profit"].sum())
    return {
        "deals": len(d),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(d) * 100 if len(d) else 0,
        "total_pnl": d["profit"].sum(),
        "avg_win": wins["profit"].mean() if len(wins) else 0,
        "avg_loss": losses["profit"].mean() if len(losses) else 0,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else math.inf,
        "expectancy": d["profit"].mean() if len(d) else 0,
    }


mt5_stats = summarize_mt5_deals(mt5_deals)
balance = as_float(mt5_status.get("balance"))
equity = as_float(mt5_status.get("equity"))
floating_pnl = equity - balance

st.markdown(
    f"""
    <div class='hero'>
      <div>{live_badge(mt5_live)}</div>
      <div class='hero-title'>Live MT5 Equity</div>
      <div class='hero-value'>{fmt_money(equity)}</div>
      <div class='hero-sub'>Balance {fmt_money(balance)} · Floating PnL {fmt_money(floating_pnl)} · Updated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("### Runtime")
    st.metric("Live Equity", fmt_money(equity))
    st.metric("Balance", fmt_money(balance))
    st.metric("Floating PnL", fmt_money(floating_pnl))
    st.caption(f"Heartbeat: {mt5_age:.0f}s ago" if mt5_age is not None else "Heartbeat: unavailable")
    if st.button("Refresh now", width="stretch"):
        st.rerun()
    if AUTO_REFRESH:
        st.caption(f"Auto-refresh every {REFRESH_SECONDS}s.")
        st.markdown(f"<meta http-equiv='refresh' content='{REFRESH_SECONDS}'>", unsafe_allow_html=True)

command_tab, signals_tab, mt5_tab, logs_tab = st.tabs(["Overview", "Signals", "Trades", "Unified Log"])

with command_tab:
    st.subheader("Live command view")
    mm = mt5_status.get("money_management", {}) if isinstance(mt5_status, dict) else {}
    exposure = open_exposure_summary(mt5_positions)
    today_pnl = as_float(mm.get("today_closed_pnl"))

    s1, s2, s3, s4 = st.columns(4)
    s1.markdown(html_card("Trading mode", "Maximum frequency", "5 positions/symbol · 60s cooldown · SMC score >= 1", "warn"), unsafe_allow_html=True)
    s2.markdown(html_card("Risk per trade", fmt_rate(mm.get("daily_risk_per_trade_pct", 0)), "Risk lot sizing is enabled" if mm.get("risk_lot_sizing") else "Risk lot sizing disabled", "ok" if mm.get("risk_lot_sizing") else "bad"), unsafe_allow_html=True)
    s3.markdown(html_card("Today closed PnL", fmt_money(today_pnl), f"Daily loss limit {fmt_rate(mm.get('daily_loss_limit_pct', 0))}", pnl_class(today_pnl)), unsafe_allow_html=True)
    s4.markdown(html_card("Last EA signal", str(mt5_status.get("last_auto_signal", "—")), f"Heartbeat {mt5_age:.0f}s" if mt5_age is not None else "Heartbeat unavailable", "ok" if mt5_live else "warn"), unsafe_allow_html=True)

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Equity", fmt_money(equity), delta=fmt_money(floating_pnl))
    c2.metric("Balance", fmt_money(balance))
    c3.metric("Free Margin", fmt_money(mt5_status.get("free_margin")))
    c4.metric("Open Positions", int(mt5_status.get("positions") or 0))
    c5.metric("Closed Deals", int(mt5_stats.get("deals", 0)))
    pf = mt5_stats.get("profit_factor", 0)
    c6.metric("Profit Factor", "∞" if pf == math.inf else f"{pf:.2f}")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Net Closed PnL", fmt_money(mt5_stats.get("total_pnl", 0)))
    m2.metric("Win Rate", f"{mt5_stats.get('win_rate', 0):.1f}%")
    m3.metric("EA Heartbeat", f"{mt5_age:.0f}s" if mt5_age is not None else "—")
    m4.metric("MT5 Login", str(mt5_status.get("login") or "—"))

    if mt5_age is not None:
        st.markdown(f"<div class='small-muted'>MT5 heartbeat: {mt5_age:.0f}s ago · {mt5_status_path}</div>", unsafe_allow_html=True)

    st.markdown("### Symbol status")
    symbols = mt5_status.get("symbols") if isinstance(mt5_status, dict) else []
    if isinstance(symbols, list) and symbols:
        cols = st.columns(len(symbols))
        for col, item in zip(cols, symbols):
            positions_used = int(item.get("auto_positions") or 0)
            status_class = "bad" if "max" in str(item.get("last_signal", "")).lower() else ("warn" if "cooldown" in str(item.get("last_signal", "")).lower() else "ok")
            col.markdown(
                html_card(
                    str(item.get("symbol", "—")),
                    str(item.get("last_signal", "—")),
                    f"{positions_used}/5 positions · spread {fmt_number(item.get('spread_points'), 1)} pts · bid {item.get('bid', '—')} / ask {item.get('ask', '—')}",
                    status_class,
                ),
                unsafe_allow_html=True,
            )

    if not exposure.empty:
        st.markdown("### Open exposure by symbol")
        st.dataframe(style_money_dataframe(exposure), width="stretch", hide_index=True)

    left, right = st.columns([1.2, 1])
    with left:
        st.subheader("Open managed positions")
        if not mt5_positions.empty:
            cols = [c for c in ["time", "symbol", "type", "volume", "open_price", "current_price", "profit", "sl", "tp", "strategy"] if c in mt5_positions]
            st.dataframe(style_money_dataframe(mt5_positions[cols]), width="stretch", hide_index=True)
        else:
            st.info("No managed MT5 positions exported yet.")

        st.subheader("Closed MT5 PnL")
        if not mt5_deals.empty and "profit" in mt5_deals:
            chart_df = mt5_deals.copy()
            if "entry" in chart_df:
                chart_df = chart_df[pd.to_numeric(chart_df["entry"], errors="coerce").fillna(0).astype(int) == 1]
            chart_df["profit"] = pd.to_numeric(chart_df["profit"], errors="coerce").fillna(0)
            if "time" in chart_df:
                chart_df["time"] = pd.to_datetime(chart_df["time"], errors="coerce")
                chart_df = chart_df.sort_values("time")
            chart_df["cum_pnl"] = chart_df["profit"].cumsum()
            fig = go.Figure()
            if "time" in chart_df:
                fig.add_trace(go.Scatter(x=chart_df["time"], y=chart_df["cum_pnl"], mode="lines+markers", name="Cumulative PnL"))
            else:
                fig.add_trace(go.Scatter(x=chart_df.index, y=chart_df["cum_pnl"], mode="lines+markers", name="Cumulative PnL"))
            fig.update_layout(height=360, margin=dict(l=10, r=10, t=20, b=10), yaxis_title="Account currency")
            st.plotly_chart(fig, width="stretch")
        else:
            st.info("No closed MT5 deal data yet.")

    with right:
        st.subheader("Recent EA fills / decisions")
        recent_fills = recent_fills_table(mt5_acks, 12)
        if not recent_fills.empty:
            st.dataframe(recent_fills, width="stretch", hide_index=True)
        else:
            st.info("No acknowledgements exported yet.")

        st.subheader("Risk state")
        risk_rows = [
            {"field": "Daily equity snapshot", "value": fmt_money(mm.get("daily_equity_snapshot"))},
            {"field": "Today closed PnL", "value": fmt_money(mm.get("today_closed_pnl"))},
            {"field": "Risk per trade", "value": fmt_rate(mm.get("daily_risk_per_trade_pct"))},
            {"field": "Daily loss limit", "value": fmt_rate(mm.get("daily_loss_limit_pct"))},
            {"field": "Loss limit reached", "value": "Yes" if mm.get("loss_limit_reached") else "No"},
            {"field": "Auto-close no SL/TP", "value": "On" if mm.get("auto_close_no_sl_tp") else "Off"},
        ]
        st.dataframe(pd.DataFrame(risk_rows), width="stretch", hide_index=True)

        if not journal.empty:
            st.subheader("Improver Decisions")
            j = journal.tail(8).copy()
            cols = [c for c in ["ts", "event", "applied", "decision", "reason", "error", "skipped"] if c in j.columns]
            if "ts" in j:
                j["time"] = ts_to_dt(j["ts"])
                cols = ["time"] + [c for c in cols if c != "ts"]
            st.dataframe(j[cols].astype(str), width="stretch", hide_index=True)

    st.markdown("### Verified Performance")
    st.link_button("Open Myfxbook", MYFXBOOK_URL)

with signals_tab:
    st.subheader("Signal Decisions")
    st.caption("Read-only MT5 XAUUSD/BTCUSD decisions, acknowledgements, and unified runtime log events.")

    st.markdown("### Current symbol signals")
    st.dataframe(latest_signal_snapshot(mt5_status, mt5_age, {}, overrides), width="stretch", hide_index=True)

    symbols = mt5_status.get("symbols") if isinstance(mt5_status, dict) else []
    if isinstance(symbols, list) and symbols:
        symbol_df = pd.DataFrame(symbols)
        st.markdown("### Live bid/ask and position limits")
        st.dataframe(symbol_df, width="stretch", hide_index=True)

    if not mt5_acks.empty:
        st.markdown("#### MT5 EA acknowledgements / trade decisions")
        st.dataframe(recent_fills_table(mt5_acks, 40), width="stretch", hide_index=True)
    else:
        st.info("No MT5 acknowledgement rows yet. The live EA signal is shown above from the heartbeat.")

    st.markdown("### Unified runtime decisions")
    combined_decisions = extract_decision_lines(recent_log(LOG_DIR / "combined.log", 800), "combined", 160)
    if not combined_decisions.empty:
        st.dataframe(combined_decisions.tail(160), width="stretch", hide_index=True)
    else:
        st.info("No decision lines found in combined.log yet.")

    st.markdown("### MT5 journal decision lines")
    journal_path = latest_mt5_journal()
    mt5_decisions = extract_decision_lines(recent_utf16_log(journal_path, 300), "mt5-journal", 100) if journal_path else pd.DataFrame()
    if not mt5_decisions.empty:
        st.dataframe(mt5_decisions, width="stretch", hide_index=True)
    else:
        st.info("No MT5 journal decision lines found yet.")


with mt5_tab:
    st.subheader("MT5 Demo - XAUUSD / BTCUSD")
    a, b, c, d, e, f = st.columns(6)
    a.metric("Balance", fmt_money(mt5_status.get("balance")))
    b.metric("Equity", fmt_money(mt5_status.get("equity")))
    c.metric("Margin", fmt_money(mt5_status.get("margin")))
    d.metric("Free Margin", fmt_money(mt5_status.get("free_margin")))
    e.metric("Positions", int(mt5_status.get("positions") or 0))
    f.metric("Heartbeat", f"{mt5_age:.0f}s" if mt5_age is not None else "—")

    st.markdown("#### Account / EA status")
    status_rows = [
        {"field": "Login", "value": mt5_status.get("login", "—")},
        {"field": "Server", "value": mt5_status.get("server", "—")},
        {"field": "Terminal trading", "value": bool_badge(mt5_status.get("trade_allowed_terminal"))},
        {"field": "EA trading", "value": bool_badge(mt5_status.get("trade_allowed_ea"))},
        {"field": "Last command id", "value": mt5_status.get("last_command_id", "—")},
        {"field": "Heartbeat file", "value": str(mt5_status_path or "not found")},
    ]
    st.dataframe(pd.DataFrame(status_rows).astype(str), width="stretch", hide_index=True)

    st.markdown("#### Closed performance by symbol")
    by_symbol = closed_summary_by(mt5_deals, "symbol")
    if not by_symbol.empty:
        st.dataframe(style_money_dataframe(by_symbol), width="stretch", hide_index=True)

    st.markdown("#### Closed performance by strategy")
    by_strategy = closed_summary_by(mt5_deals, "strategy")
    if not by_strategy.empty:
        st.dataframe(style_money_dataframe(by_strategy.head(25)), width="stretch", hide_index=True)

    col_a, col_b = st.columns([1, 1])
    with col_a:
        pos_df = mt5_positions
        st.markdown("#### Open managed positions")
        if not pos_df.empty:
            cols = [c for c in ["time", "symbol", "type", "volume", "open_price", "current_price", "profit", "sl", "tp", "strategy"] if c in pos_df]
            st.dataframe(style_money_dataframe(pos_df[cols]), width="stretch", hide_index=True)
        else:
            st.info("No managed MT5 positions exported yet.")
        deals_df = mt5_deals
        st.markdown("#### Recent managed deals")
        if not deals_df.empty:
            cols = [c for c in ["time", "symbol", "type", "volume", "price", "profit", "strategy"] if c in deals_df]
            st.dataframe(style_money_dataframe(deals_df.tail(80)[cols]), width="stretch", hide_index=True)
        else:
            st.info("No managed MT5 deals exported yet.")
    with col_b:
        st.markdown("#### Raw heartbeat")
        st.json(mt5_status or {"status": "not found"})
        st.markdown("#### Myfxbook")
        st.write("Full verified trading analytics live on Myfxbook.")
        st.link_button("Open Myfxbook report", MYFXBOOK_URL)

    journal_path = latest_mt5_journal()
    with st.expander("Latest MT5 journal", expanded=False):
        st.code(recent_utf16_log(journal_path, 160) if journal_path else "No MT5 journal found.", language="text")

with logs_tab:
    st.subheader("Unified Runtime Log")
    st.caption("All active PM2 service output goes to logs/combined.log. This view is read-only.")
    p1, p2 = st.columns(2)
    with p1:
        st.markdown("#### PM2")
        st.code(shell_lines(["bash", "-lc", "runuser -l openclaw -c 'pm2 list'"], 80), language="text")
    with p2:
        st.markdown("#### MT5 processes")
        st.code(shell_lines(["bash", "-lc", "pgrep -af 'terminal64.exe|xvfb|wineserver|start_mt5' || true"], 80), language="text")

    st.code(recent_log(LOG_DIR / "combined.log", 320), language="text")

    journal_path = latest_mt5_journal()
    st.markdown("#### MT5 journal")
    st.code(recent_utf16_log(journal_path, 220) if journal_path else "No MT5 journal found.", language="text")
