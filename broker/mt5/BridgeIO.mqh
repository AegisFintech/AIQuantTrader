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

string Trim(string s) {
   StringTrimLeft(s);
   StringTrimRight(s);
   return s;
}

string Upper(string s) {
   StringToUpper(s);
   return s;
}
