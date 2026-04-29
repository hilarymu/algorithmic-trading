# 2. Constraints

## 2.1 Technical Constraints

| Constraint | Detail |
|------------|--------|
| **Broker** | Alpaca Paper only — no live execution in current scope |
| **API rate limits** | Alpaca free tier: 200 req/min data, throttling required for 500+ ticker universe |
| **Historical options data** | Alpaca historical options data starts Feb 2024 — IV history builds from day 1 of operation |
| **IV source** | Self-computed from Alpaca indicative snapshot feed — no Bloomberg/OPRA subscription |
| **Execution language** | Python 3.x, stdlib + requests only; no heavyweight frameworks |
| **Runtime environment** | Windows Task Scheduler on local machine; no cloud/container |
| **Options contract pricing** | Alpaca options snapshots provide `impliedVolatility` at top level of snapshot object (not inside `greeks`) |

## 2.2 Organisational / Process Constraints

| Constraint | Detail |
|------------|--------|
| **Paper trading only** | All orders hit the paper account; no real money at risk |
| **Daily cadence** | Options cycle runs once per day at 16:30 ET (after market close) |
| **Manual review gate** | Phase 1: no orders placed — research mode only |
| **Phase gating** | Auto-entry (`auto_entry.enabled`) is `false` until IV history is sufficient (≥ 30 days per ticker) |
| **Single developer** | No CI/CD, no PR review — owner is sole developer and reviewer |

## 2.3 Conventions

| Convention | Detail |
|------------|--------|
| **Contract symbol format** | `{SYMBOL}{YYMMDD}C{STRIKE_8DIGIT}` e.g. `AAPL260515C00270000` |
| **Strike increments** | < $25 → $1; $25–$50 → $2.50; $50–$200 → $5; $200–$500 → $10; > $500 → $25 |
| **IV Rank formula** | `(IV_current − IV_52wk_low) / (IV_52wk_high − IV_52wk_low) × 100` |
| **Earnings** | Tracked as signal flag (`near_earnings=true`) — NOT a hard block on entry |
| **Regime detector** | Imported directly from `screener_trader/rsi_loop/regime_detector.py` |
