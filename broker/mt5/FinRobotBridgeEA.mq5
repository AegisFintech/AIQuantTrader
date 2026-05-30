#property strict
#property description "FinRobot MT5 bridge and demo auto trader for XAUUSD + BTCUSD."
#property version "1.23"

#include <Trade/Trade.mqh>

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
input double MaxLotPerTrade = 0.05;
input double HighConfluenceLotMultiplier = 2.0;
input int MinSmcConfluenceScore = 3;
input int HighConfluenceScore = 5;
input bool UseDailyRiskLotSizing = true;
input double DailyRiskPerTradePct = 0.00025;
input double DailyLossLimitPct = 0.05;
input bool AutoClosePositionsWithoutStops = true;
input bool DisableWeakStrategySignals = true;
input int MaxAutoPositionsPerSymbol = 1;
input int MinSecondsBetweenTrades = 900;
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
input bool EnableXauAutoTrading = false;
input int SmcLookbackBars = 48;
input double FvgMinAtrMultiplier = 0.15;
input double DiscountThreshold = 0.38;
input double PremiumThreshold = 0.62;
input double LiquiditySweepAtrMultiplier = 0.10;
input double MinTrendSlopeAtrMultiplier = 0.04;

CTrade trade;
int lastCommandId = 0;
int commandFileErrLogged = 0;
int timerTicks = 0;
string managedSymbols[];
string lastSignals[];
datetime lastTradeTimes[];
int moneyManagementDay = 0;
double dailyEquitySnapshot = 0.0;
double todayClosedPnlCache = 0.0;
datetime lastMoneyManagementUpdate = 0;
string riskCloseAttemptTickets = "";
int riskCloseAttemptDay = 0;

string Trim(string s) {
   StringTrimLeft(s);
   StringTrimRight(s);
   return s;
}

string Clean(string s) {
   StringReplace(s, "\"", "'");
   StringReplace(s, "\r", " ");
   StringReplace(s, "\n", " ");
   StringReplace(s, ",", ";");
   return s;
}

string Upper(string s) {
   StringToUpper(s);
   return s;
}

bool IsBtcSymbol(string symbol) {
   string s = Upper(symbol);
   return StringFind(s, "BTC") >= 0;
}

bool IsXauSymbol(string symbol) {
   string s = Upper(symbol);
   return StringFind(s, "XAU") >= 0 || StringFind(s, "GOLD") >= 0;
}

double ClampDouble(double value, double lo, double hi) {
   return MathMax(lo, MathMin(hi, value));
}

double RangeHigh(MqlRates &rates[], int count) {
   int available = ArraySize(rates) - 1;
   int n = MathMin(count, available);
   if(n < 1) return 0.0;
   double high = rates[1].high;
   for(int i = 2; i <= n; i++) {
      if(rates[i].high > high) high = rates[i].high;
   }
   return high;
}

double RangeLow(MqlRates &rates[], int count) {
   int available = ArraySize(rates) - 1;
   int n = MathMin(count, available);
   if(n < 1) return 0.0;
   double low = rates[1].low;
   for(int i = 2; i <= n; i++) {
      if(rates[i].low < low) low = rates[i].low;
   }
   return low;
}

double PremiumDiscountPosition(MqlRates &rates[], int count, double price) {
   double high = RangeHigh(rates, count);
   double low = RangeLow(rates, count);
   if(high <= low) return 0.5;
   return ClampDouble((price - low) / (high - low), 0.0, 1.0);
}

double PremiumDiscountPosition(string symbol, int idx) {
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int bars = CopyRates(symbol, AutoTimeframe, 0, MathMax(SmcLookbackBars + 5, 60), rates);
   if(bars < 10) return 0.5;
   double mid = (SymbolInfoDouble(symbol, SYMBOL_BID) + SymbolInfoDouble(symbol, SYMBOL_ASK)) * 0.5;
   return PremiumDiscountPosition(rates, SmcLookbackBars, mid);
}

bool HasBullishFvg(MqlRates &rates[], int count, double atrValue, double price) {
   int available = ArraySize(rates) - 3;
   int n = MathMin(count, available);
   double minGap = MathMax(atrValue * FvgMinAtrMultiplier, 0.0);
   for(int i = 1; i <= n; i++) {
      double gapLow = rates[i + 2].high;
      double gapHigh = rates[i].low;
      if(gapHigh > gapLow && gapHigh - gapLow >= minGap && price >= gapLow && price <= gapHigh + atrValue * 0.5) return true;
   }
   return false;
}

bool HasBearishFvg(MqlRates &rates[], int count, double atrValue, double price) {
   int available = ArraySize(rates) - 3;
   int n = MathMin(count, available);
   double minGap = MathMax(atrValue * FvgMinAtrMultiplier, 0.0);
   for(int i = 1; i <= n; i++) {
      double gapLow = rates[i].high;
      double gapHigh = rates[i + 2].low;
      if(gapHigh > gapLow && gapHigh - gapLow >= minGap && price <= gapHigh && price >= gapLow - atrValue * 0.5) return true;
   }
   return false;
}

double LastBullishOrderBlockHigh(MqlRates &rates[], int count) {
   int available = ArraySize(rates) - 2;
   int n = MathMin(count, available);
   for(int i = 2; i <= n; i++) {
      if(rates[i].close < rates[i].open && rates[i - 1].close > rates[i].high) return rates[i].high;
   }
   return 0.0;
}

double LastBearishOrderBlockLow(MqlRates &rates[], int count) {
   int available = ArraySize(rates) - 2;
   int n = MathMin(count, available);
   for(int i = 2; i <= n; i++) {
      if(rates[i].close > rates[i].open && rates[i - 1].close < rates[i].low) return rates[i].low;
   }
   return 0.0;
}

bool BullishLiquiditySweep(MqlRates &rates[], int count, double atrValue) {
   int available = ArraySize(rates) - 2;
   int n = MathMin(count, available);
   if(n < 5) return false;
   double priorLow = rates[2].low;
   for(int i = 3; i <= n; i++) {
      if(rates[i].low < priorLow) priorLow = rates[i].low;
   }
   return rates[1].low < priorLow - atrValue * LiquiditySweepAtrMultiplier && rates[1].close > priorLow;
}

bool BearishLiquiditySweep(MqlRates &rates[], int count, double atrValue) {
   int available = ArraySize(rates) - 2;
   int n = MathMin(count, available);
   if(n < 5) return false;
   double priorHigh = rates[2].high;
   for(int i = 3; i <= n; i++) {
      if(rates[i].high > priorHigh) priorHigh = rates[i].high;
   }
   return rates[1].high > priorHigh + atrValue * LiquiditySweepAtrMultiplier && rates[1].close < priorHigh;
}

bool BullishStructureShift(MqlRates &rates[], int count) {
   int available = ArraySize(rates) - 3;
   int n = MathMin(count, available);
   if(n < 6) return false;
   double priorHigh = rates[2].high;
   for(int i = 3; i <= n; i++) {
      if(rates[i].high > priorHigh) priorHigh = rates[i].high;
   }
   return rates[1].close > priorHigh;
}

bool BearishStructureShift(MqlRates &rates[], int count) {
   int available = ArraySize(rates) - 3;
   int n = MathMin(count, available);
   if(n < 6) return false;
   double priorLow = rates[2].low;
   for(int i = 3; i <= n; i++) {
      if(rates[i].low < priorLow) priorLow = rates[i].low;
   }
   return rates[1].close < priorLow;
}

int SmartMoneyLongScore(MqlRates &rates[], double atrValue, double price) {
   double pda = PremiumDiscountPosition(rates, SmcLookbackBars, price);
   double obHigh = LastBullishOrderBlockHigh(rates, SmcLookbackBars);
   bool discount = pda <= DiscountThreshold;
   bool deepDiscount = pda <= MathMax(0.18, DiscountThreshold - 0.12);
   bool hasFvg = HasBullishFvg(rates, SmcLookbackBars, atrValue, price);
   bool reclaimedOrderBlock = obHigh > 0.0 && price >= obHigh && pda <= 0.50;
   bool sweep = BullishLiquiditySweep(rates, SmcLookbackBars, atrValue);
   bool structure = BullishStructureShift(rates, MathMin(SmcLookbackBars, 20));
   int score = 0;
   if(discount) score++;
   if(deepDiscount) score++;
   if(hasFvg) score++;
   if(reclaimedOrderBlock) score++;
   if(sweep) score++;
   if(structure) score++;
   return score;
}

int SmartMoneyShortScore(MqlRates &rates[], double atrValue, double price) {
   double pda = PremiumDiscountPosition(rates, SmcLookbackBars, price);
   double obLow = LastBearishOrderBlockLow(rates, SmcLookbackBars);
   bool premium = pda >= PremiumThreshold;
   bool deepPremium = pda >= MathMin(0.82, PremiumThreshold + 0.12);
   bool hasFvg = HasBearishFvg(rates, SmcLookbackBars, atrValue, price);
   bool rejectedOrderBlock = obLow > 0.0 && price <= obLow && pda >= 0.50;
   bool sweep = BearishLiquiditySweep(rates, SmcLookbackBars, atrValue);
   bool structure = BearishStructureShift(rates, MathMin(SmcLookbackBars, 20));
   int score = 0;
   if(premium) score++;
   if(deepPremium) score++;
   if(hasFvg) score++;
   if(rejectedOrderBlock) score++;
   if(sweep) score++;
   if(structure) score++;
   return score;
}

bool SmartMoneyLongOk(MqlRates &rates[], double atrValue, double price) {
   return SmartMoneyLongScore(rates, atrValue, price) >= MinSmcConfluenceScore;
}

bool SmartMoneyShortOk(MqlRates &rates[], double atrValue, double price) {
   return SmartMoneyShortScore(rates, atrValue, price) >= MinSmcConfluenceScore;
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
   for(int i = 0; i < ArraySize(managedSymbols); i++) {
      if(lastSignals[i] == "") lastSignals[i] = "init";
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

void AppendAck(int id, string status, string message, string symbol, string side, double volume, double price) {
   int h = FileOpen(AckFile, FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h == INVALID_HANDLE) h = FileOpen(AckFile, FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h == INVALID_HANDLE) return;
   FileSeek(h, 0, SEEK_END);
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   FileWriteString(h, IntegerToString(id) + "," + TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "," + status + "," + Clean(message) + "," + symbol + "," + side + "," + DoubleToString(volume, 4) + "," + DoubleToString(price, digits) + "\n");
   FileClose(h);
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

string CombinedSignals() {
   string out = "";
   for(int i = 0; i < ArraySize(managedSymbols); i++) {
      if(i > 0) out += " | ";
      out += managedSymbols[i] + ":" + lastSignals[i];
   }
   return out;
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
   payload += "\"last_signal\":\"" + Clean(lastSignals[idx]) + "\"";
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

int DayStamp(datetime value) {
   MqlDateTime dt;
   TimeToStruct(value, dt);
   return dt.year * 10000 + dt.mon * 100 + dt.day;
}

bool SameDay(datetime value, int stamp) {
   return DayStamp(value) == stamp;
}

double ManagedClosedPnlForDay(int stamp) {
   double pnl = 0.0;
   datetime now = TimeCurrent();
   if(!HistorySelect(now - 86400 * 7, now)) return 0.0;
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++) {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;
      if((int)HistoryDealGetInteger(ticket, DEAL_MAGIC) != MagicNumber) continue;
      string symbol = HistoryDealGetString(ticket, DEAL_SYMBOL);
      if(!IsManagedSymbol(symbol)) continue;
      datetime dealTime = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
      if(!SameDay(dealTime, stamp)) continue;
      pnl += HistoryDealGetDouble(ticket, DEAL_PROFIT);
      pnl += HistoryDealGetDouble(ticket, DEAL_COMMISSION);
      pnl += HistoryDealGetDouble(ticket, DEAL_SWAP);
   }
   return pnl;
}

void UpdateMoneyManagementState() {
   int today = DayStamp(TimeCurrent());
   if(moneyManagementDay != today || dailyEquitySnapshot <= 0.0) {
      moneyManagementDay = today;
      dailyEquitySnapshot = AccountInfoDouble(ACCOUNT_EQUITY);
   }
   todayClosedPnlCache = ManagedClosedPnlForDay(today);
   lastMoneyManagementUpdate = TimeCurrent();
}

bool IsDailyLossLimitReached() {
   double limitMoney = dailyEquitySnapshot * DailyLossLimitPct / 100.0;
   return limitMoney > 0.0 && todayClosedPnlCache <= -limitMoney;
}

bool DailyLossLimitReached() {
   UpdateMoneyManagementState();
   return IsDailyLossLimitReached();
}

double DailyRiskVolume(string symbol, double slDistance, int confluenceScore) {
   UpdateMoneyManagementState();
   double tickSize = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE);
   double tickValue = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE);
   if(tickSize <= 0.0 || tickValue <= 0.0 || slDistance <= 0.0 || dailyEquitySnapshot <= 0.0) {
      return NormalizeVolume(symbol, BaseLotForSymbol(symbol));
   }
   double riskMoney = dailyEquitySnapshot * DailyRiskPerTradePct / 100.0;
   if(confluenceScore >= HighConfluenceScore) riskMoney *= MathMax(1.0, HighConfluenceLotMultiplier);
   if(todayClosedPnlCache < 0.0) riskMoney *= 0.5;
   double riskPerLot = (slDistance / tickSize) * tickValue;
   if(riskPerLot <= 0.0) return NormalizeVolume(symbol, BaseLotForSymbol(symbol));
   double volume = riskMoney / riskPerLot;
   volume = MathMin(MaxLotPerTrade, MathMax(0.0, volume));
   return NormalizeVolume(symbol, volume);
}

string MoneyManagementJson() {
   UpdateMoneyManagementState();
   string payload = "{";
   payload += "\"day\":" + IntegerToString(moneyManagementDay) + ",";
   payload += "\"daily_equity_snapshot\":" + DoubleToString(dailyEquitySnapshot, 2) + ",";
   payload += "\"today_closed_pnl\":" + DoubleToString(todayClosedPnlCache, 2) + ",";
   payload += "\"daily_risk_per_trade_pct\":" + DoubleToString(DailyRiskPerTradePct, 6) + ",";
   payload += "\"daily_loss_limit_pct\":" + DoubleToString(DailyLossLimitPct, 4) + ",";
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
         AppendAck(++lastCommandId, ok ? "RISK_CLOSED" : "RISK_CLOSE_FAILED", detail, symbol, "CLOSE", PositionGetDouble(POSITION_VOLUME), PositionGetDouble(POSITION_PRICE_CURRENT));
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
      AppendAck(id, "REJECTED", "AllowTrading=false", symbol, side, volume, 0.0);
      return;
   }
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED)) {
      AppendAck(id, "REJECTED", "AutoTrading not allowed in terminal or EA", symbol, side, volume, 0.0);
      return;
   }
   if(!EnsureSymbol(symbol)) {
      AppendAck(id, "REJECTED", "SymbolSelect failed", symbol, side, volume, 0.0);
      return;
   }

   volume = NormalizeVolume(symbol, volume);
   bool ok = false;
   if(action == "MARKET") {
      if(side == "BUY") ok = trade.Buy(volume, symbol, 0.0, sl, tp, comment);
      else if(side == "SELL") ok = trade.Sell(volume, symbol, 0.0, sl, tp, comment);
      else AppendAck(id, "REJECTED", "Unknown side", symbol, side, volume, 0.0);
   } else if(action == "CLOSE") {
      ok = trade.PositionClose(symbol, deviation);
   } else if(action == "CLOSE_ALL") {
      ok = CloseAllSymbolPositions(symbol);
   } else {
      AppendAck(id, "REJECTED", "Unknown action", symbol, side, volume, 0.0);
      return;
   }

   if(ok) AppendAck(id, "OK", IntegerToString((int)trade.ResultRetcode()) + " " + trade.ResultRetcodeDescription(), symbol, side, volume, trade.ResultPrice());
   else AppendAck(id, "ERROR", IntegerToString((int)trade.ResultRetcode()) + " " + trade.ResultRetcodeDescription(), symbol, side, volume, trade.ResultPrice());
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

double BaseLotForSymbol(string symbol) {
   if(IsBtcSymbol(symbol)) return BtcBaseLot;
   return XauBaseLot;
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
   if(!AutoTradeMT5 || !AllowTrading) return;
   if(AccountInfoInteger(ACCOUNT_TRADE_ALLOWED) == 0 || MQLInfoInteger(MQL_TRADE_ALLOWED) == 0) return;
   if(DailyLossLimitReached()) {
      lastSignals[idx] = "daily_loss_limit";
      return;
   }
   if(!SymbolSelect(symbol, true)) {
      lastSignals[idx] = "symbol_select_failed";
      return;
   }
   if(!EnableXauAutoTrading && IsXauSymbol(symbol)) {
      lastSignals[idx] = "xau_auto_disabled negative_expectancy";
      return;
   }

   int autoCount = CountPositionsByMagic(symbol, MagicNumber);
   if(autoCount >= MaxAutoPositionsPerSymbol) {
      lastSignals[idx] = "max_positions";
      return;
   }
   if(TimeCurrent() - lastTradeTimes[idx] < MinSecondsBetweenTrades) {
      lastSignals[idx] = "cooldown";
      return;
   }

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int bars = CopyRates(symbol, AutoTimeframe, 0, 100, rates);
   if(bars < 55) {
      lastSignals[idx] = "not_enough_bars";
      return;
   }

   int emaFastHandle = iMA(symbol, AutoTimeframe, FastEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   int emaSlowHandle = iMA(symbol, AutoTimeframe, SlowEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   int emaTrendHandle = iMA(symbol, AutoTimeframe, TrendEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   int rsiHandle = iRSI(symbol, AutoTimeframe, RsiPeriod, PRICE_CLOSE);
   int macdHandle = iMACD(symbol, AutoTimeframe, 12, 26, 9, PRICE_CLOSE);
   int atrHandle = iATR(symbol, AutoTimeframe, AtrPeriod);
   if(emaFastHandle == INVALID_HANDLE || emaSlowHandle == INVALID_HANDLE || emaTrendHandle == INVALID_HANDLE || rsiHandle == INVALID_HANDLE || macdHandle == INVALID_HANDLE || atrHandle == INVALID_HANDLE) {
      lastSignals[idx] = "indicator_handle_failed";
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
      lastSignals[idx] = "indicator_copy_failed";
      return;
   }

   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   double spreadPoints = point > 0 ? (ask - bid) / point : 0.0;
   if(spreadPoints > MaxSpreadForSymbol(symbol)) {
      lastSignals[idx] = "spread_too_wide " + DoubleToString(spreadPoints, 1);
      return;
   }

   double current = rates[0].close;
   double previous = rates[1].close;
   double momentum3 = (rates[0].close - rates[3].close) / rates[3].close;
   double macdHist = macdMain[0] - macdSignal[0];
   double prevMacdHist = macdMain[1] - macdSignal[1];
   bool bullishCross = emaFast[1] <= emaSlow[1] && emaFast[0] > emaSlow[0];
   bool bearishCross = emaFast[1] >= emaSlow[1] && emaFast[0] < emaSlow[0];
   bool quickMomentumLong = emaFast[0] > emaSlow[0] && previous <= emaFast[1] && current > emaFast[0] && rsi[0] < 68;
   bool quickMomentumShort = emaFast[0] < emaSlow[0] && previous >= emaFast[1] && current < emaFast[0] && rsi[0] > 32;
   bool macdLong = macdHist > 0 && prevMacdHist <= 0 && current > emaTrend[0] && rsi[0] < 72;
   bool macdShort = macdHist < 0 && prevMacdHist >= 0 && current < emaTrend[0] && rsi[0] > 28;
   bool rsiReversionLong = rsi[1] < 28 && rsi[0] > rsi[1] && current > previous;
   bool rsiReversionShort = rsi[1] > 72 && rsi[0] < rsi[1] && current < previous;
   if(DisableWeakStrategySignals) {
      if(IsBtcSymbol(symbol)) {
         rsiReversionLong = false;
         rsiReversionShort = false;
         macdLong = false;
         macdShort = false;
      }
      if(IsXauSymbol(symbol)) {
         bullishCross = false;
         bearishCross = false;
         macdLong = false;
         macdShort = false;
      }
   }

   int side = 0;
   string reason = "none";
   if(bullishCross || quickMomentumLong || macdLong || rsiReversionLong || (momentum3 > 0.0015 && current > emaTrend[0] && rsi[0] < 70)) {
      side = 1;
      reason = bullishCross ? "QuickMomentum_EMA_cross" : (macdLong ? "MACD_trend" : (rsiReversionLong ? "RSI_reversion" : "Momentum_trend"));
   } else if(bearishCross || quickMomentumShort || macdShort || rsiReversionShort || (momentum3 < -0.0015 && current < emaTrend[0] && rsi[0] > 30)) {
      side = -1;
      reason = bearishCross ? "QuickMomentum_EMA_cross" : (macdShort ? "MACD_trend" : (rsiReversionShort ? "RSI_reversion" : "Momentum_trend"));
   }

   double entry = side > 0 ? ask : bid;
   double atrValue = atr[0] > 0 ? atr[0] : current * 0.0015;
   double pda = PremiumDiscountPosition(rates, SmcLookbackBars, current);

   if(side == 0) {
      lastSignals[idx] = "no_signal rsi=" + DoubleToString(rsi[0], 1) + " mom3=" + DoubleToString(momentum3 * 100.0, 3) + "% pda=" + DoubleToString(pda, 2);
      return;
   }

   int smcScore = side > 0 ? SmartMoneyLongScore(rates, atrValue, entry) : SmartMoneyShortScore(rates, atrValue, entry);
   if(EnableSmartMoneyGates && smcScore < MinSmcConfluenceScore) {
      lastSignals[idx] = "smc_reject " + reason + " score=" + IntegerToString(smcScore) + " pda=" + DoubleToString(pda, 2);
      return;
   }

   double slDistance = MathMax(atrValue * StopAtrMultiplier, MinStopDistanceForSymbol(symbol, entry));
   double tpDistance = slDistance * TakeProfitAtrMultiplier;
   double sl = side > 0 ? entry - slDistance : entry + slDistance;
   double tp = side > 0 ? entry + tpDistance : entry - tpDistance;
   double volume = UseDailyRiskLotSizing ? DailyRiskVolume(symbol, slDistance, smcScore) : BaseLotForSymbol(symbol);
   volume = NormalizeVolume(symbol, volume);
   if(volume <= 0.0) {
      lastSignals[idx] = "risk_volume_zero";
      return;
   }

   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(DefaultDeviationPoints);
   bool ok = side > 0
      ? trade.Buy(volume, symbol, 0.0, sl, tp, "FinRobot_" + symbol + "_" + reason)
      : trade.Sell(volume, symbol, 0.0, sl, tp, "FinRobot_" + symbol + "_" + reason);
   if(ok) {
      lastTradeTimes[idx] = TimeCurrent();
      lastSignals[idx] = (side > 0 ? "BUY " : "SELL ") + reason + " smc=" + IntegerToString(smcScore) + " pda=" + DoubleToString(pda, 2) + " vol=" + DoubleToString(volume, 4);
      AppendAck(++lastCommandId, "AUTO_FILLED", symbol + " strategy " + lastSignals[idx], symbol, (side > 0 ? "BUY" : "SELL"), volume, entry);
   } else {
      lastSignals[idx] = "order_failed " + IntegerToString((int)trade.ResultRetcode()) + " " + trade.ResultRetcodeDescription();
      AppendAck(++lastCommandId, "AUTO_REJECTED", lastSignals[idx], symbol, (side > 0 ? "BUY" : "SELL"), volume, entry);
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
   Print("FinRobotBridgeEA 1.23 initialized. AutoTradeMT5=", AutoTradeMT5, " symbols=", AutoSymbols, " timeframe=", EnumToString(AutoTimeframe));
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