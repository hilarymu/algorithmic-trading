# Screener Trader — Data File Schemas

Reference for every JSON file in the project root. All files live at the project root unless noted.

---

## screener_config.json

**Purpose:** Strategy parameters. Read by screener, monitor, executor. Written by the RSI optimizer loop.  
**Producer:** rsi_loop/step4_apply_config.py (auto-tuned), or edited manually.  
**Consumer:** screener.py, monitor.py, entry_executor.py, rsi_loop/*.py

```json
{
  "indicators": {
    "rsi_period": 14,
    "rsi_oversold": 20,
    "bb_period": 20,
    "bb_std": 2.0,
    "ma_trend_period": 200,
    "volume_ratio_min": 1.0
  },
  "filters": {
    "require_above_200ma": false,
    "min_price": 5.0,
    "max_price": 500.0,
    "min_avg_volume": 500000
  },
  "scoring": {
    "rsi_weight": 0.5,
    "bb_distance_weight": 0.3,
    "volume_weight": 0.2
  },
  "execution": {
    "auto_entry": false,
    "position_size_usd": 500,
    "max_positions": 10
  },
  "exits": {
    "rsi_exit_threshold": 50,
    "hard_stop_pct": -0.10,
    "trail_activates_pct": 0.10,
    "trail_floor_pct": -0.05
  },
  "add_down_ladder": {
    "enabled": true,
    "rungs": [-0.15, -0.25, -0.35, -0.47]
  }
}
```

| Field | Default | Meaning |
|-------|---------|---------|
| `rsi_oversold` | 20 | RSI below this triggers oversold signal |
| `bb_std` | 2.0 | Bollinger Band width in standard deviations |
| `require_above_200ma` | false | If true, only picks above 200-day MA |
| `volume_ratio_min` | 1.0 | Volume must be this multiple of 20-day average |
| `auto_entry` | false | Set true to let executor place orders automatically |
| `position_size_usd` | 500 | Target dollars per new position |
| `rsi_exit_threshold` | 50 | RSI above this triggers recovery exit |
| `hard_stop_pct` | -0.10 | Exit at -10% from entry |
| `trail_activates_pct` | 0.10 | Trailing stop activates at +10% |
| `trail_floor_pct` | -0.05 | Trailing stop floor: -5% of entry |
| `rungs` | [-0.15,-0.25,-0.35,-0.47] | Add-down levels as fraction of entry price |

---

## screener_results.json

**Purpose:** Latest screener output — top picks and radar candidates.  
**Producer:** screener.py (Monday 06:00)  
**Consumer:** entry_executor.py, dashboard

```json
{
  "run_date": "2026-04-21",
  "run_time_utc": "2026-04-21T10:00:00Z",
  "universe": "S&P 500",
  "screened": 509,
  "passed": 2,
  "radar_count": 2,
  "top_picks": [
    {
      "rank": 1,
      "symbol": "AAPL",
      "price": 172.50,
      "rsi": 18.4,
      "bb_lower": 174.10,
      "bb_distance_pct": -0.009,
      "vol_ratio": 1.85,
      "composite_score": 12.3,
      "filters_passed": ["rsi", "bollinger", "volume"],
      "above_200ma": true
    }
  ],
  "radar": [
    {
      "symbol": "MSFT",
      "rsi": 28.1,
      "composite_score": 31.7,
      "note": "RSI approaching oversold"
    }
  ]
}
```

| Field | Meaning |
|-------|---------|
| `screened` | Number of S&P 500 symbols processed |
| `passed` | Symbols that cleared all active filters |
| `radar_count` | Symbols approaching thresholds (for monitoring) |
| `composite_score` | Weighted sum of RSI + BB + volume scores. Lower = stronger signal |
| `bb_distance_pct` | (price - bb_lower) / price. Negative = below lower band |
| `vol_ratio` | Today's volume / 20-day average volume |

---

## pending_entries.json

**Purpose:** Orders queued for the executor. Edit `skip: true` before 09:15 to exclude a pick.  
**Producer:** screener.py  
**Consumer:** entry_executor.py

```json
{
  "generated_utc": "2026-04-21T10:00:00Z",
  "executes_at_utc": "2026-04-21T13:15:00Z",
  "position_size_usd": 500,
  "status": "pending",
  "entries": [
    {
      "rank": 1,
      "symbol": "AAPL",
      "screened_price": 172.50,
      "planned_shares": 2,
      "skip": false
    },
    {
      "rank": 2,
      "symbol": "XYZ",
      "screened_price": 45.00,
      "planned_shares": 11,
      "skip": true
    }
  ],
  "executed": [
    {
      "symbol": "AAPL",
      "shares": 2,
      "order_id": "abc123",
      "executed_at": "2026-04-21T13:16:00Z"
    }
  ],
  "skipped": [
    {
      "symbol": "XYZ",
      "reason": "skip=true"
    }
  ]
}
```

| Field | Meaning |
|-------|---------|
| `status` | "pending" before execution, "executed" after |
| `skip` | Set to `true` to prevent executor from placing this order |
| `planned_shares` | Shares to buy: floor(position_size_usd / screened_price) |
| `executed` | Filled after executor runs — one entry per order placed |
| `skipped` | Filled after executor runs — one entry per skipped symbol |

---

## positions_state.json

**Purpose:** Live position tracker. Stores entry prices, stop levels, and add-down ladder state for each open position.  
**Producer:** entry_executor.py (creates entries), monitor.py (updates stops, ladder, closes)  
**Consumer:** monitor.py, dashboard

```json
{
  "strategy_defaults": {
    "hard_stop_pct": -0.10,
    "trail_activates_pct": 0.10,
    "trail_floor_pct": -0.05,
    "rsi_exit_threshold": 50,
    "ladder_rungs": [-0.15, -0.25, -0.35, -0.47]
  },
  "positions": {
    "AAPL": {
      "symbol": "AAPL",
      "entry_price": 172.50,
      "shares": 2,
      "entry_date": "2026-04-21",
      "entry_order_id": "abc123",
      "hard_stop": 155.25,
      "trail_active": false,
      "trail_high": null,
      "trail_floor": null,
      "ladder": {
        "-15%": { "price": 146.63, "filled": false, "shares": 2 },
        "-25%": { "price": 129.38, "filled": false, "shares": 4 },
        "-35%": { "price": 112.13, "filled": false, "shares": 8 },
        "-47%": { "price":  91.43, "filled": false, "shares": 16 }
      },
      "status": "open"
    }
  }
}
```

| Field | Meaning |
|-------|---------|
| `hard_stop` | Absolute price level for -10% stop (set at entry) |
| `trail_active` | True once price rises +10% above entry |
| `trail_high` | Highest price seen while trailing is active |
| `trail_floor` | Trailing stop level: trail_high × (1 + trail_floor_pct) |
| `ladder[rung].filled` | True once the add-down order at this level has been placed |
| `status` | "open" or "closed" |

---

## picks_history.json

**Purpose:** All tracked picks with forward returns filled in by the RSI loop. Used for signal quality analysis and the improvement report.  
**Producer:** screener.py (adds new picks), rsi_loop/step6_picks_tracker.py (fills in returns)  
**Consumer:** rsi_loop/step2_signal_quality.py, dashboard

```json
{
  "version": "1.0",
  "last_updated": "2026-04-21T10:00:00Z",
  "picks": [
    {
      "id": "AAPL-2026-04-21",
      "symbol": "AAPL",
      "screened_date": "2026-04-21",
      "entry_price": 172.50,
      "rsi": 18.4,
      "regime": "bull",
      "source": "screener",
      "filters_passed": ["rsi", "bollinger", "volume"],
      "returns": {
        "1d": 0.012,
        "5d": 0.034,
        "10d": null,
        "20d": null
      }
    }
  ]
}
```

| Field | Meaning |
|-------|---------|
| `id` | Unique pick ID: SYMBOL-YYYY-MM-DD |
| `regime` | Market regime at time of pick (bull/mild_correction/correction/recovery) |
| `returns.Nd` | N-day forward return (null until enough time has passed) |
| `filters_passed` | Which of the 4 filters this pick satisfied |

---

## market_regime.json

**Purpose:** Current market regime classification. Used by screener to adjust behavior and by signal quality analysis.  
**Producer:** rsi_loop/step1_regime.py (Monday 07:00)  
**Consumer:** screener.py, options_screener.py, dashboard

```json
{
  "computed_at": "2026-04-21T11:00:00Z",
  "regime": "bull",
  "spy_metrics": {
    "current_price": 520.15,
    "ma200": 488.70,
    "spy_vs_200ma_pct": 6.48,
    "spy_20d_return_pct": 10.67,
    "spy_5d_return_pct": 0.55
  },
  "vixy_metrics": {
    "current_price": 14.20,
    "vixy_20d_avg": 15.10,
    "vix_elevated": false
  }
}
```

| Regime | Condition |
|--------|-----------|
| `bull` | SPY > 200MA, VIXY not elevated |
| `mild_correction` | SPY slightly below 200MA or mild VIXY elevation |
| `correction` | SPY meaningfully below 200MA or high VIXY |
| `recovery` | SPY recovering from correction, trending up toward 200MA |

---

## signal_quality.json

**Purpose:** Historical pick performance statistics by market regime. Used by the optimizer to tune parameters.  
**Producer:** rsi_loop/step2_signal_quality.py  
**Consumer:** rsi_loop/step3_optimizer.py, dashboard

```json
{
  "computed_at": "2026-04-21T11:05:00Z",
  "total_samples": 1380,
  "by_regime": {
    "bull": {
      "n": 850,
      "hit_rate_5d": 0.68,
      "avg_5d_return": 0.031,
      "median_5d_return": 0.024,
      "sharpe_5d": 1.42
    },
    "mild_correction": {
      "n": 290,
      "hit_rate_5d": 0.55,
      "avg_5d_return": 0.018,
      "median_5d_return": 0.012,
      "sharpe_5d": 0.87
    },
    "correction": {
      "n": 180,
      "hit_rate_5d": 0.41,
      "avg_5d_return": -0.008,
      "median_5d_return": -0.003,
      "sharpe_5d": -0.22
    },
    "recovery": {
      "n": 60,
      "hit_rate_5d": 0.72,
      "avg_5d_return": 0.048,
      "median_5d_return": 0.039,
      "sharpe_5d": 1.95
    }
  }
}
```

| Field | Meaning |
|-------|---------|
| `hit_rate_5d` | Fraction of picks with positive 5-day return |
| `avg_5d_return` | Mean 5-day return across all picks in this regime |
| `sharpe_5d` | Return / StdDev ratio at 5-day horizon |

---

## improvement_report.json

**Purpose:** Narrative analysis of the latest optimization run. Generated by Gemini API if available; falls back to rule-based report.  
**Producer:** rsi_loop/step7_report.py  
**Consumer:** dashboard (displayed in Improvement Report panel)

```json
{
  "generated_at": "2026-04-21T11:10:00Z",
  "regime": "bull",
  "sample_count": 1380,
  "method": "data_derived (bull)",
  "source": "gemini_api",
  "changes_applied": [
    {
      "parameter": "rsi_oversold",
      "old_value": 25,
      "new_value": 20,
      "reason": "bull regime: tighter filter improves precision"
    }
  ],
  "report": "## RSI Loop Improvement Report\n\nMarket regime: bull...\n\n..."
}
```

| Field | Meaning |
|-------|---------|
| `source` | "gemini_api" or "rule_based" (fallback when no API key) |
| `changes_applied` | Parameters updated in this run (empty if none changed) |
| `report` | Markdown narrative — displayed in dashboard |

---

## config_history.json

**Purpose:** Audit log of every parameter change made by the optimizer. Used by the Config Evolution panel in the dashboard.  
**Producer:** rsi_loop/step8_log.py  
**Consumer:** dashboard

```json
{
  "history": [
    {
      "run_date": "2026-04-21",
      "run_time_utc": "2026-04-21T11:12:00Z",
      "regime": "bull",
      "sample_count": 1380,
      "changes": [
        {
          "parameter": "rsi_oversold",
          "old_value": 25,
          "new_value": 20
        }
      ],
      "config_snapshot": {
        "rsi_oversold": 20,
        "volume_ratio_min": 1.0,
        "rsi_weight": 0.5,
        "bb_distance_weight": 0.3,
        "volume_weight": 0.2
      }
    }
  ]
}
```

Each entry captures a full config snapshot so any prior configuration can be reconstructed.

---

## logs/

Log files are written to `logs\` and named by component and date:

| Pattern | Component |
|---------|-----------|
| `screener_YYYY-MM-DD.log` | screener.py |
| `executor_YYYY-MM-DD.log` | entry_executor.py |
| `monitor_YYYY-MM-DD.log` | monitor.py |
| `rsi_loop_YYYY-MM-DD.log` | rsi_loop/rsi_main.py |

Logs are plain text, one timestamped line per event. They accumulate — archive or delete old files periodically.
