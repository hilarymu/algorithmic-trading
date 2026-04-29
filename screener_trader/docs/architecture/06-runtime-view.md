# 6. Runtime View

## 6.1 Monday Morning Pipeline

```mermaid
sequenceDiagram
    participant Sched as Task Scheduler
    participant SC as screener.py
    participant RL as rsi_main.py
    participant EX as entry_executor.py
    participant ALP as Alpaca Data API
    participant BROKER as Alpaca Paper Trading
    participant GEMINI as Gemini API
    participant FILES as JSON Data Files

    Note over Sched,FILES: Monday 06:00 ET

    Sched->>SC: run_screener.bat
    SC->>ALP: GET /v2/stocks/bars (30 symbols/call × ~17 calls)
    ALP-->>SC: 220 days daily bars per symbol
    SC->>SC: compute RSI(14), BB(20,2σ), vol ratio, 200MA
    SC->>SC: apply 4 filters from screener_config.json
    SC->>FILES: write screener_results.json
    SC->>FILES: write pending_entries.json (status: pending)

    Note over Sched,FILES: Monday 07:00 ET

    Sched->>RL: run_rsi_loop.bat
    RL->>ALP: GET /v2/stocks/bars (SPY + VIXY — regime detection)
    RL->>FILES: write market_regime.json
    RL->>ALP: GET /v2/stocks/bars (unresolved picks — fill returns)
    RL->>FILES: update picks_history.json
    RL->>RL: signal_analyzer — bucket hit rates
    RL->>FILES: write signal_quality.json
    RL->>RL: optimizer — find best thresholds
    RL->>FILES: update screener_config.json, config_history.json
    RL->>GEMINI: top 15 oversold candidates (research ranking)
    GEMINI-->>RL: ranked research_picks
    RL->>FILES: write research_picks.json
    RL->>GEMINI: signal quality analysis (improvement report)
    GEMINI-->>RL: plain-English report
    RL->>FILES: write improvement_report.json

    Note over Sched,FILES: Monday 09:15 ET

    Sched->>EX: run_executor.bat
    EX->>FILES: read pending_entries.json
    EX->>BROKER: GET /v2/positions (deduplication check)
    loop each non-skipped pending entry
        EX->>BROKER: POST /v2/orders (market buy)
        BROKER-->>EX: order ID
    end
    EX->>FILES: update pending_entries.json (status: executed)

    Note over Sched,FILES: Monday 09:30 ET — Market Opens
    Note over Sched,FILES: Orders fill at open price
```

---

## 6.2 Intraday Monitor Cycle (Every 15 Min, 09:25–16:05)

```mermaid
sequenceDiagram
    participant Sched as Task Scheduler
    participant MO as monitor.py
    participant ALP as Alpaca Data API
    participant BROKER as Alpaca Paper Trading
    participant FILES as positions_state.json

    Sched->>MO: run_monitor.bat (every 15 min)
    MO->>BROKER: GET /v2/positions (all open positions)
    MO->>FILES: read positions_state.json

    loop each open position
        MO->>ALP: GET /v2/stocks/bars (50 days, this symbol)
        MO->>MO: compute RSI(14)

        alt RSI ≥ 50 (mean reversion complete)
            MO->>BROKER: DELETE /v2/orders/{stop_id}
            MO->>BROKER: DELETE /v2/orders/{ladder_ids}
            MO->>BROKER: POST /v2/orders (market sell)
            Note right of MO: PRIMARY EXIT
        else RSI < 50
            MO->>MO: update high water mark
            alt Price ≥ entry × 1.10 (trailing active)
                MO->>BROKER: DELETE /v2/orders/{old_stop_id}
                Note right of MO: wait 0.5s (race condition guard)
                MO->>BROKER: POST /v2/orders (stop @ HWM × 0.95)
            else trailing not yet active
                MO->>BROKER: verify hard stop @ entry × 0.90
                MO->>BROKER: replace if missing/filled
            end
            MO->>BROKER: verify all 4 ladder orders present
            MO->>BROKER: replace any missing ladder rungs
        end
    end

    MO->>FILES: write positions_state.json
```

---

## 6.3 Position Lifecycle State Machine

```mermaid
stateDiagram-v2
    [*] --> Screened : RSI + BB + vol filters pass
    Screened --> PendingEntry : written to pending_entries.json
    PendingEntry --> Vetoed : trader sets skip=true before 09:15
    PendingEntry --> Ordered : entry_executor places market buy
    Ordered --> Open : order fills at market open (09:30)
    Open --> ClosedRSI : RSI ≥ 50 (monitor: market sell)
    Open --> ClosedTrailing : trailing stop hit in Alpaca
    Open --> ClosedHardStop : hard stop hit in Alpaca
    Open --> OpenLadderFilled : ladder rung fills (average down)
    OpenLadderFilled --> ClosedRSI : RSI ≥ 50 (market sell whole position)
    OpenLadderFilled --> ClosedTrailing : trailing stop hit
    OpenLadderFilled --> ClosedHardStop : hard stop hit
    Vetoed --> [*]
    ClosedRSI --> [*]
    ClosedTrailing --> [*]
    ClosedHardStop --> [*]
```

---

## 6.4 Error Handling at Runtime

| Failure | Behaviour |
|---------|-----------|
| Alpaca data API timeout | Screener logs warning and skips the failed batch; partial results still written |
| Alpaca trading API 403 on stop replace | wait 0.5s then retry once; logged if second attempt fails |
| Gemini API 503 / timeout | Research layer logs warning; `research_picks.json` not updated this cycle; not a pipeline failure |
| `pending_entries.json` missing | Entry executor logs error and exits cleanly; no orders placed |
| `positions_state.json` missing | Monitor auto-initialises empty state; all Alpaca positions treated as new |
| New Alpaca position not in state | Monitor auto-initialises with entry price from Alpaca; places hard stop and ladder orders on first cycle |
| Scheduler misfire (sleep / reboot) | Date-stamped logs make missed runs detectable; manual re-run via `.bat` files |
| RSI computation with < 220 bars | Symbol skipped by screener; logged |
