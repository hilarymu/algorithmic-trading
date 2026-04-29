# 11. Risks and Technical Debt

## 11.1 Active Risks

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|-----------|--------|------------|
| R1 | Alpaca paper API changes field names or endpoints | Medium | High | Monitor Alpaca changelog; all API calls isolated in HTTP helpers |
| R2 | Wikipedia S&P 500 page restructures — ticker scrape breaks | Medium | Medium | Fallback to 50-symbol hardcoded list; alert logged |
| R3 | Python 3.x SSL library raises new exception types | Low | Medium | `(URLError, TimeoutError, OSError)` catches cover known cases |
| R4 | Positions_state.json corrupted or deleted | Low | High | No backup mechanism — entries would need to be manually reconstructed from Alpaca positions |
| R5 | Stop order fill slippage exceeds 0.5% buffer | Low | Medium | Stop-limit with 0.5% gap below stop price; accepted risk on paper account |
| R6 | Multiple positions open simultaneously during sharp correction | Medium | Medium | Add-down ladder increases exposure; hard cap at max_positions=10 |
| R7 | Gemini API unavailable for extended period | Low | Low | Research layer falls back to mechanical candidates; trading not affected |
| R8 | Task Scheduler misfires (reboot, sleep, power event) | Medium | Low | StartWhenAvailable=true on all tasks; manually re-runnable at any time |

---

## 11.2 Technical Debt

| ID | Item | Location | Impact | Remediation |
|----|------|----------|--------|-------------|
| TD1 | Wikipedia scraper for S&P 500 is fragile | `screener.py:get_sp500_tickers()` | Silently degrades to 50 symbols on parse failure | Replace with a dedicated S&P 500 provider (e.g. Alpaca asset list with `sp500=true` filter once available, or a small JSON file updated weekly) |
| TD2 | Hardcoded absolute paths in older modules | `monitor.py`, legacy paths | Breaks if project is moved | Migrate all remaining absolute paths to `Path(__file__).parent` (screener.py and entry_executor.py already fixed) |
| TD3 | picks_history.json has no size cap | `performance_tracker.py` | File grows unbounded; after ~3 years could become slow | Prune entries older than 2 years, or migrate to SQLite (planned: see ADR-006 deferred) |
| TD4 | positions_state.json has no atomic write | `monitor.py:save_state()` | Crash mid-write could corrupt state | Wrap in `os.replace(tmp, final)` pattern (performance_tracker.py already uses this) |
| TD5 | No unit tests for screener_trader | N/A | Regressions in indicator math not caught automatically | Add pytest tests for `calc_rsi`, `calc_bollinger`, `score_stock` at minimum |
| TD6 | ThreadPoolExecutor with 8 workers in performance_tracker | `performance_tracker.py:fill_missing_returns()` | May hit Alpaca rate limits with burst requests | Add per-worker rate limiting or reduce max_workers to 4 |

---

## 11.3 Deferred Decisions

| ADR | Decision | Deferred Until |
|-----|----------|---------------|
| ADR-006 (planned) | SQLite migration for picks history | ≥ 50 closed positions |
| ADR-007 (planned) | Real-money account activation | Manual decision after 6 months paper trading with positive expectancy |
