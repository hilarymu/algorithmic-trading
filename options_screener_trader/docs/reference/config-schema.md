# Configuration Schema — `options_config.json`

All strategy parameters live in `options_config.json` at the project root.
The optimizer may update this file automatically when `auto_optimize.enabled: true`
and 50+ closed positions have accumulated.

---

## Full schema with defaults

```json
{
  "universe": ["SP500", "NASDAQ100"],
  "max_positions": 8,
  "primary_strategy": "CSP",

  "indicators": {
    "rsi_period": 14,
    "rsi_oversold": 25,
    "volume_ratio_min": 1.2,
    "iv_rank_min_sell": 40,
    "iv_rank_max_buy": 30
  },

  "contract_selection": {
    "target_dte_min": 21,
    "target_dte_max": 50,
    "target_dte_ideal": 35,
    "target_delta_csp": 0.30,
    "target_delta_call_buy": 0.50,
    "target_delta_call_sell": 0.25
  },

  "position_sizing": {
    "max_pct_nav_per_position": 0.07,
    "max_positions": 8,
    "contracts_per_position": 1
  },

  "exits": {
    "profit_target_pct": 0.50,
    "loss_limit_multiplier": 2.0,
    "close_at_dte": 21,
    "rsi_recovery_exit": 50
  },

  "filters": {
    "min_stock_price": 15.0,
    "min_avg_volume": 1000000,
    "min_open_interest": 500,
    "max_bid_ask_spread_pct": 0.15
  },

  "earnings": {
    "track_earnings": true,
    "earnings_window_days": 7,
    "_note": "earnings are a signal flag, not a hard block"
  },

  "auto_entry": {
    "enabled": true,
    "_note": "Phase 2 live — paper orders placed when executor runs"
  }
}
```

---

## Field reference

### Top level

| Field | Type | Default | Description |
|---|---|---|---|
| `universe` | array | `["SP500","NASDAQ100"]` | Symbol universes to screen. Symbols are resolved at runtime from Alpaca universe endpoints. |
| `max_positions` | int | `8` | Maximum simultaneous open positions. Screener stops adding candidates once this is reached. |
| `primary_strategy` | string | `"CSP"` | Default strategy for the selector. Overridden by regime/signal conditions. |

---

### `indicators`

Signal thresholds for candidate selection.

| Field | Type | Default | Optimizer? | Description |
|---|---|---|---|---|
| `rsi_period` | int | `14` | No | RSI calculation period in trading days. |
| `rsi_oversold` | float | `25` | No | RSI must be below this to qualify. Lower = stronger signal, fewer candidates. |
| `volume_ratio_min` | float | `1.2` | No | Today's volume must be ≥ this multiple of the 20-day average. Filters noise. |
| `iv_rank_min_sell` | int | `40` | **Yes** | Minimum IV rank to sell options. Optimizer raises this if the 40–55 bucket underperforms. Bounds: [20, 70]. |
| `iv_rank_max_buy` | int | `30` | No | Maximum IV rank for buying options (debit spreads, long calls). Cheap options only. |

**Tuning `iv_rank_min_sell`:** Raising reduces candidate count but improves premium quality.
Lowering adds candidates but risks selling at poor IV levels. The optimizer adjusts this
based on win-rate data in the 40–55 IV bucket.

---

### `contract_selection`

Controls which option contract the selector picks.

| Field | Type | Default | Optimizer? | Description |
|---|---|---|---|---|
| `target_dte_min` | int | `21` | No | Minimum days to expiry. Below this, gamma risk accelerates. |
| `target_dte_max` | int | `50` | No | Maximum days to expiry. Extended to `+14` if no expiry falls in window (calendar gap handling). |
| `target_dte_ideal` | int | `35` | No | Target DTE. Selector picks the expiry closest to this within the min/max window. |
| `target_delta_csp` | float | `0.30` | **Yes** | Target put delta for CSP. 0.30 = ~30% probability of expiring ITM. Lower = more OTM, safer but less premium. Bounds: [0.15, 0.45]. |
| `target_delta_call_buy` | float | `0.50` | No | Delta for ATM call leg in debit spreads. |
| `target_delta_call_sell` | float | `0.25` | No | Delta for OTM short call leg in credit spreads. |

**Tuning `target_delta_csp`:** 0.30 is standard. Optimizer lowers it (more OTM) if
loss-limit exits exceed 30% of total closes. Optimizer raises it (more premium) if
win rate exceeds 80% over 20+ trades.

---

### `position_sizing`

How much capital to allocate per position.

| Field | Type | Default | Description |
|---|---|---|---|
| `max_pct_nav_per_position` | float | `0.07` | Maximum 7% of account NAV per position. For a $100k paper account: max $7,000 collateral per CSP. |
| `max_positions` | int | `8` | Redundant with top-level — both are checked. |
| `contracts_per_position` | int | `1` | Fixed at 1 contract per entry during paper phase. Will be dynamic when sizing by NAV% in live phase. |

---

### `exits`

When to close open positions.

| Field | Type | Default | Optimizer? | Description |
|---|---|---|---|---|
| `profit_target_pct` | float | `0.50` | **Yes** | Close when premium has decayed to this fraction of entry. 0.50 = close at 50% profit. Bounds: [0.35, 0.70]. |
| `loss_limit_multiplier` | float | `2.0` | No | Close when premium has inflated to this multiple of entry. 2.0 = close at 2× entry price (100% loss). |
| `close_at_dte` | int | `21` | **Yes** | Close regardless of P&L when this many days to expiry remain. Avoids gamma risk. Bounds: [14, 35]. |
| `rsi_recovery_exit` | float | `50` | No | Close when underlying stock RSI recovers above this level. Signal has played out. |

**Tuning `profit_target_pct`:** 50% is a common standard (close at half-premium). Optimizer
raises this if avg hold days < 10 (closing too early, leaving theta). Lowers if avg hold > 30
(running into gamma zone).

**Tuning `close_at_dte`:** Optimizer raises this (exit earlier) if loss-limit exits cluster
near the DTE boundary, suggesting positions are being held too close to expiry.

---

### `filters`

Liquidity and quality gates applied per contract.

| Field | Type | Default | Description |
|---|---|---|---|
| `min_stock_price` | float | `15.0` | Minimum underlying price. Low-priced stocks have illiquid options. |
| `min_avg_volume` | int | `1,000,000` | Minimum 20-day average volume for the underlying equity. |
| `min_open_interest` | int | `500` | Minimum open interest on the specific option contract. Below this, liquidity is poor. |
| `max_bid_ask_spread_pct` | float | `0.15` | Maximum bid-ask spread as a fraction of mid price. 0.15 = 15%. Wide spreads eat into premium. |

---

### `earnings`

| Field | Type | Default | Description |
|---|---|---|---|
| `track_earnings` | bool | `true` | If true, check earnings calendar and flag candidates with earnings within the window. |
| `earnings_window_days` | int | `7` | Days ahead to check for earnings. Candidates within this window are flagged with `near_earnings: true`. |

Earnings proximity is a **signal flag, not a hard block**. The optimizer tracks whether
earnings-adjacent trades perform better or worse. See [ADR-003](../architecture/adr/003-earnings-as-signal-not-block.md).

---

### `auto_entry`

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | bool | `true` | If false, executor generates the pending entries JSON but does not call the Alpaca orders API. Useful for dry-run / review mode. |

**Production recommendation:** Start with `false` when setting up a new instance. Enable
after verifying a few days of candidates look correct.

---

## Auto-optimizer parameter bounds

The optimizer will never adjust a parameter outside these hard bounds:

| Parameter | Floor | Ceiling |
|---|---|---|
| `iv_rank_min_sell` | 20 | 70 |
| `target_delta_csp` | 0.15 | 0.45 |
| `profit_target_pct` | 0.35 | 0.70 |
| `close_at_dte` | 14 | 35 |

Changes are applied to `options_config.json` with an audit trail in
`options_improvement_report.json`.
