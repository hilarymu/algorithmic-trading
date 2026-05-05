"""
screener.py
===========
Stock Screener -- Mean Reversion on S&P 500.

Pipeline (runs every Monday ~06:00 UTC via Task Scheduler)
----------------------------------------------------------
1. Fetch current S&P 500 constituents from Wikipedia (fallback: hardcoded 50)
2. Batch-fetch 365 days of daily OHLCV bars from Alpaca (multi-symbol endpoint,
   ~17 API requests for the full universe instead of ~500 individual calls)
3. Compute RSI(14), Bollinger Bands(20, 2sd), 200-day MA, 20-day volume ratio
4. Apply configurable filters from screener_config.json
5. Rank passing symbols by composite score (lower = stronger signal)
6. Write screener_results.json  (consumed by entry_executor, dashboard)
7. Write pending_entries.json   (consumed by entry_executor at 09:15 ET)

Filters (read from screener_config.json -- auto-tuned by rsi_loop/optimizer.py)
--------------------------------------------------------------------------------
  require_rsi_oversold         RSI(14) < rsi_oversold threshold
  require_below_lower_bb       Price below lower Bollinger Band
  require_above_200ma          Price above 200-day MA (optional -- off in corrections)
  require_volume_confirmation  Volume > volume_ratio_min x 20-day avg

Composite score (lower = higher priority)
-----------------------------------------
  rsi_score  = rsi / 100
  bb_score   = 1 + (price - bb_lower) / bb_lower   (negative = further below band)
  vol_score  = 1 / max(vol_ratio, 0.01)
  composite  = rsi_weight * rsi_score + bb_weight * bb_score + vol_weight * vol_score

Outputs
-------
  screener_results.json   -- top picks + radar list (2-3 filters)
  pending_entries.json    -- entries queued for executor (when auto_entry.enabled=True)

Dependency chain
----------------
  screener.py -> pending_entries.json -> entry_executor.py -> positions_state.json
  screener.py -> screener_results.json -> rsi_loop/performance_tracker.py
"""

import json
import math
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

# -- Paths (relative to this file -- no hardcoded user paths) --
PROJECT_DIR = Path(__file__).parent
CONFIG_PATH  = PROJECT_DIR / "alpaca_config.json"
SCREENER_CONFIG_PATH = PROJECT_DIR / "screener_config.json"

# -- Load configs --
with open(CONFIG_PATH) as f:
    cfg = json.load(f)

with open(SCREENER_CONFIG_PATH) as f:
    scfg = json.load(f)

API_KEY    = cfg["api_key"]
API_SECRET = cfg["api_secret"]
DATA_URL   = "https://data.alpaca.markets/v2"

IND     = scfg["indicators"]
FILT    = scfg["filters"]
SCORE_W = scfg["scoring"]

# Top-level config keys (flat in screener_config.json)
MAX_POSITIONS = scfg["max_positions"]
MIN_PRICE     = scfg["min_price"]
MIN_AVG_VOL   = scfg["min_avg_volume"]

BARS_NEEDED = 220   # 200 for MA + 20 buffer


# -- S&P 500 tickers --

def get_sp500_tickers() -> list[str]:
    """
    Fetch current S&P 500 constituents from Wikipedia.

    Uses two regex patterns to handle both external-link and plain-link
    table formats. Replaces '/' with '.' for Alpaca ticker format (e.g. BRK/B -> BRK.B).

    Returns
    -------
    list[str]
        Deduplicated list of ~500 ticker symbols.
        Falls back to get_sp500_fallback() if Wikipedia returns < 400 symbols.
    """
    import re
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            html = r.read().decode("utf-8")
    except Exception as e:
        print(f"  Wikipedia fetch failed: {e}. Using fallback list.")
        return get_sp500_fallback()

    patterns = [
        r'<td><a[^>]*href="https?://[^"]*">([A-Z]{1,5}(?:\.[A-Z])?)</a>',
        r'<td><a[^>]*>([A-Z]{1,5}(?:\.[A-Z])?)</a>',
    ]
    tickers = []
    for pat in patterns:
        found = re.findall(pat, html)
        tickers.extend(found)

    seen = set()
    clean = []
    for t in tickers:
        t = t.replace("/", ".")
        if t not in seen:
            seen.add(t)
            clean.append(t)

    if len(clean) < 400:
        print(f"  Wikipedia returned {len(clean)} tickers (expected ~500). Using fallback.")
        return get_sp500_fallback()

    print(f"  Found {len(clean)} S&P 500 tickers from Wikipedia.")
    return clean


def get_sp500_fallback() -> list[str]:
    """
    Emergency fallback: top-50 S&P 500 names by market cap.

    Note: This is used only when Wikipedia is unreachable. Screening is limited
    to these 50 symbols; the full 500 will be scanned on the next successful run.
    """
    return [
        "AAPL","MSFT","NVDA","AMZN","META","GOOGL","GOOG","TSLA","BRK.B","JPM",
        "LLY","V","MA","UNH","XOM","JNJ","AVGO","PG","HD","COST","MRK","CVX",
        "WMT","ABBV","KO","PEP","NFLX","BAC","CRM","TMO","MCD","CSCO","ACN",
        "ABT","LIN","ORCL","TXN","DHR","NEE","PM","AMD","NKE","QCOM","RTX",
        "AMGN","SPGI","MS","HON","UNP","LOW",
    ]


# -- Alpaca multi-symbol bar fetch --

MULTI_BARS_URL = f"{DATA_URL}/stocks/bars"
BATCH_SIZE     = 30   # symbols per request (Alpaca recommended ceiling)

def fetch_bars_bulk(symbols: list[str], start: str, end: str) -> dict[str, list]:
    """
    Fetch daily bars for all symbols using Alpaca's multi-symbol endpoint.

    Makes ~17 requests for a full S&P 500 scan instead of ~500 individual calls.
    Handles pagination (next_page_token) automatically.
    Retries up to 3x on network timeouts or HTTP 429 (rate limit).

    Parameters
    ----------
    symbols : list[str]
        Ticker symbols to fetch.
    start, end : str
        ISO date strings (YYYY-MM-DD) for the bar window.

    Returns
    -------
    dict[str, list[dict]]
        {symbol: [bar_dicts sorted ascending by timestamp]}
        Symbols with no data are absent from the dict.
    """
    headers = {
        "APCA-API-KEY-ID":     API_KEY,
        "APCA-API-SECRET-KEY": API_SECRET,
    }
    all_bars     = {}
    total_batches = -(-len(symbols) // BATCH_SIZE)   # ceiling division

    for batch_num, i in enumerate(range(0, len(symbols), BATCH_SIZE), start=1):
        batch = symbols[i : i + BATCH_SIZE]

        params = {
            "symbols":    ",".join(batch),
            "timeframe":  "1Day",
            "start":      start,
            "end":        end,
            "limit":      10000,   # total across all symbols in batch; 30 syms × 252 bars ≈ 7560
            "feed":       "iex",
            "adjustment": "all",
        }
        base_url        = MULTI_BARS_URL + "?" + urllib.parse.urlencode(params)
        batch_bars      = {s: [] for s in batch}
        next_page_token = None

        while True:
            url = base_url
            if next_page_token:
                url += f"&page_token={urllib.parse.quote(next_page_token)}"

            req     = urllib.request.Request(url, headers=headers)
            attempt = 0
            while attempt < 3:
                try:
                    with urllib.request.urlopen(req, timeout=60) as r:
                        data = json.loads(r.read())
                    bars_data       = data.get("bars") or {}
                    next_page_token = data.get("next_page_token")
                    for sym, bars in bars_data.items():
                        if bars:
                            batch_bars[sym].extend(bars)
                    break
                except (urllib.error.URLError, TimeoutError, OSError):
                    # Network timeout / SSL error -- retry with exponential backoff
                    attempt += 1
                    if attempt < 3:
                        time.sleep(2 ** attempt)
                    else:
                        raise
                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        time.sleep(2 ** attempt)
                        attempt += 1
                    else:
                        raise
            if not next_page_token:
                break

        for sym, bars in batch_bars.items():
            if bars:
                bars.sort(key=lambda b: b["t"])
                all_bars[sym] = bars

        print(f"  Batch {batch_num}/{total_batches}: "
              f"{sum(1 for s in batch if all_bars.get(s))}/{len(batch)} symbols OK", end="\r")
        time.sleep(0.2)   # polite rate-limit pause between batches

    print(f"\n  Fetched data for {len(all_bars)}/{len(symbols)} symbols via batch API.")
    return all_bars


# -- Indicator calculations --

def calc_rsi(closes: list[float], period: int = 14) -> float | None:
    """
    Wilder's smoothed RSI.

    Seeds with a simple average over the first ``period`` gains/losses, then
    applies Wilder's exponential smoothing for subsequent bars.

    Returns None if fewer than period+1 bars are available.
    """
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i-1]
        gains.append(max(delta, 0))
        losses.append(max(-delta, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calc_bollinger(closes: list[float], period: int = 20,
                   std_mult: float = 2.0) -> tuple[float | None, float | None, float | None]:
    """
    Bollinger Bands over the last ``period`` closes.

    Returns (upper, middle, lower) rounded to 4dp, or (None, None, None)
    if insufficient data. Uses population standard deviation (divides by N).
    """
    if len(closes) < period:
        return None, None, None
    window   = closes[-period:]
    sma      = sum(window) / period
    variance = sum((x - sma) ** 2 for x in window) / period
    std      = math.sqrt(variance)
    upper    = sma + std_mult * std
    lower    = sma - std_mult * std
    return round(upper, 4), round(sma, 4), round(lower, 4)


def calc_sma(closes: list[float], period: int) -> float | None:
    """Simple moving average over the last ``period`` closes. Returns None if insufficient data."""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def calc_volume_ratio(volumes: list[float], period: int = 20) -> float | None:
    """
    Today's volume / 20-day average (excluding today).

    Uses volumes[-period-1:-1] as the average window so today's spike
    does not inflate the baseline.
    Returns None if insufficient data or zero average.
    """
    if len(volumes) < period + 1:
        return None
    avg_vol = sum(volumes[-period-1:-1]) / period
    if avg_vol == 0:
        return None
    return round(volumes[-1] / avg_vol, 2)


# -- Score a single stock --

def score_stock(symbol: str, bars: list[dict], strict: bool = True) -> dict | None:
    """
    Score a stock against the screener filters.

    Parameters
    ----------
    symbol : str
        Ticker symbol (for the output record only -- not used in calculations).
    bars : list[dict]
        Daily OHLCV bar dicts (keys: o, h, l, c, v, t), sorted ascending.
    strict : bool
        True  -- return None unless all required filters pass (actionable picks).
        False -- always return a record with filter flags set (for radar/watchlist).

    Returns
    -------
    dict or None
        Score record with price, RSI, Bollinger levels, MA200, vol_ratio,
        composite_score, and per-filter boolean flags.
        None if data is insufficient or all required filters fail (strict=True).

    Notes
    -----
    Composite score is LOWER for stronger setups (sorts ascending for top picks).
    """
    if len(bars) < BARS_NEEDED:
        return None

    closes     = [b["c"] for b in bars]
    volumes    = [b["v"] for b in bars]
    current    = closes[-1]
    avg_vol_20 = sum(volumes[-21:-1]) / 20

    # Basic quality gates (always applied, not configurable)
    if current < MIN_PRICE:
        return None
    if avg_vol_20 < MIN_AVG_VOL:
        return None

    # Compute indicators
    rsi                        = calc_rsi(closes, IND["rsi_period"])
    bb_upper, bb_mid, bb_lower = calc_bollinger(closes, IND["bb_period"], IND["bb_std"])
    ma200                      = calc_sma(closes, IND["ma_trend_period"])
    vol_ratio                  = calc_volume_ratio(volumes, 20)

    if any(v is None for v in [rsi, bb_lower, ma200, vol_ratio]):
        return None

    # Evaluate each filter
    f_above200 = current > ma200
    f_below_bb = current < bb_lower
    f_rsi      = rsi < IND["rsi_oversold"]
    f_volume   = vol_ratio >= IND["volume_ratio_min"]
    filters_passed = sum([f_above200, f_below_bb, f_rsi, f_volume])

    # Apply strict filter gates (each filter is independently togglable in config)
    if strict:
        if FILT.get("require_above_200ma")        and not f_above200: return None
        if FILT.get("require_below_lower_bb")     and not f_below_bb: return None
        if FILT.get("require_rsi_oversold")       and not f_rsi:      return None
        if FILT.get("require_volume_confirmation") and not f_volume:   return None

    # Composite score -- lower = more oversold / higher priority
    rsi_score = rsi / 100.0
    bb_dist   = (current - bb_lower) / bb_lower    # negative means below band
    bb_score  = bb_dist + 1                         # lower = further below band
    vol_score = 1.0 / max(vol_ratio, 0.01)

    composite = (
        SCORE_W["rsi_weight"]         * rsi_score +
        SCORE_W["bb_distance_weight"] * bb_score  +
        SCORE_W["volume_weight"]      * vol_score
    )

    return {
        "symbol":          symbol,
        "price":           round(current, 2),
        "rsi":             rsi,
        "bb_upper":        bb_upper,
        "bb_mid":          bb_mid,
        "bb_lower":        bb_lower,
        "ma200":           round(ma200, 2),
        "vol_ratio":       vol_ratio,
        "pct_below_bb":    round(bb_dist * 100, 2),
        "pct_above_200ma": round((current / ma200 - 1) * 100, 2),
        "composite_score": round(composite, 4),
        "filters": {
            "above_200ma":  f_above200,
            "below_bb":     f_below_bb,
            "rsi_oversold": f_rsi,
            "volume_ok":    f_volume,
        },
        "filters_passed": filters_passed,
    }


# -- Main screener run --

def run_screener() -> list[dict]:
    """
    Run the full screener pipeline and write output files.

    Steps
    -----
    1. Fetch S&P 500 tickers from Wikipedia (or fallback list)
    2. Fetch 365 days of daily bars in batch via Alpaca multi-symbol endpoint
    3. Score each symbol; separate strict picks from radar (2-3 filters)
    4. Write screener_results.json
    5. Write pending_entries.json (when auto_entry.enabled=True in config)

    Returns
    -------
    list[dict]
        The top MAX_POSITIONS actionable picks (also written to screener_results.json).
    """
    now   = datetime.now(timezone.utc)
    # Use yesterday as end date to avoid partial intraday bars skewing volume ratios
    end   = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    start = (now - timedelta(days=366)).strftime("%Y-%m-%d")

    print("\n=== Stock Screener - Mean Reversion / S&P 500 ===")
    print(f"Run time : {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Universe : {scfg['universe']}")
    print(f"Filters  : RSI<{IND['rsi_oversold']} | Below Lower BB | Above 200MA | Vol>{IND['volume_ratio_min']}x\n")

    print("Step 1: Loading tickers...")
    tickers = get_sp500_tickers()

    print("Step 2: Fetching daily bars (this takes ~30s)...")
    try:
        all_bars = fetch_bars_bulk(tickers, start, end)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"  ERROR: Alpaca data API unreachable after retries: {e}")
        print("  Screener aborted -- pending_entries.json not updated.")
        print("  Re-run manually once network is restored.")
        return []
    print(f"  Bars received for {len(all_bars)} symbols.")

    print("Step 3: Scoring stocks...")
    candidates = []   # passed all required filters
    radar      = []   # passed 2+ filters (approaching setup)
    skipped    = 0

    for sym, bars in all_bars.items():
        result = score_stock(sym, bars, strict=True)
        if result:
            candidates.append(result)
        else:
            relaxed = score_stock(sym, bars, strict=False)
            if relaxed and relaxed["filters_passed"] >= 2:
                radar.append(relaxed)
            else:
                skipped += 1

    candidates.sort(key=lambda x: x["composite_score"])
    radar.sort(key=lambda x: (-x["filters_passed"], x["composite_score"]))
    top = candidates[:MAX_POSITIONS]

    # -- Print results --
    print(f"\n  Screened : {len(all_bars)} stocks")
    print(f"  Passed   : {len(candidates)} passed all 4 filters (actionable)")
    print(f"  Radar    : {len(radar)} passed 2-3 filters (watching)")

    def filter_flags(s):
        """Build MBRV flag string: M=above200MA, B=belowBB, R=RSI, V=Volume."""
        f = s["filters"]
        return "".join([
            "M" if f["above_200ma"]   else "-",
            "B" if f["below_bb"]      else "-",
            "R" if f["rsi_oversold"]  else "-",
            "V" if f["volume_ok"]     else "-",
        ])

    hdr = (f"  {'#':<3} {'Symbol':<7} {'Price':>7}  {'RSI':>5}  "
           f"{'BBlow':>8}  {'%vBB':>6}  {'MA200':>8}  {'%vMA':>6}  "
           f"{'Vol':>5}x  {'MBRV':>4}  {'Score':>7}")
    div = f"  {'-'*76}"

    print(f"\n{'='*80}")
    print(f" TOP {MAX_POSITIONS} ACTIONABLE  [{now.strftime('%Y-%m-%d')}]  "
          f"(MBRV = 200MA / BelowBB / RSI / Volume)")
    print(f"{'='*80}")

    if not top:
        print("  No stocks passed all 4 filters today -- market not offering clean setups.")
    else:
        print(hdr); print(div)
        for i, s in enumerate(top, 1):
            print(f"  {i:<3} {s['symbol']:<7} ${s['price']:>6.2f}  "
                  f"{s['rsi']:>5.1f}  "
                  f"${s['bb_lower']:>7.2f}  "
                  f"{s['pct_below_bb']:>+6.1f}%  "
                  f"${s['ma200']:>7.2f}  "
                  f"{s['pct_above_200ma']:>+5.1f}%  "
                  f"{s['vol_ratio']:>5.1f}x  "
                  f"{filter_flags(s):>4}  "
                  f"{s['composite_score']:>7.4f}")

    if radar:
        print(f"\n  -- RADAR (2-3 filters) --")
        print(hdr); print(div)
        for s in radar[:8]:
            print(f"  {'~':<3} {s['symbol']:<7} ${s['price']:>6.2f}  "
                  f"{s['rsi']:>5.1f}  "
                  f"${s['bb_lower']:>7.2f}  "
                  f"{s['pct_below_bb']:>+6.1f}%  "
                  f"${s['ma200']:>7.2f}  "
                  f"{s['pct_above_200ma']:>+5.1f}%  "
                  f"{s['vol_ratio']:>5.1f}x  "
                  f"{filter_flags(s):>4}  "
                  f"{s['composite_score']:>7.4f}")

    print(f"{'='*80}")

    # -- Save screener results --
    output = {
        "run_date":     now.strftime("%Y-%m-%d"),
        "run_time_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "universe":     scfg["universe"],
        "screened":     len(all_bars),
        "passed":       len(candidates),
        "radar_count":  len(radar),
        "top_picks":    top,
        "radar":        radar[:10],
    }
    out_path = PROJECT_DIR / "screener_results.json"
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\n  Results saved to screener_results.json")

    # -- Write pending_entries.json for auto-entry executor --
    auto_cfg = scfg.get("auto_entry", {})
    if auto_cfg.get("enabled", False):
        review_hrs    = auto_cfg.get("review_window_hours", 3.25)
        position_size = auto_cfg.get("position_size_usd", 1000)
        max_entries   = auto_cfg.get("max_new_entries_per_week", 10)
        order_type    = auto_cfg.get("order_type", "market")

        # Entries execute at screener run time + review window
        # (e.g. 06:00 UTC + 3.25 hrs = 09:15 UTC = 09:15 ET = market open buffer)
        entry_at = now + timedelta(hours=review_hrs)

        pending = []
        for i, s in enumerate(top[:max_entries]):
            shares = max(1, int(position_size / s["price"]))
            pending.append({
                "rank":            i + 1,
                "symbol":          s["symbol"],
                "screened_price":  s["price"],
                "rsi":             s["rsi"],
                "pct_below_bb":    s["pct_below_bb"],
                "pct_above_200ma": s["pct_above_200ma"],
                "composite_score": s["composite_score"],
                "planned_shares":  shares,
                "planned_usd":     round(shares * s["price"], 2),
                "order_type":      order_type,
                "skip":            False,
                "_note":           "Set skip:true to exclude this entry before executor runs",
            })

        pending_output = {
            "generated_utc":     now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "executes_at_utc":   entry_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "position_size_usd": position_size,
            "status":            "pending",
            "_instructions": (
                "Review this file before executes_at_utc. "
                "Set skip:true on any symbol you do not want entered. "
                "Change planned_shares to override position size. "
                "Set status to 'cancelled' to block ALL entries this week."
            ),
            "entries": pending,
        }

        pending_path = PROJECT_DIR / "pending_entries.json"
        with open(pending_path, "w") as f:
            json.dump(pending_output, f, indent=2)

        if pending:
            print(f"  Pending entries written ({len(pending)} picks) -- "
                  f"executor runs at {entry_at.strftime('%H:%M UTC')}")
            print(f"  Edit pending_entries.json to skip any before then.")
        else:
            print(f"  No actionable picks -- pending_entries.json written empty.")

    print()
    return top


if __name__ == "__main__":
    run_screener()
