# ADR-002: Construct option contract symbols directly without contracts API

**Date:** 2026-04-23  
**Status:** Accepted

---

## Decision

Build Alpaca OCC contract symbols directly from stock price and standard strike increments
instead of calling the contracts lookup API per ticker.

---

## Context

To fetch IV from Alpaca's options snapshot endpoint we need contract symbols (e.g.
`AAPL260515C00270000`). The naive approach is to call `/v1beta1/options/contracts` for
each ticker to discover available strikes and expirations — but with 500+ tickers that
would be 500+ sequential API calls, taking minutes and hitting rate limits. Alpaca's batch
contracts endpoint also proved problematic: batching multiple underlying symbols returns
paginated results from a single underlying at a time, not a true multi-symbol batch.

---

## Options Considered

### Option A — Direct symbol construction

Compute ATM call symbols from stock price using standard OCC strike increment tiers:
`< $25 → $1`, `$25–$50 → $2.50`, `$50–$200 → $5`, `$200–$500 → $10`, `> $500 → $25`.
Build the OCC string: `{SYMBOL}{YYMMDD}C{STRIKE_8DIGIT}`.

**Pros:**
- Zero API calls for symbol discovery
- Works for all standard-listed underlyings
- Deterministic; easily tested

**Cons:**
- Non-standard strikes (LEAPS, mini contracts) may be missed
- Requires knowing which expirations Alpaca has listed

### Option B — Contracts API per ticker

Call `/v1beta1/options/contracts?underlying_symbol=AAPL` for each ticker.

**Pros:**
- Always returns exactly what Alpaca has listed

**Cons:**
- 500+ API calls; hits rate limits; takes 5+ minutes
- Multi-symbol batching broken (returns single-symbol results paged together)

---

## Decision Outcome

**Chosen option: A** — direct construction. The universe is S&P 500 and NASDAQ 100 stocks,
all with standard listed strikes. The 15-batch approach (40 tickers per snapshot call)
completes in ~10 seconds vs 5+ minutes for the contracts API approach.

---

## Consequences

- ✅ Entire universe fetched in ~10 seconds (~15 API batch calls)
- ✅ No per-ticker API overhead
- ✅ Target expirations computed from monthly options cycle (3rd Friday nearest 35 DTE)
- ⚠️ Non-standard strikes (e.g. adjusted for splits or special dividends) will miss the snapshot — ticker skipped gracefully
- ⚠️ If Alpaca changes the OCC contract naming convention, symbol construction breaks

---

## Scope clarification (updated 2026-05-06)

This decision applies **only to `iv_tracker.py`** (daily IV rank computation across 500+ symbols).

`options_strategy_selector.py` uses a different approach for order execution: it calls
`GET /v2/options/contracts` on the **trading API** to find real listed contracts near the
target strike, then prices them with BSM. This is correct for execution because:

1. Only 2–5 candidates per run — the per-ticker API overhead is negligible.
2. Ordering a constructed symbol that isn't actually listed causes a 422 "asset not found"
   error. The contracts API guarantees the symbol exists before an order is attempted.
3. The indicative snapshot feed returning no data (common on paper accounts) does not
   prevent contract discovery via the trading API — they are separate endpoints.

In summary: **construct symbols for IV data fetching; look up real symbols for order execution.**
