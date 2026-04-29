# Getting Started — Setup from Scratch

This guide takes you from a blank Windows machine to a running daily options pipeline.
Estimated time: 20–30 minutes.

---

## Prerequisites

- **Windows 10/11** (pipeline uses Windows Task Scheduler and batch files)
- **Python 3.11+** — tested on 3.14. Download from [python.org](https://python.org).
- **Alpaca paper trading account** — sign up free at [alpaca.markets](https://alpaca.markets).
  Enable options trading in your paper account settings.
- **Git** (optional, for cloning)

---

## 1. Get the code

```
git clone <repo-url> C:\Users\<you>\Documents\Trading\Claude\options_screener_trader
```
or download and unzip to that path.

---

## 2. Install dependencies

Open a command prompt in the project root:

```
cd C:\Users\<you>\Documents\Trading\Claude\options_screener_trader
py -3 -m pip install -r requirements.txt
```

Key dependencies: `requests`, `alpaca-trade-api` (or `alpaca-py`), `numpy`, `scipy`.

---

## 3. Configure Alpaca credentials

Copy the example file and fill in your credentials:

```
copy alpaca_config.example.json alpaca_config.json
```

Then edit `alpaca_config.json`:

```json
{
  "api_key":    "PK...",
  "api_secret": "...",
  "base_url":   "https://paper-api.alpaca.markets/v2",
  "account_type": "paper"
}
```

Find your paper API key and secret in the Alpaca dashboard → API Keys.

> **Structure note:** The file uses a **flat** top-level structure (not nested under `"paper"`). The modules read `config["api_key"]` directly.

> **Security note:** `alpaca_config.json` is listed in `.gitignore`. Never commit credentials.

---

## 4. Review `options_config.json`

The config file at the project root controls all strategy parameters. The defaults are
sensible starting values. The main decision for a new setup:

**Start with `auto_entry.enabled: false`** until you have verified the pipeline is running
correctly and you understand the candidates it generates:

```json
"auto_entry": {
  "enabled": false,
  "_note": "Set to true when ready to place paper orders"
}
```

Full config documentation: [reference/config-schema.md](../reference/config-schema.md).

---

## 5. Run the first time (manual)

From the project root:

```
py -3 options_main.py
```

**First-run behaviour:**
- Detects that `iv_history.json` is absent → triggers the IV backfill.
- Backfill takes 2–5 minutes: fetches 252 days of equity bar data for ~512 symbols.
- Bootstraps IV history using the HV30 realized-volatility proxy (OPRA unavailable on paper).
- After backfill, continues with the full 7-step pipeline.

**What you'll see:**
```
[timestamp] options_main starting
[timestamp] Phase: 3 (full pipeline ...)
[timestamp] First run detected — bootstrapping IV history from Alpaca historical data
[timestamp] Backfill done: 79,251 new readings, 512 symbols with IV rank
[timestamp] Running iv_tracker...
[timestamp] Running options_screener (research mode)...
[timestamp]   screener done: 5 candidates, regime=bull, 5 new picks logged
[timestamp] Running options_monitor (daily close check)...
[timestamp]   monitor done: 0 checked, 0 closed
...
[timestamp] options_main done in 42.1s
```

Check `data/options_candidates.json` to see the screened candidates.

---

## 6. Schedule the daily run (Windows Task Scheduler)

### Option A — Use the provided batch file
Open Task Scheduler → Create Task:
- **Name:** `\Trading-Options-Daily`
- **Trigger:** Daily, 4:30 PM, repeat Mon–Fri
- **Action:** `C:\...\options_screener_trader\scripts\run_options_loop.bat`
- **Start in:** `C:\...\options_screener_trader\scripts`

### Option B — PowerShell (faster)
```powershell
$proj    = "C:\...\options_screener_trader"
$action  = New-ScheduledTaskAction -Execute "$proj\scripts\run_options_loop.bat" `
           -WorkingDirectory "$proj\scripts"
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Mon,Tue,Wed,Thu,Fri `
           -At "4:30PM"
Register-ScheduledTask -TaskName "\Trading-Options-Daily" `
    -Action $action -Trigger $trigger -RunLevel Highest
```

> **Note:** The bat files live in `scripts/` and use `pushd "%~dp0.."` to resolve the
> project root automatically — the working directory does not need to be set to the
> project root manually.

### Intraday monitor
Create a second task `\Trading-Options-Intraday` using
`scripts\run_options_monitor_intraday.bat`, triggered at **9:30 AM Mon–Fri**.

---

## 7. Enable live paper orders

Once you've verified the pipeline runs correctly for a few days (candidates look reasonable,
no errors in logs):

Edit `options_config.json`:
```json
"auto_entry": {
  "enabled": true
}
```

The executor will now place paper orders automatically during the daily run.

---

## 8. Verify it's working

After each daily run, check:

1. **Candidates file exists and looks reasonable:**
   ```
   cat data\options_candidates.json
   ```
   Should show 0–8 entries with RSI < 25 and IV rank ≥ 40.

2. **IV rank cache is populated:**
   ```
   py -3 -c "import json; d=json.load(open('data/iv_rank_cache.json')); print(len(d), 'symbols'); ranks=[v['iv_rank'] for v in d.values() if v.get('iv_rank')]; print('median rank:', sorted(ranks)[len(ranks)//2])"
   ```
   Expect ~512 symbols, median rank ~50.

3. **No errors in recent log output:**
   Check the Task Scheduler "Last Run Result" — should be `0x0` (success).

4. **Daily health check:**
   See [runbooks/daily-health-check.md](../runbooks/daily-health-check.md) for the full
   checklist.

---

## 9. Running tests

```
py -3 -m pytest tests/ -v
```

All 261 tests should pass. Tests cover IV tracking, screener logic, strategy selection,
monitor/exit logic, signal scoring, BSM math, position analysis, optimizer rules, and
config round-trips. No Alpaca API calls are made during tests.

---

## Troubleshooting

Common setup issues and fixes: [runbooks/troubleshooting.md](../runbooks/troubleshooting.md).
