#property copyright "AIQuantTrader"
#property link      "https://github.com/AegisFintech/AIQuantTrader"
#property version   "1.00"
#property script_show_inputs

input string ExportSymbol = "XAUUSD";
input int    BarCount = 50000;

int OnStart()
{
   // Copy the input into a local mutable string because StringTrimLeft /
   // StringTrimRight take their first argument by reference and `input` is const.
   string export_symbol = ExportSymbol;
   StringTrimLeft(export_symbol);
   StringTrimRight(export_symbol);
   if(export_symbol == "")
   {
      Print("AIQuantTrader export error: Symbol is required");
      return 1;
   }
   if(BarCount <= 0)
   {
      PrintFormat("AIQuantTrader export error: invalid BarCount=%d", BarCount);
      return 1;
   }
   if(!SymbolSelect(export_symbol, true))
   {
      PrintFormat(
         "AIQuantTrader export error: unable to select symbol %s, err=%d",
         export_symbol,
         GetLastError()
      );
      return 1;
   }

   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   ResetLastError();
   int copied = CopyRates(export_symbol, PERIOD_M1, 0, BarCount, rates);
   if(copied <= 0)
   {
      PrintFormat(
         "AIQuantTrader export error: no M1 history for %s, requested=%d err=%d",
         export_symbol,
         BarCount,
         GetLastError()
      );
      return 1;
   }

   string file_name = StringFormat("aiquanttrader_export_%s_M1.tsv", export_symbol);
   FileDelete(file_name, FILE_COMMON);
   ResetLastError();
   int handle = FileOpen(file_name, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(handle == INVALID_HANDLE)
   {
      PrintFormat(
         "AIQuantTrader export error: unable to open %s in Common Files, err=%d",
         file_name,
         GetLastError()
      );
      return 1;
   }

   int digits = (int)SymbolInfoInteger(export_symbol, SYMBOL_DIGITS);
   int written = 0;
   for(int i = 0; i < copied; i++)
   {
      string line = StringFormat(
         "%s\t%s\t%s\t%s\t%s\t%s\r\n",
         FormatMinute(rates[i].time),
         DoubleToString(rates[i].open, digits),
         DoubleToString(rates[i].high, digits),
         DoubleToString(rates[i].low, digits),
         DoubleToString(rates[i].close, digits),
         IntegerToString(rates[i].tick_volume)
      );
      if(FileWriteString(handle, line) <= 0)
      {
         int err = GetLastError();
         FileClose(handle);
         PrintFormat("AIQuantTrader export error: write failed for %s, err=%d", file_name, err);
         return 1;
      }
      written++;
   }
   FileClose(handle);

   if(written <= 0)
   {
      PrintFormat("AIQuantTrader export error: history too short for %s", export_symbol);
      return 1;
   }

   PrintFormat("AIQuantTrader export: %s bars=%d file=%s", export_symbol, written, file_name);
   return 0;
}

string FormatMinute(datetime value)
{
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
