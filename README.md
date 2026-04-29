# Autonomous Paper Trading Systems

Three independent, fully-automated paper trading strategies built on the [Alpaca Markets](https://alpaca.markets) paper API. Each strategy runs on a schedule via Windows Task Scheduler, manages its own positions end-to-end, and maintains a local HTTP dashboard for live monitoring.

> **Paper trading only** — all orders execute against Alpaca's simulated environment. No real money is involved.

**What this project demonstrates:**
- **Autonomous scheduling & orchestration** — three independent pipelines run unattended via Windows Task Scheduler across daily, weekly, and intraday cadences
- **Self-optimising ML-adjacent pipelines** — RSI loop back-fills its own historical returns, detects market regimes, and rewrites its own config each week from performance data
- **API integration** — Alpaca REST (order management + market data), Google Gemini AI (qualitative research layer), Capitol Trades HTML scraping (no API key required)
- **Full position lifecycle management** — entry, trailing stops, hard stops, add-down ladder orders, and RSI-recovery exits all handled automatically
- **Production-quality architecture documentation** — all three systems documented to the [arc42](https://arc42.org) standard with Architecture Decision Records (ADRs)

---

## Systems at a glance

| System | Strategy | Schedule | Dashboard |
|--------|----------|----------|-----------|
| [Screener Trader](#1-screener-trader) | S&P 500 mean-reversion (RSI + Bollinger Bands) | Weekly screener · Daily monitor | `localhost:8766` |
| [Options Screener Trader](#2-options-screener-trader) | Cash-secured puts on high-IV oversold stocks | Daily post-close pipeline | `localhost:8767` |
| [Politician Copy Trader](#3-politician-copy-trader) | Mirror congressional stock disclosures | Daily Capitol Trades poll | `localhost:8765` |

---

## 1. Screener Trader

**Strategy:** Stocks that are deeply oversold (RSI < configurable threshold), at or below their lower Bollinger Band, with above-average volume tend to mean-revert. The system screens the full S&P 500 weekly, queues candidates for review, and places market orders Monday morning.

**What makes it interesting:**
- **Self-optimising RSI loop** — an 8-step pipeline runs after every screener, back-fills forward returns, analyses signal quality, and auto-tunes `screener_config.json` for the following week. No manual parameter adjustments.
- **Regime awareness** — detects bull/bear/volatile/sideways regimes and applies regime-specific thresholds rather than one-size-fits-all settings.
- **Gemini AI research layer** — optional qualitative filter using Google Gemini 2.5 Flash; advisory-only and non-blocking.
- **Full position lifecycle** — trailing stops, hard stops, add-down ladder orders (4 rungs), and RSI-recovery exits all managed automatically.

**Weekly pipeline:**
```
Mon 06:00  screener.py          → scan 500 stocks, write pending_entries.json
Mon 07:00  rsi_loop/rsi_main.py → self-improvement loop, tune screener_config.json
           (3.25 hr review window — edit pending_entries.json to skip any pick)
Mon 09:15  entry_executor.py    → place market buy orders via Alpaca
Mon–Fri    monitor.py           → RSI exit · trailing stop · hard stop · ladder
           every 15 min, 09:25–16:05 ET
```

[Full architecture docs →](screener_trader/docs/architecture/README.md)

---

## 2. Options Screener Trader

**Strategy:** Sell cash-secured puts when a stock is oversold (RSI < 25) *and* options are expensive (IV rank ≥ 40). High IV means fat premiums; mean-reversion in the underlying means the stock is unlikely to keep falling.

**What makes it interesting:**
- **Self-computed IV rank** — calculates 252-day IV rank locally from Alpaca bar data (no options data subscription required).
- **Black-Scholes strike selection** — targets delta ~0.20 strikes using BSM pricing to pick the right expiry/strike combination.
- **Regime-aware strategy selection** — uses the same regime classifier as the equity screener to switch between aggressive/conservative parameters.
- **Zero runtime dependencies** — pure Python standard library; no pip packages required to run.

**Daily pipeline (post-close):**
```
16:30 ET  iv_tracker → options_screener → options_selector → options_executor
          signal_analyzer → optimizer → dashboard regeneration
09:30 ET  options_monitor  → intraday loss-limit checks (every 15 min)
```

[Full architecture docs →](options_screener_trader/docs/architecture/README.md)

---

## 3. Politician Copy Trader

**Strategy:** Scrape congressional stock disclosures from [Capitol Trades](https://www.capitoltrades.com), filter for high-scoring politicians (configurable watchlist), and mirror their buys/sells to an Alpaca paper account.

**What makes it interesting:**
- **No API key required** — parses Capitol Trades' public HTML directly.
- **Configurable politician watchlist** — each politician has a `score` field (0–100) used to weight or filter trades.
- **Live dashboard** — shows recent trades, open positions, and P&L alongside the screener system's activity logs.
- **Disclosure lag handling** — politician trades are disclosed up to 45 days after execution; the bot deduplicates by transaction ID so replays are idempotent.

[Full architecture docs →](politician_copy_trader/docs/architecture.md)

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.12+ (standard library only — no runtime pip dependencies) |
| Broker API | [Alpaca Markets](https://alpaca.markets) paper trading REST API |
| AI research | Google Gemini 2.5 Flash (optional; options + screener trader) |
| Scheduling | Windows Task Scheduler + PowerShell launcher scripts |
| Dashboards | Self-hosted HTTP server (`http.server`) with auto-refreshing HTML |
| State | JSON files (no external database) |
| Architecture docs | [arc42](https://arc42.org) + ADRs (Architecture Decision Records) |

---

## Repository layout

```
├── screener_trader/          # S&P 500 mean-reversion equity system
│   ├── screener.py           # Weekly S&P 500 scan
│   ├── entry_executor.py     # Monday order placer
│   ├── monitor.py            # 15-min position monitor
│   ├── rsi_loop/             # 8-step self-improvement pipeline
│   ├── screener_dashboard_server.ps1
│   ├── run_*.bat             # Windows Task Scheduler launchers
│   └── docs/architecture/   # Full arc42 docs + ADRs
│
├── options_screener_trader/  # Cash-secured puts pipeline
│   ├── options_main.py       # Daily orchestrator
│   ├── options_loop/         # IV tracker, screener, selector, executor, monitor
│   ├── scripts/run_*.bat     # Windows Task Scheduler launchers
│   └── docs/architecture/   # Full arc42 docs + ADRs
│
├── politician_copy_trader/   # Congressional disclosure mirror
│   ├── copy_trader.ps1       # Main scrape + order script
│   ├── dashboard_server.ps1  # Live dashboard server
│   └── docs/architecture.md
│
├── .gitignore                # Credentials and generated state excluded
└── .gitattributes            # LF line endings normalised
```

---

## Getting started

### Prerequisites
- Python 3.10+
- [Alpaca paper trading account](https://app.alpaca.markets/signup) (free)
- Windows (Task Scheduler + PowerShell for scheduling; core Python scripts are cross-platform)

### Setup (Screener Trader)

```powershell
# 1. Clone
git clone https://github.com/hilarymu/algorithmic-trading.git
cd paper-trading-systems\screener_trader

# 2. Credentials
copy alpaca_config.example.json alpaca_config.json
# Edit alpaca_config.json — add your Alpaca paper API key and secret

# 3. Run the screener manually (fetches live S&P 500 data)
py -3 screener.py

# 4. Start the dashboard
powershell -ExecutionPolicy Bypass -File .\screener_dashboard_server.ps1
# Open http://localhost:8766/
```

For the full Task Scheduler setup, see [`screener_trader/docs/scheduled_tasks.md`](screener_trader/docs/scheduled_tasks.md).

### Setup (Options Trader)

```powershell
cd paper-trading-systems\options_screener_trader
copy alpaca_config.example.json alpaca_config.json
# Edit with your Alpaca keys (+ optional Gemini key)
py -3 options_main.py
```

### Setup (Politician Copy Trader)

```powershell
cd paper-trading-systems\politician_copy_trader
copy config.example.json config.json
# Edit config.json — add your Alpaca keys and adjust the politician watchlist
powershell -ExecutionPolicy Bypass -File .\copy_trader.ps1
```

---

## Architecture

Each system is documented to the [arc42](https://arc42.org) standard with Architecture Decision Records (ADRs) explaining every significant design choice.

- [Screener Trader architecture](screener_trader/docs/architecture/README.md)
- [Options Trader architecture](options_screener_trader/docs/architecture/README.md)
- [Politician Copy Trader architecture](politician_copy_trader/docs/architecture.md)

---

## License

[MIT](LICENSE) — free to use, fork, and adapt.
