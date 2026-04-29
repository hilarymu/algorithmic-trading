"""
rsi_main.py
Orchestrates the full RSI recursive self-improvement loop:
  1. Market regime detection
  2. Fill missing forward returns
  3. Signal quality analysis
  4. Config optimization
  5. Research layer  (Claude-powered oversold candidate scan)
  6. Run screener    (mechanical 4-filter screen)
  7. Log new picks + fill returns
  8. Generate improvement report
"""

import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent

# Ensure the project root is on sys.path so 'rsi_loop' is importable
# regardless of whether rsi_main.py is run as a script or as a module
_project_root = str(PROJECT_DIR.resolve())
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


def _header(step, total, title):
    print(f"\n{'=' * 38}\n STEP {step}/{total}: {title}\n{'=' * 38}")


def main(skip_screener=False):
    total_steps = 8
    regime      = "unknown"
    opt_result  = {}

    # ── STEP 1: Market Regime Detection ───────────────────────────────────────
    _header(1, total_steps, "MARKET REGIME DETECTION")
    try:
        from rsi_loop import regime_detector
        regime_data = regime_detector.detect_and_write()
        regime = regime_data.get("regime", "unknown")
        print(f"  Regime: {regime}")
    except Exception as e:
        print(f"  ERROR in regime detection: {e}")

    # ── STEP 2: Fill Missing Returns ──────────────────────────────────────────
    _header(2, total_steps, "FILL MISSING RETURNS")
    try:
        from rsi_loop import performance_tracker
        performance_tracker.run()
    except Exception as e:
        print(f"  ERROR filling returns: {e}")

    # ── STEP 3: Signal Analysis ───────────────────────────────────────────────
    _header(3, total_steps, "SIGNAL QUALITY ANALYSIS")
    try:
        from rsi_loop import signal_analyzer
        signal_analyzer.run()
    except Exception as e:
        print(f"  ERROR in signal analysis: {e}")

    # ── STEP 4: Optimize Config ───────────────────────────────────────────────
    _header(4, total_steps, "OPTIMIZE SCREENER CONFIG")
    try:
        from rsi_loop import optimizer
        opt_result = optimizer.run()
        print(f"  Method: {opt_result.get('method')} | "
              f"Changes: {len(opt_result.get('changes', []))}")
    except Exception as e:
        print(f"  ERROR in optimization: {e}")
        opt_result = {}

    # ── STEP 5: Research Layer ────────────────────────────────────────────────
    _header(5, total_steps, "RESEARCH LAYER (OVERSOLD CANDIDATE SCAN)")
    research_result = {}
    try:
        from rsi_loop import research_layer
        research_result = research_layer.run(regime=regime)
        n_found = research_result.get("candidates_found", 0)
        source  = research_result.get("source", "unknown")
        scanned = research_result.get("symbols_scanned", 0)
        print(f"  Scanned: {scanned} symbols | "
              f"Candidates: {n_found} | Source: {source}")
    except Exception as e:
        print(f"  ERROR in research layer: {e}")

    # ── STEP 6: Run Screener ──────────────────────────────────────────────────
    _header(6, total_steps, "RUN SCREENER (MECHANICAL 4-FILTER)")
    screener_path = PROJECT_DIR / "screener.py"
    if skip_screener:
        print("  Skipped (--no-screener flag)")
    else:
        try:
            if not screener_path.exists():
                print(f"  screener.py not found at {screener_path} — skipping.")
            else:
                result = subprocess.run(
                    ["py", "-3", str(screener_path)],
                    cwd=str(PROJECT_DIR),
                    capture_output=False,
                )
                print(f"  Screener exited with code {result.returncode}")
        except Exception as e:
            print(f"  ERROR running screener: {e}")

    # ── STEP 7: Log New Picks + Fill Returns ──────────────────────────────────
    _header(7, total_steps, "LOG NEW PICKS & FILL RETURNS")
    screener_results_path = PROJECT_DIR / "screener_results.json"
    try:
        from rsi_loop import performance_tracker as pt

        # Log mechanical screener picks (when screener found something)
        if screener_results_path.exists():
            pt.log_new_picks(str(screener_results_path), regime)
        else:
            print("  screener_results.json not found — skipping mechanical pick logging.")

        # Log ALL research-layer oversold candidates so history accumulates
        # even when the mechanical screener finds 0 picks (e.g. bull-market ATH).
        research_candidates = research_result.get("top_candidates", [])
        if research_candidates:
            pt.log_research_picks(research_candidates, regime)
        else:
            print("  No research candidates to log.")

        pt.run()
    except Exception as e:
        print(f"  ERROR logging picks: {e}")

    # ── STEP 8: Generate Report ───────────────────────────────────────────────
    _header(8, total_steps, "GENERATE IMPROVEMENT REPORT")
    report_result = {}
    try:
        from rsi_loop import report_generator
        report_result = report_generator.run()
    except Exception as e:
        print(f"  ERROR generating report: {e}")

    # ── Final Summary ─────────────────────────────────────────────────────────
    print(f"\n{'=' * 38}\n SUMMARY\n{'=' * 38}")
    print(f"  Regime:           {regime}")
    print(f"  Method:           {opt_result.get('method', 'N/A')}")
    print(f"  Sample count:     {opt_result.get('sample_count', 'N/A')}")

    changes = opt_result.get("changes", [])
    if changes:
        print(f"  Changes applied ({len(changes)}):")
        for c in changes:
            print(f"    - {c}")
    else:
        print("  Changes applied:  none")

    n_res = research_result.get("candidates_found", "N/A")
    s_res = research_result.get("source", "N/A")
    print(f"  Research picks:   {n_res} candidates | source: {s_res}")
    print(f"  Report source:    {report_result.get('source', 'N/A')}")

    print("\n  RSI loop complete.\n")


if __name__ == "__main__":
    try:
        skip_screener = "--no-screener" in sys.argv
        main(skip_screener=skip_screener)
    except KeyboardInterrupt:
        print("\n  Interrupted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n  FATAL ERROR: {e}")
        sys.exit(1)
