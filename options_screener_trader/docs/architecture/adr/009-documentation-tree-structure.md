# ADR-009 — Comprehensive `docs/` Tree Structure

**Status:** Accepted  
**Date:** 2026-04-25  
**Deciders:** Project team

---

## Context

Before this decision, project documentation consisted of:
- A flat `STRATEGY_SPEC.md` in the project root — written as a pitch doc before the pipeline existed
- An `arc42` folder (`docs/architecture/`) with 12 formal architecture sections and 8 ADRs

This left several gaps for anyone onboarding or operating the system:

1. **No plain-English strategy explanation** — arc42 sections are technical and assume prior context
2. **No operational procedures** — nothing on how to run a health check, troubleshoot errors, or set up from scratch
3. **No visual maps** — arc42 sections 5/6/7 describe the system in prose; no diagrams existed
4. **No reference for data schemas** — the JSON file formats were only documented implicitly in code
5. **Single doc as source of truth** — `STRATEGY_SPEC.md` was both design doc and strategy bible, but diverged from the actual implementation as the system evolved

The `docs/architecture/11-risks-and-technical-debt.md` file explicitly listed replacing `STRATEGY_SPEC.md` as TD-01.

The inspiration for the new structure came from distributed-systems documentation practice:
supplementing arc42 with C4 model diagrams, runtime sequence diagrams, API reference docs,
and operational runbooks.

---

## Decision

Organise all documentation under `docs/` in five audience-based subdirectories:

```
docs/
  guides/        Plain-English narrative for humans onboarding
  reference/     Precise technical specs (schemas, APIs, config)
  diagrams/      Visual representations (Mermaid — C4 + sequence)
  runbooks/      Operational how-tos and troubleshooting
  architecture/  Formal arc42 + ADRs (existing, unchanged)
  archive/       Superseded documents (STRATEGY_SPEC.md)
```

A `docs/README.md` acts as a master index with a "I want to…" quick-jump table.

### Why five folders, not one flat `docs/`

Each folder serves a distinct audience and purpose:

| Folder | Audience | Purpose |
|---|---|---|
| `guides/` | New team members, anyone onboarding | Understand *why* and *how* in narrative form |
| `reference/` | Developers, operators who need exact field names | Precise specifications, not narrative |
| `diagrams/` | Visual learners, architects | System maps — faster to read than prose |
| `runbooks/` | The operator at 9pm when something breaks | Step-by-step procedures |
| `architecture/` | Architects, reviewers | Formal structure, design decisions |

Mixing them into a flat directory creates a discoverability problem: a new team member
doesn't know whether to read `config-schema.md` or `05-building-block-view.md` first.
The folder names signal intent immediately.

### Why Mermaid for diagrams

Several diagramming formats were considered:

| Option | Pros | Cons | Decision |
|---|---|---|---|
| **Mermaid** (chosen) | Renders natively in GitHub, VS Code, Obsidian; version-controlled as text; no install | Not as polished as vector tools | ✓ Chosen |
| PlantUML | Powerful, widely used | Requires Java runtime; needs server or plugin to render | ✗ Rejected |
| draw.io / Lucidchart | Beautiful, easy UI | Binary or XML files; poor Git diff; external service dependency | ✗ Rejected |
| ASCII art | Zero dependencies | Hard to maintain; no standard; ugly | ✗ Rejected |
| Structurizr (C4 native) | First-class C4 support | Separate tool/service, extra setup for a pipeline this size | ✗ Rejected |

Mermaid renders anywhere the code lives. A developer reading the file on GitHub sees the
diagram without any additional setup. Diagrams are diffs like any other markdown change.

### Why archive, not delete, `STRATEGY_SPEC.md`

The spec was written before the pipeline existed and contains reasoning about the strategy
that isn't captured in arc42 (which focuses on architecture, not trading philosophy). Keeping
it in `docs/archive/` preserves that context without it being mistaken for current documentation.

---

## Alternatives considered

### Option A — Keep `STRATEGY_SPEC.md`, add a flat `docs/` with a few extra files
Rejected: discoverability problem remains; flat structure doesn't scale as the project grows.
A future operator searching for "how do I troubleshoot OPRA 403" shouldn't have to scan 20
files to find the troubleshooting guide.

### Option B — External wiki (Confluence, Notion, GitHub Wiki)
Rejected: external services introduce a dependency not warranted for a single-developer
pipeline. Documentation should live with the code so it stays in sync, gets version-controlled,
and doesn't require another login or subscription.

### Option C — Single long README.md at project root
Rejected: a README is a welcome mat, not a documentation system. Long READMEs become
unmaintainable and unsearchable. Appropriate for a short project; this pipeline has enough
operational complexity to warrant proper structure.

---

## Consequences

**Positive:**
- Any new team member has a clear reading path: guides first, then reference and architecture
- Operational procedures are findable under pressure (troubleshooting runbook)
- Diagrams give a 10-second system overview that prose cannot
- `STRATEGY_SPEC.md` context is preserved; its supersession is explicit and dated
- Architecture README now links to the guides for non-technical onboarding

**Negative / tradeoffs:**
- More files to maintain — a docs change may need updates in both a guide and an architecture section
- Mermaid diagrams don't render in all markdown viewers (though GitHub and VS Code both support it)

**Neutral:**
- The `arc42` structure is unchanged — existing architecture docs are not reorganized
- No new tooling introduced; everything is plain Markdown files

---

## Links

- [docs/README.md](../../README.md) — master index
- [docs/guides/](../../guides/) — narrative guides
- [docs/diagrams/](../../diagrams/) — Mermaid diagrams
- [docs/archive/STRATEGY_SPEC.md](../../archive/STRATEGY_SPEC.md) — archived spec
- [TD-01 resolved](../11-risks-and-technical-debt.md)
