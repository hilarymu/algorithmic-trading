# 9. Architecture Decisions

This section summarises the key architecture decisions for screener_trader.
Each decision is documented as a formal ADR in the `adr/` subfolder.

| ADR | Title | Status |
|-----|-------|--------|
| [ADR-001](adr/001-mean-reversion-entry-strategy.md) | Mean-Reversion as Primary Entry Strategy | Accepted |
| [ADR-002](adr/002-weekly-screener-schedule.md) | Weekly Monday Screener Schedule | Accepted |
| [ADR-003](adr/003-add-down-ladder-position-sizing.md) | Add-Down Ladder for Position Sizing | Accepted |
| [ADR-004](adr/004-self-optimizing-rsi-loop.md) | Self-Optimizing RSI Loop | Accepted |
| [ADR-005](adr/005-gemini-research-layer.md) | Gemini Research Layer for Qualitative Filtering | Accepted |

---

## Key Design Principles Reflected in These Decisions

1. **Capital preservation first** — Hard stops are non-negotiable; no position ever lacks a stop
2. **Human veto over full automation** — 3.25-hour window between screen and execution for manual review
3. **Data-driven self-improvement** — Parameters auto-tune from signal history; no manual parameter tweaking needed
4. **Fail safe** — Every API failure is caught and logged; the pipeline continues with available data
5. **Paper trading only** — All decisions made in paper account context; architecture does not touch real capital
