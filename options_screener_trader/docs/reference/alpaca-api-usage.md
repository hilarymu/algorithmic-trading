# Alpaca API Usage

This document covers every Alpaca API endpoint used by the pipeline, why it's used,
rate limits, known constraints, and error handling.

---

## Authentication

All requests use API key + secret from `alpaca_config.json` under the `paper` key.

```
Base URL (trading):  https://paper-api.alpaca.markets
Base URL (data):     https://data.alpaca.markets
```

Authentication header: `APCA-API-KEY-ID` and `APCA-API-SECRET-KEY`.

---

## Endpoints used

### 1. Equity historical bars
**Endpoint:** `GET /v2/stocks/{symbol}/bars`
**Data URL:** `https://data.alpaca.markets`
**Used by:** `iv_backfill.py`, `iv_tracker.py`, `options_screener.py`

**Purpose:**
- `iv_backfill.py`: Fetches 252 trading days of OHLCV bars per symbol to compute HV30 proxy IV.
- `iv_tracker.py`: Fetches recent price bars to check for today's snapshot.
- `options_screener.py`: Fetches ~30 bars to compute RSI and 20-day average volume.

**Key parameters:**
```
timeframe=1Day
start=<252 trading days ago>
end=<today>
limit=1000
adjustment=split  (split-adjusted prices)
```

**Rate limits:** 200 requests/minute (data endpoints). The backfill batches requests
and sleeps between batches to stay within limits.

**Works on paper accounts:** Yes, no special agreement required.

---

### 2. Options snapshot (single symbol)
**Endpoint:** `GET /v1beta1/options/snapshots/{underlying_symbol}`
**Data URL:** `https://data.alpaca.markets`
**Used by:** `iv_tracker.py`, `options_strategy_selector.py`

**Purpose:**
- `iv_tracker.py`: Fetches today's indicative IV for a symbol from the options market snapshot.
- `options_strategy_selector.py`: Fetches a live quote for a specific contract (by strike + expiry)
  to validate open interest, bid-ask spread, and get the limit price.

**Key parameters for iv_tracker:**
```
feed=indicative   (uses indicative/composite feed, not OPRA)
```

**Key parameters for strategy selector:**
```
type=put
expiration_date=<target expiry>
strike_price_gte=<target strike - buffer>
strike_price_lte=<target strike + buffer>
```

**Works on paper accounts:** Partially. Indicative IV snapshots work.
OPRA-sourced historical options bars (separate endpoint) require OPRA agreement — see below.

---

### 3. Options historical bars (OPRA) — BLOCKED on paper
**Endpoint:** `GET /v1beta1/options/bars`
**Status:** ❌ Returns 403 `{"message": "OPRA agreement is not signed"}` on all paper accounts.

**Affected module:** `iv_backfill.py` (attempted, then falls back to HV30 proxy).

**Workaround:** HV30 realized-volatility proxy computed from equity bars (endpoint 1).
See [ADR-008](../architecture/adr/008-hv30-proxy-iv-backfill.md).

**Resolution path:** The OPRA agreement is required for live (non-paper) accounts.
Once signed, `iv_backfill.py` will use real options data instead of the HV30 proxy.

---

### 4. Account information
**Endpoint:** `GET /v2/account`
**Trading URL:** `https://paper-api.alpaca.markets`
**Used by:** `options_executor.py`, `options_strategy_selector.py`

**Purpose:** Fetch portfolio NAV for position sizing (max 7% of NAV per position).

**Works on paper accounts:** Yes.

---

### 5. Options orders (place, modify, cancel)
**Endpoint:** `POST /v2/orders`
**Trading URL:** `https://paper-api.alpaca.markets`
**Used by:** `options_executor.py` (submit entries), `options_monitor.py` (submit exits)

**Purpose:** Place limit orders to sell-to-open (CSP entry) or buy-to-close (exit).

**Order structure for CSP entry:**
```json
{
  "symbol":        "TSCO260619P00195000",
  "qty":           "1",
  "side":          "sell",
  "type":          "limit",
  "time_in_force": "day",
  "limit_price":   "3.20",
  "order_class":   "simple"
}
```

**Order structure for exit:**
```json
{
  "symbol":        "TSCO260619P00195000",
  "qty":           "1",
  "side":          "buy",
  "type":          "limit",
  "time_in_force": "day",
  "limit_price":   "1.60"
}
```

**Works on paper accounts:** Yes. This is the primary purpose of paper trading.

**Note:** The executor is the **only** module that calls this endpoint.
All other modules are read-only with respect to live trading state.

---

### 6. Options positions
**Endpoint:** `GET /v2/positions`
**Trading URL:** `https://paper-api.alpaca.markets`
**Used by:** `options_monitor.py`, `options_executor.py`

**Purpose:** Fetch currently held option positions to cross-check against `positions_state.json`.

**Works on paper accounts:** Yes.

---

## Rate limit summary

| Endpoint group | Limit | Notes |
|---|---|---|
| Data endpoints | 200 req/min | Equity bars, options snapshots |
| Trading endpoints | 200 req/min | Orders, positions, account |
| Backfill (equity bars) | ~200 req/min | Batched with sleep between batches |

The backfill processes ~512 symbols × ~4 requests each = ~2,000 requests total.
At 200/min this takes ~10 minutes if not batched. Actual implementation batches
50 symbols per sleep cycle to stay within limits.

---

## Error handling conventions

Every API call is wrapped in `try/except`. On failure:

| Error | Behaviour |
|---|---|
| 403 (OPRA not signed) | `iv_backfill.py` logs warning, falls back to HV30 proxy |
| 429 (rate limit) | Sleep 60s, retry once |
| 504 / 503 (timeout) | Log error, skip symbol, continue |
| Network error | Log error, skip symbol, continue |
| Invalid contract symbol | Log warning, skip candidate |

No single failure aborts the pipeline. Each symbol and each step fails independently.

---

## Paper vs live differences

| Feature | Paper | Live |
|---|---|---|
| OPRA options historical bars | ❌ 403 | ✓ (requires OPRA agreement) |
| Indicative IV snapshots | ✓ | ✓ |
| Equity bars | ✓ | ✓ |
| Order placement | ✓ (simulated fills) | ✓ (real fills) |
| Account NAV | ✓ (paper balance) | ✓ (real balance) |
| Options positions | ✓ | ✓ |

**Fill simulation note:** Alpaca paper fills limit orders at or better than the limit price
based on the NBBO at time of order. Fills are not guaranteed — illiquid contracts may never fill.
The pipeline currently does not handle unfilled orders; they expire at day's end.
