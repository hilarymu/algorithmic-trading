# Politician Copy Trader — Architecture

## Overview

Monitors congressional trading disclosures on Capitol Trades and mirrors buy/sell
actions to an Alpaca paper trading account. Follows 5 high-activity politicians
selected for trading volume, disclosure timeliness, and historical return quality.

---

## System Map

```
Every 30 min (weekdays, market hours)
  copy_trader.ps1
    ├── Fetch Capitol Trades HTML (page 1, 20 trades per politician)
    ├── Parse embedded JSON (regex on escaped JS object literals)
    ├── Compare against trades_executed.json (dedup by _txId)
    ├── For new BUY trades:
    │     get current price → place market buy ($2,000 USD)
    └── For new SELL trades:
          check Alpaca position exists → place market sell (full qty)

On login (Windows Startup folder)
  dashboard_server.ps1
    └── HTTP server at http://localhost:8765/
        Refreshes every 60s | CT cache every 5 min
```

---

## Components

### copy_trader.ps1
Main trading bot. Runs every 30 minutes via Windows Task Scheduler.

**Parse strategy:**
Capitol Trades embeds trade data as escaped JSON inside a JavaScript context.
Regex patterns match the escaped form (`\\\"issuerTicker\\\"`) rather than
standard JSON quotes. This is a deliberate choice to match the actual HTML source.

**Key fields parsed per trade:**
- `_txId` — unique trade ID, used for deduplication
- `issuerTicker` — stock symbol (`:US` suffix stripped)
- `txType` — `buy` or `sell`
- `txDate` — disclosure date
- `value` — estimated trade value in USD

**Safety checks:**
- Exits immediately if Alpaca market clock reports market closed
- Skips sells if no position held in Alpaca
- Skips buys if price fetch fails
- Deduplicates by `txId` stored in `trades_executed.json`
- Logs every action to `copy_trader.log`

**Trade size:** $2,000 USD per buy (configurable in `config.json` as `trade_amount_usd`)

---

### dashboard_server.ps1
Live HTTP dashboard at http://localhost:8765/

**Startup sequence (avoids browser spinning):**
1. Pre-warms Capitol Trades cache (fetches all 5 politicians — ~20s)
2. Starts HTTP listener
3. Opens browser

**On each request:**
- Fetches live Alpaca data: account, positions, orders, clock (10s timeout each)
- Serves Capitol Trades data from cache (refreshes every 5 min)
- Builds and returns full HTML page

**Features:**
- Portfolio summary cards (value, P&L, buying power, trades copied)
- Open positions table with unrealized P&L
- Per-politician panels with recent trades + Copied/Pending badges
- Recent Alpaca orders
- Bot activity log (filtered to key events)
- Screener/Executor/Monitor log panels (shared with RSI system)
- Auto-refresh every 60 seconds with countdown progress bar
- Stop server button

---

### config.json
Central configuration for the copy trader.

```json
{
  "politicians": [
    { "id": "G000583", "name": "Josh Gottheimer", "party": "D", "state": "NJ", "score": 85 },
    ...
  ],
  "trade_amount_usd": 2000,
  "alpaca_key": "...",
  "alpaca_secret": "...",
  "alpaca_base": "https://paper-api.alpaca.markets/v2"
}
```

Politicians are scored 0–100 on a composite of:
- Trading volume (more trades = more signal)
- Disclosure timeliness (faster = more actionable)
- Estimated historical return quality

---

## Data Files

| File | Written by | Read by | Purpose |
|------|-----------|---------|---------|
| `trades_executed.json` | copy_trader.ps1 | copy_trader.ps1, dashboard_server.ps1 | Dedup log of all txIds executed |
| `copy_trader.log` | copy_trader.ps1 | dashboard_server.ps1 | Full activity log |
| `config.json` | manual | copy_trader.ps1, dashboard_server.ps1 | Politicians list + Alpaca credentials |
| `politician_scores.json` | manual (research) | config.json reference | Scoring data used to build config |

---

## Scheduled Tasks

| Task | Schedule | Script |
|------|----------|--------|
| `PoliticianCopyTrader` | Every 30 min, Mon–Fri (market hours check internal) | `copy_trader.ps1` |
| `Trading-CopyTraderDashboard` | On login (Startup folder) | `run_copytrader_dashboard.bat` |

---

## Politicians Followed

| Politician | Party | State | Rationale |
|-----------|-------|-------|-----------|
| Josh Gottheimer | D | NJ | Highest volume (1,400+ trades, $185M+), most active |
| + 4 others | mix | various | Selected by composite score in `politician_scores.json` |

---

## Manual Run Commands

```powershell
# Run copy trader manually (dry run — set $DryRun = $true inside script first)
powershell -ExecutionPolicy Bypass -File ".\copy_trader.ps1"

# Force run during market closed (for testing)
powershell -ExecutionPolicy Bypass -File ".\copy_trader.ps1" -Force

# Start live dashboard
powershell -ExecutionPolicy Bypass -File ".\dashboard_server.ps1"

# Generate static dashboard snapshot (fallback if server is down)
powershell -ExecutionPolicy Bypass -File ".\dashboard.ps1"
```

---

## Key Design Decisions

**Why Capitol Trades over QuiverQuant or other sources?**
Capitol Trades provides free HTML access without an API key, updates within hours of
SEC disclosure, and covers all disclosure fields needed (ticker, direction, date, value).

**Why regex parsing instead of a proper JSON parser?**
The trade data is embedded as escaped JavaScript object literals, not valid standalone JSON.
A standard JSON parser would fail. Regex on the escaped patterns (`\\\"field\\\"`) is the
only reliable approach without a headless browser.

**Why $2,000 trade size?**
Large enough to track meaningful P&L; small enough that a bad trade doesn't significantly
impact the paper account. Configurable in `config.json`.

**Why paper account only?**
Congressional trades are disclosed with a 30–45 day lag under the STOCK Act.
By the time disclosures appear, the price move has often already happened.
Paper trading lets us track performance without real capital risk while validating
whether the strategy has alpha at all.

**Why market hours check inside the script?**
The Task Scheduler fires every 30 minutes regardless of market status. The script calls
Alpaca's `/clock` endpoint and exits immediately if the market is closed. This avoids
placing orders outside trading hours while keeping the scheduler simple.
