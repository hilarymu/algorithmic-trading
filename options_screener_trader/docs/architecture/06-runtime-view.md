# 6. Runtime View

## 6.1 Pre-close run (15:30 ET) — IV + screening + order placement

Triggered by Task Scheduler at **15:30 ET** while the market is still open.
`run_options_preclose.bat` → `options_main.py --pre-close`

```mermaid
sequenceDiagram
    participant TS as Task Scheduler
    participant Main as options_main.py
    participant IV as iv_tracker.py
    participant SC as options_screener.py
    participant SEL as options_strategy_selector.py
    participant EX as options_executor.py
    participant AlpacaData as Alpaca Data API
    participant AlpacaTrade as Alpaca Trading API
    participant Files as JSON data files

    TS->>Main: fire run_options_preclose.bat (15:30 ET)

    Main->>IV: run()
    IV->>AlpacaData: GET /v1beta1/options/snapshots (500+ symbols, direct OCC construction)
    AlpacaData-->>IV: indicative IV (or empty — HV30 proxy used as fallback)
    IV->>Files: append iv_history.json, write iv_rank_cache.json

    Main->>SC: run()
    SC->>AlpacaData: GET /v2/stocks/bars (RSI + volume for eligible symbols)
    AlpacaData-->>SC: daily bars
    SC->>SC: apply filters (RSI, IV rank, volume, regime)
    SC->>Files: write options_candidates.json

    Main->>SEL: run()
    SEL->>Files: read options_candidates.json
    loop each candidate (2–5 symbols)
        SEL->>AlpacaTrade: GET /v2/options/contracts (find real listed strikes)
        AlpacaTrade-->>SEL: listed contract symbols near target strike
        SEL->>AlpacaData: GET /v1beta1/options/snapshots (live quote attempt)
        AlpacaData-->>SEL: quote (or empty — BSM pricing used as fallback)
        SEL->>SEL: pick best delta match, build leg dict
    end
    SEL->>Files: write options_pending_entries.json

    Main->>EX: run()
    EX->>AlpacaTrade: POST /v2/orders (sell-to-open, market still open ~15:33 ET)
    AlpacaTrade-->>EX: order confirmation
    EX->>Files: append positions_state.json (new open positions)

    Main->>Main: log completion (~15:33 ET)
```

**Runtime characteristics (pre-close):**
- iv_tracker: ~10–12 seconds (500+ symbols)
- options_screener: ~15–20 seconds (RSI bar fetch)
- strategy_selector: ~5 seconds (2–5 candidates × contract lookup + BSM)
- executor: ~2 seconds (limit orders placed while market open)
- Total: ~35–50 seconds; finishes well before 16:00 ET market close

## 6.2 Post-close run (16:30 ET) — EOD monitoring and analysis

Triggered by Task Scheduler at **16:30 ET** after market close.
`run_options_loop.bat` → `options_main.py --post-close`

No orders are placed in this run (market closed). IV tracker, screener, selector,
and executor are all skipped (already ran at 15:30).

```mermaid
sequenceDiagram
    participant TS as Task Scheduler
    participant Main as options_main.py
    participant MON as options_monitor.py
    participant AN as options_signal_analyzer.py
    participant OPT as options_optimizer.py
    participant AlpacaTrade as Alpaca Trading API
    participant Files as JSON data files

    TS->>Main: fire run_options_loop.bat (16:30 ET)
    Note over Main: IV + screener + selector + executor skipped (ran at 15:30)

    Main->>MON: run() — daily close check
    MON->>Files: read positions_state.json
    loop each open position
        MON->>AlpacaTrade: GET /v1beta1/options/snapshots (EOD quote)
        MON->>MON: check profit target / loss limit / DTE / RSI recovery
        opt exit condition met
            MON->>AlpacaTrade: POST /v2/orders (buy-to-close)
            MON->>Files: update positions_state.json
        end
    end

    Main->>AN: run_analyzer()
    AN->>Files: read options_candidates.json, iv_rank_cache.json, positions_state.json
    AN->>AN: score candidates, compute outcome stats
    AN->>Files: write options_signal_quality.json

    Main->>OPT: run_optimizer()
    OPT->>Files: read options_signal_quality.json, options_config.json
    OPT->>OPT: generate insights (auto-apply when n_closed >= 50)
    OPT->>Files: write options_improvement_report.json

    Main->>Main: log completion
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
