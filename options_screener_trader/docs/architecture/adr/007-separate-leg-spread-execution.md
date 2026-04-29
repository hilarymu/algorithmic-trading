# ADR-007: Execute spread legs as separate single-leg limit orders

**Date:** 2026-04-24  
**Status:** Accepted

---

## Decision

Place multi-leg options spreads (PUT_SPREAD, OTM_PUT_SPREAD, CALL_SPREAD) as two
independent single-leg limit orders rather than as a combo/spread order.

---

## Context

Phase 2 adds credit spread strategies alongside the single-leg CSP. Submitting spread
entries requires placing orders for both the short leg and the long leg. The question
is whether to submit them as a single atomic combo order or as two independent orders.

---

## Options Considered

### Option A — Two separate single-leg limit orders (chosen)

Short leg and long leg are submitted as independent limit orders with a short delay
between them.

**Pros:**
- Alpaca paper API only supports single-leg options orders via REST (no combo order endpoint)
- Identical order placement code for all strategy types (simpler executor)
- Each leg is visible independently in Alpaca dashboard
- Fully testable — one `POST /v2/orders` call per leg

**Cons:**
- Leg risk: short leg may fill, long leg may not (temporary naked exposure)
- Net credit is not guaranteed — each leg fills at market independently
- On real (non-paper) accounts this would require careful limit-price coordination

### Option B — Combo / multi-leg order via Alpaca

Submit a single spread order with both legs defined atomically.

**Pros:**
- Atomic fill: both legs fill together or neither does
- Net credit locked in

**Cons:**
- Alpaca does NOT support multi-leg options orders via the paper REST API (as of 2026)
- Would require switching to a different broker/API for spreads

### Option C — Sequential limit orders with cancel-if-second-fails

Place short leg; if it fills, immediately place long leg; if long leg fails, cancel
short leg and log an unhedged position alert.

**Pros:**
- Attempts to recover from partial fill
- Reduces naked exposure window

**Cons:**
- Adds cancel logic and retry complexity
- The cancel may itself fail if the short leg is partially filled

---

## Decision Outcome

**Chosen option: A** — two separate single-leg orders. The strategy is paper-only for
Phase 2; the leg-risk exposure during the brief gap between orders is acceptable.
`options_monitor.py` checks position state on every run and will flag if a long
leg order ID is present but the long leg shows no position (unfilled).

If the system ever migrates to live trading, this decision should be revisited in
favour of a broker supporting atomic multi-leg orders.

---

## Consequences

- ✅ Works with Alpaca paper API as-is
- ✅ Executor code is uniform for all strategy types
- ✅ Each order is independently auditable in Alpaca dashboard
- ⚠️ Brief window of unhedged exposure between short and long leg fills (paper only)
- ⚠️ Net credit at risk of slippage vs estimate (acceptable for paper)
- ❌ Not suitable for live trading without a broker supporting combo orders
