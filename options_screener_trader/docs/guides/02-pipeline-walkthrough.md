# Pipeline Walkthrough — Daily Run Step by Step

Two Task Scheduler tasks run every trading day:

| Task | Time | Script | What runs |
|---|---|---|---|
| `\Trading-Options-Preclose` | **15:30 ET** | `run_options_preclose.bat` | Steps 0–2, 4–5 (IV + screen + select + execute) |
| `\Trading-Options-Daily` | **16:30 ET** | `run_options_loop.bat` | Steps 3, 6–7 (monitor + EOD analysis) |
| `\Trading-Options-Intraday` | **09:30 ET** | `run_options_monitor_intraday.bat` | Intraday exit monitor (every 15 min) |

The executor runs at **15:30 ET** (pre-close) so orders reach the exchange while
the market is still open. The 16:30 post-close run is analysis only — no orders.

---

## At a glance

```
Step 0  iv_backfill              (first run only — bootstrap IV history)
Step 1  iv_tracker               15:30 ET — snapshot today's IV
Step 2  options_screener         15:30 ET — filter universe → candidates
Step 4  options_strategy_selector 15:30 ET — look up real contracts, BSM-price them
Step 5  options_executor         15:30 ET — place orders while market is open (~15:33 ET)
──────────────────────────────────────────────────────────────────────────────────────
Step 3  options_monitor          16:30 ET — daily close check, EOD exits
Step 6  options_signal_analyzer  16:30 ET — score candidates, analyse outcomes
Step 7  options_optimizer        16:30 ET — generate insights, adjust config (n≥50)
```

Total runtime: pre-close ~35–50 s, post-close ~10–20 s.

---

## Step 0 — IV Backfill `iv_backfill.py`

**When:** First launch only, or when `--backfill` flag is passed.
Triggered automatically if `iv_history.json` is absent or has < 30 days of readings.

**What it does:**
1. Fetches 252 trading days of daily equity bar data (OHLCV) from Alpaca for every
   symbol in the universe (SP500 + NASDAQ100, ~512 symbols).
2. Attempts to fetch options historical bars from Alpaca for IV data. On paper accounts
   this fails with 403 (OPRA agreement required).
3. Falls back to **HV30 proxy**: computes 30-day rolling realized volatility from log
   returns, then scales it to match real IV snapshot levels using a per-symbol k-factor.
4. Writes proxy IV readings into `iv_history.json` (one entry per symbol per trading day).
5. Recomputes `iv_rank_cache.json` from the full history.

**Why the proxy:** OPRA (options price data) requires a signed agreement that paper accounts
don't have. HV30 realized volatility is a directionally correct, self-correcting proxy.
As real daily IV snapshots accumulate over ~12 months, the proxy readings phase out.
See [ADR-008](../architecture/adr/008-hv30-proxy-iv-backfill.md).

**Reads:** Alpaca historical bars API
**Writes:** `iv_history.json`, `iv_rank_cache.json`

---

## Step 1 — IV Tracker `iv_tracker.py`

**When:** Every daily run (unless `--no-iv` flag passed).

**What it does:**
1. Reads the universe symbol list.
2. For each symbol, fetches today's indicative IV from Alpaca options snapshot endpoint.
3. Appends today's reading to `iv_history.json[symbol]`.
4. Rolls a 252-trading-day window and recomputes:
   - **IV rank**: `(current_iv - min_252d) / (max_252d - min_252d) * 100`
   - **IV percentile** (similar, based on rank within the 252 values)
5. Updates `iv_rank_cache.json` — this is the fast-lookup file used by the screener.

**IV Rank** is the key metric. A rank of 100 means options are at their most expensive in
a year. We want to sell when rank ≥ 40 (paid well) and buy when rank ≤ 30 (options cheap).

**Reads:** `iv_history.json`, Alpaca options snapshot API
**Writes:** `iv_history.json`, `iv_rank_cache.json`

---

## Step 2 — Options Screener `options_screener.py`

**When:** Every daily run.

**What it does:**
1. Detects the current **market regime** (bull/bear/neutral) from SPY price and moving
   averages.
2. For every symbol in the universe:
   - Fetches RSI (14-period) from recent price bars.
   - Checks volume ratio vs. 20-day average.
   - Looks up IV rank from `iv_rank_cache.json`.
   - Checks for upcoming earnings (flagged, not blocked).
3. Applies entry filters:
   - RSI < 25 (oversold)
   - Volume ≥ 1.2× average
   - IV rank ≥ 40
   - Stock price ≥ $15
   - Not already at max open positions
4. Selects the strategy type (CSP/PUT_SPREAD/CALL_SPREAD) based on regime and signal strength.
5. Writes up to `max_positions` best candidates sorted by composite signal score.
6. Appends new picks to `options_picks_history.json` (the permanent research log).

**In bear regime:** No new CSP/put-sell entries. Spread strategies only.

**Reads:** `iv_rank_cache.json`, Alpaca equity bars API, `options_config.json`
**Writes:** `options_candidates.json`, `options_picks_history.json`

---

## Step 3 — Options Monitor `options_monitor.py` (daily close check)

**When:** Every daily run, *before* new entries (close first, then open new positions).

**What it does:**
1. Reads all open positions from `positions_state.json`.
2. For each open position, fetches the current option quote from Alpaca.
3. Checks four exit conditions in priority order:
   - **Loss limit**: if current premium ≥ 2× entry premium → close immediately
   - **DTE exit**: if days to expiry ≤ 21 → close (gamma risk window)
   - **Profit target**: if current premium ≤ 50% of entry → close, take profit
   - **RSI recovery**: if underlying RSI has recovered above 50 → close
4. For positions meeting an exit condition: submits a buy-to-close limit order via Alpaca.
5. Updates `positions_state.json` (marks closed positions with exit reason and P&L).

**Reads:** `positions_state.json`, Alpaca options quotes API
**Writes:** `positions_state.json`

The **intraday monitor** (`run_options_monitor_intraday.bat`) runs the same exit checks
every 15 minutes from 09:30–16:00 ET. This catches intraday blow-ups on loss limits
instead of waiting until 16:30 ET.

---

## Step 4 — Strategy Selector `options_strategy_selector.py`

**When:** Pre-close run (15:30 ET), after screener, before executor.

**What it does:**
1. Reads `options_candidates.json` from the screener.
2. For each candidate (typically 2–5 symbols):
   - Targets an expiry in the 21–50 DTE window.
   - Uses **Black-Scholes** to estimate the strike that gives the target delta (0.30 for CSP).
   - Calls `GET /v2/options/contracts` on the **Alpaca trading API** to find real listed
     contracts near that estimated strike — guaranteeing the OCC symbol actually exists.
   - Tries to fetch a live quote from the snapshot API; uses **BSM pricing** as fallback
     when the indicative feed returns no data (common on paper accounts).
   - Picks the listed contract whose BSM delta is closest to target.
3. If valid contract found: adds to `options_pending_entries.json`.
4. Position sizing: max 7% of paper NAV per position, 1 contract.

**Contract source field:** each leg is tagged `data_source: "alpaca_live"` or
`data_source: "bsm_estimated"` so the executor and monitor know whether quotes are live.

**Reads:** `options_candidates.json`, `options_config.json`, Alpaca trading + data APIs
**Writes:** `options_pending_entries.json`

---

## Step 5 — Executor `options_executor.py`

**When:** Pre-close run (15:30 ET), immediately after the selector (~15:33 ET).
Orders are placed while the market is still open so fills can occur before 16:00 ET close.

**What it does:**
1. Reads `options_pending_entries.json`.
2. Checks global gates:
   - `auto_entry.enabled` must be true
   - Open positions < `max_positions`
   - Regime is not bear (for sell-side strategies)
3. For each pending entry that passes gates:
   - Submits a **limit order** to sell the put (or buy the spread) via Alpaca paper API.
   - Records the order in `positions_state.json` with entry time, premium, strike, expiry.
4. Updates processed entries in `options_pending_entries.json`.

**Reads:** `options_pending_entries.json`, `positions_state.json`, `options_config.json`
**Writes:** `positions_state.json` (new open positions), Alpaca orders API

**Safety:** The executor is the only module that touches the Alpaca orders endpoint.
All other modules are read-only with respect to live trading.

**Note:** The post-close run (16:30 ET) skips the executor — the options market closes
at 16:00 ET, so order placement after that point cannot result in fills.

---

## Step 6 — Signal Analyzer `options_signal_analyzer.py`

**When:** Every daily run.

**What it does:**
1. Loads all candidates from `options_candidates.json`.
2. Scores each candidate using a **composite signal strength** formula (0–90):
   - IV rank contribution (max 40 pts): `iv_rank / 100 * 40`
   - RSI extremity (max 30 pts): scales from 0 (RSI=25) to 30 (RSI=0)
   - Volume ratio (max 20 pts): `min(vol_ratio / 2.5, 1.0) * 20`
   - Earnings penalty: −10 pts if earnings within 7 days
3. For each candidate, estimates theoretical CSP premium using **Black-Scholes**:
   - Finds the 0.30-delta strike
   - Computes put price → `premium_pct` of strike → annualized yield
4. Loads closed positions from `positions_state.json` and computes outcome statistics:
   - Win rate, avg P&L, avg hold days, annualized yield
   - Exit reason breakdown (profit target / loss limit / DTE / RSI recovery)
   - Per-IV-rank-bucket performance (40–55, 55–70, 70–85, 85–100)
5. Computes IV rank distribution across the universe.
6. Labels data quality: `real_iv` if 30+ real snapshot days exist; otherwise `hv30_proxy+Nd_real`.

**Reads:** `options_candidates.json`, `iv_rank_cache.json`, `positions_state.json`
**Writes:** `options_signal_quality.json`

---

## Step 7 — Optimizer `options_optimizer.py`

**When:** Every daily run.

**What it does:**
1. Reads `options_signal_quality.json` (produced by step 6).
2. **Gate check**: if < 10 closed positions, prints bootstrapping status and exits.
3. If ≥ 10 closed positions: generates **insights** by comparing outcome stats against rules:
   - If 40–55 IV bucket win rate < 40% (with n ≥ 5) → suggest raising `iv_rank_min_sell`
   - If loss-limit exits > 30% of closes → suggest widening delta (more OTM puts)
   - If win rate > 80% over 20+ trades → suggest tightening delta (more premium)
   - If avg hold days < 10 → suggest raising profit target (letting theta decay more)
   - If avg hold days > 30 → suggest lowering profit target (exit before gamma zone)
   - If DTE exits and loss exits co-occur → suggest raising `close_at_dte`
4. If ≥ 50 closed positions AND `auto_optimize=true`: applies high-confidence insights to
   `options_config.json` (all changes respect hard parameter bounds).
5. Writes full report to `options_improvement_report.json`.
6. Carries forward all-time applied changes in the report for audit trail.

**Confidence levels:**
- `low`: n = 10–19 (suggestions only, never applied)
- `medium`: n = 20–49 (suggestions only, never applied)
- `high`: n ≥ 50 (applied automatically when `auto_optimize=true`)

**Reads:** `options_signal_quality.json`, `options_config.json`
**Writes:** `options_improvement_report.json`, `options_config.json` (when auto-applying)

---

## Intraday monitor `options_monitor.py` (intraday mode)

**Separate process**, triggered at 09:30 ET by `\Trading-Options-Intraday` Task Scheduler task.

Runs `options_main.py --intraday`, which loops `check_exits_intraday()` every 15 minutes
from market open (09:30 ET) until market close (16:00 ET), then exits automatically.

Checks the same exit conditions as the daily monitor but with live intraday prices.
Particularly important for catching **loss-limit hits** before end of day.

---

## Key files produced each run

| File | Written by | Read by | Purpose |
|---|---|---|---|
| `iv_history.json` | iv_tracker, iv_backfill | iv_tracker | 252-day rolling IV per symbol |
| `iv_rank_cache.json` | iv_tracker, iv_backfill | screener, analyzer | Fast IV rank lookup |
| `options_candidates.json` | screener | selector, analyzer | Today's screened candidates |
| `options_picks_history.json` | screener | (humans, dashboard) | Permanent research log |
| `options_pending_entries.json` | selector | executor | Contracts ready to enter |
| `positions_state.json` | executor, monitor | monitor, analyzer, optimizer | All open and closed positions |
| `options_signal_quality.json` | analyzer | optimizer | Scored candidates + outcome stats |
| `options_improvement_report.json` | optimizer | (humans, dashboard) | Insights and applied changes |

For full schema details: [reference/data-formats.md](../reference/data-formats.md).

---

## Error handling

Every step in `options_main.py` is wrapped in `try/except`. If any step fails:
- The error is logged with a full traceback.
- The pipeline continues to the next step.
- The run is not aborted.

This means a failed screener doesn't prevent the monitor from closing positions, and a
failed executor doesn't prevent the analyzer from running. Each step is independently robust.

Errors are visible in:
- Console output / `logs/` directory
- Task Scheduler "Last Run Result" column
