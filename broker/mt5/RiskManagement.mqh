#property strict

// Risk Management for FinRobot

int DayStamp(datetime value) {
   MqlDateTime dt;
   TimeToStruct(value, dt);
   return dt.year * 10000 + dt.mon * 100 + dt.day;
}

bool SameDay(datetime value, int stamp) {
   return DayStamp(value) == stamp;
}

double ManagedClosedPnlForDay(int stamp, int magic, string &managedSymbols[]) {
   double pnl = 0.0;
   datetime now = TimeCurrent();
   if(!HistorySelect(now - 86400 * 7, now)) return 0.0;
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++) {
      ulong ticket = HistoryDealGetTicket(i);
      if(ticket == 0) continue;
      if((int)HistoryDealGetInteger(ticket, DEAL_MAGIC) != magic) continue;
      string symbol = HistoryDealGetString(ticket, DEAL_SYMBOL);
      bool isManaged = false;
      for(int j = 0; j < ArraySize(managedSymbols); j++) {
         if(managedSymbols[j] == symbol) { isManaged = true; break; }
      }
      if(!isManaged) continue;
      datetime dealTime = (datetime)HistoryDealGetInteger(ticket, DEAL_TIME);
      if(!SameDay(dealTime, stamp)) continue;
      pnl += HistoryDealGetDouble(ticket, DEAL_PROFIT);
      pnl += HistoryDealGetDouble(ticket, DEAL_COMMISSION);
      pnl += HistoryDealGetDouble(ticket, DEAL_SWAP);
   }
   return pnl;
}

bool IsSessionTime(bool enabled, int londonStart, int londonEnd, int nyStart, int nyEnd) {
   if(!enabled) return true;
   MqlDateTime dt;
   TimeCurrent(dt);
   int h = dt.hour;
   if(h >= londonStart && h < londonEnd) return true;
   if(h >= nyStart && h < nyEnd) return true;
   return false;
}

void ApplyDynamicBreakEven(bool enabled, bool allowed, int magic, double rr, double extraPoints, CTrade &trade) {
   if(!enabled || !allowed) return;
   for(int i = PositionsTotal() - 1; i >= 0; i--) {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0) continue;
      if(!PositionSelectByTicket(ticket)) continue;
      if((int)PositionGetInteger(POSITION_MAGIC) != magic) continue;
      
      double open = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl = PositionGetDouble(POSITION_SL);
      double tp = PositionGetDouble(POSITION_TP);
      double current = PositionGetDouble(POSITION_PRICE_CURRENT);
      long type = PositionGetInteger(POSITION_TYPE);
      string symbol = PositionGetString(POSITION_SYMBOL);
      double point = SymbolInfoDouble(symbol, SYMBOL_POINT);
      
      if(sl <= 0.0 || tp <= 0.0) continue;
      
      double risk = MathAbs(open - sl);
      if(risk <= 0.0) continue;
      
      double profit = 0.0;
      bool isBreakEven = false;
      
      if(type == POSITION_TYPE_BUY) {
         profit = current - open;
         isBreakEven = (sl >= open - 2.0 * point);
         if(!isBreakEven && profit >= risk * rr) {
            double newSl = open + extraPoints * point;
            if(newSl > sl) {
               trade.PositionModify(ticket, NormalizeDouble(newSl, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)), tp);
            }
         }
      } else if(type == POSITION_TYPE_SELL) {
         profit = open - current;
         isBreakEven = (sl <= open + 2.0 * point);
         if(!isBreakEven && profit >= risk * rr) {
            double newSl = open - extraPoints * point;
            if(newSl < sl || sl <= 0.0) {
               trade.PositionModify(ticket, NormalizeDouble(newSl, (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)), tp);
            }
         }
      }
   }
}
