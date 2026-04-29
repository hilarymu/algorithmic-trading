"""
iv_tracker.py
=============
Daily implied volatility tracker for the options screener universe.

Runs after market close (16:30 ET). For every symbol in the universe:
  1. Fetches current stock price (equity snapshot)
  2. Constructs the near-ATM call contract symbol directly (no contract
     lookup API call needed — symbol format is deterministic)
  3. Fetches IV from Alpaca options snapshot
  4. Appends to iv_history.json
  5. Computes IV rank from rolling 252-day window
  6. Flags symbols with earnings within EARNINGS_WINDOW days

Outputs:
  iv_history.json     -- daily IV per symbol (growing archive)
  iv_rank_cache.json  -- current IV rank per symbol (refreshed each run)

Data feed note:
  Phase 1 uses Alpaca's indicative (non-OPRA) feed — included with the
  paper account, sufficient for IV rank computation.
  Phase 2 (live execution): upgrade to OPRA for tight bid/ask on limit
  orders. Webull's OPRA subscription is the right instrument for that step.

Earnings note:
  Earnings flagged as a SIGNAL, not a hard block. The optimizer learns
  from options_picks_history.json whether earnings-window entries help or
  hurt. Flag: near_earnings=True in iv_rank_cache.json.
"""

import json
import time
import urllib.request
import urllib.error
import urllib.parse
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean

# ── Paths ──────────────────────────────────────────────────────────────────────
PROJECT_DIR  = Path(__file__).parent.parent
DATA_DIR     = PROJECT_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
CONFIG_PATH  = PROJECT_DIR / "alpaca_config.json"   # credentials — stays at root
IV_HIST_PATH = DATA_DIR / "iv_history.json"
IV_RANK_PATH = DATA_DIR / "iv_rank_cache.json"

# ── Constants ──────────────────────────────────────────────────────────────────
TARGET_DTE        = 35
MIN_DTE           = 21
MAX_DTE           = 50
IV_RANK_WINDOW    = 252   # trading days (~1 year)
MIN_IV_HISTORY    = 30    # days before IV rank is meaningful
BATCH_SNAPSHOTS   = 100   # symbols per snapshot API call
CALL_DELAY        = 0.15  # seconds between batches
EARNINGS_WINDOW   = 14    # days ahead to flag as "near earnings"

# ── Alpaca credentials ─────────────────────────────────────────────────────────
with open(CONFIG_PATH) as f:
    _cfg = json.load(f)

API_KEY      = _cfg["api_key"]
API_SECRET   = _cfg["api_secret"]
DATA_BASE    = "https://data.alpaca.markets"
TRADING_BASE = _cfg.get("base_url", "https://paper-api.alpaca.markets/v2")

HEADERS = {
    "APCA-API-KEY-ID":     API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
    "Accept":              "application/json",
}


# ── HTTP helper ────────────────────────────────────────────────────────────────

def _get(url, timeout=20):
    """
    GET helper used by all options_loop modules.

    Handles:
      - 429 rate limit: waits 15s then retries (loop, not recursion -- no stack risk)
      - 404 / 422: returns None (contract or symbol not found -- expected)
      - Other HTTP errors: logs and returns None
      - Network / timeout errors: logs and returns None
    """
    req = urllib.request.Request(url, headers=HEADERS)
    for attempt in range(4):   # up to 4 tries on rate limit (15s x 4 = 60s max wait)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 15 * (attempt + 1)
                print(f"    [rate limit] sleeping {wait}s... (attempt {attempt+1}/4)")
                time.sleep(wait)
                continue   # retry in loop rather than recursive call
            if e.code in (404, 422):
                return None
            body = e.read().decode(errors="replace")[:120] if hasattr(e, "read") else ""
            print(f"    [HTTP {e.code}] {url[-70:]}  {body}")
            return None
        except Exception as e:
            print(f"    [error] {type(e).__name__}: {e}")
            return None
    print(f"    [rate limit] max retries reached for {url[-60:]}")
    return None


# ── Universe ───────────────────────────────────────────────────────────────────

def _fetch_sp500():
    """Fetch S&P 500 components from Wikipedia (same logic as screener.py)."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [universe] SP500 Wikipedia failed: {e}")
        return []

    patterns = [
        r'<td><a[^>]*href="https?://[^"]*">([A-Z]{1,5}(?:\.[A-Z])?)</a>',
        r'<td><a[^>]*>([A-Z]{1,5}(?:\.[A-Z])?)</a>',
    ]
    tickers = []
    for pat in patterns:
        tickers.extend(re.findall(pat, html))

    seen, clean = set(), []
    for t in tickers:
        t = t.replace("/", ".")
        if t not in seen:
            seen.add(t)
            clean.append(t)

    return clean if len(clean) >= 400 else []


def _fetch_nasdaq100():
    """
    Fetch NASDAQ-100 components from Wikipedia.
    The page lists tickers in plain <td>TICK</td> cells (no href).
    We anchor on the first occurrence of a known ticker (AAPL) to locate
    the components table, then extract backwards to the <table> tag.
    """
    url = "https://en.wikipedia.org/wiki/Nasdaq-100"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            html = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  [universe] NASDAQ100 Wikipedia failed: {e}")
        return []

    # Locate the components table by finding AAPL (always present)
    idx_aapl = html.find("<td>AAPL</td>")
    if idx_aapl < 0:
        return []

    table_start = html.rfind("<table", 0, idx_aapl)
    comp_section = html[table_start:]

    found = re.findall(r"<td>([A-Z]{1,5})</td>", comp_section)
    seen, clean = set(), []
    for t in found:
        if t not in seen:
            seen.add(t)
            clean.append(t)
    return clean


def get_universe():
    """Return deduplicated SP500 + NASDAQ100 symbol list."""
    sp500  = _fetch_sp500()
    ndx100 = _fetch_nasdaq100()

    # Alpaca uses BRK/B not BRK.B for equity; skip options on it (non-standard)
    # Replace . with / for Alpaca equity endpoints, but flag . tickers for skip
    combined = list(dict.fromkeys(sp500 + ndx100))
    print(f"  Universe: {len(sp500)} SP500 + {len(ndx100)} NASDAQ100 "
          f"= {len(combined)} unique")
    return combined


# ── Stock prices ───────────────────────────────────────────────────────────────

def fetch_stock_prices(symbols):
    """
    Batch-fetch latest trade prices via Alpaca equity snapshots.
    Skips tickers with dots/slashes (options not available on these).
    Returns {symbol: price}.
    """
    # Filter to plain-ticker symbols only (options not available on BRK/B etc.)
    eligible = [s for s in symbols if "." not in s and "/" not in s]

    prices = {}
    for i in range(0, len(eligible), 100):
        batch = eligible[i : i + 100]
        syms  = ",".join(batch)
        url   = f"{DATA_BASE}/v2/stocks/snapshots?symbols={syms}&feed=iex"
        data  = _get(url)
        if data:
            for sym, snap in data.items():
                try:
                    prices[sym] = float(snap["latestTrade"]["p"])
                except (KeyError, TypeError, ValueError):
                    pass
        time.sleep(CALL_DELAY)
    return prices


# ── Contract symbol construction ───────────────────────────────────────────────

def _standard_increment(price):
    """Standard options strike increment for a given stock price."""
    if price < 5:    return 0.5
    if price < 25:   return 1.0
    if price < 50:   return 2.5
    if price < 200:  return 5.0
    if price < 500:  return 10.0
    return 25.0


def _nearest_strikes(price, count=3):
    """
    Return `count` nearest standard strikes around price.
    Always returns the ATM strike plus 1 above and 1 below.
    """
    inc  = _standard_increment(price)
    base = round(round(price / inc) * inc, 2)
    half = count // 2
    strikes = []
    for offset in range(-half, half + 1):
        s = round(base + offset * inc, 2)
        if s > 0:
            strikes.append(s)
    return strikes


def _target_expirations(today=None):
    """
    Return list of monthly option expiration dates (3rd Friday) that fall
    within [MIN_DTE, MAX_DTE], sorted by distance to TARGET_DTE.

    When no standard monthly expiry lands in [MIN_DTE, MAX_DTE] (calendar gap
    that occurs ~5 days/month), the window expands by up to +14 days so the
    nearest out-of-range expiry is used rather than returning nothing.
    Looks 4 months ahead to ensure at least one candidate is always found.
    """
    if today is None:
        today = date.today()

    def _collect(max_dte_limit):
        result = []
        for month_offset in range(4):          # 4 months covers any DTE gap
            y = today.year
            m = today.month + month_offset
            while m > 12:
                m -= 12
                y += 1
            d = date(y, m, 1)
            while d.weekday() != 4:            # advance to first Friday
                d += timedelta(days=1)
            third_friday = d + timedelta(weeks=2)
            dte = (third_friday - today).days
            if MIN_DTE <= dte <= max_dte_limit:
                result.append((abs(dte - TARGET_DTE), third_friday))
        return result

    # Try strict window first; widen by 2 weeks if calendar gap leaves it empty
    for max_dte in (MAX_DTE, MAX_DTE + 14):
        candidates = _collect(max_dte)
        if candidates:
            candidates.sort()
            return [exp for _, exp in candidates]

    return []


def build_contract_symbols(symbols_prices, today=None):
    """
    Construct Alpaca option contract symbols directly from stock prices.
    Avoids the /v2/options/contracts API call entirely.

    Format: {UNDERLYING}{YYMMDD}C{STRIKE_8DIGIT}
    Example: AAPL260515C00270000  (AAPL, 2026-05-15, Call, $270.00)

    Returns {contract_symbol: underlying_symbol}.
    """
    if today is None:
        today = date.today()

    expirations = _target_expirations(today)
    if not expirations:
        print("  [iv_tracker] No valid expirations in DTE window — check dates")
        return {}

    target_exp = expirations[0]
    date_str   = target_exp.strftime("%y%m%d")
    dte        = (target_exp - today).days

    print(f"  Target expiration : {target_exp}  ({dte} DTE)")

    mapping = {}   # contract_sym -> underlying
    for sym, price in symbols_prices.items():
        strikes = _nearest_strikes(price, count=3)
        for strike in strikes:
            strike_int = int(round(strike * 1000))
            contract_sym = f"{sym}{date_str}C{strike_int:08d}"
            mapping[contract_sym] = sym

    return mapping


# ── IV snapshots ───────────────────────────────────────────────────────────────

def fetch_iv_snapshots(contract_symbols):
    """
    Batch-fetch Alpaca options snapshots.
    Returns {contract_symbol: implied_volatility}.

    API: data.alpaca.markets/v1beta1/options/snapshots
    Field: snap["impliedVolatility"]  (top-level, NOT inside snap["greeks"])

    Feed note: indicative (non-OPRA) on paper/free plan.
    Phase 2 upgrade path: add &feed=opra once OPRA subscription active.
    """
    iv_map = {}
    symbols = list(contract_symbols)

    for i in range(0, len(symbols), BATCH_SNAPSHOTS):
        batch = symbols[i : i + BATCH_SNAPSHOTS]
        syms  = ",".join(batch)
        url   = f"{DATA_BASE}/v1beta1/options/snapshots?symbols={syms}"
        data  = _get(url)
        if data:
            snaps = data.get("snapshots", data)
            for sym, snap in snaps.items():
                try:
                    iv = float(snap["impliedVolatility"])
                    if iv > 0:
                        iv_map[sym] = iv
                except (KeyError, TypeError, ValueError):
                    pass
        time.sleep(CALL_DELAY)

    return iv_map


def select_atm_iv(contract_to_sym, iv_snapshots, prices):
    """
    From the IV readings for multiple strike candidates per underlying,
    pick the one closest to ATM. Returns {underlying: iv}.
    """
    best = {}   # underlying -> {iv, dist}

    for contract_sym, iv in iv_snapshots.items():
        underlying = contract_to_sym.get(contract_sym)
        if not underlying:
            continue
        price = prices.get(underlying)
        if not price:
            continue

        # Extract strike from contract symbol (last 8 chars)
        try:
            strike = int(contract_sym[-8:]) / 1000.0
        except ValueError:
            continue

        dist = abs(strike - price)

        if underlying not in best or dist < best[underlying]["dist"]:
            best[underlying] = {"iv": iv, "dist": dist}

    return {sym: data["iv"] for sym, data in best.items()}


# ── IV history I/O ─────────────────────────────────────────────────────────────

def load_iv_history():
    if not IV_HIST_PATH.exists():
        return {}
    with open(IV_HIST_PATH) as f:
        return json.load(f)


def save_iv_history(hist):
    with open(IV_HIST_PATH, "w") as f:
        json.dump(hist, f)


def append_today_iv(hist, today_str, sym_iv):
    """Append today's readings and prune entries older than window + 20 days."""
    cutoff = (date.today() - timedelta(days=IV_RANK_WINDOW + 20)).strftime("%Y-%m-%d")
    for sym, iv in sym_iv.items():
        if sym not in hist:
            hist[sym] = {}
        hist[sym][today_str] = round(iv, 6)
        hist[sym] = {d: v for d, v in hist[sym].items() if d >= cutoff}
    return hist


# ── IV rank ────────────────────────────────────────────────────────────────────

def compute_iv_rank(iv_series):
    """
    iv_series: list of (date_str, iv_float) sorted ascending.
    Returns dict with iv_rank, iv_52wk_high/low, iv_current, n_days.
    Returns None if fewer than MIN_IV_HISTORY readings.
    """
    if len(iv_series) < MIN_IV_HISTORY:
        return None

    recent = iv_series[-IV_RANK_WINDOW:]
    values = [iv for _, iv in recent]
    hi, lo, cur = max(values), min(values), values[-1]

    iv_rank = round((cur - lo) / (hi - lo) * 100, 1) if hi != lo else 50.0
    return {
        "iv_current":   round(cur, 4),
        "iv_rank":      iv_rank,
        "iv_52wk_high": round(hi, 4),
        "iv_52wk_low":  round(lo, 4),
        "n_days":       len(recent),
    }


def build_iv_rank_cache(hist, earnings_cal=None):
    """Build the full IV rank cache from history. Returns dict."""
    today    = date.today().strftime("%Y-%m-%d")
    window   = date.today() + timedelta(days=EARNINGS_WINDOW)
    earnings_cal = earnings_cal or {}
    cache    = {}

    for sym, daily in hist.items():
        series = sorted(daily.items())
        result = compute_iv_rank(series)

        if result:
            result.update({
                "symbol": sym, "updated": today,
                "sufficient_history": True,
            })
        else:
            result = {
                "symbol": sym, "updated": today,
                "sufficient_history": False,
                "n_days": len(daily),
                "iv_current": series[-1][1] if series else None,
                "iv_rank": None,
                "_note": f"need {MIN_IV_HISTORY} days, have {len(daily)}",
            }

        # Earnings flag
        earn_str = earnings_cal.get(sym)
        near_earn = False
        if earn_str:
            try:
                earn_dt = datetime.strptime(earn_str, "%Y-%m-%d").date()
                near_earn = date.today() <= earn_dt <= window
            except ValueError:
                pass
        result["near_earnings"] = near_earn
        result["next_earnings"] = earn_str

        cache[sym] = result

    return cache


# ── Earnings calendar (Phase 1 stub) ──────────────────────────────────────────

def load_earnings_calendar():
    """
    Load earnings_calendar.json if present.
    Format: {symbol: "YYYY-MM-DD"}  (next earnings date per symbol)

    Phase 1: empty / manually maintained
    Phase 2: auto-populated by earnings_fetcher.py from a financial data API
    """
    cal_path = DATA_DIR / "earnings_calendar.json"
    if cal_path.exists():
        with open(cal_path) as f:
            return json.load(f)
    return {}


# ── Main ───────────────────────────────────────────────────────────────────────

def run():
    now_utc   = datetime.now(timezone.utc)
    today     = date.today()
    today_str = today.strftime("%Y-%m-%d")

    print(f"\n{'='*60}")
    print(f" IV Tracker  [{now_utc.strftime('%Y-%m-%d %H:%M UTC')}]")
    print(f"{'='*60}")

    # ── 1. Universe ────────────────────────────────────────────────────────────
    universe = get_universe()
    if not universe:
        print("  ERROR: universe build failed. Aborting.")
        return

    # ── 2. Stock prices ────────────────────────────────────────────────────────
    print(f"\n  Fetching prices for {len(universe)} symbols...")
    prices = fetch_stock_prices(universe)

    min_price = 15.0
    eligible  = {s: p for s, p in prices.items() if p >= min_price}
    print(f"  Got prices: {len(prices)}  |  >= ${min_price}: {len(eligible)}")

    # ── 3. Build contract symbols + fetch IV ───────────────────────────────────
    print(f"\n  Building contract symbols (direct construction, no lookup API call)...")
    contract_to_sym = build_contract_symbols(eligible, today)
    print(f"  Constructed {len(contract_to_sym)} contract symbols "
          f"({len(eligible)} underlyings × ~3 strikes)")

    print(f"\n  Fetching IV snapshots ({len(contract_to_sym)} contracts "
          f"in batches of {BATCH_SNAPSHOTS})...")
    iv_snaps = fetch_iv_snapshots(contract_to_sym)
    print(f"  IV readings received: {len(iv_snaps)}")

    sym_iv = select_atm_iv(contract_to_sym, iv_snaps, eligible)
    print(f"  ATM IV resolved: {len(sym_iv)} underlyings "
          f"({len(eligible) - len(sym_iv)} no data)")

    if not sym_iv:
        print("\n  WARNING: 0 IV readings. Possible causes:")
        print("    - Market closed / pre-market (options data may be stale)")
        print("    - Alpaca indicative feed lag (try re-running after 16:30 ET)")
        print("    - Contract symbols constructed outside available strikes")
        print("  iv_history.json NOT updated today.")
        return {"date": today_str, "iv_fetched": 0, "with_iv_rank": 0}

    # ── 4. Update IV history ───────────────────────────────────────────────────
    print(f"\n  Updating iv_history.json...")
    hist = load_iv_history()
    hist = append_today_iv(hist, today_str, sym_iv)
    save_iv_history(hist)
    total_days = sum(len(v) for v in hist.values())
    print(f"  Archive: {len(hist)} symbols, {total_days:,} symbol-days")

    # ── 5. IV rank cache ───────────────────────────────────────────────────────
    print(f"\n  Computing IV ranks...")
    earnings_cal = load_earnings_calendar()
    cache        = build_iv_rank_cache(hist, earnings_cal)
    with_rank    = sum(1 for v in cache.values() if v.get("sufficient_history"))
    near_earn    = sum(1 for v in cache.values() if v.get("near_earnings"))

    with open(IV_RANK_PATH, "w") as f:
        json.dump(cache, f, indent=2)

    print(f"  Symbols with IV rank  : {with_rank}  "
          f"(building: {len(hist) - with_rank})")
    if near_earn:
        print(f"  Near earnings flag    : {near_earn} symbols "
              f"(within {EARNINGS_WINDOW} days)")

    # ── 6. Summary ─────────────────────────────────────────────────────────────
    if with_rank:
        ranks     = [v["iv_rank"] for v in cache.values()
                     if v.get("iv_rank") is not None]
        high_iv   = sorted(
            [v for v in cache.values() if (v.get("iv_rank") or 0) >= 50],
            key=lambda x: x["iv_rank"], reverse=True
        )
        med_rank  = sorted(ranks)[len(ranks) // 2]
        print(f"\n  IV Rank distribution:")
        print(f"    Median rank       : {med_rank:.0f}")
        print(f"    >= 50 (sell zone) : {len(high_iv)} symbols")
        if high_iv[:8]:
            top = ", ".join(
                f"{v['symbol']}({v['iv_rank']:.0f})" for v in high_iv[:8]
            )
            print(f"    Top by rank       : {top}")

    print(f"\n  Done.\n")

    return {
        "date":         today_str,
        "universe":     len(universe),
        "iv_fetched":   len(sym_iv),
        "with_iv_rank": with_rank,
        "near_earnings": near_earn,
    }


if __name__ == "__main__":
    run()
