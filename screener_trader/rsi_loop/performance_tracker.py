"""
performance_tracker.py
Logs screener picks to picks_history.json and fills forward-return data
for each tracked pick across 1d, 5d, 10d, and 20d horizons.
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
ALPACA_CONFIG_PATH = PROJECT_DIR / "alpaca_config.json"
PICKS_HISTORY_PATH = PROJECT_DIR / "picks_history.json"
SCREENER_RESULTS_PATH = PROJECT_DIR / "screener_results.json"

DATA_BASE = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"

HORIZONS = [1, 5, 10, 20]
FORWARD_BARS = 35  # enough to cover all horizons with buffer


# ── Alpaca helpers ─────────────────────────────────────────────────────────────

def _load_alpaca_config():
    with open(ALPACA_CONFIG_PATH, "r") as f:
        cfg = json.load(f)
    return cfg["api_key"], cfg["api_secret"]


def fetch_forward_bars(symbol, start_date, api_key, api_secret):
    """
    Fetch up to FORWARD_BARS daily bars starting from start_date.
    start_date: str in 'YYYY-MM-DD' format (the entry/screened date).
    Returns list of bar dicts sorted ascending by timestamp.
    """
    start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = start_dt + timedelta(days=FORWARD_BARS + 10)
    start = start_dt.strftime("%Y-%m-%d")
    end = end_dt.strftime("%Y-%m-%d")

    url = (
        DATA_BASE.format(symbol=symbol)
        + f"?timeframe=1Day&start={start}&end={end}&limit={FORWARD_BARS + 10}"
        + "&feed=iex&adjustment=all"
    )
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
    }

    bars = []
    next_page_token = None

    while True:
        page_url = url
        if next_page_token:
            page_url += f"&page_token={next_page_token}"

        req = urllib.request.Request(page_url, headers=headers)
        attempt = 0
        while attempt < 3:
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode())
                    bars.extend(data.get("bars") or [])
                    next_page_token = data.get("next_page_token")
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    wait = 2 ** attempt
                    time.sleep(wait)
                    attempt += 1
                else:
                    raise
            except (urllib.error.URLError, TimeoutError, OSError):
                attempt += 1
                if attempt < 3:
                    time.sleep(2 ** attempt)
                else:
                    raise

        if not next_page_token:
            break

    bars.sort(key=lambda b: b["t"])
    return bars[:FORWARD_BARS]


def compute_returns(entry_price, bars):
    """
    Compute forward returns at horizons 1, 5, 10, 20 days.
    bars: list of daily bars sorted ascending; bar[0] is the entry day.
    Horizon N = close at index N vs entry_price.
    Returns dict {"1d": pct|None, "5d": pct|None, "10d": pct|None, "20d": pct|None}.
    """
    result = {}
    for n in HORIZONS:
        key = f"{n}d"
        if len(bars) >= n + 1:
            close_n = bars[n]["c"]
            result[key] = round(((close_n - entry_price) / entry_price) * 100.0, 4)
        else:
            result[key] = None
    return result


# ── History I/O ────────────────────────────────────────────────────────────────

def load_history():
    """Load picks_history.json; returns dict with 'picks' list."""
    if not PICKS_HISTORY_PATH.exists():
        return {"version": 1, "last_updated": None, "picks": []}
    with open(PICKS_HISTORY_PATH, "r") as f:
        return json.load(f)


def save_history(history):
    """Atomically write picks_history.json via a .tmp file."""
    history["last_updated"] = datetime.now(timezone.utc).isoformat()
    tmp_path = PICKS_HISTORY_PATH.with_suffix(".tmp")
    with open(tmp_path, "w") as f:
        json.dump(history, f, indent=2)
    os.replace(tmp_path, PICKS_HISTORY_PATH)


# ── Pick logging ───────────────────────────────────────────────────────────────

def log_new_picks(screener_results_path, regime="unknown"):
    """
    Read screener_results.json, append new picks to picks_history.json.
    Dedup key: "{symbol}_{run_date}".
    source field = "top_picks" or "radar".
    """
    results_path = Path(screener_results_path)
    if not results_path.exists():
        print(f"  [performance_tracker] screener_results not found: {results_path}")
        return

    with open(results_path, "r") as f:
        screener_results = json.load(f)

    run_date = screener_results.get("run_date") or screener_results.get("run_time_utc", "")[:10]

    history = load_history()
    existing_ids = {p["id"] for p in history["picks"]}

    new_count = 0
    for source in ("top_picks", "radar"):
        picks_list = screener_results.get(source, [])
        if not picks_list:
            continue
        for stock in picks_list:
            symbol = stock.get("symbol", "")
            pick_id = f"{symbol}_{run_date}"
            if pick_id in existing_ids:
                continue

            entry = {
                "id": pick_id,
                "symbol": symbol,
                "screened_date": run_date,
                "entry_price": stock.get("price", 0.0),
                "rsi": stock.get("rsi"),
                "pct_below_bb": stock.get("pct_below_bb"),
                "pct_above_200ma": stock.get("pct_above_200ma"),
                "vol_ratio": stock.get("vol_ratio"),
                "composite_score": stock.get("composite_score"),
                "filters": stock.get("filters", {}),
                "filters_passed": stock.get("filters_passed", 0),
                "regime": regime,
                "source": source,
                "returns": {"1d": None, "5d": None, "10d": None, "20d": None},
            }
            history["picks"].append(entry)
            existing_ids.add(pick_id)
            new_count += 1

    if new_count > 0:
        save_history(history)
        print(f"  [performance_tracker] Logged {new_count} new picks.")
    else:
        print("  [performance_tracker] No new picks to log.")


# ── Research-pick logging ──────────────────────────────────────────────────────

def log_research_picks(candidates, regime="unknown"):
    """
    Log research-layer oversold candidates to picks_history.json.

    candidates: list of dicts from research_layer._build_candidates()
        each has: symbol, price, rsi, pct_from_lower_bb, pct_vs_200ma,
                  vol_ratio, oversold_score
    Dedup key: "{symbol}_{today_date}" — same format as screener picks.
    All candidates are logged (not just the top 3 Gemini picks) so that
    the full signal range builds history for the optimizer.
    """
    if not candidates:
        print("  [performance_tracker] No research candidates to log.")
        return

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    history = load_history()
    existing_ids = {p["id"] for p in history["picks"]}

    new_count = 0
    for c in candidates:
        symbol = c.get("symbol", "")
        if not symbol:
            continue
        pick_id = f"{symbol}_{today}"
        if pick_id in existing_ids:
            continue

        entry = {
            "id":              pick_id,
            "symbol":          symbol,
            "screened_date":   today,
            "entry_price":     c.get("price", 0.0),
            "rsi":             c.get("rsi"),
            "pct_below_bb":    c.get("pct_from_lower_bb"),   # field name remap
            "pct_above_200ma": c.get("pct_vs_200ma"),         # field name remap
            "vol_ratio":       c.get("vol_ratio"),
            "composite_score": c.get("oversold_score"),
            "filters":         {},
            "filters_passed":  0,
            "regime":          regime,
            "source":          "research_layer",
            "returns":         {"1d": None, "5d": None, "10d": None, "20d": None},
        }
        history["picks"].append(entry)
        existing_ids.add(pick_id)
        new_count += 1

    if new_count > 0:
        save_history(history)
        print(f"  [performance_tracker] Logged {new_count} research candidates.")
    else:
        print("  [performance_tracker] Research candidates already logged for today (dedup).")


# ── Return filling ─────────────────────────────────────────────────────────────

def _fill_pick_returns(pick, api_key, api_secret):
    """Fill missing return horizons for a single pick. Returns updated pick."""
    returns = pick.get("returns", {})
    needs_fill = any(returns.get(f"{n}d") is None for n in HORIZONS)
    if not needs_fill:
        return pick

    symbol = pick["symbol"]
    screened_date = pick["screened_date"]
    entry_price = pick.get("entry_price", 0.0)

    if not entry_price or entry_price <= 0:
        return pick

    try:
        bars = fetch_forward_bars(symbol, screened_date, api_key, api_secret)
        if not bars:
            return pick
        new_returns = compute_returns(entry_price, bars)
        # Only update None slots; preserve already-computed values
        for key, val in new_returns.items():
            if returns.get(key) is None and val is not None:
                returns[key] = val
        pick["returns"] = returns
    except Exception as e:
        print(f"  [performance_tracker] Error filling {symbol}: {e}")

    return pick


def fill_missing_returns(picks):
    """
    Fill return data for all picks that have any None horizon.
    Uses ThreadPoolExecutor(max_workers=8).
    Saves updated history after filling.
    """
    api_key, api_secret = _load_alpaca_config()

    needs_work = [p for p in picks if any(p.get("returns", {}).get(f"{n}d") is None for n in HORIZONS)]
    if not needs_work:
        print("  [performance_tracker] All returns already filled.")
        return picks

    print(f"  [performance_tracker] Filling returns for {len(needs_work)} picks...")

    updated_map = {p["id"]: p for p in picks}

    def worker(pick):
        return _fill_pick_returns(pick, api_key, api_secret)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(worker, needs_work))

    for pick in results:
        updated_map[pick["id"]] = pick

    return list(updated_map.values())


# ── Entry point ────────────────────────────────────────────────────────────────

def run():
    """Load history, fill missing returns, save."""
    history = load_history()
    picks = history.get("picks", [])
    if not picks:
        print("  [performance_tracker] No picks in history yet.")
        return

    updated_picks = fill_missing_returns(picks)
    history["picks"] = updated_picks
    save_history(history)
    print(f"  [performance_tracker] History updated: {len(updated_picks)} picks total.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        regime_arg = sys.argv[2] if len(sys.argv) > 2 else "unknown"
        log_new_picks(sys.argv[1], regime_arg)
    run()
