# Screener Trader — Runbook

Day-to-day operations guide for the screener trader system.

---

## Daily health check

Run this every Monday morning before 09:15 ET (before the executor fires).

```
1. Check logs\screener_YYYY-MM-DD.log    — did screener run at 06:00?
2. Check logs\rsi_loop_YYYY-MM-DD.log   — did the RSI loop run at 07:00?
3. Open pending_entries.json             — review picks before 09:15 cutoff
4. Open http://localhost:8766/           — dashboard (if server is running)
5. Check logs\executor_YYYY-MM-DD.log   — did orders place at 09:15?
```

---

## Scheduled task timing

| Time (ET) | Day | Task | Log file |
|-----------|-----|------|----------|
| 06:00 Mon | Weekly | screener.py | logs\screener_YYYY-MM-DD.log |
| 07:00 Mon | Weekly | rsi_loop (rsi_main.py) | logs\rsi_loop_YYYY-MM-DD.log |
| 09:15 Mon | Weekly | entry_executor.py | logs\executor_YYYY-MM-DD.log |
| 09:25–16:05 | Mon–Fri | monitor.py (every 15 min) | logs\monitor_YYYY-MM-DD.log |
| On login | — | screener_dashboard_server.ps1 | (console output only) |

---

## Manual triggers

All bat files are at the project root. Run them from PowerShell or a command prompt.

```bat
# Run screener (generates screener_results.json + pending_entries.json)
run_screener.bat

# Run executor (places orders for pending_entries.json — market hours only)
run_executor.bat

# Run monitor (checks exit conditions — weekday + market hours guard built in)
run_monitor.bat

# Run RSI self-improvement loop (updates screener_config.json)
run_rsi_loop.bat

# Start live dashboard (http://localhost:8766/)
run_screener_dashboard.bat
```

Or run Python directly:

```
py -3 screener.py
py -3 entry_executor.py
py -3 monitor.py
py -3 rsi_loop\rsi_main.py
```

---

## Skipping a pending trade

After the screener runs at 06:00, you have until 09:15 to review picks.

Open `pending_entries.json` and set `"skip": true` on any entry you want to exclude:

```json
{
  "entries": [
    {
      "rank": 1,
      "symbol": "AAPL",
      "screened_price": 172.50,
      "planned_shares": 5,
      "skip": false
    },
    {
      "rank": 2,
      "symbol": "XYZ",
      "screened_price": 45.00,
      "planned_shares": 11,
      "skip": true
    }
  ]
}
```

The executor reads `skip` at execution time and logs the skipped entry. The position is never opened.

---

## Pausing the system

**Pause for one week (skip screener + executor):**

Open Task Scheduler → find `Trading-Screener` and `Trading-Executor` → right-click → Disable.
Re-enable before next Monday.

**Pause monitor only (keep positions, stop exits):**

Disable `Trading-Monitor` in Task Scheduler.
Be aware: open positions will not have exit conditions checked while paused.

**Cancel all open positions manually:**

Log in to your Alpaca paper account at https://app.alpaca.markets and liquidate from the UI.
Then clear `positions_state.json` to reset the tracker:

```json
{
  "strategy_defaults": {},
  "positions": {}
}
```

---

## Dashboard

Start the dashboard server:

```bat
run_screener_dashboard.bat
```

Then open: **http://localhost:8766/**

The dashboard auto-refreshes every 60 seconds. It reads live JSON files on every request.

### Dashboard sections

| Section | Data source | What to look for |
|---------|-------------|------------------|
| Market Regime | market_regime.json | Regime label: bull / mild_correction / correction / recovery |
| Top Picks | screener_results.json | Latest screener output — composite scores |
| Radar | screener_results.json | Stocks approaching oversold thresholds |
| Open Positions | positions_state.json | Current entries, stop levels, add-down ladder status |
| Picks History | picks_history.json | Forward returns at 1d / 5d / 10d / 20d |
| Signal Quality | signal_quality.json | Hit rate and avg return by regime |
| Config Evolution | config_history.json | History of auto-tuned parameter changes |
| Improvement Report | improvement_report.json | Latest Gemini analysis narrative |

**Port conflict:** If port 8766 is in use, edit `screener_dashboard_server.ps1` line 1:
```powershell
$Port = 8766   # change to 8768 or similar
```

---

## Common issues

### Screener finds 0 picks

**Cause:** Filters are too strict for current market conditions.

**Check:**
- `screener_config.json` — `rsi_oversold` setting (default 20). In a bull market, few stocks reach RSI < 20.
- `market_regime.json` — if regime is `bull`, oversold conditions are rare; this is expected.
- Review `screener_results.json` — `radar` section shows stocks that nearly qualified.

**Fix:**
- Temporarily raise `rsi_oversold` to 25 or 30 in `screener_config.json`.
- Or wait — the RSI loop will auto-tune parameters based on historical performance.

---

### Monitor not running / missed exit

**Symptom:** No `monitor_YYYY-MM-DD.log` for today, or log is from early morning only.

**Check:**
- Task Scheduler → `Trading-Monitor` — is it enabled? Last run time?
- The monitor has a weekday + market-hours guard. It exits if run outside 09:25–16:05 ET Mon–Fri.

**Fix:**
- Run `run_monitor.bat` manually during market hours to trigger an immediate check.
- If a position needs urgent exit, liquidate via the Alpaca UI directly.

---

### Executor placed 0 orders

**Symptom:** `executor_YYYY-MM-DD.log` shows "0 entries executed".

**Check:**
- Was `pending_entries.json` populated? (screener must run first)
- Is `auto_entry` set to `true` in `screener_config.json`?
- Were all entries marked `"skip": true`?
- Did the executor run during market hours (09:15–16:00 ET)?

---

### RSI loop fails or produces no changes

**Symptom:** `rsi_loop_YYYY-MM-DD.log` shows errors or "no changes applied".

**Check:**
- `picks_history.json` — must have picks with forward returns filled in.
- Forward returns require at least 1 week of picks history. On first launch, the loop runs but has no data.
- Gemini API key (optional) — if missing, the report falls back to a rule-based narrative.

---

### Dashboard shows stale data

The dashboard reads JSON files on every HTTP request. If data looks stale:
- Check if the scheduled tasks ran (see log files).
- The screener only runs Monday 06:00 — data is weekly by design.
- Force a fresh screener run: `run_screener.bat`.

---

### Dashboard port 8766 already in use

```
Error: address already in use
```

Find and kill the existing process:

```powershell
netstat -ano | findstr :8766
# Find the PID in the last column, then:
taskkill /PID <pid> /F
```

Then restart: `run_screener_dashboard.bat`.

---

## Reviewing performance

**Per-pick returns:**

Open `picks_history.json` — each pick has `returns` at 1d, 5d, 10d, 20d horizons (filled in by the RSI loop's picks tracker step).

**Signal quality summary:**

Open `signal_quality.json` — hit rate and average return broken down by market regime.

**RSI loop changes:**

Open `config_history.json` — each entry is one optimization run with before/after parameter values.

---

## Emergency stop — close all positions

1. Log in to Alpaca paper account → Positions → Liquidate All.
2. Clear `positions_state.json`:
   ```json
   { "strategy_defaults": {}, "positions": {} }
   ```
3. Disable Task Scheduler tasks: `Trading-Monitor`, `Trading-Executor`, `Trading-Screener`.

---

## First-run checklist

- [ ] `alpaca_config.json` created with paper API key (flat structure — see README)
- [ ] `screener_config.json` present (defaults are ready to use)
- [ ] `py -3 screener.py` runs without errors
- [ ] `screener_results.json` populated with picks
- [ ] Task Scheduler tasks created (see [docs/scheduled_tasks.md](scheduled_tasks.md))
- [ ] Dashboard starts: `run_screener_dashboard.bat` → http://localhost:8766/
- [ ] (Optional) Gemini API key set in environment for improvement reports
