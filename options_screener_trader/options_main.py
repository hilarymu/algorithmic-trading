"""
options_main.py
===============
Daily orchestrator for the options screener pipeline.

Phase 1  OK  IV history building + research screener -- no orders placed.
Phase 2  OK  Strategy selector, executor (live/paper orders), intraday monitor.
Phase 3  OK  Signal analyzer + self-optimizing loop + HTML dashboard.
Phase 4  DEFERRED  SQLite migration (ADR-010, revisit at >= 50 closed positions).

Two-phase daily schedule
------------------------
15:30 ET  (pre-close, market still open):
    scripts/run_options_preclose.bat  ->  options_main.py --pre-close
    Runs: iv_tracker + screener + strategy_selector
    Purpose: fetch live IV snapshots and pick option contracts while
             Alpaca options data is still streaming.

16:30 ET  (post-close, EOD):
    scripts/run_options_loop.bat  ->  options_main.py --post-close
    Runs: monitor + executor + signal_analyzer + optimizer + dashboard
    Skips: iv_tracker (already ran), screener (already ran), selector (already ran)

Intraday exit monitor:
    scripts/run_options_monitor_intraday.bat  ->  options_main.py --intraday
    Long-lived process: loops every 15 min from 09:30 to 16:00 ET then exits.

Manual runs
-----------
    py -3 options_main.py                  # full pipeline (both phases)
    py -3 options_main.py --pre-close      # IV + screener + selector only
    py -3 options_main.py --post-close     # monitor + executor + analysis only
    py -3 options_main.py --no-iv          # skip IV fetch (manual override)
    py -3 options_main.py --backfill       # force full historical backfill
    py -3 options_main.py --intraday       # start intraday monitor loop
    py -3 options_main.py --force          # override weekend guard
"""

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).parent
DATA_DIR    = PROJECT_DIR / "data"
LOG_DIR     = PROJECT_DIR / "logs"
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

sys.path.insert(0, str(PROJECT_DIR))

# -- Intraday constants --------------------------------------------------------
INTRADAY_INTERVAL_MIN = 15
MARKET_OPEN_ET        = (9, 30)
MARKET_CLOSE_ET       = (16, 0)


def _log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# -- First-run backfill check --------------------------------------------------

def _needs_backfill() -> bool:
    """
    Return True when iv_history.json is absent or most symbols have fewer
    than MIN_BACKFILL_DAYS of readings.
    """
    MIN_BACKFILL_DAYS = 30
    hist_path = DATA_DIR / "iv_history.json"
    if not hist_path.exists():
        return True
    try:
        import json
        with open(hist_path) as f:
            hist = json.load(f)
        if len(hist) < 5:
            return True
        days_counts = [len(v) for v in hist.values()]
        median_days = sorted(days_counts)[len(days_counts) // 2]
        return median_days < MIN_BACKFILL_DAYS
    except Exception:
        return True


def _run_backfill(force: bool = False):
    _log("Running historical IV backfill...")
    try:
        from options_loop.iv_backfill import run as run_backfill
        result = run_backfill(force=force)
        if result and not result.get("skipped"):
            _log(f"  Backfill done: {result.get('new_readings', 0):,} new readings, "
                 f"{result.get('with_iv_rank', 0)} symbols with IV rank")
        elif result and result.get("skipped"):
            _log("  Backfill skipped -- history already complete")
    except Exception as e:
        _log(f"  Backfill WARNING: {e}")
        _log("  Daily tracker will build history incrementally")
        import traceback
        traceback.print_exc()


# -- Intraday monitor loop -----------------------------------------------------

def _run_intraday():
    """
    Intraday exit monitor -- runs during market hours, checks every
    INTRADAY_INTERVAL_MIN minutes.
    """
    import zoneinfo
    ET = zoneinfo.ZoneInfo("America/New_York")

    _log("Intraday monitor starting")
    _log(f"  Interval : {INTRADAY_INTERVAL_MIN} minutes")
    _log(f"  Window   : {MARKET_OPEN_ET[0]:02d}:{MARKET_OPEN_ET[1]:02d} - "
         f"{MARKET_CLOSE_ET[0]:02d}:{MARKET_CLOSE_ET[1]:02d} ET")

    while True:
        now_et   = datetime.now(ET)
        now_mins = now_et.hour * 60 + now_et.minute
        open_m   = MARKET_OPEN_ET[0]  * 60 + MARKET_OPEN_ET[1]
        close_m  = MARKET_CLOSE_ET[0] * 60 + MARKET_CLOSE_ET[1]

        if now_et.weekday() >= 5:
            _log("Weekend -- intraday monitor exiting")
            break

        if now_mins >= close_m:
            _log("Market closed -- intraday monitor exiting")
            break

        if now_mins >= open_m:
            _log(f"[intraday] {now_et.strftime('%H:%M ET')} -- checking exits...")
            try:
                from options_loop.options_monitor import check_exits_intraday
                result = check_exits_intraday()
                _log(f"  checked={result.get('checked',0)}  closed={result.get('closed',0)}")
            except Exception as e:
                _log(f"  [intraday] monitor ERROR: {e}")
                import traceback
                traceback.print_exc()
        else:
            _log(f"Pre-market ({now_et.strftime('%H:%M ET')}) -- waiting for open...")

        time.sleep(INTRADAY_INTERVAL_MIN * 60)

    _log("Intraday monitor done")


# -- Main daily run ------------------------------------------------------------

def run():
    start          = datetime.now(timezone.utc)
    args           = set(sys.argv[1:])

    # Phase flags
    pre_close      = "--pre-close"  in args   # 15:30 ET: IV + screener + selector
    post_close     = "--post-close" in args   # 16:30 ET: monitor + executor + EOD steps

    # Granular overrides (respected in any mode)
    force_backfill = "--backfill"   in args
    force_run      = "--force"      in args

    # Derived skip flags
    skip_iv        = "--no-iv"      in args or post_close
    skip_screener  = post_close                  # screener ran in pre-close
    skip_selector  = post_close                  # selector ran in pre-close
    skip_eod       = pre_close                   # monitor/executor/analysis ran post-close

    # Mode label for logging
    if pre_close:
        mode_label = "pre-close (IV + screener + selector)"
    elif post_close:
        mode_label = "post-close (monitor + executor + analysis)"
    else:
        mode_label = "full pipeline"

    _log("options_main starting")
    _log(f"Mode: {mode_label}")

    # -- Weekend guard ---------------------------------------------------------
    if not force_run and start.weekday() >= 5:
        _log(f"Weekend detected ({start.strftime('%A')}) -- skipping pipeline. "
             f"Use --force to override.")
        _log("options_main done (weekend skip)")
        return

    # -- Step 0: Historical backfill -------------------------------------------
    if force_backfill:
        _log("--backfill flag: forcing full historical backfill")
        _run_backfill(force=True)
    elif _needs_backfill():
        _log("First run detected -- bootstrapping IV history from Alpaca historical data")
        _run_backfill(force=False)
    else:
        _log("IV history present -- skipping backfill")

    # -- Step 1: Daily IV tracker (pre-close or full run) ----------------------
    if not skip_iv:
        _log("Running iv_tracker...")
        try:
            from options_loop.iv_tracker import run as run_iv
            result = run_iv()
            if result:
                _log(f"  iv_tracker done: {result.get('iv_fetched', 0)} symbols tracked, "
                     f"{result.get('with_iv_rank', 0)} with full IV rank")
        except Exception as e:
            _log(f"  iv_tracker ERROR: {e}")
            import traceback
            traceback.print_exc()
    else:
        _log("iv_tracker skipped (post-close mode -- ran at 15:30)")

    # -- Step 2: Research screener (pre-close or full run) ---------------------
    screener_candidates = []
    if not skip_screener:
        _log("Running options_screener (research mode)...")
        try:
            from options_loop.options_screener import run as run_screener
            result = run_screener()
            if result:
                _log(f"  screener done: {result.get('candidates', 0)} candidates, "
                     f"regime={result.get('regime', 'unknown')}, "
                     f"{result.get('picks_added', 0)} new picks logged")
                cand_path = DATA_DIR / "options_candidates.json"
                if cand_path.exists():
                    import json as _json
                    with open(cand_path) as _f:
                        screener_candidates = _json.load(_f).get("candidates", [])
        except Exception as e:
            _log(f"  screener ERROR: {e}")
            import traceback
            traceback.print_exc()
    else:
        _log("options_screener skipped (post-close mode -- ran at 15:30)")
        # Load candidates written by pre-close run for use by EOD steps
        try:
            import json as _json
            cand_path = DATA_DIR / "options_candidates.json"
            if cand_path.exists():
                with open(cand_path) as _f:
                    screener_candidates = _json.load(_f).get("candidates", [])
        except Exception:
            pass

    # -- Step 3: Monitor exits (post-close EOD check or full run) --------------
    if not skip_eod:
        _log("Running options_monitor (daily close check)...")
        try:
            from options_loop.options_monitor import run as run_monitor
            result = run_monitor()
            if result:
                _log(f"  monitor done: {result.get('checked', 0)} checked, "
                     f"{result.get('closed', 0)} closed")
        except Exception as e:
            _log(f"  monitor ERROR: {e}")
            import traceback
            traceback.print_exc()

    # -- Step 4: Strategy selector (pre-close or full run) --------------------
    if not skip_selector:
        _log("Running options_strategy_selector...")
        try:
            from options_loop.options_strategy_selector import run as run_selector
            run_selector(candidates=screener_candidates if screener_candidates else None)
        except Exception as e:
            _log(f"  selector ERROR: {e}")
            import traceback
            traceback.print_exc()
    else:
        _log("options_strategy_selector skipped (post-close mode -- ran at 15:30)")

    # -- Pre-close exit point --------------------------------------------------
    if pre_close:
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        _log(f"options_main pre-close done in {elapsed:.1f}s "
             f"-- pending entries ready for 16:30 executor")
        return

    # -- Step 5: Executor (post-close or full run) -----------------------------
    _log("Running options_executor...")
    try:
        from options_loop.options_executor import run as run_executor
        result = run_executor()
        if result:
            _log(f"  executor done: {result.get('executed', 0)} executed, "
                 f"{result.get('skipped', 0)} skipped")
    except Exception as e:
        _log(f"  executor ERROR: {e}")
        import traceback
        traceback.print_exc()

    # -- Step 6: Signal analyzer -----------------------------------------------
    _log("Running options_signal_analyzer...")
    try:
        from options_loop.options_signal_analyzer import run as run_analyzer
        result = run_analyzer()
        if result:
            _log(f"  analyzer done: {result.get('n_candidates_scored', 0)} scored, "
                 f"sell_zone={result.get('sell_zone_pct', 0):.0f}%  "
                 f"data={result.get('data_quality', '?')}")
    except Exception as e:
        _log(f"  analyzer ERROR: {e}")
        import traceback
        traceback.print_exc()

    # -- Step 7: Optimizer -----------------------------------------------------
    _log("Running options_optimizer...")
    try:
        from options_loop.options_optimizer import run as run_optimizer
        result = run_optimizer()
        if result:
            _log(f"  optimizer done: {result.get('n_closed', 0)} closed, "
                 f"{result.get('n_insights', 0)} insights, "
                 f"{result.get('n_applied', 0)} applied")
    except Exception as e:
        _log(f"  optimizer ERROR: {e}")
        import traceback
        traceback.print_exc()

    # -- Step 8: Dashboard -----------------------------------------------------
    _log("Running options_dashboard...")
    try:
        from options_loop.options_dashboard import run as run_dashboard
        result = run_dashboard()
        if result:
            _log(f"  dashboard written: {result.get('output', '?')} "
                 f"({result.get('candidates', 0)} candidates, "
                 f"{result.get('open_positions', 0)} open, "
                 f"{result.get('closed_positions', 0)} closed)")
    except Exception as e:
        _log(f"  dashboard ERROR: {e}")
        import traceback
        traceback.print_exc()

    elapsed = (datetime.now(timezone.utc) - start).total_seconds()
    _log(f"options_main done in {elapsed:.1f}s")


if __name__ == "__main__":
    if "--intraday" in sys.argv:
        _run_intraday()
    else:
        run()
