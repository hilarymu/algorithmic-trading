# 12. Glossary

| Term | Definition |
|------|-----------|
| **ATM** | At-the-money. An option whose strike price is equal (or nearest) to the current price of the underlying stock. |
| **Assignment** | When a put option seller is obligated to buy shares because the buyer exercises the option (stock price < strike at expiry). |
| **Bull / Bear / Correction** | Market regime labels produced by `regime_detector.py` based on SPY vs 200-day MA, 20-day return, and VIX. |
| **CSP** | Cash-Secured Put. Selling a put option while holding enough cash to buy the shares if assigned. Primary strategy of this system. |
| **Call Debit Spread** | Buying a call at a lower strike and selling a call at a higher strike. Net debit paid upfront. Used when IV is low. |
| **Contract Symbol** | Alpaca's OCC-format option identifier: `{SYMBOL}{YYMMDD}{C/P}{STRIKE_8DIGIT}` e.g. `AAPL260515C00270000`. |
| **Delta (Δ)** | Rate of change of option price relative to underlying price. CSP target: 0.25–0.35. Call spread buy leg: 0.50. |
| **DTE** | Days to Expiration. Time remaining until the option contract expires. |
| **Gamma** | Acceleration of delta. Increases sharply near expiration — reason for the 21-DTE exit rule. |
| **IV** | Implied Volatility. The market's expectation of future volatility, derived from option prices. Expressed as an annualised percentage. |
| **IV Rank** | `(IV_current − IV_52wk_low) / (IV_52wk_high − IV_52wk_low) × 100`. Measures where current IV sits within its historical range (0–100). |
| **NAV** | Net Asset Value. Total account value. Position sizing is expressed as % of NAV. |
| **OTM** | Out-of-the-money. A put is OTM when its strike is below the current stock price. |
| **Premium** | The price of an option contract × 100 (one contract = 100 shares). Cash received when selling. |
| **Put Credit Spread** | Selling a put at one strike and buying a put at a lower strike. Net credit received; max loss is capped. Used in correction regime. |
| **RSI** | Relative Strength Index (14-period). Momentum oscillator 0–100. Entry signal when < 25 (oversold). Exit signal when > 50 (recovered). |
| **Regime** | Classification of current market environment: `bull`, `mild_correction`, `correction`, `bear`, `recovery`, `geopolitical_shock`. |
| **Strike** | The price at which an option can be exercised. CSP strike chosen by delta target (0.25–0.35). |
| **Theta (Θ)** | Time decay of option value. Sellers benefit as theta erodes option premium daily. |
| **Universe** | The set of stocks eligible for screening: S&P 500 + NASDAQ 100 components (~529 unique tickers). |
| **Vol Ratio** | Current day's volume divided by the 20-day average volume. > 1.2 confirms a genuine RSI signal. |
| **Wheel** | Strategy where a CSP that results in assignment is followed by a covered call at the same strike, reducing cost basis. |
| **52-week IV high/low** | Rolling 252-trading-day maximum and minimum IV for a ticker. Used to compute IV Rank. |
