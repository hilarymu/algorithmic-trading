# 11. Risks and Technical Debt

## 11.1 Business / Trading Risks

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|-----------|--------|-----------|
| R-01 | Gap-down through stop-limit on equity positions | Medium | High | Use stop market (not stop-limit) for large moves; Wheel spreads the risk |
| R-02 | IV Rank insufficient (< 30 days history) on new entry | High (Phase 1) | Low | IV null check before any options screen; wait for 30+ days |
| R-03 | Regime detector misclassifies (e.g. fast crash as bull) | Low | High | Regime detection uses 200MA + 20d return + VIX combined; conservative |
| R-04 | Alpaca paper options pricing differs from live | Medium | Medium | Acceptable for research phase; review before any live migration |
| R-05 | Assignment in downtrend (CSP below strike) | Medium | Medium | Wheel strategy recovers cost basis; size limits cap exposure |
| R-06 | Earnings gap through strike | Medium | High | `near_earnings` flag is tracked; optimizer will learn edge over time |
| R-07 | 3 consecutive loss-limit hits in one week | Low | High | Pause trigger: no new trades until manual review |

## 11.2 Technical Risks

| ID | Risk | Likelihood | Impact | Mitigation |
|----|------|-----------|--------|-----------|
| T-01 | Alpaca API structure changes (e.g. `impliedVolatility` moves field) | Low | High | Defensive field access with fallback logging; unit test on real response shape |
| T-02 | Wikipedia HTML structure changes (ticker parsing breaks) | Low | Medium | Two-pattern parser is robust; alert if universe < 400 symbols |
| T-03 | Windows Task Scheduler misfire (sleep, patch reboot) | Medium | Low | Logs are date-stamped; missing log = missed run; easy to detect |
| T-04 | iv_history.json grows very large over years | Low | Low | One float per ticker per day ≈ 510 × 252 × 8 bytes ≈ 1 MB/year |
| T-05 | Race condition on JSON file writes during concurrent runs | Low | Medium | Task scheduler runs once/day; no concurrency expected |

## 11.3 Hard-coded Safety Rules (Never Optimised Away)

These are constraints enforced in code, not config. The optimizer cannot override them.

- Never sell **naked** puts on earnings week (IV spike, unbounded risk)
- Never size a single position > 10% of NAV
- Never hold through expiration unless intentionally accepting Wheel assignment
- Never open new positions if account margin utilisation > 70%
- Bear regime: **no new options positions** opened at all
- 3 consecutive loss-limit hits in a week → pause all new entries

## 11.4 Technical Debt

| Item | Description | Priority | Phase to resolve |
|------|-------------|---------|-----------------|
| TD-01 | ~~`STRATEGY_SPEC.md` is the source of truth for strategy — should be superseded by arc42 docs~~ | — | **Resolved 2026-04-25** — archived to `docs/archive/`; superseded by `docs/guides/` + arc42 |
| TD-02 | No unit tests for `iv_tracker.py` | Medium | Phase 2 |
| TD-03 | `options_config.json` has no schema validation | Low | Phase 2 |
| TD-04 | Alpaca options snapshot field (`impliedVolatility` at root) documented only in code comments | Low | Resolved in 08-crosscutting-concepts.md |
| TD-05 | `run_options_monitor.bat` and `run_options_executor.bat` do not yet exist | High | Phase 2 |
