"""
optimizer.py
Reads signal_quality.json and market_regime.json, derives optimal screener
parameters, and writes them directly to screener_config.json (Option C: Full Autonomy).
Also appends each run to config_history.json (a plain JSON array).
"""

import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
SCREENER_CONFIG_PATH = PROJECT_DIR / "screener_config.json"
CONFIG_HISTORY_PATH = PROJECT_DIR / "config_history.json"
SIGNAL_QUALITY_PATH = PROJECT_DIR / "signal_quality.json"
MARKET_REGIME_PATH = PROJECT_DIR / "market_regime.json"

MIN_SAMPLES_FOR_DATA = 10

REGIME_DEFAULTS = {
    "bull": {
        "rsi_oversold": 35,
        "volume_ratio_min": 1.5,
        "require_above_200ma": True,
        "rsi_weight": 0.40,
        "bb_distance_weight": 0.40,
        "volume_weight": 0.20,
    },
    "mild_correction": {
        "rsi_oversold": 38,
        "volume_ratio_min": 1.3,
        "require_above_200ma": True,
        "rsi_weight": 0.45,
        "bb_distance_weight": 0.35,
        "volume_weight": 0.20,
    },
    "correction": {
        "rsi_oversold": 40,
        "volume_ratio_min": 1.2,
        "require_above_200ma": False,
        "rsi_weight": 0.50,
        "bb_distance_weight": 0.30,
        "volume_weight": 0.20,
    },
    "recovery": {
        "rsi_oversold": 40,
        "volume_ratio_min": 1.2,
        "require_above_200ma": False,
        "rsi_weight": 0.45,
        "bb_distance_weight": 0.35,
        "volume_weight": 0.20,
    },
    "geopolitical_shock": {
        "rsi_oversold": 30,
        "volume_ratio_min": 2.0,
        "require_above_200ma": False,
        "rsi_weight": 0.35,
        "bb_distance_weight": 0.45,
        "volume_weight": 0.20,
    },
    "bear": {
        "rsi_oversold": 28,
        "volume_ratio_min": 1.8,
        "require_above_200ma": False,
        "rsi_weight": 0.50,
        "bb_distance_weight": 0.30,
        "volume_weight": 0.20,
    },
}

# ── RSI bucket upper-bound mapping ────────────────────────────────────────────
RSI_BUCKET_UPPER = {
    "<20": 20,
    "20-25": 25,
    "25-30": 30,
    "30-35": 35,
    "35-40": 40,
    "40+": 45,
}

# ── Vol bucket lower-bound mapping ────────────────────────────────────────────
VOL_BUCKET_LOWER = {
    "<1.0": 0.8,
    "1.0-1.5": 1.0,
    "1.5-2.0": 1.5,
    "2.0+": 2.0,
}


# ── Weight normalisation ───────────────────────────────────────────────────────

def _normalize_weights(rsi_w, bb_w, vol_w):
    """
    Round weights to 2dp, then adjust the largest by the residual
    so that rsi_weight + bb_distance_weight + volume_weight == 1.0 exactly.
    """
    rsi_w = round(rsi_w, 2)
    bb_w = round(bb_w, 2)
    vol_w = round(vol_w, 2)

    total = rsi_w + bb_w + vol_w
    residual = round(1.0 - total, 10)  # floating-point-safe

    if residual != 0:
        # Adjust the largest weight
        weights = [("rsi", rsi_w), ("bb", bb_w), ("vol", vol_w)]
        weights.sort(key=lambda x: x[1], reverse=True)
        name = weights[0][0]
        if name == "rsi":
            rsi_w = round(rsi_w + residual, 2)
        elif name == "bb":
            bb_w = round(bb_w + residual, 2)
        else:
            vol_w = round(vol_w + residual, 2)

    return rsi_w, bb_w, vol_w


# ── Data-derived parameter functions ──────────────────────────────────────────

def derive_rsi_threshold(signal_quality):
    """Find RSI bucket with highest avg_5d_return where n>=3. Return upper bound."""
    buckets = signal_quality.get("by_rsi_bucket", {})
    best_bkt = None
    best_return = None
    for bkt, stats in buckets.items():
        if stats and stats.get("n", 0) >= 3:
            avg_ret = stats.get("avg_5d_return")
            if avg_ret is not None:
                if best_return is None or avg_ret > best_return:
                    best_return = avg_ret
                    best_bkt = bkt
    if best_bkt and best_bkt in RSI_BUCKET_UPPER:
        return RSI_BUCKET_UPPER[best_bkt]
    return 35  # default


def derive_volume_threshold(signal_quality):
    """Find vol bucket with highest hit_rate_5d where n>=3. Return lower bound."""
    buckets = signal_quality.get("by_vol_bucket", {})
    best_bkt = None
    best_rate = None
    for bkt, stats in buckets.items():
        if stats and stats.get("n", 0) >= 3:
            rate = stats.get("hit_rate_5d")
            if rate is not None:
                if best_rate is None or rate > best_rate:
                    best_rate = rate
                    best_bkt = bkt
    if best_bkt and best_bkt in VOL_BUCKET_LOWER:
        return VOL_BUCKET_LOWER[best_bkt]
    return 1.5  # default


def derive_ma200_required(signal_quality):
    """
    Return True if above_200ma bucket clearly outperforms below_200ma.
    Condition: both buckets have n>=3 AND above.avg_5d_return > below.avg_5d_return + 0.5.
    """
    ma_buckets = signal_quality.get("by_ma200_bucket", {})
    above = ma_buckets.get("above", {})
    below = ma_buckets.get("below", {})
    if (above and below
            and above.get("n", 0) >= 3
            and below.get("n", 0) >= 3):
        above_avg = above.get("avg_5d_return", 0.0) or 0.0
        below_avg = below.get("avg_5d_return", 0.0) or 0.0
        return above_avg > below_avg + 0.5
    return False


def derive_score_weights(signal_quality):
    """
    Use abs(correlations) as raw weights, normalise to sum=1.0.
    Returns (rsi_weight, bb_distance_weight, volume_weight).
    """
    corr = signal_quality.get("correlations", {})
    rsi_raw = abs(corr.get("rsi_vs_5d_return") or 0.0)
    bb_raw = abs(corr.get("pct_below_bb_vs_5d_return") or 0.0)
    vol_raw = abs(corr.get("vol_ratio_vs_5d_return") or 0.0)

    total = rsi_raw + bb_raw + vol_raw
    if total == 0:
        return 0.40, 0.40, 0.20  # fallback to equal-ish

    rsi_w = rsi_raw / total
    bb_w = bb_raw / total
    vol_w = vol_raw / total

    return _normalize_weights(rsi_w, bb_w, vol_w)


# ── Config I/O ─────────────────────────────────────────────────────────────────

def _load_config():
    with open(SCREENER_CONFIG_PATH, "r") as f:
        return json.load(f)


def _save_config(cfg):
    with open(SCREENER_CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)


def _load_history():
    """Load config_history.json as a plain JSON array."""
    if not CONFIG_HISTORY_PATH.exists():
        return []
    with open(CONFIG_HISTORY_PATH, "r") as f:
        raw = f.read().strip()
    if not raw:
        return []
    return json.loads(raw)


def _save_history(history_list):
    with open(CONFIG_HISTORY_PATH, "w") as f:
        json.dump(history_list, f, indent=2)


def _extract_current_params(cfg):
    """Extract the four tunable params from screener_config."""
    return {
        "rsi_oversold": cfg["indicators"]["rsi_oversold"],
        "volume_ratio_min": cfg["indicators"]["volume_ratio_min"],
        "require_above_200ma": cfg["filters"]["require_above_200ma"],
        "rsi_weight": cfg["scoring"]["rsi_weight"],
        "bb_distance_weight": cfg["scoring"]["bb_distance_weight"],
        "volume_weight": cfg["scoring"]["volume_weight"],
    }


def _apply_params(cfg, params):
    """Write params into the config dict in-place."""
    cfg["indicators"]["rsi_oversold"] = params["rsi_oversold"]
    cfg["indicators"]["volume_ratio_min"] = params["volume_ratio_min"]
    cfg["filters"]["require_above_200ma"] = params["require_above_200ma"]
    cfg["scoring"]["rsi_weight"] = params["rsi_weight"]
    cfg["scoring"]["bb_distance_weight"] = params["bb_distance_weight"]
    cfg["scoring"]["volume_weight"] = params["volume_weight"]
    return cfg


def _compute_changes(before, after):
    """Return list of human-readable change strings."""
    changes = []
    for key in ("rsi_oversold", "volume_ratio_min", "require_above_200ma",
                "rsi_weight", "bb_distance_weight", "volume_weight"):
        if before.get(key) != after.get(key):
            changes.append(f"{key}: {before.get(key)} -> {after.get(key)}")
    return changes


# ── Main run ──────────────────────────────────────────────────────────────────

def run():
    """
    Determine optimal params from regime + signal data, update screener_config.json,
    append to config_history.json.
    Returns summary dict.
    """
    # Load regime
    regime = "bull"
    if MARKET_REGIME_PATH.exists():
        with open(MARKET_REGIME_PATH, "r") as f:
            regime_data = json.load(f)
        regime = regime_data.get("regime", "bull")

    # Load signal quality
    signal_quality = {}
    total_samples = 0
    if SIGNAL_QUALITY_PATH.exists():
        with open(SIGNAL_QUALITY_PATH, "r") as f:
            signal_quality = json.load(f)
        total_samples = signal_quality.get("total_samples", 0)

    # Load current config
    cfg = _load_config()
    params_before = _extract_current_params(cfg)

    # Decide method
    if total_samples >= MIN_SAMPLES_FOR_DATA:
        # Prefer regime-specific buckets when enough in-regime samples exist
        regime_detail  = signal_quality.get("by_regime_detail", {}).get(regime, {})
        regime_summary = signal_quality.get("by_regime", {}).get(regime, {}) or {}
        regime_n       = regime_summary.get("n", 0)

        if regime_n >= MIN_SAMPLES_FOR_DATA and regime_detail:
            method  = f"data_derived ({regime})"
            rsi_thr = derive_rsi_threshold(regime_detail)
            vol_thr = derive_volume_threshold(regime_detail)
            ma_req  = derive_ma200_required(regime_detail)
        else:
            method  = "data_derived (global)"
            rsi_thr = derive_rsi_threshold(signal_quality)
            vol_thr = derive_volume_threshold(signal_quality)
            ma_req  = derive_ma200_required(signal_quality)

        vol_thr = max(1.0, vol_thr)   # volume confirmation always >= 1x average
        rsi_w, bb_w, vol_w = derive_score_weights(signal_quality)  # correlations stay global
        params_after = {
            "rsi_oversold": rsi_thr,
            "volume_ratio_min": vol_thr,
            "require_above_200ma": ma_req,
            "rsi_weight": rsi_w,
            "bb_distance_weight": bb_w,
            "volume_weight": vol_w,
        }
    else:
        method = "regime_defaults"
        defaults = REGIME_DEFAULTS.get(regime, REGIME_DEFAULTS["bull"])
        rsi_w, bb_w, vol_w = _normalize_weights(
            defaults["rsi_weight"],
            defaults["bb_distance_weight"],
            defaults["volume_weight"],
        )
        params_after = {
            "rsi_oversold": defaults["rsi_oversold"],
            "volume_ratio_min": defaults["volume_ratio_min"],
            "require_above_200ma": defaults["require_above_200ma"],
            "rsi_weight": rsi_w,
            "bb_distance_weight": bb_w,
            "volume_weight": vol_w,
        }

    changes = _compute_changes(params_before, params_after)

    # Apply to config
    cfg = _apply_params(cfg, params_after)
    _save_config(cfg)
    print(f"  [optimizer] Method: {method} | Regime: {regime} | Changes: {len(changes)}")
    for c in changes:
        print(f"    {c}")

    # Append to history
    history_list = _load_history()
    history_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "regime": regime,
        "method": method,
        "sample_count": total_samples,
        "changes": changes,
        "params_before": params_before,
        "params_after": params_after,
    }
    history_list.append(history_entry)
    _save_history(history_list)

    return {
        "regime": regime,
        "method": method,
        "sample_count": total_samples,
        "changes": changes,
    }


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2))
