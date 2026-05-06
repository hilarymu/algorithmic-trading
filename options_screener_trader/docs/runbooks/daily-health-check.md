# Daily Health Check

Two runs happen each day. Both should be checked.

- **15:30 ET pre-close** (`\Trading-Options-Preclose`): IV + screener + selector + executor
- **16:30 ET post-close** (`\Trading-Options-Daily`): monitor + EOD analysis

Total check time: ~2 minutes once familiar with the output.

---

## 1. Did both runs complete without errors?

**Pre-close:** Task Scheduler → `\Trading-Options-Preclose` → Last Run Result = `0x0`
Log: `options_preclose_YYYYMMDD.log` — final line: `options_main pre-close done in X.Xs`

**Post-close:** Task Scheduler → `\Trading-Options-Daily` → Last Run Result = `0x0`
Log: `options_loop_YYYYMMDD.log` — final line: `options_main done in X.Xs`
```

If any step errored, you'll see a line like:
```
[timestamp]   screener ERROR: ...
```
followed by a traceback. Non-fatal (pipeline continues), but note which step failed.

---

## 2. How many candidates today?

**Check:** `options_candidates.json`
```
cat options_candidates.json
```
Or look for: `screener done: N candidates, regime=bull`

| Result | Interpretation |
|---|---|
| 0 candidates | Normal in calm markets or bear regime. Check regime field. |
| 1–5 candidates | Typical day. Review the symbols and signal scores. |
| 6–8 candidates | Very oversold market — more signals than usual. Verify regime is bull. |
| > 8 candidates | Only top 8 are kept (sorted by signal score). Normal. |

**Review each candidate's key metrics:**
- `rsi` should be < 25
- `iv_rank` should be ≥ 40
- `signal_score` should be > 40 for a quality trade

---

## 3. Were any positions closed?

**Check:** `monitor done: N checked, K closed`

If K > 0: review `positions_state.json` → `"closed"` array for today's exits.

Check the `exit_reason`:
- `profit_target` — normal, took 50% profit
- `dte_reached` — normal, closed before gamma window
- `rsi_recovery` — normal, signal played out
- `loss_limit` — **flag for review.** What happened? Was there a news event?

---

## 4. Were new positions entered?

**Check the pre-close log (15:30 ET):** `executor done: N executed, K skipped`

If N > 0: new paper orders were placed. Review `positions_state.json` → `"open"` array.

Verify each new entry looks reasonable:
- Strike should be below current stock price (OTM put)
- DTE should be 21–65 (depending on calendar gap fallback)
- Premium collected should match the `signal_quality.json` `premium_pct` estimate ± wide margin

If N = 0: expected if max positions reached, bear regime, or no valid contracts found.

---

## 5. Signal quality check

**Check:** `options_signal_quality.json`

Key fields to review:
```json
"sell_zone_pct": 68.2       // % of universe with IV rank ≥ 40
"data_quality": "hv30_proxy+3d_real"  // how many real IV days accumulated
"regime": "bull"
```

- `sell_zone_pct` < 30 suggests IV environment has collapsed (VIX very low). Fewer/weaker signals.
- `sell_zone_pct` > 80 suggests very high market stress. Quality of signals is excellent but review regime.
- `data_quality` will change from `hv30_proxy+Nd_real` to `real_iv` after ~30 trading days of real snapshots.

---

## 6. Optimizer status

**Check:** `optimizer done: N_closed closed, K insights, M applied`

Expected states:

| State | Condition | Action |
|---|---|---|
| `0 closed — pipeline live, insights activate at 10` | No closed trades yet | Normal — waiting for first exits |
| `N/10 closed — building data` | N < 10 | Normal — accumulating |
| `N closed — win rate X%` | N ≥ 10 | Review `options_improvement_report.json` for insights |

If `M applied > 0`: the optimizer changed `options_config.json`. Review the change and
verify it makes sense. All applied changes are logged in `options_improvement_report.json`
→ `all_applied_changes`.

---

## 7. IV rank distribution sanity check

From `options_signal_quality.json` → `iv_distribution`:

```json
"<40":    { "count": 164, "pct": 32 }   // below sell threshold
"40-55":  { "count": 87,  "pct": 17 }   // low sell zone
"55-70":  { "count": 112, "pct": 22 }   // mid sell zone
"70-85":  { "count": 89,  "pct": 17 }   // high sell zone
"85-100": { "count": 60,  "pct": 12 }   // highest premium zone
```

Healthy market (moderate IV): ~60–70% of universe in sell zone (≥ 40 rank).
Low volatility regime: may see < 30% in sell zone — fewer signals, lower premium.
High stress: may see > 80% — abundant signals but monitor bear regime gate.

---

## Weekly checks (Mondays)

- Review `options_picks_history.json` for the past week — do the picks look sensible?
- Check total open positions vs max_positions — are slots being used efficiently?
- Review any optimizer insights — are they directionally sensible?
- Run tests: `py -3 -m pytest tests/ -v` — should be 71 passed.
