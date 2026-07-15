#property strict

// Smart Money Concepts for AIQuantTrader

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

bool HasBullishFvg(MqlRates &rates[], int count, double atrValue, double price, double fvgMinAtrMultiplier) {
   int available = ArraySize(rates) - 3;
   int n = MathMin(count, available);
   double minGap = MathMax(atrValue * fvgMinAtrMultiplier, 0.0);
   for(int i = 1; i <= n; i++) {
      double gapLow = rates[i + 2].high;
      double gapHigh = rates[i].low;
      if(gapHigh > gapLow && gapHigh - gapLow >= minGap && price >= gapLow && price <= gapHigh + atrValue * 0.5) return true;
   }
   return false;
}

bool HasBearishFvg(MqlRates &rates[], int count, double atrValue, double price, double fvgMinAtrMultiplier) {
   int available = ArraySize(rates) - 3;
   int n = MathMin(count, available);
   double minGap = MathMax(atrValue * fvgMinAtrMultiplier, 0.0);
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

bool BullishLiquiditySweep(MqlRates &rates[], int count, double atrValue, double liquiditySweepAtrMultiplier) {
   int available = ArraySize(rates) - 2;
   int n = MathMin(count, available);
   if(n < 5) return false;
   double priorLow = rates[2].low;
   for(int i = 3; i <= n; i++) {
      if(rates[i].low < priorLow) priorLow = rates[i].low;
   }
   return rates[1].low < priorLow - atrValue * liquiditySweepAtrMultiplier && rates[1].close > priorLow;
}

bool BearishLiquiditySweep(MqlRates &rates[], int count, double atrValue, double liquiditySweepAtrMultiplier) {
   int available = ArraySize(rates) - 2;
   int n = MathMin(count, available);
   if(n < 5) return false;
   double priorHigh = rates[2].high;
   for(int i = 3; i <= n; i++) {
      if(rates[i].high > priorHigh) priorHigh = rates[i].high;
   }
   return rates[1].high > priorHigh + atrValue * liquiditySweepAtrMultiplier && rates[1].close < priorHigh;
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

int SmartMoneyLongScore(MqlRates &rates[], double atrValue, double price, int lookback, double discThreshold, double fvgMult, double sweepMult) {
   double pda = PremiumDiscountPosition(rates, lookback, price);
   double obHigh = LastBullishOrderBlockHigh(rates, lookback);
   bool discount = pda <= discThreshold;
   bool deepDiscount = pda <= MathMax(0.18, discThreshold - 0.12);
   bool hasFvg = HasBullishFvg(rates, lookback, atrValue, price, fvgMult);
   bool reclaimedOrderBlock = obHigh > 0.0 && price >= obHigh && pda <= 0.50;
   bool sweep = BullishLiquiditySweep(rates, lookback, atrValue, sweepMult);
   bool structure = BullishStructureShift(rates, MathMin(lookback, 20));
   int score = 0;
   if(discount) score++;
   if(deepDiscount) score++;
   if(hasFvg) score++;
   if(reclaimedOrderBlock) score++;
   if(sweep) score++;
   if(structure) score++;
   return score;
}

int SmartMoneyShortScore(MqlRates &rates[], double atrValue, double price, int lookback, double premThreshold, double fvgMult, double sweepMult) {
   double pda = PremiumDiscountPosition(rates, lookback, price);
   double obLow = LastBearishOrderBlockLow(rates, lookback);
   bool premium = pda >= premThreshold;
   bool deepPremium = pda >= MathMin(0.82, premThreshold + 0.12);
   bool hasFvg = HasBearishFvg(rates, lookback, atrValue, price, fvgMult);
   bool rejectedOrderBlock = obLow > 0.0 && price <= obLow && pda >= 0.50;
   bool sweep = BearishLiquiditySweep(rates, lookback, atrValue, sweepMult);
   bool structure = BearishStructureShift(rates, MathMin(lookback, 20));
   int score = 0;
   if(premium) score++;
   if(deepPremium) score++;
   if(hasFvg) score++;
   if(rejectedOrderBlock) score++;
   if(sweep) score++;
   if(structure) score++;
   return score;
}
