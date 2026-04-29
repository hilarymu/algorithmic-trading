# ADR-003: Treat earnings proximity as signal flag, not a hard entry block

**Date:** 2026-04-23  
**Status:** Accepted

---

## Decision

Flag tickers with earnings within 7 days as `near_earnings: true` in iv_rank_cache but
do not block option entry on that basis.

---

## Context

Earnings events cause IV spikes (IV expansion before earnings, IV crush after). Naive
risk management would block all option trades near earnings. However, the IV spike is
itself a trading opportunity — elevated IV Rank means richer premium to collect. Whether
the edge is positive or negative near earnings is an empirical question that the optimizer
needs data to answer. Blocking earnings entirely prevents that data from accumulating.

**Hard exception:** Naked puts on earnings week remain blocked by hard-coded safety rule
(unbounded loss from gap-down). Put credit spreads (capped risk) remain eligible.

---

## Options Considered

### Option A — Hard block all trades near earnings

**Pros:**
- Eliminates earnings gap risk entirely

**Cons:**
- Loses IV spike premium opportunity
- Prevents optimizer from learning the earnings edge
- Over-conservative given spread strategies cap downside

### Option B — Flag as signal, never block (current decision)

Track `near_earnings` on every trade. Optimizer learns from outcomes.

**Pros:**
- Data accumulates for optimizer
- Premium opportunity preserved (IV elevated = fat CSP premiums)
- Strategy stays flexible per regime and IV level

**Cons:**
- Earnings gap-down can blow through spread protection in extreme cases
- Requires discipline not to size up on high-IV earnings setups

### Option C — Block only naked positions, allow spreads near earnings

**Pros:**
- Practical risk management balance

**Cons:**
- More complex conditional logic than a pure flag approach

---

## Decision Outcome

**Chosen option: B** — flag only. The hard-coded safety rule already blocks naked puts on
earnings week; all other strategies are tracked as data. Owner expressed willingness to
take earnings risk as part of the research process.

---

## Consequences

- ✅ Optimizer can learn the true earnings edge from real trade data
- ✅ IV spike premium available to capture on credit spreads
- ✅ Simple: one flag on each cache and history entry
- ⚠️ Early trades near earnings may lose before the optimizer calibrates
- ❌ Naked puts near earnings remain hard-blocked regardless of this flag
