# Options Screener Trader

A self-optimizing daily options pipeline for paper trading cash-secured puts (CSPs) on S&P 500 and NASDAQ 100 stocks.

**Core edge:** RSI mean-reversion signal + high IV rank environment → sell premium when options are expensive and stocks are oversold.

---

## How it works

```
16:30 ET daily (Mon–Fri)
  iv_tracker        → snapshot today's IV, update 252-day rank
  options_screener  → RSI < 25, IV rank ≥ 40, volume confirmed
  options_monitor   → check exit conditions on open positions
  options_selector  → BSM delta-targeting to pick the right strike
  options_executor  → place paper orders via Alpaca
  signal_analyzer   → score candidates, aggregate outcomes
  optimizer         → generate config insights (apply when n ≥ 50)
  dashboard         → regenerate data/dashboard.html

09:30 ET intraday (Mon–Fri)
  options_monitor   → check loss limits every 15 min during market hours
```

All state stored in `data/`. No external database. Zero third-party runtime dependencies.

---

## Quick start

```bash
# 1. Clone
git clone <repo-url>
cd options_screener_trader

# 2. Configure credentials
cp alpaca_config.example.json alpaca_config.json
# edit alpaca_config.json with your Alpaca paper API key

# 3. First run (bootstraps IV history automatically)
py -3 options_main.py

# 4. Run tests
py -3 -m pytest tests/ -v
```

Full setup guide: [docs/runbooks/first-run-setup.md](docs/runbooks/first-run-setup.md)

---

## Project structure

```
options_screener_trader/
├── options_main.py              Daily orchestrator (7-step pipeline)
├── options_config.json          Strategy parameters (tracked in git)
├── alpaca_config.example.json   Credential template (copy → alpaca_config.json)
├── options_loop/                Pipeline modules
│   ├── iv_tracker.py            Step 1 — daily IV snapshot + IV rank
│   ├── iv_backfill.py           Step 0 — bootstrap 252-day IV history (first run)
│   ├── options_screener.py      Step 2 — RSI + IV rank + volume filter
│   ├── options_monitor.py       Step 3 — exit condition monitoring
│   ├── options_strategy_selector.py  Step 4 — BSM strike/expiry selection
│   ├── options_executor.py      Step 5 — Alpaca paper order placement
│   ├── options_signal_analyzer.py   Step 6 — candidate scoring + outcome stats
│   ├── options_optimizer.py    Step 7 — parameter insights + auto-tuning
│   └── options_dashboard.py    Step 8 — generate data/dashboard.html
├── tests/                       Unit tests (261 tests, stdlib unittest + pytest)
├── scripts/                     Windows Task Scheduler bat files
├── data/                        Runtime JSON state (gitignored)
└── docs/                        Full documentation
    ├── guides/                  Plain-English strategy + pipeline guides
    ├── reference/               Config schema, data formats, API usage
    ├── diagrams/                C4 + sequence diagrams (Mermaid)
    ├── runbooks/                Setup, daily health check, troubleshooting
    └── architecture/            arc42 + 10 ADRs
```

---

## Strategy summary

| Signal | Threshold | Strategy |
|--------|-----------|----------|
| RSI + IV rank + volume | RSI < 25, IV rank ≥ 40, vol ≥ 1.2× | Sell cash-secured put |
| Extreme oversold | RSI < 20, IV rank < 30 | Buy call debit spread |
| After assignment | — | Sell covered call (Wheel) |
| Bear regime | — | No new entries |

Exit rules: 50% profit target · 2× loss limit · 21 DTE close · RSI recovery above 50

Full strategy explanation: [docs/guides/01-strategy-overview.md](docs/guides/01-strategy-overview.md)

---

## Self-improvement loop

```
closed positions → signal_analyzer → outcome stats
                                          ↓
                             optimizer generates insights
                                          ↓
                 n ≥ 10: suggestions shown   n ≥ 50: applied automatically
                                          ↓
                              options_config.json updated
```

---

## Requirements

- Python 3.11+
- Alpaca paper trading account with options enabled
- Windows (for Task Scheduler automation)
- No third-party Python packages required for runtime
- `pytest` for running tests: `py -3 -m pip install pytest`

---

## Documentation

| I want to… | Go to |
|---|---|
| Understand the strategy | [docs/guides/01-strategy-overview.md](docs/guides/01-strategy-overview.md) |
| Set up from scratch | [docs/runbooks/first-run-setup.md](docs/runbooks/first-run-setup.md) |
| Understand a config option | [docs/reference/config-schema.md](docs/reference/config-schema.md) |
| See a system diagram | [docs/diagrams/c4-context.md](docs/diagrams/c4-context.md) |
| Debug an error | [docs/runbooks/troubleshooting.md](docs/runbooks/troubleshooting.md) |
| Understand a design decision | [docs/architecture/adr/](docs/architecture/adr/) |

---

## Phase status

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | IV history + research screener | ✅ Complete |
| 2 | Live paper order placement + exit monitoring | ✅ Complete |
| 3 | Signal analyzer + self-optimizing loop | ✅ Complete |
| 4 | SQLite migration for data store | ⏸ Deferred — revisit at ≥ 50 closed positions ([ADR-010](docs/architecture/adr/010-sqlite-for-data-store.md)) |
