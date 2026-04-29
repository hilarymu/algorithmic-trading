# Diagrams

Visual representations of the system. All use [Mermaid](https://mermaid.js.org/) syntax,
which renders natively in GitHub, VS Code (with Mermaid extension), and Obsidian.

| Document | Purpose |
|---|---|
| [c4-context.md](c4-context.md) | **C4 Level 1** — System boundary: the pipeline, Task Scheduler, Alpaca API, and the operator |
| [c4-containers.md](c4-containers.md) | **C4 Level 2** — The 8 Python modules + 9 data files and how they connect |
| [runtime-daily-run.md](runtime-daily-run.md) | **Sequence diagram** — Full 7-step daily pipeline with data handoffs and error paths |
| [data-flow.md](data-flow.md) | **Data flow graph** — Which module writes which file and downstream impact of failures |

**Reading order for visual learners:**
1. `c4-context.md` — big picture first
2. `c4-containers.md` — zoom into the modules
3. `data-flow.md` — understand data dependencies
4. `runtime-daily-run.md` — watch it run end-to-end
