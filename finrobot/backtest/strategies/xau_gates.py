"""XAU feature helpers mirroring the MT5 bridge EA signal inputs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class XauGateParams:
    """XAU SMC/PDA gate inputs from the MT5 bridge EA defaults."""

    smc_lookback: int = 48
    discount_threshold: float = 0.38
    premium_threshold: float = 0.62
    fvg_min_atr_mult: float = 0.15
    liquidity_sweep_atr_mult: float = 0.10


def ema(values: list[float], period: int) -> list[float | None]:
    """Return standard EMA values with ``None`` during SMA warmup."""

    period = _positive_period(period)
    prices = [float(value) for value in values]
    out: list[float | None] = [None] * len(prices)
    if len(prices) < period:
        return out

    alpha = 2.0 / (period + 1.0)
    current = sum(prices[:period]) / period
    out[period - 1] = current
    for idx in range(period, len(prices)):
        current = current + (prices[idx] - current) * alpha
        out[idx] = current
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    """Return Wilder RSI values with the first ``period`` bars as ``None``."""

    period = _positive_period(period)
    prices = [float(value) for value in values]
    out: list[float | None] = [None] * len(prices)
    if len(prices) <= period:
        return out

    gains = 0.0
    losses = 0.0
    for idx in range(1, period + 1):
        change = prices[idx] - prices[idx - 1]
        if change >= 0:
            gains += change
        else:
            losses -= change

    avg_gain = gains / period
    avg_loss = losses / period
    out[period] = _rsi_value(avg_gain, avg_loss)

    for idx in range(period + 1, len(prices)):
        change = prices[idx] - prices[idx - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
        out[idx] = _rsi_value(avg_gain, avg_loss)
    return out


def macd(
    values: list[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Return MACD main, signal, and histogram series."""

    fast_values = ema(values, fast)
    slow_values = ema(values, slow)
    macd_line: list[float | None] = []
    for fast_value, slow_value in zip(fast_values, slow_values):
        if fast_value is None or slow_value is None:
            macd_line.append(None)
        else:
            macd_line.append(fast_value - slow_value)

    signal_line = _ema_valid_values(macd_line, signal_period)
    histogram: list[float | None] = []
    for main, signal in zip(macd_line, signal_line):
        if main is None or signal is None:
            histogram.append(None)
        else:
            histogram.append(main - signal)
    return macd_line, signal_line, histogram


def atr_series(bars: list[dict], period: int = 14) -> list[float | None]:
    """Return True Range ATR via Wilder/MT5-style EWM alpha=1/period."""

    period = _positive_period(period)
    out: list[float | None] = [None] * len(bars)
    if len(bars) <= period:
        return out

    true_ranges = [_true_range(bars, idx) for idx in range(len(bars))]
    current = sum(true_ranges[1 : period + 1]) / period
    out[period] = current
    for idx in range(period + 1, len(bars)):
        current = current + (true_ranges[idx] - current) / period
        out[idx] = current
    return out


def compute_xau_features(
    bars: list[dict],
    *,
    fast: int = 9,
    slow: int = 21,
    trend: int = 50,
    rsi_period: int = 14,
    atr_period: int = 14,
) -> list[dict]:
    """Compute the M2.3a XAU feature set for each bar.

    The indicator reads mirror MQL5 lines 751-777 of
    ``FinRobotBridgeEA.mq5``. The quick momentum booleans mirror lines
    799-808, after the XAU weak-signal filter in lines 832-835 leaves
    ``quickMomentumLong`` and ``quickMomentumShort`` intact.
    """

    closes = [_bar_float(bar, "close") for bar in bars]
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    ema_trend = ema(closes, trend)
    rsi_values = rsi(closes, rsi_period)
    macd_main, macd_signal, macd_hist = macd(closes)
    atr_values = atr_series(bars, atr_period)

    features: list[dict] = []
    for idx, close in enumerate(closes):
        previous = closes[idx - 1] if idx >= 1 else None
        momentum3 = None
        if idx >= 3 and closes[idx - 3] != 0:
            momentum3 = (close - closes[idx - 3]) / closes[idx - 3]

        if idx < 3:
            features.append(
                {
                    "ema_fast": None,
                    "ema_slow": None,
                    "ema_trend": None,
                    "rsi": None,
                    "macd_main": None,
                    "macd_signal": None,
                    "macd_hist": None,
                    "atr": None,
                    "current": close,
                    "previous": previous,
                    "momentum3": None,
                    "bullish_cross": False,
                    "bearish_cross": False,
                    "quick_momentum_long": False,
                    "quick_momentum_short": False,
                }
            )
            continue

        bullish_cross = _bullish_cross(
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            idx=idx,
        )
        bearish_cross = _bearish_cross(
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            idx=idx,
        )
        quick_long = _quick_momentum_long(
            current=close,
            previous=previous,
            ema_fast_current=ema_fast[idx],
            ema_fast_previous=ema_fast[idx - 1],
            ema_slow_current=ema_slow[idx],
            rsi_current=rsi_values[idx],
        )
        quick_short = _quick_momentum_short(
            current=close,
            previous=previous,
            ema_fast_current=ema_fast[idx],
            ema_fast_previous=ema_fast[idx - 1],
            ema_slow_current=ema_slow[idx],
            rsi_current=rsi_values[idx],
        )

        features.append(
            {
                "ema_fast": ema_fast[idx],
                "ema_slow": ema_slow[idx],
                "ema_trend": ema_trend[idx],
                "rsi": rsi_values[idx],
                "macd_main": macd_main[idx],
                "macd_signal": macd_signal[idx],
                "macd_hist": macd_hist[idx],
                "atr": atr_values[idx],
                "current": close,
                "previous": previous,
                "momentum3": momentum3,
                "bullish_cross": bullish_cross,
                "bearish_cross": bearish_cross,
                "quick_momentum_long": quick_long,
                "quick_momentum_short": quick_short,
            }
        )
    return features


def range_high(bars: list[dict], count: int) -> float:
    """Mirror MQL5 RangeHigh in SmartMoney.mqh lines 9-18.

    ``bars`` is oldest first, most recent last. MQL5 uses series arrays where
    rates[0] is current, so this scans Python shifts -2, -3, ... and excludes
    the current bar exactly like the EA.
    """

    n = min(int(count), max(0, len(bars) - 1))
    if n < 1:
        return 0.0
    high = _bar_float(_series_bar(bars, 1), "high")
    for shift in range(2, n + 1):
        high = max(high, _bar_float(_series_bar(bars, shift), "high"))
    return high


def range_low(bars: list[dict], count: int) -> float:
    """Mirror MQL5 RangeLow in SmartMoney.mqh lines 20-29."""

    n = min(int(count), max(0, len(bars) - 1))
    if n < 1:
        return 0.0
    low = _bar_float(_series_bar(bars, 1), "low")
    for shift in range(2, n + 1):
        low = min(low, _bar_float(_series_bar(bars, shift), "low"))
    return low


def pda(bars: list[dict], lookback: int, price: float) -> float:
    """Return premium/discount position in [0, 1].

    Mirrors MQL5 PremiumDiscountPosition in SmartMoney.mqh lines 31-36.
    ``bars`` is oldest first, most recent last. MQL5 uses ArraySetAsSeries, so
    rates[0]=current, rates[1]=previous, rates[2]=two back; the Python port
    uses -1=current, -2=previous, -3=two back.
    """

    high = range_high(bars, lookback)
    low = range_low(bars, lookback)
    if high <= low:
        return 0.5
    return _clamp((float(price) - low) / (high - low), 0.0, 1.0)


def has_bullish_fvg(
    bars: list[dict],
    count: int,
    atr_value: float,
    price: float,
    fvg_min_atr_multiplier: float,
) -> bool:
    """Mirror MQL5 HasBullishFvg in SmartMoney.mqh lines 38-48."""

    n = min(int(count), max(0, len(bars) - 3))
    min_gap = max(float(atr_value) * float(fvg_min_atr_multiplier), 0.0)
    for shift in range(1, n + 1):
        gap_low = _bar_float(_series_bar(bars, shift + 2), "high")
        gap_high = _bar_float(_series_bar(bars, shift), "low")
        if (
            gap_high > gap_low
            and gap_high - gap_low >= min_gap
            and float(price) >= gap_low
            and float(price) <= gap_high + float(atr_value) * 0.5
        ):
            return True
    return False


def has_bearish_fvg(
    bars: list[dict],
    count: int,
    atr_value: float,
    price: float,
    fvg_min_atr_multiplier: float,
) -> bool:
    """Mirror MQL5 HasBearishFvg in SmartMoney.mqh lines 50-60."""

    n = min(int(count), max(0, len(bars) - 3))
    min_gap = max(float(atr_value) * float(fvg_min_atr_multiplier), 0.0)
    for shift in range(1, n + 1):
        gap_low = _bar_float(_series_bar(bars, shift), "high")
        gap_high = _bar_float(_series_bar(bars, shift + 2), "low")
        if (
            gap_high > gap_low
            and gap_high - gap_low >= min_gap
            and float(price) <= gap_high
            and float(price) >= gap_low - float(atr_value) * 0.5
        ):
            return True
    return False


def last_bullish_order_block_high(bars: list[dict], count: int) -> float:
    """Mirror MQL5 LastBullishOrderBlockHigh in SmartMoney.mqh lines 62-69."""

    n = min(int(count), max(0, len(bars) - 2))
    for shift in range(2, n + 1):
        candidate = _series_bar(bars, shift)
        newer = _series_bar(bars, shift - 1)
        if (
            _bar_float(candidate, "close") < _bar_float(candidate, "open")
            and _bar_float(newer, "close") > _bar_float(candidate, "high")
        ):
            return _bar_float(candidate, "high")
    return 0.0


def last_bearish_order_block_low(bars: list[dict], count: int) -> float:
    """Mirror MQL5 LastBearishOrderBlockLow in SmartMoney.mqh lines 71-78."""

    n = min(int(count), max(0, len(bars) - 2))
    for shift in range(2, n + 1):
        candidate = _series_bar(bars, shift)
        newer = _series_bar(bars, shift - 1)
        if (
            _bar_float(candidate, "close") > _bar_float(candidate, "open")
            and _bar_float(newer, "close") < _bar_float(candidate, "low")
        ):
            return _bar_float(candidate, "low")
    return 0.0


def bullish_liquidity_sweep(
    bars: list[dict],
    count: int,
    atr_value: float,
    liquidity_sweep_atr_multiplier: float,
) -> bool:
    """Mirror MQL5 BullishLiquiditySweep in SmartMoney.mqh lines 80-89."""

    n = min(int(count), max(0, len(bars) - 2))
    if n < 5:
        return False
    prior_low = _bar_float(_series_bar(bars, 2), "low")
    for shift in range(3, n + 1):
        prior_low = min(prior_low, _bar_float(_series_bar(bars, shift), "low"))
    previous = _series_bar(bars, 1)
    return bool(
        _bar_float(previous, "low")
        < prior_low - float(atr_value) * float(liquidity_sweep_atr_multiplier)
        and _bar_float(previous, "close") > prior_low
    )


def bearish_liquidity_sweep(
    bars: list[dict],
    count: int,
    atr_value: float,
    liquidity_sweep_atr_multiplier: float,
) -> bool:
    """Mirror MQL5 BearishLiquiditySweep in SmartMoney.mqh lines 91-100."""

    n = min(int(count), max(0, len(bars) - 2))
    if n < 5:
        return False
    prior_high = _bar_float(_series_bar(bars, 2), "high")
    for shift in range(3, n + 1):
        prior_high = max(prior_high, _bar_float(_series_bar(bars, shift), "high"))
    previous = _series_bar(bars, 1)
    return bool(
        _bar_float(previous, "high")
        > prior_high + float(atr_value) * float(liquidity_sweep_atr_multiplier)
        and _bar_float(previous, "close") < prior_high
    )


def bullish_structure_shift(bars: list[dict], count: int) -> bool:
    """Mirror MQL5 BullishStructureShift in SmartMoney.mqh lines 102-111."""

    n = min(int(count), max(0, len(bars) - 3))
    if n < 6:
        return False
    prior_high = _bar_float(_series_bar(bars, 2), "high")
    for shift in range(3, n + 1):
        prior_high = max(prior_high, _bar_float(_series_bar(bars, shift), "high"))
    return bool(_bar_float(_series_bar(bars, 1), "close") > prior_high)


def bearish_structure_shift(bars: list[dict], count: int) -> bool:
    """Mirror MQL5 BearishStructureShift in SmartMoney.mqh lines 113-122."""

    n = min(int(count), max(0, len(bars) - 3))
    if n < 6:
        return False
    prior_low = _bar_float(_series_bar(bars, 2), "low")
    for shift in range(3, n + 1):
        prior_low = min(prior_low, _bar_float(_series_bar(bars, shift), "low"))
    return bool(_bar_float(_series_bar(bars, 1), "close") < prior_low)


def smc_long_components(
    bars: list[dict],
    atr_value: float,
    price: float,
    gate_params: XauGateParams | None = None,
) -> dict:
    """Mirror MQL5 SmartMoneyLongScore in SmartMoney.mqh lines 124-141."""

    params = gate_params or XauGateParams()
    pda_value = pda(bars, params.smc_lookback, price)
    order_block_high = last_bullish_order_block_high(bars, params.smc_lookback)
    components = {
        "pda": pda_value,
        "discount": pda_value <= params.discount_threshold,
        "deep_discount": pda_value
        <= max(0.18, params.discount_threshold - 0.12),
        "has_fvg": has_bullish_fvg(
            bars,
            params.smc_lookback,
            atr_value,
            price,
            params.fvg_min_atr_mult,
        ),
        "reclaimed_order_block": order_block_high > 0.0
        and float(price) >= order_block_high
        and pda_value <= 0.50,
        "sweep": bullish_liquidity_sweep(
            bars,
            params.smc_lookback,
            atr_value,
            params.liquidity_sweep_atr_mult,
        ),
        "structure": bullish_structure_shift(
            bars,
            min(params.smc_lookback, 20),
        ),
    }
    components["total"] = _component_total(components)
    return components


def smc_long_score(
    bars: list[dict],
    atr_value: float,
    price: float,
    gate_params: XauGateParams | None = None,
) -> tuple[int, dict]:
    """Mirror MQL5 SmartMoneyLongScore in SmartMoney.mqh lines 124-141."""

    components = smc_long_components(bars, atr_value, price, gate_params)
    return int(components["total"]), components


def smc_short_components(
    bars: list[dict],
    atr_value: float,
    price: float,
    gate_params: XauGateParams | None = None,
) -> dict:
    """Mirror MQL5 SmartMoneyShortScore in SmartMoney.mqh lines 143-160."""

    params = gate_params or XauGateParams()
    pda_value = pda(bars, params.smc_lookback, price)
    order_block_low = last_bearish_order_block_low(bars, params.smc_lookback)
    components = {
        "pda": pda_value,
        "premium": pda_value >= params.premium_threshold,
        "deep_premium": pda_value
        >= min(0.82, params.premium_threshold + 0.12),
        "has_fvg": has_bearish_fvg(
            bars,
            params.smc_lookback,
            atr_value,
            price,
            params.fvg_min_atr_mult,
        ),
        "rejected_order_block": order_block_low > 0.0
        and float(price) <= order_block_low
        and pda_value >= 0.50,
        "sweep": bearish_liquidity_sweep(
            bars,
            params.smc_lookback,
            atr_value,
            params.liquidity_sweep_atr_mult,
        ),
        "structure": bearish_structure_shift(
            bars,
            min(params.smc_lookback, 20),
        ),
    }
    components["total"] = _component_total(components)
    return components


def smc_short_score(
    bars: list[dict],
    atr_value: float,
    price: float,
    gate_params: XauGateParams | None = None,
) -> tuple[int, dict]:
    """Mirror MQL5 SmartMoneyShortScore in SmartMoney.mqh lines 143-160."""

    components = smc_short_components(bars, atr_value, price, gate_params)
    return int(components["total"]), components


def _quick_momentum_long(
    *,
    current: float,
    previous: float | None,
    ema_fast_current: float | None,
    ema_fast_previous: float | None,
    ema_slow_current: float | None,
    rsi_current: float | None,
) -> bool:
    if (
        previous is None
        or ema_fast_current is None
        or ema_fast_previous is None
        or ema_slow_current is None
        or rsi_current is None
    ):
        return False
    # MQL5: emaFast[0] > emaSlow[0]
    if not ema_fast_current > ema_slow_current:
        return False
    # MQL5: previous <= emaFast[1]
    if not previous <= ema_fast_previous:
        return False
    # MQL5: current > emaFast[0]
    if not current > ema_fast_current:
        return False
    # MQL5: rsi[0] >= 42 && rsi[0] < 68
    return 42.0 <= rsi_current < 68.0


def _quick_momentum_short(
    *,
    current: float,
    previous: float | None,
    ema_fast_current: float | None,
    ema_fast_previous: float | None,
    ema_slow_current: float | None,
    rsi_current: float | None,
) -> bool:
    if (
        previous is None
        or ema_fast_current is None
        or ema_fast_previous is None
        or ema_slow_current is None
        or rsi_current is None
    ):
        return False
    # MQL5: emaFast[0] < emaSlow[0]
    if not ema_fast_current < ema_slow_current:
        return False
    # MQL5: previous >= emaFast[1]
    if not previous >= ema_fast_previous:
        return False
    # MQL5: current < emaFast[0]
    if not current < ema_fast_current:
        return False
    # MQL5: rsi[0] <= 58 && rsi[0] > 32
    return 32.0 < rsi_current <= 58.0


def _bullish_cross(
    *,
    ema_fast: list[float | None],
    ema_slow: list[float | None],
    idx: int,
) -> bool:
    prev_fast = ema_fast[idx - 1]
    prev_slow = ema_slow[idx - 1]
    cur_fast = ema_fast[idx]
    cur_slow = ema_slow[idx]
    if None in (prev_fast, prev_slow, cur_fast, cur_slow):
        return False
    # MQL5: emaFast[1] <= emaSlow[1] && emaFast[0] > emaSlow[0]
    return bool(prev_fast <= prev_slow and cur_fast > cur_slow)


def _bearish_cross(
    *,
    ema_fast: list[float | None],
    ema_slow: list[float | None],
    idx: int,
) -> bool:
    prev_fast = ema_fast[idx - 1]
    prev_slow = ema_slow[idx - 1]
    cur_fast = ema_fast[idx]
    cur_slow = ema_slow[idx]
    if None in (prev_fast, prev_slow, cur_fast, cur_slow):
        return False
    # MQL5: emaFast[1] >= emaSlow[1] && emaFast[0] < emaSlow[0]
    return bool(prev_fast >= prev_slow and cur_fast < cur_slow)


def _ema_valid_values(
    values: list[float | None],
    period: int,
) -> list[float | None]:
    period = _positive_period(period)
    out: list[float | None] = [None] * len(values)
    alpha = 2.0 / (period + 1.0)
    seed: list[float] = []
    current: float | None = None
    for idx, value in enumerate(values):
        if value is None:
            continue
        if current is None:
            seed.append(value)
            if len(seed) == period:
                current = sum(seed) / period
                out[idx] = current
            continue
        current = current + (value - current) * alpha
        out[idx] = current
    return out


def _true_range(bars: list[dict], idx: int) -> float:
    high = _bar_float(bars[idx], "high")
    low = _bar_float(bars[idx], "low")
    if idx == 0:
        return high - low
    previous_close = _bar_float(bars[idx - 1], "close")
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    relative_strength = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def _bar_float(bar: dict, key: str) -> float:
    value: Any = bar[key]
    return float(value)


def _series_bar(bars: list[dict], shift: int) -> dict:
    return bars[-1 - int(shift)]


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(value)))


def _component_total(components: dict) -> int:
    return sum(
        1
        for key, value in components.items()
        if key not in {"pda", "total"} and bool(value)
    )


def _positive_period(period: int) -> int:
    period = int(period)
    if period <= 0:
        raise ValueError("period must be positive")
    return period
