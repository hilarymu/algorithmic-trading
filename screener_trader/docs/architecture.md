# RSI Mean-Reversion System — Architecture

## Overview

An automated trading system that finds S&P 500 stocks in short-term oversold conditions
and mean-reverts them for quick bounces. The system screens weekly, enters positions
automatically at market open, monitors them intraday, and exits when RSI recovers to 50.

---

## System Map

```
Every Monday
  06:00  screener.py          ──> screener_results.json
                                   pending_entries.json
  07:00  rsi_main.py          ──> market_regime.json
                                   signal_quality.json
                                   screener_config.json (auto-tuned)
                                   research_picks.json
                                   picks_history.json
                                   improvement_report.json
  09:15  entry_executor.py    ──> places buy orders on Alpaca

Every 15 min (market hours)
  monitor.py                  ──> manages stops, ladders, RSI exits
                                   positions_state.json
```

---

## Components

### screener.py
Scans the full S&P 500 universe (500 symbols) for oversold mean-reversion setups.

**How it works:**
- Fetches 220 days of daily bars in batches of 30 symbols per API call (~17 calls total)
- Computes for each symbol: RSI(14), Bollinger Band position, 200-day MA distance, volume ratio
- Applies 4 filters (configurable in `screener_config.json`):
  1. RSI < threshold (default 20)
  2. Price below lower Bollinger Band
  3. Price above 200-day MA (optional)
  4. Volume > 2× 20-day average
- Stocks passing all 4 filters → `screener_results.json` (top picks)
- Stocks passing 2–3 filters → radar section (watching)
- Writes `pending_entries.json` with planned buy sizes for executor

**Key design:** Uses Alpaca's multi-symbol batch endpoint — one API call per 30 symbols
instead of one call per symbol. ~20× faster than the original implementation.

---

### entry_executor.py
Executes the Monday morning buys from `pending_entries.json`.

**How it works:**
- Runs at 09:15 ET (15 min before market open, so orders queue)
- Reads `pending_entries.json` — status must be "pending"
- Skips any symbol already held in Alpaca or `positions_state.json`
- Respects the `skip` flag (user can edit `pending_entries.json` before 09:15 to veto picks)
- Places market buy orders; marks file status as "executed"

---

### monitor.py
Manages all open positions every 15 minutes during market hours (09:25–16:05).

**Checks per position (in order):**

**0. RSI Exit** — mean reversion complete?
- Fetches last 50 daily bars, computes RSI(14)
- If RSI ≥ 50 (configurable per position): cancels stop, cancels all ladder orders, places market sell
- This is the primary exit signal — the strategy is complete when RSI recovers

**1. High Water Mark** — tracks peak price for trailing stop calculation

**2. Trailing Stop** — activates once price is +10% above entry
- Floor = high water mark × 0.95 (5% below peak)
- Floor only moves up, never down
- Cancel old stop → wait 0.5s → place new stop (prevents race condition / 403 errors)

**3. Hard Stop** — before trailing activates, a fixed stop at entry × 0.90 (-10%)
- Re-placed automatically if cancelled or filled

**4. Ladder Orders** — 4 limit buy orders below entry price
- Rung 1: −15% × 1.5× initial shares
- Rung 2: −25% × 2.5× initial shares
- Rung 3: −35% × 3.5× initial shares
- Rung 4: −45% × 2.0× initial shares
- Purpose: buy more on deeper dips, lowering average cost for a larger bounce

State persisted in `positions_state.json`. New Alpaca positions auto-initialised.

---

### rsi_main.py (RSI Self-Improvement Loop)
Runs weekly after the screener. 8-step pipeline:

1. **Regime Detection** → `market_regime.json`
   SPY + VIXY metrics classify market into: bull / mild_correction / correction / recovery / geopolitical_shock / bear

2. **Fill Missing Returns** → updates `picks_history.json`
   Fetches current prices, computes 1d/5d/10d/20d forward returns for all tracked picks

3. **Signal Quality Analysis** → `signal_quality.json`
   Breaks down hit rates and average returns by: regime, RSI bucket, volume bucket, 200MA position

4. **Optimizer** → updates `screener_config.json`
   - < 10 samples: uses regime defaults
   - ≥ 10 samples: data-driven — finds the RSI/volume/MA thresholds that maximised 5d returns
   - Currently: 1,332 samples, data-driven mode active

5. **Research Layer** → `research_picks.json`
   Scans 124 watchlist symbols for RSI < 40. Sends top 15 candidates to Gemini 2.5 Flash
   for qualitative ranking (news awareness, balance sheet quality, binary event risk)

6. **Screener** (optional, skip with `--no-screener`)

7. **Log Picks** → `picks_history.json`
   Logs both mechanical screener picks and all research-layer candidates for future analysis

8. **Report** → `improvement_report.json`
   Gemini generates a plain-English analysis of signal quality and suggested improvements

---

### Signal Quality & Optimizer

**signal_quality.json** key buckets:
- `by_regime`: performance split by market regime
- `by_rsi_bucket`: RSI < 15 vs 15–20 vs 20–25 etc.
- `by_vol_bucket`: volume ratio bands
- `by_ma200_bucket`: above vs below 200MA

**Current data-driven findings (1,332 picks):**
- Correction regime: 86% hit rate, +4.58% avg 5d return (Sharpe 1.05)
- Bull regime: 53% hit rate, +0.21% avg 5d return
- RSI < 20 significantly outperforms RSI 20–35
- Below 200MA outperforms above (+1.20% vs +0.49%)

---

## Data Files

| File | Written by | Read by | Purpose |
|------|-----------|---------|---------|
| `screener_config.json` | optimizer | screener, entry_executor | Strategy parameters |
| `screener_results.json` | screener | entry_executor, dashboard | Screener output |
| `pending_entries.json` | screener | entry_executor | Monday buy queue |
| `positions_state.json` | monitor | monitor | Per-position state |
| `market_regime.json` | regime_detector | optimizer, dashboard | Current market regime |
| `signal_quality.json` | signal_analyzer | optimizer, dashboard | Historical performance |
| `picks_history.json` | performance_tracker | signal_analyzer | All picks + returns |
| `research_picks.json` | research_layer | dashboard | LLM-ranked candidates |
| `improvement_report.json` | report_generator | dashboard | Weekly AI report |
| `config_history.json` | optimizer | dashboard | Config change log |

---

## Scheduled Tasks (Windows Task Scheduler)

| Task | Schedule | Script |
|------|----------|--------|
| `Trading-Screener` | Monday 06:00 | `run_screener.bat` → `screener.py` |
| `Trading-RSI-Loop` | Monday 07:00 | `run_rsi_loop.bat` → `rsi_main.py` |
| `Trading-Executor` | Monday 09:15 | `run_executor.bat` → `entry_executor.py` |
| `Trading-Monitor` | Every 15 min (Mon–Fri 09:25–16:05) | `run_monitor.bat` → `monitor.py` |
| `Trading-ScreenerDashboard` | On login | `run_screener_dashboard.bat` |

---

## Dashboard

**URL:** http://localhost:8766/

Start: `powershell -ExecutionPolicy Bypass -File screener_dashboard_server.ps1`
(auto-starts on login via Windows Startup folder)

Features:
- Live screener results and radar
- Auto-entry queue status
- Market regime panel
- Config evolution history
- Research layer LLM analysis
- Performance tracker (all 1,332 picks with forward returns)
- RSI loop and screener log viewers
- Run Screener / Run RSI Loop buttons

---

## Key Design Decisions

**Why RSI mean reversion?**
Oversold stocks (RSI < 20, below Bollinger Band, with elevated volume) in the S&P 500
have demonstrated strong bounce tendencies. The strategy captures the mean reversion
move without predicting direction — it waits for the setup to appear.

**Why exit at RSI 50?**
RSI 50 is the neutral midpoint. Mean reversion from oversold is statistically complete
when RSI recovers to neutral — holding further adds momentum/direction risk the strategy
is not designed to take.

**Why ladders?**
Mean-reverting stocks often get more oversold before bouncing. Ladders allow buying more
at better prices if the stock continues falling, lowering average cost and increasing
the P&L of the eventual bounce.

**Why Gemini in the research layer?**
The mechanical screener finds technically oversold stocks but cannot distinguish panic
selling (temporary) from fundamental deterioration (doesn't bounce). Gemini adds:
1. Catalyst awareness — skips stocks with pending binary events (earnings, FDA, DOJ)
2. Quality filter — prefers strong balance sheets, low short interest
3. Context — knows if a sector is rotating vs a company is broken
