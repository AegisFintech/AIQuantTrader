#property strict
#property description "FinRobot MT5 bridge and demo auto trader for XAUUSD."
#property version "1.41"

#include <Trade/Trade.mqh>
#include "BridgeIO.mqh"
#include "SmartMoney.mqh"
#include "RiskManagement.mqh"

input string CommandFile = "finrobot_commands.csv";
input string AckFile = "finrobot_acks.csv";
input string StatusFile = "finrobot_status.json";
input string PositionsFile = "finrobot_positions.csv";
input string DealsFile = "finrobot_deals.csv";
input string StrategyProfileFile = "finrobot_strategy_profile.csv";
input string EntryPauseFile = "finrobot_entry_pause.flag";
input string ResearchBarsFile = "finrobot_export_XAUUSD_M1.tsv";
input int ResearchBarsCount = 100000;
input int ResearchBarsExportIntervalSeconds = 21600;
input bool EnableRuntimeStrategyProfile = true;
input int PollSeconds = 1;
input int MagicNumber = 20260522;
input int DefaultDeviationPoints = 30;
input bool AllowTrading = true;
input bool AutoTradeMT5 = true;
input string AutoSymbols = "XAUUSD";
input ENUM_TIMEFRAMES AutoTimeframe = PERIOD_M1;
input double XauBaseLot = 0.05;                // Fallback only when daily risk sizing is disabled
input double MaxLotPerTrade = 50.0;            // Demo ceiling; the 1% stop-risk cap remains primary
input double MaxLotPerTradeXAUUSD = 50.0;      // Allows 1% risk sizing on the high-equity XAU demo account
input double HighConfluenceLotMultiplier = 3.0;
input int MinSmcConfluenceScore = 3;
input int MinSmcConfluenceScoreXAUUSD = 4;
input int HighConfluenceScore = 5;
input bool UseDailyRiskLotSizing = true;
input double DailyRiskPerTradeFraction = 0.0100;   // 1.00% of equity per trade
input double DailyLossLimitFraction = 0.01;        // 1.00% of equity daily cap
input int LossStreakPauseCount = 0;                // 0 disables loss-streak pause
input double BadDayDownshiftFraction = 0.50;       // Multiplier after broker-day closed PnL turns negative
input double MaxRecentDrawdownFraction = 0.0;      // 0 disables earlier drawdown pause
input bool BlackoutEnabled = false;
input string BlackoutFile = "finrobot_blackout.csv";
input double MaxAtrRegimeMultiplier = 0.0;         // 0 disables ATR regime pause
input bool AutoClosePositionsWithoutStops = true;
input bool DisableWeakStrategySignals = true;
input int MaxAutoPositionsPerSymbol = 2;
input int MaxAutoPositionsXAUUSD = 2;
input int MaxSameDirectionPositionsPerSymbol = 2;
input int MinSecondsBetweenTrades = 300;
input int MinSecondsBetweenTradesXAUUSD = 180;
input int FastEmaPeriod = 9;
input int SlowEmaPeriod = 21;
input int TrendEmaPeriod = 50;
input int RsiPeriod = 14;
input int AtrPeriod = 14;
input double AtrImpulseMultiplier = 0.12;
input double StopAtrMultiplier = 1.2;
input double TakeProfitAtrMultiplier = 2.4;
input double MaxSpreadPointsXAUUSD = 80.0;
input bool EnableSmartMoneyGates = true;
input bool EnableXauAutoTrading = true;
input int SmcLookbackBars = 48;
input double FvgMinAtrMultiplier = 0.30;
input double DiscountThreshold = 0.38;
input double PremiumThreshold = 0.62;
input double LiquiditySweepAtrMultiplier = 0.30;
input double MinTrendSlopeAtrMultiplier = 0.04;
input bool EnableXauRsiReversion = false;
input bool EnableXauAtrImpulse = true;
input bool EnableSessionGating = true;
input bool EnableXauWeekdayMarketHours = true;
input int LondonStartHour = 7;
input int LondonEndHour = 11;
input int NyStartHour = 13;
input int NyEndHour = 17;
input bool EnableAdxRegimeFilter = true;
input int AdxPeriod = 14;
input double AdxMinThreshold = 20.0;
input bool EnableMacdHistogramAlignment = false;
input bool EnableDynamicBreakEven = true;
input double BreakEvenRrRatio = 1.0;
input double BreakEvenExtraPoints = 10.0;

string EaVersion = "unknown";
string EaGitSha = "";

CTrade trade;
const double MAX_EFFECTIVE_RISK_PER_TRADE_FRACTION = 0.0100;
int lastCommandId = 0;
int commandFileErrLogged = 0;
int timerTicks = 0;
string managedSymbols[];
string lastSignals[];
datetime lastTradeTimes[];
int signalTelemetryDay = 0;
int filledSignalCounts[];
int outsideSessionRejectCounts[];
int marketClosedRejectCounts[];
int spreadRejectCounts[];
int noSignalCounts[];
int smcRejectCounts[];
int directionRejectCounts[];
int pdaRejectCounts[];
int positionRejectCounts[];
int orderRejectCounts[];
int recoveryRejectCounts[];
int moneyManagementDay = 0;
double dailyEquitySnapshot = 0.0;
double todayClosedPnlCache = 0.0;
datetime lastMoneyManagementUpdate = 0;
string riskCloseAttemptTickets = "";
int riskCloseAttemptDay = 0;
string activeProfileName = "compiled_defaults";
int activeRiskTier = 0;
int runtimeProfileLoaded = 0;
string runtimeProfileMessage = "compiled defaults";
ENUM_TIMEFRAMES runtimeAutoTimeframe = PERIOD_M1;
double runtimeAtrImpulseMultiplier = 0.12;
double runtimeMaxLotPerTradeXAUUSD = 50.0;
double runtimeHighConfluenceLotMultiplier = 3.0;
int runtimeMinSmcConfluenceScoreXAUUSD = 4;
int runtimeHighConfluenceScore = 5;
double runtimeDailyRiskPerTradeFraction = 0.0100;
double runtimeDailyLossLimitFraction = 0.01;
int runtimeMaxAutoPositionsXAUUSD = 2;
int runtimeMaxSameDirectionPositionsPerSymbol = 2;
int runtimeMinSecondsBetweenTradesXAUUSD = 180;
double runtimeStopAtrMultiplier = 1.2;
double runtimeTakeProfitAtrMultiplier = 2.4;
double runtimeMaxSpreadPointsXAUUSD = 80.0;
bool runtimeEnableSmartMoneyGates = true;
double runtimeFvgMinAtrMultiplier = 0.30;
double runtimeDiscountThreshold = 0.38;
double runtimePremiumThreshold = 0.62;
double runtimeLiquiditySweepAtrMultiplier = 0.30;
bool runtimeEnableXauRsiReversion = false;
bool runtimeEnableXauAtrImpulse = true;
bool runtimeEnableAdxRegimeFilter = true;
double runtimeAdxMinThreshold = 20.0;
bool runtimeEnableMacdHistogramAlignment = false;
double runtimePdaLongCeiling = 0.40;
double runtimePdaShortFloor = 0.60;
int runtimeLossStreakPauseCount = 0;
double runtimeBadDayDownshiftFraction = 0.50;
double runtimeMaxRecentDrawdownFraction = 0.0;
bool runtimeBlackoutEnabled = false;
double runtimeMaxAtrRegimeMultiplier = 0.0;
datetime lastResearchBarsExport = 0;
datetime lastResearchBarsExportAttempt = 0;
int lastResearchBarsCount = 0;

bool IsXauSymbol(string symbol) {
   string s = Upper(symbol);
   return StringFind(s, "XAU") >= 0 || StringFind(s, "GOLD") >= 0;
}

bool IsEntryPauseActive() {
   return FileIsExist(EntryPauseFile, FILE_COMMON);
}

string FormatResearchMinute(datetime value) {
   MqlDateTime dt;
   TimeToStruct(value, dt);
   return StringFormat(
      "%04d-%02d-%02d %02d:%02d",
      dt.year,
      dt.mon,
      dt.day,
      dt.hour,
      dt.min
   );
}

bool WriteResearchBars() {
   if(ResearchBarsCount <= 0 || ResearchBarsExportIntervalSeconds <= 0) return false;
   datetime now = TimeCurrent();
   if(lastResearchBarsExport > 0 && now - lastResearchBarsExport < ResearchBarsExportIntervalSeconds) return false;
   if(lastResearchBarsExportAttempt > 0 && now - lastResearchBarsExportAttempt < 300) return false;
   lastResearchBarsExportAttempt = now;

   string symbol = "XAUUSD";
   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   ResetLastError();
   int copied = CopyRates(symbol, PERIOD_M1, 0, ResearchBarsCount, rates);
   if(copied <= 0) {
      Print("FinRobot research export: CopyRates failed symbol=", symbol, " err=", GetLastError());
      return false;
   }

   int h = FileOpen(ResearchBarsFile, FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h == INVALID_HANDLE) {
      Print("FinRobot research export: FileOpen failed file=", ResearchBarsFile, " err=", GetLastError());
      return false;
   }
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   for(int i = 0; i < copied; i++) {
      string line = IntegerToString((int)rates[i].time) + "\t" +
         DoubleToString(rates[i].open, digits) + "\t" +
         DoubleToString(rates[i].high, digits) + "\t" +
         DoubleToString(rates[i].low, digits) + "\t" +
         DoubleToString(rates[i].close, digits) + "\t" +
         IntegerToString((int)rates[i].tick_volume) + "\r\n";
      if(FileWriteString(h, line) <= 0) {
         int err = GetLastError();
         FileClose(h);
         Print("FinRobot research export: write failed file=", ResearchBarsFile, " err=", err);
         return false;
      }
   }
   FileClose(h);
   lastResearchBarsExport = now;
   lastResearchBarsCount = copied;
   Print("FinRobot research export: symbol=", symbol, " bars=", copied, " file=", ResearchBarsFile);
   return true;
}

string Lower(string s) {
   StringToLower(s);
   return s;
}

double ClampProfileDouble(double value, double lo, double hi) {
   return MathMax(lo, MathMin(hi, value));
}

int ClampProfileInt(int value, int lo, int hi) {
   return MathMax(lo, MathMin(hi, value));
}

bool ParseProfileBool(string value, bool fallback) {
   string v = Lower(Trim(value));
   if(v == "1" || v == "true" || v == "yes" || v == "on") return true;
   if(v == "0" || v == "false" || v == "no" || v == "off") return false;
   return fallback;
}

ENUM_TIMEFRAMES ParseProfileTimeframe(string value, ENUM_TIMEFRAMES fallback) {
   string v = Upper(Trim(value));
   if(v == "1" || v == "M1" || v == "PERIOD_M1") return PERIOD_M1;
   if(v == "5" || v == "M5" || v == "PERIOD_M5") return PERIOD_M5;
   if(v == "15" || v == "M15" || v == "PERIOD_M15") return PERIOD_M15;
   return fallback;
}

void ResetRuntimeStrategyProfile() {
   activeProfileName = "compiled_defaults";
   activeRiskTier = 0;
   runtimeProfileLoaded = 0;
   runtimeProfileMessage = "compiled defaults";
   runtimeAutoTimeframe = AutoTimeframe;
   runtimeAtrImpulseMultiplier = ClampProfileDouble(AtrImpulseMultiplier, 0.04, 0.30);
   runtimeMaxLotPerTradeXAUUSD = ClampProfileDouble(MaxLotPerTradeXAUUSD, 0.01, 50.0);
   runtimeHighConfluenceLotMultiplier = ClampProfileDouble(HighConfluenceLotMultiplier, 1.0, 5.0);
   runtimeMinSmcConfluenceScoreXAUUSD = ClampProfileInt(MinSmcConfluenceScoreXAUUSD, 1, 6);
   runtimeHighConfluenceScore = ClampProfileInt(HighConfluenceScore, 4, 6);
   runtimeDailyRiskPerTradeFraction = ClampProfileDouble(DailyRiskPerTradeFraction, 0.0001, MAX_EFFECTIVE_RISK_PER_TRADE_FRACTION);
   runtimeDailyLossLimitFraction = ClampProfileDouble(DailyLossLimitFraction, 0.0025, 0.0500);
   runtimeMaxAutoPositionsXAUUSD = ClampProfileInt(MaxAutoPositionsXAUUSD, 1, 4);
   runtimeMaxSameDirectionPositionsPerSymbol = ClampProfileInt(MaxSameDirectionPositionsPerSymbol, 1, 4);
   runtimeMinSecondsBetweenTradesXAUUSD = ClampProfileInt(MinSecondsBetweenTradesXAUUSD, 30, 900);
   runtimeStopAtrMultiplier = ClampProfileDouble(StopAtrMultiplier, 0.50, 3.00);
   runtimeTakeProfitAtrMultiplier = ClampProfileDouble(TakeProfitAtrMultiplier, 0.80, 6.00);
   runtimeMaxSpreadPointsXAUUSD = ClampProfileDouble(MaxSpreadPointsXAUUSD, 20.0, 120.0);
   runtimeEnableSmartMoneyGates = EnableSmartMoneyGates;
   runtimeFvgMinAtrMultiplier = ClampProfileDouble(FvgMinAtrMultiplier, 0.05, 1.50);
   runtimeDiscountThreshold = ClampProfileDouble(DiscountThreshold, 0.10, 0.50);
   runtimePremiumThreshold = ClampProfileDouble(PremiumThreshold, 0.50, 0.90);
   runtimeLiquiditySweepAtrMultiplier = ClampProfileDouble(LiquiditySweepAtrMultiplier, 0.05, 1.50);
   runtimeEnableXauRsiReversion = EnableXauRsiReversion;
   runtimeEnableXauAtrImpulse = EnableXauAtrImpulse;
   runtimeEnableAdxRegimeFilter = EnableAdxRegimeFilter;
   runtimeAdxMinThreshold = ClampProfileDouble(AdxMinThreshold, 5.0, 45.0);
   runtimeEnableMacdHistogramAlignment = EnableMacdHistogramAlignment;
   runtimePdaLongCeiling = 0.40;
   runtimePdaShortFloor = 0.60;
   runtimeLossStreakPauseCount = ClampProfileInt(LossStreakPauseCount, 0, 8);
   runtimeBadDayDownshiftFraction = ClampProfileDouble(BadDayDownshiftFraction, 0.0, 1.0);
   runtimeMaxRecentDrawdownFraction = ClampProfileDouble(MaxRecentDrawdownFraction, 0.0, 0.0500);
   runtimeBlackoutEnabled = BlackoutEnabled;
   runtimeMaxAtrRegimeMultiplier = ClampProfileDouble(MaxAtrRegimeMultiplier, 0.0, 8.0);
}

void ApplyRuntimeProfileKey(string keyRaw, string valueRaw) {
   string key = Lower(Trim(keyRaw));
   string value = Trim(valueRaw);
   if(key == "profile_name") activeProfileName = value;
   else if(key == "risk_tier") activeRiskTier = ClampProfileInt((int)StringToInteger(value), 0, 2);
   else if(key == "auto_timeframe") runtimeAutoTimeframe = ParseProfileTimeframe(value, runtimeAutoTimeframe);
   else if(key == "enable_xau_atr_impulse") runtimeEnableXauAtrImpulse = ParseProfileBool(value, runtimeEnableXauAtrImpulse);
   else if(key == "enable_xau_rsi_reversion") runtimeEnableXauRsiReversion = ParseProfileBool(value, runtimeEnableXauRsiReversion);
   else if(key == "enable_smart_money_gates") runtimeEnableSmartMoneyGates = ParseProfileBool(value, runtimeEnableSmartMoneyGates);
   else if(key == "enable_adx_regime_filter") runtimeEnableAdxRegimeFilter = ParseProfileBool(value, runtimeEnableAdxRegimeFilter);
   else if(key == "enable_macd_histogram_alignment") runtimeEnableMacdHistogramAlignment = ParseProfileBool(value, runtimeEnableMacdHistogramAlignment);
   else if(key == "impulse_atr_multiplier") runtimeAtrImpulseMultiplier = ClampProfileDouble(StringToDouble(value), 0.04, 0.30);
   else if(key == "min_smc_confluence_score_xauusd") runtimeMinSmcConfluenceScoreXAUUSD = ClampProfileInt((int)StringToInteger(value), 1, 6);
   else if(key == "pda_long_ceiling") runtimePdaLongCeiling = ClampProfileDouble(StringToDouble(value), 0.05, 0.50);
   else if(key == "pda_short_floor") runtimePdaShortFloor = ClampProfileDouble(StringToDouble(value), 0.50, 0.95);
   else if(key == "discount_threshold") runtimeDiscountThreshold = ClampProfileDouble(StringToDouble(value), 0.10, 0.50);
   else if(key == "premium_threshold") runtimePremiumThreshold = ClampProfileDouble(StringToDouble(value), 0.50, 0.90);
   else if(key == "fvg_min_atr_multiplier") runtimeFvgMinAtrMultiplier = ClampProfileDouble(StringToDouble(value), 0.05, 1.50);
   else if(key == "liquidity_sweep_atr_multiplier") runtimeLiquiditySweepAtrMultiplier = ClampProfileDouble(StringToDouble(value), 0.05, 1.50);
   else if(key == "daily_risk_per_trade_fraction") runtimeDailyRiskPerTradeFraction = ClampProfileDouble(StringToDouble(value), 0.0001, MAX_EFFECTIVE_RISK_PER_TRADE_FRACTION);
   else if(key == "daily_loss_limit_fraction") runtimeDailyLossLimitFraction = ClampProfileDouble(StringToDouble(value), 0.0025, 0.0500);
   else if(key == "max_lot_per_trade_xauusd") runtimeMaxLotPerTradeXAUUSD = ClampProfileDouble(StringToDouble(value), 0.01, 50.0);
   else if(key == "max_auto_positions_xauusd") runtimeMaxAutoPositionsXAUUSD = ClampProfileInt((int)StringToInteger(value), 1, 4);
   else if(key == "max_same_direction_positions_per_symbol") runtimeMaxSameDirectionPositionsPerSymbol = ClampProfileInt((int)StringToInteger(value), 1, 4);
   else if(key == "min_seconds_between_trades_xauusd") runtimeMinSecondsBetweenTradesXAUUSD = ClampProfileInt((int)StringToInteger(value), 30, 900);
   else if(key == "stop_atr_multiplier") runtimeStopAtrMultiplier = ClampProfileDouble(StringToDouble(value), 0.50, 3.00);
   else if(key == "take_profit_atr_multiplier") runtimeTakeProfitAtrMultiplier = ClampProfileDouble(StringToDouble(value), 0.80, 6.00);
   else if(key == "adx_min_threshold") runtimeAdxMinThreshold = ClampProfileDouble(StringToDouble(value), 5.0, 45.0);
   else if(key == "high_confluence_lot_multiplier") runtimeHighConfluenceLotMultiplier = ClampProfileDouble(StringToDouble(value), 1.0, 5.0);
   else if(key == "high_confluence_score") runtimeHighConfluenceScore = ClampProfileInt((int)StringToInteger(value), 4, 6);
   else if(key == "max_spread_points_xauusd") runtimeMaxSpreadPointsXAUUSD = ClampProfileDouble(StringToDouble(value), 20.0, 120.0);
   else if(key == "loss_streak_pause_count") runtimeLossStreakPauseCount = ClampProfileInt((int)StringToInteger(value), 0, 8);
   else if(key == "bad_day_downshift_fraction") runtimeBadDayDownshiftFraction = ClampProfileDouble(StringToDouble(value), 0.0, 1.0);
   else if(key == "max_recent_drawdown_fraction") runtimeMaxRecentDrawdownFraction = ClampProfileDouble(StringToDouble(value), 0.0, 0.0500);
   else if(key == "blackout_enabled") runtimeBlackoutEnabled = ParseProfileBool(value, runtimeBlackoutEnabled);
   else if(key == "max_atr_regime_multiplier") runtimeMaxAtrRegimeMultiplier = ClampProfileDouble(StringToDouble(value), 0.0, 8.0);
}

void LoadRuntimeStrategyProfile() {
   ResetRuntimeStrategyProfile();
   if(!EnableRuntimeStrategyProfile) {
      runtimeProfileMessage = "runtime profile disabled";
      return;
   }
   int h = FileOpen(StrategyProfileFile, FILE_READ|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h == INVALID_HANDLE) {
      runtimeProfileMessage = "profile file not found";
      return;
   }
   int applied = 0;
   while(!FileIsEnding(h)) {
      string line = Trim(FileReadString(h));
      if(line == "" || StringFind(line, "#") == 0) continue;
      string cols[];
      int n = StringSplit(line, ',', cols);
      if(n < 2) continue;
      string key = Trim(cols[0]);
      if(Lower(key) == "key") continue;
      ApplyRuntimeProfileKey(key, cols[1]);
      applied++;
   }
   FileClose(h);
   if(applied > 0) {
      runtimeProfileLoaded = 1;
      runtimeProfileMessage = "loaded";
   } else {
      activeProfileName = "compiled_defaults";
      runtimeProfileMessage = "profile empty";
   }
}

string StrategyProfileJson() {
   string payload = "{";
   payload += "\"name\":\"" + Clean(activeProfileName) + "\",";
   payload += "\"risk_tier\":" + IntegerToString(activeRiskTier) + ",";
   payload += "\"loaded\":" + IntegerToString(runtimeProfileLoaded) + ",";
   payload += "\"message\":\"" + Clean(runtimeProfileMessage) + "\",";
   payload += "\"timeframe\":\"" + Clean(EnumToString(runtimeAutoTimeframe)) + "\",";
   payload += "\"risk_per_trade\":" + DoubleToString(runtimeDailyRiskPerTradeFraction, 6) + ",";
   payload += "\"max_effective_risk_per_trade\":" + DoubleToString(MAX_EFFECTIVE_RISK_PER_TRADE_FRACTION, 6) + ",";
   payload += "\"daily_loss_limit\":" + DoubleToString(runtimeDailyLossLimitFraction, 4) + ",";
   payload += "\"max_lot_xauusd\":" + DoubleToString(runtimeMaxLotPerTradeXAUUSD, 2) + ",";
   payload += "\"max_positions_xauusd\":" + IntegerToString(runtimeMaxAutoPositionsXAUUSD) + ",";
   payload += "\"min_smc_xauusd\":" + IntegerToString(runtimeMinSmcConfluenceScoreXAUUSD) + ",";
   payload += "\"pda_long_ceiling\":" + DoubleToString(runtimePdaLongCeiling, 2) + ",";
   payload += "\"pda_short_floor\":" + DoubleToString(runtimePdaShortFloor, 2) + ",";
   payload += "\"atr_impulse_multiplier\":" + DoubleToString(runtimeAtrImpulseMultiplier, 3) + ",";
   payload += "\"macd_histogram_alignment\":" + IntegerToString((int)runtimeEnableMacdHistogramAlignment) + ",";
   payload += "\"loss_streak_pause_count\":" + IntegerToString(runtimeLossStreakPauseCount) + ",";
   payload += "\"bad_day_downshift_fraction\":" + DoubleToString(runtimeBadDayDownshiftFraction, 2) + ",";
   payload += "\"max_recent_drawdown_fraction\":" + DoubleToString(runtimeMaxRecentDrawdownFraction, 4) + ",";
   payload += "\"blackout_enabled\":" + IntegerToString((int)runtimeBlackoutEnabled) + ",";
   payload += "\"max_atr_regime_multiplier\":" + DoubleToString(runtimeMaxAtrRegimeMultiplier, 2);
   payload += "}";
   return payload;
}

void ResizeSignalTelemetry(int n) {
   ArrayResize(filledSignalCounts, n);
   ArrayResize(outsideSessionRejectCounts, n);
   ArrayResize(marketClosedRejectCounts, n);
   ArrayResize(spreadRejectCounts, n);
   ArrayResize(noSignalCounts, n);
   ArrayResize(smcRejectCounts, n);
   ArrayResize(directionRejectCounts, n);
   ArrayResize(pdaRejectCounts, n);
   ArrayResize(positionRejectCounts, n);
   ArrayResize(orderRejectCounts, n);
   ArrayResize(recoveryRejectCounts, n);
}

void ResetSignalTelemetry() {
   int n = ArraySize(managedSymbols);
   ResizeSignalTelemetry(n);
   signalTelemetryDay = DayStamp(TimeCurrent());
   for(int i = 0; i < n; i++) {
      filledSignalCounts[i] = 0;
      outsideSessionRejectCounts[i] = 0;
      marketClosedRejectCounts[i] = 0;
      spreadRejectCounts[i] = 0;
      noSignalCounts[i] = 0;
      smcRejectCounts[i] = 0;
      directionRejectCounts[i] = 0;
      pdaRejectCounts[i] = 0;
      positionRejectCounts[i] = 0;
      orderRejectCounts[i] = 0;
      recoveryRejectCounts[i] = 0;
   }
}

void EnsureSignalTelemetryDay() {
   int today = DayStamp(TimeCurrent());
   if(signalTelemetryDay != today || ArraySize(filledSignalCounts) != ArraySize(managedSymbols)) {
      ResetSignalTelemetry();
   }
}

void CountSignalTelemetry(int idx, string signal) {
   if(idx < 0 || idx >= ArraySize(managedSymbols)) return;
   if(StringFind(signal, "BUY ") == 0 || StringFind(signal, "SELL ") == 0) filledSignalCounts[idx]++;
   else if(StringFind(signal, "outside_trading_session") == 0) outsideSessionRejectCounts[idx]++;
   else if(StringFind(signal, "market_closed") == 0) marketClosedRejectCounts[idx]++;
   else if(StringFind(signal, "spread_too_wide") == 0) spreadRejectCounts[idx]++;
   else if(StringFind(signal, "no_signal") == 0) noSignalCounts[idx]++;
   else if(StringFind(signal, "smc_reject") == 0) smcRejectCounts[idx]++;
   else if(StringFind(signal, "xau_pda_reject") == 0) pdaRejectCounts[idx]++;
   else if(StringFind(signal, "max_positions") == 0 || StringFind(signal, "same_side_max") == 0 || StringFind(signal, "cooldown") == 0) positionRejectCounts[idx]++;
   else if(StringFind(signal, "order_failed") == 0 || StringFind(signal, "risk_volume_zero") == 0) orderRejectCounts[idx]++;
   else if(StringFind(signal, "loss_streak_pause") == 0 || StringFind(signal, "recent_drawdown_pause") == 0 || StringFind(signal, "blackout_reject") == 0 || StringFind(signal, "atr_regime_reject") == 0) recoveryRejectCounts[idx]++;
}

void SetLastSignal(int idx, string signal) {
   if(idx < 0 || idx >= ArraySize(managedSymbols)) return;
   EnsureSignalTelemetryDay();
   lastSignals[idx] = signal;
   CountSignalTelemetry(idx, signal);
}

bool UseSessionGateForSymbol(string symbol) {
   if(!EnableSessionGating) return false;
   if(IsXauSymbol(symbol) && EnableXauWeekdayMarketHours) return false;
   return true;
}

bool UseWeekdayMarketHoursForSymbol(string symbol) {
   return EnableSessionGating && IsXauSymbol(symbol) && EnableXauWeekdayMarketHours;
}

int SecondsOfDay(datetime value) {
   long raw = (long)value;
   if(raw >= 0 && raw <= 86400) return (int)raw;
   MqlDateTime dt;
   TimeToStruct(value, dt);
   return dt.hour * 3600 + dt.min * 60 + dt.sec;
}

bool IsWithinSessionSeconds(int nowSec, int fromSec, int toSec) {
   if(fromSec == toSec) return true;
   if(toSec > fromSec) return nowSec >= fromSec && nowSec < toSec;
   return nowSec >= fromSec || nowSec < toSec;
}

bool IsWeekdaySymbolMarketOpen(string symbol) {
   MqlDateTime dt;
   TimeCurrent(dt);
   if(dt.day_of_week <= 0 || dt.day_of_week >= 6) return false;

   long tradeMode = SymbolInfoInteger(symbol, SYMBOL_TRADE_MODE);
   if(tradeMode == SYMBOL_TRADE_MODE_DISABLED || tradeMode == SYMBOL_TRADE_MODE_CLOSEONLY) return false;

   int nowSec = dt.hour * 3600 + dt.min * 60 + dt.sec;
   bool hasSessions = false;
   datetime fromTime = 0;
   datetime toTime = 0;
   ENUM_DAY_OF_WEEK day = (ENUM_DAY_OF_WEEK)dt.day_of_week;
   for(uint i = 0; i < 24; i++) {
      if(!SymbolInfoSessionTrade(symbol, day, i, fromTime, toTime)) break;
      hasSessions = true;
      if(IsWithinSessionSeconds(nowSec, SecondsOfDay(fromTime), SecondsOfDay(toTime))) return true;
   }
   return !hasSessions;
}

bool IsAutoSessionOpen(string symbol) {
   if(UseWeekdayMarketHoursForSymbol(symbol)) return IsWeekdaySymbolMarketOpen(symbol);
   if(!UseSessionGateForSymbol(symbol)) return true;
   return IsSessionTime(true, LondonStartHour, LondonEndHour, NyStartHour, NyEndHour);
}

string AutoSessionRejectReason(string symbol) {
   if(UseWeekdayMarketHoursForSymbol(symbol)) return "market_closed";
   return "outside_trading_session";
}

double PremiumDiscountPosition(string symbol, int idx) {
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int bars = CopyRates(symbol, runtimeAutoTimeframe, 0, MathMax(SmcLookbackBars + 5, 60), rates);
   if(bars < 10) return 0.5;
   double mid = (SymbolInfoDouble(symbol, SYMBOL_BID) + SymbolInfoDouble(symbol, SYMBOL_ASK)) * 0.5;
   return PremiumDiscountPosition(rates, SmcLookbackBars, mid);
}

int SymbolIndex(string symbol) {
   for(int i = 0; i < ArraySize(managedSymbols); i++) {
      if(managedSymbols[i] == symbol) return i;
   }
   return -1;
}

bool IsManagedSymbol(string symbol) {
   return SymbolIndex(symbol) >= 0;
}

void LoadManagedSymbols() {
   string parts[];
   int n = StringSplit(AutoSymbols, ',', parts);
   if(n <= 0) {
      ArrayResize(managedSymbols, 1);
      managedSymbols[0] = "XAUUSD";
   } else {
      ArrayResize(managedSymbols, 0);
      for(int i = 0; i < n; i++) {
         string sym = Trim(parts[i]);
         if(sym == "") continue;
         int next = ArraySize(managedSymbols);
         ArrayResize(managedSymbols, next + 1);
         managedSymbols[next] = sym;
      }
   }
   if(ArraySize(managedSymbols) == 0) {
      ArrayResize(managedSymbols, 1);
      managedSymbols[0] = "XAUUSD";
   }
   ArrayResize(lastSignals, ArraySize(managedSymbols));
   ArrayResize(lastTradeTimes, ArraySize(managedSymbols));
   ResizeSignalTelemetry(ArraySize(managedSymbols));
   ResetSignalTelemetry();
   for(int i = 0; i < ArraySize(managedSymbols); i++) {
      if(lastSignals[i] == "") lastSignals[i] = "init";
      lastTradeTimes[i] = 0;
      SymbolSelect(managedSymbols[i], true);
   }
}

double NormalizeVolume(string symbol, double volume) {
   double minLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0.0) step = 0.01;
   volume = MathMax(minLot, MathMin(maxLot, volume));
   volume = MathFloor(volume / step) * step;
   int digits = 2;
   if(step < 0.01) digits = 3;
   if(step < 0.001) digits = 4;
   return NormalizeDouble(volume, digits);
}

int CountPositionsByMagic(string symbol, int magic) {
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(PositionSelectByTicket(ticket)) {
         string ps = PositionGetString(POSITION_SYMBOL);
         long pm = PositionGetInteger(POSITION_MAGIC);
         if(ps == symbol && (int)pm == magic) count++;
      }
   }
   return count;
}

int CountPositionsByMagicAndSide(string symbol, int magic, int side) {
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      string ps = PositionGetString(POSITION_SYMBOL);
      long pm = PositionGetInteger(POSITION_MAGIC);
      if(ps != symbol || (int)pm != magic) continue;
      long type = PositionGetInteger(POSITION_TYPE);
      if((side > 0 && type == POSITION_TYPE_BUY) || (side < 0 && type == POSITION_TYPE_SELL)) count++;
   }
   return count;
}

int MaxAutoPositionsForSymbol(string symbol) {
   if(IsXauSymbol(symbol)) return MathMax(0, runtimeMaxAutoPositionsXAUUSD);
   return MathMax(0, MaxAutoPositionsPerSymbol);
}

int MinSecondsBetweenTradesForSymbol(string symbol) {
   if(IsXauSymbol(symbol)) return MathMax(0, runtimeMinSecondsBetweenTradesXAUUSD);
   return MathMax(0, MinSecondsBetweenTrades);
}

int MinSmcConfluenceForSymbol(string symbol) {
   if(IsXauSymbol(symbol)) return MathMax(1, runtimeMinSmcConfluenceScoreXAUUSD);
   return MathMax(1, MinSmcConfluenceScore);
}

double MaxLotForSymbol(string symbol) {
   if(IsXauSymbol(symbol)) return MathMax(0.0, MathMin(MaxLotPerTrade, runtimeMaxLotPerTradeXAUUSD));
   return MathMax(0.0, MaxLotPerTrade);
}

string CombinedSignals() {
   string out = "";
   for(int i = 0; i < ArraySize(managedSymbols); i++) {
      if(i > 0) out += " | ";
      out += managedSymbols[i] + ":" + lastSignals[i];
   }
   return out;
}

string SymbolTelemetryJson(int idx) {
   EnsureSignalTelemetryDay();
   string payload = "{";
   payload += "\"day\":" + IntegerToString(signalTelemetryDay) + ",";
   payload += "\"filled\":" + IntegerToString(filledSignalCounts[idx]) + ",";
   payload += "\"outside_session\":" + IntegerToString(outsideSessionRejectCounts[idx]) + ",";
   payload += "\"market_closed\":" + IntegerToString(marketClosedRejectCounts[idx]) + ",";
   payload += "\"spread_or_cost\":" + IntegerToString(spreadRejectCounts[idx]) + ",";
   payload += "\"no_signal\":" + IntegerToString(noSignalCounts[idx]) + ",";
   payload += "\"smc_reject\":" + IntegerToString(smcRejectCounts[idx]) + ",";
   payload += "\"direction_reject\":" + IntegerToString(directionRejectCounts[idx]) + ",";
   payload += "\"pda_reject\":" + IntegerToString(pdaRejectCounts[idx]) + ",";
   payload += "\"position_reject\":" + IntegerToString(positionRejectCounts[idx]) + ",";
   payload += "\"order_reject\":" + IntegerToString(orderRejectCounts[idx]) + ",";
   payload += "\"recovery_reject\":" + IntegerToString(recoveryRejectCounts[idx]);
   payload += "}";
   return payload;
}

string SymbolStatusJson(string symbol, int idx) {
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   double spread = point > 0 ? (ask - bid) / point : 0.0;
   string payload = "{";
   payload += "\"symbol\":\"" + Clean(symbol) + "\",";
   payload += "\"bid\":" + DoubleToString(bid, digits) + ",";
   payload += "\"ask\":" + DoubleToString(ask, digits) + ",";
   payload += "\"spread_points\":" + DoubleToString(spread, 1) + ",";
   payload += "\"auto_positions\":" + IntegerToString(CountPositionsByMagic(symbol, MagicNumber)) + ",";
   payload += "\"session_gated\":" + IntegerToString((int)UseSessionGateForSymbol(symbol)) + ",";
   payload += "\"weekday_market_hours\":" + IntegerToString((int)UseWeekdayMarketHoursForSymbol(symbol)) + ",";
   payload += "\"session_open\":" + IntegerToString((int)IsAutoSessionOpen(symbol)) + ",";
   payload += "\"recovery_armed\":" + IntegerToString((int)(runtimeLossStreakPauseCount > 0 || runtimeMaxRecentDrawdownFraction > 0.0 || runtimeBlackoutEnabled || runtimeMaxAtrRegimeMultiplier > 0.0)) + ",";
   payload += "\"last_signal\":\"" + Clean(lastSignals[idx]) + "\",";
   payload += "\"signal_telemetry\":" + SymbolTelemetryJson(idx);
   payload += "}";
   return payload;
}

void WriteStatus() {
   int h = FileOpen(StatusFile, FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h == INVALID_HANDLE) return;
   string payload = "{";
   payload += "\"ts\":" + IntegerToString((int)TimeCurrent()) + ",";
   payload += "\"login\":" + IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN)) + ",";
   payload += "\"server\":\"" + Clean(AccountInfoString(ACCOUNT_SERVER)) + "\",";
   payload += "\"balance\":" + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + ",";
   payload += "\"equity\":" + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2) + ",";
   payload += "\"margin\":" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN), 2) + ",";
   payload += "\"free_margin\":" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2) + ",";
   payload += "\"positions\":" + IntegerToString(PositionsTotal()) + ",";
   payload += "\"trade_allowed_terminal\":" + IntegerToString((int)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) + ",";
   payload += "\"trade_allowed_ea\":" + IntegerToString((int)MQLInfoInteger(MQL_TRADE_ALLOWED)) + ",";
   payload += "\"auto_trade_mt5\":" + IntegerToString((int)AutoTradeMT5) + ",";
   payload += "\"entry_pause\":" + IntegerToString((int)IsEntryPauseActive()) + ",";
   payload += "\"research_bars_last_export\":" + IntegerToString((int)lastResearchBarsExport) + ",";
   payload += "\"research_bars_count\":" + IntegerToString(lastResearchBarsCount) + ",";
   payload += "\"ea_version\":\"" + Clean(EaVersion) + "\",";
   payload += "\"git_sha\":\"" + Clean(EaGitSha) + "\",";
   payload += "\"symbol\":\"" + Clean(AutoSymbols) + "\",";
   payload += "\"last_auto_signal\":\"" + Clean(CombinedSignals()) + "\",";
   payload += "\"last_command_id\":" + IntegerToString(lastCommandId) + ",";
   payload += "\"strategy_profile\":" + StrategyProfileJson() + ",";
   payload += "\"money_management\":" + MoneyManagementJson() + ",";
   payload += "\"symbols\":[";
   for(int i = 0; i < ArraySize(managedSymbols); i++) {
      if(i > 0) payload += ",";
      payload += SymbolStatusJson(managedSymbols[i], i);
   }
   payload += "]";
   payload += "}";
   FileWriteString(h, payload);
   FileClose(h);
}

bool EnsureSymbol(string symbol) {
   return SymbolSelect(symbol, true);
}

void UpdateMoneyManagementState() {
   int today = DayStamp(TimeCurrent());
   if(moneyManagementDay != today || dailyEquitySnapshot <= 0.0) {
      moneyManagementDay = today;
      dailyEquitySnapshot = AccountInfoDouble(ACCOUNT_EQUITY);
   }
   todayClosedPnlCache = ManagedClosedPnlForDay(today, MagicNumber, managedSymbols);
   lastMoneyManagementUpdate = TimeCurrent();
}

bool IsDailyLossLimitReached() {
   double limitMoney = dailyEquitySnapshot * runtimeDailyLossLimitFraction;
   return limitMoney > 0.0 && todayClosedPnlCache <= -limitMoney;
}

bool DailyLossLimitReached() {
   UpdateMoneyManagementState();
   return IsDailyLossLimitReached();
}

double BaseLotForSymbol(string symbol) {
   return XauBaseLot;
}

double DailyRiskVolume(string symbol, double slDistance, int confluenceScore) {
   UpdateMoneyManagementState();
   double tickSize = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tickSize <= 0.0 || tickValue <= 0.0 || slDistance <= 0.0 || dailyEquitySnapshot <= 0.0) {
      return NormalizeVolume(symbol, BaseLotForSymbol(symbol));
   }
   double riskMoney = dailyEquitySnapshot * runtimeDailyRiskPerTradeFraction;
   if(confluenceScore >= runtimeHighConfluenceScore) riskMoney *= MathMax(1.0, runtimeHighConfluenceLotMultiplier);
   riskMoney = MathMin(riskMoney, dailyEquitySnapshot * MAX_EFFECTIVE_RISK_PER_TRADE_FRACTION);
   if(todayClosedPnlCache < 0.0) riskMoney *= runtimeBadDayDownshiftFraction;
   if(riskMoney <= 0.0) return 0.0;
   double riskPerLot = (slDistance / tickSize) * tickValue;
   if(riskPerLot <= 0.0) return NormalizeVolume(symbol, BaseLotForSymbol(symbol));
   double volume = riskMoney / riskPerLot;
   volume = MathMin(MaxLotForSymbol(symbol), MathMax(0.0, volume));
   return NormalizeVolume(symbol, volume);
}

string MoneyManagementJson() {
   UpdateMoneyManagementState();
   string payload = "{";
   payload += "\"day\":" + IntegerToString(moneyManagementDay) + ",";
   payload += "\"daily_equity_snapshot\":" + DoubleToString(dailyEquitySnapshot, 2) + ",";
   payload += "\"today_closed_pnl\":" + DoubleToString(todayClosedPnlCache, 2) + ",";
   payload += "\"daily_risk_per_trade_fraction\":" + DoubleToString(runtimeDailyRiskPerTradeFraction, 6) + ",";
   payload += "\"max_effective_risk_per_trade_fraction\":" + DoubleToString(MAX_EFFECTIVE_RISK_PER_TRADE_FRACTION, 6) + ",";
   payload += "\"daily_loss_limit_fraction\":" + DoubleToString(runtimeDailyLossLimitFraction, 4) + ",";
   payload += "\"bad_day_downshift_fraction\":" + DoubleToString(runtimeBadDayDownshiftFraction, 2) + ",";
   payload += "\"max_recent_drawdown_fraction\":" + DoubleToString(runtimeMaxRecentDrawdownFraction, 4) + ",";
   payload += "\"loss_streak_pause_count\":" + IntegerToString(runtimeLossStreakPauseCount) + ",";
   payload += "\"loss_limit_reached\":" + IntegerToString((int)IsDailyLossLimitReached()) + ",";
   payload += "\"risk_lot_sizing\":" + IntegerToString((int)UseDailyRiskLotSizing) + ",";
   payload += "\"auto_close_no_sl_tp\":" + IntegerToString((int)AutoClosePositionsWithoutStops);
   payload += "}";
   return payload;
}

bool RiskCloseAlreadyAttempted(ulong ticket) {
   int today = DayStamp(TimeCurrent());
   if(riskCloseAttemptDay != today) {
      riskCloseAttemptDay = today;
      riskCloseAttemptTickets = "";
   }
   string marker = "|" + IntegerToString((int)ticket) + "|";
   if(StringFind(riskCloseAttemptTickets, marker) >= 0) return true;
   riskCloseAttemptTickets += marker;
   return false;
}

void EnforceManagedRisk() {
   if(!AutoClosePositionsWithoutStops || !AllowTrading) return;
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED)) return;
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(DefaultDeviationPoints);
   for(int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      string symbol = PositionGetString(POSITION_SYMBOL);
      if(!IsManagedSymbol(symbol)) continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      double sl = PositionGetDouble(POSITION_SL);
      double tp = PositionGetDouble(POSITION_TP);
      string comment = PositionGetString(POSITION_COMMENT);
      if((sl <= 0.0 || tp <= 0.0) && StringFind(comment, "FinRobot_") != 0) {
         if(RiskCloseAlreadyAttempted(ticket)) continue;
         bool ok = trade.PositionClose(ticket, DefaultDeviationPoints);
         string detail = "unmanaged managed-symbol position without SL/TP: " + comment + " retcode=" + IntegerToString((int)trade.ResultRetcode()) + " " + trade.ResultRetcodeDescription();
         AppendAck(AckFile, ++lastCommandId, ok ? "RISK_CLOSED" : "RISK_CLOSE_FAILED", detail, symbol, "CLOSE", PositionGetDouble(POSITION_VOLUME), PositionGetDouble(POSITION_PRICE_CURRENT));
         Sleep(250);
      }
   }
}

bool CloseAllSymbolPositions(string symbol) {
   bool allOk = true;
   for(int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) != symbol) continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      bool ok = trade.PositionClose(ticket, DefaultDeviationPoints);
      if(!ok) allOk = false;
      Sleep(250);
   }
   return allOk;
}

void ExecuteCommand(int id, string action, string symbol, string side, double volume, double sl, double tp, int deviation, string comment) {
   if(id <= lastCommandId) return;
   lastCommandId = id;
   action = Upper(Trim(action));
   symbol = Trim(symbol);
   side = Upper(Trim(side));
   if(deviation <= 0) deviation = DefaultDeviationPoints;
   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(deviation);

   if(!AllowTrading) {
      AppendAck(AckFile, id, "REJECTED", "AllowTrading=false", symbol, side, volume, 0.0);
      return;
   }
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED)) {
      AppendAck(AckFile, id, "REJECTED", "AutoTrading not allowed in terminal or EA", symbol, side, volume, 0.0);
      return;
   }
   if(action == "MARKET" && IsEntryPauseActive()) {
      AppendAck(AckFile, id, "REJECTED", "Entry pause is active", symbol, side, volume, 0.0);
      return;
   }
   if(!EnsureSymbol(symbol)) {
      AppendAck(AckFile, id, "REJECTED", "SymbolSelect failed", symbol, side, volume, 0.0);
      return;
   }
   // Phase 1 hardening (v1.30): enforce mandate and risk policy on the command path.
   if(!IsManagedSymbol(symbol)) {
      AppendAck(AckFile, id, "REJECTED", "Symbol not in mandate: " + symbol, symbol, side, volume, 0.0);
      return;
   }

   volume = NormalizeVolume(symbol, volume);
   bool ok = false;
   if(action == "MARKET") {
      if(side != "BUY" && side != "SELL") {
         AppendAck(AckFile, id, "REJECTED", "Unknown side: " + side, symbol, side, volume, 0.0);
         return;
      }
      if(sl <= 0.0) {
         AppendAck(AckFile, id, "REJECTED", "MARKET requires SL > 0", symbol, side, volume, 0.0);
         return;
      }
      if(tp <= 0.0) {
         AppendAck(AckFile, id, "REJECTED", "MARKET requires TP > 0", symbol, side, volume, 0.0);
         return;
      }
      double maxLot = MaxLotForSymbol(symbol);
      if(volume > maxLot + 1e-9) {
         AppendAck(AckFile, id, "REJECTED", "Lot " + DoubleToString(volume, 4) + " exceeds MaxLot " + DoubleToString(maxLot, 4), symbol, side, volume, 0.0);
         return;
      }
      int maxPos = MaxAutoPositionsForSymbol(symbol);
      if(CountPositionsByMagic(symbol, MagicNumber) >= maxPos) {
         AppendAck(AckFile, id, "REJECTED", "Max positions reached for " + symbol + " (max=" + IntegerToString(maxPos) + ")", symbol, side, volume, 0.0);
         return;
      }
      if(DailyLossLimitReached()) {
         AppendAck(AckFile, id, "REJECTED", "Daily loss limit reached", symbol, side, volume, 0.0);
         return;
      }
      if(side == "BUY") ok = trade.Buy(volume, symbol, 0.0, sl, tp, comment);
      else if(side == "SELL") ok = trade.Sell(volume, symbol, 0.0, sl, tp, comment);
   } else if(action == "CLOSE") {
      // Magic-filtered close: only close positions owned by this EA on this symbol.
      bool anyClosed = false;
      for(int i = PositionsTotal() - 1; i >= 0; i--) {
         ulong ticket = PositionGetTicket(i);
         if(ticket == 0) continue;
         if(!PositionSelectByTicket(ticket)) continue;
         if(PositionGetString(POSITION_SYMBOL) != symbol) continue;
         if((int)PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
         if(trade.PositionClose(ticket, deviation)) anyClosed = true;
         Sleep(250);
      }
      ok = anyClosed;
   } else if(action == "CLOSE_ALL") {
      ok = CloseAllSymbolPositions(symbol);
   } else {
      AppendAck(AckFile, id, "REJECTED", "Unknown action", symbol, side, volume, 0.0);
      return;
   }

   if(ok) AppendAck(AckFile, id, "OK", IntegerToString((int)trade.ResultRetcode()) + " " + trade.ResultRetcodeDescription(), symbol, side, volume, trade.ResultPrice());
   else AppendAck(AckFile, id, "ERROR", IntegerToString((int)trade.ResultRetcode()) + " " + trade.ResultRetcodeDescription(), symbol, side, volume, trade.ResultPrice());
}

void PollCommands() {
   int h = FileOpen(CommandFile, FILE_READ|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h == INVALID_HANDLE) {
      if(commandFileErrLogged == 0) {
         Print("FinRobotBridgeEA: command file not found yet: ", CommandFile, " err=", GetLastError());
         commandFileErrLogged = 1;
      }
      return;
   }
   commandFileErrLogged = 0;
   while(!FileIsEnding(h)) {
      string line = FileReadString(h);
      line = Trim(line);
      if(line == "" || StringFind(line, "id") == 0) continue;
      string cols[];
      int n = StringSplit(line, ',', cols);
      if(n < 9) n = StringSplit(line, '\t', cols);
      if(n < 9) {
         Print("FinRobotBridgeEA: malformed command line: ", line);
         continue;
      }
      ExecuteCommand((int)StringToInteger(cols[0]), Trim(cols[1]), Trim(cols[2]), Trim(cols[3]), StringToDouble(cols[4]), StringToDouble(cols[5]), StringToDouble(cols[6]), (int)StringToInteger(cols[7]), Trim(cols[8]));
   }
   FileClose(h);
   FileDelete(CommandFile, FILE_COMMON);
}

double MaxSpreadForSymbol(string symbol) {
   return runtimeMaxSpreadPointsXAUUSD;
}

double MinStopDistanceForSymbol(string symbol, double entry) {
   return MathMax(entry * 0.00045, 2.0);
}

int RecentManagedLossStreak(string symbol) {
   if(runtimeLossStreakPauseCount <= 0) return 0;
   if(!HistorySelect(TimeCurrent() - 86400 * 14, TimeCurrent())) return 0;
   int streak = 0;
   for(int i = HistoryDealsTotal() - 1; i >= 0; i--) {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;
      if(HistoryDealGetString(ticket, DEAL_SYMBOL) != symbol) continue;
      if((int)HistoryDealGetInteger(ticket, DEAL_MAGIC) != MagicNumber) continue;
      long entry = HistoryDealGetInteger(ticket, DEAL_ENTRY);
      if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY) continue;
      double pnl = HistoryDealGetDouble(ticket, DEAL_PROFIT) + HistoryDealGetDouble(ticket, DEAL_COMMISSION) + HistoryDealGetDouble(ticket, DEAL_SWAP);
      if(pnl < 0.0) {
         streak++;
         continue;
      }
      if(pnl > 0.0) break;
   }
   return streak;
}

bool IsRecentDrawdownPauseActive() {
   if(runtimeMaxRecentDrawdownFraction <= 0.0) return false;
   UpdateMoneyManagementState();
   double limitMoney = dailyEquitySnapshot * runtimeMaxRecentDrawdownFraction;
   return limitMoney > 0.0 && todayClosedPnlCache <= -limitMoney;
}

bool IsRuntimeBlackoutActive(string &reason) {
   reason = "";
   if(!runtimeBlackoutEnabled) return false;
   int h = FileOpen(BlackoutFile, FILE_READ|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h == INVALID_HANDLE) return false;
   datetime now = TimeCurrent();
   while(!FileIsEnding(h)) {
      string line = Trim(FileReadString(h));
      if(line == "" || StringFind(line, "#") == 0) continue;
      string cols[];
      int n = StringSplit(line, ',', cols);
      if(n < 2) n = StringSplit(line, '\t', cols);
      if(n < 2) continue;
      if(Lower(Trim(cols[0])) == "start") continue;
      datetime fromTime = StringToTime(Trim(cols[0]));
      datetime toTime = StringToTime(Trim(cols[1]));
      if(fromTime <= 0 || toTime <= 0) continue;
      if(now >= fromTime && now < toTime) {
         reason = n >= 3 ? Trim(cols[2]) : "scheduled";
         FileClose(h);
         return true;
      }
   }
   FileClose(h);
   return false;
}

bool AtrRegimeTooHot(double currentAtr, double &atrValues[], int copied) {
   if(runtimeMaxAtrRegimeMultiplier <= 0.0 || currentAtr <= 0.0 || copied < 12) return false;
   double sumAtr = 0.0;
   int count = 0;
   int maxItems = MathMin(copied - 1, 50);
   for(int i = 1; i <= maxItems; i++) {
      if(atrValues[i] <= 0.0) continue;
      sumAtr += atrValues[i];
      count++;
   }
   if(count < 10) return false;
   double avgAtr = sumAtr / count;
   return avgAtr > 0.0 && currentAtr > avgAtr * runtimeMaxAtrRegimeMultiplier;
}

void ManageAutoSymbol(string symbol, int idx) {
   bool isXau = IsXauSymbol(symbol);
   if(!AutoTradeMT5 || !AllowTrading) {
      SetLastSignal(idx, "auto_trading_disabled");
      return;
   }
   if(IsEntryPauseActive()) {
      SetLastSignal(idx, "entry_pause");
      return;
   }
   if(AccountInfoInteger(ACCOUNT_TRADE_ALLOWED) == 0 || MQLInfoInteger(MQL_TRADE_ALLOWED) == 0) {
      SetLastSignal(idx, "trading_not_allowed");
      return;
   }
   if(DailyLossLimitReached()) {
      SetLastSignal(idx, "daily_loss_limit");
      return;
   }
   if(!SymbolSelect(symbol, true)) {
      SetLastSignal(idx, "symbol_select_failed");
      return;
   }
   if(!EnableXauAutoTrading && isXau) {
      SetLastSignal(idx, "xau_auto_disabled negative_expectancy");
      return;
   }
   if(!IsAutoSessionOpen(symbol)) {
      SetLastSignal(idx, AutoSessionRejectReason(symbol));
      return;
   }
   if(IsRecentDrawdownPauseActive()) {
      SetLastSignal(idx, "recent_drawdown_pause pnl=" + DoubleToString(todayClosedPnlCache, 2));
      return;
   }
   int lossStreak = RecentManagedLossStreak(symbol);
   if(runtimeLossStreakPauseCount > 0 && lossStreak >= runtimeLossStreakPauseCount) {
      SetLastSignal(idx, "loss_streak_pause streak=" + IntegerToString(lossStreak));
      return;
   }
   string blackoutReason = "";
   if(IsRuntimeBlackoutActive(blackoutReason)) {
      SetLastSignal(idx, "blackout_reject " + blackoutReason);
      return;
   }

   int autoCount = CountPositionsByMagic(symbol, MagicNumber);
   int maxAutoPositions = MaxAutoPositionsForSymbol(symbol);
   if(autoCount >= maxAutoPositions) {
      SetLastSignal(idx, "max_positions");
      return;
   }
   if(TimeCurrent() - lastTradeTimes[idx] < MinSecondsBetweenTradesForSymbol(symbol)) {
      SetLastSignal(idx, "cooldown");
      return;
   }

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int bars = CopyRates(symbol, runtimeAutoTimeframe, 0, 100, rates);
   if(bars < 55) {
      SetLastSignal(idx, "not_enough_bars");
      return;
   }

   int emaFastHandle = iMA(symbol, runtimeAutoTimeframe, FastEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   int emaSlowHandle = iMA(symbol, runtimeAutoTimeframe, SlowEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   int emaTrendHandle = iMA(symbol, runtimeAutoTimeframe, TrendEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   int rsiHandle = iRSI(symbol, runtimeAutoTimeframe, RsiPeriod, PRICE_CLOSE);
   int macdHandle = iMACD(symbol, runtimeAutoTimeframe, 12, 26, 9, PRICE_CLOSE);
   int atrHandle = iATR(symbol, runtimeAutoTimeframe, AtrPeriod);
   int adxHandle = iADX(symbol, runtimeAutoTimeframe, AdxPeriod);
   if(emaFastHandle == INVALID_HANDLE || emaSlowHandle == INVALID_HANDLE || emaTrendHandle == INVALID_HANDLE || rsiHandle == INVALID_HANDLE || macdHandle == INVALID_HANDLE || atrHandle == INVALID_HANDLE || adxHandle == INVALID_HANDLE) {
      SetLastSignal(idx, "indicator_handle_failed");
      return;
   }

   double emaFast[], emaSlow[], emaTrend[], rsi[], macdMain[], macdSignal[], atr[], adxVal[];
   ArraySetAsSeries(emaFast, true);
   ArraySetAsSeries(emaSlow, true);
   ArraySetAsSeries(emaTrend, true);
   ArraySetAsSeries(rsi, true);
   ArraySetAsSeries(macdMain, true);
   ArraySetAsSeries(macdSignal, true);
   ArraySetAsSeries(atr, true);
   ArraySetAsSeries(adxVal, true);

   int atrCopied = CopyBuffer(atrHandle, 0, 0, 60, atr);
   bool copied = CopyBuffer(emaFastHandle, 0, 0, 5, emaFast) >= 5 &&
                 CopyBuffer(emaSlowHandle, 0, 0, 5, emaSlow) >= 5 &&
                 CopyBuffer(emaTrendHandle, 0, 0, 5, emaTrend) >= 5 &&
                 CopyBuffer(rsiHandle, 0, 0, 5, rsi) >= 5 &&
                 CopyBuffer(macdHandle, 0, 0, 5, macdMain) >= 5 &&
                 CopyBuffer(macdHandle, 1, 0, 5, macdSignal) >= 5 &&
                 atrCopied >= 5 &&
                 CopyBuffer(adxHandle, 0, 0, 3, adxVal) >= 3;
   IndicatorRelease(emaFastHandle);
   IndicatorRelease(emaSlowHandle);
   IndicatorRelease(emaTrendHandle);
   IndicatorRelease(rsiHandle);
   IndicatorRelease(macdHandle);
   IndicatorRelease(atrHandle);
   IndicatorRelease(adxHandle);
   if(!copied) {
      SetLastSignal(idx, "indicator_copy_failed");
      return;
   }

   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   double spreadPoints = point > 0 ? (ask - bid) / point : 0.0;
   if(spreadPoints > MaxSpreadForSymbol(symbol)) {
      SetLastSignal(idx, "spread_too_wide " + DoubleToString(spreadPoints, 1));
      return;
   }

   double current = rates[0].close;
   double previous = rates[1].close;
   double momentum3 = (rates[0].close - rates[3].close) / rates[3].close;
   double macdHist = macdMain[0] - macdSignal[0];
   double prevMacdHist = macdMain[1] - macdSignal[1];
   bool bullishCross = emaFast[1] <= emaSlow[1] && emaFast[0] > emaSlow[0];
   bool bearishCross = emaFast[1] >= emaSlow[1] && emaFast[0] < emaSlow[0];
   bool quickMomentumLong = emaFast[0] > emaSlow[0] && previous <= emaFast[1] && current > emaFast[0] && rsi[0] >= 42 && rsi[0] < 68;
   bool quickMomentumShort = emaFast[0] < emaSlow[0] && previous >= emaFast[1] && current < emaFast[0] && rsi[0] <= 58 && rsi[0] > 32;
   bool macdLong = macdHist > 0 && macdHist > prevMacdHist && current > emaTrend[0] && rsi[0] >= 45 && rsi[0] < 68;
   bool macdShort = macdHist < 0 && macdHist < prevMacdHist && current < emaTrend[0] && rsi[0] <= 55 && rsi[0] > 32;
   bool rsiReversionLong = rsi[1] < 30 && rsi[0] > rsi[1] && current > previous;
   bool rsiReversionShort = rsi[1] > 70 && rsi[0] < rsi[1] && current < previous;
   bool atrImpulseLong = current > rates[1].high && (current - previous) > atr[0] * runtimeAtrImpulseMultiplier && rsi[0] < 80;
   bool atrImpulseShort = current < rates[1].low && (previous - current) > atr[0] * runtimeAtrImpulseMultiplier && rsi[0] > 20;
   if(isXau) {
      if(!runtimeEnableXauRsiReversion) { rsiReversionLong = false; rsiReversionShort = false; }
      if(!runtimeEnableXauAtrImpulse) { atrImpulseLong = false; atrImpulseShort = false; }
   }
   if(DisableWeakStrategySignals) {
      if(isXau) { bullishCross = false; bearishCross = false; macdLong = false; macdShort = false; }
   }

   int side = 0;
   string reason = "none";
   if(bullishCross || quickMomentumLong || macdLong || rsiReversionLong || atrImpulseLong || (momentum3 > 0.0015 && current > emaTrend[0] && rsi[0] < 70)) {
      side = 1;
      reason = bullishCross ? "QuickMomentum_EMA_cross" : (macdLong ? "MACD_trend" : (rsiReversionLong ? "RSI_reversion" : (atrImpulseLong ? "ATR_impulse" : "Momentum_trend")));
   } else if(bearishCross || quickMomentumShort || macdShort || rsiReversionShort || atrImpulseShort || (momentum3 < -0.0015 && current < emaTrend[0] && rsi[0] > 30)) {
      side = -1;
      reason = bearishCross ? "QuickMomentum_EMA_cross" : (macdShort ? "MACD_trend" : (rsiReversionShort ? "RSI_reversion" : (atrImpulseShort ? "ATR_impulse" : "Momentum_trend")));
   }

   double entry = side > 0 ? ask : bid;
   double atrValue = atr[0] > 0 ? atr[0] : current * 0.0015;
   double pda = PremiumDiscountPosition(rates, SmcLookbackBars, current);

   if(side == 0) {
      SetLastSignal(idx, "no_signal rsi=" + DoubleToString(rsi[0], 1) + " mom3=" + DoubleToString(momentum3 * 100.0, 3) + "% pda=" + DoubleToString(pda, 2));
      return;
   }

   if(AtrRegimeTooHot(atrValue, atr, atrCopied)) {
      SetLastSignal(idx, "atr_regime_reject atr=" + DoubleToString(atrValue, digits));
      return;
   }

   if(runtimeEnableAdxRegimeFilter && adxVal[0] < runtimeAdxMinThreshold) {
      SetLastSignal(idx, "adx_regime_reject " + reason + " adx=" + DoubleToString(adxVal[0], 1));
      return;
   }

   if(runtimeEnableMacdHistogramAlignment) {
      bool macdAligned = side > 0
         ? macdHist > 0.0 && macdHist > prevMacdHist
         : macdHist < 0.0 && macdHist < prevMacdHist;
      if(!macdAligned) {
         SetLastSignal(idx, "direction_reject " + reason + " macd_hist");
         return;
      }
   }

   int sameSidePositions = CountPositionsByMagicAndSide(symbol, MagicNumber, side);
   if(sameSidePositions >= runtimeMaxSameDirectionPositionsPerSymbol) {
      SetLastSignal(idx, "same_side_max " + reason + " side=" + (side > 0 ? "BUY" : "SELL"));
      return;
   }

   if(isXau) {
      if(side > 0 && pda > runtimePdaLongCeiling) {
         SetLastSignal(idx, "xau_pda_reject " + reason + " pda=" + DoubleToString(pda, 2));
         return;
      }
      if(side < 0 && pda < runtimePdaShortFloor) {
         SetLastSignal(idx, "xau_pda_reject " + reason + " pda=" + DoubleToString(pda, 2));
         return;
      }
   }

   int smcScore = side > 0 
      ? SmartMoneyLongScore(rates, atrValue, entry, SmcLookbackBars, runtimeDiscountThreshold, runtimeFvgMinAtrMultiplier, runtimeLiquiditySweepAtrMultiplier)
      : SmartMoneyShortScore(rates, atrValue, entry, SmcLookbackBars, runtimePremiumThreshold, runtimeFvgMinAtrMultiplier, runtimeLiquiditySweepAtrMultiplier);
   int minSmc = MinSmcConfluenceForSymbol(symbol);
   if(runtimeEnableSmartMoneyGates && smcScore < minSmc) {
      SetLastSignal(idx, "smc_reject " + reason + " score=" + IntegerToString(smcScore) + " pda=" + DoubleToString(pda, 2));
      return;
   }

   double slDistance = MathMax(atrValue * runtimeStopAtrMultiplier, MinStopDistanceForSymbol(symbol, entry));
   double tpDistance = slDistance * runtimeTakeProfitAtrMultiplier;
   double sl = side > 0 ? entry - slDistance : entry + slDistance;
   double tp = side > 0 ? entry + tpDistance : entry - tpDistance;
   double volume = UseDailyRiskLotSizing ? DailyRiskVolume(symbol, slDistance, smcScore) : BaseLotForSymbol(symbol);
   volume = NormalizeVolume(symbol, volume);
   if(volume <= 0.0) {
      SetLastSignal(idx, "risk_volume_zero");
      return;
   }

   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(DefaultDeviationPoints);
   bool ok = side > 0
      ? trade.Buy(volume, symbol, 0.0, sl, tp, "FinRobot_" + symbol + "_" + reason)
      : trade.Sell(volume, symbol, 0.0, sl, tp, "FinRobot_" + symbol + "_" + reason);
   if(ok) {
      lastTradeTimes[idx] = TimeCurrent();
      SetLastSignal(idx, (side > 0 ? "BUY " : "SELL ") + reason + " smc=" + IntegerToString(smcScore) + " pda=" + DoubleToString(pda, 2) + " vol=" + DoubleToString(volume, 4));
      AppendAck(AckFile, ++lastCommandId, "AUTO_FILLED", symbol + " strategy " + lastSignals[idx], symbol, (side > 0 ? "BUY" : "SELL"), volume, entry);
   } else {
      SetLastSignal(idx, "order_failed " + IntegerToString((int)trade.ResultRetcode()) + " " + trade.ResultRetcodeDescription());
      AppendAck(AckFile, ++lastCommandId, "AUTO_REJECTED", lastSignals[idx], symbol, (side > 0 ? "BUY" : "SELL"), volume, entry);
   }
}

void WritePositions() {
   int h = FileOpen(PositionsFile, FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h == INVALID_HANDLE) return;
   FileWriteString(h, "time,ticket,symbol,type,volume,open_price,current_price,profit,sl,tp,comment\n");
   for(int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      string symbol = PositionGetString(POSITION_SYMBOL);
      if(!IsManagedSymbol(symbol)) continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
      string type = PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY ? "BUY" : "SELL";
      FileWriteString(h,
         TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "," +
         IntegerToString((int)ticket) + "," + symbol + "," + type + "," +
         DoubleToString(PositionGetDouble(POSITION_VOLUME), 4) + "," +
         DoubleToString(PositionGetDouble(POSITION_PRICE_OPEN), digits) + "," +
         DoubleToString(PositionGetDouble(POSITION_PRICE_CURRENT), digits) + "," +
         DoubleToString(PositionGetDouble(POSITION_PROFIT), 2) + "," +
         DoubleToString(PositionGetDouble(POSITION_SL), digits) + "," +
         DoubleToString(PositionGetDouble(POSITION_TP), digits) + "," +
         Clean(PositionGetString(POSITION_COMMENT)) + "\n"
      );
   }
   FileClose(h);
}

void WriteDealsHistory() {
   int h = FileOpen(DealsFile, FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h == INVALID_HANDLE) return;
   FileWriteString(h, "time,ticket,order,position_id,symbol,entry,type,volume,price,profit,commission,swap,comment\n");
   datetime fromTime = TimeCurrent() - 86400 * 14;
   if(!HistorySelect(fromTime, TimeCurrent())) {
      FileClose(h);
      return;
   }
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++) {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;
      string symbol = HistoryDealGetString(ticket, DEAL_SYMBOL);
      if(!IsManagedSymbol(symbol)) continue;
      if((int)HistoryDealGetInteger(ticket, DEAL_MAGIC) != MagicNumber) continue;
      int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
      datetime t = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
      FileWriteString(h,
         TimeToString(t, TIME_DATE|TIME_SECONDS) + "," +
         IntegerToString((int)ticket) + "," +
         IntegerToString((int)HistoryDealGetInteger(ticket, DEAL_ORDER)) + "," +
         IntegerToString((int)HistoryDealGetInteger(ticket, DEAL_POSITION_ID)) + "," +
         symbol + "," +
         IntegerToString((int)HistoryDealGetInteger(ticket, DEAL_ENTRY)) + "," +
         IntegerToString((int)HistoryDealGetInteger(ticket, DEAL_TYPE)) + "," +
         DoubleToString(HistoryDealGetDouble(ticket, DEAL_VOLUME), 4) + "," +
         DoubleToString(HistoryDealGetDouble(ticket, DEAL_PRICE), digits) + "," +
         DoubleToString(HistoryDealGetDouble(ticket, DEAL_PROFIT), 2) + "," +
         DoubleToString(HistoryDealGetDouble(ticket, DEAL_COMMISSION), 2) + "," +
         DoubleToString(HistoryDealGetDouble(ticket, DEAL_SWAP), 2) + "," +
         Clean(HistoryDealGetString(ticket, DEAL_COMMENT)) + "\n"
      );
   }
   FileClose(h);
}

int OnInit() {
   // Read release manifest if present (graceful fallback when missing).
   {
      int mh = FileOpen("EA_MANIFEST.txt", FILE_READ|FILE_TXT|FILE_ANSI|FILE_COMMON);
      if(mh != INVALID_HANDLE) {
         while(!FileIsEnding(mh)) {
            string line = FileReadString(mh);
            line = Trim(line);
            if(line == "" || StringFind(line, "=") <= 0) continue;
            string kv[];
            int n = StringSplit(line, '=', kv);
            if(n < 2) continue;
            string key = Trim(kv[0]);
            string val = Trim(kv[1]);
            if(key == "ea_version") EaVersion = val;
            else if(key == "git_sha") EaGitSha = val;
         }
         FileClose(mh);
      }
   }
   EventSetTimer(MathMax(PollSeconds, 1));
   trade.SetExpertMagicNumber(MagicNumber);
   LoadManagedSymbols();
   LoadRuntimeStrategyProfile();
   UpdateMoneyManagementState();
   Print("FinRobotBridgeEA 1.40 initialized. AutoTradeMT5=", AutoTradeMT5, " symbols=", AutoSymbols, " timeframe=", EnumToString(runtimeAutoTimeframe), " profile=", activeProfileName, " profile_loaded=", runtimeProfileLoaded, " entry_pause=", IsEntryPauseActive());
   WriteStatus();
   WritePositions();
   WriteDealsHistory();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) {
   EventKillTimer();
   WriteStatus();
   WritePositions();
   WriteDealsHistory();
}

void OnTimer() {
   timerTicks++;
   if(timerTicks % 30 == 0) LoadRuntimeStrategyProfile();
   PollCommands();
   EnforceManagedRisk();
   ApplyDynamicBreakEven(EnableDynamicBreakEven, AllowTrading, MagicNumber, BreakEvenRrRatio, BreakEvenExtraPoints, trade);
   for(int i = 0; i < ArraySize(managedSymbols); i++) {
      ManageAutoSymbol(managedSymbols[i], i);
   }
   WriteResearchBars();
   WriteStatus();
   WritePositions();
   if(timerTicks % 10 == 0) WriteDealsHistory();
}

void OnTick() {
   WriteStatus();
}
