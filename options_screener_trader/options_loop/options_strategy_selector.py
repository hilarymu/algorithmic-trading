"""
options_strategy_selector.py
=============================
Phase 2: For each screened candidate, find the specific option contract
(expiry, strike, legs) to trade.

Contract selection logic
------------------------
  CSP (Cash-Secured Put):
    Short put, target delta ~0.30, DTE 21-50 (ideal 35)

  PUT_SPREAD (Bull Put Credit Spread):
    Short put delta ~0.30, long put delta ~0.15, same expiry
    Width determined by strike difference

  OTM_PUT_SPREAD (More OTM Bull Put Spread):
    Short put delta ~0.20, long put delta ~0.10

  CALL_SPREAD (Bull Call Debit Spread):
    Long call delta ~0.50 (ATM), short call delta ~0.25

Method
------
1. Estimate delta-targeted strike via Black-Scholes analytical inversion
2. Round to nearest standard strike increment
3. Build 5 candidate contract symbols (target +/- 2 increments)
4. Batch-fetch Alpaca option snapshots (greeks.delta, latestQuote, openInterest)
5. Pick the contract whose delta is closest to target, within liquidity filters
6. Repeat for second leg if spread strategy

Output
------
Writes options_pending_entries.json — one entry per selected candidate.
Each entry has status "pending_review" until executor processes it.
"""

import json
import math
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ── Shared helpers from iv_tracker ────────────────────────────────────────────
import sys
sys.path.insert(0, str(Path(__file__).parent))
from iv_tracker import (
    _get, _standard_increment, _target_expirations,
    DATA_BASE, TRADING_BASE, CALL_DELAY,
)

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_DIR      = Path(__file__).parent.parent
DATA_DIR         = PROJECT_DIR / "data"
CONFIG_PATH      = PROJECT_DIR / "options_config.json"
PENDING_PATH     = DATA_DIR / "options_pending_entries.json"
IV_RANK_PATH     = DATA_DIR / "iv_rank_cache.json"

# ── Constants ─────────────────────────────────────────────────────────────────
RISK_FREE_RATE   = 0.05          # annualised, used only for strike estimation
DELTA_TOLERANCE  = 0.12          # accept if |delta_actual - target| <= this
SNAPSHOT_BATCH   = 100           # contracts per snapshot API call


# ==============================================================================
#  Black-Scholes helpers (strike estimation from target delta)
# ==============================================================================

def _norm_cdf(x: float) -> float:
    """Standard normal CDF using math.erf."""
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def _norm_inv(p: float) -> float:
    """
    Inverse normal CDF (probit).  Rational approximation; error < 5e-4.
    Valid for p in (0, 1).
    """
    c = [2.515517, 0.802853, 0.010328]
    d = [1.432788, 0.189269, 0.001308]

    def _inner(t):
        num = c[0] + c[1] * t + c[2] * t * t
        den = 1.0 + d[0] * t + d[1] * t * t + d[2] * t * t * t
        return t - num / den

    if p < 1e-8 or p > 1 - 1e-8:
        raise ValueError(f"p={p} out of range")
    if p < 0.5:
        t = math.sqrt(-2.0 * math.log(p))
        return -_inner(t)
    else:
        t = math.sqrt(-2.0 * math.log(1.0 - p))
        return _inner(t)


def _put_strike_for_delta(S: float, iv: float, T: float,
                          target_delta_abs: float,
                          r: float = RISK_FREE_RATE) -> float:
    """
    Analytically solve for the put strike K that gives the target
    absolute delta (e.g. pass 0.30 for a -0.30 delta put).

    Delta_put = N(d1) - 1  =>  N(d1) = 1 - target_delta_abs
    d1 = [ln(S/K) + (r + 0.5*iv^2)*T] / (iv*sqrt(T))
    Solving for K:
        K = S * exp(-d1_target * iv * sqrt(T) + (r + 0.5*iv^2)*T)
    """
    if T <= 0 or iv <= 0 or S <= 0:
        return S * (1.0 - target_delta_abs)   # degenerate fallback
    d1_target = _norm_inv(1.0 - target_delta_abs)
    iv_sqrtT  = iv * math.sqrt(T)
    drift     = (r + 0.5 * iv * iv) * T
    return S * math.exp(-d1_target * iv_sqrtT + drift)


def _call_strike_for_delta(S: float, iv: float, T: float,
                           target_delta: float,
                           r: float = RISK_FREE_RATE) -> float:
    """
    Analytically solve for the call strike K that gives the target delta
    (e.g. pass 0.50 for an ATM call, 0.25 for an OTM call).

    Delta_call = N(d1) =>  d1 = N_inv(target_delta)
    K = S * exp(-d1_target * iv * sqrt(T) + (r + 0.5*iv^2)*T)
    """
    if T <= 0 or iv <= 0 or S <= 0:
        return S   # fallback
    d1_target = _norm_inv(target_delta)
    iv_sqrtT  = iv * math.sqrt(T)
    drift     = (r + 0.5 * iv * iv) * T
    return S * math.exp(-d1_target * iv_sqrtT + drift)


# ==============================================================================
#  BSM option pricing (used for synthetic leg estimates when Alpaca is dark)
# ==============================================================================

def _bsm_d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    return (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))


def _bsm_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes theoretical put price."""
    if T <= 0 or sigma <= 0:
        return max(0.0, K - S)
    d1 = _bsm_d1(S, K, T, r, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def _bsm_put_delta(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes put delta (negative value, e.g. -0.30)."""
    if T <= 0 or sigma <= 0:
        return -1.0 if K > S else 0.0
    d1 = _bsm_d1(S, K, T, r, sigma)
    return _norm_cdf(d1) - 1.0


def _bsm_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes theoretical call price."""
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K)
    d1 = _bsm_d1(S, K, T, r, sigma)
    d2 = d1 - sigma * math.sqrt(T)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def _bsm_call_delta(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes call delta (positive value, e.g. 0.50)."""
    if T <= 0 or sigma <= 0:
        return 1.0 if S > K else 0.0
    d1 = _bsm_d1(S, K, T, r, sigma)
    return _norm_cdf(d1)


# ==============================================================================
#  Contract symbol utilities
# ==============================================================================

def _occ_symbol(underlying: str, expiry: date,
                opt_type: str, strike: float) -> str:
    """
    Build an OCC option symbol.
    Format: {UNDERLYING}{YYMMDD}{C|P}{STRIKE_8DIGIT}
    Example: AAPL260516P00250000
    """
    date_str   = expiry.strftime("%y%m%d")
    strike_int = int(round(strike * 1000))
    return f"{underlying}{date_str}{opt_type.upper()}{strike_int:08d}"


def _candidate_strikes(target_K: float, price: float,
                        n_each_side: int = 2) -> list[float]:
    """
    Return 2*n_each_side + 1 standard strikes centred near target_K,
    all OTM relative to price for a put (i.e. strike <= price).
    """
    inc  = _standard_increment(price)
    base = round(round(target_K / inc) * inc, 2)
    strikes = set()
    for offset in range(-n_each_side, n_each_side + 1):
        s = round(base + offset * inc, 2)
        if s > 0:
            strikes.add(s)
    return sorted(strikes)


# ==============================================================================
#  Alpaca snapshot fetch (rich version — includes greeks + quotes + OI)
# ==============================================================================

def fetch_option_snapshots(contract_symbols: list[str]) -> dict:
    """
    Batch-fetch Alpaca options snapshots.
    Returns {contract_symbol: {delta, iv, bid, ask, open_interest}}.
    """
    result = {}
    for i in range(0, len(contract_symbols), SNAPSHOT_BATCH):
        batch = contract_symbols[i : i + SNAPSHOT_BATCH]
        url   = (f"{DATA_BASE}/v1beta1/options/snapshots"
                 f"?symbols={','.join(batch)}")
        data  = _get(url)
        if not data:
            time.sleep(CALL_DELAY)
            continue
        snaps = data.get("snapshots", data)
        for sym, snap in snaps.items():
            try:
                greeks  = snap.get("greeks") or {}
                quote   = snap.get("latestQuote") or {}
                delta   = greeks.get("delta")
                bid     = quote.get("bp")
                ask     = quote.get("ap")
                iv      = snap.get("impliedVolatility")
                oi      = snap.get("openInterest", 0)
                if delta is None or bid is None or ask is None:
                    continue
                result[sym] = {
                    "delta":          float(delta),
                    "iv":             float(iv) if iv else None,
                    "bid":            float(bid),
                    "ask":            float(ask),
                    "open_interest":  int(oi),
                }
            except (TypeError, ValueError, KeyError):
                pass
        time.sleep(CALL_DELAY)
    return result


# ==============================================================================
#  Alpaca trading-API contract listing (finds real, tradeable OCC symbols)
# ==============================================================================

def fetch_listed_contracts(
    symbol:   str,
    expiry:   date,
    opt_type: str,      # "P" or "C"
    target_K: float,
    price:    float,
) -> list[dict]:
    """
    Query the Alpaca trading API for real listed option contracts near
    the target strike and expiry.

    Uses TRADING_BASE (/v2/options/contracts) — separate from the market-
    data snapshot endpoint and works without OPRA.  Returns a list of
    {symbol, strike, expiry} dicts sorted by proximity to target_K,
    or an empty list if the API call fails or returns nothing.
    """
    inc = _standard_increment(price)
    lo  = max(0.01, round(target_K - 5 * inc, 2))
    hi  = round(target_K + 5 * inc, 2)
    exp = expiry.strftime("%Y-%m-%d")

    url = (
        f"{TRADING_BASE}/options/contracts"
        f"?underlying_symbols={symbol}"
        f"&type={'put' if opt_type == 'P' else 'call'}"
        f"&expiration_date_gte={exp}&expiration_date_lte={exp}"
        f"&status=active"
        f"&strike_price_gte={lo}&strike_price_lte={hi}"
        f"&limit=25"
    )
    data = _get(url)
    if not data:
        return []

    contracts = data.get("option_contracts", [])
    result = []
    for c in contracts:
        try:
            sym    = c["symbol"]
            strike = float(c["strike_price"])
            exp_d  = c.get("expiration_date", exp)
            if sym and strike > 0:
                result.append({"symbol": sym, "strike": strike, "expiry": exp_d})
        except (KeyError, TypeError, ValueError):
            pass

    result.sort(key=lambda c: abs(c["strike"] - target_K))
    return result


# ==============================================================================
#  Config / cache loaders
# ==============================================================================

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_iv_rank_cache() -> dict:
    if not IV_RANK_PATH.exists():
        return {}
    with open(IV_RANK_PATH) as f:
        return json.load(f)


def load_pending_entries() -> list:
    if not PENDING_PATH.exists():
        return []
    with open(PENDING_PATH) as f:
        return json.load(f)


def save_pending_entries(entries: list) -> None:
    with open(PENDING_PATH, "w") as f:
        json.dump(entries, f, indent=2)


# ==============================================================================
#  BSM synthetic leg (fallback when Alpaca options feed returns no data)
# ==============================================================================

def _bsm_synthetic_leg(
    symbol:           str,
    expiry:           date,
    opt_type:         str,
    target_delta_abs: float,
    target_K:         float,
    price:            float,
    iv:               float,
    T:                float,
    dte:              int,
) -> dict | None:
    """
    Build a synthetic leg dict using Black-Scholes when Alpaca returns no
    options contract data (consistent behaviour on paper accounts).

    All values are theoretical estimates.  The entry is flagged
    data_source='bsm_estimated' so the executor and monitor know to treat
    quotes as indicative, not live fills.
    """
    inc    = _standard_increment(price)
    strike = round(round(target_K / inc) * inc, 2)
    if strike <= 0:
        return None

    r = RISK_FREE_RATE
    if opt_type == "P":
        mid   = _bsm_put_price(price, strike, T, r, iv)
        delta = _bsm_put_delta(price, strike, T, r, iv)
    else:
        mid   = _bsm_call_price(price, strike, T, r, iv)
        delta = _bsm_call_delta(price, strike, T, r, iv)

    if mid < 0.05:
        return None   # theoretically too cheap — skip

    # Synthetic bid/ask: assume 15% half-spread (conservative for illiquid paper)
    half = mid * 0.075
    bid  = round(max(0.01, mid - half), 2)
    ask  = round(mid + half, 2)
    mid  = round(mid, 4)

    contract = _occ_symbol(symbol, expiry, opt_type, strike)
    print(f"      BSM synthetic (last resort): {contract}  delta={delta:.2f}  mid=${mid:.2f}  (contract listing unavailable)")

    return {
        "contract":      contract,
        "strike":        strike,
        "expiry":        expiry.strftime("%Y-%m-%d"),
        "dte":           dte,
        "opt_type":      opt_type,
        "delta":         round(delta, 4),
        "bid":           bid,
        "ask":           ask,
        "mid":           mid,
        "open_interest": None,
        "spread_pct":    round((ask - bid) / mid, 4) if mid > 0 else None,
        "iv":            round(iv, 4),
        "data_source":   "bsm_estimated",
    }


# ==============================================================================
#  Core: pick one option leg
# ==============================================================================

def _pick_leg(
    symbol:           str,
    expiry:           date,
    opt_type:         str,    # "P" or "C"
    target_delta_abs: float,
    price:            float,
    iv:               float,
    config:           dict,
) -> dict | None:
    """
    Find the best-matching contract leg for the given parameters.

    Path A — Alpaca contract listing API returns real listed symbols:
        1. Try live snapshot data (greeks + quotes).
        2. If snapshots empty (normal on paper), BSM-price the real contracts
           and pick closest delta.  Orders will succeed — real OCC symbols.

    Path B — contract listing API unavailable (network / auth error):
        BSM synthetic: constructs a theoretical OCC symbol and prices it.
        Orders will likely 422 (symbol may not be listed) but the pipeline
        still produces an actionable entry for review.

    Returns a leg dict, or None if no suitable contract found.
    """
    filt       = config.get("filters", {})
    min_oi     = filt.get("min_open_interest", 500)
    max_spread = filt.get("max_bid_ask_spread_pct", 0.15)

    dte = (expiry - date.today()).days
    T   = dte / 365.0
    r   = RISK_FREE_RATE

    # Estimate target strike via BSM
    if opt_type == "P":
        target_K = _put_strike_for_delta(price, iv, T, target_delta_abs)
    else:
        target_K = _call_strike_for_delta(price, iv, T, target_delta_abs)

    # ── Path A: real listed contracts from Alpaca trading API ─────────────
    listed = fetch_listed_contracts(symbol, expiry, opt_type, target_K, price)

    if listed:
        # Try live snapshots for the verified-real contract symbols
        listed_syms = [c["symbol"] for c in listed[:10]]
        snaps = fetch_option_snapshots(listed_syms)

        if snaps:
            # Live data available — pick best by delta + liquidity
            best      = None
            best_diff = float("inf")
            for sym, snap in snaps.items():
                delta_raw = snap["delta"]
                delta_abs = abs(delta_raw)
                bid, ask  = snap["bid"], snap["ask"]
                oi        = snap["open_interest"]

                if oi < min_oi:
                    continue
                mid = (bid + ask) / 2.0
                if mid < 0.01:
                    continue
                spread_pct = (ask - bid) / mid if mid > 0 else 999
                if spread_pct > max_spread:
                    continue

                try:
                    strike_raw = int(sym[-8:]) / 1000.0
                except (ValueError, IndexError):
                    continue
                if opt_type == "P" and strike_raw >= price * 1.02:
                    continue
                if opt_type == "C" and strike_raw <= price * 0.98:
                    continue

                diff = abs(delta_abs - target_delta_abs)
                if diff < best_diff:
                    best_diff = diff
                    best = {
                        "contract":      sym,
                        "strike":        strike_raw,
                        "expiry":        expiry.strftime("%Y-%m-%d"),
                        "dte":           dte,
                        "opt_type":      opt_type,
                        "delta":         round(delta_raw, 4),
                        "bid":           round(bid, 4),
                        "ask":           round(ask, 4),
                        "mid":           round((bid + ask) / 2.0, 4),
                        "open_interest": oi,
                        "spread_pct":    round(spread_pct, 4),
                        "iv":            round(snap["iv"], 4) if snap["iv"] else None,
                        "data_source":   "alpaca_live",
                    }

            if best and best_diff <= DELTA_TOLERANCE:
                return best

            # Live data was available but every contract failed a filter
            # (OI, spread, or delta).  Respect the filters — do NOT fall
            # through to BSM pricing, which would bypass them.
            print(f"    [{symbol}] {opt_type} live-data filters rejected all contracts "
                  f"(OI/spread/delta — not falling back to BSM)")
            return None

        # Snapshots empty (expected on paper) — BSM-price the real contracts
        best_diff = float("inf")
        best_raw  = None

        for c in listed[:10]:
            strike = c["strike"]
            if opt_type == "P" and strike >= price * 1.02:
                continue    # skip ITM puts
            if opt_type == "C" and strike <= price * 0.98:
                continue    # skip ITM calls

            if opt_type == "P":
                delta = _bsm_put_delta(price, strike, T, r, iv)
                mid   = _bsm_put_price(price, strike, T, r, iv)
            else:
                delta = _bsm_call_delta(price, strike, T, r, iv)
                mid   = _bsm_call_price(price, strike, T, r, iv)

            if mid < 0.05:
                continue

            diff = abs(abs(delta) - target_delta_abs)
            if diff < best_diff:
                best_diff = diff
                best_raw  = (c["symbol"], strike, delta, mid)

        if best_raw and best_diff <= DELTA_TOLERANCE:
            sym, strike, delta, mid = best_raw
            half = mid * 0.075
            print(f"      Real contract (BSM priced): {sym}  "
                  f"delta={delta:.2f}  mid=${mid:.2f}  (Alpaca feed dark)")
            return {
                "contract":      sym,
                "strike":        strike,
                "expiry":        expiry.strftime("%Y-%m-%d"),
                "dte":           dte,
                "opt_type":      opt_type,
                "delta":         round(delta, 4),
                "bid":           round(max(0.01, mid - half), 2),
                "ask":           round(mid + half, 2),
                "mid":           round(mid, 4),
                "open_interest": None,
                "spread_pct":    round(half * 2 / mid, 4),
                "iv":            round(iv, 4),
                "data_source":   "bsm_estimated",
            }

        # Real contracts listed but none match delta target
        print(f"    [{symbol}] {opt_type} delta mismatch — "
              f"listed contracts too far from target {target_delta_abs:.2f}")
        return None

    # ── Path B: listing API unavailable — BSM synthetic (last resort) ─────
    # Constructs a theoretical OCC symbol; orders may 422 if not listed.
    return _bsm_synthetic_leg(
        symbol, expiry, opt_type, target_delta_abs,
        target_K, price, iv, T, dte,
    )


# ==============================================================================
#  Build a pending entry for one candidate
# ==============================================================================

def _position_id(symbol: str, strategy: str, expiry: str, strike: float) -> str:
    today = date.today().strftime("%Y%m%d")
    return f"{symbol}-{today}-{strategy}-{expiry.replace('-', '')}-{int(strike)}"


def select_contract(candidate: dict, config: dict) -> dict | None:
    """
    Select the option contract(s) for one screened candidate.
    Returns a pending_entry dict or None if no suitable contract found.
    """
    symbol   = candidate["symbol"]
    strategy = candidate["strategy"]
    iv       = candidate.get("iv_current")
    price    = candidate.get("price")
    rsi      = candidate.get("rsi")
    vol_ratio = candidate.get("vol_ratio")
    iv_rank  = candidate.get("iv_rank")
    regime   = candidate.get("regime")

    if not iv or not price or iv <= 0:
        return None

    cs = config.get("contract_selection", {})

    # Find the target expiry
    expirations = _target_expirations()
    if not expirations:
        print(f"    [{symbol}] No valid expirations in DTE window")
        return None
    expiry = expirations[0]

    # Strategy-specific leg parameters
    if strategy == "CSP":
        target_delta = cs.get("target_delta_csp", 0.30)
        short_leg = _pick_leg(symbol, expiry, "P", target_delta, price, iv, config)
        if not short_leg:
            return None
        long_leg     = None
        net_credit   = short_leg["mid"]
        capital_risk = short_leg["strike"] * 100    # per contract

    elif strategy == "PUT_SPREAD":
        short_delta = cs.get("target_delta_csp", 0.30)
        long_delta  = short_delta * 0.5              # long leg half the delta
        short_leg = _pick_leg(symbol, expiry, "P", short_delta, price, iv, config)
        if not short_leg:
            return None
        long_leg  = _pick_leg(symbol, expiry, "P", long_delta,  price, iv, config)
        if not long_leg:
            return None
        if long_leg["strike"] >= short_leg["strike"]:
            return None    # legs crossed or same strike
        net_credit   = short_leg["mid"] - long_leg["mid"]
        spread_width = short_leg["strike"] - long_leg["strike"]
        capital_risk = spread_width * 100            # max loss per contract

    elif strategy == "OTM_PUT_SPREAD":
        short_delta = 0.20
        long_delta  = 0.10
        short_leg = _pick_leg(symbol, expiry, "P", short_delta, price, iv, config)
        if not short_leg:
            return None
        long_leg  = _pick_leg(symbol, expiry, "P", long_delta,  price, iv, config)
        if not long_leg:
            return None
        if long_leg["strike"] >= short_leg["strike"]:
            return None
        net_credit   = short_leg["mid"] - long_leg["mid"]
        spread_width = short_leg["strike"] - long_leg["strike"]
        capital_risk = spread_width * 100

    elif strategy == "CALL_SPREAD":
        long_delta  = cs.get("target_delta_call_buy", 0.50)
        short_delta = cs.get("target_delta_call_sell", 0.25)
        long_leg  = _pick_leg(symbol, expiry, "C", long_delta,  price, iv, config)
        if not long_leg:
            return None
        short_leg = _pick_leg(symbol, expiry, "C", short_delta, price, iv, config)
        if not short_leg:
            return None
        if short_leg["strike"] <= long_leg["strike"]:
            return None    # legs crossed
        net_credit   = long_leg["mid"] - short_leg["mid"]   # debit (negative credit)
        spread_width = short_leg["strike"] - long_leg["strike"]
        capital_risk = abs(net_credit) * 100                # max loss = debit paid

    else:
        return None

    if net_credit <= 0.05 and strategy != "CALL_SPREAD":
        print(f"    [{symbol}] Net credit too thin: ${net_credit:.2f}")
        return None

    strike_for_id = short_leg["strike"] if short_leg else long_leg["strike"]
    pid = _position_id(symbol, strategy, short_leg["expiry"] if short_leg
                       else long_leg["expiry"], strike_for_id)

    # Determine contract data source (live Alpaca vs BSM estimate)
    _src_leg = short_leg or long_leg
    contract_source = _src_leg.get("data_source", "alpaca_live") if _src_leg else "unknown"

    entry = {
        "id":              pid,
        "symbol":          symbol,
        "strategy":        strategy,
        "regime":          regime,
        "screened_date":   date.today().strftime("%Y-%m-%d"),
        "iv_rank":         iv_rank,
        "iv_current":      iv,
        "rsi":             rsi,
        "vol_ratio":       vol_ratio,
        "underlying_close": price,
        "expiry":          short_leg["expiry"] if short_leg else long_leg["expiry"],
        "dte":             short_leg["dte"] if short_leg else long_leg["dte"],
        "short_leg":       short_leg,
        "long_leg":        long_leg,
        "net_credit_est":  round(net_credit, 4),
        "capital_at_risk": round(capital_risk, 2),
        "near_earnings":   candidate.get("near_earnings", False),
        "contract_source": contract_source,
        "status":          "pending_review",
        "created_at":      datetime.now(timezone.utc).isoformat(),
    }
    return entry


# ==============================================================================
#  Main
# ==============================================================================

def run(candidates: list[dict] | None = None) -> list[dict]:
    """
    Process all screened candidates and build pending entry proposals.

    Parameters
    ----------
    candidates : list of candidate dicts from options_screener.screen_candidates().
                 If None, reads options_candidates.json written by screener.

    Returns list of pending_entry dicts (also written to options_pending_entries.json).
    """
    print(f"\n{'='*60}")
    print(f" Strategy Selector  (Phase 2)")
    print(f"{'='*60}")

    config = load_config()

    # Load candidates from disk if not passed in
    if candidates is None:
        cand_path = DATA_DIR / "options_candidates.json"
        if not cand_path.exists():
            print("  WARNING: options_candidates.json not found — run screener first")
            return []
        with open(cand_path) as f:
            cand_doc = json.load(f)
        candidates = cand_doc.get("candidates", [])

    if not candidates:
        print("  No candidates to process")
        return []

    print(f"  Processing {len(candidates)} candidates...")

    # Hard block: near-earnings naked puts
    safe = []
    for c in candidates:
        if c.get("near_earnings") and c.get("strategy") == "CSP":
            print(f"    [{c['symbol']}] SKIP — near earnings + CSP (naked put, hard block)")
            continue
        safe.append(c)

    # Existing pending entries — avoid duplicates within same screened_date
    existing = load_pending_entries()
    today    = date.today().strftime("%Y-%m-%d")
    existing_ids = {
        e["id"] for e in existing
        if e.get("screened_date") == today
    }

    new_entries = []
    for c in safe:
        symbol   = c["symbol"]
        strategy = c["strategy"]

        # Check maximum pending entries per symbol (one per day)
        candidate_id_prefix = f"{symbol}-{today}"
        if any(e.startswith(candidate_id_prefix) for e in existing_ids):
            print(f"    [{symbol}] already has a pending entry today — skipping")
            continue

        print(f"    [{symbol}] {strategy} — selecting contract...")
        entry = select_contract(c, config)
        if entry is None:
            print(f"    [{symbol}] no suitable contract found")
            continue

        leg_info = entry["short_leg"] or entry["long_leg"]
        print(f"    [{symbol}] {strategy}  {leg_info['contract']}  "
              f"delta={leg_info['delta']:.2f}  "
              f"mid=${entry['net_credit_est']:.2f}  "
              f"dte={entry['dte']}")

        new_entries.append(entry)
        existing_ids.add(entry["id"])

    # Merge new into existing (keep prior days' pending entries)
    merged = [e for e in existing if e.get("screened_date") != today] + new_entries
    save_pending_entries(merged)

    print(f"\n  New entries today   : {len(new_entries)}")
    print(f"  Total in pending    : {len(merged)}")
    print()
    return new_entries


if __name__ == "__main__":
    run()
