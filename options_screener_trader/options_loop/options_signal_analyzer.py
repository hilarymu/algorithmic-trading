"""
options_signal_analyzer.py
==========================
Phase 3 — Daily signal quality analysis for the options pipeline.

Runs after the executor each day. Two distinct workloads:

A) Universe scan (always runs)
   For every symbol with an IV rank, compute:
   - Signal strength score (0-100): IV rank + RSI extremity + volume
   - Estimated theoretical premium yield at the configured delta/DTE
   Aggregated by IV-rank bucket to show where the premium opportunity is.

B) Outcome analysis (activates after first closed position)
   Reads closed positions from positions_state.json and links them back to
   their screening-date entry in options_picks_history.json.
   Buckets by: IV rank at entry, RSI at entry, regime, strategy type.
   Computes per-bucket: win rate, avg P&L %, avg hold days, annualised yield.

Output
------
options_signal_quality.json  -- consumed by options_optimizer.py

Self-improvement loop
---------------------
  Daily run --> picks_history.json
             --> options_signal_analyzer  --> signal_quality.json
                                          --> options_optimizer
                                               --> options_config.json (Phase 3+)
"""

import json
import math
import sys
from datetime import datetime, timezone, date, timedelta
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
_HERE        = Path(__file__).parent
PROJECT_DIR  = _HERE.parent
DATA_DIR     = PROJECT_DIR / "data"
CONFIG_PATH  = PROJECT_DIR / "options_config.json"
CACHE_PATH   = DATA_DIR / "iv_rank_cache.json"
CANDS_PATH   = DATA_DIR / "options_candidates.json"
PICKS_PATH   = DATA_DIR / "options_picks_history.json"
STATE_PATH   = DATA_DIR / "positions_state.json"
OUTPUT_PATH  = DATA_DIR / "options_signal_quality.json"
REGIME_PATH  = DATA_DIR / "market_regime.json"

# ── IV rank buckets (for distribution & outcome analysis) ─────────────────────
IV_RANK_BUCKETS = [
    ("40-55",   40,  55),
    ("55-70",   55,  70),
    ("70-85",   70,  85),
    ("85-100",  85, 101),   # 101 so rank=100 is included
]

RSI_BUCKETS = [
    ("<10",   0,  10),
    ("10-15", 10, 15),
    ("15-20", 15, 20),
    ("20-25", 20, 25),
]

MIN_OUTCOMES_FOR_STATS = 5   # need at least this many closed positions per bucket


# ══════════════════════════════════════════════════════════════════════════════
#  Black-Scholes helpers (inlined for module independence)
# ══════════════════════════════════════════════════════════════════════════════

def _ncdf(x: float) -> float:
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def _npdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _norm_inv(p: float) -> float:
    """Rational approximation (A&S), error < 5e-4."""
    def _inner(t):
        c = [2.515517, 0.802853, 0.010328]
        d = [1.432788, 0.189269, 0.001308]
        return t - (c[0] + c[1]*t + c[2]*t*t) / (1 + d[0]*t + d[1]*t*t + d[2]*t*t*t)
    if p < 0.5:
        return -_inner(math.sqrt(-2.0 * math.log(p)))
    return _inner(math.sqrt(-2.0 * math.log(1.0 - p)))


def _bs_put_price(S, K, T, r, sigma):
    """Black-Scholes European put price."""
    if T < 1e-6 or sigma <= 0:
        return max(0.0, K - S)
    sq  = sigma * math.sqrt(T)
    d1  = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / sq
    d2  = d1 - sq
    return K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)


def _put_strike_for_delta(S, iv, T, target_delta_abs, r=0.05):
    """Strike giving target absolute put delta via BSM."""
    d1 = _norm_inv(1.0 - target_delta_abs)
    return S * math.exp(-d1 * iv * math.sqrt(T) + (r + 0.5 * iv**2) * T)


# ══════════════════════════════════════════════════════════════════════════════
#  Signal strength scoring
# ══════════════════════════════════════════════════════════════════════════════

def signal_strength(iv_rank: float, rsi: float, vol_ratio: float,
                    near_earnings: bool = False) -> float:
    """
    Composite signal score 0-100.  Higher = stronger premium-sell setup.

    Weights:
      IV rank      40 pts  (full score at rank=100)
      RSI extremity 30 pts  (full score at RSI=0; zero at RSI=25)
      Volume surge  20 pts  (full score at vol_ratio >= 2.5x)
      Earnings      -10 pts penalty if within EARNINGS_WINDOW
    """
    iv_score  = min(iv_rank, 100.0) / 100.0 * 40.0
    rsi_score = max(0.0, (25.0 - rsi) / 25.0) * 30.0
    vol_score = min(vol_ratio / 2.5, 1.0) * 20.0
    penalty   = -10.0 if near_earnings else 0.0
    return round(iv_score + rsi_score + vol_score + penalty, 1)


def est_premium_yield(S: float, iv: float, dte: int,
                      target_delta: float = 0.30,
                      r: float = 0.05) -> dict:
    """
    Estimate theoretical premium yield for a cash-secured put at target_delta.

    Returns
    -------
    {
      "strike":           float,   # estimated CSP strike
      "premium_pct":      float,   # put premium / strike  (per-trade %)
      "annual_yield_pct": float,   # premium_pct annualised by DTE
    }
    All values None if inputs are invalid.
    """
    if not S or S <= 0 or not iv or iv <= 0 or dte <= 0:
        return {"strike": None, "premium_pct": None, "annual_yield_pct": None}
    try:
        T      = dte / 252.0
        K      = _put_strike_for_delta(S, iv, T, target_delta, r)
        put_px = _bs_put_price(S, K, T, r, iv)
        if K <= 0 or put_px < 0:
            return {"strike": None, "premium_pct": None, "annual_yield_pct": None}
        prem_pct   = round(put_px / K * 100, 2)     # as % of capital at risk
        annual_pct = round(prem_pct * (252 / dte), 1)
        return {
            "strike":           round(K, 2),
            "premium_pct":      prem_pct,
            "annual_yield_pct": annual_pct,
        }
    except Exception:
        return {"strike": None, "premium_pct": None, "annual_yield_pct": None}


# ══════════════════════════════════════════════════════════════════════════════
#  IV rank distribution across universe
# ══════════════════════════════════════════════════════════════════════════════

def iv_rank_distribution(cache: dict) -> dict:
    """
    Returns per-bucket counts and pct for the universe-wide IV rank spread.
    """
    total = len([v for v in cache.values() if v.get("iv_rank") is not None])
    dist  = {}
    for label, lo, hi in IV_RANK_BUCKETS:
        cnt = sum(1 for v in cache.values()
                  if v.get("iv_rank") is not None and lo <= v["iv_rank"] < hi)
        dist[label] = {
            "count": cnt,
            "pct":   round(cnt / total * 100, 1) if total else 0.0,
        }
    below_40 = sum(1 for v in cache.values()
                   if v.get("iv_rank") is not None and v["iv_rank"] < 40)
    dist["<40"] = {
        "count": below_40,
        "pct":   round(below_40 / total * 100, 1) if total else 0.0,
    }
    return dist


# ══════════════════════════════════════════════════════════════════════════════
#  Outcome analysis from closed positions
# ══════════════════════════════════════════════════════════════════════════════

def _bucket_label(iv_rank_buckets, rsi_buckets, iv_rank, rsi):
    """Return (iv_bucket, rsi_bucket) labels for a given entry."""
    iv_lbl = "other"
    for label, lo, hi in iv_rank_buckets:
        if lo <= iv_rank < hi:
            iv_lbl = label
            break
    rsi_lbl = "other"
    for label, lo, hi in rsi_buckets:
        if lo <= rsi < hi:
            rsi_lbl = label
            break
    return iv_lbl, rsi_lbl


def analyze_closed_positions(closed: list) -> dict:
    """
    Compute outcome statistics from a list of closed position dicts.
    Each position must have: pnl_pct, hold_days, strategy, regime,
    iv_rank_at_entry, rsi_at_entry, exit_reason.

    Returns a stats dict; all fields None if len(closed) < MIN_OUTCOMES.
    """
    n = len(closed)
    if n == 0:
        return {"n": 0, "status": "no_data",
                "message": "No closed positions yet — accumulating data."}

    wins     = [p for p in closed if p.get("pnl_pct", 0) > 0]
    losses   = [p for p in closed if p.get("pnl_pct", 0) <= 0]
    pnls     = [p["pnl_pct"] for p in closed if p.get("pnl_pct") is not None]
    holds    = [p["hold_days"] for p in closed if p.get("hold_days") is not None]

    win_rate   = round(len(wins) / n * 100, 1)
    avg_pnl    = round(sum(pnls) / len(pnls) * 100, 2) if pnls else None
    avg_hold   = round(sum(holds) / len(holds), 1) if holds else None
    ann_yield  = (round(avg_pnl * 252 / avg_hold, 1)
                  if avg_pnl and avg_hold and avg_hold > 0 else None)

    # Exit reason breakdown
    exit_counts: dict = {}
    for p in closed:
        reason = p.get("exit_reason", "unknown")
        exit_counts[reason] = exit_counts.get(reason, 0) + 1

    # By IV rank bucket
    iv_bucket_stats: dict = {}
    for label, lo, hi in IV_RANK_BUCKETS:
        bucket_pos = [p for p in closed
                      if p.get("iv_rank_at_entry") is not None
                      and lo <= p["iv_rank_at_entry"] < hi]
        if len(bucket_pos) >= MIN_OUTCOMES_FOR_STATS:
            b_wins = [p for p in bucket_pos if p.get("pnl_pct", 0) > 0]
            b_pnls = [p["pnl_pct"] for p in bucket_pos if p.get("pnl_pct") is not None]
            iv_bucket_stats[label] = {
                "n":        len(bucket_pos),
                "win_rate": round(len(b_wins) / len(bucket_pos) * 100, 1),
                "avg_pnl_pct": round(sum(b_pnls) / len(b_pnls) * 100, 2) if b_pnls else None,
            }

    # By strategy type
    strat_stats: dict = {}
    for strat in ("CSP", "PUT_SPREAD", "OTM_PUT_SPREAD", "CALL_SPREAD"):
        sp = [p for p in closed if p.get("strategy") == strat]
        if len(sp) >= MIN_OUTCOMES_FOR_STATS:
            sp_wins = [p for p in sp if p.get("pnl_pct", 0) > 0]
            sp_pnls = [p["pnl_pct"] for p in sp if p.get("pnl_pct") is not None]
            strat_stats[strat] = {
                "n":        len(sp),
                "win_rate": round(len(sp_wins) / len(sp) * 100, 1),
                "avg_pnl_pct": round(sum(sp_pnls) / len(sp_pnls) * 100, 2) if sp_pnls else None,
            }

    # By regime
    regime_stats: dict = {}
    for regime in ("bull", "mild_correction", "correction", "recovery"):
        rp = [p for p in closed if p.get("regime") == regime]
        if len(rp) >= MIN_OUTCOMES_FOR_STATS:
            rp_wins = [p for p in rp if p.get("pnl_pct", 0) > 0]
            rp_pnls = [p["pnl_pct"] for p in rp if p.get("pnl_pct") is not None]
            regime_stats[regime] = {
                "n":        len(rp),
                "win_rate": round(len(rp_wins) / len(rp) * 100, 1),
                "avg_pnl_pct": round(sum(rp_pnls) / len(rp_pnls) * 100, 2) if rp_pnls else None,
            }

    return {
        "n":            n,
        "n_wins":       len(wins),
        "n_losses":     len(losses),
        "win_rate_pct": win_rate,
        "avg_pnl_pct":  avg_pnl,
        "avg_hold_days": avg_hold,
        "ann_yield_pct": ann_yield,
        "exit_reasons":  exit_counts,
        "by_iv_rank":    iv_bucket_stats,
        "by_strategy":   strat_stats,
        "by_regime":     regime_stats,
        "status":        "active" if n >= MIN_OUTCOMES_FOR_STATS else "sparse",
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Closed positions from positions_state.json
# ══════════════════════════════════════════════════════════════════════════════

def load_closed_positions() -> list:
    """
    Read positions_state.json and build closed-position dicts enriched
    with iv_rank and rsi from options_picks_history.json.
    """
    if not STATE_PATH.exists():
        return []
    try:
        state = json.loads(STATE_PATH.read_text())
    except Exception:
        return []

    closed_raw = [p for p in state.get("positions", []) if p.get("status") == "closed"]
    if not closed_raw:
        return []

    # Build lookup: symbol -> latest entry in picks history
    picks_lookup: dict = {}
    if PICKS_PATH.exists():
        try:
            picks = json.loads(PICKS_PATH.read_text())
            for pk in picks:
                sym = pk.get("symbol")
                if sym:
                    picks_lookup[sym] = pk
        except Exception:
            pass

    closed: list = []
    for pos in closed_raw:
        sym        = pos.get("symbol", "")
        entry_dt   = pos.get("entry_date")
        exit_dt    = pos.get("exit_date")
        pnl_pct    = pos.get("pnl_pct")
        strategy   = pos.get("strategy")
        exit_reason = pos.get("exit_reason")

        hold_days = None
        if entry_dt and exit_dt:
            try:
                e = datetime.fromisoformat(entry_dt).date()
                x = datetime.fromisoformat(exit_dt).date()
                hold_days = (x - e).days
            except Exception:
                pass

        pick = picks_lookup.get(sym, {})
        closed.append({
            "symbol":          sym,
            "strategy":        strategy,
            "pnl_pct":         pnl_pct,
            "hold_days":       hold_days,
            "exit_reason":     exit_reason,
            "regime":          pos.get("regime") or pick.get("regime"),
            "iv_rank_at_entry": (pos.get("iv_rank_at_entry")
                                  or pick.get("iv_rank_at_screen")),
            "rsi_at_entry":     (pos.get("rsi_at_entry")
                                  or pick.get("rsi_at_screen")),
        })

    return closed


# ══════════════════════════════════════════════════════════════════════════════
#  Main entry point
# ══════════════════════════════════════════════════════════════════════════════

def run() -> dict:
    print("\n" + "=" * 60)
    print(" Signal Analyzer  (Phase 3)")
    print("=" * 60)

    # ── Load config ────────────────────────────────────────────────────────
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
        except Exception:
            pass

    cs            = cfg.get("contract_selection", {})
    target_delta  = cs.get("target_delta_csp", 0.30)
    target_dte    = cs.get("target_dte_ideal", 35)

    # ── Load iv_rank_cache ────────────────────────────────────────────────
    cache: dict = {}
    if CACHE_PATH.exists():
        try:
            cache = json.loads(CACHE_PATH.read_text())
        except Exception:
            pass

    if not cache:
        print("  iv_rank_cache.json missing — skipping analysis")
        return {"error": "no_iv_cache"}

    # ── Universe IV distribution ──────────────────────────────────────────
    iv_dist = iv_rank_distribution(cache)
    n_total = len([v for v in cache.values() if v.get("iv_rank") is not None])
    n_sell  = sum(v["count"] for k, v in iv_dist.items() if k != "<40")

    print(f"  Universe: {n_total} symbols with IV rank")
    print(f"  Sell zone (rank >= 40): {n_sell} ({round(n_sell/n_total*100)}%)")
    print(f"  IV rank dist: " +
          "  ".join(f"{k}={v['count']}" for k, v in iv_dist.items() if k != "<40"))

    # ── Score current candidates ──────────────────────────────────────────
    candidates_out: list = []
    if CANDS_PATH.exists():
        try:
            raw = json.loads(CANDS_PATH.read_text())
            cands = raw if isinstance(raw, list) else raw.get("candidates", [])
        except Exception:
            cands = []

        print(f"\n  Scoring {len(cands)} screener candidates:")
        for c in cands:
            sym        = c["symbol"]
            iv_rank    = c.get("iv_rank", 0)
            rsi        = c.get("rsi", 25)
            vol_ratio  = c.get("vol_ratio", 1)
            iv_current = c.get("iv_current", 0)
            price      = c.get("price", 0)
            near_earn  = c.get("near_earnings", False)
            strategy   = c.get("strategy", "CSP")

            score = signal_strength(iv_rank, rsi, vol_ratio, near_earn)
            yield_ = est_premium_yield(price, iv_current, target_dte,
                                       target_delta) if strategy == "CSP" else {}

            candidates_out.append({
                "symbol":           sym,
                "strategy":         strategy,
                "signal_strength":  score,
                "iv_rank":          iv_rank,
                "iv_current":       iv_current,
                "rsi":              rsi,
                "vol_ratio":        round(vol_ratio, 2),
                "near_earnings":    near_earn,
                "est_strike":       yield_.get("strike"),
                "est_premium_pct":  yield_.get("premium_pct"),
                "est_annual_pct":   yield_.get("annual_yield_pct"),
            })
            print(f"    {sym:<6}  score={score:5.1f}  "
                  f"IV%={iv_rank:.0f}  RSI={rsi:.1f}  "
                  f"~prem={yield_.get('premium_pct','?')}%  "
                  f"~ann={yield_.get('annual_yield_pct','?')}%/yr")

        # Sort by signal strength descending
        candidates_out.sort(key=lambda x: x["signal_strength"], reverse=True)

    # ── Outcome analysis from closed positions ────────────────────────────
    closed = load_closed_positions()
    outcome_stats = analyze_closed_positions(closed)
    n_closed = outcome_stats.get("n", 0)

    if n_closed == 0:
        print(f"\n  Closed positions: 0 — outcome stats build as trades close")
    else:
        print(f"\n  Closed positions: {n_closed}")
        print(f"  Win rate: {outcome_stats.get('win_rate_pct')}%  "
              f"Avg P&L: {outcome_stats.get('avg_pnl_pct')}%  "
              f"Ann yield: {outcome_stats.get('ann_yield_pct')}%/yr")

    # ── Data quality flag ─────────────────────────────────────────────────
    # Count real snapshot IV days (post-backfill) vs HV30 proxy days.
    # Backfill ends the day before the first iv_tracker run; real dates
    # are those within the last 30 calendar days (iv_tracker window).
    real_iv_days = 0
    hist_path = DATA_DIR / "iv_history.json"
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    if hist_path.exists():
        try:
            hist = json.loads(hist_path.read_text())
            sample_sym = next(iter(hist))
            real_iv_days = sum(1 for d in hist[sample_sym] if d >= cutoff)
        except Exception:
            pass
    data_quality = "real_iv" if real_iv_days >= 30 else f"hv30_proxy+{real_iv_days}d_real"

    # ── Regime ────────────────────────────────────────────────────────────
    regime = "unknown"
    if REGIME_PATH.exists():
        try:
            regime = json.loads(REGIME_PATH.read_text()).get("regime", "unknown")
        except Exception:
            pass

    # ── Write output ──────────────────────────────────────────────────────
    output = {
        "generated_at":        datetime.now(timezone.utc).isoformat(),
        "regime":              regime,
        "data_quality":        data_quality,
        "n_symbols_with_rank": n_total,
        "iv_rank_distribution": iv_dist,
        "sell_zone_pct":       round(n_sell / n_total * 100, 1) if n_total else 0,
        "candidates":          candidates_out,
        "outcome_stats":       outcome_stats,
        "config_snapshot": {
            "target_delta_csp": target_delta,
            "target_dte_ideal": target_dte,
            "iv_rank_min_sell": cfg.get("indicators", {}).get("iv_rank_min_sell", 40),
            "profit_target_pct": cfg.get("exits", {}).get("profit_target_pct", 0.50),
        },
    }

    OUTPUT_PATH.write_text(json.dumps(output, indent=2))
    print(f"\n  Wrote {OUTPUT_PATH.name}")

    return {
        "n_candidates_scored": len(candidates_out),
        "n_closed_positions":  n_closed,
        "sell_zone_pct":       output["sell_zone_pct"],
        "data_quality":        data_quality,
    }


if __name__ == "__main__":
    run()
