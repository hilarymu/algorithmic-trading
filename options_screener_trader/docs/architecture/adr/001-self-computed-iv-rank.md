# ADR-001: Self-compute IV Rank from Alpaca indicative feed

**Date:** 2026-04-23  
**Status:** Accepted

---

## Decision

Compute IV Rank ourselves daily from Alpaca's options snapshot feed rather than sourcing
it from an external data vendor.

---

## Context

Options strategy selection depends on IV Rank — knowing whether current implied volatility
is high or low relative to its recent history. We need this for every ticker in a 500+
symbol universe, daily. Vendors (Bloomberg, OPRA, Market Chameleon) either cost money or
require exchange subscriptions we don't hold. Alpaca's paper account provides a free
indicative options snapshot endpoint that returns `impliedVolatility` per contract.

---

## Options Considered

### Option A — Self-compute from Alpaca indicative feed

**Pros:**
- Free, within existing Alpaca paper account
- Full control over computation window (252 days)
- Builds automatically over time; no vendor dependency

**Cons:**
- First 30 days: IV Rank unavailable (insufficient history)
- Indicative feed — not OPRA-quality; slight pricing differences vs live

### Option B — Subscribe to a third-party IV Rank feed

**Pros:**
- Instant historical IV Rank from day one

**Cons:**
- Cost ($50–$200+/month for full universe)
- Integration complexity
- Overkill for a paper trading research system

### Option C — Use Webull OPRA feed manually

**Pros:**
- User already has Webull account with OPRA feed

**Cons:**
- No API; manual extraction is not automatable at 500 tickers/day
- Cannot integrate into daily scheduled pipeline

---

## Decision Outcome

**Chosen option: A** — self-compute. Cost-free, automatable, and the 30-day bootstrap
period is acceptable given the Phase 1 research-only stance anyway.

---

## Consequences

- ✅ No external data cost
- ✅ Fully automated; runs in ~10 seconds for 529 tickers
- ✅ IV history accumulates permanently in `iv_history.json`
- ⚠️ IV Rank is `null` for the first 30 days per ticker — those tickers are excluded from options screening until data matures
- ⚠️ Indicative IV may differ slightly from OPRA; acceptable for strategy-selection purposes
