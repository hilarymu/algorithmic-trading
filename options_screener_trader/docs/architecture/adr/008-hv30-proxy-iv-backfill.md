# ADR-008: Use HV30 realized-vol proxy when OPRA historical bars are unavailable

**Date:** 2026-04-25  
**Status:** Accepted

---

## Decision

When Alpaca historical options bars (`/v1beta1/options/bars`) return 403 due to
missing OPRA subscription, automatically fall back to computing 30-day realized
volatility (HV30) from equity price history as the IV proxy for bootstrapping
`iv_history.json`.

---

## Context

`iv_backfill.py` was designed to bootstrap IV history from Alpaca historical
options bars so the screener's IV rank filter is meaningful from day one.
The paper account does not include OPRA; all 17,000+ contract bar requests fail
with `{"message":"OPRA agreement is not signed"}`.

Without bootstrapped history, `iv_tracker` builds only 1 day of IV per symbol
per daily run.  `compute_iv_rank()` requires 30 days (`MIN_IV_HISTORY`), so
the screener produces 0 candidates for the first 6 weeks of operation.

---

## Options Considered

### Option A — Wait 30 trading days (rejected)
Let `iv_tracker` accumulate real snapshot IV naturally.  Simple but means 6
weeks of zero candidates and a cold-start pipeline.

### Option B — Lower `iv_rank_min_sell` threshold temporarily (rejected)
Bypass the IV rank filter.  Would let the screener produce picks without
knowing whether IV is elevated — defeats the premium-selling logic entirely.

### Option C — HV30 proxy from equity bars (chosen)
1. Fetch 270 calendar days of daily stock closes (no OPRA required).
2. Compute rolling 30-day annualized realized vol:
   `HV30_t = std(log_returns_{t-29:t}) * sqrt(252)`
3. Scale per-symbol: `k = mean(snapshot_IV_real) / mean(HV30_calibration_dates)`
   where calibration dates are the 1–2 most recent dates where real snapshot IV
   exists (from prior `iv_tracker` runs).  `k` clamped to [0.5, 3.0].
4. Store `HV30 * k` as synthetic IV for dates not already in `iv_history.json`.

---

## Decision Outcome

**Chosen option: C** — HV30 proxy.

**Rationale:**
- Equity bars are freely available without OPRA.
- After per-symbol scaling, HV30 proxy values are on the same scale as snapshot
  IV, making IV rank directionally correct.
- IV rank is a relative measure (`cur vs 52w high/low`); systematic bias
  introduced by using HV as the historical baseline cancels out when computing
  the percentile rank.
- As daily `iv_tracker` accumulates real snapshot IV, proxy dates phase out of
  the rolling window (~252 trading days) automatically — the proxy is
  self-correcting.
- Implementation is ~80 lines added to `iv_backfill.py` with no changes to any
  other module.

---

## Consequences

- ✅ IV rank available for all 512 symbols from day one (not after 6 weeks)
- ✅ Screener produces candidates immediately (verified: 5 CSP candidates on
  2026-04-25 first post-backfill run)
- ✅ No changes to `iv_tracker`, `options_screener`, or any downstream module
- ✅ Self-correcting: proxy data phases out of 252-day window as real IV
  accumulates; no manual cleanup needed
- ⚠️ HV30 is realized vol; real IV includes a volatility risk premium (~20-30%
  higher on average). Per-symbol scaling corrects the level but may be
  imprecise if calibration period is atypical (only 1–2 anchor dates).
- ⚠️ In extreme-vol regimes (tariff shocks, earnings), current snapshot IV may
  exceed all scaled-HV30 historical values, causing IV rank = 100 for many
  symbols. This is directionally correct (IV is genuinely elevated) but inflates
  the pool of screener candidates temporarily.
- ❌ Not a substitute for OPRA subscription if live trading requires tight
  bid/ask data for limit order pricing.
