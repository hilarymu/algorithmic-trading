# Scheduled Tasks Reference

## Windows Task Scheduler Tasks

### Trading-Screener
- **When:** Every Monday at 06:00 local time
- **Script:** `run_screener.bat` → `screener.py`
- **Output:** `screener_results.json`, `pending_entries.json`
- **What it does:** Scans S&P 500 for oversold setups; writes the Monday buy queue

### Trading-RSI-Loop
- **When:** Every Monday at 07:00 local time
- **Script:** `run_rsi_loop.bat` → `rsi_loop\rsi_main.py`
- **Output:** `market_regime.json`, `signal_quality.json`, `screener_config.json`, `picks_history.json`, `research_picks.json`, `improvement_report.json`
- **What it does:** Full 8-step self-improvement loop — fills returns, analyses signal quality, auto-tunes config, runs Gemini research scan, generates report

### Trading-Executor
- **When:** Every Monday at 09:15 local time
- **Script:** `run_executor.bat` → `entry_executor.py`
- **Output:** Alpaca buy orders
- **What it does:** Reads `pending_entries.json` and places market buy orders for all non-skipped picks
- **Note:** User can edit `pending_entries.json` before 09:15 to set `skip: true` on any pick

### Trading-Monitor
- **When:** Every 15 minutes, Monday–Friday 09:25–16:05 local time
- **Script:** `run_monitor.bat` → `monitor.py`
- **Output:** Updates `positions_state.json`, places/cancels Alpaca orders
- **What it does:** RSI exit check, trailing stop management, hard stop verification, ladder order integrity

### Trading-ScreenerDashboard (Startup folder)
- **When:** On Windows login (via Startup folder shortcut)
- **Script:** `run_screener_dashboard.bat` → `screener_dashboard_server.ps1`
- **What it does:** Starts local HTTP dashboard at http://localhost:8766/

---

## Manual Run Commands

```powershell
# (run from the screener_trader\ directory)

# Run screener manually
py -3 screener.py

# Run RSI loop manually (skips screener step)
py -3 rsi_loop\rsi_main.py --no-screener

# Run RSI loop with screener
py -3 rsi_loop\rsi_main.py

# Run monitor manually (check/act on all positions)
py -3 monitor.py

# Run entry executor manually
py -3 entry_executor.py

# Start screener dashboard
powershell -ExecutionPolicy Bypass -File .\screener_dashboard_server.ps1

# Fix regime labels on historical picks (one-off, already run)
py -3 rsi_loop\fix_regimes.py

# Backfill historical picks (one-off, already run)
py -3 rsi_loop\backfill.py
```

---

## Log Files

All logs written to `screener_trader\logs\`

| Pattern | Source |
|---------|--------|
| `screener_YYYYMMDD.log` | Trading-Screener task |
| `executor_YYYYMMDD.log` | Trading-Executor task |
| `monitor_YYYYMMDD.log` | Trading-Monitor task |
| `rsi_loop_YYYYMMDDHHMMSS.log` | Dashboard "Run RSI Loop" button |

---

## Monday Timeline

```
06:00  Screener runs      → finds oversold stocks, writes pending_entries.json
07:00  RSI loop runs      → tunes config, runs Gemini research, fills returns
09:15  Executor runs      → places buy orders for Monday's picks
09:25  Monitor fires      → begins 15-min monitoring cycle
09:30  Market opens       → orders fill at open price
16:00  Market closes
16:05  Last monitor run
```
