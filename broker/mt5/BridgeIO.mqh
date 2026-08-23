#property strict

// Bridge I/O for AIQuantTrader

string Clean(string s) {
   StringReplace(s, "\"", "'");
   StringReplace(s, "\r", " ");
   StringReplace(s, "\n", " ");
   StringReplace(s, ",", ";");
   return s;
}

void AppendAck(string file, int id, string status, string message, string symbol, string side, double volume, double price) {
   int h = FileOpen(file, FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h == INVALID_HANDLE) h = FileOpen(file, FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h == INVALID_HANDLE) return;
   FileSeek(h, 0, SEEK_END);
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   FileWriteString(h, IntegerToString(id) + "," + TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "," + status + "," + Clean(message) + "," + symbol + "," + side + "," + DoubleToString(volume, 4) + "," + DoubleToString(price, digits) + "\n");
   FileClose(h);
}

void AppendShadowSignal(
   string file,
   string signalId,
   datetime signalBarTime,
   string symbol,
   string profile,
   string side,
   string strategy,
   double volume,
   double entry,
   double sl,
   double tp,
   int smcScore,
   double pda,
   double spreadPoints,
   bool dynamicBreakEven,
   double breakEvenRrRatio,
   double breakEvenExtraPoints
) {
   int h = FileOpen(file, FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h == INVALID_HANDLE) h = FileOpen(file, FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_COMMON);
   if(h == INVALID_HANDLE) return;
   if(FileSize(h) == 0) {
      FileWriteString(
         h,
         "signal_id,time,ts_server,signal_bar_time,symbol,profile,side,strategy,volume,entry,sl,tp,smc,pda,spread_points,dynamic_break_even,break_even_rr_ratio,break_even_extra_points\n"
      );
   }
   FileSeek(h, 0, SEEK_END);
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   FileWriteString(
      h,
      Clean(signalId) + "," +
      TimeToString(TimeCurrent(), TIME_DATE|TIME_SECONDS) + "," +
      IntegerToString((int)TimeCurrent()) + "," +
      IntegerToString((int)signalBarTime) + "," +
      Clean(symbol) + "," +
      Clean(profile) + "," +
      Clean(side) + "," +
      Clean(strategy) + "," +
      DoubleToString(volume, 4) + "," +
      DoubleToString(entry, digits) + "," +
      DoubleToString(sl, digits) + "," +
      DoubleToString(tp, digits) + "," +
      IntegerToString(smcScore) + "," +
      DoubleToString(pda, 4) + "," +
      DoubleToString(spreadPoints, 1) + "," +
      IntegerToString((int)dynamicBreakEven) + "," +
      DoubleToString(breakEvenRrRatio, 2) + "," +
      DoubleToString(breakEvenExtraPoints, 1) + "\n"
   );
   FileClose(h);
}

string Trim(string s) {
   StringTrimLeft(s);
   StringTrimRight(s);
   return s;
}

string Upper(string s) {
   StringToUpper(s);
   return s;
}
