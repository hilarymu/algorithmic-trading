# ADR-005: Build IV history before placing any options orders

**Date:** 2026-04-23  
**Status:** Accepted

---

## Decision

Run Phase 1 (IV history accumulation and research-only screening) for a minimum of 30 days
before enabling any live paper order execution.

---

## Context

IV Rank is the core additional signal this system adds over the equity screener. Without
sufficient IV history, IV Rank is `null` and strategy selection degrades to regime-only
logic — which is not the designed system. Additionally, options carry more complexity
than equities (Greeks, expiration, assignment) so running in observation mode first lets
us validate the pipeline end-to-end before orders are placed.

---

## Options Considered

### Option A — IV-first phased build (current decision)

Phase 1: accumulate IV history. Phase 2: enable execution. Gate controlled by
`auto_entry.enabled: false` in `options_config.json`.

**Pros:**
- System validates itself in production before risking capital (even paper capital)
- IV Rank computable from day 30 onwards; strategy selection is fully informed
- Bugs in screener/selector visible in logs before they affect orders
- 252 days of IV = full 52-week rank; best ranking quality

**Cons:**
- 30+ day delay before first paper trade
- Impatient — opportunity cost in research-only period

### Option B — Trade immediately with regime-only strategy selection

Skip IV Rank gate; select strategy on regime alone.

**Pros:**
- Faster to first trade
- Still uses regime as a meaningful gate

**Cons:**
- Half the signal is missing; strategy selection is less precise
- Cannot distinguish cheap vs expensive IV — may sell options when premium is thin

### Option C — Use hard-coded IV thresholds from literature (no history needed)

Accept RSI < 25 + VIX > 20 as proxy for elevated IV, bypass IV Rank.

**Pros:**
- No bootstrap period

**Cons:**
- VIX ≠ individual ticker IV Rank; broad signal loses per-stock precision
- Contradicts core design of self-computed per-ticker IV history

---

## Decision Outcome

**Chosen option: A** — IV history first. The 30-day wait is minimal against the strategy's
12-month evaluation horizon. `auto_entry.enabled` is the single config toggle to move from
Phase 1 to Phase 2.

---

## Consequences

- ✅ IV Rank available from day 30; full 52-week rank from day 252
- ✅ Pipeline fully validated in logs before orders touch the account
- ✅ No risk of bad orders from half-initialised system
- ⚠️ No paper trades for at least 30 days from first run (2026-04-23)
- ⚠️ `auto_entry.enabled: false` must be toggled manually after review — not automatic
