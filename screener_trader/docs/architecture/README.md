# screener_trader — Architecture Documentation

This folder contains the arc42 architecture documentation for **screener_trader**, a self-optimising mean-reversion equity screener that trades against an Alpaca paper account.

---

## Document Index

| # | Section | Contents |
|---|---------|----------|
| [01](01-introduction-and-goals.md) | Introduction and Goals | System purpose, quality goals, stakeholders |
| [02](02-constraints.md) | Constraints | Technology, organisational, and regulatory constraints |
| [03](03-context-and-scope.md) | Context and Scope | External systems, data flows, system boundary |
| [04](04-solution-strategy.md) | Solution Strategy | Core design decisions and architectural approach |
| [05](05-building-block-view.md) | Building Block View | Component breakdown and responsibilities |
| [06](06-runtime-view.md) | Runtime View | Sequence diagrams for key scenarios |
| [07](07-deployment-view.md) | Deployment View | Windows Task Scheduler, file system layout |
| [08](08-crosscutting-concepts.md) | Cross-cutting Concepts | Shared patterns: error handling, logging, self-improvement |
| [09](09-decisions.md) | Architecture Decisions | ADR summary table and design principles |
| [10](10-quality-requirements.md) | Quality Requirements | Quality tree, scenarios, performance targets |
| [11](11-risks-and-technical-debt.md) | Risks and Technical Debt | Active risks, debt items, deferred decisions |
| [12](12-glossary.md) | Glossary | Trading and technical term definitions |

---

## ADR Index

Architecture Decision Records are in the [`adr/`](adr/) subfolder.

| ADR | Decision | Status |
|-----|----------|--------|
| [ADR-001](adr/001-mean-reversion-entry-strategy.md) | Mean-Reversion as Primary Entry Strategy | Accepted |
| [ADR-002](adr/002-weekly-screener-schedule.md) | Weekly Monday Screener Schedule | Accepted |
| [ADR-003](adr/003-add-down-ladder-position-sizing.md) | Add-Down Ladder for Position Sizing | Accepted |
| [ADR-004](adr/004-self-optimizing-rsi-loop.md) | Self-Optimizing RSI Loop | Accepted |
| [ADR-005](adr/005-gemini-research-layer.md) | Gemini Research Layer for Qualitative Filtering | Accepted |

---

## Quick Reference — Monday Pipeline

```
06:00  screener.py        Screen S&P 500; write pending_entries.json
06:05  rsi_main.py        8-step optimisation loop; update screener_config.json
09:15  entry_executor.py  Place market orders for approved entries
09:30–16:00  monitor.py  (every 15 min) Manage stops, trailing stops, ladders, exits
```

---

## Key Design Principles

1. **Capital preservation first** — Hard stops are non-negotiable
2. **Human veto over full automation** — 3.25-hour window between screen and execution
3. **Data-driven self-improvement** — Parameters auto-tune from pick history
4. **Fail safe** — Every API failure caught and logged; pipeline continues
5. **Paper trading only** — No real capital at risk
