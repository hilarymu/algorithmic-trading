"""
options_optimizer.py
====================
Phase 3 — Parameter optimizer for the options pipeline.

Reads options_signal_quality.json (produced by options_signal_analyzer)
and positions_state.json to recommend — and eventually apply — config
parameter adjustments.

Thresholds
----------
  n >= 10  :  begin generating insights / suggestions (read-only)
  n >= 50  :  apply changes to options_config.json when auto_optimize=true

Adjustable parameters and their evidence triggers
--------------------------------------------------
  iv_rank_min_sell  (default 40)
      Raise by 5 if win_rate in the 40-55 bucket is < 40% AND n >= 20.
      Lower by 5 if no candidates pass for 5+ consecutive days (too restrictive).

  target_delta_csp  (default 0.30)
      Widen to 0.25 (more OTM) if loss_limit exit rate is > 30%.
      Tighten to 0.35 if win_rate > 80% for 20+ trades (can take more premium).

  profit_target_pct  (default 0.50)
      Raise to 0.60 if avg_hold_days < 10 (closing too early, leaving premium).
      Lower to 0.40 if avg_hold_days > 30 (DTE risk building up).

  close_at_dte  (default 21)
      Raise to 28 if loss_limit exits cluster in the final 7 DTE window.

Output
------
options_improvement_report.json  -- read by dashboard and daily log
options_config.json              -- updated only when auto_optimize=true AND n >= 50
"""

import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
_HERE        = Path(__file__).parent
PROJECT_DIR  = _HERE.parent
DATA_DIR     = PROJECT_DIR / "data"
CONFIG_PATH  = PROJECT_DIR / "options_config.json"
SIGNAL_PATH  = DATA_DIR / "options_signal_quality.json"
STATE_PATH   = DATA_DIR / "positions_state.json"
REPORT_PATH  = DATA_DIR / "options_improvement_report.json"

# ── Optimizer gates ────────────────────────────────────────────────────────────
MIN_FOR_INSIGHTS = 10    # show performance stats from here
MIN_FOR_CHANGES  = 50    # apply config changes from here

# ── Parameter bounds (hard floors / ceilings) ─────────────────────────────────
BOUNDS = {
    "iv_rank_min_sell":  (20, 70),
    "target_delta_csp":  (0.15, 0.45),
    "profit_target_pct": (0.35, 0.70),
    "close_at_dte":      (14, 35),
}


# ══════════════════════════════════════════════════════════════════════════════
#  Config helpers
# ══════════════════════════════════════════════════════════════════════════════

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


# ══════════════════════════════════════════════════════════════════════════════
#  Insight generation
# ══════════════════════════════════════════════════════════════════════════════

def generate_insights(outcome_stats: dict, cfg: dict) -> list[dict]:
    """
    Produce a list of insight dicts from outcome statistics.
    Each insight: {param, current, suggested, direction, reason, confidence}.
    confidence: 'low' (<20 trades), 'medium' (20-49), 'high' (50+)
    """
    n = outcome_stats.get("n", 0)
    if n < MIN_FOR_INSIGHTS:
        return []

    insights: list = []
    conf = "low" if n < 20 else ("medium" if n < MIN_FOR_CHANGES else "high")

    win_rate      = outcome_stats.get("win_rate_pct", 0)
    avg_hold      = outcome_stats.get("avg_hold_days")
    exit_reasons  = outcome_stats.get("exit_reasons", {})
    loss_limit_n  = exit_reasons.get("loss_limit", 0)
    loss_limit_pct = loss_limit_n / n * 100 if n > 0 else 0

    by_iv = outcome_stats.get("by_iv_rank", {})
    exits_cfg  = cfg.get("exits", {})
    cs_cfg     = cfg.get("contract_selection", {})
    ind_cfg    = cfg.get("indicators", {})

    cur_iv_min   = ind_cfg.get("iv_rank_min_sell", 40)
    cur_delta    = cs_cfg.get("target_delta_csp", 0.30)
    cur_profit   = exits_cfg.get("profit_target_pct", 0.50)
    cur_close_dte = exits_cfg.get("close_at_dte", 21)

    # ── IV rank minimum ────────────────────────────────────────────────────
    low_iv_bucket = by_iv.get("40-55", {})
    if low_iv_bucket.get("n", 0) >= 5 and (low_iv_bucket.get("win_rate", 100) or 100) < 40:
        insights.append({
            "param":      "iv_rank_min_sell",
            "current":    cur_iv_min,
            "suggested":  min(cur_iv_min + 5, BOUNDS["iv_rank_min_sell"][1]),
            "direction":  "raise",
            "reason":     f"40-55 IV-rank bucket win rate "
                          f"{low_iv_bucket['win_rate']}% < 40% threshold "
                          f"(n={low_iv_bucket['n']})",
            "confidence": conf,
        })

    # ── Put delta (CSP strike selection) ──────────────────────────────────
    if loss_limit_pct > 30:
        insights.append({
            "param":      "target_delta_csp",
            "current":    cur_delta,
            "suggested":  max(round(cur_delta - 0.05, 2),
                              BOUNDS["target_delta_csp"][0]),
            "direction":  "lower (more OTM)",
            "reason":     f"Loss-limit exits = {loss_limit_pct:.0f}% of closes "
                          f"(> 30% threshold).  Selling more OTM reduces assignment risk.",
            "confidence": conf,
        })
    elif win_rate > 80 and n >= 20:
        insights.append({
            "param":      "target_delta_csp",
            "current":    cur_delta,
            "suggested":  min(round(cur_delta + 0.05, 2),
                              BOUNDS["target_delta_csp"][1]),
            "direction":  "raise (more premium)",
            "reason":     f"Win rate {win_rate}% over {n} trades — can capture "
                          f"higher premium with slightly closer strike.",
            "confidence": conf,
        })

    # ── Profit target ─────────────────────────────────────────────────────
    if avg_hold and avg_hold < 10:
        insights.append({
            "param":      "profit_target_pct",
            "current":    cur_profit,
            "suggested":  min(round(cur_profit + 0.10, 2),
                              BOUNDS["profit_target_pct"][1]),
            "reason":     f"Avg hold only {avg_hold:.0f} days — "
                          f"raising profit target captures more theta decay.",
            "direction":  "raise",
            "confidence": conf,
        })
    elif avg_hold and avg_hold > 30:
        insights.append({
            "param":      "profit_target_pct",
            "current":    cur_profit,
            "suggested":  max(round(cur_profit - 0.10, 2),
                              BOUNDS["profit_target_pct"][0]),
            "reason":     f"Avg hold {avg_hold:.0f} days — positions running into "
                          f"gamma zone. Lowering target exits sooner.",
            "direction":  "lower",
            "confidence": conf,
        })

    # ── Close-at-DTE ──────────────────────────────────────────────────────
    dte_exits = exit_reasons.get("dte_reached", 0)
    if dte_exits > 0 and loss_limit_pct > 20:
        insights.append({
            "param":      "close_at_dte",
            "current":    cur_close_dte,
            "suggested":  min(cur_close_dte + 7, BOUNDS["close_at_dte"][1]),
            "direction":  "raise (exit earlier)",
            "reason":     f"Loss-limit exits ({loss_limit_pct:.0f}%) and DTE exits "
                          f"({dte_exits}) co-occurring — extend DTE exit buffer.",
            "confidence": conf,
        })

    return insights


# ══════════════════════════════════════════════════════════════════════════════
#  Apply changes
# ══════════════════════════════════════════════════════════════════════════════

def apply_insights(cfg: dict, insights: list, n_closed: int) -> tuple[dict, list]:
    """
    Apply high-confidence insights to cfg when n_closed >= MIN_FOR_CHANGES.
    Returns (updated_cfg, list_of_applied_change_dicts).
    """
    if n_closed < MIN_FOR_CHANGES:
        return cfg, []

    applied: list = []
    new_cfg = deepcopy(cfg)

    # Only apply changes with confidence == 'high'
    for ins in insights:
        if ins.get("confidence") != "high":
            continue
        param     = ins["param"]
        suggested = ins["suggested"]

        # Map param name to config path
        if param == "iv_rank_min_sell":
            cur = new_cfg.setdefault("indicators", {}).get("iv_rank_min_sell", 40)
            new_cfg["indicators"]["iv_rank_min_sell"] = suggested
        elif param == "target_delta_csp":
            cur = new_cfg.setdefault("contract_selection", {}).get("target_delta_csp", 0.30)
            new_cfg["contract_selection"]["target_delta_csp"] = suggested
        elif param == "profit_target_pct":
            cur = new_cfg.setdefault("exits", {}).get("profit_target_pct", 0.50)
            new_cfg["exits"]["profit_target_pct"] = suggested
        elif param == "close_at_dte":
            cur = new_cfg.setdefault("exits", {}).get("close_at_dte", 21)
            new_cfg["exits"]["close_at_dte"] = suggested
        else:
            continue

        applied.append({
            "param":  param,
            "from":   cur,
            "to":     suggested,
            "reason": ins["reason"],
            "at":     datetime.now(timezone.utc).isoformat(),
        })

    return new_cfg, applied


# ══════════════════════════════════════════════════════════════════════════════
#  Main entry point
# ══════════════════════════════════════════════════════════════════════════════

def run(auto_optimize: bool | None = None) -> dict:
    print("\n" + "=" * 60)
    print(" Options Optimizer  (Phase 3)")
    print("=" * 60)

    cfg = load_config()
    if auto_optimize is None:
        auto_optimize = cfg.get("auto_optimize", {}).get("enabled", False)

    # ── Load signal quality output ─────────────────────────────────────────
    signal_data: dict = {}
    if SIGNAL_PATH.exists():
        try:
            signal_data = json.loads(SIGNAL_PATH.read_text())
        except Exception:
            pass

    outcome_stats = signal_data.get("outcome_stats", {})
    n_closed      = outcome_stats.get("n", 0)

    # ── Status reporting ───────────────────────────────────────────────────
    if n_closed == 0:
        status_line = (f"0 closed positions — pipeline live, "
                       f"insights activate at {MIN_FOR_INSIGHTS}, "
                       f"auto-adjust at {MIN_FOR_CHANGES}")
        print(f"  {status_line}")
    elif n_closed < MIN_FOR_INSIGHTS:
        status_line = f"{n_closed}/{MIN_FOR_INSIGHTS} closed positions — building data"
        print(f"  {status_line}")
    else:
        wr = outcome_stats.get("win_rate_pct", 0)
        ap = outcome_stats.get("avg_pnl_pct", 0)
        ah = outcome_stats.get("avg_hold_days", 0)
        ay = outcome_stats.get("ann_yield_pct")
        status_line = (f"{n_closed} closed — win rate {wr}%  "
                       f"avg P&L {ap}%  hold {ah}d  "
                       f"ann yield {ay}%/yr")
        print(f"  {status_line}")

    # ── Generate insights ─────────────────────────────────────────────────
    insights = generate_insights(outcome_stats, cfg)

    if insights:
        print(f"\n  Insights ({len(insights)}):")
        for ins in insights:
            mark = "[APPLY]" if (ins["confidence"] == "high"
                                 and auto_optimize
                                 and n_closed >= MIN_FOR_CHANGES) else "[suggest]"
            print(f"    {mark} {ins['param']}: "
                  f"{ins['current']} -> {ins['suggested']}  "
                  f"({ins['direction']}, {ins['confidence']})")
            print(f"           {ins['reason']}")
    else:
        print(f"  No parameter changes suggested")

    # ── Apply changes if warranted ────────────────────────────────────────
    applied: list = []
    if auto_optimize and n_closed >= MIN_FOR_CHANGES and insights:
        new_cfg, applied = apply_insights(cfg, insights, n_closed)
        if applied:
            save_config(new_cfg)
            print(f"\n  Applied {len(applied)} config change(s) to options_config.json")
            for ch in applied:
                print(f"    {ch['param']}: {ch['from']} -> {ch['to']}")
        else:
            print(f"  No high-confidence changes to apply yet")
    elif not auto_optimize:
        print(f"  auto_optimize disabled — insights are suggestions only")

    # ── Build report ──────────────────────────────────────────────────────
    prev_report: dict = {}
    if REPORT_PATH.exists():
        try:
            prev_report = json.loads(REPORT_PATH.read_text())
        except Exception:
            pass

    # Carry forward all-time applied changes
    all_applied = prev_report.get("all_applied_changes", []) + applied

    report = {
        "generated_at":         datetime.now(timezone.utc).isoformat(),
        "pipeline_phase":        3,
        "n_closed_positions":    n_closed,
        "min_for_insights":      MIN_FOR_INSIGHTS,
        "min_for_changes":       MIN_FOR_CHANGES,
        "auto_optimize":         auto_optimize,
        "status":                status_line,
        "outcome_stats":         outcome_stats,
        "current_insights":      insights,
        "applied_this_run":      applied,
        "all_applied_changes":   all_applied,
        "config_snapshot":       signal_data.get("config_snapshot", {}),
        "sell_zone_pct":         signal_data.get("sell_zone_pct"),
        "regime":                signal_data.get("regime"),
        "candidates_scored":     signal_data.get("candidates", []),
    }

    REPORT_PATH.write_text(json.dumps(report, indent=2))
    print(f"\n  Wrote {REPORT_PATH.name}")

    return {
        "n_closed":    n_closed,
        "n_insights":  len(insights),
        "n_applied":   len(applied),
        "auto_optimize": auto_optimize,
    }


if __name__ == "__main__":
    run(auto_optimize="--apply" in sys.argv)
