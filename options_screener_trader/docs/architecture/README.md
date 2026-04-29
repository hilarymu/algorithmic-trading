# Options Screener Trader — Architecture Documentation

Architecture documented using the [arc42](https://arc42.org) template.
Any change to the system should be reflected in the relevant section below.
New architecture decisions are recorded by creating `adr/0NN-title.md` and adding
a row to the ADR table below and in [09-decisions.md](09-decisions.md).

---

## Sections

| # | Document | Summary |
|---|----------|---------|
| 1 | [Introduction and Goals](01-introduction-and-goals.md) | What we're building, stakeholders, top quality goals |
| 2 | [Constraints](02-constraints.md) | API limits, paper-only scope, naming conventions |
| 3 | [Context and Scope](03-context-and-scope.md) | System boundary, external interfaces, what's in/out of scope |
| 4 | [Solution Strategy](04-solution-strategy.md) | Strategy selection matrix, exit rules, self-improvement loop, phase plan |
| 5 | [Building Block View](05-building-block-view.md) | Component map, module responsibilities, reused screener_trader parts |
| 6 | [Runtime View](06-runtime-view.md) | Daily pipeline sequences, error handling, position lifecycle state machine |
| 7 | [Deployment View](07-deployment-view.md) | Task Scheduler, directory layout, logging format |
| 8 | [Cross-cutting Concepts](08-crosscutting-concepts.md) | Shared regime detector, self-improvement pattern, earnings flag, API error handling |
| 9 | [Decisions](09-decisions.md) | Architecture Decision Records (ADRs) — table of contents |
| 10 | [Quality Requirements](10-quality-requirements.md) | Quality tree, quality scenarios, 12-month target metrics |
| 11 | [Risks and Technical Debt](11-risks-and-technical-debt.md) | Risk register, hard safety rules, tech debt backlog |
| 12 | [Glossary](12-glossary.md) | Definitions for IV Rank, CSP, DTE, delta, Wheel, regime, and more |

---

## Architecture Decision Records

Stored in [`adr/`](adr/). Use `/record-adr` to add a new one.

| # | Decision | Status |
|---|----------|--------|
| [001](adr/001-self-computed-iv-rank.md) | Self-compute IV Rank from Alpaca indicative feed | Accepted |
| [002](adr/002-direct-contract-symbol-construction.md) | Construct option contract symbols directly without contracts API | Accepted |
| [003](adr/003-earnings-as-signal-not-block.md) | Treat earnings proximity as signal flag, not a hard entry block | Accepted |
| [004](adr/004-regime-aware-strategy-selection.md) | Use shared regime detector to gate strategy selection | Accepted |
| [005](adr/005-phased-build-iv-first.md) | Build IV history before placing any options orders | Accepted |
| [006](adr/006-intraday-polling-over-websocket.md) | Use polling loop over Alpaca websocket for intraday exit monitoring | Accepted |
| [007](adr/007-separate-leg-spread-execution.md) | Execute spread legs as separate single-leg limit orders | Accepted |
| [008](adr/008-hv30-proxy-iv-backfill.md) | Use HV30 realized-vol proxy when OPRA historical bars are unavailable | Accepted |
| [009](adr/009-documentation-tree-structure.md) | Comprehensive `docs/` tree — guides, reference, diagrams, runbooks, Mermaid | Accepted |
| [010](adr/010-sqlite-for-data-store.md) | Migrate JSON flat files to SQLite for query capability and write safety | Deferred |

---

## Current Build Phase

**Phase 3 — Complete (as of 2026-04-26); self-optimizing loop active**
- All 7 pipeline steps wired: iv_tracker → screener → monitor → selector → executor → signal_analyzer → optimizer
- `iv_backfill.py` bootstraps IV history — HV30 proxy fallback active (ADR-008; OPRA unavailable on paper account)
- 512 symbols with IV rank · median rank 55 · 348 in sell zone (≥ 40)
- Daily run via `scripts/run_options_loop.bat` at 16:30 ET; intraday monitor via `scripts/run_options_monitor_intraday.bat` at 09:30 ET Mon–Fri
- Runtime data in `data/` (gitignored); 261 unit tests passing
- Optimizer generates insights after 10 closed positions; auto-applies at 50 (`auto_optimize.enabled: false` until then)
- Phase 4 (SQLite migration) deferred — revisit at ≥ 50 closed positions (ADR-010)

See [04-solution-strategy.md § Phased Build](04-solution-strategy.md) for the full phase plan.
