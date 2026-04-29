# Options Screener Trader — Strategy Specification

**Version:** 1.0  
**Account:** Alpaca Paper (options-enabled)  
**Universe:** S&P 500 + NASDAQ 100 components  
**Core edge:** RSI mean-reversion signal applied to options premium collection

---

## 1. Philosophy

The RSI equity screener proves one thing: stocks that are deeply oversold
with volume confirmation tend to revert. That is the edge. Options do not
change the edge — they change the *vehicle* through which you express it.

Instead of buying the stock and waiting for it to recover, you sell a put
at a strike you'd be happy to own the stock at. You collect premium upfront.
If the stock recovers (the most common case in a bull regime), the put
expires worthless and you keep all the premium. If it doesn't recover, you
get assigned at your chosen price — which you were willing to pay anyway —
and can then sell a covered call to reduce cost basis further (the Wheel).

**The hierarchy of strategies by confidence in the signal:**

| RSI signal strength | IV environment | Action |
|---|---|---|
| RSI < 25, vol confirmed, bull regime | IV Rank > 40 | Sell cash-secured put |
| RSI < 20, extreme oversold | IV Rank < 30 | Buy call debit spread |
| RSI < 30, mild correction regime | IV Rank > 50 | Sell put credit spread (capped risk) |
| After assignment from CSP | Any | Sell covered call (Wheel) |
| Bear regime | Any | Stand aside |

Selling puts is the primary strategy. Buying calls is opportunistic and
secondary. The Wheel extends positions that were assigned.

---

## 2. Universe & Eligibility Filters

### 2.1 Stock universe
- S&P 500 components (as maintained in screener_config.json)
- NASDAQ 100 components (new — add to universe config)
- Phase 2 consideration: Russell 2000 (richer premiums, wider spreads — review after 6 months)

### 2.2 Stock eligibility (must pass before options check)
| Filter | Value | Reason |
|---|---|---|
| Price | > $15 | Options below $15 have wide bid/ask relative to premium |
| Average daily volume | > 1,000,000 shares | Ensures liquid underlying |
| Not OTC / pink sheet | (same filter as copy trader) | Alpaca cannot price |

### 2.3 Options eligibility (applied to filtered stocks)
| Filter | Value | Reason |
|---|---|---|
| Open interest at target strike | > 500 contracts | Sufficient liquidity |
| Bid/ask spread | < 15% of mid price | Slippage control |
| Options available | Standard (not mini) | Consistent contract sizing |
| Expiration available | 21–45 DTE exists | Theta sweet spot |

---

## 3. Signal Generation

Options screening runs after the RSI screener. Only stocks that pass the
equity screener's RSI + volume filters are evaluated for options.

### 3.1 Entry signals (same as equity screener)
| Signal | Threshold |
|---|---|
| RSI(14) | < 25 (data-driven, regime-adjusted by optimizer) |
| Volume ratio | > 1.2x 20-day avg (data-driven by optimizer) |
| Bollinger Band | Price below lower band (confirms oversold) |

### 3.2 Options-specific signal: IV Rank
IV Rank measures how elevated current implied volatility is relative to
the past 252 trading days for that specific ticker.

```
IV_Rank = (IV_current - IV_52wk_low) / (IV_52wk_high - IV_52wk_low) × 100
```

**We compute this ourselves.** The options_main.py daily run:
1. Fetches current IV from Alpaca options chain for each universe stock
2. Appends to iv_history.json (keyed by symbol + date)
3. Computes IV Rank from the rolling 252-day window

Since Alpaca historical options data starts Feb 2024, we will have
~12 months of IV history from day one — enough for a meaningful rank.

### 3.3 IV thresholds for strategy selection
| IV Rank | Interpretation | Implication |
|---|---|---|
| > 50 | Elevated — premium is fat | Ideal for selling puts |
| 30–50 | Moderate | Acceptable for selling, prefer > 40 |
| < 30 | Compressed — premium is thin | Prefer buying (calls) if RSI extreme |
| < 20 | Very compressed | Skip selling entirely |

---

## 4. Strategy Selection Matrix

The optimizer will learn which cell of this matrix produces the best
risk-adjusted return. This matrix defines the initial (regime-default) rules.

| Regime | IV Rank | RSI | Strategy | Notes |
|---|---|---|---|---|
| bull | > 40 | < 25 | Sell CSP | Core strategy |
| bull | > 40 | < 20 | Sell CSP (wider strike) | More OTM for safety |
| bull | < 30 | < 20 | Buy call debit spread | IV too cheap to sell |
| mild_correction | > 50 | < 25 | Sell put credit spread | Cap downside risk |
| mild_correction | > 50 | < 20 | Sell CSP (smaller size) | High conviction only |
| correction | > 60 | < 20 | Sell OTM put spread | Very selective |
| correction | < 60 | Any | Skip | Risk not worth it |
| bear | Any | Any | Skip | Stand aside |
| recovery | > 40 | < 30 | Sell CSP (normal size) | Regime turning — buy dips |

---

## 5. Contract Selection

### 5.1 Expiration
- **Target:** 30–45 DTE (days to expiration) at entry
- **Minimum:** 21 DTE (avoid gamma risk in final weeks)
- **Maximum:** 50 DTE (diminishing theta returns beyond this)
- Use the monthly expiration (3rd Friday) closest to 35 DTE

### 5.2 Strike selection for cash-secured puts
Strike is selected by **delta targeting**, not fixed percentage OTM.

| Signal strength | Target delta | Approx OTM % |
|---|---|---|
| RSI < 20, IV Rank > 60 | 0.20–0.25 Δ | ~8–12% OTM |
| RSI 20–25, IV Rank > 40 | 0.25–0.35 Δ | ~4–8% OTM |
| RSI 20–25, IV Rank 30–40 | 0.30–0.35 Δ | ~3–6% OTM |

Logic: higher delta = higher premium but more assignment risk.
In low IV environments we accept less premium (lower delta is pointless —
premium is too thin to bother).

For **call debit spreads** (buying):
- Buy the 0.50 Δ call (near ATM for max participation)
- Sell the 0.25 Δ call (cap the cost, limit max gain to ~2× debit paid)
- Spread width: 5–10% of stock price

For **put credit spreads** (correction regime):
- Sell the 0.30 Δ put
- Buy the 0.15 Δ put (protection, 2–3 strikes below)
- Max risk = spread width − premium received

### 5.3 Position size
| Account capital | Per-position allocation | Max concurrent |
|---|---|---|
| Full account | 5–8% of NAV per CSP | 8 positions |
| Adjustment | Reduce to 3–4% in correction regime | 10 positions max |

For a CSP: notional exposure = strike price × 100 × contracts.
Target: 1 contract per position initially (simplest to manage).

---

## 6. Exit Rules

Exits run in options_monitor.py (daily, same cadence as equity monitor).

### 6.1 Profit take (primary)
- **Close at 50% of max profit.** Standard rule — captures most theta
  decay while freeing capital faster than holding to expiration.
  If premium received = $2.00, close when value = $1.00 (buy back at $1.00).

### 6.2 Time-based exit
- **Close at 21 DTE** regardless of P&L if 50% target not hit.
  Gamma risk accelerates in the final 3 weeks. Not worth holding through it.

### 6.3 Mean-reversion signal exit (RSI target)
- **Close early (full profit or near) if RSI crosses back above 50.**
  This is the same signal the equity monitor uses. If the stock recovered,
  the put is probably safe and we free capital for the next setup.

### 6.4 Loss limit
- **Close if position loss reaches 2× premium received** (i.e., option
  value = 3× premium received). Example: sold for $2.00 → close if $6.00.
  This caps any single loss at roughly 2% of account per position.

### 6.5 Assignment (CSP only)
- If assigned (stock price < strike at expiration): **accept the shares**
  and immediately enter the Wheel: sell a covered call at the same strike
  (or slightly above) targeting 30–45 DTE. Track cost basis reduction.

---

## 7. IV Rank Tracking (self-computed)

Since Alpaca provides no historical IV rank, we build it ourselves.

**iv_history.json** structure:
```json
{
  "AAPL": {
    "2025-01-21": 0.24,
    "2025-01-22": 0.27,
    ...
  },
  "MSFT": { ... }
}
```

**iv_rank_cache.json** — updated daily by options_main.py:
```json
{
  "AAPL": { "iv_current": 0.27, "iv_rank": 62, "iv_52wk_high": 0.45, "iv_52wk_low": 0.18, "updated": "2026-04-22" },
  ...
}
```

On each daily run:
1. Fetch current ATM IV from Alpaca options chain for all universe tickers
2. Append to iv_history.json
3. Compute IV rank for each ticker with >= 30 days of history
4. Write iv_rank_cache.json

Tickers with < 30 days of history get IV Rank = null (skip options
screening for those tickers until sufficient data exists).

---

## 8. Self-Improvement Loop

The optimizer learns which parameters produce the best risk-adjusted
premium yield over time. This mirrors the equity optimizer exactly.

### 8.1 What gets tracked per trade (options_picks_history.json)
```json
{
  "symbol": "AAPL",
  "screened_date": "2026-04-22",
  "regime": "bull",
  "rsi_at_entry": 22.4,
  "vol_ratio": 1.8,
  "iv_rank_at_entry": 58,
  "strategy": "CSP",
  "expiration": "2026-05-17",
  "dte_at_entry": 25,
  "strike": 170.0,
  "delta_at_entry": -0.28,
  "premium_received": 2.15,
  "contracts": 1,
  "collateral": 17000,
  "premium_yield": 1.26,
  "exit_date": "2026-05-03",
  "exit_reason": "50pct_profit",
  "exit_price": 1.05,
  "pnl": 110.0,
  "pnl_pct": 0.65,
  "assigned": false,
  "returns": { "premium_yield": 1.26, "annualised": 18.3 }
}
```

### 8.2 What the options optimizer learns
| Parameter | Current default | Learns from |
|---|---|---|
| IV Rank entry threshold | 40 | Which IV rank bucket produces best yield |
| Delta target | 0.30 | Which delta bucket produces best win rate |
| DTE at entry | 35 | Which DTE bucket has best theta capture |
| Exit at % of max profit | 50% | Does 40% or 60% produce better annualised return? |
| Strategy (CSP vs spread) | Regime-based | Which strategy wins in each regime |

### 8.3 Regime-aware (same architecture as equity)
- options_signal_analyzer.py → computes per-regime, per-IV-bucket stats
- options_optimizer.py → derives optimal parameters
- options_regime_detector.py → **reuse equity regime_detector.py directly**

---

## 9. Component Map

### 9.1 Reused from screener_trader (import or copy)
| Component | How used |
|---|---|
| `rsi_loop/regime_detector.py` | Imported directly — same market regime logic |
| `screener_config.json` universe list | Read S&P 500 symbols from it |
| `rsi_loop/signal_analyzer.py` | Extended — add IV rank bucket analysis |
| `rsi_loop/optimizer.py` | Pattern followed; options_optimizer.py mirrors it |

### 9.2 New files in options_screener_trader/
```
options_screener_trader/
├── alpaca_config.json             (options paper account credentials)
├── options_config.json            (strategy parameters — equiv of screener_config.json)
├── options_main.py                (daily orchestrator — equiv of rsi_main.py)
│
├── options_loop/
│   ├── options_screener.py        (RSI + IV rank filter → candidate list)
│   ├── options_strategy_selector.py  (picks CSP / call spread / skip per matrix)
│   ├── options_executor.py        (places Alpaca options orders)
│   ├── options_monitor.py         (daily: check exits, 50% profit, 21DTE, RSI target)
│   ├── iv_tracker.py              (fetches + stores daily IV, computes IV rank)
│   ├── options_signal_analyzer.py (stats by IV rank bucket, delta bucket, regime)
│   ├── options_optimizer.py       (derives optimal IV rank / delta / DTE thresholds)
│   └── greeks_fetcher.py          (Alpaca /v1beta1/options/snapshots wrapper)
│
├── iv_history.json                (daily IV per ticker — auto-grows)
├── iv_rank_cache.json             (current IV rank per ticker — refreshed daily)
├── options_config.json            (strategy parameters)
├── options_picks_history.json     (all options trades with outcomes — the learning corpus)
├── options_positions_state.json   (open positions, strikes, expiry, cost basis)
├── options_pending_entries.json   (equiv of pending_entries.json — review before execution)
│
├── run_options_loop.bat           (daily 07:00 trigger)
├── run_options_executor.bat       (Monday 09:15 trigger)
├── run_options_monitor.bat        (daily 15:45 trigger — just before close)
└── docs/
    ├── architecture.md
    └── strategy.md                (this file, simplified)
```

---

## 10. options_config.json (initial values)

```json
{
  "universe": ["SP500", "NASDAQ100"],
  "max_positions": 8,
  "primary_strategy": "CSP",
  "indicators": {
    "rsi_period": 14,
    "rsi_oversold": 25,
    "volume_ratio_min": 1.2,
    "iv_rank_min_sell": 40,
    "iv_rank_max_buy": 30
  },
  "contract_selection": {
    "target_dte_min": 21,
    "target_dte_max": 45,
    "target_dte_ideal": 35,
    "target_delta_csp": 0.30,
    "target_delta_call_buy": 0.50,
    "target_delta_call_sell": 0.25
  },
  "position_sizing": {
    "max_pct_nav_per_position": 0.07,
    "max_positions": 8,
    "contracts_per_position": 1
  },
  "exits": {
    "profit_target_pct": 0.50,
    "loss_limit_multiplier": 2.0,
    "close_at_dte": 21,
    "rsi_recovery_exit": 50
  },
  "filters": {
    "min_stock_price": 15.0,
    "min_avg_volume": 1000000,
    "min_open_interest": 500,
    "max_bid_ask_spread_pct": 0.15
  },
  "auto_entry": {
    "enabled": true,
    "order_type": "limit",
    "limit_price": "mid",
    "review_window_hours": 3.25
  }
}
```

---

## 11. Phased Build

### Phase 1 — Foundation (build first)
1. `iv_tracker.py` — starts accumulating IV history immediately, no options needed
2. `options_screener.py` — RSI + IV rank filter, produces daily candidate list
3. `options_config.json` — static config, no optimizer yet
4. `run_options_loop.bat` + scheduled task

Run in **research mode only** (no orders) for 2–4 weeks. Accumulate
iv_history.json and options_picks_history.json with simulated entries.

### Phase 2 — Execution
5. `greeks_fetcher.py` — wraps Alpaca options chain API
6. `options_strategy_selector.py` — picks the right contract
7. `options_executor.py` — places paper orders
8. `options_monitor.py` — manages exits
9. `options_positions_state.json` — tracks open positions
10. `options_pending_entries.json` + review window (same pattern as equity)

### Phase 3 — Self-Improvement
11. `options_signal_analyzer.py` — builds stats from options_picks_history.json
12. `options_optimizer.py` — derives optimal parameters
13. Wire into options_main.py daily run

### Phase 4 — Wheel automation
14. Auto-detect assignment from Alpaca positions
15. Auto-generate covered call pending entry after assignment
16. Track wheel cost basis reduction

---

## 12. Risk boundaries (hard-coded, never optimised away)

- Never sell naked puts on earnings week (IV spike risk)
- Never size a single position > 10% of NAV
- Never hold through expiration unless intentionally accepting assignment
- Never open new positions if account margin utilisation > 70%
- Maximum loss per week: if 3 positions hit loss limit in same week, pause and review
- Bear regime: no new options positions opened at all

---

## 13. What success looks like

**Target metrics (12-month horizon):**
- Annualised premium yield: > 15% on deployed capital
- Win rate (puts expire worthless or closed at 50% profit): > 65%
- Assignment rate: < 20% of CSPs
- Average days held per trade: < 25 DTE (rapid premium capture)

**The optimizer is working if:**
- IV rank threshold narrows to the most predictive range (expect 45–60 to emerge)
- Delta target stabilises at a consistent level per regime
- Average pnl per trade increases quarter-on-quarter
