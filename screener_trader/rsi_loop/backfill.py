"""
backfill.py
Bootstraps picks_history.json with ~1 year of simulated history.

Simulates the research-layer RSI < 40 screen over every weekly bar in the
historical price data for the full watchlist.  Forward returns are computed
directly from the historical bars — no extra API calls needed.

Run once from any directory:
    py -3 "path\to\screener_trader\rsi_loop\backfill.py"

After the run, `picks_history.json` will contain enough data for the
signal_analyzer and optimizer to switch from regime_defaults to data_driven.
"""

import json
import os
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import mean, stdev

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_DIR       = Path(__file__).parent.parent
ALPACA_CONFIG_PATH = PROJECT_DIR / "alpaca_config.json"
PICKS_HISTORY_PATH = PROJECT_DIR / "picks_history.json"
MULTI_BARS_URL    = "https://data.alpaca.markets/v2/stocks/bars"

# ── Watchlist (mirrors research_layer.py) ─────────────────────────────────────
WATCHLIST = [
    # Healthcare / Med-devices / Pharma
    "ABT", "JNJ", "PFE", "MRK", "LLY", "ABBV", "BMY", "AMGN", "GILD",
    "CVS", "CI", "HUM", "UNH", "MDT", "SYK", "BSX", "EW", "BAX", "HOLX",
    # Consumer Discretionary
    "NKE", "SBUX", "MCD", "TGT", "LOW", "HD", "RCL", "CCL", "MAR", "HLT",
    "BKNG", "EXPE", "M", "KSS", "BBWI", "PVH", "RL", "TPR",
    # Consumer Staples
    "PG", "KO", "PEP", "WMT", "COST", "CL", "EL", "MO", "PM", "STZ", "K",
    # Financials
    "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "AXP", "COF", "DFS",
    "V", "MA", "BX", "KKR", "APO", "SCHW", "TFC", "USB",
    # Energy
    "XOM", "CVX", "COP", "EOG", "DVN", "MPC", "VLO", "PSX", "OXY",
    "HAL", "SLB", "BKR", "FANG",
    # Technology
    "MSFT", "INTC", "CSCO", "IBM", "QCOM", "ORCL", "AMAT", "TXN",
    "ADI", "MCHP", "HPE", "STX", "WDC",
    # Communication Services
    "META", "GOOGL", "DIS", "NFLX", "T", "VZ", "CMCSA", "WBD", "PARA",
    # Industrials
    "GE", "BA", "CAT", "MMM", "HON", "UPS", "FDX", "DE", "RTX", "LMT",
    # Materials
    "LIN", "APD", "DOW", "NEM", "FCX", "MOS", "CF",
    # Real Estate / Utilities
    "AMT", "PLD", "O", "NEE", "DUK", "SO",
]

# ── Config ────────────────────────────────────────────────────────────────────
BATCH_SIZE        = 30
CALENDAR_LOOKBACK = 750   # ~2.1 years of calendar days => ~530 trading days
BAR_LIMIT         = 530   # bars requested per symbol per request
RSI_PERIOD        = 14
BB_PERIOD         = 20
BB_STD            = 2.0
MA200_PERIOD      = 200
VOL_PERIOD        = 20
RESEARCH_RSI_CAP  = 40    # same threshold as research_layer


# ── Alpaca fetch ──────────────────────────────────────────────────────────────

def _load_config():
    with open(ALPACA_CONFIG_PATH) as f:
        cfg = json.load(f)
    return cfg["api_key"], cfg["api_secret"]


def _fetch_multi_bars(symbols, api_key, api_secret):
    """Fetch up to BAR_LIMIT daily bars per symbol using multi-symbol endpoint."""
    end_dt   = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=CALENDAR_LOOKBACK)
    start    = start_dt.strftime("%Y-%m-%d")
    end      = end_dt.strftime("%Y-%m-%d")

    headers = {
        "APCA-API-KEY-ID":     api_key,
        "APCA-API-SECRET-KEY": api_secret,
    }
    all_bars = {}

    for i in range(0, len(symbols), BATCH_SIZE):
        batch = symbols[i : i + BATCH_SIZE]
        params = {
            "symbols":    ",".join(batch),
            "timeframe":  "1Day",
            "start":      start,
            "end":        end,
            "limit":      BAR_LIMIT,
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
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        data = json.loads(resp.read().decode())
                    bars_data       = data.get("bars") or {}
                    next_page_token = data.get("next_page_token")
                    for sym, bars in bars_data.items():
                        if bars:
                            batch_bars[sym].extend(bars)
                    break
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

        print(f"  Fetched batch {i // BATCH_SIZE + 1}/{-(-len(symbols) // BATCH_SIZE)}: "
              f"{sum(1 for s in batch if all_bars.get(s))}/{len(batch)} symbols OK")
        time.sleep(0.25)

    return all_bars


# ── Indicators (point-in-time, no look-ahead) ─────────────────────────────────

def _rsi(closes):
    """Wilder's smoothed RSI on full closes slice (last value = current RSI)."""
    if len(closes) < RSI_PERIOD + 2:
        return None
    deltas = [closes[j] - closes[j-1] for j in range(1, len(closes))]
    gains  = [max(d, 0.0) for d in deltas]
    losses = [abs(min(d, 0.0)) for d in deltas]

    ag = mean(gains[:RSI_PERIOD])
    al = mean(losses[:RSI_PERIOD])
    for j in range(RSI_PERIOD, len(gains)):
        ag = (ag * (RSI_PERIOD - 1) + gains[j]) / RSI_PERIOD
        al = (al * (RSI_PERIOD - 1) + losses[j]) / RSI_PERIOD

    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 2)


def _bb_pct(closes):
    """% distance from lower Bollinger Band (negative = below band)."""
    if len(closes) < BB_PERIOD + 1:
        return None
    window = closes[-(BB_PERIOD + 1):-1]
    sma    = mean(window)
    sd     = stdev(window) if len(window) > 1 else 0.0
    lower  = sma - BB_STD * sd
    if lower <= 0:
        return None
    return round(((closes[-1] - lower) / lower) * 100, 2)


def _ma200_pct(closes):
    """% above/below 200-day SMA."""
    if len(closes) < MA200_PERIOD:
        return None
    ma200 = mean(closes[-MA200_PERIOD:])
    if ma200 <= 0:
        return None
    return round(((closes[-1] - ma200) / ma200) * 100, 2)


def _vol_ratio(volumes):
    """Current bar volume / 20-bar average volume."""
    if len(volumes) < VOL_PERIOD + 1:
        return None
    avg = mean(volumes[-(VOL_PERIOD + 1):-1])
    if avg <= 0:
        return None
    return round(volumes[-1] / avg, 2)


# ── Weekly bar sampling ───────────────────────────────────────────────────────

def _weekly_indices(bars):
    """
    Return bar indices where a new ISO week begins (first trading day of each week).
    This mirrors running the screen on Monday (or first trading day of the week
    if Monday is a holiday).
    """
    seen  = set()
    out   = []
    for i, bar in enumerate(bars):
        dt = datetime.fromisoformat(bar["t"].replace("Z", "+00:00"))
        iso = dt.isocalendar()
        week_key = (iso.year, iso.week)
        if week_key not in seen:
            seen.add(week_key)
            out.append(i)
    return out


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 56)
    print(" RSI Loop — Historical Backfill")
    print("=" * 56)

    api_key, api_secret = _load_config()

    # ── Fetch price history ────────────────────────────────────────────────────
    print(f"\nFetching {CALENDAR_LOOKBACK}-day history for {len(WATCHLIST)} symbols...")
    bars_map = _fetch_multi_bars(WATCHLIST, api_key, api_secret)
    fetched  = sum(1 for v in bars_map.values() if v)
    print(f"\nFetched data for {fetched}/{len(WATCHLIST)} symbols")

    # ── Load existing picks (for dedup) ───────────────────────────────────────
    if PICKS_HISTORY_PATH.exists():
        with open(PICKS_HISTORY_PATH) as f:
            history = json.load(f)
    else:
        history = {"version": 1, "last_updated": None, "picks": []}

    existing_ids = {p["id"] for p in history["picks"]}
    today_str    = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new_picks    = []

    # ── Simulate weekly screen for each symbol ────────────────────────────────
    print("\nSimulating weekly screen across historical bars...")
    for symbol, bars in bars_map.items():
        if not bars or len(bars) < MA200_PERIOD + 20:
            continue  # not enough data for 200MA warmup + some future bars

        closes  = [b["c"] for b in bars]
        volumes = [b["v"] for b in bars]
        weekly  = _weekly_indices(bars)

        for idx in weekly:
            if idx < MA200_PERIOD:
                continue  # need 200 bars of warmup

            bar_date = bars[idx]["t"][:10]
            if bar_date >= today_str:
                continue  # today handled by live run

            pick_id = f"{symbol}_{bar_date}"
            if pick_id in existing_ids:
                continue

            # ── Compute indicators using only data up to this bar (no look-ahead)
            c_slice = closes[:idx + 1]
            v_slice = volumes[:idx + 1]

            rsi_val = _rsi(c_slice)
            if rsi_val is None or rsi_val >= RESEARCH_RSI_CAP:
                continue  # not oversold

            bb_val  = _bb_pct(c_slice)
            ma_val  = _ma200_pct(c_slice)
            vr_val  = _vol_ratio(v_slice)

            # composite score (same formula as research_layer)
            rsi_score = max(0.0, (RESEARCH_RSI_CAP - rsi_val) / RESEARCH_RSI_CAP)
            bb_score  = 0.0
            if bb_val is not None:
                bb_score = max(0.0, min(1.0, -bb_val / 10.0))
            composite = round(rsi_score * 0.6 + bb_score * 0.4, 4)

            entry_price = closes[idx]

            # ── Forward returns from actual historical prices ───────────────────
            rets = {}
            for n in [1, 5, 10, 20]:
                fi = idx + n
                if fi < len(bars):
                    rets[f"{n}d"] = round(
                        (closes[fi] - entry_price) / entry_price * 100, 4
                    )
                else:
                    rets[f"{n}d"] = None  # future not yet available

            new_picks.append({
                "id":              pick_id,
                "symbol":          symbol,
                "screened_date":   bar_date,
                "entry_price":     round(entry_price, 2),
                "rsi":             rsi_val,
                "pct_below_bb":    bb_val,
                "pct_above_200ma": ma_val,
                "vol_ratio":       vr_val,
                "composite_score": composite,
                "filters":         {},
                "filters_passed":  0,
                "regime":          "unknown",
                "source":          "backfill",
                "returns":         rets,
            })
            existing_ids.add(pick_id)

    # ── Save ──────────────────────────────────────────────────────────────────
    if not new_picks:
        print("\nNo new historical picks found (all already in history, or none passed RSI filter).")
        return

    # Sort chronologically before appending
    new_picks.sort(key=lambda p: p["screened_date"])
    history["picks"].extend(new_picks)
    history["last_updated"] = datetime.now(timezone.utc).isoformat()

    tmp = PICKS_HISTORY_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(history, f, indent=2)
    os.replace(tmp, PICKS_HISTORY_PATH)

    # ── Summary stats ─────────────────────────────────────────────────────────
    rets_5d = [p["returns"]["5d"] for p in new_picks if p["returns"]["5d"] is not None]
    rets_20d = [p["returns"]["20d"] for p in new_picks if p["returns"]["20d"] is not None]
    by_sym  = {}
    for p in new_picks:
        by_sym.setdefault(p["symbol"], 0)
        by_sym[p["symbol"]] += 1

    print(f"\n{'=' * 56}")
    print(f" Backfill complete")
    print(f"{'=' * 56}")
    print(f"  New picks added:      {len(new_picks)}")
    print(f"  Unique symbols:       {len(by_sym)}")
    print(f"  Total in history now: {len(history['picks'])}")

    if rets_5d:
        avg5  = sum(rets_5d) / len(rets_5d)
        hit5  = sum(1 for r in rets_5d if r > 0) / len(rets_5d)
        print(f"\n  5d avg return:  {avg5:+.2f}%  ({hit5:.0%} hit rate, n={len(rets_5d)})")
    if rets_20d:
        avg20 = sum(rets_20d) / len(rets_20d)
        hit20 = sum(1 for r in rets_20d if r > 0) / len(rets_20d)
        print(f"  20d avg return: {avg20:+.2f}%  ({hit20:.0%} hit rate, n={len(rets_20d)})")

    # Top symbols by frequency
    top_syms = sorted(by_sym.items(), key=lambda x: x[1], reverse=True)[:10]
    print(f"\n  Most-frequently oversold symbols (backfill period):")
    for sym, cnt in top_syms:
        print(f"    {sym:<6} {cnt} week(s)")

    print()


if __name__ == "__main__":
    main()
