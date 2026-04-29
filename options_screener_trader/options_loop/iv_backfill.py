"""
iv_backfill.py
==============
One-time (re-runnable) historical backfill of iv_history.json.

Primary method: Alpaca historical options bars + Black-Scholes IV inversion.
  Requires OPRA subscription (not available on paper accounts).

Fallback method (auto-selected when OPRA unavailable):
  HV30 proxy — 30-day realized volatility from equity price history.
  Scaled per-symbol to match the current snapshot IV level from iv_tracker.
  Eliminates the 30-day wait while building enough history for IV rank.
  Self-corrects as daily iv_tracker accumulates real snapshot IV over time.

Algorithm (primary)
-------------------
1. Fetch 270 calendar days of daily equity prices for all universe symbols
2. For each (symbol, date) construct the historically-appropriate ATM call
   contract: nearest standard strike to that day's price, 3rd-Friday
   monthly expiry closest to 35 DTE from that date
3. Batch-fetch daily bars for every unique contract symbol via Alpaca's
   /v1beta1/options/bars endpoint (handles pagination)
4. Compute IV via Black-Scholes Newton-Raphson inversion from option
   mid-price, stock price, strike, and time-to-expiry
5. Merge new readings into iv_history.json — daily tracker data is never
   overwritten (daily run adds today on top)

Algorithm (HV30 fallback)
-------------------------
1. Fetch equity price history (same as step 1 above)
2. Compute rolling 30-day annualized realized volatility (HV30) per symbol
3. Scale HV30 per-symbol to match current snapshot IV (from iv_history.json)
   using the most recent overlapping dates as calibration anchors
4. Merge scaled proxy values into iv_history.json for any missing dates

Run
---
    py -3 options_loop/iv_backfill.py           # incremental (skip dates already present)
    py -3 options_loop/iv_backfill.py --force   # full overwrite

Integration
-----------
options_main.py calls run(universe) automatically when iv_history.json has
fewer than 5 symbols (first launch), then skips on subsequent daily runs.

API cost
--------
Roughly 14 equity-bars batches + ~170 options-bars batches = ~184 API calls.
At 0.15 s/call: < 30 seconds total.
"""

import json
import math
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

# ── Import shared helpers from iv_tracker (same package) ──────────────────────
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))

from iv_tracker import (          # noqa: E402  (after sys.path insert)
    _get,
    _nearest_strikes,
    _standard_increment,
    DATA_BASE,
    IV_HIST_PATH,
    IV_RANK_WINDOW,
    MIN_IV_HISTORY,
    CALL_DELAY,
    load_iv_history,
    save_iv_history,
    get_universe,
)

# ── Constants ──────────────────────────────────────────────────────────────────
TARGET_DTE       = 35
BACKFILL_DAYS    = 270        # calendar days back (~252 trading days)
BATCH_EQUITY     = 100        # symbols per equity bars request
BATCH_OPTIONS    = 35         # symbols per options bars request (35×260 < 10 000 rows)
RISK_FREE_RATE   = 0.05       # constant r — sufficient for IV rank
MIN_VOLUME       = 1          # skip bars with zero reported volume
MIN_OPTION_PRICE = 0.01       # ignore sub-penny prices
MAX_IV           = 5.0        # 500% IV cap — above this is a data artefact
MIN_T            = 1 / 365.25 # 1 calendar day minimum time-to-expiry


# ══════════════════════════════════════════════════════════════════════════════
#  Black-Scholes IV inversion — pure Python, no scipy
# ══════════════════════════════════════════════════════════════════════════════

def _norm_cdf(x: float) -> float:
    """Standard normal CDF via math.erf."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def _norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def bs_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes European call price.  Returns intrinsic value when T ~ 0."""
    if T < MIN_T or sigma <= 0:
        return max(0.0, S - K)
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / sq
    d2 = d1 - sq
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def bs_vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """dC/dσ — used as Newton-Raphson step denominator."""
    if T < MIN_T or sigma <= 0:
        return 0.0
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / sq
    return S * _norm_pdf(d1) * math.sqrt(T)


def implied_volatility(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float = RISK_FREE_RATE,
    tol: float = 1e-6,
    max_iter: int = 100,
) -> float | None:
    """
    Newton-Raphson Black-Scholes IV inversion for a European call.

    Returns the annualised implied volatility (0–MAX_IV) or None when:
    - inputs are invalid (T ≤ 0, price ≤ 0, etc.)
    - market_price is below intrinsic value (data error)
    - the solver does not converge within max_iter steps
    """
    if T < MIN_T or market_price < MIN_OPTION_PRICE or S <= 0 or K <= 0:
        return None
    intrinsic = max(0.0, S - K)
    if market_price <= intrinsic + 1e-8:
        return None   # below intrinsic — not a valid option price

    sigma = 0.30      # typical starting guess; converges in < 10 iterations
    for _ in range(max_iter):
        price = bs_call_price(S, K, T, r, sigma)
        vega  = bs_vega(S, K, T, r, sigma)
        if vega < 1e-10:
            break
        diff  = price - market_price
        sigma -= diff / vega
        if sigma < 1e-4:
            sigma = 1e-4          # floor — prevents negative sigma
        if sigma > MAX_IV:
            return None           # diverged; skip this data point
        if abs(diff) < tol:
            return round(sigma, 6)
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  Trading-day helpers
# ══════════════════════════════════════════════════════════════════════════════

def get_trading_days(days_back: int = BACKFILL_DAYS) -> list[date]:
    """Return sorted list of weekdays going back `days_back` calendar days."""
    end   = date.today()
    start = end - timedelta(days=days_back)
    return [
        start + timedelta(n)
        for n in range((end - start).days + 1)
        if (start + timedelta(n)).weekday() < 5
    ]


def _hist_target_expiry(as_of_date: date) -> date | None:
    """
    Return the 3rd-Friday monthly expiry closest to 35 DTE from as_of_date.
    Uses a 6-month forward search (wider than the live tracker) to always
    find a result for any historical date.
    """
    candidates = []
    for month_offset in range(6):
        y = as_of_date.year
        m = as_of_date.month + month_offset
        while m > 12:
            m -= 12
            y += 1
        d = date(y, m, 1)
        while d.weekday() != 4:          # advance to first Friday
            d += timedelta(days=1)
        third_friday = d + timedelta(weeks=2)
        dte = (third_friday - as_of_date).days
        if dte >= 14:                    # skip expirations that are too close
            candidates.append((abs(dte - TARGET_DTE), third_friday))
    candidates.sort()
    return candidates[0][1] if candidates else None


# ══════════════════════════════════════════════════════════════════════════════
#  Alpaca data fetchers (paginated)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_stock_bar_history(
    symbols: list[str],
    start_date: date,
    end_date: date,
) -> dict[str, dict[str, float]]:
    """
    Batch-fetch daily close prices for equity symbols.
    Returns {symbol: {date_str: close_price}}.
    """
    eligible = [s for s in symbols if "." not in s and "/" not in s]
    result: dict[str, dict[str, float]] = {}
    start_s, end_s = start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")

    print(f"  [backfill] equity bars: {len(eligible)} symbols  {start_s} -> {end_s}")

    for i in range(0, len(eligible), BATCH_EQUITY):
        batch = eligible[i : i + BATCH_EQUITY]
        base_url = (
            f"{DATA_BASE}/v2/stocks/bars?symbols={','.join(batch)}"
            f"&timeframe=1Day&start={start_s}&end={end_s}&feed=iex&limit=10000"
        )
        page_token = None
        while True:
            url  = base_url + (f"&page_token={page_token}" if page_token else "")
            data = _get(url)
            if not data:
                break
            for sym, bars in data.get("bars", {}).items():
                if sym not in result:
                    result[sym] = {}
                for bar in bars:
                    try:
                        result[sym][bar["t"][:10]] = float(bar["c"])
                    except (KeyError, ValueError):
                        pass
            page_token = data.get("next_page_token")
            if not page_token:
                break
            time.sleep(CALL_DELAY)
        time.sleep(CALL_DELAY)

    print(f"  [backfill] equity history: {len(result)} symbols retrieved")
    return result


def fetch_options_bar_history(
    contract_symbols: set[str],
    start_date: date,
    end_date: date,
) -> dict[str, dict[str, float]]:
    """
    Batch-fetch daily bars for options contracts.
    Returns {contract_symbol: {date_str: mid_price}}.
    Mid-price = vwap if available, else (high+low)/2, else close.
    """
    if not contract_symbols:
        return {}

    sym_list = sorted(contract_symbols)
    result: dict[str, dict[str, float]] = {}
    start_s, end_s = start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")
    total   = len(sym_list)
    batches = (total + BATCH_OPTIONS - 1) // BATCH_OPTIONS

    print(f"  [backfill] options bars : {total} contracts, {batches} batches")

    for i in range(0, total, BATCH_OPTIONS):
        batch = sym_list[i : i + BATCH_OPTIONS]
        base_url = (
            f"{DATA_BASE}/v1beta1/options/bars?symbols={','.join(batch)}"
            f"&timeframe=1Day&start={start_s}&end={end_s}&limit=10000"
        )
        page_token = None
        while True:
            url  = base_url + (f"&page_token={page_token}" if page_token else "")
            data = _get(url)
            if not data:
                break
            for sym, bars in data.get("bars", {}).items():
                if sym not in result:
                    result[sym] = {}
                for bar in bars:
                    try:
                        if bar.get("v", 0) < MIN_VOLUME:
                            continue
                        mid = (bar.get("vw")
                               or ((bar["h"] + bar["l"]) / 2)
                               or bar["c"])
                        if mid and float(mid) >= MIN_OPTION_PRICE:
                            result[sym][bar["t"][:10]] = float(mid)
                    except (KeyError, ValueError, TypeError):
                        pass
            page_token = data.get("next_page_token")
            if not page_token:
                break
            time.sleep(CALL_DELAY)

        if (i // BATCH_OPTIONS) % 20 == 0 and i > 0:
            print(f"    {i}/{total} contracts processed...")
        time.sleep(CALL_DELAY)

    filled = sum(1 for v in result.values() if v)
    print(f"  [backfill] options bars : {filled}/{total} contracts with data")
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  Backfill core logic
# ══════════════════════════════════════════════════════════════════════════════

def build_date_contract_map(
    equity_history: dict[str, dict[str, float]],
    trading_days: list[date],
) -> tuple[dict[tuple[str, str], str], set[str]]:
    """
    For every (symbol, trading_day) determine the historically-appropriate
    ATM contract symbol.

    Returns
    -------
    date_contract_map : {(symbol, date_str): contract_sym}
    contract_symbols  : set of all unique contract symbols required
    """
    date_contract_map: dict[tuple[str, str], str] = {}
    contract_symbols:  set[str]                   = set()

    for sym, price_hist in equity_history.items():
        for td in trading_days:
            d_str = td.strftime("%Y-%m-%d")
            price = price_hist.get(d_str)
            if not price or price < 5.0:
                continue

            expiry = _hist_target_expiry(td)
            if not expiry or expiry <= td:
                continue

            inc      = _standard_increment(price)
            atm      = round(round(price / inc) * inc, 2)
            stk_int  = int(round(atm * 1000))
            date_str = expiry.strftime("%y%m%d")
            contract = f"{sym}{date_str}C{stk_int:08d}"

            date_contract_map[(sym, d_str)] = contract
            contract_symbols.add(contract)

    return date_contract_map, contract_symbols


def compute_backfill_iv(
    date_contract_map: dict[tuple[str, str], str],
    equity_history:    dict[str, dict[str, float]],
    options_bars:      dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    """
    For each (symbol, date) pair, compute IV via Black-Scholes inversion.

    Returns {symbol: {date_str: iv}}.
    """
    hist: dict[str, dict[str, float]] = {}
    hits = misses = 0

    for (sym, d_str), contract in date_contract_map.items():
        stock_price  = equity_history.get(sym, {}).get(d_str)
        option_price = options_bars.get(contract, {}).get(d_str)

        if not stock_price or not option_price:
            misses += 1
            continue

        try:
            # Contract format: {SYM}{YYMMDD}C{STRIKE_8DIGIT}
            strike   = int(contract[-8:]) / 1000.0
            exp_str  = contract[len(sym) : len(sym) + 6]   # YYMMDD
            exp_date = datetime.strptime(exp_str, "%y%m%d").date()
            as_of    = datetime.strptime(d_str, "%Y-%m-%d").date()
            T        = max(MIN_T, (exp_date - as_of).days / 365.25)
        except (ValueError, IndexError):
            misses += 1
            continue

        iv = implied_volatility(option_price, stock_price, strike, T)
        if iv and 0.01 <= iv <= MAX_IV:
            if sym not in hist:
                hist[sym] = {}
            hist[sym][d_str] = iv
            hits += 1
        else:
            misses += 1

    print(f"  [backfill] IV computed : {hits:,} readings, {misses:,} skipped "
          f"(no bar / below intrinsic / non-convergent)")
    return hist


# ══════════════════════════════════════════════════════════════════════════════
#  HV30 proxy (fallback when OPRA options bars are unavailable)
# ══════════════════════════════════════════════════════════════════════════════

def compute_hv30_series(
    equity_history: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    """
    Compute 30-day realized volatility (annualized) from equity price history.

    For each symbol, rolls a 30-return window across all available dates and
    stores annualized HV at each date.  Requires >= 32 price observations
    (31 log-returns gives one 30-return window, applied to the 32nd date).

    Returns {symbol: {date_str: hv30}} -- values in the same 0-to-5 range as
    the snapshot IV values stored by iv_tracker.
    """
    result: dict[str, dict[str, float]] = {}

    for sym, price_hist in equity_history.items():
        dates  = sorted(price_hist.keys())
        prices = [price_hist[d] for d in dates]

        if len(prices) < 32:        # need 31 log-returns for one full window
            continue

        # Log returns: r_i = ln(C_i / C_{i-1})
        log_rets: list[float] = []
        for i in range(1, len(prices)):
            p0, p1 = prices[i - 1], prices[i]
            log_rets.append(math.log(p1 / p0) if p0 > 0 and p1 > 0 else 0.0)

        # Rolling 30-day HV (sample std, annualized)
        sym_hv: dict[str, float] = {}
        for i in range(29, len(log_rets)):        # i is the last return in window
            window  = log_rets[i - 29 : i + 1]   # 30 returns
            n       = len(window)
            mean_r  = sum(window) / n
            var     = sum((r - mean_r) ** 2 for r in window) / max(n - 1, 1)
            hv30    = math.sqrt(var * 252)
            date_str = dates[i + 1]               # date of the last price in window
            if 0.005 <= hv30 <= MAX_IV:
                sym_hv[date_str] = round(hv30, 6)

        if sym_hv:
            result[sym] = sym_hv

    total_pts = sum(len(v) for v in result.values())
    print(f"  [backfill] HV30 series : {len(result)} symbols, {total_pts:,} readings")
    return result


def scale_hv30_to_iv(
    hv30_series:     dict[str, dict[str, float]],
    current_iv_hist: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    """
    Scale HV30 proxy values to match the IV level in current_iv_hist.

    For each symbol, the scaling factor k = mean(real_IV) / mean(HV30) is
    computed using dates where both series overlap (the 1-2 most recent days
    from iv_tracker).  Without overlap, k defaults to 1.3 (typical equity
    IV/HV ratio).  k is clamped to [0.5, 3.0] to limit data artefacts.

    Only dates NOT already in current_iv_hist are written (real IV is never
    overwritten).

    Returns {symbol: {date_str: scaled_iv}} for historical-only dates.
    """
    scaled: dict[str, dict[str, float]] = {}

    for sym, hv_hist in hv30_series.items():
        iv_hist = current_iv_hist.get(sym, {})

        # Calibrate scale from overlapping dates
        common = sorted(set(hv_hist) & set(iv_hist))
        if common:
            iv_vals = [iv_hist[d] for d in common]
            hv_vals = [hv_hist[d] for d in common]
            avg_iv  = sum(iv_vals) / len(iv_vals)
            avg_hv  = sum(hv_vals) / len(hv_vals)
            k = (avg_iv / avg_hv) if avg_hv > 0 else 1.3
            k = max(0.5, min(3.0, k))          # sanity clamp
        else:
            k = 1.3                             # market-wide typical IV/HV ratio

        # Write scaled values for historical dates only
        sym_out: dict[str, float] = {}
        for d, hv in hv_hist.items():
            if d not in iv_hist:               # never overwrite real snapshot IV
                sv = round(hv * k, 6)
                if 0.01 <= sv <= MAX_IV:
                    sym_out[d] = sv

        if sym_out:
            scaled[sym] = sym_out

    filled     = len(scaled)
    total_pts  = sum(len(v) for v in scaled.values())
    print(f"  [backfill] Scaled proxy: {filled} symbols, {total_pts:,} proxy-IV readings")
    return scaled


# ══════════════════════════════════════════════════════════════════════════════
#  Main entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(force: bool = False, universe: list[str] | None = None) -> dict | None:
    """
    Backfill iv_history.json from Alpaca historical options bars.

    Parameters
    ----------
    force    : overwrite existing history entirely (default: incremental)
    universe : symbol list; defaults to existing history symbols or fetched universe
    """
    print(f"\n{'='*60}")
    print(f" IV Backfill  (force={force})")
    print(f"{'='*60}")

    # ── Existing history ───────────────────────────────────────────────────
    existing = {} if force else load_iv_history()
    if existing:
        print(f"  Existing : {len(existing)} symbols, "
              f"{sum(len(v) for v in existing.values()):,} readings")

    # ── Symbol list ────────────────────────────────────────────────────────
    if universe is None:
        universe = list(existing.keys()) if existing else get_universe()
    if not universe:
        print("  ERROR: no symbols to backfill")
        return None
    print(f"  Symbols  : {len(universe)}")

    # ── Check if anything is actually missing ──────────────────────────────
    today        = date.today()
    start_date   = today - timedelta(days=BACKFILL_DAYS)
    trading_days = get_trading_days(BACKFILL_DAYS)

    if not force and existing:
        sample_syms = list(existing.keys())[:20]
        sample_days = [td.strftime("%Y-%m-%d") for td in trading_days[-30:]]
        missing = any(
            d not in existing.get(sym, {})
            for sym in sample_syms
            for d in sample_days
        )
        if not missing:
            print("  History already complete. Use --force to overwrite.")
            return {"skipped": True}

    print(f"  Date range : {start_date} -> {today} "
          f"({len(trading_days)} trading days)")

    # ── Step 1: equity price history ───────────────────────────────────────
    equity_history = fetch_stock_bar_history(universe, start_date, today)
    if not equity_history:
        print("  ERROR: equity history fetch failed. Aborting.")
        return None

    # ── Step 2: (symbol, date) -> contract mapping ─────────────────────────
    print(f"\n  Building historical contract map...")
    date_contract_map, contract_symbols = build_date_contract_map(
        equity_history, trading_days
    )
    print(f"  Unique contracts   : {len(contract_symbols):,}")
    print(f"  (symbol,date) pairs: {len(date_contract_map):,}")

    # ── Step 3: options bar history (OPRA required) ────────────────────────
    print()
    options_bars = fetch_options_bar_history(contract_symbols, start_date, today)

    # ── Step 4: compute IV (primary) or HV30 proxy (fallback) ─────────────
    if options_bars:
        print(f"\n  Computing IV via Black-Scholes inversion...")
        backfilled = compute_backfill_iv(date_contract_map, equity_history, options_bars)
    else:
        print(f"\n  OPRA historical bars unavailable (403 or empty response).")
        print(f"  Falling back to HV30 proxy (30-day realized vol, per-symbol scaled).")
        existing_iv = {} if force else load_iv_history()
        hv30_raw    = compute_hv30_series(equity_history)
        backfilled  = scale_hv30_to_iv(hv30_raw, existing_iv)

    # ── Step 5: merge into history ─────────────────────────────────────────
    print(f"\n  Merging into iv_history.json...")
    merged = {} if force else existing
    new_readings = 0

    for sym, daily in backfilled.items():
        if sym not in merged:
            merged[sym] = {}
        for d_str, iv in daily.items():
            if d_str not in merged[sym]:    # never overwrite today's live reading
                merged[sym][d_str] = iv
                new_readings += 1

    save_iv_history(merged)

    total   = sum(len(v) for v in merged.values())
    ready   = sum(1 for v in merged.values() if len(v) >= MIN_IV_HISTORY)

    print(f"\n  Backfill complete:")
    print(f"    Symbols      : {len(merged)}")
    print(f"    New readings : {new_readings:,}")
    print(f"    Total        : {total:,}")
    print(f"    IV rank ready: {ready} symbols (>= {MIN_IV_HISTORY} days)")
    print()

    return {
        "symbols":      len(merged),
        "new_readings": new_readings,
        "total":        total,
        "with_iv_rank": ready,
    }


if __name__ == "__main__":
    result = run(force="--force" in sys.argv)
    # Rebuild IV rank cache so screener can run immediately after backfill
    if result and not result.get("skipped"):
        print("  Rebuilding iv_rank_cache.json...")
        from iv_tracker import load_iv_history, build_iv_rank_cache, IV_RANK_PATH
        import json as _json
        _hist  = load_iv_history()
        _cache = build_iv_rank_cache(_hist)
        with open(IV_RANK_PATH, "w") as _f:
            _json.dump(_cache, _f, indent=2)
        _ready = sum(1 for v in _cache.values() if v.get("sufficient_history"))
        print(f"  iv_rank_cache.json: {len(_cache)} symbols, {_ready} with rank")
