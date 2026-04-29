# ADR-004: Self-Optimizing RSI Loop

**Status:** Accepted  
**Date:** 2025-Q1  
**Deciders:** Project owner

---

## Context

The mean-reversion screener has several tunable parameters:

- RSI oversold threshold (entry trigger)
- RSI exit threshold (when to close a position)
- Bollinger Band multiplier (stddev factor)
- Scoring weights (RSI vs BB vs volume)

These parameters are regime-dependent: an RSI threshold of 30 may be appropriate in a bull market (rarely breached; high precision) but too conservative in a correction (many stocks breach 30; need tighter threshold to be selective).

Two approaches to parameter management were considered:

**Option A — Manual tuning:** Owner reviews performance periodically and adjusts `screener_config.json` manually based on observation.

**Option B — Automated self-optimization:** A pipeline of components collects pick outcomes, analyses performance by regime/RSI/volume buckets, derives improved thresholds, and updates the config automatically.

---

## Decision

**Implement an automated 8-step self-optimization loop (`rsi_main.py`) that runs immediately after each Monday screen:**

```
Step 1: regime_detector.py    -- Classify current market (bull/correction/recovery/...)
Step 2: performance_tracker.py -- Fetch forward returns for open/closed picks
Step 3: signal_analyzer.py    -- Bucket outcomes; compute win rates per bucket
Step 4: optimizer.py          -- Derive improved thresholds from bucket stats
Step 5: screener.py           -- (already run at 06:00 with current config)
Step 6: research_layer.py     -- Gemini qualitative filtering (optional)
Step 7: report_generator.py   -- Plain-English improvement report (Gemini)
Step 8: (dashboard update)    -- signal_quality.json and improvement_report.json refreshed
```

**Data-driven mode activation rule:** The optimizer uses data-driven thresholds only when a bucket has >= 10 historical picks. Below this threshold, it falls back to `regime_defaults` to prevent overfitting to small samples.

**Config audit trail:** Every parameter change is appended to `config_history.json` with before/after values, timestamp, and regime context. No config change is made without an audit record.

---

## Rationale

**Why automated over manual:**

- Manual tuning requires consistent effort from the owner every week. In practice, manual tuning either gets skipped (parameters stagnate) or is based on recency bias (over-weighting recent losses)
- Automated analysis operates on the full pick history without bias — it weights all observations equally within each bucket
- The system can detect regime changes and adjust in response without human intervention. A manual process would lag by weeks

**Why regime-bucketed analysis:**

- RSI signals perform very differently in bull vs correction markets. A single global threshold optimised across all regimes would be worse than regime-specific thresholds
- Bucketing by RSI tier and volume tier (in addition to regime) identifies which signal combinations actually produce positive returns
- The minimum-sample threshold (10 picks per bucket) prevents the optimizer from deriving thresholds from 2-3 picks, which would be noise not signal

**Why picks_history.json grows without reset:**

- The full history is valuable: regime cycles repeat (bull, correction, recovery, bull...) and historical corrections from 2-3 years ago are relevant to the current correction. Resetting history would discard this
- The optimizer handles growth by treating all samples equally within a regime bucket; recency is not artificially weighted

---

## Consequences

**Positive:**
- Parameters improve over time without manual effort
- Regime-aware thresholds prevent using bull-market parameters in a correction (which would produce too many picks) or correction parameters in a bull (which would produce none)
- Config history provides a full audit trail of every parameter change with rationale
- Currently (April 2026): 1,332 picks tracked; fully data-driven mode active across all regimes

**Negative / trade-offs:**
- Complex pipeline with 8 steps; any step failure must not block execution (executor runs at 09:15 regardless)
- Optimizer makes automated changes to `screener_config.json` — owner must trust the process or review `config_history.json` weekly
- `picks_history.json` grows unbounded (technical debt TD3); after ~3 years could become slow to load (mitigation: prune or migrate to SQLite)
- Feedback loop latency: a bad parameter change takes at least one week to produce picks, another 5-20 days for return data, then one more week to be corrected

**Safeguards:**
- Minimum sample threshold (10 picks) prevents overfitting to small buckets
- `regime_defaults` provide sane fallback values for any bucket with insufficient data
- Owner can override any parameter by directly editing `screener_config.json` between loop runs (change will be overwritten next Monday, so permanent overrides require modifying `regime_defaults`)
