# ADR-004: Use shared regime detector to gate strategy selection

**Date:** 2026-04-23  
**Status:** Accepted

---

## Decision

Import `regime_detector.py` directly from `screener_trader` and use its regime
classification as the primary gate for options strategy selection.

---

## Context

The equity screener has a proven regime detector that classifies the market into
`bull`, `mild_correction`, `correction`, `bear`, `recovery`, and `geopolitical_shock`
based on SPY relative to its 200-day MA, 20-day return, and VIX level. The same
macro context that determines whether to buy equities should determine which options
strategy (or whether to trade at all) is appropriate.

---

## Options Considered

### Option A — Shared import from screener_trader

Import `regime_detector.run()` directly. Both sub-projects stay in sync automatically.

**Pros:**
- Single source of truth for regime
- No duplication; consistency guaranteed
- Regime improvements benefit both systems simultaneously

**Cons:**
- Coupling between two sub-projects via file-system import
- `screener_trader` path must be stable

### Option B — Duplicate regime_detector.py in options_screener_trader

Copy the file; maintain independently.

**Pros:**
- Full independence between sub-projects

**Cons:**
- Divergence risk; bug fixes applied in one, missed in other
- Contradictory regime signals possible if they drift

### Option C — New, simpler regime rule for options only

**Pros:**
- Tailored to options (e.g., VIX-only gating)

**Cons:**
- Duplicates analysis; introduces inconsistency with equity signals

---

## Decision Outcome

**Chosen option: A** — shared import. The projects are intentionally related; a shared
regime is a feature, not a bug. The file-system coupling is acceptable given single-developer
ownership of both sub-projects.

---

## Consequences

- ✅ Regime signals are always consistent across equity and options decisions
- ✅ Zero maintenance overhead for regime logic in options sub-project
- ✅ Regime improvements (e.g., new geopolitical shock detection) automatically apply
- ⚠️ Renaming/moving `screener_trader` breaks the options import — path must stay stable
- ⚠️ In bear regime, options system places zero new trades — this is intentional, not a bug
