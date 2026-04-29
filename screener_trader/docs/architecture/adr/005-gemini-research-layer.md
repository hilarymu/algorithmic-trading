# ADR-005: Gemini Research Layer for Qualitative Filtering

**Status:** Accepted  
**Date:** 2025-Q2  
**Deciders:** Project owner

---

## Context

The mechanical screener (RSI + BB + volume) produces a ranked list of oversold candidates but has no awareness of qualitative context:

- Is there a pending earnings report that could make the stock gap down further?
- Is the sector in structural decline (not a mean-reversion candidate)?
- Did news break over the weekend that explains the sell-off and makes recovery less likely?

Two options were considered for adding this layer:

**Option A — No qualitative filter:** Accept all mechanical picks, rely on the hard stop for protection. Simpler; no LLM dependency.

**Option B — LLM qualitative filter:** Pass the top mechanical candidates to a language model with current indicator data; ask it to rank by recovery probability and flag concerns.

---

## Decision

**Add an optional Gemini 2.5 Flash research layer as a non-blocking step in the weekly pipeline.**

Key design constraints that shaped this decision:

1. **Non-blocking:** If Gemini is unavailable or the API key is not configured, the trading pipeline continues unaffected. The executor reads `pending_entries.json` (mechanical output), not `research_picks.json` (Gemini output)
2. **Advisory only:** Gemini output is displayed on the dashboard for human review but does not automatically suppress or promote any pick
3. **Prompt contains hard data:** Gemini is given the actual RSI, BB distance, volume ratio, and regime — it must reason from data, not confabulate
4. **Two separate Gemini calls:**
   - `research_layer.py` — research picks with rationale (top 15 candidates in, ranked picks out)
   - `report_generator.py` — plain-English improvement report from `signal_quality.json` stats

**Configuration:** API key read from `alpaca_config.json` (key: `gemini_api_key`) or `GEMINI_API_KEY` environment variable. If neither is present, `research_picks.json` is not written and the dashboard shows a setup message.

---

## Rationale

**Why add an LLM layer at all:**

- The mechanical screener cannot distinguish between a stock that is oversold due to sector rotation (likely to recover) vs. a stock that is oversold due to a fundamental business deterioration (unlikely to recover quickly)
- An LLM with broad training data on market events, sectors, and companies can flag obvious qualitative concerns that quantitative indicators miss
- The dashboard already shows the Gemini output — the human reviewing picks on Monday morning benefits from having this context alongside the RSI/BB numbers

**Why advisory rather than automated suppression:**

- LLM outputs are probabilistic — false suppression of good picks would cost real returns over time
- The human review window (06:00–09:15) is the right place to apply qualitative judgment; the LLM assists but does not decide
- Adding automated suppression would create a system where a flaky LLM call could silently block all entries for the week

**Why Gemini 2.5 Flash specifically:**

- Flash model is fast (under 10 seconds per call) — acceptable within the pre-market window
- Flash is significantly cheaper per token than Pro, appropriate for a weekly research call with a ~2,000 token prompt
- Google's Gemini API was available without waitlist at the time of implementation; alternatives (GPT-4, Claude via API) were not evaluated but could substitute

**Why non-blocking:**

- The trading pipeline's safety guarantees must not depend on any third-party LLM API. If Gemini has a service outage Monday morning, entries must still execute
- `pending_entries.json` is populated by the mechanical screener before the Gemini call; the executor reads only this file

---

## Consequences

**Positive:**
- Qualitative context available to the human reviewer each Monday morning
- Plain-English improvement reports make the signal_quality statistics accessible without reading JSON
- Gemini failures have zero impact on execution pipeline

**Negative / trade-offs:**
- Gemini API key required (additional credential to manage)
- Gemini 2.5 Flash has a knowledge cutoff — very recent events may not be in its training data; it cannot browse the web
- The prompt is fixed; Gemini may not consistently apply the same ranking criteria across weeks
- Advisory output has unknown effect on human decision-making; owner may over-weight or under-weight it

**Accepted risks:**
- LLM hallucination on individual stock commentary. Mitigated by: (1) prompt includes hard data; (2) output is advisory only; (3) human reviews before acting
- API key exposure. Mitigated by: key stored in `alpaca_config.json` (not committed to source control)
