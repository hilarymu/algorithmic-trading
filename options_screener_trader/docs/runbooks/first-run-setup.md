# First-Run Setup — Complete Walkthrough

This runbook covers everything needed to go from zero to a running pipeline on a new machine.
Covers prerequisites, configuration, first run, Task Scheduler setup, and verification.

For the short version see [guides/03-getting-started.md](../guides/03-getting-started.md).
This runbook is more detailed and covers edge cases.

---

## Prerequisites

### Software
- **Windows 10/11** with Task Scheduler enabled
- **Python 3.11+** — verify with `py -3 --version`
- **pip** — included with Python; verify with `py -3 -m pip --version`
- **Git** (optional) — for cloning; alternatively download ZIP

### Accounts
- **Alpaca paper trading account** — [alpaca.markets](https://alpaca.markets)
  - Must have **options trading enabled** in paper account settings
  - Go to: Dashboard → Paper Trading → Account Settings → Enable Options Trading
  - Generate a Paper API key: Dashboard → API Keys → Generate New Key (Paper)

---

## Step 1: Get the code

**Option A — git clone:**
```
git clone <repo-url> "C:\Users\<you>\Documents\Trading\Claude\options_screener_trader"
```

**Option B — download ZIP:**
Extract to `C:\Users\<you>\Documents\Trading\Claude\options_screener_trader\`

The project folder name and path matter if you use the Task Scheduler bat files as-is.
You can use any path but will need to update the `.bat` files accordingly.

---

## Step 2: Install Python dependencies

```
cd "C:\Users\<you>\Documents\Trading\Claude\options_screener_trader"
py -3 -m pip install -r requirements.txt
```

If `requirements.txt` doesn't exist, the minimum packages are:
```
py -3 -m pip install requests
```
(Most other standard library. Check `import` statements if something is missing.)

---

## Step 3: Configure Alpaca credentials

Create `alpaca_config.json` in the **project root** (same level as `options_main.py`):

```json
{
  "paper": {
    "api_key":    "PKXXXXXXXXXXXXXXXX",
    "api_secret": "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
    "base_url":   "https://paper-api.alpaca.markets",
    "data_url":   "https://data.alpaca.markets"
  }
}
```

**Verify credentials work:**
```
py -3 -c "
import json, requests
cfg = json.load(open('alpaca_config.json'))['paper']
r = requests.get(cfg['base_url'] + '/v2/account',
    headers={'APCA-API-KEY-ID': cfg['api_key'], 'APCA-API-SECRET-KEY': cfg['api_secret']})
print(r.status_code, r.json().get('status', r.text[:100]))
"
```
Expected: `200 ACTIVE`

---

## Step 4: Review and configure `options_config.json`

The default `options_config.json` is ready to use. Two decisions to make:

### Decision 1: `auto_entry.enabled`
For a first setup, **start with false**:
```json
"auto_entry": {
  "enabled": false
}
```
This lets the pipeline run completely (screener, selector, analyzer, optimizer) without
placing any paper orders. Run this way for 1–2 days to verify candidates look reasonable,
then enable.

### Decision 2: `auto_optimize` (future)
Not yet in the default config. Leave it absent — the optimizer defaults to suggestions-only.
Add it later when you have 50+ closed positions:
```json
"auto_optimize": {
  "enabled": false
}
```

See [reference/config-schema.md](../reference/config-schema.md) for all config options.

---

## Step 5: First manual run

```
cd "C:\Users\<you>\Documents\Trading\Claude\options_screener_trader"
py -3 options_main.py
```

### What will happen (first run):

1. **Backfill triggers** — `iv_history.json` doesn't exist.
   - Fetches 252 days of equity bars for ~512 symbols from Alpaca.
   - Tries OPRA options bars → 403 (expected on paper).
   - Falls back to HV30 proxy.
   - Takes 2–10 minutes depending on network speed.
   - Writes `iv_history.json` (~79k entries) and `iv_rank_cache.json`.

2. **IV tracker** — appends today's snapshot (may be same as last backfill date, that's fine).

3. **Screener** — produces 0–10 candidates depending on market conditions.

4. **Monitor** — checks 0 open positions, does nothing.

5. **Selector** — finds contracts for each candidate.

6. **Executor** — skips all entries because `auto_entry: false`.

7. **Analyzer** — scores candidates, reports 0 closed positions (bootstrapping state).

8. **Optimizer** — reports bootstrapping state. Writes `options_improvement_report.json`.

### Expected final output:
```
[timestamp] options_main done in 42.1s
```

### If the backfill is slow or times out:
The backfill can be re-run standalone:
```
py -3 options_loop/iv_backfill.py
```
Or force a full fresh backfill on next main run:
```
py -3 options_main.py --backfill
```

---

## Step 6: Verify the setup

### Check 1: IV cache populated
```
py -3 -c "
import json
d = json.load(open('iv_rank_cache.json'))
ranks = [v['iv_rank'] for v in d.values() if v.get('iv_rank')]
print(f'{len(d)} symbols, {len(ranks)} with rank, median: {sorted(ranks)[len(ranks)//2]:.0f}')
"
```
Expected: `512 symbols, 512 with rank, median: ~50`

### Check 2: Candidates generated
```
py -3 -c "
import json
d = json.load(open('options_candidates.json'))
print(f'Regime: {d[\"regime\"]}, Candidates: {len(d[\"candidates\"])}')
for c in d['candidates']:
    print(f'  {c[\"symbol\"]}: RSI={c[\"rsi\"]:.1f} IV%={c[\"iv_rank\"]:.0f} score={c[\"signal_score\"]:.1f}')
"
```

### Check 3: Run all tests
```
py -3 -m pytest tests/ -v
```
Expected: `71 passed`

---

## Step 7: Schedule daily run (Task Scheduler)

### Create `\Trading-Options-Daily`

Open **PowerShell as Administrator**:

```powershell
$projectPath = "C:\Users\<you>\Documents\Trading\Claude\options_screener_trader"
$batPath     = "$projectPath\run_options_loop.bat"

$action  = New-ScheduledTaskAction -Execute $batPath -WorkingDirectory $projectPath
$trigger = New-ScheduledTaskTrigger -Weekly `
           -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
           -At "4:30PM"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask -TaskName "\Trading-Options-Daily" `
    -Action $action -Trigger $trigger -Settings $settings `
    -RunLevel Highest -Force
```

### Create `\Trading-Options-Intraday`

```powershell
$action  = New-ScheduledTaskAction `
           -Execute "$projectPath\run_options_monitor_intraday.bat" `
           -WorkingDirectory $projectPath
$trigger = New-ScheduledTaskTrigger -Weekly `
           -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
           -At "9:30AM"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 8)

Register-ScheduledTask -TaskName "\Trading-Options-Intraday" `
    -Action $action -Trigger $trigger -Settings $settings `
    -RunLevel Highest -Force
```

### Verify tasks exist:
```powershell
Get-ScheduledTask -TaskPath "\" | Where-Object { $_.TaskName -like "*Trading*" }
```

---

## Step 8: Enable paper orders (day 2+)

After verifying 1–2 days of candidates look reasonable:

Edit `options_config.json`:
```json
"auto_entry": {
  "enabled": true
}
```

The next 16:30 ET run will place paper orders for new candidates.

---

## Step 9: First paper order verification

After the first run with `auto_entry: true`:

1. Check `positions_state.json` → `"open"` array should have new entries.
2. Verify on Alpaca dashboard: Paper Trading → Orders → should show filled limit orders.
3. The monitor (both daily and intraday) will now actively watch these positions.

---

## Ongoing maintenance

- **Daily:** Run the [daily health check](daily-health-check.md) (2 min).
- **Weekly:** Review picks history, open positions, optimizer insights.
- **After 50 closed positions:** Consider enabling `auto_optimize: true` to let the
  optimizer self-tune config parameters.
