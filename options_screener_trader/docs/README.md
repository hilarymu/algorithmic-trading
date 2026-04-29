# Options Screener Trader — Documentation Index

This folder contains all project documentation, organized by audience and purpose.

---

## Quick orientation

| I want to… | Go to |
|---|---|
| Understand the strategy in plain English | [guides/01-strategy-overview.md](guides/01-strategy-overview.md) |
| Learn what runs each day and why | [guides/02-pipeline-walkthrough.md](guides/02-pipeline-walkthrough.md) |
| Set this up from scratch | [guides/03-getting-started.md](guides/03-getting-started.md) |
| See the system as a diagram | [diagrams/c4-context.md](diagrams/c4-context.md) |
| Understand a config option | [reference/config-schema.md](reference/config-schema.md) |
| Understand a data file | [reference/data-formats.md](reference/data-formats.md) |
| Debug a specific error | [runbooks/troubleshooting.md](runbooks/troubleshooting.md) |
| Do a morning health-check | [runbooks/daily-health-check.md](runbooks/daily-health-check.md) |
| Understand *why* a design decision was made | [architecture/adr/](architecture/adr/) |
| Read the full technical architecture | [architecture/README.md](architecture/README.md) |

---

## Folder structure

```
docs/
  guides/          Plain-English explanations for humans
  reference/       Precise technical reference (schemas, APIs)
  diagrams/        Visual maps of the system (Mermaid / C4)
  runbooks/        How-to and operational procedures
  architecture/    arc42 architecture documentation + ADRs
  archive/         Superseded documents (kept for history)
```

---

## Guides

Step-by-step introductions for anyone new to the project.

| Document | Purpose |
|---|---|
| [01-strategy-overview.md](guides/01-strategy-overview.md) | What this system is, why it works, strategy hierarchy |
| [02-pipeline-walkthrough.md](guides/02-pipeline-walkthrough.md) | The 7-step daily pipeline explained module by module |
| [03-getting-started.md](guides/03-getting-started.md) | Installation, configuration, and first run |

---

## Reference

Precise technical details for when you need the exact spec.

| Document | Purpose |
|---|---|
| [config-schema.md](reference/config-schema.md) | Every field in `options_config.json` documented |
| [data-formats.md](reference/data-formats.md) | Every JSON data file: schema, producer, consumers |
| [alpaca-api-usage.md](reference/alpaca-api-usage.md) | API endpoints used, rate limits, OPRA constraints |

---

## Diagrams

Visual representations. All diagrams use [Mermaid](https://mermaid.js.org/) syntax
(renders in GitHub, VS Code, most markdown viewers).

| Document | Purpose |
|---|---|
| [c4-context.md](diagrams/c4-context.md) | System boundary: what the system is and who/what it talks to |
| [c4-containers.md](diagrams/c4-containers.md) | The 8 Python modules and 9 data files and how they connect |
| [runtime-daily-run.md](diagrams/runtime-daily-run.md) | Sequence diagram: the 7-step 16:30 ET daily run |
| [data-flow.md](diagrams/data-flow.md) | Which module writes which file and who reads it |

---

## Runbooks

Operational procedures for day-to-day running.

| Document | Purpose |
|---|---|
| [first-run-setup.md](runbooks/first-run-setup.md) | Complete setup from a blank machine |
| [daily-health-check.md](runbooks/daily-health-check.md) | What to verify after each 16:30 ET daily run |
| [troubleshooting.md](runbooks/troubleshooting.md) | Known errors, their causes, and fixes |

---

## Architecture

Formal technical architecture using the [arc42](https://arc42.org) template,
supplemented by Architectural Decision Records (ADRs).

See [architecture/README.md](architecture/README.md) for the full index.

Key sections for onboarders:

| Section | Most useful for |
|---|---|
| [05-building-block-view.md](architecture/05-building-block-view.md) | Component responsibilities |
| [06-runtime-view.md](architecture/06-runtime-view.md) | Detailed runtime sequences |
| [07-deployment-view.md](architecture/07-deployment-view.md) | Task Scheduler, directory layout |
| [12-glossary.md](architecture/12-glossary.md) | IV rank, CSP, DTE, delta, Wheel, regime |
| [adr/](architecture/adr/) | Why each major decision was made |

---

## Archive

Documents superseded by this documentation set. Kept for historical context.

| Document | Notes |
|---|---|
| [archive/STRATEGY_SPEC.md](archive/STRATEGY_SPEC.md) | Original strategy spec (v1.0). Superseded by guides and architecture docs. |
