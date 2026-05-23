"""
Moonshot Daemon - 24/7 Automated Trading Main Loop
Runs continuously, checks for opportunities every 60 seconds
Integrates real-time WebSocket prices with strategy engine and paper trading
"""

import sys
import os
import time
import json
import logging
import signal
import threading
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
from pathlib import Path
from collections import deque

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from moonshot.daemon.hyperliquid_ws_client import (
    HyperliquidWebSocketClient, PriceUpdate, TradeData
)
from moonshot.daemon.state_manager import StateManager, PositionState, TradeRecord
from moonshot.strategies.strategies import (
    AggressiveADXScalper,
    BreakoutHunter, MeanReversionBandit,
    SmartMoneyConcepts, FibonacciRetracement,
    VWAPStrategy, MACDStrategy,
    CrossAssetLeadLag, FundingRateContrarian, VolatilitySqueeze,
    EMARibbonPullback, VWAPReversion, MomentumExhaustion,
    QQEStrategy,
    TradingSignal, SignalType, TechnicalIndicators
)
from moonshot.strategies.executor import (
    HyperliquidPaperTrading, OrderSide, OrderType, Position
)
import pandas as pd
import numpy as np

log_dir = Path("logs")
log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler(log_dir / "daemon.log"),
        logging.StreamHandler()
    ],
    force=True,
)
logger = logging.getLogger(__name__)


class QuickMomentum:
    """Fast momentum strategy that generates signals from EMA crosses"""

    def __init__(self, ema_fast: int = 8, ema_slow: int = 21, rsi_period: int = 14,
                 rsi_ob: float = 65, rsi_os: float = 35, max_leverage: float = 20.0):
        self.name = "Quick_Momentum"
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        self.rsi_ob = rsi_ob
        self.rsi_os = rsi_os
        self.max_leverage = max_leverage
        self.indicators = TechnicalIndicators()
        self.coins_traded = set()
        self.last_signal_time = {}

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame,
                        current_price: float, account_balance: float) -> Optional[TradingSignal]:
        import time as _time
        now = _time.time()
        coin = symbol.replace("-PERP", "")
        if coin in self.last_signal_time and now - self.last_signal_time[coin] < 5:
            return None

        min_len = max(self.ema_slow, self.rsi_period) + 3
        if len(ohlcv) < min_len:
            return None

        closes = ohlcv['close']
        volumes = ohlcv.get('volume', pd.Series([1] * len(ohlcv)))

        ema_f = self.indicators.ema(closes, self.ema_fast)
        ema_s = self.indicators.ema(closes, self.ema_slow)
        rsi = self.indicators.rsi(closes, self.rsi_period)

        fast_val = ema_f.iloc[-1]
        slow_val = ema_s.iloc[-1]
        prev_fast = ema_f.iloc[-2] if len(ema_f) > 1 else fast_val
        prev_slow = ema_s.iloc[-2] if len(ema_s) > 1 else slow_val
        cur_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50

        bullish_cross = prev_fast <= prev_slow and fast_val > slow_val
        bearish_cross = prev_fast >= prev_slow and fast_val < slow_val

        price_chg = (closes.iloc[-1] - closes.iloc[-2]) / closes.iloc[-2] if len(closes) >= 2 else 0

        signal = None
        if bullish_cross and cur_rsi < self.rsi_ob:
            confidence = 0.55 + min(0.25, abs(price_chg) * 10)
            if cur_rsi < 45:
                confidence += 0.1
            sl = current_price * 0.993
            tp = current_price * 1.014
            size = (account_balance * 0.15) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.BUY,
                confidence=min(0.9, confidence), suggested_leverage=5.0,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"Mom LONG: EMA{self.ema_fast}/{self.ema_slow} cross, RSI={cur_rsi:.0f}"
            )
            self.last_signal_time[coin] = now

        elif bearish_cross and cur_rsi > self.rsi_os:
            confidence = 0.55 + min(0.25, abs(price_chg) * 10)
            if cur_rsi > 55:
                confidence += 0.1
            sl = current_price * 1.007
            tp = current_price * 0.986
            size = (account_balance * 0.15) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.SELL,
                confidence=min(0.9, confidence), suggested_leverage=5.0,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"Mom SHORT: EMA{self.ema_fast}/{self.ema_slow} cross, RSI={cur_rsi:.0f}"
            )
            self.last_signal_time[coin] = now

        return signal


class RsiDivergence:
    """RSI overbought/oversold mean reversion with trend filter"""

    def __init__(self, rsi_period: int = 14, rsi_ob: float = 72, rsi_os: float = 28,
                 max_leverage: float = 20.0):
        self.name = "RSI_Reversion"
        self.rsi_period = rsi_period
        self.rsi_ob = rsi_ob
        self.rsi_os = rsi_os
        self.max_leverage = max_leverage
        self.indicators = TechnicalIndicators()
        self.last_signal_time = {}

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame,
                        current_price: float, account_balance: float) -> Optional[TradingSignal]:
        import time as _time
        now = _time.time()
        coin = symbol.replace("-PERP", "")
        if coin in self.last_signal_time and now - self.last_signal_time[coin] < 5:
            return None

        if len(ohlcv) < max(self.rsi_period + 3, 50):
            return None

        closes = ohlcv['close']
        rsi = self.indicators.rsi(closes, self.rsi_period)
        cur_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50
        prev_rsi = rsi.iloc[-2] if len(rsi) > 1 and not pd.isna(rsi.iloc[-2]) else 50

        ema20 = closes.rolling(20).mean().iloc[-1] if len(closes) >= 20 else current_price
        ema50 = closes.rolling(50).mean().iloc[-1] if len(closes) >= 50 else current_price
        uptrend = current_price > ema20 and ema20 > ema50
        downtrend = current_price < ema20 and ema20 < ema50

        signal = None
        if cur_rsi < self.rsi_os:
            confidence = 0.55 + (self.rsi_os - cur_rsi) / 100
            if uptrend:
                confidence += 0.1
            sl = current_price * 0.993
            tp = current_price * 1.014
            size = (account_balance * 0.12) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.BUY,
                confidence=min(0.9, confidence), suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"RSI LONG: RSI={cur_rsi:.0f} oversold bounce (trend={'UP' if uptrend else 'flat'})"
            )
            self.last_signal_time[coin] = now

        elif cur_rsi > self.rsi_ob:
            if uptrend:
                return None
            confidence = 0.6 + (cur_rsi - self.rsi_ob) / 100
            if downtrend:
                confidence += 0.1
            sl = current_price * 1.007
            tp = current_price * 0.986
            size = (account_balance * 0.12) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.SELL,
                confidence=min(0.9, confidence), suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"RSI SHORT: RSI={cur_rsi:.0f} overbought reversal (trend={'DOWN' if downtrend else 'flat'})"
            )
            self.last_signal_time[coin] = now

        return signal


class MicroTrend:
    """Micro trend follower - trades on momentum with trend confirmation"""

    def __init__(self, lookback: int = 10, momentum_threshold: float = 0.002,
                 max_leverage: float = 20.0):
        self.name = "Micro_Trend"
        self.lookback = lookback
        self.momentum_threshold = momentum_threshold
        self.max_leverage = max_leverage
        self.last_signal_time = {}

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame,
                        current_price: float, account_balance: float) -> Optional[TradingSignal]:
        import time as _time
        now = _time.time()
        coin = symbol.replace("-PERP", "")
        if coin in self.last_signal_time and now - self.last_signal_time[coin] < 5:
            return None

        if len(ohlcv) < self.lookback + 2:
            return None

        closes = ohlcv['close']

        recent = closes.iloc[-self.lookback:]
        momentum = (recent.iloc[-1] - recent.iloc[0]) / recent.iloc[0]

        short_mom = (closes.iloc[-1] - closes.iloc[-3]) / closes.iloc[-3] if len(closes) >= 3 else 0

        ema20 = closes.rolling(20).mean().iloc[-1] if len(closes) >= 20 else current_price
        trending_up = current_price > ema20
        trending_down = current_price < ema20

        signal = None

        if momentum > self.momentum_threshold and short_mom > 0 and trending_up:
            confidence = 0.55 + min(0.3, abs(momentum) * 20)
            sl = current_price * 0.993
            tp = current_price * 1.014
            size = (account_balance * 0.10) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.BUY,
                confidence=min(0.85, confidence), suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"uTrend LONG: mom={momentum*100:.2f}%, short={short_mom*100:.2f}%"
            )
            self.last_signal_time[coin] = now

        elif momentum < -self.momentum_threshold and short_mom < 0 and trending_down:
            confidence = 0.55 + min(0.3, abs(momentum) * 20)
            sl = current_price * 1.007
            tp = current_price * 0.986
            size = (account_balance * 0.10) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.SELL,
                confidence=min(0.85, confidence), suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"uTrend SHORT: mom={momentum*100:.2f}%, short={short_mom*100:.2f}%"
            )
            self.last_signal_time[coin] = now

        return signal


class RangeScalper:
    """Range-bound scalper - trades mean reversion in sideways markets
    Uses Bollinger Bands + RSI to catch bounces off extremes in ranging conditions"""

    def __init__(self, bb_period: int = 20, bb_std: float = 2.0,
                 rsi_period: int = 14, max_leverage: float = 5.0):
        self.name = "Range_Scalper"
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.rsi_period = rsi_period
        self.max_leverage = max_leverage
        self.indicators = TechnicalIndicators()
        self.last_signal_time = {}

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame,
                        current_price: float, account_balance: float) -> Optional[TradingSignal]:
        import time as _time
        now = _time.time()
        coin = symbol.replace("-PERP", "")
        if coin in self.last_signal_time and now - self.last_signal_time[coin] < 5:
            return None

        if len(ohlcv) < max(self.bb_period, self.rsi_period) + 5:
            return None

        closes = ohlcv['close']
        highs = ohlcv['high']
        lows = ohlcv['low']

        upper, middle, lower = self.indicators.bollinger_bands(
            closes, self.bb_period, self.bb_std
        )
        rsi = self.indicators.rsi(closes, self.rsi_period)
        adx = self.indicators.adx(highs, lows, closes, 14)

        cur_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50
        cur_adx = adx.iloc[-1] if not pd.isna(adx.iloc[-1]) else 25
        cur_upper = upper.iloc[-1] if not pd.isna(upper.iloc[-1]) else current_price * 1.01
        cur_lower = lower.iloc[-1] if not pd.isna(lower.iloc[-1]) else current_price * 0.99
        cur_mid = middle.iloc[-1] if not pd.isna(middle.iloc[-1]) else current_price

        is_ranging = cur_adx < 25

        signal = None

        if is_ranging and current_price <= cur_lower * 1.005 and cur_rsi < 42:
            confidence = 0.58 + min(0.25, (42 - cur_rsi) / 100)
            sl = current_price * 0.993
            tp = current_price * 1.014
            size = (account_balance * 0.10) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.BUY,
                confidence=min(0.85, confidence), suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"Range LONG: BB lower touch, RSI={cur_rsi:.0f}, ADX={cur_adx:.0f}"
            )
            self.last_signal_time[coin] = now

        elif is_ranging and current_price >= cur_upper * 0.995 and cur_rsi > 58:
            confidence = 0.58 + min(0.25, (cur_rsi - 58) / 100)
            sl = current_price * 1.007
            tp = current_price * 0.986
            size = (account_balance * 0.10) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.SELL,
                confidence=min(0.85, confidence), suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"Range SHORT: BB upper touch, RSI={cur_rsi:.0f}, ADX={cur_adx:.0f}"
            )
            self.last_signal_time[coin] = now

        elif is_ranging and cur_rsi < 45 and current_price < cur_mid * 0.998:
            prev_rsi = rsi.iloc[-2] if len(rsi) > 1 and not pd.isna(rsi.iloc[-2]) else 50
            if cur_rsi > prev_rsi:
                confidence = 0.58 + min(0.15, (45 - cur_rsi) / 100)
                sl = current_price * 0.993
                tp = current_price * 1.014
                size = (account_balance * 0.08) / current_price
                signal = TradingSignal(
                    symbol=symbol, signal_type=SignalType.BUY,
                    confidence=min(0.80, confidence), suggested_leverage=self.max_leverage,
                    suggested_size=size, entry_price=current_price,
                    stop_loss=sl, take_profit=tp,
                    rationale=f"Range LONG: RSI bounce {prev_rsi:.0f}->{cur_rsi:.0f}, below mid, ADX={cur_adx:.0f}"
                )
                self.last_signal_time[coin] = now

        elif is_ranging and cur_rsi > 55 and current_price > cur_mid * 1.002:
            prev_rsi = rsi.iloc[-2] if len(rsi) > 1 and not pd.isna(rsi.iloc[-2]) else 50
            if cur_rsi < prev_rsi:
                confidence = 0.58 + min(0.15, (cur_rsi - 55) / 100)
                sl = current_price * 1.007
                tp = current_price * 0.986
                size = (account_balance * 0.08) / current_price
                signal = TradingSignal(
                    symbol=symbol, signal_type=SignalType.SELL,
                    confidence=min(0.80, confidence), suggested_leverage=self.max_leverage,
                    suggested_size=size, entry_price=current_price,
                    stop_loss=sl, take_profit=tp,
                    rationale=f"Range SHORT: RSI fade {prev_rsi:.0f}->{cur_rsi:.0f}, above mid, ADX={cur_adx:.0f}"
                )
                self.last_signal_time[coin] = now

        return signal


class CandleBuilder:
    """Builds OHLCV candles from real-time price ticks"""

    def __init__(self, timeframe_seconds: int = 60):
        self.timeframe_seconds = timeframe_seconds
        self.candles: Dict[str, List[Dict]] = {}
        self.current_candle: Dict[str, Dict] = {}
        self.last_tick: Dict[str, float] = {}

    def load_historical(self, coin: str, candles: List[Dict]):
        normalized = []
        for candle in candles:
            try:
                open_time = float(candle.get("open_time", candle.get("time", 0)))
                normalized.append({
                    "open_time": open_time,
                    "open": float(candle["open"]),
                    "high": float(candle["high"]),
                    "low": float(candle["low"]),
                    "close": float(candle["close"]),
                    "volume": float(candle.get("volume", 0.0)),
                })
            except (KeyError, TypeError, ValueError):
                continue
        normalized.sort(key=lambda x: x["open_time"])
        if normalized:
            self.candles[coin] = normalized[-1000:]
            last = normalized[-1]
            current_start = int(time.time() // self.timeframe_seconds) * self.timeframe_seconds
            if last["open_time"] >= current_start:
                self.candles[coin] = normalized[:-1][-1000:]
                self.current_candle[coin] = last.copy()
            else:
                self.current_candle.pop(coin, None)

    def update(self, coin: str, price: float, volume: float = 0.0):
        now = time.time()
        candle_start = int(now // self.timeframe_seconds) * self.timeframe_seconds

        if coin not in self.candles:
            self.candles[coin] = []

        if coin not in self.current_candle:
            self.current_candle[coin] = self._new_candle(candle_start, price, volume)
        else:
            candle = self.current_candle[coin]
            if candle["open_time"] != candle_start:
                self.candles[coin].append(candle.copy())
                if len(self.candles[coin]) > 1000:
                    self.candles[coin] = self.candles[coin][-1000:]
                self.current_candle[coin] = self._new_candle(candle_start, price, volume)
            else:
                candle["high"] = max(candle["high"], price)
                candle["low"] = min(candle["low"], price)
                candle["close"] = price
                candle["volume"] += volume

        self.last_tick[coin] = now

    def _new_candle(self, open_time: float, price: float, volume: float) -> Dict:
        return {
            "open_time": open_time,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": volume,
        }

    def get_ohlcv_df(self, coin: str, min_candles: int = 15):
        import pandas as pd

        all_candles = list(self.candles.get(coin, []))
        if coin in self.current_candle:
            all_candles.append(self.current_candle[coin])

        if len(all_candles) < min_candles:
            return None

        df = pd.DataFrame(all_candles)
        df.rename(columns={"open_time": "time"}, inplace=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        return df


class MoonshotDaemon:

    def __init__(
        self,
        initial_balance: float = 100.0,
        symbols: List[str] = None,
        check_interval: float = 60.0,
        paper_trading: bool = True
    ):
        self.initial_balance = initial_balance
        self.symbols = symbols or ["BTC-PERP", "ETH-PERP", "SOL-PERP"]
        self.coin_map = {s.replace("-PERP", ""): s for s in self.symbols}
        self.check_interval = check_interval
        self.paper_trading = paper_trading

        self.ws_client: Optional[HyperliquidWebSocketClient] = None
        self.state_manager: Optional[StateManager] = None

        self.candle_builder = CandleBuilder(timeframe_seconds=60)

        trading_mode = os.getenv("TRADING_MODE", "paper").lower()
        if not paper_trading:
            trading_mode = "live"
        self.paper_trading = trading_mode == "paper"
        configured_max_leverage = float(os.getenv("MAX_LEVERAGE", os.getenv("LIVE_MAX_LEVERAGE", "3" if not self.paper_trading else "5")))

        if self.paper_trading:
            self.trading_engine = HyperliquidPaperTrading(
                initial_balance=initial_balance,
                symbols=self.symbols,
                default_leverage=min(5.0, configured_max_leverage),
                max_leverage=configured_max_leverage,
            )
        else:
            from moonshot.strategies.live_executor import HyperliquidLiveTrading
            pk = os.getenv("HYPERLIQUID_PRIVATE_KEY", "")
            wa = os.getenv("HYPERLIQUID_WALLET_ADDRESS", "")
            network = os.getenv("HYPERLIQUID_NETWORK", "mainnet")
            max_pos = float(os.getenv("LIVE_MAX_POSITION_USD", "50"))
            if not pk or not wa:
                logger.error("LIVE mode requires HYPERLIQUID_PRIVATE_KEY and HYPERLIQUID_WALLET_ADDRESS in .env")
                logger.error("Falling back to paper trading")
                self.paper_trading = True
                self.trading_engine = HyperliquidPaperTrading(
                    initial_balance=initial_balance,
                    symbols=self.symbols,
                    default_leverage=min(5.0, configured_max_leverage),
                    max_leverage=configured_max_leverage,
                )
            else:
                self.trading_engine = HyperliquidLiveTrading(
                    private_key=pk,
                    wallet_address=wa,
                    network=network,
                    initial_balance=initial_balance,
                    symbols=self.symbols,
                    default_leverage=configured_max_leverage,
                    max_leverage=configured_max_leverage,
                    max_position_usd=max_pos,
                )

        self.strategies = [
            QuickMomentum(
                ema_fast=8, ema_slow=21, rsi_period=14,
                rsi_ob=65, rsi_os=35, max_leverage=5.0,
            ),
            RsiDivergence(
                rsi_period=14, rsi_ob=65, rsi_os=35, max_leverage=5.0,
            ),
            FibonacciRetracement(
                swing_lookback=10, confluence_threshold=0.005, max_leverage=5.0,
            ),
            MACDStrategy(
                macd_fast=12, macd_slow=26, macd_signal=9, max_leverage=5.0,
            ),
            VWAPStrategy(
                vwap_std_mult=2.0, reversion_band=1.5, max_leverage=5.0,
            ),
            RangeScalper(
                bb_period=20, bb_std=2.0, rsi_period=14, max_leverage=5.0,
            ),
            CrossAssetLeadLag(
                lookback=5, btc_move_threshold=0.0005, lag_threshold=0.2, max_leverage=5.0,
            ),
            FundingRateContrarian(
                high_funding_threshold=0.0005, low_funding_threshold=-0.0003, max_leverage=5.0,
                zscore_threshold=2.0,
            ),
            VolatilitySqueeze(
                bb_period=20, bb_std=2.0, kc_atr_mult=1.5, atr_period=10, max_leverage=5.0,
            ),
            EMARibbonPullback(
                ema_fast=8, ema_mid=13, ema_slow=21, rsi_period=14, max_leverage=5.0,
            ),
            VWAPReversion(
                vwap_period=20, deviation_threshold=0.003, rsi_period=14, max_leverage=5.0,
            ),
            MomentumExhaustion(
                rsi_period=14, rsi_ob=75, rsi_os=25, lookback=3, max_leverage=5.0,
            ),
            QQEStrategy(
                rsi_period=14, rsi_smoothing=5, threshold_mult=1.618,
                ema_fast=8, ema_slow=21, max_leverage=5.0,
            ),
        ]

        self.running = False
        self.iteration_count = 0
        self.total_signals = 0
        self.total_trades = 0
        self._overrides_path = Path("state/moonshot/runtime_overrides.json")
        self._overrides_mtime = 0.0
        self._applied_overrides: Dict[str, Any] = {}

        self._strategy_tracker = None
        self._self_improver = None
        self._last_signal_strategy: Dict[str, str] = {}
        self._last_improvement_time = 0.0
        self._improvement_interval = 3600
        self._last_backtest_time = 0.0
        self._backtest_interval = 1800
        self._last_funding_fetch = 0.0
        self._funding_fetch_interval = 300

        self.max_open_positions = int(os.getenv("MAX_OPEN_POSITIONS", "3"))
        self.max_risk_per_trade_pct = float(os.getenv("RISK_PER_TRADE_PCT", "0.005"))
        self.stop_loss_pct = 0.007
        self.take_profit_pct = 0.014
        self.trailing_stop_pct = 0.004
        self.max_drawdown_pct = float(os.getenv("MAX_DRAWDOWN_PCT", "0.25"))
        self.max_position_duration = int(os.getenv("MAX_POSITION_DURATION_SECONDS", "0"))
        self.stale_position_threshold = 999999
        self.stale_pnl_threshold = 0.001
        self.min_confidence = float(os.getenv("MIN_CONFIDENCE", "0.72"))
        self.max_leverage = configured_max_leverage
        self.trade_cooldown = float(os.getenv("TRADE_COOLDOWN_SECONDS", "15"))
        self._last_trade_time = 0.0
        self.use_atr_sl_tp = False
        self.atr_sl_mult = 7.0
        self.atr_tp_mult = 9.0
        self.atr_trail_mult = 6.0
        self.atr_period = 30
        self.coin_vol_mult = {"BTC": 1.0, "ETH": 1.1, "SOL": 1.5}
        self.regime_sl_tp = {
            "ranging": {"sl": 0.003, "tp": 0.006, "trail": 0.002},
            "mild_trend": {"sl": 0.004, "tp": 0.009, "trail": 0.003},
            "trending": {"sl": 0.005, "tp": 0.012, "trail": 0.004},
            "unknown": {"sl": 0.004, "tp": 0.009, "trail": 0.003},
        }
        self.regime_timeout = {
            "ranging": self.max_position_duration,
            "mild_trend": self.max_position_duration,
            "trending": self.max_position_duration,
            "unknown": self.max_position_duration,
        }
        self.per_asset_sl_tp = {
            "BTC": {"sl": 0.005, "tp": 0.012, "trail": 0.004},
            "ETH": {"sl": 0.005, "tp": 0.010, "trail": 0.004},
            "SOL": {"sl": 0.005, "tp": 0.008, "trail": 0.004},
        }
        self.partial_tp_enabled = False
        self.partial_tp_pct = 0.50
        self.partial_tp_rr = 1.0
        self._partial_closed = {}
        self._partial_pnl: Dict[str, float] = {}
        self.confluence_boost = 0.10
        self.candle_builder_5m = CandleBuilder(timeframe_seconds=300)
        self.stale_drift_exit = False
        self.stale_drift_min_age = 2700
        self.stale_drift_max_pnl_pct = 0.05

        self.breakeven_stop_enabled = True
        self.breakeven_activation_pct = 0.005
        self._breakeven_hit: Dict[str, bool] = {}
        self.trail_activation_pct = 0.004
        self.max_sl_pct = 0.012
        self.max_tp_pct = 0.025

        self.early_profit_exit = False
        self.early_profit_min_pct = 0.0015
        self.early_profit_min_age = 300

        self.high_confidence_threshold = 0.80
        self.high_confidence_risk_mult = 1.0 if not self.paper_trading else 1.15

        self.partial_tp_rr = 1.5

        self.regime_risk_mult = {
            "ranging": 0.5,
            "mild_trend": 0.75,
            "trending": 1.0,
            "unknown": 1.0,
        }

        self.adaptive_exits_enabled = os.getenv("ADAPTIVE_EXITS_ENABLED", "false").lower() in ("1", "true", "yes", "on")
        self.chandelier_atr_mult = float(os.getenv("CHANDELIER_ATR_MULT", "2.5"))
        if os.getenv("PARTIAL_TP_AT_1R", "false").lower() in ("1", "true", "yes", "on"):
            self.partial_tp_rr = 1.0
        if self.adaptive_exits_enabled:
            self.partial_tp_enabled = True
            self.breakeven_stop_enabled = True
        self.regime_gate_enabled = os.getenv("REGIME_GATE_ENABLED", "false").lower() in ("1", "true", "yes", "on")
        self.regime_gate_min_trades = int(os.getenv("REGIME_GATE_MIN_TRADES", "20"))
        self.regime_gate_min_expected_R = float(os.getenv("REGIME_GATE_MIN_EXPECTED_R", "0.05"))
        self.regime_gate_min_wr = float(os.getenv("REGIME_GATE_MIN_WR", "0.40"))
        self.regime_gate_grace_trades = int(os.getenv("REGIME_GATE_GRACE_TRADES", "30"))

        self.position_sl_at_entry: Dict[str, float] = {}
        self.position_entry_atr: Dict[str, float] = {}

        self.strategy_coin_blacklist = {
            "Fibonacci_Retracement": {"SOL"},
            "MACD_Divergence": {"SOL", "ETH"},
            "VWAP_Mean": {"BTC", "ETH", "SOL"},
            "Range_Scalper": {"BTC", "ETH"},
            "Funding_Contrarian": {"SOL"},
        }
        self.regime_strategy_filter = {
            "ranging": {"Quick_Momentum", "Cross_Lead_Lag", "Vol_Squeeze", "RSI_Reversion", "Range_Scalper", "VWAP_Revert", "Mom_Exhaust", "QQE"},
            "mild_trend": {"Quick_Momentum", "Cross_Lead_Lag", "Vol_Squeeze", "Fibonacci_Retracement", "MACD_Divergence", "RSI_Reversion", "EMA_Ribbon", "VWAP_Revert", "QQE"},
            "trending": {"Quick_Momentum", "Cross_Lead_Lag", "Funding_Contrarian", "MACD_Divergence", "EMA_Ribbon", "Mom_Exhaust", "QQE"},
        }

        self.current_prices: Dict[str, float] = {}
        self.position_highest: Dict[str, float] = {}
        self.position_lowest: Dict[str, float] = {}
        self.position_entry_time: Dict[str, float] = {}
        self.position_sl: Dict[str, float] = {}
        self.position_tp: Dict[str, float] = {}
        self.position_trail: Dict[str, float] = {}
        self.position_regime: Dict[str, str] = {}
        self.funding_rates: Dict[str, float] = {}
        self._daily_pnl: float = 0.0
        self._daily_pnl_date: str = ""
        self._daily_loss_limit: float = float(os.getenv("DAILY_LOSS_LIMIT_PCT", os.getenv("LIVE_DAILY_LOSS_LIMIT", "1.0"))) / 100.0

        logger.info("=" * 70)
        logger.info("MOONSHOT DAEMON INITIALIZED - V14")
        logger.info("=" * 70)
        logger.info(f"  Initial Balance: {initial_balance} USDT")
        logger.info(f"  Symbols: {', '.join(self.symbols)}")
        logger.info(f"  Check Interval: {check_interval}s")
        logger.info(f"  Paper Trading: {self.paper_trading}")
        if not self.paper_trading:
            logger.warning("  ⚠️  LIVE TRADING MODE - REAL FUNDS AT RISK ⚠️")
        logger.info(f"  Max Leverage: {self.max_leverage}x | Risk/Trade: {self.max_risk_per_trade_pct*100:.1f}%")
        logger.info(f"  ATR SL/TP: {'ON' if self.use_atr_sl_tp else 'OFF'} | SL: {self.stop_loss_pct*100:.2f}%-{self.max_sl_pct*100:.2f}% | TP: {self.take_profit_pct*100:.2f}%-{self.max_tp_pct*100:.2f}% | Trail: {self.trailing_stop_pct*100:.2f}%")
        logger.info(f"  Coin Vol Mult: {self.coin_vol_mult}")
        logger.info(f"  MinConf: {self.min_confidence} | Cooldown: {self.trade_cooldown}s | MaxPos: {self.max_open_positions}")
        logger.info(f"  Timeout: {'OFF' if self.max_position_duration <= 0 else str(self.max_position_duration) + 's'} | Strategies: {len(self.strategies)}")
        logger.info(f"  Regime SL/TP: ranging={self.regime_sl_tp['ranging']} | mild_trend={self.regime_sl_tp['mild_trend']} | trending={self.regime_sl_tp['trending']}")
        logger.info(f"  Regime Timeout: ranging={self.regime_timeout['ranging']}s | mild_trend={self.regime_timeout['mild_trend']}s | trending={self.regime_timeout['trending']}s")
        logger.info(f"  EarlyProfitExit: {'ON' if self.early_profit_exit else 'OFF'} (>{self.early_profit_min_pct*100:.2f}% after {self.early_profit_min_age}s)")
        logger.info(f"  RegimeRiskMult: {self.regime_risk_mult}")
        logger.info(f"  PartialTP: {'ON' if self.partial_tp_enabled else 'OFF'} ({self.partial_tp_pct*100:.0f}% at {self.partial_tp_rr}R) | ConfluenceBoost: +{self.confluence_boost}")
        logger.info(f"  MultiTF: 1m + 5m candles | RangingMarket detection: ON")
        logger.info(f"  Blacklist: {self.strategy_coin_blacklist} | DriftExit: {'ON' if self.stale_drift_exit else 'OFF'}")
        logger.info(f"  BreakevenStop: {'ON' if self.breakeven_stop_enabled else 'OFF'} (activate at {self.breakeven_activation_pct*100:.1f}%) | TrailActivation: {self.trail_activation_pct*100:.1f}%")
        logger.info(f"  DailyLossLimit: -{self._daily_loss_limit*100:.1f}% | CorrelationCheck: ON")
        logger.info(f"  RegimeDetection: ON | FundingRateData: {'via WS' if True else 'OFF'}")
        logger.info(
            f"  AdaptiveExits: {'ON' if self.adaptive_exits_enabled else 'OFF'}"
            f" (Chandelier x{self.chandelier_atr_mult} ATR, PartialTP@{self.partial_tp_rr}R)"
        )
        logger.info(
            f"  RegimeGate: {'ON' if self.regime_gate_enabled else 'OFF'}"
            f" (min_trades={self.regime_gate_min_trades},"
            f" min_R={self.regime_gate_min_expected_R},"
            f" min_wr={self.regime_gate_min_wr},"
            f" grace={self.regime_gate_grace_trades})"
        )
        logger.info("=" * 70)

    def initialize(self):
        logger.info("Initializing components...")

        self.state_manager = StateManager(
            data_dir="state/moonshot",
            save_interval=30.0
        )
        self.state_manager.start_auto_save()
        self._restore_trading_positions_from_state()
        self._sync_state_snapshot()

        self.ws_client = HyperliquidWebSocketClient()

        self.ws_client.on_price_update = self._on_price_update
        self.ws_client.on_trade = self._on_trade
        self.ws_client.on_connect = self._on_ws_connect
        self.ws_client.on_disconnect = self._on_ws_disconnect

        self.ws_client.subscribe_all_mids()
        for symbol in self.symbols:
            coin = symbol.replace("-PERP", "")
            self.ws_client.subscribe_trades(coin)

        try:
            from moonshot.daemon.self_improvement import StrategyPerformanceTracker, SelfImprover
            self._strategy_tracker = StrategyPerformanceTracker(
                data_dir="state/moonshot"
            )
            self._self_improver = SelfImprover(
                strategy_tracker=self._strategy_tracker,
                strategies=self.strategies,
                data_dir="state/moonshot",
                improvement_interval=self._improvement_interval,
            )
            self._strategy_tracker.load()
            logger.info("Self-improvement system initialized")
        except Exception as e:
            logger.warning(f"Self-improvement system not available: {e}")
            self._strategy_tracker = None
            self._self_improver = None

        logger.info("All components initialized")

    def _restore_trading_positions_from_state(self) -> None:
        """Restore paper-trading positions into the execution engine after a restart."""
        if not self.paper_trading or not self.state_manager:
            return
        try:
            for symbol, saved in self.state_manager.get_all_positions().items():
                if symbol in self.trading_engine.positions:
                    continue
                side = OrderSide.BUY if saved.side == "long" else OrderSide.SELL
                self.trading_engine.positions[symbol] = Position(
                    symbol=symbol,
                    side=side,
                    size=float(saved.size),
                    entry_price=float(saved.entry_price),
                    leverage=float(saved.leverage),
                    unrealized_pnl=float(saved.unrealized_pnl),
                    timestamp=float(saved.open_time),
                )
                coin = symbol.replace("-PERP", "")
                self.position_entry_time[coin] = float(saved.open_time)
                self.position_highest[coin] = max(float(saved.entry_price), float(saved.current_price))
                self.position_lowest[coin] = min(float(saved.entry_price), float(saved.current_price))
                if saved.stop_loss:
                    self.position_sl[coin] = float(saved.stop_loss)
                    self.position_sl_at_entry[coin] = float(saved.stop_loss)
                if saved.take_profit:
                    self.position_tp[coin] = float(saved.take_profit)
            if self.trading_engine.positions:
                logger.info("Restored %d paper positions from state", len(self.trading_engine.positions))
        except Exception as e:
            logger.warning("Failed to restore paper positions from state: %s", e)

    def _sync_state_snapshot(self) -> None:
        """Keep dashboard-facing state.json aligned with the live execution engine."""
        if not self.state_manager:
            return
        try:
            positions = self.trading_engine.positions
            margin_used = sum((p.size * p.entry_price) / max(p.leverage, 1e-9) for p in positions.values())
            equity = self.trading_engine.get_balance()
            self.state_manager.balance = float(getattr(self.trading_engine, "balance", equity))
            self.state_manager.equity = float(equity)
            self.state_manager.margin_used = float(margin_used)
            self.state_manager.free_margin = float(equity - margin_used)
        except Exception as e:
            logger.debug("State snapshot sync skipped: %s", e)

    def _bootstrap_historical_candles(self):
        logger.info("Bootstrapping historical candles from Hyperliquid...")
        now_ms = int(time.time() * 1000)
        interval_ms = 60_000
        start_ms = now_ms - (240 * interval_ms)
        for coin in [s.replace("-PERP", "") for s in self.symbols]:
            candles_1m = self._fetch_candle_snapshot(coin, "1m", start_ms, now_ms)
            if candles_1m:
                self.candle_builder.load_historical(coin, candles_1m)
                logger.info(f"  Loaded {len(candles_1m)} 1m candles for {coin}")
            candles_5m = self._aggregate_candles(candles_1m, 300) if candles_1m else []
            if candles_5m:
                self.candle_builder_5m.load_historical(coin, candles_5m)
                logger.info(f"  Loaded {len(candles_5m)} 5m candles for {coin}")

    def _fetch_candle_snapshot(self, coin: str, interval: str, start_ms: int, end_ms: int) -> List[Dict]:
        try:
            import urllib.request
            url = "https://api.hyperliquid.xyz/info"
            payload = json.dumps({
                "type": "candleSnapshot",
                "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms},
            }).encode()
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            candles = []
            for row in data if isinstance(data, list) else []:
                candles.append({
                    "open_time": float(row["t"]) / 1000.0,
                    "open": float(row["o"]),
                    "high": float(row["h"]),
                    "low": float(row["l"]),
                    "close": float(row["c"]),
                    "volume": float(row.get("v", 0.0)),
                })
            return candles
        except Exception as e:
            logger.warning(f"Failed to bootstrap candles for {coin}: {e}")
            return []

    def _aggregate_candles(self, candles: List[Dict], timeframe_seconds: int) -> List[Dict]:
        buckets: Dict[int, Dict] = {}
        for candle in candles:
            bucket = int(candle["open_time"] // timeframe_seconds) * timeframe_seconds
            if bucket not in buckets:
                buckets[bucket] = {
                    "open_time": bucket,
                    "open": candle["open"],
                    "high": candle["high"],
                    "low": candle["low"],
                    "close": candle["close"],
                    "volume": candle.get("volume", 0.0),
                }
            else:
                target = buckets[bucket]
                target["high"] = max(target["high"], candle["high"])
                target["low"] = min(target["low"], candle["low"])
                target["close"] = candle["close"]
                target["volume"] += candle.get("volume", 0.0)
        return [buckets[key] for key in sorted(buckets)]

    def _on_price_update(self, update: PriceUpdate):
        coin = update.coin
        self.current_prices[coin] = update.mid

        self.candle_builder.update(coin, update.mid)
        self.candle_builder_5m.update(coin, update.mid)

        symbol = f"{coin}-PERP" if f"{coin}-PERP" in self.trading_engine.symbols else coin
        self.trading_engine.update_price(symbol, update.mid)
        if self.state_manager:
            self.state_manager.update_position_price(symbol, update.mid)

        if coin in [s.replace("-PERP", "") for s in self.symbols]:
            self._check_stops_and_profits(coin, update.mid)

    def _fetch_funding_rates(self):
        try:
            import urllib.request
            url = "https://api.hyperliquid.xyz/info"
            payload = json.dumps({"type": "metaAndAssetCtxs"}).encode()
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            if isinstance(data, list) and len(data) >= 2:
                meta = data[0]
                contexts = data[1]
                if isinstance(meta, dict) and "universe" in meta:
                    for i, asset in enumerate(meta["universe"]):
                        name = asset.get("name", "")
                        if name in [s.replace("-PERP", "") for s in self.symbols]:
                            ctx = contexts[i] if i < len(contexts) else {}
                            funding = ctx.get("funding")
                            if funding is not None:
                                self.funding_rates[name] = float(funding)
            if self.funding_rates:
                logger.info(f"Funding rates: {self.funding_rates}")
        except Exception as e:
            logger.debug(f"Failed to fetch funding rates: {e}")

    def _on_trade(self, trade: TradeData):
        if trade.px > 0 and trade.sz > 0:
            self.candle_builder.update(trade.coin, trade.px, trade.sz)
            self.candle_builder_5m.update(trade.coin, trade.px, trade.sz)

    def _on_ws_connect(self):
        logger.info("WebSocket connected to Hyperliquid")

    def _on_ws_disconnect(self):
        logger.warning("WebSocket disconnected from Hyperliquid")

    def _calculate_atr(self, coin: str, period: int = None) -> Optional[float]:
        if period is None:
            period = self.atr_period
        df = self.candle_builder.get_ohlcv_df(coin, min_candles=period + 1)
        if df is None or len(df) < period + 1:
            return None
        high = df['high'].astype(float)
        low = df['low'].astype(float)
        close = df['close'].astype(float)
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean().iloc[-1]
        if pd.isna(atr):
            return None
        return float(atr)

    def _detect_market_regime(self, coin: str) -> str:
        df = self.candle_builder.get_ohlcv_df(coin, min_candles=30)
        if df is None or len(df) < 30:
            return "unknown"
        closes = df['close'].astype(float)
        highs = df['high'].astype(float)
        lows = df['low'].astype(float)
        from moonshot.strategies.strategies import TechnicalIndicators
        adx = TechnicalIndicators.adx(highs, lows, closes, 14)
        cur_adx = adx.iloc[-1] if not pd.isna(adx.iloc[-1]) else 25

        hurst = self._hurst_exponent(closes)

        if cur_adx < 20 and hurst < 0.55:
            return "ranging"
        elif cur_adx > 28 and hurst > 0.55:
            return "trending"
        elif cur_adx < 20:
            return "ranging"
        elif cur_adx > 28:
            return "trending"
        else:
            return "mild_trend"

    def _hurst_exponent(self, prices: pd.Series) -> float:
        if len(prices) < 50:
            return 0.5
        try:
            log_returns = np.log(prices / prices.shift(1)).dropna().values
            if len(log_returns) < 30:
                return 0.5
            max_lag = min(20, len(log_returns) // 2)
            lags = range(2, max_lag + 1)
            tau = []
            for lag in lags:
                diff = log_returns[lag:] - log_returns[:-lag]
                std = np.std(diff)
                if std > 0:
                    tau.append(std)
            if len(tau) < 3:
                return 0.5
            valid_lags = list(range(2, 2 + len(tau)))
            log_lags = np.log(valid_lags)
            log_tau = np.log(tau)
            poly = np.polyfit(log_lags, log_tau, 1)
            return float(np.clip(poly[0], 0.0, 1.0))
        except Exception:
            return 0.5

    def _get_5m_trend(self, coin: str) -> str:
        df = self.candle_builder_5m.get_ohlcv_df(coin, min_candles=5)
        if df is None or len(df) < 5:
            return "unknown"
        closes = df['close'].astype(float)
        if len(closes) < 10:
            return "unknown"
        ema5 = closes.rolling(5).mean().iloc[-1]
        ema10 = closes.rolling(10).mean().iloc[-1] if len(closes) >= 10 else ema5
        if ema5 > ema10 * 1.0005:
            return "uptrend"
        elif ema5 < ema10 * 0.9995:
            return "downtrend"
        return "ranging"

    def _get_atr_sl_tp(self, coin: str, current_price: float, side: SignalType) -> Tuple[float, float, float]:
        vol_mult = self.coin_vol_mult.get(coin, 1.0)
        atr = self._calculate_atr(coin)
        if atr is not None and atr > 0:
            sl_distance = atr * self.atr_sl_mult * vol_mult
            tp_distance = atr * self.atr_tp_mult * vol_mult
            trail_distance = atr * self.atr_trail_mult * vol_mult
            if side == SignalType.BUY:
                sl = current_price - sl_distance
                tp = current_price + tp_distance
            else:
                sl = current_price + sl_distance
                tp = current_price - tp_distance
            return sl, tp, trail_distance
        if side == SignalType.BUY:
            sl = current_price * (1 - self.stop_loss_pct * vol_mult)
            tp = current_price * (1 + self.take_profit_pct * vol_mult)
        else:
            sl = current_price * (1 + self.stop_loss_pct * vol_mult)
            tp = current_price * (1 - self.take_profit_pct * vol_mult)
        return sl, tp, current_price * self.trailing_stop_pct * vol_mult

    def _check_stops_and_profits(self, coin: str, current_price: float):
        symbol = f"{coin}-PERP"

        if symbol not in self.trading_engine.positions:
            return

        pos = self.trading_engine.positions[symbol]

        if coin not in self.position_highest:
            self.position_highest[coin] = current_price
        if coin not in self.position_lowest:
            self.position_lowest[coin] = current_price
        if coin not in self.position_entry_time:
            self.position_entry_time[coin] = pos.timestamp

        self.position_highest[coin] = max(
            self.position_highest.get(coin, current_price), current_price
        )
        self.position_lowest[coin] = min(
            self.position_lowest.get(coin, current_price), current_price
        )
        highest = self.position_highest[coin]
        lowest = self.position_lowest[coin]
        entry_time = self.position_entry_time.get(coin, pos.timestamp)
        position_age = time.time() - entry_time

        regime = self.position_regime.get(coin, self._detect_market_regime(coin))
        regime_params = self.regime_sl_tp.get(regime, self.regime_sl_tp["unknown"])
        regime_timeout = self.regime_timeout.get(regime, self.max_position_duration)

        sl_pct = regime_params["sl"]
        tp_pct = regime_params["tp"]
        trail_pct = regime_params["trail"]

        vol_mult = self.coin_vol_mult.get(coin, 1.0)
        sl_pct *= vol_mult
        tp_pct *= vol_mult
        trail_pct *= vol_mult

        pos_sl = self.position_sl.get(coin)
        pos_tp = self.position_tp.get(coin)
        pos_trail = self.position_trail.get(coin)

        if pos_sl is None:
            if pos.side == OrderSide.BUY:
                pos_sl = pos.entry_price * (1 - sl_pct)
            else:
                pos_sl = pos.entry_price * (1 + sl_pct)
            self.position_sl[coin] = pos_sl
        if pos_tp is None:
            if pos.side == OrderSide.BUY:
                pos_tp = pos.entry_price * (1 + tp_pct)
            else:
                pos_tp = pos.entry_price * (1 - tp_pct)
            self.position_tp[coin] = pos_tp
        if pos_trail is None:
            pos_trail = pos.entry_price * trail_pct
            self.position_trail[coin] = pos_trail

        if self.adaptive_exits_enabled:
            atr_val = self.position_entry_atr.get(coin)
            if atr_val is None or atr_val <= 0:
                atr_val = self._calculate_atr(coin)
                if atr_val and atr_val > 0:
                    self.position_entry_atr[coin] = atr_val
            if atr_val and atr_val > 0:
                pos_trail = atr_val * self.chandelier_atr_mult
                self.position_trail[coin] = pos_trail

        if self.breakeven_stop_enabled and coin not in self._breakeven_hit:
            if pos.side == OrderSide.BUY and current_price >= pos.entry_price * (1 + self.breakeven_activation_pct):
                be_price = pos.entry_price * 1.0005
                if be_price > pos_sl:
                    pos_sl = be_price
                    self.position_sl[coin] = pos_sl
                    self._breakeven_hit[coin] = True
                    logger.info(f"  BREAKEVEN STOP activated for {coin} LONG: SL moved to {pos_sl:.2f}")
            elif pos.side == OrderSide.SELL and current_price <= pos.entry_price * (1 - self.breakeven_activation_pct):
                be_price = pos.entry_price * 0.9995
                if be_price < pos_sl:
                    pos_sl = be_price
                    self.position_sl[coin] = pos_sl
                    self._breakeven_hit[coin] = True
                    logger.info(f"  BREAKEVEN STOP activated for {coin} SHORT: SL moved to {pos_sl:.2f}")

        if pos.side == OrderSide.BUY:
            pnl_pct = (current_price - pos.entry_price) / pos.entry_price
            risk_distance = abs(pos.entry_price - pos_sl)
            should_close_sl = current_price <= pos_sl
            should_close_tp = current_price >= pos_tp
            trailing_stop = highest - pos_trail
            should_close_trail = current_price < trailing_stop and highest > pos.entry_price * (1 + self.trail_activation_pct)
            should_close_timeout = regime_timeout > 0 and position_age > regime_timeout
        else:
            pnl_pct = (pos.entry_price - current_price) / pos.entry_price
            risk_distance = abs(pos.entry_price - pos_sl)
            should_close_sl = current_price >= pos_sl
            should_close_tp = current_price <= pos_tp
            trailing_stop = lowest + pos_trail
            should_close_trail = current_price > trailing_stop and lowest < pos.entry_price * (1 - self.trail_activation_pct)
            should_close_timeout = regime_timeout > 0 and position_age > regime_timeout

        should_close_early_profit = False
        if self.early_profit_exit and pnl_pct >= self.early_profit_min_pct and position_age >= self.early_profit_min_age:
            should_close_early_profit = True

        if self.partial_tp_enabled and coin not in self._partial_closed and risk_distance > 0:
            if pos.side == OrderSide.BUY:
                partial_tp_price = pos.entry_price + risk_distance * self.partial_tp_rr
                if current_price >= partial_tp_price:
                    partial_size = pos.size * self.partial_tp_pct
                    if partial_size > 0 and pos.size - partial_size > 0:
                        self._partial_closed[coin] = True
                        self._do_partial_close(symbol, coin, partial_size, current_price, "PARTIAL_TP")
                        if self.adaptive_exits_enabled:
                            be_price = pos.entry_price * 1.0005
                            if be_price > pos_sl:
                                pos_sl = be_price
                                self.position_sl[coin] = pos_sl
                            self._breakeven_hit[coin] = True
            else:
                partial_tp_price = pos.entry_price - risk_distance * self.partial_tp_rr
                if current_price <= partial_tp_price:
                    partial_size = pos.size * self.partial_tp_pct
                    if partial_size > 0 and pos.size - partial_size > 0:
                        self._partial_closed[coin] = True
                        self._do_partial_close(symbol, coin, partial_size, current_price, "PARTIAL_TP")
                        if self.adaptive_exits_enabled:
                            be_price = pos.entry_price * 0.9995
                            if be_price < pos_sl:
                                pos_sl = be_price
                                self.position_sl[coin] = pos_sl
                            self._breakeven_hit[coin] = True

        should_close_drift = False
        if self.stale_drift_exit and position_age > self.stale_drift_min_age:
            if abs(pnl_pct * 100) < self.stale_drift_max_pnl_pct:
                should_close_drift = True

        should_close = (should_close_sl or should_close_tp or should_close_trail
                        or should_close_timeout or should_close_drift or should_close_early_profit)

        if should_close:
            if should_close_sl:
                reason = "SL"
            elif should_close_tp:
                reason = "TP"
            elif should_close_trail:
                reason = "TRAIL"
            elif should_close_early_profit:
                reason = "PROFIT_TAKE"
            elif should_close_drift:
                reason = "DRIFT"
            elif should_close_timeout:
                reason = "TIMEOUT"
            else:
                reason = "UNKNOWN"

            realized = pos.calculate_unrealized_pnl(current_price)
            self.trading_engine.close_position(symbol)
            self.total_trades += 1

            if coin in self.position_highest:
                del self.position_highest[coin]
            if coin in self.position_lowest:
                del self.position_lowest[coin]
            if coin in self.position_entry_time:
                del self.position_entry_time[coin]
            if coin in self.position_sl:
                del self.position_sl[coin]
            if coin in self.position_tp:
                del self.position_tp[coin]
            if coin in self.position_trail:
                del self.position_trail[coin]
            if coin in self._breakeven_hit:
                del self._breakeven_hit[coin]
            if coin in self._partial_closed:
                del self._partial_closed[coin]
            if coin in self.position_regime:
                del self.position_regime[coin]
            if coin in self.position_sl_at_entry:
                del self.position_sl_at_entry[coin]
            if coin in self.position_entry_atr:
                del self.position_entry_atr[coin]

            emoji = "+" if realized > 0 else ""
            logger.info(
                f"  CLOSED {coin} {reason} | PnL: {emoji}{realized:.4f} USDT "
                f"({pnl_pct*100:+.2f}%) | Age: {position_age:.0f}s | Bal: {self.trading_engine.balance:.2f}"
            )

            self._record_trade_to_state(coin, pos, realized, reason)
            if self.state_manager:
                self.state_manager.remove_position(symbol)

            today = datetime.now().strftime("%Y-%m-%d")
            if today != self._daily_pnl_date:
                self._daily_pnl = 0.0
                self._daily_pnl_date = today
            self._daily_pnl += realized

    def _do_partial_close(self, symbol: str, coin: str, size: float, current_price: float, reason: str):
        try:
            pos = self.trading_engine.positions.get(symbol)
            if pos is None or size <= 0 or size >= pos.size:
                return
            partial_pnl = pos.calculate_unrealized_pnl(current_price) * (size / pos.size)
            close_side = OrderSide.SELL if pos.side == OrderSide.BUY else OrderSide.BUY
            self.trading_engine.place_order(
                symbol=symbol, side=close_side, size=size,
                order_type=OrderType.MARKET, leverage=pos.leverage,
            )
            self.total_trades += 1
            self._partial_pnl[coin] = self._partial_pnl.get(coin, 0.0) + partial_pnl
            logger.info(
                f"  PARTIAL CLOSE {coin} {reason} | Size: {size:.6f}/{pos.size:.6f} | "
                f"EstPnL: {partial_pnl:+.4f} | AccPartialPnL: {self._partial_pnl[coin]:+.4f} | Price: {current_price:.2f} | Bal: {self.trading_engine.balance:.2f}"
            )
        except Exception as e:
            logger.error(f"Partial close error for {coin}: {e}")

    def _record_trade_to_state(self, coin: str, pos, realized: float, reason: str):
        try:
            exit_price = self.current_prices.get(coin, pos.entry_price)
            partial_pnl = self._partial_pnl.pop(coin, 0.0)
            total_realized = realized + partial_pnl
            if pos.size > 0 and pos.entry_price > 0:
                pnl_pct_val = (total_realized / (pos.entry_price * pos.size)) * 100
            else:
                pnl_pct_val = 0.0

            entry_sl = self.position_sl_at_entry.get(coin)
            if entry_sl is None or entry_sl <= 0:
                entry_sl = self.position_sl.get(coin)
            if entry_sl and pos.size > 0:
                risk_amount = abs(pos.entry_price - entry_sl) * pos.size
            else:
                risk_amount = 0.0
            r_multiple = (total_realized / risk_amount) if risk_amount > 1e-9 else 0.0
            trade_regime = self.position_regime.get(coin, self._detect_market_regime(coin))

            trade = TradeRecord(
                trade_id=f"T{int(time.time()*1000)}",
                symbol=f"{coin}-PERP",
                side="long" if pos.side == OrderSide.BUY else "short",
                entry_time=pos.timestamp,
                exit_time=time.time(),
                entry_price=pos.entry_price,
                exit_price=exit_price,
                size=pos.size,
                leverage=pos.leverage,
                pnl=realized,
                pnl_pct=pnl_pct_val,
                exit_reason=reason,
                strategy=self._last_signal_strategy.get(coin, "unknown"),
            )
            self.state_manager.add_trade(trade)

            if hasattr(self, '_strategy_tracker') and self._strategy_tracker:
                last_strategy = self._last_signal_strategy.get(coin, "unknown")
                self._strategy_tracker.record_trade(
                    strategy_name=last_strategy,
                    coin=coin,
                    side="long" if pos.side == OrderSide.BUY else "short",
                    pnl=total_realized,
                    pnl_pct=pnl_pct_val,
                    exit_reason=reason,
                    duration=time.time() - pos.timestamp,
                    risk_amount=risk_amount,
                    r_multiple=r_multiple,
                    regime=trade_regime,
                )
        except Exception as e:
            logger.error(f"Error recording trade: {e}")

    def _compute_position_size(self, symbol: str, entry_price: float, stop_price: float, strategy_name: str = "unknown") -> float:
        balance = self.trading_engine.get_available_balance()
        risk_pct = self.max_risk_per_trade_pct

        if self._strategy_tracker and strategy_name != "unknown":
            perf = self._strategy_tracker.get_performance(strategy_name, lookback=30)
            if perf and perf["trades"] >= 5:
                wr = perf["win_rate"]
                pf = perf.get("profit_factor", 1.0)
                if wr >= 70 and pf > 1.5:
                    risk_pct = self.max_risk_per_trade_pct * 1.5
                elif wr >= 55 and pf > 1.0:
                    risk_pct = self.max_risk_per_trade_pct * 1.2
                elif wr < 40 or pf < 0.5:
                    risk_pct = self.max_risk_per_trade_pct * 0.3
                elif wr < 50 or pf < 0.8:
                    risk_pct = self.max_risk_per_trade_pct * 0.5

        risk_amount = balance * risk_pct
        risk_per_unit = abs(entry_price - stop_price)
        if risk_per_unit <= 0:
            return 0.0
        size = risk_amount / risk_per_unit
        max_notional = balance * 0.3
        if size * entry_price > max_notional:
            size = max_notional / entry_price
        return size

    OVERRIDE_ATTR_MAP: Dict[str, str] = {
        "min_confidence":            "min_confidence",
        "max_open_positions":        "max_open_positions",
        "trade_cooldown_seconds":    "trade_cooldown",
        "chandelier_atr_mult":       "chandelier_atr_mult",
        "partial_tp_rr":             "partial_tp_rr",
        "partial_tp_pct":            "partial_tp_pct",
        "adaptive_exits_enabled":    "adaptive_exits_enabled",
        "partial_tp_enabled":        "partial_tp_enabled",
        "breakeven_after_partial":   "breakeven_after_partial",
        "atr_sl_mult":               "atr_sl_mult",
        "atr_tp_mult":               "atr_tp_mult",
        "atr_trail_mult":            "atr_trail_mult",
        "use_atr_sl_tp":             "use_atr_sl_tp",
        "regime_gate_enabled":       "regime_gate_enabled",
        "regime_gate_min_trades":    "regime_gate_min_trades",
        "regime_gate_min_expected_r":"regime_gate_min_expected_r",
        "regime_gate_min_wr":        "regime_gate_min_wr",
        "regime_gate_grace_trades":  "regime_gate_grace_trades",
    }

    def _apply_runtime_overrides(self) -> None:
        try:
            if not self._overrides_path.exists():
                return
            mtime = self._overrides_path.stat().st_mtime
            if mtime <= self._overrides_mtime:
                return
            payload = json.loads(self._overrides_path.read_text() or "{}")
            values = payload.get("values", {})
            if not isinstance(values, dict):
                return
            applied: Dict[str, Any] = {}
            for key, val in values.items():
                attr = self.OVERRIDE_ATTR_MAP.get(key)
                if attr is None or not hasattr(self, attr):
                    continue
                current = getattr(self, attr)
                try:
                    if isinstance(current, bool):
                        new_val = bool(val)
                    elif isinstance(current, int) and not isinstance(current, bool):
                        new_val = int(val)
                    elif isinstance(current, float):
                        new_val = float(val)
                    else:
                        new_val = val
                except (TypeError, ValueError):
                    continue
                if new_val != current:
                    setattr(self, attr, new_val)
                    applied[key] = new_val
            self._overrides_mtime = mtime
            self._applied_overrides = applied
            if applied:
                logger.info(f"Applied runtime overrides: {applied}")
        except Exception as e:
            logger.warning(f"Failed to apply runtime overrides: {e}")

    def check_opportunities(self):
        self._apply_runtime_overrides()
        self.iteration_count += 1
        logger.info(f"--- Iteration {self.iteration_count} ---")

        target_coins = [s.replace("-PERP", "") for s in self.symbols]
        available_prices = {c: p for c, p in self.current_prices.items() if c in target_coins}

        if not available_prices:
            logger.info("Waiting for price data...")
            return

        price_str = " | ".join(f"{c}=${p:,.2f}" for c, p in available_prices.items())
        logger.info(f"Prices: {price_str}")

        open_count = len(self.trading_engine.positions)
        logger.info(
            f"Balance: {self.trading_engine.balance:.2f} | "
            f"Equity: {self.trading_engine.get_balance():.2f} | "
            f"Open: {open_count}/{self.max_open_positions} | "
            f"Trades: {self.total_trades} | Signals: {self.total_signals}"
        )

        if open_count >= self.max_open_positions:
            logger.info("Max positions reached, managing existing")
            return

        balance = self.trading_engine.balance
        if balance < 5.0:
            logger.warning("Balance too low for new trades")
            return

        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._daily_pnl_date:
            self._daily_pnl = 0.0
            self._daily_pnl_date = today
        if self._daily_pnl < -(self.initial_balance * self._daily_loss_limit):
            logger.warning(f"Daily loss limit hit ({self._daily_pnl:.4f} USDT), pausing new trades for today")
            return

        drawdown = 1.0 - (self.trading_engine.get_balance() / self.trading_engine.stats.get('peak_balance', balance))
        if drawdown > self.max_drawdown_pct:
            logger.warning(f"Max drawdown hit ({drawdown*100:.1f}%), pausing new trades")
            return

        available_slots = self.max_open_positions - open_count
        coin_signals: Dict[str, Tuple[TradingSignal, str, float]] = {}

        active_strategies = self.strategies
        if self._self_improver and self._self_improver.strategy_lab:
            active_strategies = self._self_improver.strategy_lab.evaluate_strategies(self.strategies)

        open_long_count = 0
        open_short_count = 0
        for sym, pos in self.trading_engine.positions.items():
            if pos.side == OrderSide.BUY:
                open_long_count += 1
            else:
                open_short_count += 1

        coin_signal_counts: Dict[str, List[Tuple[SignalType, str]]] = {}

        btc_ohlcv = self.candle_builder.get_ohlcv_df("BTC", min_candles=5)

        for coin in target_coins:
            if coin not in available_prices:
                continue

            symbol = f"{coin}-PERP"
            if symbol in self.trading_engine.positions:
                continue

            df = self.candle_builder.get_ohlcv_df(coin, min_candles=5)
            if df is None:
                candle_count = len(self.candle_builder.candles.get(coin, []))
                logger.debug(f"  {coin}: only {candle_count} candles, need 15+")
                continue

            current_price = available_prices[coin]
            regime = self._detect_market_regime(coin)
            regime_allowed = self.regime_strategy_filter.get(regime, set())

            coin_candle_count = len(self.candle_builder.candles.get(coin, [])) + (1 if coin in self.candle_builder.current_candle else 0)

            for strategy in active_strategies:
                sname = getattr(strategy, 'name', strategy.__class__.__name__)
                if sname in self.strategy_coin_blacklist and coin in self.strategy_coin_blacklist[sname]:
                    continue
                if regime_allowed and sname not in regime_allowed:
                    continue
                if not self._strategy_passes_regime_gate(sname, regime, coin):
                    continue
                try:
                    kwargs = {}
                    if isinstance(strategy, CrossAssetLeadLag):
                        kwargs['btc_ohlcv'] = btc_ohlcv
                    elif isinstance(strategy, FundingRateContrarian):
                        kwargs['funding_rate'] = self.funding_rates.get(coin)
                        if kwargs['funding_rate'] is None:
                            continue
                    signal = strategy.generate_signal(
                        symbol=symbol,
                        ohlcv=df,
                        current_price=current_price,
                        account_balance=balance,
                        **kwargs,
                    )
                    if signal:
                        if coin not in coin_signal_counts:
                            coin_signal_counts[coin] = []
                        coin_signal_counts[coin].append((signal.signal_type, sname))
                        existing_conf = coin_signals.get(coin, (None, "", 0.0))[2]
                        if signal.confidence > existing_conf:
                            coin_signals[coin] = (signal, sname, signal.confidence)
                except Exception as e:
                    logger.debug(f"Strategy {strategy.name} error for {coin}: {e}")

        for coin, signals_list in coin_signal_counts.items():
            if len(signals_list) >= 2 and coin in coin_signals:
                buy_count = sum(1 for st, _ in signals_list if st == SignalType.BUY)
                sell_count = sum(1 for st, _ in signals_list if st == SignalType.SELL)
                dominant = max(buy_count, sell_count)
                if dominant >= 2:
                    signal, sname, conf = coin_signals[coin]
                    signal.confidence = min(0.95, signal.confidence + self.confluence_boost)
                    coin_signals[coin] = (signal, sname, signal.confidence)
                    logger.info(f"  CONFLUENCE: {coin} has {dominant}/{len(signals_list)} strategies agreeing, confidence boosted to {signal.confidence:.2f}")

        for coin in coin_signals:
            signal, sname, conf = coin_signals[coin]
            tf5 = self._get_5m_trend(coin)
            if tf5 != "unknown":
                sig_dir = signal.signal_type
                if (sig_dir == SignalType.BUY and tf5 == "uptrend") or \
                   (sig_dir == SignalType.SELL and tf5 == "downtrend"):
                    signal.confidence = min(0.95, signal.confidence + 0.05)
                elif (sig_dir == SignalType.BUY and tf5 == "downtrend") or \
                      (sig_dir == SignalType.SELL and tf5 == "uptrend"):
                    signal.confidence = max(0.0, signal.confidence - 0.05)
                coin_signals[coin] = (signal, sname, signal.confidence)

        sorted_signals = sorted(coin_signals.items(), key=lambda x: x[1][2], reverse=True)

        max_correlated = 2
        executed = 0
        for coin, (signal, strategy_name, confidence) in sorted_signals:
            if executed >= available_slots:
                break
            if executed == 0 and time.time() - self._last_trade_time < self.trade_cooldown:
                logger.info(f"  Trade cooldown active ({self.trade_cooldown}s), skipping")
                break
            if confidence >= self.min_confidence:
                test_signal = TradingSignal(
                    symbol=signal.symbol,
                    signal_type=signal.signal_type,
                    confidence=signal.confidence,
                    suggested_leverage=signal.suggested_leverage,
                    suggested_size=signal.suggested_size,
                    entry_price=signal.entry_price,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profit,
                    rationale=signal.rationale,
                    timestamp=signal.timestamp,
                )
                if not self._check_trend_filter(test_signal, coin):
                    logger.info(f"  Skipping {coin} {signal.signal_type.value}: counter-trend filter rejected (conf fell below min)")
                    continue
                signal.confidence = test_signal.confidence
                if signal.confidence < self.min_confidence:
                    logger.info(f"  Skipping {coin}: trend-adjusted confidence {signal.confidence:.2f} < {self.min_confidence:.2f}")
                    continue
                if signal.signal_type == SignalType.BUY and open_long_count >= max_correlated:
                    logger.info(f"  Skipping {coin} LONG: correlation limit ({open_long_count}/{max_correlated} long)")
                    continue
                if signal.signal_type == SignalType.SELL and open_short_count >= max_correlated:
                    logger.info(f"  Skipping {coin} SHORT: correlation limit ({open_short_count}/{max_correlated} short)")
                    continue
                self._execute_signal(signal, strategy_name=strategy_name, skip_trend_filter=True)
                executed += 1
                self._last_trade_time = time.time()
                if signal.signal_type == SignalType.BUY:
                    open_long_count += 1
                else:
                    open_short_count += 1

        remaining_slots = available_slots - executed
        if remaining_slots > 0:
            if self.iteration_count % 4 == 0:
                regimes = {c: self._detect_market_regime(c) for c in target_coins if c in available_prices}
                candle_counts = {c: len(self.candle_builder.candles.get(c, [])) + (1 if c in self.candle_builder.current_candle else 0) for c in target_coins}
                logger.info(f"  {remaining_slots} empty slots | Regimes: {regimes} | Candles: {candle_counts}")
            else:
                logger.info(f"  {remaining_slots} empty slots, waiting for strategy signals")

        if executed == 0 and remaining_slots <= 0:
            logger.info("No high-confidence signals found")

    def _auto_enter_positions(self, target_coins: List[str], available_prices: Dict[str, float],
                               balance: float, max_entries: int):
        entered = 0
        for coin in target_coins:
            if entered >= max_entries:
                break
            symbol = f"{coin}-PERP"
            if symbol in self.trading_engine.positions:
                continue
            if coin not in available_prices:
                continue

            current_price = available_prices[coin]

            df = self.candle_builder.get_ohlcv_df(coin, min_candles=5)
            ema20 = current_price
            ema50 = current_price
            if df is not None and len(df) >= 5:
                closes = df['close']
                if len(closes) >= 20:
                    ema20 = closes.rolling(20).mean().iloc[-1]
                if len(closes) >= 50:
                    ema50 = closes.rolling(50).mean().iloc[-1]

            if ema20 > ema50 or (ema20 == ema50 and df is None):
                side = SignalType.BUY
                sl = current_price * 0.997
                tp = current_price * 1.006
                rationale = f"Auto-LONG: EMA20={ema20:.2f} > EMA50={ema50:.2f}"
            elif ema20 < ema50:
                side = SignalType.SELL
                sl = current_price * 1.003
                tp = current_price * 0.994
                rationale = f"Auto-SHORT: EMA20={ema20:.2f} < EMA50={ema50:.2f}"
            else:
                continue

            size = (balance * 0.10) / current_price if balance > 0 else 0
            if size <= 0:
                continue

            logger.info(f"  Auto-entering {side.value} {coin} @ ${current_price:.2f} | EMA20={ema20:.2f} EMA50={ema50:.2f}")

            signal = TradingSignal(
                symbol=symbol, signal_type=side,
                confidence=0.65, suggested_leverage=20.0,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=rationale,
            )
            self._execute_signal(signal, strategy_name="Auto_Trend", skip_trend_filter=True)
            entered += 1

    def _execute_signal(self, signal: TradingSignal, strategy_name: str = "unknown", skip_trend_filter: bool = False):
        self.total_signals += 1
        coin = signal.symbol.replace("-PERP", "")
        current_price = self.current_prices.get(coin, signal.entry_price or 0)

        if current_price <= 0:
            return

        if not skip_trend_filter and not self._check_trend_filter(signal, coin):
            return

        regime = self._detect_market_regime(coin)
        regime_params = self.regime_sl_tp.get(regime, self.regime_sl_tp["unknown"])
        vol_mult = self.coin_vol_mult.get(coin, 1.0)
        regime_sl_pct = regime_params["sl"] * vol_mult
        regime_tp_pct = regime_params["tp"] * vol_mult
        regime_trail_pct = regime_params["trail"] * vol_mult

        if self.use_atr_sl_tp:
            stop_price, take_profit, trail_distance = self._get_atr_sl_tp(
                coin, current_price, signal.signal_type
            )
        else:
            stop_price = signal.stop_loss or (
                current_price * (1 - regime_sl_pct) if signal.signal_type == SignalType.BUY
                else current_price * (1 + regime_sl_pct)
            )

            take_profit = signal.take_profit or (
                current_price * (1 + regime_tp_pct) if signal.signal_type == SignalType.BUY
                else current_price * (1 - regime_tp_pct)
            )

        sl_pct_actual = abs(stop_price - current_price) / current_price
        tp_pct_actual = abs(take_profit - current_price) / current_price
        if sl_pct_actual < regime_sl_pct:
            if signal.signal_type == SignalType.BUY:
                stop_price = current_price * (1 - regime_sl_pct)
            else:
                stop_price = current_price * (1 + regime_sl_pct)
        if tp_pct_actual < regime_tp_pct:
            if signal.signal_type == SignalType.BUY:
                take_profit = current_price * (1 + regime_tp_pct)
            else:
                take_profit = current_price * (1 - regime_tp_pct)

        sl_pct_final = abs(stop_price - current_price) / current_price
        tp_pct_final = abs(take_profit - current_price) / current_price
        if sl_pct_final > self.max_sl_pct:
            if signal.signal_type == SignalType.BUY:
                stop_price = current_price * (1 - self.max_sl_pct)
            else:
                stop_price = current_price * (1 + self.max_sl_pct)
        if tp_pct_final > self.max_tp_pct:
            if signal.signal_type == SignalType.BUY:
                take_profit = current_price * (1 + self.max_tp_pct)
            else:
                take_profit = current_price * (1 - self.max_tp_pct)

        risk_mult = self.regime_risk_mult.get(regime, 1.0)
        effective_risk = self.max_risk_per_trade_pct * risk_mult

        size = self._compute_position_size(signal.symbol, current_price, stop_price, strategy_name=strategy_name)
        if risk_mult != 1.0:
            size *= risk_mult
        if signal.confidence >= self.high_confidence_threshold:
            size *= self.high_confidence_risk_mult
            logger.info(f"  High-confidence boost: {signal.confidence:.0%} >= {self.high_confidence_threshold:.0%}, size x{self.high_confidence_risk_mult}")
        if size <= 0:
            logger.info(f"  Signal for {coin} skipped: size too small")
            return

        leverage = min(signal.suggested_leverage, self.max_leverage)

        side = OrderSide.BUY if signal.signal_type == SignalType.BUY else OrderSide.SELL

        try:
            order = self.trading_engine.place_order(
                symbol=signal.symbol,
                side=side,
                size=size,
                order_type=OrderType.MARKET,
                leverage=leverage,
            )

            self.position_highest[coin] = current_price
            self.position_lowest[coin] = current_price
            self.position_entry_time[coin] = time.time()
            self.position_sl[coin] = stop_price
            self.position_tp[coin] = take_profit
            self.position_sl_at_entry[coin] = stop_price
            atr_at_entry = self._calculate_atr(coin)
            if atr_at_entry and atr_at_entry > 0:
                self.position_entry_atr[coin] = atr_at_entry
            self.position_regime[coin] = regime
            if self.use_atr_sl_tp:
                _, _, trail_distance = self._get_atr_sl_tp(coin, current_price, signal.signal_type)
                self.position_trail[coin] = trail_distance
            else:
                self.position_trail[coin] = current_price * regime_trail_pct
            self._last_signal_strategy[coin] = strategy_name

            direction = "LONG" if side == OrderSide.BUY else "SHORT"
            sl_pct = abs(stop_price - current_price) / current_price * 100
            tp_pct = abs(take_profit - current_price) / current_price * 100
            regime_timeout = self.regime_timeout.get(regime, 2400)
            logger.info(
                f"  >> OPENED {direction} {coin} | "
                f"Size: {size:.6f} @ ${current_price:,.2f} | "
                f"Lev: {leverage}x | Conf: {signal.confidence:.0%} | "
                f"SL: {sl_pct:.1f}% TP: {tp_pct:.1f}% | "
                f"Regime: {regime} Timeout: {regime_timeout}s | "
                f"Strategy: {strategy_name} | "
                f"{signal.rationale}"
            )

            pos = PositionState(
                symbol=signal.symbol,
                side="long" if side == OrderSide.BUY else "short",
                size=size,
                entry_price=current_price,
                current_price=current_price,
                leverage=leverage,
                stop_loss=stop_price,
                take_profit=take_profit,
            )
            self.state_manager.add_position(pos)

        except ValueError as e:
            logger.info(f"  Signal for {coin} rejected: {e}")
        except Exception as e:
            logger.error(f"  Order error for {coin}: {e}")

    def _check_trend_filter(self, signal: TradingSignal, coin: str) -> bool:
        regime = self._detect_market_regime(coin)
        if regime == "ranging":
            return True

        df = self.candle_builder.get_ohlcv_df(coin, min_candles=30)
        if df is None or len(df) < 30:
            return True

        closes = df['close']
        ema50 = closes.rolling(50).mean().iloc[-1] if len(closes) >= 50 else None
        ema20 = closes.rolling(20).mean().iloc[-1] if len(closes) >= 20 else None

        if ema20 is not None and ema50 is not None:
            if signal.signal_type == SignalType.SELL and ema20 > ema50 * 1.001:
                confidence_penalty = 0.08 if regime == "mild_trend" else 0.15
                signal.confidence = max(0.0, signal.confidence - confidence_penalty)
                if signal.confidence < 0.40:
                    return False
            elif signal.signal_type == SignalType.BUY and ema20 < ema50 * 0.999:
                confidence_penalty = 0.08 if regime == "mild_trend" else 0.15
                signal.confidence = max(0.0, signal.confidence - confidence_penalty)
                if signal.confidence < 0.40:
                    return False

        return True

    def _strategy_passes_regime_gate(self, sname: str, regime: str, coin: str) -> bool:
        if not self.regime_gate_enabled or not self._strategy_tracker:
            return True
        total_trades = len(self._strategy_tracker.trades.get(sname, []))
        if total_trades < self.regime_gate_grace_trades:
            return True
        ok, edge = self._strategy_tracker.edge_passes_hurdle(
            sname,
            regime=regime,
            min_trades=self.regime_gate_min_trades,
            min_expected_R=self.regime_gate_min_expected_R,
            min_win_rate=self.regime_gate_min_wr,
        )
        if not ok:
            logger.info(
                f"  RegimeGate blocked {sname} on {coin}/{regime}: "
                f"n={int(edge['n'])} wr={edge['wr_mean']:.2f} "
                f"E[R]={edge['expected_R_mean']:.3f}"
            )
        return ok

    def run(self):
        logger.info("Starting Moonshot Daemon main loop")

        self.running = True
        self.iteration_count = 0

        self._bootstrap_historical_candles()

        if self.ws_client:
            self.ws_client.start()

            logger.info("Waiting for WebSocket connection...")
            timeout = 10
            start = time.time()
            while not self.ws_client.connected and time.time() - start < timeout:
                time.sleep(0.1)

            if not self.ws_client.connected:
                logger.error("Failed to connect to Hyperliquid within timeout")
                self.running = False
                return

            logger.info("WebSocket connected, starting main loop")

        warmup_start = time.time()
        warmup_seconds = 10 if all(len(self.candle_builder.candles.get(c, [])) >= 30 for c in [s.replace("-PERP", "") for s in self.symbols]) else 120
        logger.info(f"Warming up price data ({warmup_seconds}s)...")
        while self.running and time.time() - warmup_start < warmup_seconds:
            time.sleep(1)
        logger.info(f"Warmup done. Prices: {len(self.current_prices)} coins")

        try:
            while self.running:
                loop_start = time.time()

                try:
                    self.check_opportunities()
                except Exception as e:
                    logger.error(f"Error in opportunity check: {e}", exc_info=True)

                self._sync_state_snapshot()

                if self.iteration_count % 10 == 0:
                    self._print_summary()

                if self._self_improver and time.time() - self._last_improvement_time > self._improvement_interval:
                    try:
                        self._run_self_improvement()
                        self._last_improvement_time = time.time()
                    except Exception as e:
                        logger.error(f"Error in self-improvement: {e}", exc_info=True)

                if time.time() - self._last_backtest_time > self._backtest_interval:
                    try:
                        self._run_rolling_backtest()
                        self._last_backtest_time = time.time()
                    except Exception as e:
                        logger.error(f"Error in rolling backtest: {e}", exc_info=True)

                if time.time() - self._last_funding_fetch > self._funding_fetch_interval:
                    try:
                        self._fetch_funding_rates()
                        self._last_funding_fetch = time.time()
                    except Exception as e:
                        logger.error(f"Error fetching funding rates: {e}", exc_info=True)

                elapsed = time.time() - loop_start
                sleep_time = max(0, self.check_interval - elapsed)

                if sleep_time > 0:
                    slept = 0
                    while slept < sleep_time and self.running:
                        time.sleep(min(1, sleep_time - slept))
                        slept += 1

        except KeyboardInterrupt:
            logger.info("Interrupted by user")
        except Exception as e:
            logger.error(f"Fatal error in main loop: {e}", exc_info=True)
        finally:
            self.running = False
            logger.info("Moonshot Daemon main loop stopped")

    def _print_summary(self):
        stats = self.trading_engine.get_stats_summary()
        strategy_summary = ""
        if self._strategy_tracker:
            strategy_summary = self._strategy_tracker.get_summary_str()
        logger.info(
            f"=== SUMMARY | Bal: {stats['balance']:.2f} | "
            f"Return: {stats['total_return_pct']:+.2f}% | "
            f"Trades: {stats['total_trades']} | "
            f"Win: {stats['win_rate']:.0f}% | "
            f"DD: {stats['max_drawdown_pct']:.1f}% | "
            f"Positions: {stats['current_positions']} ==="
        )
        if strategy_summary:
            logger.info(f"  STRATEGIES: {strategy_summary}")

    def _run_self_improvement(self):
        if not self._self_improver:
            return
        logger.info("Running self-improvement cycle...")
        stats = self.trading_engine.get_stats_summary()
        improvement_result = self._self_improver.run_improvement_cycle(
            current_stats=stats,
            trading_engine=self.trading_engine,
        )
        if improvement_result.get("changes_made"):
            for change in improvement_result["changes_made"]:
                logger.info(f"  IMPROVEMENT: {change}")
            self._apply_strategy_updates()
        if improvement_result.get("should_notify_opencode"):
            logger.info("  Triggering Opencode feedback for deeper analysis...")
            self._trigger_opencode_feedback(stats, improvement_result)

    def _run_rolling_backtest(self):
        if not self._strategy_tracker:
            return
        logger.info("Running rolling backtest on accumulated data...")
        results = {}
        for coin in [s.replace("-PERP", "") for s in self.symbols]:
            df = self.candle_builder.get_ohlcv_df(coin, min_candles=30)
            if df is None or len(df) < 30:
                continue
            current_price = self.current_prices.get(coin, 0)
            if current_price <= 0:
                continue
            for strategy in self.strategies:
                try:
                    signal = strategy.generate_signal(
                        symbol=f"{coin}-PERP",
                        ohlcv=df,
                        current_price=current_price,
                        account_balance=self.trading_engine.balance,
                    )
                    results[f"{coin}_{getattr(strategy, 'name', 'unknown')}"] = {
                        "signal": signal.signal_type.value if signal else "neutral",
                        "confidence": signal.confidence if signal else 0,
                    }
                except Exception:
                    pass
        if results:
            logger.info(f"  Rolling backtest: {len(results)} strategy-coin pairs evaluated")

    def _apply_strategy_updates(self):
        if not self._self_improver:
            return
        updates = self._self_improver.get_strategy_updates()
        for strategy_name, new_params in updates.items():
            for strategy in self.strategies:
                sname = getattr(strategy, 'name', strategy.__class__.__name__)
                if sname == strategy_name:
                    for param, value in new_params.items():
                        if hasattr(strategy, param):
                            old_value = getattr(strategy, param)
                            if old_value != value:
                                setattr(strategy, param, value)
                                logger.info(f"  Updated {sname}.{param}: {old_value} -> {value}")

    def _trigger_opencode_feedback(self, stats, improvement_result):
        try:
            from moonshot.daemon.self_improvement import OpencodeFeedback
            feedback = OpencodeFeedback(data_dir="state/moonshot")
            feedback.request_improvement(
                stats=stats,
                improvement_result=improvement_result,
                strategy_performance=self._strategy_tracker.get_all_performance() if self._strategy_tracker else {},
            )
        except Exception as e:
            logger.error(f"Opencode feedback failed: {e}")

    def stop(self):
        logger.info("Stopping Moonshot Daemon...")
        self.running = False

        if self.ws_client:
            self.ws_client.stop()

        self._print_summary()

        if self.state_manager:
            self.state_manager.stop_auto_save()

        logger.info("Moonshot Daemon stopped")

    def get_status(self) -> Dict:
        return {
            'running': self.running,
            'iteration_count': self.iteration_count,
            'websocket_connected': self.ws_client.connected if self.ws_client else False,
            'prices_available': len(self.current_prices),
            'current_prices': self.current_prices,
            'total_signals': self.total_signals,
            'total_trades': self.total_trades,
            'timestamp': time.time()
        }


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Moonshot 24/7 Trading Daemon')
    parser.add_argument('--balance', type=float, default=100.0, help='Initial balance')
    parser.add_argument('--interval', type=float, default=60.0, help='Check interval in seconds')
    parser.add_argument('--symbols', nargs='+', default=['BTC-PERP', 'ETH-PERP', 'SOL-PERP'])
    parser.add_argument('--live', action='store_true', help='Enable live trading (requires .env config)')
    args = parser.parse_args()

    daemon = MoonshotDaemon(
        initial_balance=args.balance,
        symbols=args.symbols,
        check_interval=args.interval,
        paper_trading=not args.live,
    )

    def signal_handler(signum, frame):
        logger.info(f"Received signal {signum}, shutting down...")
        daemon.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        daemon.initialize()
        daemon.run()
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        daemon.stop()
        raise


if __name__ == "__main__":
    main()