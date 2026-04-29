"""
options_screener.py
===================
Phase 1 research screener.  Produces a daily candidate list of options
opportunities with no orders placed.

Pipeline
--------
1. Load iv_rank_cache.json (written by iv_tracker earlier the same run)
2. Resolve current market regime (reads screener_trader/market_regime.json
   if fresh; otherwise computes inline from SPY/VIXY data)
3. Fetch RSI(14) + volume-ratio data for all symbols with a valid IV rank
4. Apply signal filters: RSI, volume ratio, min price
5. Apply regime × IV-rank strategy matrix → assign strategy type
6. Write options_candidates.json  (overwritten each run — latest only)
7. Append new simulated picks to options_picks_history.json
   (research corpus used by Phase 3 optimizer)

Research mode contract
----------------------
Every pick written to options_picks_history.json carries:
    "research_mode": true
    "phase": 1
    "outcome_tracked": false   <- Phase 3 signal_analyzer fills this in
Orders are NEVER placed from this module.
"""

import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
PROJECT_DIR    = Path(__file__).parent.parent
DATA_DIR       = PROJECT_DIR / "data"
CONFIG_PATH    = PROJECT_DIR / "options_config.json"
IV_RANK_PATH   = DATA_DIR / "iv_rank_cache.json"
CANDIDATES_PATH = DATA_DIR / "options_candidates.json"
PICKS_PATH     = DATA_DIR / "options_picks_history.json"

# Sibling project — screener_trader writes market_regime.json here daily
SCREENER_DIR   = PROJECT_DIR.parent / "screener_trader"
REGIME_CACHE   = SCREENER_DIR / "market_regime.json"

# ── Re-use HTTP helper + credentials from iv_tracker ──────────────────────────
import sys
sys.path.insert(0, str(Path(__file__).parent))
from iv_tracker import _get, DATA_BASE, CALL_DELAY

# ── Constants ──────────────────────────────────────────────────────────────────
RSI_PERIOD      = 14
RSI_BARS_NEEDED = 50    # calendar-day lookback (~35 trading days → stable RSI)
VOL_BARS        = 21    # today + 20-day average for volume ratio
BATCH_SIGNALS   = 100   # symbols per equity-bars request


# ══════════════════════════════════════════════════════════════════════════════
#  Config / cache loaders
# ══════════════════════════════════════════════════════════════════════════════

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_iv_rank_cache() -> dict:
    if not IV_RANK_PATH.exists():
        return {}
    with open(IV_RANK_PATH) as f:
        return json.load(f)


def load_picks_history() -> list:
    if not PICKS_PATH.exists():
        return []
    with open(PICKS_PATH) as f:
        return json.load(f)


def save_picks_history(history: list) -> None:
    with open(PICKS_PATH, "w") as f:
        json.dump(history, f, indent=2)


# ══════════════════════════════════════════════════════════════════════════════
#  Regime detection
# ══════════════════════════════════════════════════════════════════════════════

def _regime_from_spy() -> str:
    """
    Compute market regime inline using the options account credentials.
    Mirrors regime_detector.py logic so regime labels are consistent.
    Falls back to 'bull' on any API failure.
    """
    today  = date.today()
    start  = (today - timedelta(days=290)).strftime("%Y-%m-%d")
    end    = today.strftime("%Y-%m-%d")
    url    = (f"{DATA_BASE}/v2/stocks/bars?symbols=SPY,VIXY"
              f"&timeframe=1Day&start={start}&end={end}&feed=iex&limit=620")
    data   = _get(url)
    if not data:
        return "bull"

    bars_map  = data.get("bars", {})
    spy_bars  = bars_map.get("SPY", [])
    vixy_bars = bars_map.get("VIXY", [])

    if len(spy_bars) < 20:
        return "bull"

    spy_c   = [b["c"] for b in spy_bars]
    vixy_c  = [b["c"] for b in vixy_bars]
    cur     = spy_c[-1]
    ma200   = sum(spy_c[-200:]) / min(200, len(spy_c))
    vs200   = (cur - ma200) / ma200 * 100
    ret_20  = (cur - spy_c[-21]) / spy_c[-21] * 100 if len(spy_c) >= 21 else 0.0
    ret_5   = (cur - spy_c[-6])  / spy_c[-6]  * 100 if len(spy_c) >= 6  else 0.0

    vixy_cur = vixy_c[-1] if vixy_c else 0.0
    vixy_avg = sum(vixy_c[-20:]) / min(20, len(vixy_c)) if vixy_c else 0.0

    if vixy_avg > 0 and vixy_cur > vixy_avg * 1.8 and ret_5 < -3.0:
        return "geopolitical_shock"
    if vs200 < -15.0:
        return "bear"
    if vs200 < -5.0 or ret_20 < -8.0:
        return "correction"
    if vs200 < -2.0 or ret_20 < -4.0:
        return "mild_correction"
    if cur < ma200 and ret_5 > 2.0:
        return "recovery"
    return "bull"


def get_regime() -> str:
    """
    Return current market regime string.
    Priority: screener_trader/market_regime.json (if written today) → inline SPY calc.
    """
    today_s = date.today().strftime("%Y-%m-%d")

    if REGIME_CACHE.exists():
        try:
            with open(REGIME_CACHE) as f:
                cached = json.load(f)
            computed_at = cached.get("computed_at", "")[:10]
            if computed_at == today_s:
                return cached["regime"]
        except Exception:
            pass

    print("  [screener] regime cache absent/stale — computing from SPY...")
    return _regime_from_spy()


# ══════════════════════════════════════════════════════════════════════════════
#  Signal data: RSI + volume ratio
# ══════════════════════════════════════════════════════════════════════════════

def _wilder_rsi(closes: list[float], period: int = RSI_PERIOD) -> float | None:
    """
    Wilder's smoothed RSI.  Returns None if insufficient data.
    """
    if len(closes) < period + 1:
        return None

    gains  = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss < 1e-10:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - 100.0 / (1.0 + rs), 2)


def _vol_ratio(volumes: list[float]) -> float | None:
    """
    Today's volume / 20-day average.
    Requires at least VOL_BARS entries (today + 20 prior days).
    """
    if len(volumes) < VOL_BARS:
        return None
    avg_20 = sum(volumes[-VOL_BARS:-1]) / (VOL_BARS - 1)
    if avg_20 < 1:
        return None
    return round(volumes[-1] / avg_20, 3)


def fetch_signal_data(symbols: list[str]) -> dict[str, dict]:
    """
    Batch-fetch daily bars for symbols and compute RSI + volume ratio.
    Returns {symbol: {rsi, vol_ratio, close}}.
    Missing data symbols are simply absent from the result.
    """
    today  = date.today()
    start  = (today - timedelta(days=RSI_BARS_NEEDED * 2)).strftime("%Y-%m-%d")
    end    = today.strftime("%Y-%m-%d")
    result = {}

    for i in range(0, len(symbols), BATCH_SIGNALS):
        batch = symbols[i : i + BATCH_SIGNALS]
        url   = (f"{DATA_BASE}/v2/stocks/bars?symbols={','.join(batch)}"
                 f"&timeframe=1Day&start={start}&end={end}&feed=iex&limit=10000")
        page_token = None

        while True:
            full_url = url + (f"&page_token={page_token}" if page_token else "")
            data = _get(full_url)
            if not data:
                break
            for sym, bars in data.get("bars", {}).items():
                if len(bars) < RSI_PERIOD + 5:
                    continue
                closes  = [b["c"] for b in bars]
                volumes = [float(b["v"]) for b in bars]
                rsi     = _wilder_rsi(closes)
                vr      = _vol_ratio(volumes)
                result[sym] = {
                    "rsi":       rsi,
                    "vol_ratio": vr,
                    "close":     round(closes[-1], 4),
                }
            page_token = data.get("next_page_token")
            if not page_token:
                break
            time.sleep(CALL_DELAY)

        time.sleep(CALL_DELAY)

    return result


# ══════════════════════════════════════════════════════════════════════════════
#  Strategy selection matrix
# ══════════════════════════════════════════════════════════════════════════════

def select_strategy(
    rsi:       float | None,
    vol_ratio: float | None,
    iv_rank:   float | None,
    regime:    str,
    config:    dict,
) -> tuple[str | None, str]:
    """
    Apply the regime × IV-rank × RSI strategy matrix.

    Returns (strategy_type | None, rationale_string).
    strategy_type: "CSP" | "PUT_SPREAD" | "OTM_PUT_SPREAD" | "CALL_SPREAD" | None
    """
    ind         = config.get("indicators", {})
    rsi_thresh  = ind.get("rsi_oversold", 25)
    iv_min_sell = ind.get("iv_rank_min_sell", 40)
    iv_max_buy  = ind.get("iv_rank_max_buy", 30)
    vol_min     = ind.get("volume_ratio_min", 1.2)
    filt        = config.get("filters", {})
    min_price   = filt.get("min_stock_price", 15.0)   # checked upstream, not here

    # ── Hard gates ────────────────────────────────────────────────────────────
    if regime == "bear":
        return None, "bear regime — stand aside"
    if iv_rank is None:
        return None, "IV rank not available (< 30 days history)"
    if rsi is None:
        return None, "RSI not available"
    if rsi >= rsi_thresh:
        return None, f"RSI {rsi:.1f} at/above threshold {rsi_thresh}"
    if vol_ratio is None or vol_ratio < vol_min:
        vr_s = f"{vol_ratio:.2f}" if vol_ratio is not None else "n/a"
        return None, f"vol ratio {vr_s} below min {vol_min}"

    # ── Strategy matrix ───────────────────────────────────────────────────────
    if regime in ("bull", "recovery"):
        if iv_rank >= iv_min_sell:
            label = "CSP"
            note  = f"RSI extreme ({rsi:.1f})" if rsi < 20 else f"IV rank {iv_rank:.0f}"
            return label, f"{regime} + {note} >= {iv_min_sell}"
        if iv_rank < iv_max_buy and rsi < 20:
            return "CALL_SPREAD", f"IV cheap ({iv_rank:.0f} < {iv_max_buy}), RSI extreme ({rsi:.1f})"
        return None, (f"IV rank {iv_rank:.0f} in neutral zone "
                      f"(sell >= {iv_min_sell}, buy < {iv_max_buy})")

    if regime == "mild_correction":
        if iv_rank >= 50:
            if rsi < 20:
                return "CSP", f"high conviction: RSI {rsi:.1f}, IV rank {iv_rank:.0f}"
            return "PUT_SPREAD", f"mild_correction + IV rank {iv_rank:.0f} >= 50 (capped risk)"
        return None, f"IV rank {iv_rank:.0f} < 50 in mild correction — insufficient premium"

    if regime in ("correction", "geopolitical_shock"):
        if iv_rank >= 60 and rsi < 20:
            return "OTM_PUT_SPREAD", f"extreme setup: RSI {rsi:.1f}, IV rank {iv_rank:.0f}"
        return None, f"{regime}: need IV rank >= 60 and RSI < 20 (got {iv_rank:.0f}, {rsi:.1f})"

    return None, f"no strategy defined for regime '{regime}'"


# ══════════════════════════════════════════════════════════════════════════════
#  Screening
# ══════════════════════════════════════════════════════════════════════════════

def screen_candidates(
    iv_cache:    dict,
    signal_data: dict,
    regime:      str,
    config:      dict,
) -> list[dict]:
    """
    Apply all filters and the strategy matrix to produce the candidate list.
    """
    min_price = config.get("filters", {}).get("min_stock_price", 15.0)
    candidates = []

    for sym, iv_data in iv_cache.items():
        iv_rank    = iv_data.get("iv_rank")        # None if insufficient history
        iv_current = iv_data.get("iv_current")
        near_earn  = iv_data.get("near_earnings", False)
        next_earn  = iv_data.get("next_earnings")

        sig   = signal_data.get(sym, {})
        rsi   = sig.get("rsi")
        vr    = sig.get("vol_ratio")
        close = sig.get("close")

        # Price gate (coarse — options liquidity proxy)
        if close is not None and close < min_price:
            continue

        strategy, rationale = select_strategy(rsi, vr, iv_rank, regime, config)
        if strategy is None:
            continue

        candidates.append({
            "symbol":        sym,
            "rsi":           rsi,
            "vol_ratio":     vr,
            "iv_rank":       iv_rank,
            "iv_current":    iv_current,
            "price":         close,
            "near_earnings": near_earn,
            "next_earnings": next_earn,
            "regime":        regime,
            "strategy":      strategy,
            "rationale":     rationale,
        })

    # Sort by IV rank descending (highest premium opportunity first)
    candidates.sort(key=lambda c: c["iv_rank"] or 0, reverse=True)
    return candidates


# ══════════════════════════════════════════════════════════════════════════════
#  Output writers
# ══════════════════════════════════════════════════════════════════════════════

def save_candidates(candidates: list[dict], regime: str, n_screened: int, n_iv: int) -> None:
    output = {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "regime":         regime,
        "n_universe":     n_screened,
        "n_with_iv_rank": n_iv,
        "n_candidates":   len(candidates),
        "candidates":     candidates,
    }
    with open(CANDIDATES_PATH, "w") as f:
        json.dump(output, f, indent=2)


def append_to_picks_history(candidates: list[dict]) -> int:
    """
    Append new simulated picks to options_picks_history.json.
    Deduplicates by (symbol, screened_date) so re-runs don't double-count.
    Returns number of new records added.
    """
    today_s  = date.today().strftime("%Y-%m-%d")
    history  = load_picks_history()
    existing = {(r["symbol"], r["screened_date"]) for r in history}
    added    = 0

    for c in candidates:
        key = (c["symbol"], today_s)
        if key in existing:
            continue
        history.append({
            "symbol":              c["symbol"],
            "screened_date":       today_s,
            "regime":              c["regime"],
            "rsi_at_screen":       c["rsi"],
            "vol_ratio":           c["vol_ratio"],
            "iv_rank_at_screen":   c["iv_rank"],
            "iv_current_at_screen": c["iv_current"],
            "price_at_screen":     c["price"],
            "near_earnings":       c["near_earnings"],
            "next_earnings":       c["next_earnings"],
            "strategy_recommended": c["strategy"],
            "research_mode":       True,
            "phase":               1,
            "outcome_tracked":     False,   # Phase 3 signal_analyzer fills this
            "exit_date":           None,
            "exit_reason":         None,
            "pnl":                 None,
            "returns":             {},
        })
        existing.add(key)
        added += 1

    if added:
        save_picks_history(history)
    return added


# ══════════════════════════════════════════════════════════════════════════════
#  Main
# ══════════════════════════════════════════════════════════════════════════════

def run() -> dict:
    print(f"\n{'='*60}")
    print(f" Options Screener  (research mode — no orders)")
    print(f"{'='*60}")

    config   = load_config()
    iv_cache = load_iv_rank_cache()

    if not iv_cache:
        print("  WARNING: iv_rank_cache.json empty — run iv_tracker first")
        return {"candidates": 0, "skipped": True}

    # ── Regime ────────────────────────────────────────────────────────────────
    regime = get_regime()
    print(f"  Regime   : {regime.upper()}")

    if regime == "bear":
        print("  Bear regime — standing aside, no candidates generated")
        save_candidates([], regime, len(iv_cache), 0)
        return {"regime": regime, "candidates": 0}

    # ── Eligible symbols (those with a valid IV rank) ──────────────────────
    iv_eligible = [
        sym for sym, data in iv_cache.items()
        if data.get("iv_rank") is not None
    ]
    print(f"  Universe : {len(iv_cache)} tracked  |  IV rank ready: {len(iv_eligible)}")

    if not iv_eligible:
        print("  No symbols with IV rank yet — iv_history still building")
        save_candidates([], regime, len(iv_cache), 0)
        return {"regime": regime, "candidates": 0}

    # ── Signal data ────────────────────────────────────────────────────────
    print(f"  Fetching RSI + volume data ({len(iv_eligible)} symbols)...")
    signal_data = fetch_signal_data(iv_eligible)
    print(f"  Signal data received: {len(signal_data)} symbols")

    # ── Screen ─────────────────────────────────────────────────────────────
    candidates = screen_candidates(iv_cache, signal_data, regime, config)
    print(f"\n  Candidates: {len(candidates)}")

    # ── Print top candidates ──────────────────────────────────────────────
    if candidates:
        print(f"\n  {'SYM':<6} {'RSI':>5} {'VR':>5} {'IV%':>5} {'STRAT':<12} NOTE")
        print(f"  {'-'*60}")
        for c in candidates[:15]:
            earn_flag = " *EARN*" if c["near_earnings"] else ""
            print(f"  {c['symbol']:<6} {(c['rsi'] or 0):>5.1f} "
                  f"{(c['vol_ratio'] or 0):>5.2f} "
                  f"{(c['iv_rank'] or 0):>5.0f} "
                  f"{c['strategy']:<12} {earn_flag}")
        if len(candidates) > 15:
            print(f"  ... and {len(candidates) - 15} more")

    # ── Save outputs ────────────────────────────────────────────────────────
    save_candidates(candidates, regime, len(iv_cache), len(iv_eligible))
    n_added = append_to_picks_history(candidates)
    print(f"\n  Saved    : options_candidates.json  ({len(candidates)} candidates)")
    print(f"  History  : {n_added} new picks added to options_picks_history.json")
    print()

    return {
        "regime":     regime,
        "screened":   len(iv_eligible),
        "candidates": len(candidates),
        "picks_added": n_added,
    }


if __name__ == "__main__":
    run()
