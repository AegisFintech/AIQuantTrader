"""
Hyperliquid Moonshot Engine - Aggressive Trading Strategies
High-risk, high-reward strategies for rapid account growth
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class SignalType(Enum):
    BUY = "buy"
    SELL = "sell"
    NEUTRAL = "neutral"


@dataclass
class TradingSignal:
    symbol: str
    signal_type: SignalType
    confidence: float  # 0.0 to 1.0
    suggested_leverage: float
    suggested_size: float
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    rationale: str = ""
    timestamp: float = 0.0
    
    def __post_init__(self):
        if self.timestamp == 0.0:
            import time
            self.timestamp = time.time()


class TechnicalIndicators:
    """Technical indicators for strategy calculations"""
    
    @staticmethod
    def ema(prices: pd.Series, period: int) -> pd.Series:
        """Exponential Moving Average"""
        return prices.ewm(span=period, adjust=False).mean()
    
    @staticmethod
    def sma(prices: pd.Series, period: int) -> pd.Series:
        """Simple Moving Average"""
        return prices.rolling(window=period).mean()
    
    @staticmethod
    def rsi(prices: pd.Series, period: int = 14) -> pd.Series:
        """Relative Strength Index"""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))
    
    @staticmethod
    def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """Average True Range"""
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=period).mean()
    
    @staticmethod
    def bollinger_bands(prices: pd.Series, period: int = 20, std_dev: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """Bollinger Bands - returns upper, middle, lower"""
        middle = TechnicalIndicators.sma(prices, period)
        std = prices.rolling(window=period).std()
        upper = middle + (std * std_dev)
        lower = middle - (std * std_dev)
        return upper, middle, lower
    
    @staticmethod
    def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """Average Directional Index"""
        # Calculate +DM and -DM
        plus_dm = high.diff()
        minus_dm = -low.diff()
        
        plus_dm[plus_dm < 0] = 0
        minus_dm[minus_dm < 0] = 0
        
        plus_dm[plus_dm <= minus_dm] = 0
        minus_dm[minus_dm <= plus_dm] = 0
        
        # Calculate ATR
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period).mean()
        
        # Calculate +DI and -DI
        plus_di = 100 * (plus_dm.rolling(window=period).mean() / atr)
        minus_di = 100 * (minus_dm.rolling(window=period).mean() / atr)
        
        # Calculate DX and ADX
        dx = (abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
        adx = dx.rolling(window=period).mean()
        
        return adx
    
    @staticmethod
    def macd(prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """MACD - returns macd_line, signal_line, histogram"""
        ema_fast = TechnicalIndicators.ema(prices, fast)
        ema_slow = TechnicalIndicators.ema(prices, slow)
        macd_line = ema_fast - ema_slow
        signal_line = TechnicalIndicators.ema(macd_line, signal)
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram


class AggressiveADXScalper:
    """
    Aggressive ADX Momentum Scalping Strategy
    High leverage entries on strong trend confirmations
    """
    
    def __init__(
        self,
        adx_period: int = 14,
        adx_threshold: float = 25.0,
        ema_fast: int = 9,
        ema_slow: int = 21,
        volume_threshold: float = 1.5,
        atr_period: int = 14,
        risk_reward: float = 2.0,
        max_leverage: float = 20.0
    ):
        self.name = "ADX_Momentum_Scalper"
        self.adx_period = adx_period
        self.adx_threshold = adx_threshold
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.volume_threshold = volume_threshold
        self.atr_period = atr_period
        self.risk_reward = risk_reward
        self.max_leverage = max_leverage
        
        # Indicators
        self.indicators = TechnicalIndicators()
    
    def generate_signal(
        self,
        symbol: str,
        ohlcv: pd.DataFrame,
        current_price: float,
        account_balance: float
    ) -> Optional[TradingSignal]:
        """Generate trading signal based on ADX momentum"""
        
        if len(ohlcv) < max(self.adx_period, self.ema_slow) + 10:
            return None
        
        # Calculate indicators
        closes = ohlcv['close']
        highs = ohlcv['high']
        lows = ohlcv['low']
        volumes = ohlcv.get('volume', pd.Series([1] * len(ohlcv)))
        
        # EMAs
        ema_fast = self.indicators.ema(closes, self.ema_fast)
        ema_slow = self.indicators.ema(closes, self.ema_slow)
        
        # ADX
        adx = self.indicators.adx(highs, lows, closes, self.adx_period)
        
        # ATR
        atr = self.indicators.atr(highs, lows, closes, self.atr_period)
        
        # Volume check
        avg_volume = volumes.rolling(window=20).mean().iloc[-1]
        current_volume = volumes.iloc[-1]
        volume_spike = current_volume > (avg_volume * self.volume_threshold) if avg_volume > 0 else True

        # Get current values
        current_adx = adx.iloc[-1] if not pd.isna(adx.iloc[-1]) else 0
        current_atr = atr.iloc[-1] if not pd.isna(atr.iloc[-1]) else current_price * 0.01
        fast_val = ema_fast.iloc[-1]
        slow_val = ema_slow.iloc[-1]
        prev_fast = ema_fast.iloc[-2] if len(ema_fast) > 1 else fast_val
        prev_slow = ema_slow.iloc[-2] if len(ema_slow) > 1 else slow_val

        # Check for crossover
        bullish_cross = (prev_fast <= prev_slow) and (fast_val > slow_val)
        bearish_cross = (prev_fast >= prev_slow) and (fast_val < slow_val)

        signal = None
        confidence = 0.0

        # Bullish signal conditions
        if bullish_cross and current_adx > self.adx_threshold:
            confidence = min(1.0, (current_adx / 50.0) + (0.2 if volume_spike else 0.0))

            # Calculate stop loss and take profit
            stop_loss = current_price - (current_atr * 1.0)
            take_profit = current_price + (current_atr * self.risk_reward)
            
            # Calculate position size based on risk
            risk_amount = account_balance * 0.03  # 5% risk per trade
            risk_per_unit = current_price - stop_loss
            position_size = risk_amount / risk_per_unit if risk_per_unit > 0 else 0
            
            # Apply leverage
            max_position_value = account_balance * self.max_leverage
            position_value = position_size * current_price
            
            if position_value > max_position_value:
                position_size = max_position_value / current_price
            
            leverage = min(self.max_leverage, position_value / account_balance)
            
            signal = TradingSignal(
                symbol=symbol,
                signal_type=SignalType.BUY,
                confidence=confidence,
                suggested_leverage=leverage,
                suggested_size=position_size,
                entry_price=current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                rationale=f"ADX Momentum Long: ADX={current_adx:.1f}, EMA9/21 cross, volume spike"
            )
        
        # Bearish signal conditions
        elif bearish_cross and current_adx > self.adx_threshold:
            confidence = min(1.0, (current_adx / 50.0) + (0.2 if volume_spike else 0.0))
            
            # Calculate stop loss and take profit
            stop_loss = current_price + (current_atr * 1.0)
            take_profit = current_price - (current_atr * self.risk_reward)
            
            # Calculate position size
            risk_amount = account_balance * 0.03
            risk_per_unit = stop_loss - current_price
            position_size = risk_amount / risk_per_unit if risk_per_unit > 0 else 0
            
            max_position_value = account_balance * self.max_leverage
            position_value = position_size * current_price
            
            if position_value > max_position_value:
                position_size = max_position_value / current_price
            
            leverage = min(self.max_leverage, position_value / account_balance)
            
            signal = TradingSignal(
                symbol=symbol,
                signal_type=SignalType.SELL,
                confidence=confidence,
                suggested_leverage=leverage,
                suggested_size=position_size,
                entry_price=current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                rationale=f"ADX Momentum Short: ADX={current_adx:.1f}, EMA9/21 cross, volume spike"
            )
        
        return signal


class AggressiveCryptoScalper:
    """
    Ultra-aggressive scalping strategy for crypto
    Captures small moves with high frequency
    """
    
    def __init__(
        self,
        ema_fast: int = 3,
        ema_slow: int = 8,
        rsi_period: int = 7,
        rsi_overbought: float = 70,
        rsi_oversold: float = 30,
        volume_threshold: float = 1.3,
        min_move_pct: float = 0.001,  # 0.1%
        max_leverage: float = 50.0
    ):
        self.name = "Aggressive_Scalper"
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold
        self.volume_threshold = volume_threshold
        self.min_move_pct = min_move_pct
        self.max_leverage = max_leverage
        
        self.indicators = TechnicalIndicators()
        self.last_trade_time = 0
        self.min_trade_interval = 5
    
    def generate_signal(
        self,
        symbol: str,
        ohlcv: pd.DataFrame,
        current_price: float,
        account_balance: float
    ) -> Optional[TradingSignal]:
        """Generate scalping signals"""
        
        import time
        
        # Check minimum trade interval
        if time.time() - self.last_trade_time < self.min_trade_interval:
            return None
        
        if len(ohlcv) < max(self.ema_slow, self.rsi_period) + 5:
            return None
        
        closes = ohlcv['close']
        highs = ohlcv['high']
        lows = ohlcv['low']
        volumes = ohlcv.get('volume', pd.Series([1] * len(ohlcv)))
        
        # Calculate indicators
        ema_fast = self.indicators.ema(closes, self.ema_fast)
        ema_slow = self.indicators.ema(closes, self.ema_slow)
        rsi = self.indicators.rsi(closes, self.rsi_period)
        
        # Volume analysis
        avg_volume = volumes.rolling(window=20).mean().iloc[-1] if len(volumes) >= 20 else 1
        current_volume = volumes.iloc[-1]
        volume_spike = current_volume > (avg_volume * self.volume_threshold) if avg_volume > 0 else True

        # Get current values
        fast_val = ema_fast.iloc[-1]
        slow_val = ema_slow.iloc[-1]
        current_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50

        prev_fast = ema_fast.iloc[-2] if len(ema_fast) > 1 else fast_val
        prev_slow = ema_slow.iloc[-2] if len(ema_slow) > 1 else slow_val

        # Calculate price change
        price_change_pct = abs(closes.iloc[-1] - closes.iloc[-5]) / closes.iloc[-5] if len(closes) >= 5 else 0

        signal = None

        # Bullish scalping setup
        if (fast_val > slow_val and prev_fast <= prev_slow and  # EMA cross
            current_rsi < self.rsi_overbought and  # Not overbought
            price_change_pct >= self.min_move_pct):  # Sufficient movement

            confidence = 0.6 + (0.2 if volume_spike else 0) + (0.1 if current_rsi < 50 else 0)
            confidence = min(1.0, confidence)

            stop_loss = current_price * 0.997
            take_profit = current_price * 1.009
            
            # Max position with high leverage
            max_position_value = account_balance * self.max_leverage * 0.9  # Use 90% of available
            position_size = max_position_value / current_price
            
            signal = TradingSignal(
                symbol=symbol,
                signal_type=SignalType.BUY,
                confidence=confidence,
                suggested_leverage=self.max_leverage,
                suggested_size=position_size,
                entry_price=current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                rationale=f"Scalp Long: EMA{self.ema_fast}/{self.ema_slow} cross, RSI={current_rsi:.1f}, volume spike"
            )
            
            self.last_trade_time = time.time()
        
        # Bearish scalping setup
        elif (fast_val < slow_val and prev_fast >= prev_slow and  # EMA cross
              current_rsi > self.rsi_oversold and  # Not oversold
              price_change_pct >= self.min_move_pct):

            confidence = 0.6 + (0.2 if volume_spike else 0) + (0.1 if current_rsi > 50 else 0)
            confidence = min(1.0, confidence)

            stop_loss = current_price * 1.003
            take_profit = current_price * 0.991
            
            max_position_value = account_balance * self.max_leverage * 0.9
            position_size = max_position_value / current_price
            
            signal = TradingSignal(
                symbol=symbol,
                signal_type=SignalType.SELL,
                confidence=confidence,
                suggested_leverage=self.max_leverage,
                suggested_size=position_size,
                entry_price=current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                rationale=f"Scalp Short: EMA{self.ema_fast}/{self.ema_slow} cross, RSI={current_rsi:.1f}, volume spike"
            )
            
            self.last_trade_time = time.time()
        
        return signal


class BreakoutHunter:
    """
    High-probability breakout strategy
    Captures explosive moves when price breaks key levels
    """
    
    def __init__(
        self,
        lookback_period: int = 20,
        consolidation_threshold: float = 0.03,  # 3% range
        volume_multiplier: float = 2.0,
        breakout_confirmation: float = 0.005,  # 0.5% beyond level
        ema_filter_period: int = 50,
        max_leverage: float = 30.0
    ):
        self.name = "Breakout_Hunter"
        self.lookback_period = lookback_period
        self.consolidation_threshold = consolidation_threshold
        self.volume_multiplier = volume_multiplier
        self.breakout_confirmation = breakout_confirmation
        self.ema_filter_period = ema_filter_period
        self.max_leverage = max_leverage
        
        self.indicators = TechnicalIndicators()
        self.last_breakout_time = 0
        self.cooldown_period = 60
    
    def generate_signal(
        self,
        symbol: str,
        ohlcv: pd.DataFrame,
        current_price: float,
        account_balance: float
    ) -> Optional[TradingSignal]:
        """Generate breakout signals"""
        
        import time
        
        # Cooldown check
        if time.time() - self.last_breakout_time < self.cooldown_period:
            return None
        
        if len(ohlcv) < self.lookback_period + self.ema_filter_period:
            return None
        
        highs = ohlcv['high']
        lows = ohlcv['low']
        closes = ohlcv['close']
        volumes = ohlcv.get('volume', pd.Series([1] * len(ohlcv)))
        
        # Find consolidation range
        recent_highs = highs.tail(self.lookback_period)
        recent_lows = lows.tail(self.lookback_period)
        
        resistance = recent_highs.max()
        support = recent_lows.min()
        range_size = (resistance - support) / support
        
        # Check if in consolidation
        in_consolidation = range_size < self.consolidation_threshold
        
        if not in_consolidation:
            return None
        
        # Volume analysis
        avg_volume = volumes.tail(self.lookback_period).mean() if len(volumes) > 0 else 1
        current_volume = volumes.iloc[-1]
        volume_confirmed = current_volume > (avg_volume * self.volume_multiplier) if avg_volume > 0 else True
        
        # EMA filter
        ema_filter = self.indicators.ema(closes, self.ema_filter_period)
        above_ema = current_price > ema_filter.iloc[-1]
        below_ema = current_price < ema_filter.iloc[-1]
        
        # Breakout detection
        breakout_up = current_price > (resistance * (1 + self.breakout_confirmation))
        breakout_down = current_price < (support * (1 - self.breakout_confirmation))
        
        signal = None
        
        # Bullish breakout
        if breakout_up and volume_confirmed and above_ema:
            self.last_breakout_time = time.time()
            
            confidence = 0.7
            if current_price > ema_filter.iloc[-1] * 1.02:
                confidence += 0.15
            if volume_confirmed:
                confidence += 0.1
            
            stop_loss = support * 0.995  # Just below support
            take_profit = current_price + ((current_price - stop_loss) * 3)  # 3:1 R:R
            
            # Large position with leverage
            max_position_value = account_balance * self.max_leverage * 0.95
            position_size = max_position_value / current_price
            
            signal = TradingSignal(
                symbol=symbol,
                signal_type=SignalType.BUY,
                confidence=confidence,
                suggested_leverage=self.max_leverage,
                suggested_size=position_size,
                entry_price=current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                rationale=f"Breakout Long: R/S range {range_size*100:.1f}%, volume {current_volume/avg_volume:.1f}x, EMA50 filter"
            )
        
        # Bearish breakout
        elif breakout_down and volume_confirmed and below_ema:
            self.last_breakout_time = time.time()
            
            confidence = 0.7
            if current_price < ema_filter.iloc[-1] * 0.98:
                confidence += 0.15
            if volume_confirmed:
                confidence += 0.1
            
            stop_loss = resistance * 1.005  # Just above resistance
            take_profit = current_price - ((stop_loss - current_price) * 3)
            
            max_position_value = account_balance * self.max_leverage * 0.95
            position_size = max_position_value / current_price
            
            signal = TradingSignal(
                symbol=symbol,
                signal_type=SignalType.SELL,
                confidence=confidence,
                suggested_leverage=self.max_leverage,
                suggested_size=position_size,
                entry_price=current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                rationale=f"Breakout Short: R/S range {range_size*100:.1f}%, volume {current_volume/avg_volume:.1f}x, EMA50 filter"
            )
        
        return signal


class MeanReversionBandit:
    """
    High-probability mean reversion strategy
    Exploits price extremes with Bollinger Bands and RSI
    """
    
    def __init__(
        self,
        bb_period: int = 20,
        bb_std_dev: float = 2.5,  # Wider bands for crypto
        rsi_period: int = 14,
        rsi_overbought: float = 75,
        rsi_oversold: float = 25,
        volume_threshold: float = 1.2,
        adx_filter_max: float = 30.0,  # Only trade when ADX < 30 (ranging to mild trend)
        max_leverage: float = 15.0
    ):
        self.name = "Mean_Reversion_Bandit"
        self.bb_period = bb_period
        self.bb_std_dev = bb_std_dev
        self.rsi_period = rsi_period
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold
        self.volume_threshold = volume_threshold
        self.adx_filter_max = adx_filter_max
        self.max_leverage = max_leverage
        
        self.indicators = TechnicalIndicators()
        self.last_trade_time = 0
        self.cooldown_period = 30
    
    def generate_signal(
        self,
        symbol: str,
        ohlcv: pd.DataFrame,
        current_price: float,
        account_balance: float
    ) -> Optional[TradingSignal]:
        """Generate mean reversion signals"""
        
        import time
        
        # Cooldown
        if time.time() - self.last_trade_time < self.cooldown_period:
            return None
        
        if len(ohlcv) < max(self.bb_period, self.rsi_period) + 10:
            return None
        
        closes = ohlcv['close']
        highs = ohlcv['high']
        lows = ohlcv['low']
        volumes = ohlcv.get('volume', pd.Series([1] * len(ohlcv)))
        
        # Calculate indicators
        upper, middle, lower = self.indicators.bollinger_bands(
            closes, self.bb_period, self.bb_std_dev
        )
        rsi = self.indicators.rsi(closes, self.rsi_period)
        adx = self.indicators.adx(highs, lows, closes, 14)
        
        # Volume check
        avg_volume = volumes.tail(20).mean() if len(volumes) >= 20 else 1
        current_volume = volumes.iloc[-1]
        volume_ok = current_volume > (avg_volume * self.volume_threshold) if avg_volume > 0 else True
        
        # Get current values
        current_upper = upper.iloc[-1]
        current_middle = middle.iloc[-1]
        current_lower = lower.iloc[-1]
        current_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50
        current_adx = adx.iloc[-1] if not pd.isna(adx.iloc[-1]) else 25
        
        # Check ranging market condition
        is_ranging = current_adx < self.adx_filter_max
        
        signal = None
        
        # Long signal: Price at or below lower band with oversold RSI
        if (current_price <= current_lower * 1.002 and  # Within 0.2% of lower band
            current_rsi < self.rsi_oversold and
            is_ranging and volume_ok):
            
            self.last_trade_time = time.time()
            
            confidence = 0.6 + ((self.rsi_oversold - current_rsi) / 100) * 0.3
            if volume_ok:
                confidence += 0.1
            
            stop_loss = current_lower * 0.995  # Below lower band
            take_profit = current_middle  # Target middle band
            
            position_value = account_balance * self.max_leverage * 0.8
            position_size = position_value / current_price
            
            signal = TradingSignal(
                symbol=symbol,
                signal_type=SignalType.BUY,
                confidence=confidence,
                suggested_leverage=self.max_leverage,
                suggested_size=position_size,
                entry_price=current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                rationale=f"Mean Reversion Long: BB lower touch, RSI={current_rsi:.1f}, ranging market"
            )
        
        # Short signal: Price at or above upper band with overbought RSI
        elif (current_price >= current_upper * 0.998 and  # Within 0.2% of upper band
              current_rsi > self.rsi_overbought and
              is_ranging and volume_ok):
            
            self.last_trade_time = time.time()
            
            confidence = 0.6 + ((current_rsi - self.rsi_overbought) / 100) * 0.3
            if volume_ok:
                confidence += 0.1
            
            stop_loss = current_upper * 1.005  # Above upper band
            take_profit = current_middle  # Target middle band
            
            position_value = account_balance * self.max_leverage * 0.8
            position_size = position_value / current_price
            
            signal = TradingSignal(
                symbol=symbol,
                signal_type=SignalType.SELL,
                confidence=confidence,
                suggested_leverage=self.max_leverage,
                suggested_size=position_size,
                entry_price=current_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                rationale=f"Mean Reversion Short: BB upper touch, RSI={current_rsi:.1f}, ranging market"
            )
        
        return signal


class SmartMoneyConcepts:
    """
    Smart Money Concepts (SMC) Strategy
    Detects order blocks, fair value gaps, and breaker blocks
    Trades with institutional flow direction
    """

    def __init__(
        self,
        ob_lookback: int = 20,
        fvg_min_size_pct: float = 0.002,
        fvg_fill_tolerance: float = 0.5,
        volume_threshold: float = 1.2,
        max_leverage: float = 5.0,
    ):
        self.name = "SMC_OrderFlow"
        self.ob_lookback = ob_lookback
        self.fvg_min_size_pct = fvg_min_size_pct
        self.fvg_fill_tolerance = fvg_fill_tolerance
        self.volume_threshold = volume_threshold
        self.max_leverage = max_leverage
        self.indicators = TechnicalIndicators()
        self.last_signal_time = {}

    def _detect_bullish_order_block(self, ohlcv: pd.DataFrame) -> Optional[Dict]:
        closes = ohlcv['close']
        opens = ohlcv['open']
        highs = ohlcv['high']
        lows = ohlcv['low']
        lookback = min(self.ob_lookback, len(ohlcv) - 1)

        for i in range(len(ohlcv) - 1, max(len(ohlcv) - lookback, 2), -1):
            if closes.iloc[i] > highs.iloc[i - 1]:
                if i - 2 >= 0:
                    ob_candle = i - 2
                    if closes.iloc[ob_candle] < opens.iloc[ob_candle]:
                        return {
                            "type": "bullish_ob",
                            "high": highs.iloc[ob_candle],
                            "low": lows.iloc[ob_candle],
                            "index": ob_candle,
                        }
        return None

    def _detect_bearish_order_block(self, ohlcv: pd.DataFrame) -> Optional[Dict]:
        closes = ohlcv['close']
        opens = ohlcv['open']
        highs = ohlcv['high']
        lows = ohlcv['low']
        lookback = min(self.ob_lookback, len(ohlcv) - 1)

        for i in range(len(ohlcv) - 1, max(len(ohlcv) - lookback, 2), -1):
            if closes.iloc[i] < lows.iloc[i - 1]:
                if i - 2 >= 0:
                    ob_candle = i - 2
                    if closes.iloc[ob_candle] > opens.iloc[ob_candle]:
                        return {
                            "type": "bearish_ob",
                            "high": highs.iloc[ob_candle],
                            "low": lows.iloc[ob_candle],
                            "index": ob_candle,
                        }
        return None

    def _detect_fvg(self, ohlcv: pd.DataFrame) -> Optional[Dict]:
        highs = ohlcv['high']
        lows = ohlcv['low']
        if len(ohlcv) < 3:
            return None

        i = len(ohlcv) - 1
        if i < 2:
            return None

        gap_up = lows.iloc[i] - highs.iloc[i - 2]
        gap_down = lows.iloc[i - 2] - highs.iloc[i]

        if gap_up > 0:
            size_pct = gap_up / ohlcv['close'].iloc[i]
            if size_pct >= self.fvg_min_size_pct:
                return {"type": "bullish_fvg", "top": lows.iloc[i], "bottom": highs.iloc[i - 2], "size_pct": size_pct}

        if gap_down > 0:
            size_pct = gap_down / ohlcv['close'].iloc[i]
            if size_pct >= self.fvg_min_size_pct:
                return {"type": "bearish_fvg", "top": lows.iloc[i - 2], "bottom": highs.iloc[i], "size_pct": size_pct}

        return None

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame,
                        current_price: float, account_balance: float) -> Optional[TradingSignal]:
        import time as _time
        now = _time.time()
        coin = symbol.replace("-PERP", "")
        if coin in self.last_signal_time and now - self.last_signal_time[coin] < 5:
            return None

        if len(ohlcv) < self.ob_lookback + 5:
            return None

        closes = ohlcv['close']
        volumes = ohlcv.get('volume', pd.Series([1] * len(ohlcv)))
        avg_vol = volumes.rolling(20).mean().iloc[-1] if len(volumes) >= 20 else 1
        cur_vol = volumes.iloc[-1]
        vol_ok = cur_vol > avg_vol * self.volume_threshold if avg_vol > 0 else True

        ema50 = closes.rolling(50).mean().iloc[-1] if len(closes) >= 50 else current_price
        uptrend = current_price > ema50
        downtrend = current_price < ema50

        signal = None

        bull_ob = self._detect_bullish_order_block(ohlcv)
        fvg = self._detect_fvg(ohlcv)

        if bull_ob and uptrend and current_price <= bull_ob["high"] * 1.002:
            confidence = 0.55
            if fvg and fvg["type"] == "bullish_fvg":
                confidence += 0.15
            if vol_ok:
                confidence += 0.1
            confidence = min(0.85, confidence)

            sl = bull_ob["low"] * 0.997
            tp = current_price * 1.009
            size = (account_balance * 0.12) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.BUY,
                confidence=confidence, suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"SMC LONG: Bullish OB @ {bull_ob['high']:.2f}{' + FVG' if fvg and fvg['type'] == 'bullish_fvg' else ''} uptrend"
            )
            self.last_signal_time[coin] = now

        bear_ob = self._detect_bearish_order_block(ohlcv)
        if not signal and bear_ob and downtrend and current_price >= bear_ob["low"] * 0.998:
            confidence = 0.55
            if fvg and fvg["type"] == "bearish_fvg":
                confidence += 0.15
            if vol_ok:
                confidence += 0.1
            confidence = min(0.85, confidence)

            sl = bear_ob["high"] * 1.003
            tp = current_price * 0.991
            size = (account_balance * 0.12) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.SELL,
                confidence=confidence, suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"SMC SHORT: Bearish OB @ {bear_ob['low']:.2f}{' + FVG' if fvg and fvg['type'] == 'bearish_fvg' else ''} downtrend"
            )
            self.last_signal_time[coin] = now

        if not signal and fvg and vol_ok:
            if fvg["type"] == "bullish_fvg" and uptrend:
                if current_price <= fvg["top"] and current_price >= fvg["bottom"]:
                    confidence = 0.5 + min(0.2, fvg["size_pct"] * 20)
                    sl = fvg["bottom"] * 0.997
                    tp = current_price * 1.009
                    size = (account_balance * 0.10) / current_price
                    signal = TradingSignal(
                        symbol=symbol, signal_type=SignalType.BUY,
                        confidence=confidence, suggested_leverage=self.max_leverage,
                        suggested_size=size, entry_price=current_price,
                        stop_loss=sl, take_profit=tp,
                        rationale=f"SMC LONG: FVG fill zone {fvg['bottom']:.2f}-{fvg['top']:.2f}, size={fvg['size_pct']*100:.2f}%"
                    )
                    self.last_signal_time[coin] = now
            elif fvg["type"] == "bearish_fvg" and downtrend:
                if current_price >= fvg["bottom"] and current_price <= fvg["top"]:
                    confidence = 0.5 + min(0.2, fvg["size_pct"] * 20)
                    sl = fvg["top"] * 1.003
                    tp = current_price * 0.991
                    size = (account_balance * 0.10) / current_price
                    signal = TradingSignal(
                        symbol=symbol, signal_type=SignalType.SELL,
                        confidence=confidence, suggested_leverage=self.max_leverage,
                        suggested_size=size, entry_price=current_price,
                        stop_loss=sl, take_profit=tp,
                        rationale=f"SMC SHORT: FVG fill zone {fvg['bottom']:.2f}-{fvg['top']:.2f}, size={fvg['size_pct']*100:.2f}%"
                    )
                    self.last_signal_time[coin] = now

        return signal


class FibonacciRetracement:
    """
    Fibonacci Retracement Strategy
    Identifies key Fib levels (0.382, 0.5, 0.618, 0.786) as support/resistance
    Trades bounces off Fib levels with trend confirmation
    """

    FIB_LEVELS = [0.236, 0.382, 0.5, 0.618, 0.786]

    def __init__(
        self,
        swing_lookback: int = 30,
        confluence_threshold: float = 0.001,
        max_leverage: float = 5.0,
    ):
        self.name = "Fibonacci_Retracement"
        self.swing_lookback = swing_lookback
        self.confluence_threshold = confluence_threshold
        self.max_leverage = max_leverage
        self.indicators = TechnicalIndicators()
        self.last_signal_time = {}
        self.cooldown_seconds = 15

    def _find_swing_points(self, ohlcv: pd.DataFrame) -> Tuple[Optional[float], Optional[float]]:
        lookback = min(self.swing_lookback, len(ohlcv))
        recent = ohlcv.tail(lookback)
        swing_high = recent['high'].max()
        swing_low = recent['low'].min()
        return swing_high, swing_low

    def _get_nearest_fib_level(self, price: float, swing_high: float, swing_low: float) -> Tuple[Optional[float], Optional[str]]:
        rng = swing_high - swing_low
        if rng <= 0:
            return None, None

        for level in self.FIB_LEVELS:
            fib_price = swing_high - rng * level
            if abs(price - fib_price) / fib_price < self.confluence_threshold:
                return fib_price, f"{level:.3f}"
        return None, None

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame,
                        current_price: float, account_balance: float) -> Optional[TradingSignal]:
        import time as _time
        now = _time.time()
        coin = symbol.replace("-PERP", "")
        if coin in self.last_signal_time and now - self.last_signal_time[coin] < self.cooldown_seconds:
            return None

        if len(ohlcv) < self.swing_lookback + 5:
            return None

        closes = ohlcv['close']
        volumes = ohlcv.get('volume', pd.Series([1] * len(ohlcv)))
        avg_vol = volumes.rolling(20).mean().iloc[-1] if len(volumes) >= 20 else 1
        cur_vol = volumes.iloc[-1]
        vol_ok = cur_vol > avg_vol * 0.8

        swing_high, swing_low = self._find_swing_points(ohlcv)
        if swing_high is None or swing_low is None:
            return None

        ema20 = closes.rolling(20).mean().iloc[-1] if len(closes) >= 20 else current_price
        ema50 = closes.rolling(50).mean().iloc[-1] if len(closes) >= 50 else current_price
        uptrend = ema20 > ema50
        downtrend = ema20 < ema50

        rsi = self.indicators.rsi(closes, 14)
        cur_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50

        fib_price, fib_name = self._get_nearest_fib_level(current_price, swing_high, swing_low)
        if fib_price is None:
            return None

        signal = None

        if uptrend and vol_ok and current_price >= fib_price * 0.997 and current_price <= fib_price * 1.003:
            confidence = 0.55 + (0.1 if fib_name in ("0.618", "0.786") else 0.05)
            if cur_rsi < 45:
                confidence += 0.1
            if vol_ok:
                confidence += 0.05
            confidence = min(0.85, confidence)

            sl = fib_price * 0.997
            tp = current_price * 1.009
            size = (account_balance * 0.10) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.BUY,
                confidence=confidence, suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"Fib LONG: Bounce off {fib_name} ({fib_price:.2f}), RSI={cur_rsi:.0f}, uptrend"
            )
            self.last_signal_time[coin] = now

        elif downtrend and vol_ok and current_price <= fib_price * 1.003 and current_price >= fib_price * 0.997:
            confidence = 0.55 + (0.1 if fib_name in ("0.382", "0.236") else 0.05)
            if cur_rsi > 55:
                confidence += 0.1
            if vol_ok:
                confidence += 0.05
            confidence = min(0.85, confidence)

            sl = fib_price * 1.003
            tp = current_price * 0.991
            size = (account_balance * 0.10) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.SELL,
                confidence=confidence, suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"Fib SHORT: Rejection at {fib_name} ({fib_price:.2f}), RSI={cur_rsi:.0f}, downtrend"
            )
            self.last_signal_time[coin] = now

        return signal


class VWAPStrategy:
    """
    VWAP (Volume Weighted Average Price) Strategy
    Uses VWAP as dynamic support/resistance with deviation bands
    Trades price reverting to VWAP mean or breaking away with volume
    """

    def __init__(
        self,
        vwap_std_mult: float = 2.0,
        reversion_band: float = 1.5,
        volume_threshold: float = 1.0,
        max_leverage: float = 5.0,
    ):
        self.name = "VWAP_Mean"
        self.vwap_std_mult = vwap_std_mult
        self.reversion_band = reversion_band
        self.volume_threshold = volume_threshold
        self.max_leverage = max_leverage
        self.indicators = TechnicalIndicators()
        self.last_signal_time = {}

    def _calculate_vwap(self, ohlcv: pd.DataFrame) -> Tuple[float, float, float, float]:
        typical = (ohlcv['high'] + ohlcv['low'] + ohlcv['close']) / 3
        vol = ohlcv.get('volume', pd.Series([1.0] * len(ohlcv)))

        has_real_volume = vol.sum() > len(vol) * 0.5 and vol.std() > 0.01

        if has_real_volume:
            window = min(60, len(ohlcv))
            cum_tp_vol = (typical * vol).rolling(window).sum()
            cum_vol = vol.rolling(window).sum()
            vwap = cum_tp_vol / cum_vol
        else:
            vwap = typical.rolling(20).mean()

        vwap_val = vwap.iloc[-1] if not pd.isna(vwap.iloc[-1]) else ohlcv['close'].iloc[-1]

        deviations = typical - vwap
        std_dev = deviations.rolling(20).std().iloc[-1] if len(ohlcv) >= 20 else 0
        if pd.isna(std_dev) or std_dev == 0:
            std_dev = abs(ohlcv['close'].iloc[-1]) * 0.005

        upper_band = vwap_val + std_dev * self.vwap_std_mult
        lower_band = vwap_val - std_dev * self.vwap_std_mult
        upper_rev = vwap_val + std_dev * self.reversion_band
        lower_rev = vwap_val - std_dev * self.reversion_band

        return vwap_val, upper_band, lower_band, upper_rev, lower_rev

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame,
                        current_price: float, account_balance: float) -> Optional[TradingSignal]:
        import time as _time
        now = _time.time()
        coin = symbol.replace("-PERP", "")
        if coin in self.last_signal_time and now - self.last_signal_time[coin] < 5:
            return None

        if len(ohlcv) < 25:
            return None

        closes = ohlcv['close']
        volumes = ohlcv.get('volume', pd.Series([1] * len(ohlcv)))
        avg_vol = volumes.rolling(20).mean().iloc[-1] if len(volumes) >= 20 else 1
        cur_vol = volumes.iloc[-1]
        vol_ok = cur_vol > avg_vol * self.volume_threshold if avg_vol > 0 else True

        vwap_val, upper_band, lower_band, upper_rev, lower_rev = self._calculate_vwap(ohlcv)

        ema20 = closes.rolling(20).mean().iloc[-1] if len(closes) >= 20 else current_price
        uptrend = current_price > ema20 and current_price > vwap_val
        downtrend = current_price < ema20 and current_price < vwap_val

        signal = None

        if current_price <= lower_rev and uptrend:
            confidence = 0.55 + (0.15 if vol_ok else 0.0)
            if current_price <= lower_band:
                confidence += 0.1
            confidence = min(0.85, confidence)

            sl = lower_band * 0.997
            tp = vwap_val
            size = (account_balance * 0.10) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.BUY,
                confidence=confidence, suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"VWAP LONG: Price below lower band ({current_price:.2f} vs VWAP {vwap_val:.2f}), reversion to mean"
            )
            self.last_signal_time[coin] = now

        elif current_price >= upper_rev and downtrend:
            confidence = 0.55 + (0.15 if vol_ok else 0.0)
            if current_price >= upper_band:
                confidence += 0.1
            confidence = min(0.85, confidence)

            sl = upper_band * 1.003
            tp = vwap_val
            size = (account_balance * 0.10) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.SELL,
                confidence=confidence, suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"VWAP SHORT: Price above upper band ({current_price:.2f} vs VWAP {vwap_val:.2f}), reversion to mean"
            )
            self.last_signal_time[coin] = now

        elif vol_ok:
            if current_price > vwap_val and closes.iloc[-2] <= vwap_val and uptrend:
                confidence = 0.55 + 0.1
                sl = vwap_val * 0.997
                tp = current_price * 1.009
                size = (account_balance * 0.08) / current_price
                signal = TradingSignal(
                    symbol=symbol, signal_type=SignalType.BUY,
                    confidence=min(0.8, confidence), suggested_leverage=self.max_leverage,
                    suggested_size=size, entry_price=current_price,
                    stop_loss=sl, take_profit=tp,
                    rationale=f"VWAP LONG: Price crossed above VWAP ({vwap_val:.2f}) with volume, uptrend"
                )
                self.last_signal_time[coin] = now
            elif current_price < vwap_val and closes.iloc[-2] >= vwap_val and downtrend:
                confidence = 0.55 + 0.1
                sl = vwap_val * 1.003
                tp = current_price * 0.991
                size = (account_balance * 0.08) / current_price
                signal = TradingSignal(
                    symbol=symbol, signal_type=SignalType.SELL,
                    confidence=min(0.8, confidence), suggested_leverage=self.max_leverage,
                    suggested_size=size, entry_price=current_price,
                    stop_loss=sl, take_profit=tp,
                    rationale=f"VWAP SHORT: Price crossed below VWAP ({vwap_val:.2f}) with volume, downtrend"
                )
                self.last_signal_time[coin] = now

        return signal


class MACDStrategy:
    """
    MACD Divergence & Crossover Strategy
    Trades MACD line/signal crossovers and bullish/bearish divergences
    Uses RSI and EMA trend filter for confirmation
    """

    def __init__(
        self,
        macd_fast: int = 12,
        macd_slow: int = 26,
        macd_signal: int = 9,
        rsi_period: int = 14,
        max_leverage: float = 5.0,
    ):
        self.name = "MACD_Divergence"
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.rsi_period = rsi_period
        self.max_leverage = max_leverage
        self.indicators = TechnicalIndicators()
        self.last_signal_time = {}

    def _detect_divergence(self, ohlcv: pd.DataFrame, macd_line: pd.Series) -> Tuple[bool, bool]:
        closes = ohlcv['close']
        if len(closes) < 10 or len(macd_line) < 10:
            return False, False

        bull_div = False
        bear_div = False

        recent_closes = closes.iloc[-10:]
        recent_macd = macd_line.iloc[-10:]

        close_low_idx = recent_closes.idxmin()
        close_high_idx = recent_closes.idxmax()

        macd_slices_before_low = macd_line.iloc[:macd_line.index.get_loc(close_low_idx) + 1] if close_low_idx in macd_line.index else recent_macd
        macd_slices_before_high = macd_line.iloc[:macd_line.index.get_loc(close_high_idx) + 1] if close_high_idx in macd_line.index else recent_macd

        if len(macd_slices_before_low) > 1:
            prev_macd_low = macd_slices_before_low.min()
            if recent_closes.iloc[-1] <= recent_closes.mean() and recent_macd.iloc[-1] > prev_macd_low:
                bull_div = True

        if len(macd_slices_before_high) > 1:
            prev_macd_high = macd_slices_before_high.max()
            if recent_closes.iloc[-1] >= recent_closes.mean() and recent_macd.iloc[-1] < prev_macd_high:
                bear_div = True

        return bull_div, bear_div

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame,
                        current_price: float, account_balance: float) -> Optional[TradingSignal]:
        import time as _time
        now = _time.time()
        coin = symbol.replace("-PERP", "")
        if coin in self.last_signal_time and now - self.last_signal_time[coin] < 5:
            return None

        if len(ohlcv) < self.macd_slow + self.macd_signal + 5:
            return None

        closes = ohlcv['close']
        volumes = ohlcv.get('volume', pd.Series([1] * len(ohlcv)))

        macd_line, signal_line, histogram = self.indicators.macd(
            closes, self.macd_fast, self.macd_slow, self.macd_signal
        )

        rsi = self.indicators.rsi(closes, self.rsi_period)
        cur_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50

        ema20 = closes.rolling(20).mean().iloc[-1] if len(closes) >= 20 else current_price
        ema50 = closes.rolling(50).mean().iloc[-1] if len(closes) >= 50 else current_price
        uptrend = ema20 > ema50
        downtrend = ema20 < ema50

        cur_macd = macd_line.iloc[-1]
        cur_signal = signal_line.iloc[-1]
        cur_hist = histogram.iloc[-1]
        prev_macd = macd_line.iloc[-2] if len(macd_line) > 1 else cur_macd
        prev_signal = signal_line.iloc[-2] if len(signal_line) > 1 else cur_signal
        prev_hist = histogram.iloc[-2] if len(histogram) > 1 else cur_hist

        bull_cross = prev_macd <= prev_signal and cur_macd > cur_signal
        bear_cross = prev_macd >= prev_signal and cur_macd < cur_signal

        hist_flip_bull = prev_hist < 0 and cur_hist > 0
        hist_flip_bear = prev_hist > 0 and cur_hist < 0

        bull_div, bear_div = self._detect_divergence(ohlcv, macd_line)

        signal = None

        if bull_cross and uptrend and cur_rsi < 65:
            confidence = 0.55 + (0.1 if cur_hist > 0 else 0.0) + (0.1 if cur_rsi < 50 else 0.0)
            confidence = min(0.85, confidence)
            sl = current_price * 0.997
            tp = current_price * 1.009
            size = (account_balance * 0.12) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.BUY,
                confidence=confidence, suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"MACD LONG: Bullish crossover, RSI={cur_rsi:.0f}, uptrend confirmed"
            )
            self.last_signal_time[coin] = now

        elif bear_cross and downtrend and cur_rsi > 35:
            confidence = 0.55 + (0.1 if cur_hist < 0 else 0.0) + (0.1 if cur_rsi > 50 else 0.0)
            confidence = min(0.85, confidence)
            sl = current_price * 1.003
            tp = current_price * 0.991
            size = (account_balance * 0.12) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.SELL,
                confidence=confidence, suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"MACD SHORT: Bearish crossover, RSI={cur_rsi:.0f}, downtrend confirmed"
            )
            self.last_signal_time[coin] = now

        elif bull_div and uptrend:
            confidence = 0.6 + (0.1 if hist_flip_bull else 0.0)
            confidence = min(0.85, confidence)
            sl = current_price * 0.997
            tp = current_price * 1.009
            size = (account_balance * 0.10) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.BUY,
                confidence=confidence, suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"MACD LONG: Bullish divergence, histogram {'flipping' if hist_flip_bull else 'rising'}, uptrend"
            )
            self.last_signal_time[coin] = now

        elif bear_div and downtrend:
            confidence = 0.6 + (0.1 if hist_flip_bear else 0.0)
            confidence = min(0.85, confidence)
            sl = current_price * 1.003
            tp = current_price * 0.991
            size = (account_balance * 0.10) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.SELL,
                confidence=confidence, suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"MACD SHORT: Bearish divergence, histogram {'flipping' if hist_flip_bear else 'falling'}, downtrend"
            )
            self.last_signal_time[coin] = now

        return signal


class CrossAssetLeadLag:
    """
    Cross-Asset Lead-Lag Strategy
    BTC leads ETH/SOL by 1-3 minutes on 1m charts.
    When BTC makes a significant move and ETH/SOL haven't followed,
    trade the laggard in the same direction.
    """

    def __init__(self, lookback: int = 5, btc_move_threshold: float = 0.001,
                 lag_threshold: float = 0.3, max_leverage: float = 5.0):
        self.name = "Cross_Lead_Lag"
        self.lookback = lookback
        self.btc_move_threshold = btc_move_threshold
        self.lag_threshold = lag_threshold
        self.max_leverage = max_leverage
        self.last_signal_time = {}

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame,
                        current_price: float, account_balance: float,
                        btc_ohlcv: pd.DataFrame = None) -> Optional[TradingSignal]:
        import time as _time
        now = _time.time()
        coin = symbol.replace("-PERP", "")
        if coin == "BTC":
            return None
        if coin in self.last_signal_time and now - self.last_signal_time[coin] < 60:
            return None
        if btc_ohlcv is None or len(btc_ohlcv) < self.lookback + 5:
            return None
        if len(ohlcv) < self.lookback + 5:
            return None

        btc_closes = btc_ohlcv['close']
        closes = ohlcv['close']

        btc_change = (btc_closes.iloc[-1] - btc_closes.iloc[-self.lookback]) / btc_closes.iloc[-self.lookback]
        asset_change = (closes.iloc[-1] - closes.iloc[-self.lookback]) / closes.iloc[-self.lookback]

        if abs(btc_change) < self.btc_move_threshold:
            return None

        vol_mult = 1.5 if coin == "SOL" else 1.0
        signal = None

        if btc_change > 0 and asset_change < btc_change * self.lag_threshold:
            confidence = 0.58 + min(0.25, abs(btc_change) * 50)
            sl = current_price * (1 - 0.005 * vol_mult)
            tp = current_price * (1 + abs(btc_change) * 1.5)
            size = (account_balance * 0.10) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.BUY,
                confidence=min(0.85, confidence), suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"Lead-Lag LONG: BTC +{btc_change*100:.2f}%, {coin} only +{asset_change*100:.2f}%"
            )
            self.last_signal_time[coin] = now

        elif btc_change < 0 and asset_change > btc_change * self.lag_threshold:
            confidence = 0.58 + min(0.25, abs(btc_change) * 50)
            sl = current_price * (1 + 0.005 * vol_mult)
            tp = current_price * (1 - abs(btc_change) * 1.5)
            size = (account_balance * 0.10) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.SELL,
                confidence=min(0.85, confidence), suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"Lead-Lag SHORT: BTC {btc_change*100:.2f}%, {coin} only {asset_change*100:.2f}%"
            )
            self.last_signal_time[coin] = now

        return signal


class FundingRateContrarian:
    """
    Extreme funding rates predict price reversals.
    Uses z-score of recent funding rates instead of fixed thresholds.
    When z-score > 2.0, the funding rate is statistically extreme.
    """

    def __init__(self, high_funding_threshold: float = 0.001,
                 low_funding_threshold: float = -0.0005, max_leverage: float = 5.0,
                 zscore_threshold: float = 2.0):
        self.name = "Funding_Contrarian"
        self.high_funding_threshold = high_funding_threshold
        self.low_funding_threshold = low_funding_threshold
        self.max_leverage = max_leverage
        self.zscore_threshold = zscore_threshold
        self.funding_history: Dict[str, List[float]] = {}
        self.last_signal_time = {}

    def _compute_zscore(self, coin: str, rate: float) -> float:
        if coin not in self.funding_history:
            self.funding_history[coin] = []
        self.funding_history[coin].append(rate)
        if len(self.funding_history[coin]) > 100:
            self.funding_history[coin] = self.funding_history[coin][-100:]
        history = self.funding_history[coin]
        if len(history) < 5:
            return 0.0
        import numpy as np
        arr = np.array(history)
        mean = arr.mean()
        std = arr.std()
        if std < 1e-10:
            return 0.0
        return float((rate - mean) / std)

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame = None,
                        current_price: float = 0, account_balance: float = 0,
                        funding_rate: float = None) -> Optional[TradingSignal]:
        import time as _time
        now = _time.time()
        coin = symbol.replace("-PERP", "")
        if coin in self.last_signal_time and now - self.last_signal_time[coin] < 300:
            return None
        if funding_rate is None:
            return None
        if current_price <= 0:
            return None

        zscore = self._compute_zscore(coin, funding_rate)

        signal = None

        if zscore > self.zscore_threshold or funding_rate > self.high_funding_threshold:
            confidence = 0.58 + min(0.3, abs(zscore) * 0.05)
            sl = current_price * 1.007
            tp = current_price * 0.986
            size = (account_balance * 0.08) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.SELL,
                confidence=min(0.85, confidence), suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"Funding SHORT: Rate={funding_rate*100:.4f}% z={zscore:.1f} (overleveraged longs)"
            )
            self.last_signal_time[coin] = now

        elif zscore < -self.zscore_threshold or funding_rate < self.low_funding_threshold:
            confidence = 0.58 + min(0.3, abs(zscore) * 0.05)
            sl = current_price * 0.993
            tp = current_price * 1.014
            size = (account_balance * 0.08) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.BUY,
                confidence=min(0.85, confidence), suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"Funding LONG: Rate={funding_rate*100:.4f}% z={zscore:.1f} (overleveraged shorts)"
            )
            self.last_signal_time[coin] = now

        return signal


class VolatilitySqueeze:
    """
    Keltner Channel + Bollinger Band squeeze detection.
    When BBs contract inside KC, low-vol regime detected.
    Trade the breakout when price breaks above/below BB.
    Volume spike requirement removed (tick-derived volume is unreliable).
    """

    def __init__(self, bb_period: int = 20, bb_std: float = 2.0,
                 kc_atr_mult: float = 1.5, atr_period: int = 10,
                 max_leverage: float = 5.0):
        self.name = "Vol_Squeeze"
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.kc_atr_mult = kc_atr_mult
        self.atr_period = atr_period
        self.max_leverage = max_leverage
        self.indicators = TechnicalIndicators()
        self.last_signal_time = {}

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame,
                        current_price: float, account_balance: float) -> Optional[TradingSignal]:
        import time as _time
        now = _time.time()
        coin = symbol.replace("-PERP", "")
        if coin in self.last_signal_time and now - self.last_signal_time[coin] < 60:
            return None

        if len(ohlcv) < self.bb_period + 10:
            return None

        closes = ohlcv['close']
        highs = ohlcv['high']
        lows = ohlcv['low']

        upper_bb, middle_bb, lower_bb = self.indicators.bollinger_bands(closes, self.bb_period, self.bb_std)
        atr = self.indicators.atr(highs, lows, closes, self.atr_period)
        ema = self.indicators.ema(closes, 20)

        if pd.isna(upper_bb.iloc[-1]) or pd.isna(atr.iloc[-1]) or pd.isna(ema.iloc[-1]):
            return None

        upper_kc = ema + atr * self.kc_atr_mult
        lower_kc = ema - atr * self.kc_atr_mult

        bb_width = upper_bb.iloc[-1] - lower_bb.iloc[-1]
        kc_width = upper_kc.iloc[-1] - lower_kc.iloc[-1]

        if kc_width <= 0:
            return None

        is_squeezed = bb_width < kc_width

        if not is_squeezed:
            return None

        prev_close = closes.iloc[-2]
        signal = None

        if current_price > upper_bb.iloc[-1] and prev_close <= upper_bb.iloc[-2]:
            sl = middle_bb.iloc[-1] * 0.993
            tp = current_price + (current_price - middle_bb.iloc[-1]) * 1.5
            size = (account_balance * 0.10) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.BUY,
                confidence=0.60, suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"Squeeze Break LONG: BB/KC squeeze, breakout above BB"
            )
            self.last_signal_time[coin] = now

        elif current_price < lower_bb.iloc[-1] and prev_close >= lower_bb.iloc[-2]:
            sl = middle_bb.iloc[-1] * 1.007
            tp = current_price - (middle_bb.iloc[-1] - current_price) * 1.5
            size = (account_balance * 0.10) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.SELL,
                confidence=0.60, suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"Squeeze Break SHORT: BB/KC squeeze, breakdown below BB"
            )
            self.last_signal_time[coin] = now

        return signal


class EMARibbonPullback:
    """
    EMA Ribbon Pullback Strategy
    When EMA 8/13/21 are properly stacked (trend confirmed),
    trade pullbacks to the EMA 21 zone.
    Trending regime strategy that complements Quick_Momentum.
    """

    def __init__(self, ema_fast: int = 8, ema_mid: int = 13, ema_slow: int = 21,
                 rsi_period: int = 14, max_leverage: float = 5.0):
        self.name = "EMA_Ribbon"
        self.ema_fast = ema_fast
        self.ema_mid = ema_mid
        self.ema_slow = ema_slow
        self.rsi_period = rsi_period
        self.max_leverage = max_leverage
        self.indicators = TechnicalIndicators()
        self.last_signal_time = {}

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame,
                        current_price: float, account_balance: float) -> Optional[TradingSignal]:
        import time as _time
        now = _time.time()
        coin = symbol.replace("-PERP", "")
        if coin in self.last_signal_time and now - self.last_signal_time[coin] < 60:
            return None

        min_len = max(self.ema_slow, self.rsi_period) + 5
        if len(ohlcv) < min_len:
            return None

        closes = ohlcv['close']
        ema8 = self.indicators.ema(closes, self.ema_fast)
        ema13 = self.indicators.ema(closes, self.ema_mid)
        ema21 = self.indicators.ema(closes, self.ema_slow)
        rsi = self.indicators.rsi(closes, self.rsi_period)

        e8 = ema8.iloc[-1]
        e13 = ema13.iloc[-1]
        e21 = ema21.iloc[-1]
        cur_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50

        bullish_stack = e8 > e13 > e21
        bearish_stack = e8 < e13 < e21

        signal = None

        if bullish_stack:
            dist_to_21 = (current_price - e21) / e21
            if 0 <= dist_to_21 <= 0.003 and cur_rsi < 60:
                confidence = 0.58 + min(0.20, (e8 - e21) / e21 * 100)
                sl = e21 * 0.993
                tp = current_price * 1.014
                size = (account_balance * 0.10) / current_price
                signal = TradingSignal(
                    symbol=symbol, signal_type=SignalType.BUY,
                    confidence=min(0.85, confidence), suggested_leverage=self.max_leverage,
                    suggested_size=size, entry_price=current_price,
                    stop_loss=sl, take_profit=tp,
                    rationale=f"Ribbon Pullback LONG: EMA8/13/21 stacked, pullback to EMA21 zone, RSI={cur_rsi:.0f}"
                )
                self.last_signal_time[coin] = now

        elif bearish_stack:
            dist_to_21 = (e21 - current_price) / e21
            if 0 <= dist_to_21 <= 0.003 and cur_rsi > 40:
                confidence = 0.58 + min(0.20, (e21 - e8) / e21 * 100)
                sl = e21 * 1.007
                tp = current_price * 0.986
                size = (account_balance * 0.10) / current_price
                signal = TradingSignal(
                    symbol=symbol, signal_type=SignalType.SELL,
                    confidence=min(0.85, confidence), suggested_leverage=self.max_leverage,
                    suggested_size=size, entry_price=current_price,
                    stop_loss=sl, take_profit=tp,
                    rationale=f"Ribbon Pullback SHORT: EMA8/13/21 stacked bearish, pullback to EMA21, RSI={cur_rsi:.0f}"
                )
                self.last_signal_time[coin] = now

        return signal


class VWAPReversion:
    """
    VWAP Mean Reversion Strategy
    When price deviates significantly from VWAP, trade back toward it.
    Ranging regime strategy - catches overextensions from fair value.
    """

    def __init__(self, vwap_period: int = 20, deviation_threshold: float = 0.003,
                 rsi_period: int = 14, max_leverage: float = 5.0):
        self.name = "VWAP_Revert"
        self.vwap_period = vwap_period
        self.deviation_threshold = deviation_threshold
        self.rsi_period = rsi_period
        self.max_leverage = max_leverage
        self.indicators = TechnicalIndicators()
        self.last_signal_time = {}

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame,
                        current_price: float, account_balance: float) -> Optional[TradingSignal]:
        import time as _time
        now = _time.time()
        coin = symbol.replace("-PERP", "")
        if coin in self.last_signal_time and now - self.last_signal_time[coin] < 60:
            return None

        if len(ohlcv) < self.vwap_period + 5:
            return None

        closes = ohlcv['close']
        volumes = ohlcv.get('volume', pd.Series([1.0] * len(ohlcv)))

        typical_price = (closes * volumes).rolling(self.vwap_period).sum()
        cum_volume = volumes.rolling(self.vwap_period).sum()
        vwap = typical_price / cum_volume.replace(0, 1)

        if pd.isna(vwap.iloc[-1]):
            return None

        cur_vwap = float(vwap.iloc[-1])
        if cur_vwap <= 0:
            return None

        deviation = (current_price - cur_vwap) / cur_vwap
        rsi = self.indicators.rsi(closes, self.rsi_period)
        cur_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50

        signal = None

        if deviation > self.deviation_threshold and cur_rsi > 60:
            confidence = 0.58 + min(0.25, deviation * 50)
            sl = current_price * 1.007
            tp = cur_vwap
            size = (account_balance * 0.08) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.SELL,
                confidence=min(0.85, confidence), suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"VWAP Revert SHORT: price +{deviation*100:.2f}% above VWAP ({cur_vwap:.2f}), RSI={cur_rsi:.0f}"
            )
            self.last_signal_time[coin] = now

        elif deviation < -self.deviation_threshold and cur_rsi < 40:
            confidence = 0.58 + min(0.25, abs(deviation) * 50)
            sl = current_price * 0.993
            tp = cur_vwap
            size = (account_balance * 0.08) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.BUY,
                confidence=min(0.85, confidence), suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"VWAP Revert LONG: price {deviation*100:.2f}% below VWAP ({cur_vwap:.2f}), RSI={cur_rsi:.0f}"
            )
            self.last_signal_time[coin] = now

        return signal


class QQEStrategy:
    """
    QQE (Quantitative Qualitative Estimation) Strategy
    
    Computes a smoothed RSI, then measures the volatility of that smoothed RSI
    (similar to ATR applied to RSI). Dynamic threshold bands are created from
    this volatility. Crossovers of the QQE line through these bands produce
    cleaner signals than raw RSI because the volatility-adaptive bands filter
    noise and adjust to market conditions automatically.
    
    Signal logic:
    - BUY: QQE line crosses above the lower threshold band (oversold reversal)
    - SELL: QQE line crosses below the upper threshold band (overbought reversal)
    - Confirmation: EMA trend filter + optional RSI divergence
    """

    def __init__(
        self,
        rsi_period: int = 14,
        rsi_smoothing: int = 5,
        threshold_mult: float = 1.618,
        ema_fast: int = 8,
        ema_slow: int = 21,
        max_leverage: float = 5.0,
    ):
        self.name = "QQE"
        self.rsi_period = rsi_period
        self.rsi_smoothing = rsi_smoothing
        self.threshold_mult = threshold_mult
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.max_leverage = max_leverage
        self.indicators = TechnicalIndicators()
        self.last_signal_time = {}

    def _compute_qqe(self, closes: pd.Series) -> Tuple[pd.Series, pd.Series]:
        delta = closes.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)

        alpha = 1.0 / self.rsi_period
        avg_gain = gain.ewm(alpha=alpha, adjust=False).mean()
        avg_loss = loss.ewm(alpha=alpha, adjust=False).mean()

        rs = avg_gain / avg_loss.replace(0, 1e-10)
        rsi = 100.0 - (100.0 / (1.0 + rs))

        smooth_alpha = 1.0 / self.rsi_smoothing
        smoothed_rsi = rsi.ewm(alpha=smooth_alpha, adjust=False).mean()

        rsi_change = smoothed_rsi.diff().abs()
        smoothed_delta = rsi_change.ewm(alpha=smooth_alpha, adjust=False).mean()

        trailing = pd.Series(np.nan, index=closes.index)
        trail_val = smoothed_rsi.iloc[0] if not pd.isna(smoothed_rsi.iloc[0]) else 50.0

        for i in range(len(smoothed_rsi)):
            cur_rsi = smoothed_rsi.iloc[i]
            cur_delta = smoothed_delta.iloc[i]
            if pd.isna(cur_rsi) or pd.isna(cur_delta):
                trailing.iloc[i] = trail_val
                continue

            step = max(cur_delta * self.threshold_mult, 5.0)

            if cur_rsi > trail_val:
                new_trail = cur_rsi - step
                if new_trail > trail_val:
                    trail_val = new_trail
            else:
                new_trail = cur_rsi + step
                if new_trail < trail_val:
                    trail_val = new_trail

            trailing.iloc[i] = trail_val

        return smoothed_rsi, trailing

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame,
                        current_price: float, account_balance: float) -> Optional[TradingSignal]:
        import time as _time
        now = _time.time()
        coin = symbol.replace("-PERP", "")
        if coin in self.last_signal_time and now - self.last_signal_time[coin] < 30:
            return None

        min_len = max(self.rsi_period, self.ema_slow) + 10
        if len(ohlcv) < min_len:
            return None

        closes = ohlcv['close']

        qqe_line, trailing = self._compute_qqe(closes)

        if pd.isna(qqe_line.iloc[-1]) or pd.isna(trailing.iloc[-1]):
            return None
        if len(qqe_line) < 3 or pd.isna(qqe_line.iloc[-2]) or pd.isna(trailing.iloc[-2]):
            return None

        cur_qqe = qqe_line.iloc[-1]
        prev_qqe = qqe_line.iloc[-2]
        cur_trail = trailing.iloc[-1]
        prev_trail = trailing.iloc[-2]

        ema_f = self.indicators.ema(closes, self.ema_fast)
        ema_s = self.indicators.ema(closes, self.ema_slow)
        fast_val = ema_f.iloc[-1]
        slow_val = ema_s.iloc[-1]

        rsi_raw = self.indicators.rsi(closes, self.rsi_period)
        cur_rsi = rsi_raw.iloc[-1] if not pd.isna(rsi_raw.iloc[-1]) else 50

        bullish_cross = prev_qqe <= prev_trail and cur_qqe > cur_trail
        bearish_cross = prev_qqe >= prev_trail and cur_qqe < cur_trail

        qqe_above_trail = cur_qqe > cur_trail
        qqe_oversold = cur_qqe < 35 and cur_qqe > prev_qqe and qqe_above_trail
        qqe_overbought = cur_qqe > 65 and cur_qqe < prev_qqe and not qqe_above_trail

        signal = None

        if bullish_cross or qqe_oversold:
            confidence = 0.55
            if fast_val > slow_val:
                confidence += 0.10
            if cur_qqe < 30:
                confidence += 0.10
            if cur_rsi < 40:
                confidence += 0.05
            if bullish_cross:
                confidence += 0.05
            confidence = min(0.90, confidence)

            sl = current_price * 0.993
            tp = current_price * 1.014
            size = (account_balance * 0.10) / current_price
            sig_type = "cross" if bullish_cross else "zone"
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.BUY,
                confidence=confidence, suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"QQE LONG ({sig_type}): QQE={cur_qqe:.1f} trail={cur_trail:.1f}, RSI={cur_rsi:.0f}"
            )
            self.last_signal_time[coin] = now

        elif bearish_cross or qqe_overbought:
            confidence = 0.55
            if fast_val < slow_val:
                confidence += 0.10
            if cur_qqe > 70:
                confidence += 0.10
            if cur_rsi > 60:
                confidence += 0.05
            if bearish_cross:
                confidence += 0.05
            confidence = min(0.90, confidence)

            sl = current_price * 1.007
            tp = current_price * 0.986
            size = (account_balance * 0.10) / current_price
            sig_type = "cross" if bearish_cross else "zone"
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.SELL,
                confidence=confidence, suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"QQE SHORT ({sig_type}): QQE={cur_qqe:.1f} trail={cur_trail:.1f}, RSI={cur_rsi:.0f}"
            )
            self.last_signal_time[coin] = now

        return signal


class MomentumExhaustion:
    """
    Momentum Exhaustion Strategy
    When RSI is extended AND recent candle ranges are shrinking,
    the move is running out of steam - trade the reversal.
    Catches overextended moves that are decelerating.
    """

    def __init__(self, rsi_period: int = 14, rsi_ob: float = 75, rsi_os: float = 25,
                 lookback: int = 3, max_leverage: float = 5.0):
        self.name = "Mom_Exhaust"
        self.rsi_period = rsi_period
        self.rsi_ob = rsi_ob
        self.rsi_os = rsi_os
        self.lookback = lookback
        self.max_leverage = max_leverage
        self.indicators = TechnicalIndicators()
        self.last_signal_time = {}

    def generate_signal(self, symbol: str, ohlcv: pd.DataFrame,
                        current_price: float, account_balance: float) -> Optional[TradingSignal]:
        import time as _time
        now = _time.time()
        coin = symbol.replace("-PERP", "")
        if coin in self.last_signal_time and now - self.last_signal_time[coin] < 60:
            return None

        if len(ohlcv) < max(self.rsi_period, self.lookback) + 10:
            return None

        closes = ohlcv['close']
        highs = ohlcv['high']
        lows = ohlcv['low']

        rsi = self.indicators.rsi(closes, self.rsi_period)
        cur_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50

        ranges = highs - lows
        recent_ranges = ranges.iloc[-self.lookback:]
        avg_range = recent_ranges.mean()
        prev_avg = ranges.iloc[-(self.lookback * 2):-self.lookback].mean() if len(ranges) >= self.lookback * 2 else avg_range

        shrinking = avg_range < prev_avg * 0.85 if prev_avg > 0 else False

        if not shrinking:
            return None

        signal = None

        if cur_rsi > self.rsi_ob:
            confidence = 0.58 + min(0.25, (cur_rsi - self.rsi_ob) / 100)
            sl = current_price * 1.007
            tp = current_price * 0.986
            size = (account_balance * 0.08) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.SELL,
                confidence=min(0.85, confidence), suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"Exhaustion SHORT: RSI={cur_rsi:.0f}, candle ranges shrinking, momentum fading"
            )
            self.last_signal_time[coin] = now

        elif cur_rsi < self.rsi_os:
            confidence = 0.58 + min(0.25, (self.rsi_os - cur_rsi) / 100)
            sl = current_price * 0.993
            tp = current_price * 1.014
            size = (account_balance * 0.08) / current_price
            signal = TradingSignal(
                symbol=symbol, signal_type=SignalType.BUY,
                confidence=min(0.85, confidence), suggested_leverage=self.max_leverage,
                suggested_size=size, entry_price=current_price,
                stop_loss=sl, take_profit=tp,
                rationale=f"Exhaustion LONG: RSI={cur_rsi:.0f}, candle ranges shrinking, selling exhausted"
            )
            self.last_signal_time[coin] = now

        return signal


# Export all strategies
__all__ = [
    'AggressiveADXScalper',
    'AggressiveCryptoScalper',
    'BreakoutHunter',
    'MeanReversionBandit',
    'SmartMoneyConcepts',
    'FibonacciRetracement',
    'VWAPStrategy',
    'MACDStrategy',
    'CrossAssetLeadLag',
    'FundingRateContrarian',
    'VolatilitySqueeze',
    'EMARibbonPullback',
    'VWAPReversion',
    'MomentumExhaustion',
    'QQEStrategy',
    'TradingSignal',
    'SignalType',
    'TechnicalIndicators'
]