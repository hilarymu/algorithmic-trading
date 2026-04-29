# ADR-002: Weekly Monday Screener Schedule

**Status:** Accepted  
**Date:** 2025-Q1  
**Deciders:** Project owner

---

## Context

The screener must run on some cadence to identify entry candidates. Options considered:

| Cadence | Pros | Cons |
|---------|------|------|
| Daily (each market open) | Fresh signals every day; captures mid-week oversold events | Higher API usage; more frequent entries; harder to review manually |
| Weekly (Monday) | Aligns with weekly charting patterns; lower API load; easy weekly review cycle | Misses mid-week oversold opportunities |
| Monthly | Very low overhead | Too infrequent; many opportunities missed |

The system also needed a review window: time between the screener producing picks and the executor placing orders, so the human owner can veto individual picks before execution.

---

## Decision

**Run the screener once per week, every Monday at 06:00 (pre-market).**

The executor runs at 09:15, providing a **3.25-hour review window** for the human to inspect `pending_entries.json` and set `"skip": true` on any pick before market open.

Full Monday pipeline:
```
06:00  screener.py        (screen S&P 500; write pending_entries.json)
06:05  rsi_main.py        (self-optimisation loop; update screener_config.json)
09:15  entry_executor.py  (place market orders for un-vetoed picks)
```

---

## Rationale

**Why Monday specifically:**

- Monday opens with the full weekend's news digested by the market. First-hour volatility often produces RSI extremes that are actionable
- Monday aligns with the "end-of-week" return measurement window: a Monday entry has 5 trading days before the next screener cycle. The performance tracker measures 1d/5d/10d/20d forward returns, and the 5d measurement aligns exactly with the next screen date
- The RSI optimisation loop (rsi_main.py) runs immediately after the screener to update parameters before execution — this sequential dependency requires a fixed, single weekly slot

**Why 3.25-hour review window:**

- Screener runs at 06:00 (US ET pre-market); market opens at 09:30; executor runs at 09:15
- 09:15 gives 15 minutes before open — enough time to use Alpaca's pre-open liquidity check, but still within normal market session for limit order placement
- 3.25 hours is sufficient to review a typical 3-10 pick list via the dashboard and veto any picks that look problematic (news, earnings surprise, sector concern)

---

## Consequences

**Positive:**
- Weekly cadence matches mean-reversion holding periods (typical hold: 5-20 days)
- 3.25-hour review window preserves human oversight without requiring real-time attention
- Low API load: one batch call per week vs. 5 daily calls
- Predictable schedule: owner knows exactly when to check dashboard (Monday morning)

**Negative / trade-offs:**
- Mid-week oversold events (Tuesday-Friday) are missed entirely
- A strong Monday gap-down may create many oversold signals simultaneously, hitting the `max_new_entries_per_week` cap and potentially missing some picks
- If Monday is a US market holiday, no screen fires that week (no holiday fallback implemented)

**Accepted risks:**
- Missing mid-week opportunities is accepted in exchange for manageable weekly review cadence and lower operational burden
