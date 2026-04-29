# ADR-001: Mean-Reversion as Primary Entry Strategy

**Status:** Accepted  
**Date:** 2025-Q1  
**Deciders:** Project owner

---

## Context

screener_trader needed a primary signal to identify entry candidates from the S&P 500 universe (~500 symbols). The strategy must:

- Produce actionable signals on a weekly cadence (not require intraday data)
- Work against a paper account with limited capital ($1,000 per position)
- Be explainable and auditable — each pick must have a clear quantitative justification
- Self-improve over time as more pick outcomes are observed

Two approaches were considered:

**Option A — Momentum / trend following:** Buy stocks making new 52-week highs with strong relative strength. Simple, well-documented, works well in sustained bull markets.

**Option B — Mean reversion:** Buy stocks that are statistically oversold relative to their recent history, expecting reversion toward the mean. Works best during corrections and pullbacks.

---

## Decision

**Adopt mean reversion as the primary entry strategy**, using three complementary oversold indicators:

1. **RSI(14) below threshold** — Measures speed of recent price decline; low RSI = selling pressure exhausted
2. **Price below lower Bollinger Band (20, 2σ)** — Measures distance from moving average in standard deviation units; below band = statistically extreme
3. **Volume confirmation** — Above-average volume during the down move confirms institutional selling (not thin-market noise)

The **composite score** ranks candidates by weighted distance from each threshold:

```
score = (rsi_weight × rsi_distance)
      + (bb_weight  × bb_distance)
      + (vol_weight × vol_ratio_distance)
```

Lower score = more oversold = higher priority pick.

---

## Rationale

**Why mean reversion over momentum for this system:**

- The S&P 500 universe is large-cap, liquid stocks. Mean reversion is well-documented in this universe: oversold large-caps tend to recover faster than small-caps because institutional buyers step in at statistical extremes
- The weekly Monday cadence aligns with mean reversion (multi-day recovery windows) better than momentum (which needs faster entry on breakouts)
- The paper account starts small; mean reversion allows entries on pullbacks, which typically occur at lower prices than momentum breakout entries
- Mean reversion performance is regime-dependent: strongest during corrections, weakest in sustained bull markets. This is acceptable because the system detects regime and adjusts thresholds accordingly

**Why RSI + BB + volume (not just one signal):**

- RSI alone misses cases where a stock is falling slowly (RSI drifts down without conviction)
- BB alone fires on low-volatility stocks that are barely below the band
- Volume confirmation filters out thin-market moves that reverse quickly

The three-signal composite is more precise than any single indicator.

---

## Consequences

**Positive:**
- Clear, auditable entry logic — each pick can be explained with three numbers
- Three-indicator composite reduces false positives vs. single-indicator approaches
- Regime-aware thresholds (via self-optimizing loop) adapt to market conditions
- Historical performance data supports data-driven threshold tuning

**Negative / trade-offs:**
- Strategy underperforms in sustained bull markets (fewer oversold conditions; weaker recovery)
- RSI and BB are lagging indicators — entry may be "too early" during sharp sell-offs
- Requires parameter tuning (RSI threshold, BB std) that must be kept regime-appropriate

**Accepted risks:**
- During sharp corrections, mean reversion picks may continue falling before reverting (add-down ladder mitigates this — see ADR-003)
- Strategy does not apply to trending/momentum stocks; these are filtered out, not captured
