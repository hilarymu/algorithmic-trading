"""
replay_optimizer.py
====================
Walk-forward simulation of the RSI Self-Improvement Loop.

For every week in picks history it asks:
  "If the optimizer had only seen data UP TO this week,
   what rules would it have derived — and did those rules
   actually improve the NEXT week's picks?"

This is a point-in-time backtest of the optimizer itself, not
just the picks.  No look-ahead bias: each week's rules are derived
only from prior history.

Output:
  - Console table (weekly results)
  - rsi_loop/replay_results.json  (full detail)
  - rsi_loop/replay_summary.json  (aggregate stats)

Run:
    py -3 "path\to\screener_trader\rsi_loop\replay_optimizer.py"
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import mean, stdev

PROJECT_DIR        = Path(__file__).parent.parent
PICKS_HISTORY_PATH = PROJECT_DIR / "picks_history.json"
RESULTS_PATH       = Path(__file__).parent / "replay_results.json"
SUMMARY_PATH       = Path(__file__).parent / "replay_summary.json"

MIN_TRAINING_SAMPLES = 10   # need this many before optimizer goes data-driven
TARGET_HORIZON       = "5d"

# Sensible per-regime defaults when not enough regime-specific history yet
REGIME_DEFAULTS = {
    "bull":               (35, 1.2, False),
    "mild_correction":    (30, 1.5, False),
    "correction":         (25, 2.0, False),
    "recovery":           (35, 1.2, False),
    "geopolitical_shock": (25, 2.0, False),
    "bear":               (20, 2.0, False),
}


# ── RSI bucket helpers (mirrors optimizer.py) ─────────────────────────────────

RSI_BUCKET_UPPER = {"<20": 20, "20-25": 25, "25-30": 30,
                    "30-35": 35, "35-40": 40, "40+": 45}
VOL_BUCKET_LOWER = {"<1.0": 0.8, "1.0-1.5": 1.0, "1.5-2.0": 1.5, "2.0+": 2.0}


def _rsi_bucket(rsi):
    if rsi is None: return None
    r = float(rsi)
    if r < 20:  return "<20"
    if r < 25:  return "20-25"
    if r < 30:  return "25-30"
    if r < 35:  return "30-35"
    if r < 40:  return "35-40"
    return "40+"


def _vol_bucket(vol):
    if vol is None: return None
    v = float(vol)
    if v < 1.0:  return "<1.0"
    if v < 1.5:  return "1.0-1.5"
    if v < 2.0:  return "1.5-2.0"
    return "2.0+"


def _ma_bucket(pct):
    if pct is None: return None
    return "above" if float(pct) >= 0 else "below"


# ── Signal analysis on a subset of picks ──────────────────────────────────────

def _group_stats(picks_subset):
    """Returns {n, hit_rate_5d, avg_5d_return, sharpe} or None."""
    returns = [float(p["returns"][TARGET_HORIZON])
               for p in picks_subset
               if p.get("returns", {}).get(TARGET_HORIZON) is not None]
    n = len(returns)
    if n < 3:
        return None
    avg = mean(returns)
    hit = sum(1 for r in returns if r > 0) / n
    sd  = stdev(returns) if n >= 2 else None
    sharpe = round(avg / sd, 3) if sd and sd > 0 else None
    return {"n": n, "hit_rate_5d": round(hit, 3),
            "avg_5d_return": round(avg, 3), "sharpe_5d": sharpe}


def _signal_quality(training_picks):
    """Compute signal quality buckets from training picks only."""
    rsi_bkts, vol_bkts, ma_bkts = {}, {}, {}
    for p in training_picks:
        rb = _rsi_bucket(p.get("rsi"))
        if rb:
            rsi_bkts.setdefault(rb, []).append(p)
        vb = _vol_bucket(p.get("vol_ratio"))
        if vb:
            vol_bkts.setdefault(vb, []).append(p)
        mb = _ma_bucket(p.get("pct_above_200ma"))
        if mb:
            ma_bkts.setdefault(mb, []).append(p)

    return {
        "by_rsi_bucket": {k: _group_stats(v) for k, v in rsi_bkts.items()},
        "by_vol_bucket": {k: _group_stats(v) for k, v in vol_bkts.items()},
        "by_ma200_bucket": {k: _group_stats(v) for k, v in ma_bkts.items()},
    }


# ── Optimizer logic (mirrors optimizer.py) ────────────────────────────────────

def _derive_rsi_threshold(sq):
    buckets = sq.get("by_rsi_bucket", {})
    best_bkt, best_ret = None, None
    for bkt, stats in buckets.items():
        if stats and stats.get("n", 0) >= 3:
            avg = stats.get("avg_5d_return")
            if avg is not None and (best_ret is None or avg > best_ret):
                best_ret, best_bkt = avg, bkt
    return RSI_BUCKET_UPPER.get(best_bkt, 35) if best_bkt else 35


def _derive_vol_threshold(sq):
    buckets = sq.get("by_vol_bucket", {})
    best_bkt, best_rate = None, None
    for bkt, stats in buckets.items():
        if stats and stats.get("n", 0) >= 3:
            rate = stats.get("hit_rate_5d")
            if rate is not None and (best_rate is None or rate > best_rate):
                best_rate, best_bkt = rate, bkt
    raw = VOL_BUCKET_LOWER.get(best_bkt, 1.5) if best_bkt else 1.5
    return max(1.0, raw)  # volume confirmation always >= 1x average


def _derive_ma200_required(sq):
    ma = sq.get("by_ma200_bucket", {})
    above = ma.get("above") or {}
    below = ma.get("below") or {}
    if above.get("n", 0) >= 3 and below.get("n", 0) >= 3:
        a_avg = above.get("avg_5d_return", 0) or 0
        b_avg = below.get("avg_5d_return", 0) or 0
        return a_avg > b_avg + 0.5
    return False


def _derive_rules(training_picks, current_regime=None):
    """
    Return (rsi_thr, vol_thr, require_200ma, method).

    Priority:
      1. Data-driven from same-regime training picks  (if >= MIN_TRAINING_SAMPLES)
      2. Data-driven from all training picks           (if >= MIN_TRAINING_SAMPLES)
      3. Regime-specific hard defaults
    """
    # ── 1. Regime-specific data ────────────────────────────────────────────────
    if current_regime and current_regime != "unknown":
        regime_picks = [
            p for p in training_picks
            if p.get("regime") == current_regime
            and p.get("returns", {}).get(TARGET_HORIZON) is not None
        ]
        if len(regime_picks) >= MIN_TRAINING_SAMPLES:
            sq = _signal_quality(regime_picks)
            return (
                _derive_rsi_threshold(sq),
                _derive_vol_threshold(sq),
                _derive_ma200_required(sq),
                f"data_{current_regime}",
            )

    # ── 2. All-regime data ─────────────────────────────────────────────────────
    valid = [p for p in training_picks
             if p.get("returns", {}).get(TARGET_HORIZON) is not None]
    if len(valid) >= MIN_TRAINING_SAMPLES:
        sq = _signal_quality(valid)
        return (
            _derive_rsi_threshold(sq),
            _derive_vol_threshold(sq),
            _derive_ma200_required(sq),
            "data_global",
        )

    # ── 3. Regime-specific hard defaults ──────────────────────────────────────
    rsi_d, vol_d, ma_d = REGIME_DEFAULTS.get(current_regime or "", (35, 1.5, False))
    return rsi_d, vol_d, ma_d, "defaults"


# ── Filter test picks using derived rules ─────────────────────────────────────

def _apply_rules(picks, rsi_thr, vol_thr, require_200ma):
    """Return picks that would have passed the derived rules."""
    filtered = []
    for p in picks:
        rsi = p.get("rsi")
        vol = p.get("vol_ratio")
        ma  = p.get("pct_above_200ma")
        if rsi is None or float(rsi) > rsi_thr:
            continue
        if vol is None or float(vol) < vol_thr:
            continue
        if require_200ma and (ma is None or float(ma) < 0):
            continue
        filtered.append(p)
    return filtered


# ── Week grouping ─────────────────────────────────────────────────────────────

def _iso_week_key(date_str):
    """Return (iso_year, iso_week) for a YYYY-MM-DD string."""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    iso = d.isocalendar()
    return (iso[0], iso[1])


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print(" Replay Optimizer — Walk-Forward Backtest")
    print("=" * 70)

    with open(PICKS_HISTORY_PATH) as f:
        history = json.load(f)

    picks = history.get("picks", [])

    # Keep only picks with a valid screened_date AND 5d return filled
    picks_with_returns = [
        p for p in picks
        if p.get("screened_date")
        and p.get("returns", {}).get(TARGET_HORIZON) is not None
    ]
    print(f"\n  Total picks in history : {len(picks)}")
    print(f"  Picks with 5d return   : {len(picks_with_returns)}")

    if len(picks_with_returns) < 20:
        print("\n  Not enough picks with returns to run replay. Run rsi_main.py first.")
        return

    # Sort by date
    picks_with_returns.sort(key=lambda p: p["screened_date"])

    # Group into ISO weeks
    weeks = {}
    for p in picks_with_returns:
        key = _iso_week_key(p["screened_date"])
        weeks.setdefault(key, []).append(p)

    sorted_weeks = sorted(weeks.keys())
    print(f"  Weeks spanned          : {len(sorted_weeks)}")
    print(f"  Date range             : {picks_with_returns[0]['screened_date']} "
          f"-> {picks_with_returns[-1]['screened_date']}")
    print()

    # ── Walk-forward loop ─────────────────────────────────────────────────────
    weekly_results = []

    print(f"  {'Week':<12} {'Train':>6} {'Method':<20} "
          f"{'RSI':>5} {'Vol':>5} "
          f"{'Test':>5} {'Filt':>5} "
          f"{'All Ret':>8} {'Filt Ret':>9} {'Delta':>7}")
    print("  " + "-" * 97)

    training_pool = []

    for i, week_key in enumerate(sorted_weeks):
        test_picks = weeks[week_key]
        week_str   = f"{week_key[0]}-W{week_key[1]:02d}"

        # Determine this week's dominant regime first (used for rule derivation)
        regimes = [p.get("regime", "unknown") for p in test_picks]
        regime  = max(set(regimes), key=regimes.count)

        # Derive rules from everything BEFORE this week, regime-aware
        rsi_thr, vol_thr, req_200ma, method = _derive_rules(training_pool, regime)

        # Apply rules to this week's test picks
        filtered = _apply_rules(test_picks, rsi_thr, vol_thr, req_200ma)

        # Compute returns
        all_rets  = [float(p["returns"][TARGET_HORIZON]) for p in test_picks]
        filt_rets = [float(p["returns"][TARGET_HORIZON]) for p in filtered]

        avg_all  = round(mean(all_rets), 3)  if all_rets  else None
        avg_filt = round(mean(filt_rets), 3) if filt_rets else None
        delta    = round(avg_filt - avg_all, 3) if (avg_filt is not None and avg_all is not None) else None

        hit_all  = round(sum(1 for r in all_rets  if r > 0) / len(all_rets),  3) if all_rets  else None
        hit_filt = round(sum(1 for r in filt_rets if r > 0) / len(filt_rets), 3) if filt_rets else None

        result = {
            "week":            week_str,
            "week_key":        week_key,
            "regime":          regime,
            "n_training":      len(training_pool),
            "method":          method,
            "derived_rsi_thr": rsi_thr,
            "derived_vol_thr": vol_thr,
            "require_200ma":   req_200ma,
            "n_test":          len(test_picks),
            "n_filtered":      len(filtered),
            "avg_return_all":  avg_all,
            "avg_return_filt": avg_filt,
            "delta":           delta,
            "hit_rate_all":    hit_all,
            "hit_rate_filt":   hit_filt,
        }
        weekly_results.append(result)

        # Print row
        delta_str = f"{delta:+.3f}" if delta is not None else "   N/A"
        filt_str  = f"{avg_filt:+.3f}" if avg_filt is not None else "   N/A"
        all_str   = f"{avg_all:+.3f}"  if avg_all  is not None else "   N/A"
        print(f"  {week_str:<12} {len(training_pool):>6} {method:<20} "
              f"{rsi_thr:>5} {vol_thr:>5} "
              f"{len(test_picks):>5} {len(filtered):>5} "
              f"{all_str:>8} {filt_str:>9} {delta_str:>7}")

        # Add this week's picks to training pool for next iteration
        training_pool.extend(test_picks)

    # ── Aggregate summary ─────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print(" Summary")
    print("=" * 70)

    # Only weeks where filter actually reduced the set (non-trivial filtering)
    data_driven_weeks = [r for r in weekly_results
                         if r["method"].startswith("data_")
                         and r["n_filtered"] > 0
                         and r["avg_return_filt"] is not None
                         and r["avg_return_all"]  is not None]

    all_weeks_with_returns = [r for r in weekly_results
                              if r["avg_return_all"] is not None]

    if data_driven_weeks:
        avg_delta     = mean(r["delta"] for r in data_driven_weeks if r["delta"] is not None)
        pct_positive  = sum(1 for r in data_driven_weeks if (r["delta"] or 0) > 0) / len(data_driven_weeks)
        avg_ret_all   = mean(r["avg_return_all"]  for r in data_driven_weeks)
        avg_ret_filt  = mean(r["avg_return_filt"] for r in data_driven_weeks)
        avg_hit_all   = mean(r["hit_rate_all"]  for r in data_driven_weeks if r["hit_rate_all"]  is not None)
        avg_hit_filt  = mean(r["hit_rate_filt"] for r in data_driven_weeks if r["hit_rate_filt"] is not None)

        print(f"\n  Data-driven weeks          : {len(data_driven_weeks)}")
        print(f"  Avg 5d return  (all picks) : {avg_ret_all:+.3f}%")
        print(f"  Avg 5d return  (filtered)  : {avg_ret_filt:+.3f}%")
        print(f"  Avg delta (filtered - all) : {avg_delta:+.3f}%  "
              f"({'optimizer adds value' if avg_delta > 0 else 'optimizer hurts'})")
        print(f"  Weeks where filter helped  : {pct_positive:.0%}")
        print(f"  Hit rate  (all picks)      : {avg_hit_all:.1%}")
        print(f"  Hit rate  (filtered)       : {avg_hit_filt:.1%}")

        # By regime
        print(f"\n  By regime (data-driven weeks only):")
        regime_groups = {}
        for r in data_driven_weeks:
            regime_groups.setdefault(r["regime"], []).append(r)

        for reg, rows in sorted(regime_groups.items()):
            deltas = [r["delta"] for r in rows if r["delta"] is not None]
            if deltas:
                avg_d = mean(deltas)
                rets_filt = [r["avg_return_filt"] for r in rows if r["avg_return_filt"] is not None]
                avg_f = mean(rets_filt) if rets_filt else 0
                bar = "+" * int(max(0, avg_d * 10)) if avg_d > 0 else "-" * int(min(10, abs(avg_d) * 10))
                print(f"    {reg:<22} n={len(rows):>3}  avg_delta={avg_d:+.3f}%  "
                      f"filt_return={avg_f:+.3f}%  {bar}")
    else:
        print("\n  No data-driven weeks yet — need more picks with returns.")

    # ── Trend: is the optimizer improving over time? ───────────────────────────
    if len(data_driven_weeks) >= 8:
        mid = len(data_driven_weeks) // 2
        early_delta = mean(r["delta"] for r in data_driven_weeks[:mid] if r["delta"] is not None)
        late_delta  = mean(r["delta"] for r in data_driven_weeks[mid:] if r["delta"] is not None)
        improving   = late_delta > early_delta
        print(f"\n  Optimizer trend (early vs late data-driven weeks):")
        print(f"    Early half avg delta : {early_delta:+.3f}%")
        print(f"    Late  half avg delta : {late_delta:+.3f}%")
        print(f"    Trend                : {'IMPROVING over time' if improving else 'NOT improving over time'}")

    # ── Save results ──────────────────────────────────────────────────────────
    summary = {
        "generated_at":       datetime.now(timezone.utc).isoformat(),
        "total_weeks":        len(weekly_results),
        "data_driven_weeks":  len(data_driven_weeks),
        "total_picks":        len(picks_with_returns),
    }
    if data_driven_weeks:
        summary["avg_delta_data_driven"]    = round(mean(r["delta"] for r in data_driven_weeks if r["delta"] is not None), 4)
        summary["pct_weeks_filter_helped"]  = round(sum(1 for r in data_driven_weeks if (r["delta"] or 0) > 0) / len(data_driven_weeks), 3)
        summary["avg_return_all_picks"]     = round(mean(r["avg_return_all"]  for r in data_driven_weeks), 4)
        summary["avg_return_filtered"]      = round(mean(r["avg_return_filt"] for r in data_driven_weeks), 4)

    with open(RESULTS_PATH, "w") as f:
        json.dump({"summary": summary, "weekly": weekly_results}, f, indent=2)
    with open(SUMMARY_PATH, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\n  Results saved to: {RESULTS_PATH.name}")
    print(f"  Summary saved to: {SUMMARY_PATH.name}")
    print()


if __name__ == "__main__":
    main()
