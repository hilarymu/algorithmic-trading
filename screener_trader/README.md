# Screener Trader

A self-optimizing RSI mean-reversion equity screener and paper trader for S&P 500 stocks.

**Core edge:** Stocks that are deeply oversold (RSI < threshold), at or below their lower Bollinger Band, and showing abnormal volume tend to bounce. The system screens for these conditions weekly, places paper orders automatically, and uses a self-improving loop to tune its own parameters from historical pick performance.

---

## How it works

```
Monday 06:00 ET
  screener.py       → scan S&P 500, score candidates, write pending_entries.json

Monday 09:15 ET  (3.25h review window — edit pending_entries.json to skip any pick)
  entry_executor.py → place market orders for pending entries via Alpaca

Mon–Fri 09:25–16:05 ET  (every 15 min)
  monitor.py        → check RSI recovery exits, trailing stops, hard stops, add-down ladder

Monday 07:00 ET
  rsi_loop/         → 8-step self-improvement: regime detect → signal quality → optimizer
                      → updates screener_config.json for the coming week

On login
  screener_dashboard_server.ps1  →  http://localhost:8766/  (live dashboard, auto-refresh 60s)
```

---

## Quick start

```
# 1. Clone
git clone <repo-url>
cd screener_trader

# 2. Configure credentials
copy alpaca_config.example.json alpaca_config.json
# Edit alpaca_config.json with your Alpaca paper API key (flat structure — see below)

# 3. Run the screener manually
py -3 screener.py

# 4. Start the live dashboard
scripts\run_screener_dashboard.bat   # opens http://localhost:8766/
```

### alpaca_config.json structure

```json
{
  "api_key":      "PK...",
  "api_secret":   "...",
  "base_url":     "https://paper-api.alpaca.markets/v2",
  "account_type": "paper"
}
```

---

## Project structure

```
screener_trader/
├── screener.py                  Weekly screener — 4-filter scan of S&P 500
├── entry_executor.py            Places market orders from pending_entries.json
├── monitor.py                   Intraday exit manager (RSI, stops, ladder)
├── screener_config.json         Strategy parameters (tuned weekly by RSI loop)
├── alpaca_config.json           Credentials (never commit)
│
├── rsi_loop/                    Self-improvement pipeline (8 steps)
│   ├── rsi_main.py              Orchestrator — runs all 8 steps in sequence
│   ├── step1_regime.py          SPY vs 200MA + VIXY → market regime label
│   ├── step2_signal_quality.py  Analyse historical pick returns by regime
│   ├── step3_optimizer.py       Derive new config parameters from data
│   ├── step4_apply_config.py    Write updated parameters to screener_config.json
│   ├── step5_research.py        Oversold candidate scan (research layer)
│   ├── step6_picks_tracker.py   Fetch forward returns for tracked picks
│   ├── step7_report.py          Generate improvement_report.json via Gemini API
│   └── step8_log.py             Append optimization run to config_history.json
│
├── run_screener.bat             Manual screener trigger
├── run_executor.bat             Manual executor trigger
├── run_monitor.bat              Manual monitor trigger (weekday + market-hours guard)
├── run_rsi_loop.bat             Manual RSI loop trigger
├── run_screener_dashboard.bat   Start live dashboard server
├── screener_dashboard_server.ps1  PowerShell HTTP server (port 8766)
│
├── screener_results.json        Latest screener output (top picks + radar)
├── pending_entries.json         Orders queued for executor (edit here to skip a pick)
├── positions_state.json         Live position tracker (entries, stops, ladder)
├── picks_history.json           All tracked picks with forward returns
├── market_regime.json           Latest regime detection
├── signal_quality.json          Historical pick performance by regime
├── improvement_report.json      Latest RSI loop analysis report
├── config_history.json          Audit log of all config changes
│
├── logs/                        Daily log files (screener, monitor, rsi_loop, executor)
└── docs/                        Full documentation
```

---

## Strategy summary

| Filter | Condition | Configurable |
|--------|-----------|--------------|
| RSI | RSI(14) < `rsi_oversold` (default 20) | Yes — tuned by optimizer |
| Bollinger Band | Price at or below lower BB(20, 2σ) | Yes |
| 200-day MA | Optional trend filter | Toggle via `require_above_200ma` |
| Volume | `vol_ratio` > `volume_ratio_min` (default 1.0×) | Yes — tuned by optimizer |

Picks are scored by composite: `rsi_weight × RSI_score + bb_distance_weight × BB_score + volume_weight × vol_score`. Lower composite score = stronger signal.

Exit rules (per position):
- **RSI recovery**: sell when RSI crosses back above `rsi_exit_threshold` (50)
- **Hard stop**: exit at −10% from entry
- **Trailing stop**: activates at +10%, floors at −5% of entry
- **Add-down ladder**: automatically adds shares at −15%, −25%, −35%, −47% levels

---

## Self-improvement loop

```
Historical picks → signal_quality.py → hit rate / avg return by regime
                                              ↓
                              optimizer derives new RSI threshold,
                              volume filter, scoring weights
                                              ↓
                              screener_config.json updated
                              config_history.json appended
                                              ↓
                              improvement_report.json (Gemini analysis)
```

The loop runs every Monday at 07:00 ET. Config changes are logged to `config_history.json` and visible in the dashboard → Config Evolution panel.

---

## Requirements

- **Python 3.11+** — no third-party runtime dependencies
- **Alpaca paper trading account** — [alpaca.markets](https://alpaca.markets), options not required
- **Windows** — Task Scheduler automation uses `.bat` files and PowerShell
- **Gemini API key** (optional) — for the `improvement_report.json` narrative; falls back to a rule-based report if unavailable
- `pytest` for running tests (dev only): `py -3 -m pip install pytest`

---

## Documentation

| I want to… | Go to |
|---|---|
| Understand the system architecture | [docs/architecture.md](docs/architecture.md) |
| Understand the strategy and signals | [docs/strategy.md](docs/strategy.md) |
| Set up Task Scheduler automation | [docs/scheduled_tasks.md](docs/scheduled_tasks.md) |
| Operate the system day-to-day | [docs/runbook.md](docs/runbook.md) |
| Understand a data file's structure | [docs/data-schemas.md](docs/data-schemas.md) |
