# Troubleshooting — Known Errors and Fixes

---

## OPRA 403 on options historical bars

**Symptom:**
```
{"message": "OPRA agreement is not signed"}
```
Appears during `iv_backfill` when trying to fetch historical options bars.

**Cause:** Paper trading accounts do not have OPRA data access. This is an Alpaca
limitation, not a configuration error.

**Fix:** The pipeline handles this automatically — `iv_backfill.py` catches the 403 and
falls back to HV30 realized-volatility proxy (computed from equity bars, which do work on
paper accounts). No action required.

**Long-term resolution:** Upgrading to a live Alpaca account and signing the OPRA agreement
will unlock real options bars. The backfill code will use them automatically.

---

## "No valid expirations in DTE window"

**Symptom:**
```
[screener] TSCO — No valid expirations in DTE window [21, 50]
```
All candidates return this error and the pending entries file is empty.

**Cause:** Calendar gap — no 3rd-Friday monthly expiry falls between 21 and 50 DTE from
today. This happens most often in late April (May monthly is DTE ~20, June is DTE ~55).

**Fix:** Already handled automatically. The `_target_expirations()` function in
`iv_tracker.py` extends the DTE window by +14 days if the strict [21, 50] window is empty.
June expiry (DTE ~55) is used as the fallback.

If still failing: check today's date against the 3rd-Friday calendar manually. The system
should handle any calendar gap up to about 65 DTE.

---

## Dashboard showing N/A / -- for all metrics

**Symptom:** The screener dashboard shows `--` for IV rank, `N/A` for candidates, etc.
All data appears missing even though JSON files exist on disk.

**Cause:** The PowerShell dashboard server process was started with a stale `$ProjectDir`
path (from a previous session). The server holds this path in memory and all `Test-Path`
checks fail because it's looking in the wrong directory.

**Fix:**
1. Stop the old server: `curl http://localhost:8765/stop` (or kill the process in Task Manager)
2. Restart: double-click `run_screener_dashboard.bat`

**How to verify:** After restart, check the terminal — the server logs should show the
correct project directory path.

---

## `schtasks` quoting error when creating Task Scheduler task

**Symptom:**
```
ERROR: Invalid argument/option - 'C:\...\bat\'.
```
When trying to create a scheduled task via `schtasks /create` with a path containing spaces.

**Fix:** Use PowerShell cmdlets instead of `schtasks`:
```powershell
$action  = New-ScheduledTaskAction -Execute "C:\path\to\script.bat" `
           -WorkingDirectory "C:\path\to\project"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Mon,Tue,Wed,Thu,Fri -At "4:30PM"
Register-ScheduledTask -TaskName "\Trading-Options-Daily" `
    -Action $action -Trigger $trigger -RunLevel Highest
```

---

## `iv_history.json` structure error

**Symptom:**
```
TypeError: 'dict' object is not subscriptable
```
or code trying to do `entries[-1]` on a dict.

**Cause:** `iv_history.json` stores `{symbol: {date: float}}`, NOT a list of `{date, iv}` objects.

**Fix (inspection script):**
```python
import json
hist = json.load(open('iv_history.json'))
sym = list(hist.keys())[0]
# Correct access:
dates = sorted(hist[sym].keys())
latest_date = dates[-1]
latest_iv = hist[sym][latest_date]
```

---

## Data quality label shows "real_iv" incorrectly

**Symptom:** `options_signal_quality.json` shows `data_quality: "real_iv"` but the backfill
was just run today (no real snapshot days should qualify yet).

**Cause (historical):** An early version of the signal analyzer used `len(hist[sym])` (total
entries including proxy) instead of counting only entries within the last 30 calendar days.

**Fix:** Already fixed. The current analyzer counts only dates `>= today - 30 days` as real.
The label will correctly show `hv30_proxy+Nd_real` until 30 real snapshot days accumulate.

---

## Tests fail with `No module named pytest`

**Symptom:**
```
C:\...\python.exe: No module named pytest
```

**Fix:**
```
py -3 -m pip install pytest
```
Then run: `py -3 -m pytest tests/ -v`

---

## Executor submits no orders despite `auto_entry: true`

**Symptom:** Executor logs `executed: 0, skipped: 0` even though there are candidates.

**Causes to check (in order):**
1. `options_pending_entries.json` is empty — selector found no valid contracts. Check
   `options_candidates.json` to see if the screener produced candidates, and check selector
   logs for "No valid expirations" or "open interest too low" messages.
2. `max_positions` reached — all 8 slots are occupied. Check `positions_state.json`.
3. Bear regime — executor skips sell-side entries in bear regime. Check `options_candidates.json`
   for `regime` field.
4. Alpaca API error in selector — if the options snapshot returned no results for the target
   strike, the entry is skipped silently. Check for Python exceptions in the daily log.

---

## `positions_state.json` corrupted or missing

**Symptom:** Monitor or executor errors referencing `positions_state.json`.

**Severity:** HIGH — this is the live trading ledger.

**Recovery:**
- If you have a backup: restore it.
- If no backup: reconstruct from Alpaca's account positions API (`GET /v2/positions`)
  and order history (`GET /v2/orders?status=filled`).
- If starting fresh: create an empty file `{"open": [], "closed": []}`.

**Prevention:** Back up `positions_state.json` daily. Consider adding a pre-run backup step
to `run_options_loop.bat`.

---

## Task Scheduler task "Last Run Result" shows error

**Common error codes:**
| Code | Meaning | Fix |
|---|---|---|
| `0x0` | Success | Nothing to fix |
| `0x1` | Python/script error | Check the `.bat` file and Python logs |
| `0x41301` | Task still running | Previous run still active (slow network?) |
| `0xC0000005` | Access violation | Python path or working directory wrong |
| `0x2` | File not found | `.bat` file path wrong in the task action |

**How to debug:** Open Task Scheduler → find the task → right-click → "Open Last Run" or
check the History tab for details. Run the `.bat` file manually first to verify it works.
