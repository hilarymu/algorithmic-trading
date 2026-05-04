"""
research_layer.py
Automated research layer for the RSI self-improvement loop.

Finds mean-reversion candidates BEYOND the mechanical screener by:
  1. Fetching daily price data for a focused S&P 500 watchlist via Alpaca
  2. Computing RSI, Bollinger Band distance, 200MA distance, volume ratio
  3. Identifying oversold candidates (RSI < 40) regardless of 200MA status
  4. Calling Claude API to generate research-backed picks with thesis

This runs as Step 5 in rsi_main.py and is the primary pick engine when
the mechanical screener finds 0 actionable picks.
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

PROJECT_DIR = Path(__file__).parent.parent
ALPACA_CONFIG_PATH = PROJECT_DIR / "alpaca_config.json"
RESEARCH_PICKS_PATH = PROJECT_DIR / "research_picks.json"

# ── Watchlist: S&P 500 stocks most likely to offer mean-reversion setups ──────
# Covers sectors prone to stock-specific pullbacks within long-term uptrends.
WATCHLIST = [
    # Healthcare / Med-devices / Pharma
    "ABT", "JNJ", "PFE", "MRK", "LLY", "ABBV", "BMY", "AMGN", "GILD",
    "CVS", "CI", "HUM", "UNH", "MDT", "SYK", "BSX", "EW", "BAX", "HOLX",
    # Consumer Discretionary
    "NKE", "SBUX", "MCD", "TGT", "LOW", "HD", "RCL", "CCL", "MAR", "HLT",
    "BKNG", "EXPE", "M", "KSS", "BBWI", "PVH", "RL", "TPR",
    # Consumer Staples
    "PG", "KO", "PEP", "WMT", "COST", "CL", "EL", "MO", "PM", "STZ", "K",
    # Financials — banks, asset managers, cards
    "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "AXP", "COF", "DFS",
    "V", "MA", "BX", "KKR", "APO", "SCHW", "TFC", "USB",
    # Energy
    "XOM", "CVX", "COP", "EOG", "DVN", "MPC", "VLO", "PSX", "OXY",
    "HAL", "SLB", "BKR", "FANG",
    # Technology — potential laggards / value plays
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

# ── Configuration ─────────────────────────────────────────────────────────────
MULTI_BARS_URL   = "https://data.alpaca.markets/v2/stocks/bars"
CALENDAR_LOOKBACK = 500   # calendar days — enough for 220+ trading days
BAR_LIMIT         = 220   # bars requested per symbol (covers 200MA + RSI + BB)
BATCH_SIZE        = 30    # symbols per Alpaca request
RSI_PERIOD        = 14
BB_PERIOD         = 20
BB_STD            = 2.0
VOL_PERIOD        = 20
MA200_PERIOD      = 200
RESEARCH_RSI_CAP  = 40    # include stocks with RSI below this (vs screener's 35)
TOP_N_CANDIDATES  = 15    # pass this many to Claude for analysis


# ── Alpaca data fetch ─────────────────────────────────────────────────────────

def _load_alpaca_config():
    with open(ALPACA_CONFIG_PATH) as f:
        cfg = json.load(f)
    return cfg["api_key"], cfg["api_secret"]


def _fetch_multi_bars(symbols, api_key, api_secret):
    """
    Fetch up to BAR_LIMIT daily bars for each symbol using Alpaca's
    multi-symbol endpoint.  Returns {symbol: [bar_dicts]}.
    """
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
            "symbols":   ",".join(batch),
            "timeframe": "1Day",
            "start":     start,
            "end":       end,
            "limit":     BAR_LIMIT,
            "feed":      "iex",
            "adjustment":"all",
        }
        base_url = MULTI_BARS_URL + "?" + urllib.parse.urlencode(params)

        batch_bars       = {s: [] for s in batch}
        next_page_token  = None

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
                except (urllib.error.URLError, TimeoutError, OSError):
                    attempt += 1
                    if attempt < 3:
                        time.sleep(2 ** attempt)
                    else:
                        raise
            if not next_page_token:
                break

        for sym, bars in batch_bars.items():
            if bars:
                bars.sort(key=lambda b: b["t"])
                all_bars[sym] = bars[-BAR_LIMIT:]

        time.sleep(0.25)  # polite rate limiting between batches

    return all_bars


# ── Technical indicator calculations ─────────────────────────────────────────

def _compute_rsi(closes, period=RSI_PERIOD):
    """Wilder's smoothed RSI."""
    if len(closes) < period + 2:
        return None
    deltas = [closes[j] - closes[j - 1] for j in range(1, len(closes))]
    gains  = [max(d, 0.0) for d in deltas]
    losses = [abs(min(d, 0.0)) for d in deltas]

    avg_gain = mean(gains[:period])
    avg_loss = mean(losses[:period])

    for j in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[j]) / period
        avg_loss = (avg_loss * (period - 1) + losses[j]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _compute_bb_pct(closes, period=BB_PERIOD, num_std=BB_STD):
    """
    % distance from lower Bollinger Band.
    Negative = below the band (oversold per BB signal).
    """
    if len(closes) < period + 1:
        return None
    window = closes[-(period + 1):-1]
    sma    = mean(window)
    sd     = stdev(window) if len(window) > 1 else 0.0
    lower  = sma - num_std * sd
    if lower <= 0:
        return None
    return round(((closes[-1] - lower) / lower) * 100, 2)


def _compute_ma200_pct(closes):
    """% above (+) or below (-) 200-day simple moving average."""
    if len(closes) < MA200_PERIOD:
        return None
    ma200 = mean(closes[-MA200_PERIOD:])
    if ma200 <= 0:
        return None
    return round(((closes[-1] - ma200) / ma200) * 100, 2)


def _compute_vol_ratio(volumes):
    """Today's volume / 20-day average volume."""
    if len(volumes) < VOL_PERIOD + 1:
        return None
    avg_vol = mean(volumes[-(VOL_PERIOD + 1):-1])
    if avg_vol <= 0:
        return None
    return round(volumes[-1] / avg_vol, 2)


def _compute_technicals(bars):
    """Return a dict of indicators for a symbol, or None if insufficient data."""
    if not bars or len(bars) < 25:
        return None
    closes  = [b["c"] for b in bars]
    volumes = [b["v"] for b in bars]
    return {
        "price":          round(closes[-1], 2),
        "rsi":            _compute_rsi(closes),
        "pct_from_lower_bb": _compute_bb_pct(closes),
        "pct_vs_200ma":   _compute_ma200_pct(closes),
        "vol_ratio":      _compute_vol_ratio(volumes),
    }


# ── Candidate filtering & scoring ─────────────────────────────────────────────

def _build_candidates(tech_map):
    """
    Filter symbols to those with RSI < RESEARCH_RSI_CAP.
    Score = 0.6 * rsi_component + 0.4 * bb_component.
    Returns top TOP_N_CANDIDATES sorted by score descending.
    """
    candidates = []
    for sym, tech in tech_map.items():
        if tech is None:
            continue
        rsi = tech.get("rsi")
        if rsi is None or rsi >= RESEARCH_RSI_CAP:
            continue

        rsi_score = max(0.0, (RESEARCH_RSI_CAP - rsi) / RESEARCH_RSI_CAP)

        bb_pct    = tech.get("pct_from_lower_bb")
        bb_score  = 0.0
        if bb_pct is not None:
            bb_score = max(0.0, min(1.0, -bb_pct / 10.0))

        composite = round(rsi_score * 0.6 + bb_score * 0.4, 4)

        candidates.append({
            "symbol":            sym,
            "price":             tech["price"],
            "rsi":               rsi,
            "pct_from_lower_bb": bb_pct,
            "pct_vs_200ma":      tech["pct_vs_200ma"],
            "vol_ratio":         tech["vol_ratio"],
            "oversold_score":    composite,
        })

    candidates.sort(key=lambda x: x["oversold_score"], reverse=True)
    return candidates[:TOP_N_CANDIDATES]


# ── Claude API call ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the research layer of an autonomous mean-reversion trading system for S&P 500 stocks.

Your role: identify which technically oversold stocks have strong fundamental reasons to revert to the mean — not just noise or structural decline.

You will be given real-time technical data (RSI, Bollinger Band position, 200MA distance, volume) alongside market regime context. You apply your knowledge of company fundamentals, sector dynamics, competitive positioning, and common catalysts to determine which pullbacks are temporary vs structural.

Be direct and specific. Each pick must have a clear 2-3 sentence thesis, a specific catalyst, and a named key risk. No hedging. State your confidence level honestly. Avoid recommending stocks whose decline is clearly fundamental (patent cliff, regulatory ban, structural disruption) unless there is a compelling near-term catalyst."""


def _get_api_key():
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    try:
        with open(ALPACA_CONFIG_PATH) as f:
            cfg = json.load(f)
        return cfg.get("gemini_api_key")
    except Exception:
        return None


def _call_llm(candidates, regime, total_scanned):
    """
    Pass top candidates to Gemini with market context.
    Returns (analysis_text, source_str).
    """
    try:
        from google import genai
        from google.genai import types as genai_types
    except ImportError:
        return None, "google_genai_not_installed"

    api_key = _get_api_key()
    if not api_key:
        return None, "no_api_key"

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Format candidates table
    lines = []
    for c in candidates:
        ma  = f"{c['pct_vs_200ma']:+.1f}%" if c["pct_vs_200ma"] is not None else "N/A"
        bb  = f"{c['pct_from_lower_bb']:+.1f}%" if c["pct_from_lower_bb"] is not None else "N/A"
        vol = f"{c['vol_ratio']:.2f}x" if c["vol_ratio"] is not None else "N/A"
        lines.append(
            f"  {c['symbol']:<6} price=${c['price']:<8} RSI={c['rsi']:<6} "
            f"vs200MA={ma:<8} vsLowerBB={bb:<8} vol={vol}"
        )
    candidate_text = "\n".join(lines)

    user_msg = f"""Date: {today}
Market regime: {regime}
Watchlist scanned: {total_scanned} S&P 500 stocks
Oversold candidates found (RSI < {RESEARCH_RSI_CAP}): {len(candidates)}

The mechanical screener (RSI<35, below lower BB, above 200MA, vol>1.5x) found 0 actionable picks today. The market is at or near ATH -- stocks that are oversold are predominantly BELOW their 200MA, which blocks the mechanical screen.

These are the top {len(candidates)} oversold candidates from the watchlist scan, sorted by oversold composite score:

{candidate_text}

Identify the TOP 3 mean-reversion candidates. For each provide:

1. SYMBOL -- one-line headline
   - Thesis: why this stock's decline is temporary/overdone (2-3 sentences, specific)
   - Catalyst: the specific near-term event or dynamic that drives recovery
   - Risk: the one thing that could prevent the rebound
   - Confidence: High / Medium / Low

Focus on companies where the stock is pricing in excessive pessimism relative to business fundamentals. Be specific about what you know of these companies. Do not pick all three from the same sector."""

    max_attempts = 3
    retry_delay  = 45   # seconds between attempts (503 spikes are usually brief)
    last_err     = None
    for attempt in range(1, max_attempts + 1):
        try:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model    = "gemini-2.5-flash",
                contents = user_msg,
                config   = genai_types.GenerateContentConfig(
                    system_instruction = SYSTEM_PROMPT,
                    max_output_tokens  = 8192,
                    temperature        = 0.3,
                ),
            )
            if attempt > 1:
                print(f"  [research_layer] Gemini succeeded on attempt {attempt}.")
            return response.text, "gemini_api"
        except Exception as e:
            last_err = e
            if attempt < max_attempts:
                print(f"  [research_layer] Gemini attempt {attempt} failed ({e}) — retrying in {retry_delay}s...")
                import time as _time
                _time.sleep(retry_delay)
    return str(last_err), "error"


def _fallback_analysis(candidates, source="error"):
    """Mechanical fallback when Gemini API is unavailable or not configured."""
    if not candidates:
        return "No oversold candidates found in watchlist scan. Market breadth strong — no mean-reversion setups available."

    lines = [
        f"Top {min(3, len(candidates))} oversold candidates (mechanical screen — Gemini qualitative filter unavailable):",
        "",
    ]
    for c in candidates[:3]:
        ma_str  = f"{c['pct_vs_200ma']:+.1f}% vs 200MA" if c["pct_vs_200ma"] is not None else ""
        bb_str  = f", {c['pct_from_lower_bb']:+.1f}% vs LowerBB" if c["pct_from_lower_bb"] is not None else ""
        vol_str = f", vol {c['vol_ratio']:.1f}x" if c["vol_ratio"] is not None else ""
        lines.append(f"  {c['symbol']}: RSI {c['rsi']}, ${c['price']} {ma_str}{bb_str}{vol_str}")

    lines.append("")
    if source == "no_api_key":
        lines += [
            "To enable Gemini-powered research picks:",
            '  Add "gemini_api_key": "AIza..." to alpaca_config.json',
            '  Or set GEMINI_API_KEY environment variable',
        ]
    else:
        lines.append("Gemini API temporarily unavailable (high demand / transient error) — will retry next run.")

    return "\n".join(lines)


# ── Main entry point ──────────────────────────────────────────────────────────

def run(regime="unknown"):
    """
    Fetch technicals for watchlist, identify oversold candidates,
    call Claude for research-backed picks.
    Saves results to research_picks.json.
    Returns result dict.
    """
    print(f"  [research_layer] Scanning {len(WATCHLIST)} symbols for oversold candidates (RSI < {RESEARCH_RSI_CAP})...")

    try:
        api_key, api_secret = _load_alpaca_config()
    except Exception as e:
        print(f"  [research_layer] ERROR loading config: {e}")
        return {"source": "error", "error": str(e)}

    # ── Fetch price data ───────────────────────────────────────────────────────
    try:
        bars_map = _fetch_multi_bars(WATCHLIST, api_key, api_secret)
        print(f"  [research_layer] Fetched data for {len(bars_map)}/{len(WATCHLIST)} symbols")
    except Exception as e:
        print(f"  [research_layer] ERROR fetching bars: {e}")
        return {"source": "error", "error": str(e)}

    # ── Compute technicals ─────────────────────────────────────────────────────
    tech_map = {}
    for sym, bars in bars_map.items():
        try:
            tech_map[sym] = _compute_technicals(bars)
        except Exception:
            tech_map[sym] = None

    # ── Filter & rank candidates ───────────────────────────────────────────────
    candidates = _build_candidates(tech_map)
    print(f"  [research_layer] {len(candidates)} oversold candidates found")

    if candidates:
        top5 = [f"{c['symbol']}(RSI={c['rsi']})" for c in candidates[:5]]
        print(f"  [research_layer] Top: {', '.join(top5)}")

    # ── Gemini analysis ────────────────────────────────────────────────────────
    analysis, source = _call_llm(candidates, regime, len(bars_map))

    if source == "gemini_api":
        print(f"  [research_layer] Gemini research analysis complete.")
    else:
        print(f"  [research_layer] Gemini unavailable ({source}) — using fallback.")
        analysis = _fallback_analysis(candidates, source=source)
        source   = "fallback"

    # ── Save results ───────────────────────────────────────────────────────────
    result = {
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "regime":          regime,
        "symbols_scanned": len(bars_map),
        "candidates_found": len(candidates),
        "top_candidates":  candidates,
        "analysis":        analysis,
        "source":          source,
    }

    import os as _os
    tmp = RESEARCH_PICKS_PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(result, f, indent=2)
    _os.replace(tmp, RESEARCH_PICKS_PATH)

    print(f"  [research_layer] Saved to research_picks.json")
    return result


if __name__ == "__main__":
    result = run()
    print("\n--- Analysis ---")
    print(result.get("analysis", "No analysis"))
