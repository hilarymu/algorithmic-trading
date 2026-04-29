# 10. Quality Requirements

## 10.1 Quality Tree

```
screener_trader quality
├── Safety
│   ├── Hard stop always live in Alpaca for every open position
│   ├── Trailing stop raises floor only (never lowers)
│   └── No position survives without a stop order
├── Reliability
│   ├── Scheduler tasks fire Mon–Fri without manual intervention
│   ├── Monitor completes all positions per cycle without crashing
│   └── API timeouts retry 3× before failing gracefully
├── Self-improvement
│   ├── Entry thresholds update weekly from pick history
│   ├── Optimizer uses data-driven mode when ≥ 10 samples per bucket
│   └── Config changes audited in config_history.json
├── Observability
│   ├── Every screener run produces a date-stamped log
│   ├── Every order placement logged with order ID
│   └── Every exit logged with reason and P&L
└── Controllability
    ├── Any pending entry can be vetoed before market open
    └── Entire pipeline can be halted by setting pending status = cancelled
```

---

## 10.2 Quality Scenarios

| ID | Stimulus | Response | Measure |
|----|---------|----------|---------|
| Q1 | Alpaca API returns HTTP 503 during monitor run | Retry 3× with exponential backoff; log warning; continue with next position | No unhandled exception; position checked on next cycle |
| Q2 | Wikipedia S&P 500 scrape fails at 06:00 Monday | Fall back to hardcoded 50-symbol list; log warning; screener completes | Result contains at least 1 symbol; screener does not crash |
| Q3 | Hard stop order disappears from Alpaca (order expired/cancelled) | Monitor detects missing stop at next cycle; re-places it automatically | Stop re-placed within one 15-min monitor cycle |
| Q4 | Stock price drops 15% after entry (rung 1 of ladder) | Ladder buy limit order placed at -15% level automatically | Order confirmed in Alpaca open orders |
| Q5 | RSI optimizer runs with only 3 historical picks | Falls back to regime_defaults; does not derive data-driven thresholds | Method = "regime_defaults" logged; no config regression |
| Q6 | Trailing stop would lower the floor price | Guard: `target_stop = max(new_floor, current_stop)` | Floor never moves down; stop only tightens |
| Q7 | Python 3.14 SSL timeout raises bare `TimeoutError` | Caught by `(URLError, TimeoutError, OSError)` clause; retry fires | No crash; retry logged |

---

## 10.3 Performance Targets

| Metric | Target | Actual (observed) |
|--------|--------|-------------------|
| Screener runtime | < 120s for full S&P 500 | ~35-50s (batch API) |
| Monitor cycle (all positions) | < 60s | ~10s per position |
| RSI loop full run | < 10 min | ~5 min |
| Executor: entry to order placed | < 30s | < 5s |
