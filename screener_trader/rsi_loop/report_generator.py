"""
report_generator.py
Generates a human-readable improvement report by combining market regime data,
signal quality analysis, config history, and picks performance stats.
Attempts to use the Claude API with prompt caching; falls back to a built-in
text report if the anthropic package is unavailable or the API call fails.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
ALPACA_CONFIG_PATH = PROJECT_DIR / "alpaca_config.json"
SIGNAL_QUALITY_PATH = PROJECT_DIR / "signal_quality.json"
MARKET_REGIME_PATH = PROJECT_DIR / "market_regime.json"
CONFIG_HISTORY_PATH = PROJECT_DIR / "config_history.json"
PICKS_HISTORY_PATH = PROJECT_DIR / "picks_history.json"
IMPROVEMENT_REPORT_PATH = PROJECT_DIR / "improvement_report.json"

# ── System prompt (long enough for caching — well over 1024 tokens) ───────────
SYSTEM_PROMPT = """You are a quantitative trading analyst specializing in mean-reversion equity strategies.
You assist a retail trader who runs an automated S&P 500 mean-reversion screener that identifies
stocks with:
  - RSI below an oversold threshold (typically 28–40 depending on market regime)
  - Price below the lower Bollinger Band (20-period, 2 standard deviations)
  - Price above the 200-day moving average (trend filter, regime-dependent)
  - Volume above a minimum ratio vs average (typically 1.2–2.0x depending on regime)

The screener runs weekly (Monday mornings) and produces a ranked list of actionable picks
and a radar list of near-misses. Each pick is tracked for forward returns at 1-day, 5-day,
10-day, and 20-day horizons to build a performance history.

The system operates a recursive self-improvement (RSI) loop with the following stages:
  1. Market Regime Detection — classifies current market as bull/mild_correction/correction/
     recovery/geopolitical_shock/bear using SPY and VIXY metrics.
  2. Performance Tracking — logs each screener pick and fills in forward returns as they mature.
  3. Signal Analysis — groups picks by regime, RSI bucket, volume bucket, 200MA bucket, and
     filter combinations to compute hit rates, average returns, and Pearson correlations.
  4. Config Optimization — uses either regime-default parameters or data-derived parameters
     (when sufficient historical picks exist) to tune RSI threshold, volume minimum,
     200MA filter requirement, and scoring weights.
  5. Report Generation — this stage. You produce a concise, actionable improvement report
     that helps the trader understand what the data shows and why parameters changed.

Your reports should be:
  - Concise and actionable (focus on what matters for the next week of trading)
  - Honest about data limitations (small sample sizes, regime changes, etc.)
  - Specific: reference actual numbers from the provided context
  - Structured: use a brief header, bullet-point key findings, and a short recommendation section
  - No more than 600 words — the trader reads this in a dashboard panel

Always begin your report with: "RSI Loop Report — [date]"
Then include sections:
  ## Market Regime
  ## Signal Quality
  ## Parameter Changes
  ## Recommendation

Use plain text only — no markdown formatting that would not render in a monospace dashboard panel.
Asterisks for bold are fine. Avoid complex tables.

You are operating in a live paper-trading environment. The trader monitors this dashboard
daily and uses your report to gain confidence in (or override) the automated parameter changes.
Be direct and data-driven. If sample sizes are too small to draw conclusions, say so clearly.
Remind the trader that past pick performance should inform but not override market judgment.
"""


# ── API key resolution ────────────────────────────────────────────────────────

def get_api_key():
    """Check GEMINI_API_KEY env var, then alpaca_config.json gemini_api_key."""
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    if ALPACA_CONFIG_PATH.exists():
        with open(ALPACA_CONFIG_PATH, "r") as f:
            cfg = json.load(f)
        key = cfg.get("gemini_api_key")
        if key:
            return key
    return None


# ── Context loading ───────────────────────────────────────────────────────────

def load_context():
    """
    Load signal_quality.json, market_regime.json, last 5 config_history.json entries,
    and a summary of picks_history.json.
    Returns a combined context dict.
    """
    ctx = {}

    if SIGNAL_QUALITY_PATH.exists():
        with open(SIGNAL_QUALITY_PATH, "r") as f:
            ctx["signal_quality"] = json.load(f)
    else:
        ctx["signal_quality"] = None

    if MARKET_REGIME_PATH.exists():
        with open(MARKET_REGIME_PATH, "r") as f:
            ctx["market_regime"] = json.load(f)
    else:
        ctx["market_regime"] = None

    if CONFIG_HISTORY_PATH.exists():
        with open(CONFIG_HISTORY_PATH, "r") as f:
            raw = f.read().strip()
        if raw:
            history_list = json.loads(raw)
            ctx["config_history_last5"] = history_list[-5:]
        else:
            ctx["config_history_last5"] = []
    else:
        ctx["config_history_last5"] = []

    # Picks summary
    if PICKS_HISTORY_PATH.exists():
        with open(PICKS_HISTORY_PATH, "r") as f:
            picks_data = json.load(f)
        picks = picks_data.get("picks", [])
        total_count = len(picks)
        with_returns = sum(
            1 for p in picks
            if any(p.get("returns", {}).get(f"{n}d") is not None for n in [1, 5, 10, 20])
        )
        returns_5d = [p["returns"]["5d"] for p in picks if p.get("returns", {}).get("5d") is not None]
        if returns_5d:
            avg_5d = round(sum(returns_5d) / len(returns_5d), 2)
            hit_rate = round(sum(1 for r in returns_5d if r > 0) / len(returns_5d), 3)
        else:
            avg_5d = None
            hit_rate = None
        ctx["picks_summary"] = {
            "total_count": total_count,
            "with_returns": with_returns,
            "overall_avg_5d_return": avg_5d,
            "overall_hit_rate_5d": hit_rate,
        }
    else:
        ctx["picks_summary"] = {"total_count": 0, "with_returns": 0}

    return ctx


# ── Prompt construction ───────────────────────────────────────────────────────

def build_user_prompt(context):
    """Build detailed user prompt referencing actual numbers from context."""
    lines = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines.append(f"Please generate an RSI Loop Report for {today}.\n")

    # Market regime
    regime_data = context.get("market_regime")
    if regime_data:
        r = regime_data
        spy = r.get("spy_metrics", {})
        vixy = r.get("vixy_metrics", {})
        lines.append("MARKET REGIME DATA:")
        lines.append(f"  Regime: {r.get('regime', 'unknown')}")
        lines.append(f"  SPY current: ${spy.get('current_price', 'N/A')}")
        lines.append(f"  SPY vs 200MA: {spy.get('spy_vs_200ma_pct', 'N/A')}%")
        lines.append(f"  SPY 20d return: {spy.get('spy_20d_return_pct', 'N/A')}%")
        lines.append(f"  SPY 5d return: {spy.get('spy_5d_return_pct', 'N/A')}%")
        lines.append(f"  VIXY current: {vixy.get('current_price', 'N/A')}")
        lines.append(f"  VIXY 20d avg: {vixy.get('vixy_20d_avg', 'N/A')}")
        lines.append(f"  VIX elevated: {vixy.get('vix_elevated', 'N/A')}")
        lines.append(f"  Computed at: {r.get('computed_at', 'N/A')}")
    else:
        lines.append("MARKET REGIME DATA: Not available")
    lines.append("")

    # Signal quality
    sq = context.get("signal_quality")
    if sq:
        lines.append("SIGNAL QUALITY DATA:")
        lines.append(f"  Total samples: {sq.get('total_samples', 0)}")
        corr = sq.get("correlations", {})
        lines.append(f"  Correlations with 5d return:")
        lines.append(f"    RSI: {corr.get('rsi_vs_5d_return', 'N/A')}")
        lines.append(f"    Volume ratio: {corr.get('vol_ratio_vs_5d_return', 'N/A')}")
        lines.append(f"    Pct below BB: {corr.get('pct_below_bb_vs_5d_return', 'N/A')}")
        lines.append(f"    Pct above 200MA: {corr.get('pct_above_200ma_vs_5d_return', 'N/A')}")

        by_regime = sq.get("by_regime", {})
        if by_regime:
            lines.append(f"  By regime (5d stats):")
            for regime, stats in by_regime.items():
                if stats:
                    lines.append(f"    {regime}: n={stats.get('n')}, "
                                 f"hit_rate={stats.get('hit_rate_5d')}, "
                                 f"avg={stats.get('avg_5d_return')}%")

        rsi_buckets = sq.get("by_rsi_bucket", {})
        if rsi_buckets:
            lines.append(f"  Best RSI bucket by avg return:")
            best = max(rsi_buckets.items(),
                       key=lambda kv: kv[1].get("avg_5d_return", -999) if kv[1] else -999,
                       default=(None, None))
            if best[0]:
                lines.append(f"    {best[0]}: n={best[1].get('n')}, "
                             f"avg={best[1].get('avg_5d_return')}%")

        ma_buckets = sq.get("by_ma200_bucket", {})
        if "above" in ma_buckets and "below" in ma_buckets:
            above = ma_buckets["above"]
            below = ma_buckets["below"]
            lines.append(f"  200MA comparison:")
            lines.append(f"    Above 200MA: n={above.get('n')}, avg={above.get('avg_5d_return')}%")
            lines.append(f"    Below 200MA: n={below.get('n')}, avg={below.get('avg_5d_return')}%")
    else:
        lines.append("SIGNAL QUALITY DATA: Not available")
    lines.append("")

    # Picks summary
    ps = context.get("picks_summary", {})
    lines.append("PICKS HISTORY SUMMARY:")
    lines.append(f"  Total logged picks: {ps.get('total_count', 0)}")
    lines.append(f"  Picks with return data: {ps.get('with_returns', 0)}")
    lines.append(f"  Overall avg 5d return: {ps.get('overall_avg_5d_return', 'N/A')}%")
    lines.append(f"  Overall 5d hit rate: {ps.get('overall_hit_rate_5d', 'N/A')}")
    lines.append("")

    # Config history
    history = context.get("config_history_last5", [])
    if history:
        lines.append("RECENT CONFIG CHANGES (last 5):")
        for entry in reversed(history):
            lines.append(f"  [{entry.get('timestamp', '')[:10]}] "
                         f"Regime: {entry.get('regime')} | "
                         f"Method: {entry.get('method')} | "
                         f"Changes: {', '.join(entry.get('changes', [])) or 'none'}")
    else:
        lines.append("RECENT CONFIG CHANGES: None yet")
    lines.append("")

    lines.append("Generate the improvement report now, following the structure in your instructions.")

    return "\n".join(lines)


# ── Gemini API call ───────────────────────────────────────────────────────────

def call_llm(api_key, user_prompt):
    """
    Call Gemini 2.5 Flash with system instruction.
    Returns the response text.
    """
    from google import genai
    from google.genai import types as genai_types

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model    = "gemini-2.5-flash",
        contents = user_prompt,
        config   = genai_types.GenerateContentConfig(
            system_instruction = SYSTEM_PROMPT,
            max_output_tokens  = 8192,
            temperature        = 0.3,
        ),
    )
    return response.text


# ── Fallback report ───────────────────────────────────────────────────────────

def build_fallback_report(context, reason):
    """Generate a useful text report from stats without Claude."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"RSI Loop Report — {today}", "=" * 40, ""]

    regime_data = context.get("market_regime")
    if regime_data:
        regime = regime_data.get("regime", "unknown")
        spy = regime_data.get("spy_metrics", {})
        lines.append("## Market Regime")
        lines.append(f"  Current regime: *{regime.upper()}*")
        lines.append(f"  SPY vs 200MA: {spy.get('spy_vs_200ma_pct', 'N/A')}%")
        lines.append(f"  SPY 20d return: {spy.get('spy_20d_return_pct', 'N/A')}%")
        lines.append(f"  SPY 5d return: {spy.get('spy_5d_return_pct', 'N/A')}%")
        vixy = regime_data.get("vixy_metrics", {})
        lines.append(f"  VIX elevated: {vixy.get('vix_elevated', 'N/A')}")
    else:
        regime = "unknown"
        lines.append("## Market Regime")
        lines.append("  No regime data available. Run regime_detector.py.")
    lines.append("")

    sq = context.get("signal_quality")
    lines.append("## Signal Quality")
    if sq and sq.get("total_samples", 0) > 0:
        total = sq["total_samples"]
        lines.append(f"  Samples: {total}")
        corr = sq.get("correlations", {})
        lines.append(f"  RSI correlation with 5d return: {corr.get('rsi_vs_5d_return', 'N/A')}")
        lines.append(f"  Vol ratio correlation: {corr.get('vol_ratio_vs_5d_return', 'N/A')}")
        lines.append(f"  BB distance correlation: {corr.get('pct_below_bb_vs_5d_return', 'N/A')}")
        if total < 10:
            lines.append(f"  NOTE: Only {total} samples — insufficient for data-derived optimization.")
            lines.append("  Using regime-default parameters until 10+ samples accumulate.")
    else:
        lines.append("  No signal data yet. Run screener and let picks mature over 5+ trading days.")
    lines.append("")

    ps = context.get("picks_summary", {})
    lines.append("## Performance Summary")
    lines.append(f"  Total picks tracked: {ps.get('total_count', 0)}")
    lines.append(f"  Picks with return data: {ps.get('with_returns', 0)}")
    if ps.get("overall_avg_5d_return") is not None:
        lines.append(f"  Avg 5d return: {ps.get('overall_avg_5d_return')}%")
        lines.append(f"  5d hit rate: {ps.get('overall_hit_rate_5d', 'N/A')}")
    lines.append("")

    history = context.get("config_history_last5", [])
    lines.append("## Parameter Changes")
    if history:
        latest = history[-1]
        changes = latest.get("changes", [])
        method = latest.get("method", "unknown")
        lines.append(f"  Method: {method}")
        if changes:
            for c in changes:
                lines.append(f"  - {c}")
        else:
            lines.append("  - No changes from previous run.")
    else:
        lines.append("  No config history yet.")
    lines.append("")

    lines.append("## Recommendation")
    lines.append(f"  Operating in *{regime.upper()}* regime.")
    lines.append("  Monitor picks as they mature to build signal quality data.")
    lines.append("  Review top picks before market open Monday for execution.")
    lines.append("")
    lines.append(f"  [Fallback report — reason: {reason}]")

    return "\n".join(lines)


# ── Main run ──────────────────────────────────────────────────────────────────

def run():
    """Full report generation flow. Writes improvement_report.json."""
    context = load_context()

    regime_data = context.get("market_regime", {}) or {}
    regime = regime_data.get("regime", "unknown")

    sq = context.get("signal_quality", {}) or {}
    sample_count = sq.get("total_samples", 0)

    history = context.get("config_history_last5", [])
    method = history[-1].get("method", "unknown") if history else "unknown"
    changes = history[-1].get("changes", []) if history else []

    # Try Claude API
    report_text = None
    source = "fallback"
    fallback_reason = None

    api_key = get_api_key()
    if api_key:
        max_attempts = 3
        retry_delay  = 45   # seconds between attempts
        user_prompt  = build_user_prompt(context)
        for attempt in range(1, max_attempts + 1):
            try:
                report_text = call_llm(api_key, user_prompt)
                source = "gemini_api"
                if attempt > 1:
                    print(f"  [report_generator] Gemini succeeded on attempt {attempt}.")
                else:
                    print("  [report_generator] Report generated via Gemini API.")
                break
            except ImportError:
                fallback_reason = "google_genai_not_installed"
                print("  [report_generator] google-genai package not available — using fallback.")
                break
            except Exception as e:
                if attempt < max_attempts:
                    print(f"  [report_generator] Gemini attempt {attempt} failed ({str(e)[:80]}) — retrying in {retry_delay}s...")
                    import time as _time
                    _time.sleep(retry_delay)
                else:
                    fallback_reason = f"api_error: {str(e)[:100]}"
                    print(f"  [report_generator] Gemini API error: {e} — using fallback.")
    else:
        fallback_reason = "no_api_key"
        print("  [report_generator] No Gemini API key found — using fallback report.")

    if report_text is None:
        report_text = build_fallback_report(context, fallback_reason or "unknown")
        source = "fallback"

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "regime": regime,
        "sample_count": sample_count,
        "method": method,
        "source": source,
        "fallback_reason": fallback_reason,
        "changes_applied": changes,
        "report": report_text,
    }

    with open(IMPROVEMENT_REPORT_PATH, "w") as f:
        json.dump(result, f, indent=2)

    print(f"  [report_generator] Report written to {IMPROVEMENT_REPORT_PATH} (source: {source})")
    return result


if __name__ == "__main__":
    result = run()
    print("\n" + result["report"])
