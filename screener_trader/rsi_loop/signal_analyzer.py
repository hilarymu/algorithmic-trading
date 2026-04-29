"""
signal_analyzer.py
Analyzes historical picks performance to assess signal quality.
Uses stdlib statistics only (no numpy/scipy).
Writes signal_quality.json.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median, stdev

PROJECT_DIR = Path(__file__).parent.parent
PICKS_HISTORY_PATH = PROJECT_DIR / "picks_history.json"
SIGNAL_QUALITY_PATH = PROJECT_DIR / "signal_quality.json"

MIN_SAMPLES = 5
TARGET_HORIZON = "5d"


# ── Stat helpers ───────────────────────────────────────────────────────────────

def _safe_stdev(values):
    """Return stdev or None if fewer than 2 values."""
    if len(values) < 2:
        return None
    return stdev(values)


def pearson_correlation(xs, ys):
    """
    Manual Pearson correlation coefficient.
    Returns float or None if fewer than MIN_SAMPLES pairs.
    """
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < MIN_SAMPLES:
        return None

    n = len(pairs)
    xs_ = [p[0] for p in pairs]
    ys_ = [p[1] for p in pairs]

    mx = mean(xs_)
    my = mean(ys_)

    num = sum((x - mx) * (y - my) for x, y in zip(xs_, ys_))
    denom_x = sum((x - mx) ** 2 for x in xs_) ** 0.5
    denom_y = sum((y - my) ** 2 for y in ys_) ** 0.5

    if denom_x == 0 or denom_y == 0:
        return None

    return round(num / (denom_x * denom_y), 4)


def compute_group_stats(picks):
    """
    Compute stats for a group of picks using TARGET_HORIZON (5d).
    Returns dict with n, hit_rate_5d, avg_5d_return, median_5d_return, sharpe_5d.
    Returns None if fewer than MIN_SAMPLES picks with non-None 5d return.
    """
    returns = [p["returns"][TARGET_HORIZON] for p in picks
               if p.get("returns", {}).get(TARGET_HORIZON) is not None]

    if len(returns) < MIN_SAMPLES:
        return None

    hit_rate = sum(1 for r in returns if r > 0) / len(returns)
    avg_ret = mean(returns)
    med_ret = median(returns)

    sd = _safe_stdev(returns)
    if sd and sd > 0:
        sharpe = round(avg_ret / sd, 4)
    else:
        sharpe = None

    return {
        "n": len(returns),
        "hit_rate_5d": round(hit_rate, 4),
        "avg_5d_return": round(avg_ret, 4),
        "median_5d_return": round(med_ret, 4),
        "sharpe_5d": sharpe,
    }


# ── Bucketing functions ────────────────────────────────────────────────────────

def bucket_rsi(rsi_val):
    """Map RSI value to a bucket label string."""
    if rsi_val is None:
        return "unknown"
    if rsi_val < 20:
        return "<20"
    elif rsi_val < 25:
        return "20-25"
    elif rsi_val < 30:
        return "25-30"
    elif rsi_val < 35:
        return "30-35"
    elif rsi_val < 40:
        return "35-40"
    else:
        return "40+"


def bucket_vol(vol_ratio):
    """Map volume ratio to a bucket label string."""
    if vol_ratio is None:
        return "unknown"
    if vol_ratio < 1.0:
        return "<1.0"
    elif vol_ratio < 1.5:
        return "1.0-1.5"
    elif vol_ratio < 2.0:
        return "1.5-2.0"
    else:
        return "2.0+"


def bucket_ma200(pct_above_200ma):
    """Map pct_above_200ma to 'above' or 'below'."""
    if pct_above_200ma is None:
        return "unknown"
    return "above" if pct_above_200ma >= 0 else "below"


# ── Grouping helpers ───────────────────────────────────────────────────────────

def _group_picks(picks, key_fn):
    """Group picks by a key function, return dict of label -> list of picks."""
    groups = {}
    for p in picks:
        key = key_fn(p)
        groups.setdefault(key, []).append(p)
    return groups


def _filter_combo_key(pick):
    """
    Build filter combo key.
    '4_filters' if all 4 pass, '3_filters' if 3, '2_filters' if 2.
    Also encode as MBRV string (M=above_200ma, B=below_bb, R=rsi_oversold, V=vol_ok).
    Returns the numeric key and also a flags string key.
    """
    f = pick.get("filters", {})
    passed = pick.get("filters_passed", sum([
        bool(f.get("above_200ma")),
        bool(f.get("below_bb")),
        bool(f.get("rsi_oversold")),
        bool(f.get("volume_ok")),
    ]))
    return f"{passed}_filters"


def _filter_flags_key(pick):
    """Return MBRV flags string key."""
    f = pick.get("filters", {})
    m = "M" if f.get("above_200ma") else "-"
    b = "B" if f.get("below_bb") else "-"
    r = "R" if f.get("rsi_oversold") else "-"
    v = "V" if f.get("volume_ok") else "-"
    return f"{m}{b}{r}{v}"


# ── Main analysis ──────────────────────────────────────────────────────────────

def analyze(picks):
    """
    Compute full signal quality analysis from picks list.
    Returns signal_quality dict.
    """
    total = len(picks)

    # ── By regime ─────────────────────────────────────────────────────────────
    regime_groups = _group_picks(picks, lambda p: p.get("regime", "unknown"))
    by_regime = {}
    for regime, group in regime_groups.items():
        stats = compute_group_stats(group)
        if stats:
            by_regime[regime] = stats

    # ── By regime, detailed buckets (RSI / vol / MA within each regime) ───────
    by_regime_detail = {}
    for r_name, r_picks in regime_groups.items():
        if len(r_picks) < MIN_SAMPLES:
            continue
        r_rsi_groups = _group_picks(r_picks, lambda p: bucket_rsi(p.get("rsi")))
        r_vol_groups = _group_picks(r_picks, lambda p: bucket_vol(p.get("vol_ratio")))
        r_ma_groups  = _group_picks(r_picks, lambda p: bucket_ma200(p.get("pct_above_200ma")))

        rsi_bkts = {}
        for bkt, grp in r_rsi_groups.items():
            s = compute_group_stats(grp)
            if s:
                rsi_bkts[bkt] = s
        vol_bkts = {}
        for bkt, grp in r_vol_groups.items():
            s = compute_group_stats(grp)
            if s:
                vol_bkts[bkt] = s
        ma_bkts = {}
        for bkt, grp in r_ma_groups.items():
            s = compute_group_stats(grp)
            if s:
                ma_bkts[bkt] = s

        by_regime_detail[r_name] = {
            "by_rsi_bucket":   rsi_bkts,
            "by_vol_bucket":   vol_bkts,
            "by_ma200_bucket": ma_bkts,
        }

    # ── By RSI bucket ──────────────────────────────────────────────────────────
    rsi_groups = _group_picks(picks, lambda p: bucket_rsi(p.get("rsi")))
    by_rsi_bucket = {}
    for bkt, group in rsi_groups.items():
        stats = compute_group_stats(group)
        if stats:
            by_rsi_bucket[bkt] = stats

    # ── By volume bucket ───────────────────────────────────────────────────────
    vol_groups = _group_picks(picks, lambda p: bucket_vol(p.get("vol_ratio")))
    by_vol_bucket = {}
    for bkt, group in vol_groups.items():
        stats = compute_group_stats(group)
        if stats:
            by_vol_bucket[bkt] = stats

    # ── By 200MA bucket ────────────────────────────────────────────────────────
    ma_groups = _group_picks(picks, lambda p: bucket_ma200(p.get("pct_above_200ma")))
    by_ma200_bucket = {}
    for bkt, group in ma_groups.items():
        stats = compute_group_stats(group)
        if stats:
            by_ma200_bucket[bkt] = stats

    # ── By filter combo ────────────────────────────────────────────────────────
    combo_groups = _group_picks(picks, _filter_combo_key)
    flags_groups = _group_picks(picks, _filter_flags_key)
    by_filter_combo = {}
    for key, group in combo_groups.items():
        stats = compute_group_stats(group)
        if stats:
            by_filter_combo[key] = stats
    for key, group in flags_groups.items():
        # Only add specific MBRV combos (skip generic N_filters keys handled above)
        if "-" in key:  # it's a specific combo string
            stats = compute_group_stats(group)
            if stats:
                by_filter_combo[key] = stats

    # ── Correlations ──────────────────────────────────────────────────────────
    returns_5d = [p["returns"].get(TARGET_HORIZON) for p in picks]

    rsi_corr = pearson_correlation(
        [p.get("rsi") for p in picks], returns_5d
    )
    vol_corr = pearson_correlation(
        [p.get("vol_ratio") for p in picks], returns_5d
    )
    bb_corr = pearson_correlation(
        [p.get("pct_below_bb") for p in picks], returns_5d
    )
    ma_corr = pearson_correlation(
        [p.get("pct_above_200ma") for p in picks], returns_5d
    )

    correlations = {
        "rsi_vs_5d_return": rsi_corr,
        "vol_ratio_vs_5d_return": vol_corr,
        "pct_below_bb_vs_5d_return": bb_corr,
        "pct_above_200ma_vs_5d_return": ma_corr,
    }

    return {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "total_samples": total,
        "by_regime": by_regime,
        "by_regime_detail": by_regime_detail,
        "by_rsi_bucket": by_rsi_bucket,
        "by_vol_bucket": by_vol_bucket,
        "by_ma200_bucket": by_ma200_bucket,
        "by_filter_combo": by_filter_combo,
        "correlations": correlations,
    }


def run():
    """Load picks history, run analysis, write signal_quality.json."""
    if not PICKS_HISTORY_PATH.exists():
        print("  [signal_analyzer] No picks_history.json found — skipping analysis.")
        # Write an empty signal quality file so downstream steps don't fail
        empty = {
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "total_samples": 0,
            "by_regime": {},
            "by_regime_detail": {},
            "by_rsi_bucket": {},
            "by_vol_bucket": {},
            "by_ma200_bucket": {},
            "by_filter_combo": {},
            "correlations": {
                "rsi_vs_5d_return": None,
                "vol_ratio_vs_5d_return": None,
                "pct_below_bb_vs_5d_return": None,
                "pct_above_200ma_vs_5d_return": None,
            },
        }
        with open(SIGNAL_QUALITY_PATH, "w") as f:
            json.dump(empty, f, indent=2)
        return

    with open(PICKS_HISTORY_PATH, "r") as f:
        history = json.load(f)

    picks = history.get("picks", [])
    print(f"  [signal_analyzer] Analyzing {len(picks)} picks...")

    result = analyze(picks)

    with open(SIGNAL_QUALITY_PATH, "w") as f:
        json.dump(result, f, indent=2)

    print(f"  [signal_analyzer] Wrote signal_quality.json ({result['total_samples']} samples)")
    return result


if __name__ == "__main__":
    run()
