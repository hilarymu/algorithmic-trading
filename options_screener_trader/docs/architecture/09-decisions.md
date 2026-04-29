# 9. Architecture Decisions

This chapter contains the Architecture Decision Records (ADRs) for the
Options Screener Trader. ADR files are stored in `docs/architecture/adr/`.

To add a new ADR: create `adr/0NN-short-title.md` using the template below,
then add a row to the table here and to `architecture/README.md`.

---

## Table of Contents

| # | Title | Status | Date |
|---|-------|--------|------|
| [001](adr/001-self-computed-iv-rank.md) | Self-compute IV Rank from Alpaca indicative feed | Accepted | 2026-04-23 |
| [002](adr/002-direct-contract-symbol-construction.md) | Construct option contract symbols directly without contracts API | Accepted | 2026-04-23 |
| [003](adr/003-earnings-as-signal-not-block.md) | Treat earnings proximity as signal flag, not a hard entry block | Accepted | 2026-04-23 |
| [004](adr/004-regime-aware-strategy-selection.md) | Use shared regime detector to gate strategy selection | Accepted | 2026-04-23 |
| [005](adr/005-phased-build-iv-first.md) | Build IV history before placing any options orders | Accepted | 2026-04-23 |
| [006](adr/006-intraday-polling-over-websocket.md) | Use polling loop over Alpaca websocket for intraday exit monitoring | Accepted | 2026-04-24 |
| [007](adr/007-separate-leg-spread-execution.md) | Execute spread legs as separate single-leg limit orders | Accepted | 2026-04-24 |
| [008](adr/008-hv30-proxy-iv-backfill.md) | Use HV30 realized-vol proxy when OPRA historical bars are unavailable | Accepted | 2026-04-25 |
| [009](adr/009-documentation-tree-structure.md) | Comprehensive `docs/` tree — guides, reference, diagrams, runbooks, Mermaid | Accepted | 2026-04-25 |
| [010](adr/010-sqlite-for-data-store.md) | Migrate JSON flat files to SQLite for query capability and write safety | Deferred | 2026-04-25 |
