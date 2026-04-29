"""
options_dashboard.py
====================
Step 8 of the daily pipeline.

Generates a self-contained HTML dashboard from the current pipeline data files.
Output: data/dashboard.html  — open in any browser, no server required.

Reads (all optional — missing files render gracefully):
  data/options_candidates.json
  data/options_signal_quality.json
  data/options_improvement_report.json
  data/positions_state.json
  data/options_picks_history.json
  data/iv_rank_cache.json
"""

import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR   = Path(__file__).parent.parent
DATA_DIR      = PROJECT_DIR / "data"
OUTPUT_PATH   = DATA_DIR / "dashboard.html"

# ── Data loader ───────────────────────────────────────────────────────────────

def _load(filename, default=None):
    path = DATA_DIR / filename
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt_pct(v, decimals=1):
    if v is None:
        return "—"
    return f"{v:.{decimals}f}%"

def _fmt_float(v, decimals=2):
    if v is None:
        return "—"
    return f"{v:.{decimals}f}"

def _fmt_ts(iso_str):
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return iso_str[:16]

def _signal_class(score):
    if score is None:
        return "neutral"
    if score >= 50:
        return "high"
    if score >= 20:
        return "medium"
    return "low"

def _pnl_class(v):
    if v is None:
        return "neutral"
    return "profit" if v > 0 else "loss"

def _iv_bar(pct, color):
    width = min(int(pct * 2), 100)   # scale so 50% fills ~the bar
    return (f'<div class="bar-wrap">'
            f'<div class="bar" style="width:{width}%;background:{color}"></div>'
            f'<span class="bar-label">{pct:.1f}%</span></div>')


# ── Section renderers ─────────────────────────────────────────────────────────

def _render_header(report, signal_quality):
    ts        = _fmt_ts(report.get("generated_at") or signal_quality.get("generated_at"))
    regime    = (report.get("regime") or signal_quality.get("regime") or "unknown").upper()
    quality   = signal_quality.get("data_quality", "—")
    sell_zone = signal_quality.get("sell_zone_pct")
    n_symbols = signal_quality.get("n_symbols_with_rank", 0)
    phase     = report.get("pipeline_phase", 3)

    regime_class = {"BULL": "regime-bull", "BEAR": "regime-bear"}.get(regime, "regime-neutral")

    sell_txt = f"{sell_zone:.0f}%" if sell_zone is not None else "—"
    quality_label = {"real_iv": "Live IV", "proxy": "HV30 Proxy", "mixed": "Mixed"}.get(quality, quality)

    return f"""
<header>
  <div class="header-left">
    <h1>📊 Options Screener Trader</h1>
    <span class="subtitle">Paper Trading Dashboard · Phase {phase}</span>
  </div>
  <div class="header-right">
    <div class="stat-pill {regime_class}">Regime: {regime}</div>
    <div class="stat-pill">IV Data: {quality_label}</div>
    <div class="stat-pill">Sell Zone: {sell_txt} of {n_symbols} symbols</div>
    <div class="updated">Updated: {ts}</div>
  </div>
</header>"""


def _render_candidates(signal_quality):
    candidates = signal_quality.get("candidates", [])
    outcome    = signal_quality.get("outcome_stats", {})

    rows = ""
    if not candidates:
        rows = '<tr><td colspan="9" class="empty">No candidates today</td></tr>'
    else:
        for c in candidates:
            sc    = c.get("signal_strength")
            sc_cl = _signal_class(sc)
            sc_txt = f"{sc:.1f}" if sc is not None else "—"
            ep    = c.get("est_premium_pct")
            ea    = c.get("est_annual_pct")
            ek    = c.get("est_strike")
            ne    = "⚠️" if c.get("near_earnings") else ""
            rows += f"""<tr>
              <td class="sym">{c.get('symbol','—')}{ne}</td>
              <td>{c.get('strategy','—')}</td>
              <td class="num">{_fmt_float(c.get('rsi'), 1)}</td>
              <td class="num">{_fmt_float(c.get('iv_rank'), 1)}</td>
              <td class="num">{_fmt_float(c.get('iv_current', 0) * 100, 1)}%</td>
              <td class="num">{_fmt_float(c.get('vol_ratio'), 2)}×</td>
              <td class="num">${_fmt_float(ek, 2)}</td>
              <td class="num">{_fmt_pct(ep)} / {_fmt_pct(ea)} ann.</td>
              <td class="num score {sc_cl}">{sc_txt}</td>
            </tr>"""

    # Outcome summary
    n_closed  = outcome.get("n", 0)
    win_rate  = outcome.get("win_rate_pct")
    avg_pnl   = outcome.get("avg_pnl_pct")
    wr_txt    = f"{win_rate:.1f}%" if win_rate is not None else "—"
    pnl_txt   = f"{avg_pnl:+.2f}%" if avg_pnl is not None else "—"
    pnl_cl    = _pnl_class(avg_pnl)

    return f"""
<section class="card">
  <h2>Today's Signals <span class="badge">{len(candidates)}</span></h2>
  <div class="outcome-bar">
    <span>Closed: <strong>{n_closed}</strong></span>
    <span>Win Rate: <strong>{wr_txt}</strong></span>
    <span>Avg P&amp;L: <strong class="{pnl_cl}">{pnl_txt}</strong></span>
  </div>
  <table>
    <thead>
      <tr>
        <th>Symbol</th><th>Strategy</th><th>RSI</th><th>IV Rank</th>
        <th>IV%</th><th>Vol×</th><th>Est Strike</th><th>Est Premium</th>
        <th>Score</th>
      </tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  <p class="legend">
    Score: <span class="score high">≥50 High</span>
    <span class="score medium">20–49 Medium</span>
    <span class="score low">&lt;20 Low</span>
    · ⚠️ Near earnings
  </p>
</section>"""


def _render_positions(state):
    if not state:
        open_pos   = []
        closed_pos = []
    else:
        all_pos    = state if isinstance(state, list) else []
        open_pos   = [p for p in all_pos if p.get("status") == "open"]
        closed_pos = [p for p in all_pos if p.get("status") == "closed"]

    # ── Open positions ─────────────────────────────────────────────────────────
    open_rows = ""
    if not open_pos:
        open_rows = '<tr><td colspan="8" class="empty">No open positions</td></tr>'
    else:
        for p in open_pos:
            entry_dt = p.get("entry_date", "—")[:10]
            open_rows += f"""<tr>
              <td class="sym">{p.get('symbol','—')}</td>
              <td>{p.get('strategy','—')}</td>
              <td class="num">${_fmt_float(p.get('strike'))}</td>
              <td class="num">{p.get('expiry','—')}</td>
              <td class="num">{p.get('dte_at_entry','—')} DTE</td>
              <td class="num">${_fmt_float(p.get('entry_premium'))}</td>
              <td class="num">{_fmt_float(p.get('iv_rank_at_entry'), 1)}</td>
              <td class="num">{entry_dt}</td>
            </tr>"""

    # ── Closed positions ───────────────────────────────────────────────────────
    closed_rows = ""
    if not closed_pos:
        closed_rows = '<tr><td colspan="8" class="empty">No closed positions yet — accumulating trade history</td></tr>'
    else:
        for p in sorted(closed_pos, key=lambda x: x.get("exit_date", ""), reverse=True):
            pnl    = p.get("pnl_pct")
            pnl_cl = _pnl_class(pnl)
            pnl_tx = f"{pnl:+.2f}%" if pnl is not None else "—"
            closed_rows += f"""<tr>
              <td class="sym">{p.get('symbol','—')}</td>
              <td>{p.get('strategy','—')}</td>
              <td class="num">${_fmt_float(p.get('strike'))}</td>
              <td class="num">{p.get('exit_date','—')[:10]}</td>
              <td class="num">{p.get('hold_days','—')}d</td>
              <td class="num {pnl_cl}">{pnl_tx}</td>
              <td class="num">{p.get('exit_reason','—')}</td>
              <td class="num">{p.get('alpaca_order_id','—')[:12] if p.get('alpaca_order_id') else '—'}</td>
            </tr>"""

    return f"""
<section class="card">
  <h2>Positions</h2>
  <h3>Open <span class="badge">{len(open_pos)}</span></h3>
  <table>
    <thead>
      <tr>
        <th>Symbol</th><th>Strategy</th><th>Strike</th><th>Expiry</th>
        <th>DTE</th><th>Premium</th><th>IV Rank</th><th>Entry</th>
      </tr>
    </thead>
    <tbody>{open_rows}</tbody>
  </table>

  <h3 style="margin-top:1.5rem">Closed <span class="badge">{len(closed_pos)}</span></h3>
  <table>
    <thead>
      <tr>
        <th>Symbol</th><th>Strategy</th><th>Strike</th><th>Exit Date</th>
        <th>Hold</th><th>P&amp;L</th><th>Reason</th><th>Order ID</th>
      </tr>
    </thead>
    <tbody>{closed_rows}</tbody>
  </table>
</section>"""


def _render_iv_universe(signal_quality, iv_cache):
    dist   = signal_quality.get("iv_rank_distribution", {})
    colors = {
        "<40":    "#6e7681",
        "40-55":  "#388bfd",
        "55-70":  "#3fb950",
        "70-85":  "#d29922",
        "85-100": "#f85149",
    }

    bars = ""
    order = ["<40", "40-55", "55-70", "70-85", "85-100"]
    for bucket in order:
        info  = dist.get(bucket, {})
        pct   = info.get("pct", 0)
        count = info.get("count", 0)
        color = colors.get(bucket, "#888")
        label = f"{bucket} IV rank"
        bars += f"""
        <div class="iv-row">
          <span class="iv-label">{label}</span>
          {_iv_bar(pct, color)}
          <span class="iv-count">{count}</span>
        </div>"""

    # Top 15 by IV rank from cache
    top_rows = ""
    if iv_cache:
        top = sorted(
            ((sym, v) for sym, v in iv_cache.items() if v.get("iv_rank") is not None),
            key=lambda x: x[1]["iv_rank"],
            reverse=True
        )[:15]
        for i, (sym, v) in enumerate(top, 1):
            iv_r   = v.get("iv_rank", 0)
            iv_cur = v.get("iv_current", 0)
            bar_w  = int(iv_r)
            top_rows += f"""<tr>
              <td class="num muted">{i}</td>
              <td class="sym">{sym}</td>
              <td class="num">{iv_r:.1f}</td>
              <td><div class="mini-bar" style="width:{bar_w}%"></div></td>
              <td class="num muted">{iv_cur*100:.1f}%</td>
            </tr>"""
    else:
        top_rows = '<tr><td colspan="5" class="empty">IV cache not loaded</td></tr>'

    return f"""
<section class="card two-col">
  <div>
    <h2>IV Rank Universe</h2>
    <div class="iv-chart">{bars}</div>
  </div>
  <div>
    <h2>Top 15 by IV Rank</h2>
    <table>
      <thead><tr><th>#</th><th>Symbol</th><th>IV Rank</th><th></th><th>IV%</th></tr></thead>
      <tbody>{top_rows}</tbody>
    </table>
  </div>
</section>"""


def _render_optimizer(report):
    status     = report.get("status", "No report available")
    n_closed   = report.get("n_closed_positions", 0)
    min_ins    = report.get("min_for_insights", 10)
    min_chg    = report.get("min_for_changes", 50)
    auto_opt   = report.get("auto_optimize", False)
    insights   = report.get("current_insights", [])
    applied    = report.get("all_applied_changes", [])
    cfg        = report.get("config_snapshot", {})

    progress_ins = min(n_closed / min_ins, 1.0) if min_ins else 1.0
    progress_chg = min(n_closed / min_chg, 1.0) if min_chg else 1.0

    insight_rows = ""
    if not insights:
        insight_rows = '<li class="empty">No insights yet — accumulating closed positions</li>'
    else:
        for ins in insights:
            conf  = ins.get("confidence", "")
            conf_cl = {"high": "high", "medium": "medium", "low": "low"}.get(conf, "")
            insight_rows += f"""<li>
              <span class="score {conf_cl}">{conf.upper()}</span>
              <strong>{ins.get('param','')}</strong>:
              {ins.get('current','?')} → {ins.get('suggested','?')}
              <span class="muted">({ins.get('reason','')})</span>
            </li>"""

    applied_rows = ""
    if not applied:
        applied_rows = '<tr><td colspan="5" class="empty">No changes applied yet</td></tr>'
    else:
        for ch in sorted(applied, key=lambda x: x.get("applied_at", ""), reverse=True)[:10]:
            applied_rows += f"""<tr>
              <td class="muted">{_fmt_ts(ch.get('applied_at'))}</td>
              <td><strong>{ch.get('param','')}</strong></td>
              <td class="num">{ch.get('from_value','')}</td>
              <td class="num">→ {ch.get('to_value','')}</td>
              <td class="muted">{ch.get('confidence','')}</td>
            </tr>"""

    cfg_rows = "".join(
        f'<tr><td class="muted">{k}</td><td class="num">{v}</td></tr>'
        for k, v in cfg.items()
    )

    auto_badge = (
        '<span class="score high">AUTO-APPLY ON</span>' if auto_opt
        else '<span class="score low">AUTO-APPLY OFF</span>'
    )

    return f"""
<section class="card">
  <h2>Self-Optimizing Loop {auto_badge}</h2>
  <p class="status-msg">{status}</p>

  <div class="progress-group">
    <div class="progress-item">
      <span>Insights ({n_closed}/{min_ins} closed)</span>
      <div class="progress-bar">
        <div class="progress-fill insights" style="width:{progress_ins*100:.0f}%"></div>
      </div>
    </div>
    <div class="progress-item">
      <span>Auto-apply ({n_closed}/{min_chg} closed)</span>
      <div class="progress-bar">
        <div class="progress-fill changes" style="width:{progress_chg*100:.0f}%"></div>
      </div>
    </div>
  </div>

  <div class="two-col" style="margin-top:1.5rem">
    <div>
      <h3>Current Insights</h3>
      <ul class="insight-list">{insight_rows}</ul>
    </div>
    <div>
      <h3>Config Snapshot</h3>
      <table>
        <tbody>{cfg_rows}</tbody>
      </table>
    </div>
  </div>

  <h3 style="margin-top:1.5rem">Applied Changes <span class="badge">{len(applied)}</span></h3>
  <table>
    <thead>
      <tr><th>When</th><th>Parameter</th><th>From</th><th>To</th><th>Confidence</th></tr>
    </thead>
    <tbody>{applied_rows}</tbody>
  </table>
</section>"""


def _render_picks_history(picks):
    if not picks:
        return ""
    recent = sorted(picks, key=lambda p: p.get("date", ""), reverse=True)[:20]
    rows = ""
    for p in recent:
        rows += f"""<tr>
          <td class="muted">{p.get('date','')[:10]}</td>
          <td class="sym">{p.get('symbol','')}</td>
          <td>{p.get('strategy','')}</td>
          <td class="num">{_fmt_float(p.get('rsi'), 1)}</td>
          <td class="num">{_fmt_float(p.get('iv_rank'), 1)}</td>
          <td class="num">{_fmt_float(p.get('signal_score'), 1)}</td>
          <td class="muted">{p.get('regime','')}</td>
        </tr>"""

    return f"""
<section class="card">
  <h2>Recent Picks History <span class="badge">{len(picks)}</span></h2>
  <table>
    <thead>
      <tr><th>Date</th><th>Symbol</th><th>Strategy</th><th>RSI</th>
          <th>IV Rank</th><th>Score</th><th>Regime</th></tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
</section>"""


# ── CSS ───────────────────────────────────────────────────────────────────────

CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 14px;
  background: #0d1117;
  color: #e6edf3;
  min-height: 100vh;
}

header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 1.2rem 2rem;
  background: #161b22;
  border-bottom: 1px solid #30363d;
  flex-wrap: wrap;
  gap: 0.75rem;
}
header h1 { font-size: 1.3rem; font-weight: 700; color: #e6edf3; }
.subtitle  { font-size: 0.78rem; color: #8b949e; display: block; margin-top: 2px; }
.header-right { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; }
.updated  { font-size: 0.75rem; color: #8b949e; }

.stat-pill {
  font-size: 0.75rem;
  padding: 0.25rem 0.65rem;
  border-radius: 20px;
  background: #21262d;
  border: 1px solid #30363d;
  color: #8b949e;
}
.regime-bull { background: #0f2d1c; border-color: #3fb950; color: #3fb950; }
.regime-bear { background: #2d0f0f; border-color: #f85149; color: #f85149; }
.regime-neutral { background: #1f2428; border-color: #d29922; color: #d29922; }

main { padding: 1.5rem 2rem; max-width: 1400px; margin: 0 auto; }

.card {
  background: #161b22;
  border: 1px solid #30363d;
  border-radius: 8px;
  padding: 1.25rem 1.5rem;
  margin-bottom: 1.25rem;
}
.card h2 {
  font-size: 1rem;
  font-weight: 600;
  color: #e6edf3;
  margin-bottom: 1rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.card h3 {
  font-size: 0.85rem;
  font-weight: 600;
  color: #8b949e;
  margin-bottom: 0.5rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.badge {
  font-size: 0.7rem;
  padding: 0.1rem 0.5rem;
  background: #21262d;
  border: 1px solid #30363d;
  border-radius: 20px;
  color: #8b949e;
  font-weight: 400;
}

.outcome-bar {
  display: flex;
  gap: 2rem;
  margin-bottom: 0.75rem;
  font-size: 0.82rem;
  color: #8b949e;
}
.outcome-bar strong { color: #e6edf3; }

table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
thead th {
  text-align: left;
  padding: 0.35rem 0.6rem;
  color: #8b949e;
  font-weight: 500;
  font-size: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  border-bottom: 1px solid #30363d;
}
tbody tr:hover { background: #1c2128; }
tbody td {
  padding: 0.4rem 0.6rem;
  border-bottom: 1px solid #21262d;
  color: #c9d1d9;
}

.sym   { font-weight: 600; color: #58a6ff; letter-spacing: 0.02em; }
.num   { text-align: right; font-variant-numeric: tabular-nums; }
.muted { color: #8b949e; }
.empty { text-align: center; color: #8b949e; padding: 1.5rem !important; font-style: italic; }

.score      { font-size: 0.72rem; font-weight: 700; padding: 0.15rem 0.5rem;
              border-radius: 4px; white-space: nowrap; display: inline-block; }
.score.high   { background: #0f2d1c; color: #3fb950; border: 1px solid #3fb950; }
.score.medium { background: #2b2005; color: #d29922; border: 1px solid #d29922; }
.score.low    { background: #2d0f0f; color: #f85149; border: 1px solid #f85149; }
.score.neutral{ background: #1f2428; color: #8b949e; border: 1px solid #30363d; }

.profit { color: #3fb950; font-weight: 600; }
.loss   { color: #f85149; font-weight: 600; }

.two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; }

/* IV rank bars */
.iv-chart { display: flex; flex-direction: column; gap: 0.5rem; margin-top: 0.5rem; }
.iv-row   { display: flex; align-items: center; gap: 0.5rem; }
.iv-label { font-size: 0.78rem; color: #8b949e; width: 100px; flex-shrink: 0; }
.iv-count { font-size: 0.78rem; color: #8b949e; width: 36px; text-align: right; flex-shrink: 0; }
.bar-wrap { flex: 1; background: #21262d; border-radius: 3px; height: 14px;
            position: relative; display: flex; align-items: center; overflow: hidden; }
.bar      { height: 100%; border-radius: 3px; transition: width 0.3s; }
.bar-label{ position: absolute; right: 6px; font-size: 0.7rem; color: #e6edf3;
             font-weight: 600; text-shadow: 0 0 4px #0d1117; }

/* Top symbols mini bar */
.mini-bar { height: 6px; background: #388bfd; border-radius: 3px; max-width: 100%; }

/* Optimizer progress */
.progress-group { display: flex; flex-direction: column; gap: 0.6rem; margin-top: 0.75rem; }
.progress-item  { display: flex; align-items: center; gap: 0.75rem; font-size: 0.8rem; color: #8b949e; }
.progress-item span { width: 200px; flex-shrink: 0; }
.progress-bar  { flex: 1; background: #21262d; border-radius: 4px; height: 8px; overflow: hidden; }
.progress-fill { height: 100%; border-radius: 4px; transition: width 0.4s; }
.progress-fill.insights { background: #388bfd; }
.progress-fill.changes  { background: #3fb950; }

.status-msg { font-size: 0.82rem; color: #8b949e; margin-bottom: 0.5rem; }

.insight-list { list-style: none; display: flex; flex-direction: column; gap: 0.4rem; }
.insight-list li { font-size: 0.82rem; color: #c9d1d9; display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap; }

.legend { font-size: 0.75rem; color: #8b949e; margin-top: 0.6rem; display: flex; gap: 0.5rem; align-items: center; flex-wrap: wrap; }

footer {
  text-align: center;
  padding: 1.5rem;
  font-size: 0.75rem;
  color: #484f58;
  border-top: 1px solid #21262d;
  margin-top: 1rem;
}

@media (max-width: 900px) {
  .two-col { grid-template-columns: 1fr; }
  main { padding: 1rem; }
  header { padding: 1rem; }
}
"""


# ── Main builder ──────────────────────────────────────────────────────────────

def run():
    """
    Generate dashboard.html from current data files.
    Returns a result dict for logging.
    """
    candidates   = _load("options_candidates.json", {})
    signal_qual  = _load("options_signal_quality.json", {})
    report       = _load("options_improvement_report.json", {})
    state_raw    = _load("positions_state.json", [])
    picks        = _load("options_picks_history.json", [])

    # iv_rank_cache is large (512 symbols) — load keys + iv_rank only
    iv_cache_raw = _load("iv_rank_cache.json", {})

    now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Merge generated_at from whichever file has it
    if not report.get("generated_at"):
        report["generated_at"] = signal_qual.get("generated_at", "")

    # positions_state may be a list (legacy) or dict with "positions" key
    if isinstance(state_raw, dict):
        positions = state_raw.get("positions", [])
    else:
        positions = state_raw if isinstance(state_raw, list) else []

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Options Screener Trader — Dashboard</title>
  <style>{CSS}</style>
</head>
<body>
{_render_header(report, signal_qual)}
<main>
{_render_candidates(signal_qual)}
{_render_positions(positions)}
{_render_iv_universe(signal_qual, iv_cache_raw)}
{_render_optimizer(report)}
{_render_picks_history(picks if isinstance(picks, list) else [])}
</main>
<footer>
  Generated {now_ts} · Options Screener Trader · Paper Trading Only · Not Financial Advice
</footer>
</body>
</html>"""

    OUTPUT_PATH.write_text(html, encoding="utf-8")

    open_count   = sum(1 for p in positions if p.get("status") == "open")
    closed_count = sum(1 for p in positions if p.get("status") == "closed")

    return {
        "output": str(OUTPUT_PATH),
        "candidates": len(signal_qual.get("candidates", [])),
        "open_positions": open_count,
        "closed_positions": closed_count,
    }


if __name__ == "__main__":
    result = run()
    print(f"Dashboard written: {result['output']}")
    print(f"  Candidates : {result['candidates']}")
    print(f"  Open       : {result['open_positions']}")
    print(f"  Closed     : {result['closed_positions']}")
