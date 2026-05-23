#property strict
#property description "FinRobot MT5 bridge + optional demo XAUUSD M5 auto trader."
#property version "1.10"

#include <Trade/Trade.mqh>

input string CommandFile = "finrobot_commands.csv";
input string AckFile = "finrobot_acks.csv";
input string StatusFile = "finrobot_status.json";
input int PollSeconds = 1;
input int MagicNumber = 20260522;
input int DefaultDeviationPoints = 30;
input bool AllowTrading = true;
input bool AutoTradeXAUUSD = true;
input string AutoSymbol = "XAUUSD";
input ENUM_TIMEFRAMES AutoTimeframe = PERIOD_M5;
input double BaseLot = 0.01;
input int MaxAutoPositions = 30;
input int MinSecondsBetweenTrades = 900;
input int FastEmaPeriod = 9;
input int SlowEmaPeriod = 21;
input int RsiPeriod = 14;
input int AtrPeriod = 14;
input double MinAtrPoints = 80.0;
input double StopAtrMultiplier = 1.2;
input double TakeProfitAtrMultiplier = 1.8;
input double MaxSpreadPoints = 80.0;

CTrade trade;
int lastCommandId = 0;
int commandFileErrLogged = 0;
datetime lastAutoBar = 0;
datetime lastAutoTradeTime = 0;
int fastHandle = INVALID_HANDLE;
int slowHandle = INVALID_HANDLE;
int rsiHandle = INVALID_HANDLE;
int atrHandle = INVALID_HANDLE;
string lastAutoSignal = "init";

string Trim(string s) {
   StringTrimLeft(s);
   StringTrimRight(s);
   return s;
}

string Clean(string s) {
   StringReplace(s, "\"", "'");
   StringReplace(s, "\r", " ");
   StringReplace(s, "\n", " ");
   return s;
}

double NormalizeVolume(string symbol, double volume) {
   double minLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN);
   double maxLot = SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP);
   if(step <= 0.0) step = 0.01;
   volume = MathMax(minLot, MathMin(maxLot, volume));
   volume = MathFloor(volume / step) * step;
   return NormalizeDouble(volume, 2);
}

void AppendAck(int id, string status, string message, string symbol, string side, double volume, double price) {
   int h = FileOpen(AckFile, FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h == INVALID_HANDLE) h = FileOpen(AckFile, FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h == INVALID_HANDLE) return;
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, IntegerToString(id) + "," + TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "," + status + "," + Clean(message) + "," + symbol + "," + side + "," + DoubleToString(volume, 2) + "," + DoubleToString(price, _Digits) + "\n");
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

void WriteStatus() {
   int h = FileOpen(StatusFile, FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h == INVALID_HANDLE) return;
   double bid = SymbolInfoDouble(AutoSymbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(AutoSymbol, SYMBOL_ASK);
   double point = SymbolInfoDouble(AutoSymbol, SYMBOL_POINT);
   double spread = point > 0 ? (ask - bid) / point : 0.0;
   string payload = "{"
      + "\"ts\":" + IntegerToString((int)TimeCurrent()) + ","
      + "\"login\":" + IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN)) + ","
      + "\"server\":\"" + Clean(AccountInfoString(ACCOUNT_SERVER)) + "\"," 
      + "\"balance\":" + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + ","
      + "\"equity\":" + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2) + ","
      + "\"margin\":" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN), 2) + ","
      + "\"free_margin\":" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2) + ","
      + "\"positions\":" + IntegerToString(PositionsTotal()) + ","
      + "\"auto_positions\":" + IntegerToString(CountPositionsByMagic(AutoSymbol, MagicNumber)) + ","
      + "\"trade_allowed_terminal\":" + IntegerToString((int)TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)) + ","
      + "\"trade_allowed_ea\":" + IntegerToString((int)MQLInfoInteger(MQL_TRADE_ALLOWED)) + ","
      + "\"auto_trade_xauusd\":" + IntegerToString((int)AutoTradeXAUUSD) + ","
      + "\"symbol\":\"" + Clean(AutoSymbol) + "\"," 
      + "\"bid\":" + DoubleToString(bid, _Digits) + ","
      + "\"ask\":" + DoubleToString(ask, _Digits) + ","
      + "\"spread_points\":" + DoubleToString(spread, 1) + ","
      + "\"last_auto_signal\":\"" + Clean(lastAutoSignal) + "\"," 
      + "\"last_command_id\":" + IntegerToString(lastCommandId)
      + "}";
   FileWriteString(h, payload);
   FileClose(h);
}

bool EnsureSymbol(string symbol) {
   return SymbolSelect(symbol, true);
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
   action = Trim(action);
   symbol = Trim(symbol);
   side = Trim(side);
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

void ManageAutoGold() {
   if(!AutoTradeXAUUSD || !AllowTrading) return;
   if(AccountInfoInteger(ACCOUNT_TRADE_ALLOWED) == 0 || MQLInfoInteger(MQL_TRADE_ALLOWED) == 0) return;
   if(!SymbolSelect(AutoSymbol, true)) {
      lastAutoSignal = "symbol_select_failed";
      return;
   }

   int autoCount = 0;
   for(int i=PositionsTotal()-1; i>=0; i--) {
      ulong ticket = PositionGetTicket(i);
      if(ticket <= 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if(PositionGetString(POSITION_SYMBOL) == AutoSymbol && PositionGetInteger(POSITION_MAGIC) == MagicNumber) autoCount++;
   }
   if(autoCount >= MaxAutoPositions) {
      lastAutoSignal = "max_positions";
      return;
   }
   if(TimeCurrent() - lastAutoTradeTime < MinSecondsBetweenTrades) {
      lastAutoSignal = "cooldown";
      return;
   }

   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int bars = CopyRates(AutoSymbol, AutoTimeframe, 0, 80, rates);
   if(bars < 55) {
      lastAutoSignal = "not_enough_bars";
      return;
   }

   double closes[];
   ArrayResize(closes, bars);
   ArraySetAsSeries(closes, true);
   for(int i=0; i<bars; i++) closes[i] = rates[i].close;

   int emaFastHandle = iMA(AutoSymbol, AutoTimeframe, 8, 0, MODE_EMA, PRICE_CLOSE);
   int emaSlowHandle = iMA(AutoSymbol, AutoTimeframe, 21, 0, MODE_EMA, PRICE_CLOSE);
   int emaTrendHandle = iMA(AutoSymbol, AutoTimeframe, 50, 0, MODE_EMA, PRICE_CLOSE);
   int rsiHandle = iRSI(AutoSymbol, AutoTimeframe, 14, PRICE_CLOSE);
   int macdHandle = iMACD(AutoSymbol, AutoTimeframe, 12, 26, 9, PRICE_CLOSE);
   int atrHandle = iATR(AutoSymbol, AutoTimeframe, 14);
   if(emaFastHandle == INVALID_HANDLE || emaSlowHandle == INVALID_HANDLE || emaTrendHandle == INVALID_HANDLE || rsiHandle == INVALID_HANDLE || macdHandle == INVALID_HANDLE || atrHandle == INVALID_HANDLE) {
      lastAutoSignal = "indicator_handle_failed";
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

   if(CopyBuffer(emaFastHandle, 0, 0, 5, emaFast) < 5 ||
      CopyBuffer(emaSlowHandle, 0, 0, 5, emaSlow) < 5 ||
      CopyBuffer(emaTrendHandle, 0, 0, 5, emaTrend) < 5 ||
      CopyBuffer(rsiHandle, 0, 0, 5, rsi) < 5 ||
      CopyBuffer(macdHandle, 0, 0, 5, macdMain) < 5 ||
      CopyBuffer(macdHandle, 1, 0, 5, macdSignal) < 5 ||
      CopyBuffer(atrHandle, 0, 0, 5, atr) < 5) {
      lastAutoSignal = "indicator_copy_failed";
      IndicatorRelease(emaFastHandle); IndicatorRelease(emaSlowHandle); IndicatorRelease(emaTrendHandle); IndicatorRelease(rsiHandle); IndicatorRelease(macdHandle); IndicatorRelease(atrHandle);
      return;
   }
   IndicatorRelease(emaFastHandle); IndicatorRelease(emaSlowHandle); IndicatorRelease(emaTrendHandle); IndicatorRelease(rsiHandle); IndicatorRelease(macdHandle); IndicatorRelease(atrHandle);

   double bid = SymbolInfoDouble(AutoSymbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(AutoSymbol, SYMBOL_ASK);
   double point = SymbolInfoDouble(AutoSymbol, SYMBOL_POINT);
   double spreadPoints = point > 0 ? (ask - bid) / point : 0.0;
   if(spreadPoints > MaxSpreadPoints) {
      lastAutoSignal = "spread_too_wide";
      return;
   }

   double current = closes[0];
   double previous = closes[1];
   double momentum3 = (closes[0] - closes[3]) / closes[3];
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

   int side = 0;
   string reason = "none";
   if(bullishCross || quickMomentumLong || macdLong || rsiReversionLong || (momentum3 > 0.0015 && current > emaTrend[0] && rsi[0] < 70)) {
      side = 1;
      reason = bullishCross ? "QuickMomentum_EMA_cross" : (macdLong ? "MACD_trend" : (rsiReversionLong ? "RSI_reversion" : "Momentum_trend"));
   } else if(bearishCross || quickMomentumShort || macdShort || rsiReversionShort || (momentum3 < -0.0015 && current < emaTrend[0] && rsi[0] > 30)) {
      side = -1;
      reason = bearishCross ? "QuickMomentum_EMA_cross" : (macdShort ? "MACD_trend" : (rsiReversionShort ? "RSI_reversion" : "Momentum_trend"));
   }

   if(side == 0) {
      lastAutoSignal = "no_signal rsi=" + DoubleToString(rsi[0], 1) + " mom3=" + DoubleToString(momentum3 * 100.0, 3) + "%";
      return;
   }

   double entry = side > 0 ? ask : bid;
   double atrValue = atr[0] > 0 ? atr[0] : entry * 0.0015;
   double slDistance = MathMax(atrValue * 1.2, 2.0);
   double tpDistance = slDistance * 1.8;
   double sl = side > 0 ? entry - slDistance : entry + slDistance;
   double tp = side > 0 ? entry + tpDistance : entry - tpDistance;
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);
   double volume = MathMax(BaseLot, 0.01);
   if(equity > 0) {
      double scaled = NormalizeDouble(MathMin(0.05, MathMax(0.01, equity / 100000.0)), 2);
      volume = MathMax(volume, scaled);
   }

   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints(DefaultDeviationPoints);
   bool ok = side > 0
      ? trade.Buy(volume, AutoSymbol, 0.0, sl, tp, "FinRobot_gold_" + reason)
      : trade.Sell(volume, AutoSymbol, 0.0, sl, tp, "FinRobot_gold_" + reason);
   if(ok) {
      lastAutoTradeTime = TimeCurrent();
      lastAutoSignal = (side > 0 ? "BUY " : "SELL ") + reason + " vol=" + DoubleToString(volume, 2);
      AppendAck(++lastCommandId, "AUTO_FILLED", "gold strategy " + lastAutoSignal, AutoSymbol, (side > 0 ? "BUY" : "SELL"), volume, entry);
   } else {
      lastAutoSignal = "order_failed " + IntegerToString((int)trade.ResultRetcode()) + " " + trade.ResultRetcodeDescription();
      AppendAck(++lastCommandId, "AUTO_REJECTED", lastAutoSignal, AutoSymbol, (side > 0 ? "BUY" : "SELL"), volume, entry);
   }
}

int OnInit() {
   EventSetTimer(MathMax(PollSeconds, 1));
   trade.SetExpertMagicNumber(MagicNumber);
   EnsureSymbol(AutoSymbol);
   fastHandle = iMA(AutoSymbol, AutoTimeframe, FastEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   slowHandle = iMA(AutoSymbol, AutoTimeframe, SlowEmaPeriod, 0, MODE_EMA, PRICE_CLOSE);
   rsiHandle = iRSI(AutoSymbol, AutoTimeframe, RsiPeriod, PRICE_CLOSE);
   atrHandle = iATR(AutoSymbol, AutoTimeframe, AtrPeriod);
   Print("FinRobotBridgeEA 1.10 initialized. AutoTradeXAUUSD=", AutoTradeXAUUSD, " symbol=", AutoSymbol, " timeframe=", EnumToString(AutoTimeframe));
   WriteStatus();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason) {
   EventKillTimer();
   if(fastHandle != INVALID_HANDLE) IndicatorRelease(fastHandle);
   if(slowHandle != INVALID_HANDLE) IndicatorRelease(slowHandle);
   if(rsiHandle != INVALID_HANDLE) IndicatorRelease(rsiHandle);
   if(atrHandle != INVALID_HANDLE) IndicatorRelease(atrHandle);
   WriteStatus();
}

void OnTimer() {
   PollCommands();
   ManageAutoGold();
   WriteStatus();
}

void OnTick() {
   WriteStatus();
}
