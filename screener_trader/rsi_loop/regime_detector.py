"""
regime_detector.py
Fetches SPY and VIXY daily bars from Alpaca, computes market regime metrics,
classifies the current market regime, and writes market_regime.json.
"""

import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
ALPACA_CONFIG_PATH = PROJECT_DIR / "alpaca_config.json"
REGIME_PATH = PROJECT_DIR / "market_regime.json"

DATA_BASE = "https://data.alpaca.markets/v2/stocks/{symbol}/bars"


def load_alpaca_config():
    """Read alpaca_config.json and return api_key and api_secret."""
    with open(ALPACA_CONFIG_PATH, "r") as f:
        cfg = json.load(f)
    return cfg["api_key"], cfg["api_secret"]


def fetch_bars(symbol, days, api_key, api_secret):
    """
    Fetch daily bars for symbol going back `days` calendar days.
    Retries up to 3 times on HTTP 429 with exponential backoff.
    Returns list of bar dicts with keys: t, o, h, l, c, v.
    """
    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days + 10)  # add buffer for weekends/holidays
    start = start_dt.strftime("%Y-%m-%d")
    end = end_dt.strftime("%Y-%m-%d")

    url = (
        DATA_BASE.format(symbol=symbol)
        + f"?timeframe=1Day&start={start}&end={end}&limit={days + 20}"
        + "&feed=iex&adjustment=all"
    )
    headers = {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
    }

    bars = []
    next_page_token = None
    attempt = 0

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
                    bars.extend(data.get("bars", []))
                    next_page_token = data.get("next_page_token")
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    wait = 2 ** attempt
                    print(f"  [regime_detector] 429 rate limit for {symbol}, waiting {wait}s...")
                    time.sleep(wait)
                    attempt += 1
                else:
                    raise
            except (urllib.error.URLError, TimeoutError, OSError):
                # Network timeout / SSL error -- retry with backoff
                attempt += 1
                if attempt < 3:
                    time.sleep(2 ** attempt)
                else:
                    raise

        if not next_page_token:
            break

    # Sort by timestamp ascending
    bars.sort(key=lambda b: b["t"])
    return bars[-days:] if len(bars) > days else bars


def _avg(values):
    """Simple average of a list of floats."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def detect_and_write():
    """
    Fetches SPY (250 bars) + VIXY (60 bars), computes metrics,
    classifies regime, writes market_regime.json, returns regime dict.
    """
    api_key, api_secret = load_alpaca_config()

    print("  [regime_detector] Fetching SPY bars (250 days)...")
    spy_bars = fetch_bars("SPY", 250, api_key, api_secret)
    print(f"  [regime_detector] Got {len(spy_bars)} SPY bars")

    print("  [regime_detector] Fetching VIXY bars (60 days)...")
    vixy_bars = fetch_bars("VIXY", 60, api_key, api_secret)
    print(f"  [regime_detector] Got {len(vixy_bars)} VIXY bars")

    # ── SPY metrics ────────────────────────────────────────────────────────────
    spy_closes = [b["c"] for b in spy_bars]
    spy_current = spy_closes[-1]

    # 200-day MA: use available bars up to 200
    ma200_bars = spy_closes[-200:] if len(spy_closes) >= 200 else spy_closes
    ma200 = _avg(ma200_bars)

    spy_vs_200ma_pct = ((spy_current - ma200) / ma200) * 100.0 if ma200 else 0.0
    spy_below_200ma = spy_current < ma200

    # 20-day return
    if len(spy_closes) >= 21:
        spy_20d_return = ((spy_current - spy_closes[-21]) / spy_closes[-21]) * 100.0
    elif len(spy_closes) >= 2:
        spy_20d_return = ((spy_current - spy_closes[0]) / spy_closes[0]) * 100.0
    else:
        spy_20d_return = 0.0

    # 5-day return
    if len(spy_closes) >= 6:
        spy_5d_return = ((spy_current - spy_closes[-6]) / spy_closes[-6]) * 100.0
    elif len(spy_closes) >= 2:
        spy_5d_return = ((spy_current - spy_closes[0]) / spy_closes[0]) * 100.0
    else:
        spy_5d_return = 0.0

    # ── VIXY metrics ──────────────────────────────────────────────────────────
    vixy_closes = [b["c"] for b in vixy_bars]
    vixy_current = vixy_closes[-1] if vixy_closes else 0.0

    vixy_20d_bars = vixy_closes[-20:] if len(vixy_closes) >= 20 else vixy_closes
    vixy_20d_avg = _avg(vixy_20d_bars) if vixy_20d_bars else 0.0

    vix_elevated = vixy_current > vixy_20d_avg * 1.2 if vixy_20d_avg else False

    # ── Regime classification (first match wins) ───────────────────────────────
    if (vixy_20d_avg > 0
            and vixy_current > vixy_20d_avg * 1.8
            and spy_5d_return < -3.0):
        regime = "geopolitical_shock"
    elif spy_vs_200ma_pct < -15.0:
        regime = "bear"
    elif spy_vs_200ma_pct < -5.0 or spy_20d_return < -8.0:
        regime = "correction"
    elif spy_vs_200ma_pct < -2.0 or spy_20d_return < -4.0:
        regime = "mild_correction"
    elif spy_below_200ma and spy_5d_return > 2.0:
        regime = "recovery"
    else:
        regime = "bull"

    result = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "regime": regime,
        "spy_metrics": {
            "current_price": round(spy_current, 2),
            "ma200": round(ma200, 2),
            "spy_vs_200ma_pct": round(spy_vs_200ma_pct, 2),
            "spy_20d_return_pct": round(spy_20d_return, 2),
            "spy_5d_return_pct": round(spy_5d_return, 2),
        },
        "vixy_metrics": {
            "current_price": round(vixy_current, 2),
            "vixy_20d_avg": round(vixy_20d_avg, 2),
            "vix_elevated": vix_elevated,
        },
    }

    with open(REGIME_PATH, "w") as f:
        json.dump(result, f, indent=2)
    print(f"  [regime_detector] Regime: {regime} -> written to {REGIME_PATH}")
    return result


if __name__ == "__main__":
    result = detect_and_write()
    print(json.dumps(result, indent=2))
