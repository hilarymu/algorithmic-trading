# 6. Runtime View

## 6.1 Daily Pipeline — Phase 1 (Current)

Triggered by Windows Task Scheduler at **16:30 ET** (after market close).

```mermaid
sequenceDiagram
    participant Scheduler as Task Scheduler
    participant Main as options_main.py
    participant BF as iv_backfill.py
    participant IV as iv_tracker.py
    participant SC as options_screener.py
    participant Wiki as Wikipedia
    participant AlpacaData as Alpaca Data API
    participant Files as JSON data files

    Scheduler->>Main: daily trigger (16:30 ET)

    alt First run (iv_history.json absent or tiny)
        Main->>BF: run()
        BF->>AlpacaData: GET /v2/stocks/bars (270 cal days, all universe)
        BF->>AlpacaData: GET /v1beta1/options/bars (35 contracts/batch)
        BF->>BF: Black-Scholes IV inversion per (symbol, date)
        BF->>Files: write iv_history.json (~252 readings per symbol)
    end

    Main->>IV: run()
    IV->>Wiki: GET SP500 + NASDAQ100 component lists
    IV->>AlpacaData: GET /v2/stocks/trades/latest (batch, 500+ tickers)
    AlpacaData-->>IV: latest prices
    IV->>IV: construct ATM contract symbols (no API call)
    IV->>AlpacaData: GET /v1beta1/options/snapshots (batches of ~40)
    AlpacaData-->>IV: impliedVolatility per contract
    IV->>IV: select ATM IV per ticker
    IV->>Files: append iv_history.json
    IV->>IV: compute IV Rank (rolling 252-day)
    IV->>Files: write iv_rank_cache.json
    IV-->>Main: {iv_fetched: 510, with_iv_rank: N}

    Main->>SC: run()
    SC->>SC: read iv_rank_cache + detect regime
    SC->>AlpacaData: GET /v2/stocks/bars (RSI + volume, eligible symbols)
    AlpacaData-->>SC: daily bars
    SC->>SC: Wilder RSI(14) + volume ratio per symbol
    SC->>SC: apply strategy matrix (regime x IV rank x RSI)
    SC->>Files: write options_candidates.json
    SC->>Files: append options_picks_history.json (research_mode=true)
    SC-->>Main: {candidates: N, regime: ..., picks_added: M}

    Main->>Main: log completion
```

**Runtime characteristics (Phase 1):**
- iv_tracker duration: ~10-12 seconds for 529 universe tickers
- options_screener duration: ~15-20 seconds (RSI bar fetch for eligible symbols)
- iv_backfill (first run only): ~30-60 seconds (~184 API calls)
- No orders placed

## 6.2 Daily Pipeline — Phase 2 (Planned)

```mermaid
sequenceDiagram
    participant Main as options_main.py
    participant Screen as options_screener.py
    participant Selector as options_strategy_selector.py
    participant Executor as options_executor.py
    participant Monitor as options_monitor.py
    participant Alpaca as Alpaca Paper API

    Main->>Monitor: run() — check exits first
    Monitor->>Alpaca: GET /v2/positions
    Monitor->>Monitor: check 50% profit / 21 DTE / RSI / loss limit
    Monitor->>Alpaca: POST /v2/orders (buy to close where triggered)
    Monitor-->>Main: exit actions taken

    Main->>Screen: run()
    Screen->>Screen: apply RSI + IV Rank filter
    Screen-->>Main: candidate list

    Main->>Selector: run(candidates)
    Selector->>Selector: apply regime × IV matrix
    Selector-->>Main: pending_entries list

    Main->>Executor: run(pending_entries)
    Executor->>Alpaca: POST /v2/orders (CSP / spread)
    Executor-->>Main: orders placed
```

## 6.3 Error Handling at Runtime

| Failure | Behaviour |
|---------|-----------|
| Wikipedia fetch fails | Log warning, use cached ticker list from previous run |
| Alpaca data API timeout | `safe_get` retries 2× with 2 s gap; logs warning on persistent failure |
| Options snapshot 404 | Ticker skipped; logged; does not crash pipeline |
| IV Rank unavailable (< 30 days) | Ticker excluded from options screening; IV history continues |
| Order 403 (insufficient qty) | Log error; do not retry; investigate state next cycle |
| Gemini 503 (AI report) | Fallback report used; not a pipeline failure |

## 6.4 State Transitions — Position Lifecycle _(Phase 2)_

```mermaid
stateDiagram-v2
    [*] --> Screened : RSI + IV Rank pass
    Screened --> PendingEntry : strategy selected
    PendingEntry --> Open : order filled
    Open --> ClosedProfit : 50% profit target hit
    Open --> ClosedTime : 21 DTE reached
    Open --> ClosedRSI : RSI > 50 (mean reversion)
    Open --> ClosedLoss : loss > 2× premium
    Open --> Assigned : price < strike at expiry
    Assigned --> WheelCC : covered call opened
    WheelCC --> WheelClosed : CC expires / closed
    ClosedProfit --> [*]
    ClosedTime --> [*]
    ClosedRSI --> [*]
    ClosedLoss --> [*]
    WheelClosed --> [*]
```
