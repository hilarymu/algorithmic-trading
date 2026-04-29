# 8. Cross-cutting Concepts

## 8.1 Regime Detection (Shared Component)

`rsi_loop/regime_detector.py` is used by:
- `screener_trader` — via `rsi_main.py` step 1; output to `market_regime.json`
- `options_screener_trader` — imported directly to gate strategy selection

The detector uses SPY and VIXY daily bars to classify current market conditions:

| Regime | Conditions | Impact on screener_trader |
|--------|-----------|--------------------------|
| `bull` | SPY above 200MA, 20d return > 0, VIX low | Conservative thresholds; oversold in bull = weak stock |
| `mild_correction` | SPY near 200MA, moderate pullback | Moderate thresholds |
| `correction` | SPY below 200MA, 20d return < −5% | Most aggressive — strategy excels in corrections |
| `recovery` | SPY recovering from correction | Aggressive thresholds |
| `geopolitical_shock` | VIX spike, SPY flash drop | Treated conservatively |
| `bear` | Sustained bear conditions | Very conservative; no data yet |

`market_regime.json` is read by the optimizer to select regime-appropriate defaults
and by the dashboard to display the current market environment.

---

## 8.2 Self-Improvement Pattern

Every component in the feedback loop follows the same structure:

1. **Collector** — logs outcomes into `picks_history.json` (entry price, entry RSI, entry vol ratio, regime at entry, forward returns at 1d/5d/10d/20d)
2. **Analyser** — `signal_analyzer.py` buckets outcomes by regime, RSI, volume, 200MA position
3. **Optimizer** — reads bucketed stats, applies minimum-sample threshold (10), derives improved thresholds
4. **Config writer** — updates `screener_config.json`; appends change record to `config_history.json`

**Mode switch rule:** If fewer than 10 picks exist for a bucket, the optimizer falls back
to regime-based defaults rather than data-driven thresholds. This prevents overfitting
to small samples.

**Currently (April 2026):** 1,332 picks tracked — fully data-driven mode active.

---

## 8.3 JSON File Ownership

Each JSON file has exactly one writer and one or more readers. No two processes write
the same file during normal operation (race conditions are avoided by design — screener,
RSI loop, and executor run sequentially Monday morning).

| File | Writer | Readers |
|------|--------|---------|
| `screener_config.json` | `optimizer.py` | `screener.py`, `entry_executor.py` |
| `screener_results.json` | `screener.py` | dashboard |
| `pending_entries.json` | `screener.py` | `entry_executor.py`, dashboard, trader (manual edit) |
| `positions_state.json` | `monitor.py` | `monitor.py`, `entry_executor.py`, dashboard |
| `market_regime.json` | `regime_detector.py` | `optimizer.py`, dashboard |
| `signal_quality.json` | `signal_analyzer.py` | `optimizer.py`, dashboard |
| `picks_history.json` | `performance_tracker.py` | `signal_analyzer.py`, dashboard |
| `research_picks.json` | `research_layer.py` | dashboard |
| `improvement_report.json` | `report_generator.py` | dashboard |
| `config_history.json` | `optimizer.py` | dashboard |

---

## 8.4 Alpaca API Error Handling Pattern

All Alpaca API calls follow the same defensive pattern:

```
request → check HTTP status → on 4xx/5xx: log with full response → return None / skip symbol
```

Specific handling:

| HTTP Status | Context | Behaviour |
|-------------|---------|-----------|
| 403 | Stop order replace (trailing stop update) | Wait 0.5s, retry once; log if second fails |
| 404 | Order query (stop/ladder verification) | Treat as cancelled; re-place the order |
| 422 | Order validation (bad symbol, market closed) | Log and skip; do not crash |
| 429 | Rate limit | Sleep 60s; retry |
| 5xx | Data API timeout | Retry 2× with 2s gap; log warning on persistent failure |

No failure causes an unhandled exception that stops the monitor mid-cycle. Each position
is processed independently — one failure does not affect others.

---

## 8.5 Stop Order Race Condition Guard

When the trailing stop floor rises (monitor detects new high water mark), the sequence is:

1. Cancel existing stop order (`DELETE /v2/orders/{id}`)
2. **Sleep 0.5 seconds** — Alpaca needs time to process the cancellation
3. Place new stop order at updated floor price

Without the sleep, the new order sometimes gets a 403 because Alpaca still sees the old
order as live. This is a known Alpaca paper API behaviour.

---

## 8.6 Logging Convention

All scripts write date-stamped log files to `logs\`:

| Script | Log filename | Content |
|--------|-------------|---------|
| `screener.py` | `screener_YYYYMMDD.log` | Per-symbol RSI/BB/vol/MA values; filter pass/fail; final picks |
| `entry_executor.py` | `executor_YYYYMMDD.log` | Symbols processed; skipped (held/skip=true); orders placed |
| `monitor.py` | `monitor_YYYYMMDD.log` | Per-position RSI; exit actions; stop/ladder updates |
| `rsi_main.py` | `rsi_loop_YYYYMMDDHHMMSS.log` | Full 8-step pipeline output; Gemini responses |

Logs are the primary debugging tool. A missing log for a given date indicates a scheduler misfire.

---

## 8.7 Human Override Points

The system has two designed human intervention points:

| Point | Mechanism | Window |
|-------|-----------|--------|
| Veto a pick | Edit `pending_entries.json`, set `"skip": true` | After screener (06:00) and before executor (09:15) Monday |
| Emergency halt | Delete or empty `pending_entries.json` | Before 09:15 Monday |

The monitor has no manual override point — it runs automatically during market hours.
Emergency position closure must be done directly in the Alpaca paper dashboard.

---

## 8.8 Gemini Integration Pattern

Gemini 2.5 Flash is called in two contexts:

| Context | Input | Output | Failure mode |
|---------|-------|--------|-------------|
| Research layer | Top 15 oversold candidates + RSI/vol data | Ranked `research_picks.json` with rationale | Log warning; file not updated |
| Improvement report | `signal_quality.json` stats | Plain-English `improvement_report.json` | Log warning; stale report retained |

Both calls are non-blocking for the trading pipeline — if Gemini fails, entries are not
affected. The mechanical screener results in `pending_entries.json` are what the executor
reads, not the Gemini research picks.
