# C4 — Level 2: Containers

The eight Python modules and their relationships to the nine JSON data files.

---

## Container diagram

```mermaid
graph LR
    subgraph triggers["Triggers"]
        daily["⏰ Task Scheduler<br/>16:30 ET Daily"]
        intra["⏰ Task Scheduler<br/>09:30 ET Intraday"]
    end

    subgraph pipeline["options_loop/ — Python Modules"]
        direction TB
        backfill["iv_backfill.py<br/><i>First-run only</i><br/>HV30 proxy bootstrap"]
        tracker["iv_tracker.py<br/><i>Step 1</i><br/>Daily IV snapshot"]
        screener["options_screener.py<br/><i>Step 2</i><br/>RSI + IV rank filter"]
        monitor["options_monitor.py<br/><i>Step 3 + Intraday</i><br/>Exit condition checks"]
        selector["options_strategy_selector.py<br/><i>Step 4</i><br/>BSM delta targeting"]
        executor["options_executor.py<br/><i>Step 5</i><br/>Paper order placement"]
        analyzer["options_signal_analyzer.py<br/><i>Step 6</i><br/>Score + outcome stats"]
        optimizer["options_optimizer.py<br/><i>Step 7</i><br/>Insights + config tuning"]
    end

    subgraph data["JSON Data Store (project root)"]
        direction TB
        ivhist["iv_history.json<br/><i>252-day IV per symbol</i>"]
        ivcache["iv_rank_cache.json<br/><i>Fast rank lookup</i>"]
        cands["options_candidates.json<br/><i>Today's screened picks</i>"]
        pickshist["options_picks_history.json<br/><i>Permanent research log</i>"]
        pending["options_pending_entries.json<br/><i>Contracts ready to enter</i>"]
        positions["positions_state.json<br/><i>Open + closed positions</i>"]
        quality["options_signal_quality.json<br/><i>Scores + outcome stats</i>"]
        report["options_improvement_report.json<br/><i>Insights + applied changes</i>"]
        config["options_config.json<br/><i>All strategy parameters</i>"]
    end

    subgraph alpaca["Alpaca Paper API"]
        eqbars["Equity bars<br/>(OHLCV)"]
        optsnapshot["Options snapshots<br/>(IV, quotes)"]
        orders["Orders API<br/>(paper trades)"]
    end

    daily -->|"options_main.py"| pipeline
    intra -->|"options_main.py --intraday"| monitor

    backfill -->|writes| ivhist
    backfill -->|writes| ivcache
    backfill <-->|reads| eqbars

    tracker -->|appends| ivhist
    tracker -->|writes| ivcache
    tracker <-->|reads| optsnapshot

    screener -->|reads| ivcache
    screener -->|writes| cands
    screener -->|appends| pickshist
    screener <-->|reads| eqbars
    screener -->|reads| config

    monitor -->|reads/writes| positions
    monitor <-->|reads/writes| orders
    monitor <-->|reads| optsnapshot

    selector -->|reads| cands
    selector -->|writes| pending
    selector <-->|reads| optsnapshot
    selector -->|reads| config

    executor -->|reads| pending
    executor -->|writes| positions
    executor <-->|writes| orders
    executor -->|reads| config

    analyzer -->|reads| cands
    analyzer -->|reads| ivcache
    analyzer -->|reads| positions
    analyzer -->|writes| quality

    optimizer -->|reads| quality
    optimizer -->|reads/writes| config
    optimizer -->|writes| report

    style pipeline fill:#dbeafe,stroke:#3b82f6
    style data fill:#dcfce7,stroke:#22c55e
    style alpaca fill:#fef3c7,stroke:#f59e0b
    style triggers fill:#f3f4f6,stroke:#9ca3af
```

---

## Module responsibilities at a glance

| Module | Single responsibility | Touches orders? |
|---|---|---|
| `iv_backfill.py` | Bootstrap 252-day IV history on first run | No |
| `iv_tracker.py` | Append today's IV snapshot; recompute ranks | No |
| `options_screener.py` | Filter universe by RSI, IV rank, volume | No |
| `options_monitor.py` | Check exit conditions on open positions | Yes — buy-to-close |
| `options_strategy_selector.py` | Find the right contract for each candidate | No |
| `options_executor.py` | Place paper entry orders | Yes — sell-to-open |
| `options_signal_analyzer.py` | Score candidates; aggregate closed-position stats | No |
| `options_optimizer.py` | Generate insights; optionally tune config | No |

**Safety principle:** Only `options_executor.py` and `options_monitor.py` touch the
Alpaca orders API. All other modules are purely analytical.

---

## Data file ownership

| File | Owner (writes) | Consumers (reads) |
|---|---|---|
| `iv_history.json` | iv_backfill, iv_tracker | iv_tracker |
| `iv_rank_cache.json` | iv_backfill, iv_tracker | screener, analyzer |
| `options_candidates.json` | screener | selector, analyzer |
| `options_picks_history.json` | screener | humans |
| `options_pending_entries.json` | selector | executor |
| `positions_state.json` | executor (create), monitor (update) | monitor, analyzer, optimizer |
| `options_signal_quality.json` | analyzer | optimizer |
| `options_improvement_report.json` | optimizer | humans |
| `options_config.json` | human + optimizer | all modules |
