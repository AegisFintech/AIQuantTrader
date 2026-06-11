#property strict
#property description "FinRobot MT5 bridge and demo auto trader for XAUUSD + BTCUSD."
#property version "1.30"

#include <Trade/Trade.mqh>
#include "BridgeIO.mqh"
#include "SmartMoney.mqh"
#include "RiskManagement.mqh"

input string CommandFile = "finrobot_commands.csv";
input string AckFile = "finrobot_acks.csv";
input string StatusFile = "finrobot_status.json";
input string PositionsFile = "finrobot_positions.csv";
input string DealsFile = "finrobot_deals.csv";
input int PollSeconds = 1;
input int MagicNumber = 20260522;
input int DefaultDeviationPoints = 30;
input bool AllowTrading = true;
input bool AutoTradeMT5 = true;
input string AutoSymbols = "XAUUSD,BTCUSD";
input ENUM_TIMEFRAMES AutoTimeframe = PERIOD_M5;
input double XauBaseLot = 0.01;
input double BtcBaseLot = 0.01;
input double MaxLotPerTrade = 0.25;
input double MaxLotPerTradeBTCUSD = 0.25;
input double MaxLotPerTradeXAUUSD = 0.10;
input double HighConfluenceLotMultiplier = 3.0;
input int MinSmcConfluenceScore = 3;
input int MinSmcConfluenceScoreBTCUSD = 2;
input int MinSmcConfluenceScoreXAUUSD = 3;
input int HighConfluenceScore = 5;
input bool UseDailyRiskLotSizing = true;
input double DailyRiskPerTradeFraction = 0.0010;   // 0.10% of equity per trade
input double DailyLossLimitFraction = 0.01;        // 1.00% of equity daily cap
input bool AutoClosePositionsWithoutStops = true;
input bool DisableWeakStrategySignals = true;
input int MaxAutoPositionsPerSymbol = 2;
input int MaxAutoPositionsBTCUSD = 2;
input int MaxAutoPositionsXAUUSD = 2;
input int MaxSameDirectionPositionsPerSymbol = 2;
input int MinSecondsBetweenTrades = 300;
input int MinSecondsBetweenTradesBTCUSD = 300;
input int MinSecondsBetweenTradesXAUUSD = 180;
input int FastEmaPeriod = 9;
input int SlowEmaPeriod = 21;
input int TrendEmaPeriod = 50;
input int RsiPeriod = 14;
input int AtrPeriod = 14;
input double StopAtrMultiplier = 1.2;
input double TakeProfitAtrMultiplier = 1.8;
input double MaxSpreadPointsXAUUSD = 80.0;
input double MaxSpreadPointsBTCUSD = 5000.0;
input bool EnableSmartMoneyGates = true;
input bool EnableXauAutoTrading = true;
input int SmcLookbackBars = 48;
input double FvgMinAtrMultiplier = 0.15;
input double DiscountThreshold = 0.38;
input double PremiumThreshold = 0.62;
input double LiquiditySweepAtrMultiplier = 0.10;
input double MinTrendSlopeAtrMultiplier = 0.04;
input bool EnableBtcRsiReversion = false;
input bool EnableBtcAtrImpulse = false;
input bool EnableBtcMomentumTrend = false;
input bool EnableBtcMacdTrend = false;
input bool EnableBtcQuickMomentum = true;
input bool EnableXauRsiReversion = false;
input bool EnableXauAtrImpulse = true;
input bool EnableSessionGating = true;
input bool EnableBtcContinuousTrading = true;
input bool EnableXauWeekdayMarketHours = true;
input bool EnableBtcCostFilters = true;
input double MaxBtcSpreadAtrRatio = 0.15;
input double MaxBtcSpreadTakeProfitRatio = 0.08;
input int LondonStartHour = 7;
input int LondonEndHour = 11;
input int NyStartHour = 13;
input int NyEndHour = 17;
input bool EnableDynamicBreakEven = true;
input double BreakEvenRrRatio = 1.0;
input double BreakEvenExtraPoints = 10.0;

CTrade trade;
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
int moneyManagementDay = 0;
double dailyEquitySnapshot = 0.0;
double todayClosedPnlCache = 0.0;
datetime lastMoneyManagementUpdate = 0;
string riskCloseAttemptTickets = "";
int riskCloseAttemptDay = 0;

bool IsBtcSymbol(string symbol) {
   string s = Upper(symbol);
   return StringFind(s, "BTC") >= 0;
}

bool IsXauSymbol(string symbol) {
   string s = Upper(symbol);
   return StringFind(s, "XAU") >= 0 || StringFind(s, "GOLD") >= 0;
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
   else if(StringFind(signal, "spread_too_wide") == 0 || StringFind(signal, "btc_cost_reject") == 0) spreadRejectCounts[idx]++;
   else if(StringFind(signal, "no_signal") == 0) noSignalCounts[idx]++;
   else if(StringFind(signal, "smc_reject") == 0) smcRejectCounts[idx]++;
   else if(StringFind(signal, "btc_direction_reject") == 0) directionRejectCounts[idx]++;
   else if(StringFind(signal, "xau_pda_reject") == 0) pdaRejectCounts[idx]++;
   else if(StringFind(signal, "max_positions") == 0 || StringFind(signal, "same_side_max") == 0 || StringFind(signal, "cooldown") == 0) positionRejectCounts[idx]++;
   else if(StringFind(signal, "order_failed") == 0 || StringFind(signal, "risk_volume_zero") == 0) orderRejectCounts[idx]++;
}

void SetLastSignal(int idx, string signal) {
   if(idx < 0 || idx >= ArraySize(managedSymbols)) return;
   EnsureSignalTelemetryDay();
   lastSignals[idx] = signal;
   CountSignalTelemetry(idx, signal);
}

bool UseSessionGateForSymbol(string symbol) {
   if(!EnableSessionGating) return false;
   if(IsBtcSymbol(symbol) && EnableBtcContinuousTrading) return false;
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

bool BtcCostFilterReject(double spreadPrice, double atrValue, double tpDistance, string &detail) {
   if(!EnableBtcCostFilters) return false;
   double atrRatio = atrValue > 0.0 ? spreadPrice / atrValue : 0.0;
   double tpRatio = tpDistance > 0.0 ? spreadPrice / tpDistance : 0.0;
   if(MaxBtcSpreadAtrRatio > 0.0 && atrRatio > MaxBtcSpreadAtrRatio) {
      detail = "spread_atr=" + DoubleToString(atrRatio, 3) + " max=" + DoubleToString(MaxBtcSpreadAtrRatio, 3);
      return true;
   }
   if(MaxBtcSpreadTakeProfitRatio > 0.0 && tpRatio > MaxBtcSpreadTakeProfitRatio) {
      detail = "spread_tp=" + DoubleToString(tpRatio, 3) + " max=" + DoubleToString(MaxBtcSpreadTakeProfitRatio, 3);
      return true;
   }
   detail = "spread_atr=" + DoubleToString(atrRatio, 3) + " spread_tp=" + DoubleToString(tpRatio, 3);
   return false;
}

double PremiumDiscountPosition(string symbol, int idx) {
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int bars = CopyRates(symbol, AutoTimeframe, 0, MathMax(SmcLookbackBars + 5, 60), rates);
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
      ArrayResize(managedSymbols, 2);
      managedSymbols[0] = "XAUUSD";
      managedSymbols[1] = "BTCUSD";
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
      ArrayResize(managedSymbols, 2);
      managedSymbols[0] = "XAUUSD";
      managedSymbols[1] = "BTCUSD";
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
   if(IsBtcSymbol(symbol)) return MathMax(0, MaxAutoPositionsBTCUSD);
   if(IsXauSymbol(symbol)) return MathMax(0, MaxAutoPositionsXAUUSD);
   return MathMax(0, MaxAutoPositionsPerSymbol);
}

int MinSecondsBetweenTradesForSymbol(string symbol) {
   if(IsBtcSymbol(symbol)) return MathMax(0, MinSecondsBetweenTradesBTCUSD);
   if(IsXauSymbol(symbol)) return MathMax(0, MinSecondsBetweenTradesXAUUSD);
   return MathMax(0, MinSecondsBetweenTrades);
}

int MinSmcConfluenceForSymbol(string symbol) {
   if(IsBtcSymbol(symbol)) return MathMax(1, MinSmcConfluenceScoreBTCUSD);
   if(IsXauSymbol(symbol)) return MathMax(1, MinSmcConfluenceScoreXAUUSD);
   return MathMax(1, MinSmcConfluenceScore);
}

double MaxLotForSymbol(string symbol) {
   if(IsBtcSymbol(symbol)) return MathMax(0.0, MathMin(MaxLotPerTrade, MaxLotPerTradeBTCUSD));
   if(IsXauSymbol(symbol)) return MathMax(0.0, MathMin(MaxLotPerTrade, MaxLotPerTradeXAUUSD));
   return MathMax(0.0, MaxLotPerTrade);
}

int HigherTimeframeTrend(string symbol) {
   MqlRates h1[];
   ArraySetAsSeries(h1, true);
   if(CopyRates(symbol, PERIOD_H1, 0, 240, h1) < 210) return 0;
   int emaFastHandle = iMA(symbol, PERIOD_H1, 50, 0, MODE_EMA, PRICE_CLOSE);
   int emaSlowHandle = iMA(symbol, PERIOD_H1, 200, 0, MODE_EMA, PRICE_CLOSE);
   if(emaFastHandle == INVALID_HANDLE || emaSlowHandle == INVALID_HANDLE) {
      if(emaFastHandle != INVALID_HANDLE) IndicatorRelease(emaFastHandle);
      if(emaSlowHandle != INVALID_HANDLE) IndicatorRelease(emaSlowHandle);
      return 0;
   }
   double emaFast[], emaSlow[];
   ArraySetAsSeries(emaFast, true);
   ArraySetAsSeries(emaSlow, true);
   bool copied = CopyBuffer(emaFastHandle, 0, 0, 5, emaFast) >= 5 && CopyBuffer(emaSlowHandle, 0, 0, 5, emaSlow) >= 5;
   IndicatorRelease(emaFastHandle);
   IndicatorRelease(emaSlowHandle);
   if(!copied) return 0;
   double slope = emaFast[0] - emaFast[3];
   if(emaFast[0] > emaSlow[0] && slope > 0.0) return 1;
   if(emaFast[0] < emaSlow[0] && slope < 0.0) return -1;
   return 0;
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
   payload += "\"order_reject\":" + IntegerToString(orderRejectCounts[idx]);
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
   payload += "\"symbol\":\"" + Clean(AutoSymbols) + "\",";
   payload += "\"last_auto_signal\":\"" + Clean(CombinedSignals()) + "\",";
   payload += "\"last_command_id\":" + IntegerToString(lastCommandId) + ",";
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
   double limitMoney = dailyEquitySnapshot * DailyLossLimitFraction;
   return limitMoney > 0.0 && todayClosedPnlCache <= -limitMoney;
}

bool DailyLossLimitReached() {
   UpdateMoneyManagementState();
   return IsDailyLossLimitReached();
}

double BaseLotForSymbol(string symbol) {
   if(IsBtcSymbol(symbol)) return BtcBaseLot;
   return XauBaseLot;
}

double DailyRiskVolume(string symbol, double slDistance, int confluenceScore) {
   UpdateMoneyManagementState();
   double tickSize = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tickSize <= 0.0 || tickValue <= 0.0 || slDistance <= 0.0 || dailyEquitySnapshot <= 0.0) {
      return NormalizeVolume(symbol, BaseLotForSymbol(symbol));
   }
   double riskMoney = dailyEquitySnapshot * DailyRiskPerTradeFraction;
   if(confluenceScore >= HighConfluenceScore) riskMoney *= MathMax(1.0, HighConfluenceLotMultiplier);
   if(todayClosedPnlCache < 0.0) riskMoney *= 0.5;
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
   payload += "\"daily_risk_per_trade_fraction\":" + DoubleToString(DailyRiskPerTradeFraction, 6) + ",";
   payload += "\"daily_loss_limit_fraction\":" + DoubleToString(DailyLossLimitFraction, 4) + ",";
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
   if(IsBtcSymbol(symbol)) return MaxSpreadPointsBTCUSD;
   return MaxSpreadPointsXAUUSD;
}

double MinStopDistanceForSymbol(string symbol, double entry) {
   if(IsBtcSymbol(symbol)) return MathMax(entry * 0.003, 100.0);
   return MathMax(entry * 0.00045, 2.0);
}

void ManageAutoSymbol(string symbol, int idx) {
   bool isBtc = IsBtcSymbol(symbol);
   bool isXau = IsXauSymbol(symbol);
   if(!AutoTradeMT5 || !AllowTrading) {
      SetLastSignal(idx, "auto_trading_disabled");
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
   int bars = CopyRates(symbol, AutoTimeframe, 0, 100, rates);
   if(bars < 55) {
      SetLastSignal(idx, "not_enough_bars");
      return;
   }

   int emaFastHandle = iMA(symbol, AutoTimeframe, FastEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   int emaSlowHandle = iMA(symbol, AutoTimeframe, SlowEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   int emaTrendHandle = iMA(symbol, AutoTimeframe, TrendEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   int rsiHandle = iRSI(symbol, AutoTimeframe, RsiPeriod, PRICE_CLOSE);
   int macdHandle = iMACD(symbol, AutoTimeframe, 12, 26, 9, PRICE_CLOSE);
   int atrHandle = iATR(symbol, AutoTimeframe, AtrPeriod);
   if(emaFastHandle == INVALID_HANDLE || emaSlowHandle == INVALID_HANDLE || emaTrendHandle == INVALID_HANDLE || rsiHandle == INVALID_HANDLE || macdHandle == INVALID_HANDLE || atrHandle == INVALID_HANDLE) {
      SetLastSignal(idx, "indicator_handle_failed");
      return;
   }

   double emaFast[], emaSlow[], emaTrend[], rsi[], macdMain[], macdSignal[], atr[];
   ArraySetAsSeries(emaFast, true);
   ArraySetAsSeries(emaSlow, true);
   ArraySetAsSeries(emaTrend, true);
   ArraySetAsSeries(rsi, true);
   ArraySetAsSeries(macdMain, true);
   ArraySetAsSeries(macdSignal, true);
   ArraySetAsSeries(atr, true);

   bool copied = CopyBuffer(emaFastHandle, 0, 0, 5, emaFast) >= 5 &&
                 CopyBuffer(emaSlowHandle, 0, 0, 5, emaSlow) >= 5 &&
                 CopyBuffer(emaTrendHandle, 0, 0, 5, emaTrend) >= 5 &&
                 CopyBuffer(rsiHandle, 0, 0, 5, rsi) >= 5 &&
                 CopyBuffer(macdHandle, 0, 0, 5, macdMain) >= 5 &&
                 CopyBuffer(macdHandle, 1, 0, 5, macdSignal) >= 5 &&
                 CopyBuffer(atrHandle, 0, 0, 5, atr) >= 5;
   IndicatorRelease(emaFastHandle);
   IndicatorRelease(emaSlowHandle);
   IndicatorRelease(emaTrendHandle);
   IndicatorRelease(rsiHandle);
   IndicatorRelease(macdHandle);
   IndicatorRelease(atrHandle);
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
   int htfTrend = isBtc ? HigherTimeframeTrend(symbol) : 0;
   bool quickMomentumLong = emaFast[0] > emaSlow[0] && previous <= emaFast[1] && current > emaFast[0] && rsi[0] >= 42 && rsi[0] < 68;
   bool quickMomentumShort = emaFast[0] < emaSlow[0] && previous >= emaFast[1] && current < emaFast[0] && rsi[0] <= 58 && rsi[0] > 32;
   bool macdLong = macdHist > 0 && macdHist > prevMacdHist && current > emaTrend[0] && rsi[0] >= 45 && rsi[0] < 68;
   bool macdShort = macdHist < 0 && macdHist < prevMacdHist && current < emaTrend[0] && rsi[0] <= 55 && rsi[0] > 32;
   bool rsiReversionLong = rsi[1] < 30 && rsi[0] > rsi[1] && current > previous;
   bool rsiReversionShort = rsi[1] > 70 && rsi[0] < rsi[1] && current < previous;
   bool atrImpulseLong = current > rates[1].high && (current - previous) > atr[0] * 0.12 && rsi[0] < 80;
   bool atrImpulseShort = current < rates[1].low && (previous - current) > atr[0] * 0.12 && rsi[0] > 20;
   if(isBtc) {
      if(!EnableBtcQuickMomentum) {
         bullishCross = false;
         bearishCross = false;
         quickMomentumLong = false;
         quickMomentumShort = false;
      }
      if(!EnableBtcMacdTrend) { macdLong = false; macdShort = false; }
      if(!EnableBtcRsiReversion) { rsiReversionLong = false; rsiReversionShort = false; }
      if(!EnableBtcAtrImpulse) { atrImpulseLong = false; atrImpulseShort = false; }
      if(!EnableBtcMomentumTrend) { momentum3 = 0.0; }
      if(htfTrend <= 0) { bullishCross = false; quickMomentumLong = false; macdLong = false; }
      if(htfTrend >= 0) { bearishCross = false; quickMomentumShort = false; macdShort = false; }
   } else if(isXau) {
      if(!EnableXauRsiReversion) { rsiReversionLong = false; rsiReversionShort = false; }
      if(!EnableXauAtrImpulse) { atrImpulseLong = false; atrImpulseShort = false; }
   }
   if(DisableWeakStrategySignals) {
      if(isBtc) { rsiReversionLong = false; rsiReversionShort = false; atrImpulseLong = false; atrImpulseShort = false; }
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

   int sameSidePositions = CountPositionsByMagicAndSide(symbol, MagicNumber, side);
   if(sameSidePositions >= MaxSameDirectionPositionsPerSymbol) {
      SetLastSignal(idx, "same_side_max " + reason + " side=" + (side > 0 ? "BUY" : "SELL"));
      return;
   }

   if(isBtc) {
      if(side > 0 && (htfTrend <= 0 || pda > 0.45)) {
         SetLastSignal(idx, "btc_direction_reject " + reason + " h1=" + IntegerToString(htfTrend) + " pda=" + DoubleToString(pda, 2));
         return;
      }
      if(side < 0 && (htfTrend >= 0 || pda < 0.55)) {
         SetLastSignal(idx, "btc_direction_reject " + reason + " h1=" + IntegerToString(htfTrend) + " pda=" + DoubleToString(pda, 2));
         return;
      }
   }
   if(isXau) {
      if(side > 0 && pda > 0.40) {
         SetLastSignal(idx, "xau_pda_reject " + reason + " pda=" + DoubleToString(pda, 2));
         return;
      }
      if(side < 0 && pda < 0.60) {
         SetLastSignal(idx, "xau_pda_reject " + reason + " pda=" + DoubleToString(pda, 2));
         return;
      }
   }

   int smcScore = side > 0 
      ? SmartMoneyLongScore(rates, atrValue, entry, SmcLookbackBars, DiscountThreshold, FvgMinAtrMultiplier, LiquiditySweepAtrMultiplier) 
      : SmartMoneyShortScore(rates, atrValue, entry, SmcLookbackBars, PremiumThreshold, FvgMinAtrMultiplier, LiquiditySweepAtrMultiplier);
   int minSmc = MinSmcConfluenceForSymbol(symbol);
   if(EnableSmartMoneyGates && smcScore < minSmc) {
      SetLastSignal(idx, "smc_reject " + reason + " score=" + IntegerToString(smcScore) + " pda=" + DoubleToString(pda, 2));
      return;
   }

   double slDistance = MathMax(atrValue * StopAtrMultiplier, MinStopDistanceForSymbol(symbol, entry));
   double tpDistance = slDistance * TakeProfitAtrMultiplier;
   if(isBtc) {
      string costDetail = "";
      if(BtcCostFilterReject(ask - bid, atrValue, tpDistance, costDetail)) {
         SetLastSignal(idx, "btc_cost_reject " + reason + " " + costDetail);
         return;
      }
   }
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
   EventSetTimer(MathMax(PollSeconds, 1));
   trade.SetExpertMagicNumber(MagicNumber);
   LoadManagedSymbols();
   UpdateMoneyManagementState();
   Print("FinRobotBridgeEA 1.30 initialized. AutoTradeMT5=", AutoTradeMT5, " symbols=", AutoSymbols, " timeframe=", EnumToString(AutoTimeframe));
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
   PollCommands();
   EnforceManagedRisk();
   ApplyDynamicBreakEven(EnableDynamicBreakEven, AllowTrading, MagicNumber, BreakEvenRrRatio, BreakEvenExtraPoints, trade);
   for(int i = 0; i < ArraySize(managedSymbols); i++) {
      ManageAutoSymbol(managedSymbols[i], i);
   }
   WriteStatus();
   WritePositions();
   if(timerTicks % 10 == 0) WriteDealsHistory();
}

void OnTick() {
   WriteStatus();
}
