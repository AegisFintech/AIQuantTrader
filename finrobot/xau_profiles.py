"""Runtime XAUUSD strategy profiles shared by research and the MT5 bridge."""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any


PROFILE_FILENAME = "finrobot_strategy_profile.csv"


@dataclass(frozen=True)
class XauStrategyProfile:
    """Bounded runtime profile for MT5 XAUUSD auto-trading."""

    profile_name: str
    risk_tier: int
    auto_timeframe: str = "M5"
    enable_xau_atr_impulse: bool = True
    enable_xau_rsi_reversion: bool = False
    impulse_atr_multiplier: float = 0.12
    enable_smart_money_gates: bool = True
    enable_adx_regime_filter: bool = True
    min_smc_confluence_score_xauusd: int = 4
    pda_long_ceiling: float = 0.40
    pda_short_floor: float = 0.60
    discount_threshold: float = 0.38
    premium_threshold: float = 0.62
    fvg_min_atr_multiplier: float = 0.30
    liquidity_sweep_atr_multiplier: float = 0.30
    daily_risk_per_trade_fraction: float = 0.0100
    daily_loss_limit_fraction: float = 0.0100
    max_lot_per_trade_xauusd: float = 50.0
    max_auto_positions_xauusd: int = 2
    max_same_direction_positions_per_symbol: int = 2
    min_seconds_between_trades_xauusd: int = 180
    stop_atr_multiplier: float = 1.20
    take_profit_atr_multiplier: float = 2.40
    adx_min_threshold: float = 20.0
    high_confluence_lot_multiplier: float = 3.0
    high_confluence_score: int = 5
    max_spread_points_xauusd: float = 80.0
    loss_streak_pause_count: int = 0
    bad_day_downshift_fraction: float = 0.50
    max_recent_drawdown_fraction: float = 0.0
    blackout_enabled: bool = False
    max_atr_regime_multiplier: float = 0.0

    def bounded(self) -> "XauStrategyProfile":
        """Return a profile clamped to owner-approved demo XAUUSD bounds."""

        return replace(
            self,
            risk_tier=_clamp_int(self.risk_tier, 0, 2),
            min_smc_confluence_score_xauusd=_clamp_int(
                self.min_smc_confluence_score_xauusd,
                1,
                6,
            ),
            pda_long_ceiling=_clamp_float(self.pda_long_ceiling, 0.05, 0.50),
            pda_short_floor=_clamp_float(self.pda_short_floor, 0.50, 0.95),
            impulse_atr_multiplier=_clamp_float(
                self.impulse_atr_multiplier,
                0.04,
                0.30,
            ),
            discount_threshold=_clamp_float(self.discount_threshold, 0.10, 0.50),
            premium_threshold=_clamp_float(self.premium_threshold, 0.50, 0.90),
            fvg_min_atr_multiplier=_clamp_float(
                self.fvg_min_atr_multiplier,
                0.05,
                1.50,
            ),
            liquidity_sweep_atr_multiplier=_clamp_float(
                self.liquidity_sweep_atr_multiplier,
                0.05,
                1.50,
            ),
            daily_risk_per_trade_fraction=_clamp_float(
                self.daily_risk_per_trade_fraction,
                0.0001,
                0.0100,
            ),
            daily_loss_limit_fraction=_clamp_float(
                self.daily_loss_limit_fraction,
                0.0025,
                0.0500,
            ),
            max_lot_per_trade_xauusd=_clamp_float(
                self.max_lot_per_trade_xauusd,
                0.01,
                50.0,
            ),
            max_auto_positions_xauusd=_clamp_int(
                self.max_auto_positions_xauusd,
                1,
                4,
            ),
            max_same_direction_positions_per_symbol=_clamp_int(
                self.max_same_direction_positions_per_symbol,
                1,
                4,
            ),
            min_seconds_between_trades_xauusd=_clamp_int(
                self.min_seconds_between_trades_xauusd,
                30,
                900,
            ),
            stop_atr_multiplier=_clamp_float(self.stop_atr_multiplier, 0.50, 3.00),
            take_profit_atr_multiplier=_clamp_float(
                self.take_profit_atr_multiplier,
                0.80,
                6.00,
            ),
            adx_min_threshold=_clamp_float(self.adx_min_threshold, 5.0, 45.0),
            high_confluence_lot_multiplier=_clamp_float(
                self.high_confluence_lot_multiplier,
                1.0,
                5.0,
            ),
            high_confluence_score=_clamp_int(self.high_confluence_score, 4, 6),
            max_spread_points_xauusd=_clamp_float(
                self.max_spread_points_xauusd,
                20.0,
                120.0,
            ),
            loss_streak_pause_count=_clamp_int(
                self.loss_streak_pause_count,
                0,
                8,
            ),
            bad_day_downshift_fraction=_clamp_float(
                self.bad_day_downshift_fraction,
                0.0,
                1.0,
            ),
            max_recent_drawdown_fraction=_clamp_float(
                self.max_recent_drawdown_fraction,
                0.0,
                0.0500,
            ),
            max_atr_regime_multiplier=_clamp_float(
                self.max_atr_regime_multiplier,
                0.0,
                8.0,
            ),
        )

    def to_rows(self) -> list[tuple[str, str]]:
        """Return stable key/value rows for the MT5 Common Files profile."""

        payload = asdict(self.bounded())
        return [(key, _profile_value(value)) for key, value in payload.items()]

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe bounded profile payload."""

        return asdict(self.bounded())


DEFAULT_PROFILE = XauStrategyProfile(profile_name="incumbent_smc4", risk_tier=0)


PROFILE_CANDIDATES: tuple[XauStrategyProfile, ...] = (
    DEFAULT_PROFILE,
    XauStrategyProfile(
        profile_name="attack_atr_m1",
        risk_tier=1,
        auto_timeframe="M5",
        min_smc_confluence_score_xauusd=3,
        impulse_atr_multiplier=0.10,
        pda_long_ceiling=0.42,
        pda_short_floor=0.58,
        daily_risk_per_trade_fraction=0.0030,
        daily_loss_limit_fraction=0.0300,
        max_lot_per_trade_xauusd=7.5,
        max_auto_positions_xauusd=3,
        max_same_direction_positions_per_symbol=2,
        min_seconds_between_trades_xauusd=90,
        stop_atr_multiplier=1.00,
        take_profit_atr_multiplier=2.00,
        adx_min_threshold=18.0,
        high_confluence_lot_multiplier=3.0,
        loss_streak_pause_count=3,
        max_recent_drawdown_fraction=0.0150,
        max_atr_regime_multiplier=3.0,
    ),
    XauStrategyProfile(
        profile_name="sweep_reversal",
        risk_tier=1,
        min_smc_confluence_score_xauusd=4,
        impulse_atr_multiplier=0.16,
        pda_long_ceiling=0.34,
        pda_short_floor=0.66,
        daily_risk_per_trade_fraction=0.0030,
        daily_loss_limit_fraction=0.0300,
        max_lot_per_trade_xauusd=7.5,
        max_auto_positions_xauusd=3,
        min_seconds_between_trades_xauusd=180,
        stop_atr_multiplier=0.90,
        take_profit_atr_multiplier=2.70,
        adx_min_threshold=16.0,
        loss_streak_pause_count=2,
        max_recent_drawdown_fraction=0.0125,
        max_atr_regime_multiplier=2.75,
    ),
    XauStrategyProfile(
        profile_name="breakout_continuation",
        risk_tier=2,
        min_smc_confluence_score_xauusd=3,
        impulse_atr_multiplier=0.08,
        pda_long_ceiling=0.48,
        pda_short_floor=0.52,
        daily_risk_per_trade_fraction=0.0050,
        daily_loss_limit_fraction=0.0500,
        max_lot_per_trade_xauusd=10.0,
        max_auto_positions_xauusd=4,
        max_same_direction_positions_per_symbol=3,
        min_seconds_between_trades_xauusd=60,
        stop_atr_multiplier=1.30,
        take_profit_atr_multiplier=1.80,
        adx_min_threshold=24.0,
        high_confluence_lot_multiplier=4.0,
        loss_streak_pause_count=3,
        bad_day_downshift_fraction=0.35,
        max_recent_drawdown_fraction=0.0200,
        max_atr_regime_multiplier=3.25,
    ),
    XauStrategyProfile(
        profile_name="sniper_smc5",
        risk_tier=1,
        min_smc_confluence_score_xauusd=5,
        impulse_atr_multiplier=0.18,
        pda_long_ceiling=0.30,
        pda_short_floor=0.70,
        daily_risk_per_trade_fraction=0.0030,
        daily_loss_limit_fraction=0.0300,
        max_lot_per_trade_xauusd=7.5,
        max_auto_positions_xauusd=2,
        max_same_direction_positions_per_symbol=1,
        min_seconds_between_trades_xauusd=240,
        stop_atr_multiplier=1.10,
        take_profit_atr_multiplier=3.20,
        adx_min_threshold=20.0,
        high_confluence_lot_multiplier=4.0,
        loss_streak_pause_count=2,
        max_recent_drawdown_fraction=0.0100,
        blackout_enabled=True,
        max_atr_regime_multiplier=2.50,
    ),
)


def profile_by_name(name: str) -> XauStrategyProfile:
    """Return a configured profile by name."""

    wanted = str(name or "").strip()
    for profile in PROFILE_CANDIDATES:
        if profile.profile_name == wanted:
            return profile
    raise KeyError(f"unknown XAU profile: {name}")


def write_profile_csv(profile: XauStrategyProfile, path: Path) -> Path:
    """Write a profile to Common Files-compatible CSV."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["key", "value"])
        writer.writerows(profile.to_rows())
    return path


def read_profile_csv(path: Path) -> dict[str, str]:
    """Read a profile CSV as plain key/value strings."""

    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = csv.DictReader(handle)
        return {
            str(row.get("key") or "").strip(): str(row.get("value") or "").strip()
            for row in rows
            if str(row.get("key") or "").strip()
        }


def _profile_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.8f}".rstrip("0").rstrip(".")
    return str(value)


def _clamp_float(value: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(value)))


def _clamp_int(value: int, lo: int, hi: int) -> int:
    return max(int(lo), min(int(hi), int(value)))
