# 2. Constraints

## 2.1 Technical Constraints

| Constraint | Detail |
|------------|--------|
| **Broker** | Alpaca Paper trading only — no live execution in current scope |
| **API rate limits** | Alpaca free tier: 200 req/min on data endpoints; screener batches 30 symbols per call to stay within limits |
| **Execution language** | Python 3.x, stdlib + requests + yfinance + alpaca-trade-api; no heavyweight frameworks |
| **Runtime environment** | Windows Task Scheduler on local Windows machine; no cloud or containers |
| **Data persistence** | JSON flat files only; no database |
| **RSI computation** | Wilder smoothing (Wilder's RSI), not simple EMA; requires 220 days of history per symbol |
| **Gemini model** | Gemini 2.5 Flash via Gemini API; used for research ranking and improvement report generation |
| **Market hours** | US equities only; monitor runs Mon–Fri 09:25–16:05 ET; screener runs pre-market Monday |

## 2.2 Organisational / Process Constraints

| Constraint | Detail |
|------------|--------|
| **Paper trading only** | All orders hit the Alpaca paper account; no real money at risk |
| **Weekly cadence** | Screener and self-optimizer run once per week on Monday morning |
| **Manual veto window** | Entry executor deferred to 09:15 ET to give the trader time to review and veto picks |
| **Single developer** | No CI/CD, no PR review — owner is sole developer and reviewer |
| **Max positions** | 10 simultaneous open positions; configured in `screener_config.json` |

## 2.3 Conventions

| Convention | Detail |
|------------|--------|
| **Position size** | $1,000 per initial buy; configurable via `screener_config.json` |
| **RSI period** | 14-period Wilder RSI; requires 220 days of daily bars (14 × initial seed + lookback buffer) |
| **Bollinger Band** | 20-period SMA ± 2 standard deviations |
| **Volume confirmation** | Current day's volume vs 20-day average; threshold configurable (default 2.0×) |
| **Hard stop level** | Entry price × 0.90 (−10%) |
| **Trailing stop activation** | Price ≥ entry × 1.10 (+10%); floor = high water mark × 0.95 |
| **RSI exit level** | RSI ≥ 50 (mean reversion complete; configurable per position) |
| **Ladder rungs** | Fixed at −15%, −25%, −35%, −45% from entry with share multipliers 1.5×, 2.5×, 3.5×, 2.0× |
| **Config file** | `screener_config.json` — auto-updated by optimizer; never edited manually |
| **Log location** | `screener_trader\logs\` (relative to repo root) |
