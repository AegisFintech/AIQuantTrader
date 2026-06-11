"""XAU feature helpers mirroring the MT5 bridge EA signal inputs."""

from __future__ import annotations

from typing import Any


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


def _positive_period(period: int) -> int:
    period = int(period)
    if period <= 0:
        raise ValueError("period must be positive")
    return period
