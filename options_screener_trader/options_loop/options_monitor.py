"""
options_monitor.py
==================
Phase 2: Monitor open options positions and close when exit conditions are met.

Exit rules (checked in priority order)
---------------------------------------
1. 50% profit      : current_bid <= 50% of entry credit -> buy to close
2. Loss limit      : current_bid >= 2x entry credit     -> buy to close (cut loss)
3. 21 DTE          : days to expiry <= close_at_dte     -> buy to close (gamma risk)
4. RSI recovery    : underlying RSI crosses above 50     -> buy to close (signal gone)

Intraday checks (via --intraday flag / scheduled task)
------------------------------------------------------
    Checks rules 1 and 2 only (profit and loss limit).
    DTE and RSI recovery are checked only in the daily close run.

P&L calculation
---------------
    Short put (CSP) / short spread leg:
        pnl_pct = (entry_credit - current_bid) / entry_credit
        50% profit  : pnl_pct >= 0.50  (bid decayed to half)
        Loss limit  : pnl_pct <= -1.0  (bid doubled from entry)

    Put spread:
        cost_to_close = short_ask - long_bid  (net debit to close)
        pnl_pct = (net_credit - cost_to_close) / net_credit

    Call spread (debit spread):
        value_now = long_bid - short_ask
        pnl_pct = (value_now - net_debit) / net_debit  (positive = gain)

Consecutive-loss circuit breaker
---------------------------------
    After 3 loss-limit exits in one calendar week,
    state["pause_new_entries"] is set to True.
    Manual reset required: set pause_new_entries: false in options_positions_state.json
"""

import json
import math
import time
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from iv_tracker import _get, API_KEY, API_SECRET, TRADING_BASE, DATA_BASE, CALL_DELAY

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_DIR  = Path(__file__).parent.parent
DATA_DIR     = PROJECT_DIR / "data"
CONFIG_PATH  = PROJECT_DIR / "options_config.json"
STATE_PATH   = DATA_DIR / "positions_state.json"
PICKS_PATH   = DATA_DIR / "options_picks_history.json"

HEADERS = {
    "APCA-API-KEY-ID":     API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
    "Content-Type":        "application/json",
    "Accept":              "application/json",
}

RSI_PERIOD    = 14
RSI_BARS_DAYS = 60    # calendar-day lookback for RSI bars
BSM_RATE      = 0.05  # risk-free rate used in BSM pricing


# ==============================================================================
#  BSM helpers (for pricing simulated positions that Alpaca can't quote)
# ==============================================================================

def _norm_cdf(x: float) -> float:
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def _bsm_put_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or S <= 0:
        return max(0.0, K - S)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def _bsm_call_price(S: float, K: float, T: float, r: float, sigma: float) -> float:
    if T <= 0 or sigma <= 0 or S <= 0:
        return max(0.0, S - K)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)


def fetch_underlying_price(symbol: str) -> float | None:
    """
    Fetch the most recent trade price for a stock via Alpaca equity snapshot.
    Uses the same endpoint as iv_tracker.fetch_stock_prices() — latestTrade.p
    — which gives a real-time/intraday price rather than a delayed daily bar.
    """
    url  = f"{DATA_BASE}/v2/stocks/snapshots?symbols={symbol}&feed=iex"
    data = _get(url)
    if not data:
        return None
    snap = data.get(symbol, {})
    try:
        return float(snap["latestTrade"]["p"])
    except (KeyError, TypeError, ValueError):
        return None


def _bsm_snaps_for_simulated(position: dict) -> dict:
    """
    For a simulated position (Alpaca doesn't have the contracts), compute
    theoretical option prices via BSM and return a fake-snaps dict so that
    P&L, profit targets, and loss limits can be evaluated normally.

    Uses the entry IV (iv_current_at_entry) and the current underlying price.
    This is an approximation — IV may have changed since entry.
    """
    symbol     = position.get("symbol")
    expiry_str = position.get("expiry")
    iv         = position.get("iv_current_at_entry")

    if not (symbol and expiry_str and iv and iv > 0):
        return {}

    S = fetch_underlying_price(symbol)
    if not S:
        return {}

    expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
    T      = max((expiry - date.today()).days / 365.0, 0.0)
    r      = BSM_RATE

    result = {}
    for leg in [position.get("short_leg"), position.get("long_leg")]:
        if not leg:
            continue
        contract = leg.get("contract")
        strike   = leg.get("strike")
        opt_type = leg.get("opt_type", "P")
        if not (contract and strike):
            continue
        K = float(strike)
        if opt_type == "P":
            mid = _bsm_put_price(S, K, T, r, float(iv))
        else:
            mid = _bsm_call_price(S, K, T, r, float(iv))
        mid  = round(max(mid, 0.01), 4)
        half = mid * 0.075
        bid  = round(max(mid - half, 0.01), 2)
        ask  = round(mid + half, 2)
        result[contract] = {
            "bid": bid, "ask": ask, "mid": mid,
            "delta": None, "iv": float(iv),
            "_source": "bsm_estimated",
        }
    return result


# ==============================================================================
#  State / config I/O
# ==============================================================================

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_positions_state() -> dict:
    if not STATE_PATH.exists():
        return {"positions": [], "archived": [], "pause_new_entries": False,
                "consecutive_losses": 0, "last_updated": None}
    with open(STATE_PATH) as f:
        return json.load(f)


def save_positions_state(state: dict) -> None:
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def load_picks_history() -> list:
    if not PICKS_PATH.exists():
        return []
    with open(PICKS_PATH) as f:
        return json.load(f)


def save_picks_history(history: list) -> None:
    with open(PICKS_PATH, "w") as f:
        json.dump(history, f, indent=2)


# ==============================================================================
#  HTTP helpers
# ==============================================================================

def _post(url: str, body: dict) -> dict | None:
    try:
        data = json.dumps(body).encode()
        req  = urllib.request.Request(url, data=data, headers=HEADERS, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode(errors="replace")[:200] if hasattr(e, "read") else ""
        print(f"    [POST {e.code}] {url[-60:]}  {body_txt}")
        return None
    except Exception as e:
        print(f"    [POST error] {type(e).__name__}: {e}")
        return None


# ==============================================================================
#  Market data
# ==============================================================================

def fetch_option_snapshots(contracts: list[str]) -> dict:
    """
    Fetch current snapshots for a list of option contracts.
    Returns {contract: {bid, ask, mid, delta, iv}}.
    """
    result = {}
    BATCH  = 100
    for i in range(0, len(contracts), BATCH):
        batch = contracts[i : i + BATCH]
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
                bid     = quote.get("bp")
                ask     = quote.get("ap")
                if bid is None or ask is None:
                    continue
                bid  = float(bid)
                ask  = float(ask)
                result[sym] = {
                    "bid":   bid,
                    "ask":   ask,
                    "mid":   round((bid + ask) / 2, 4),
                    "delta": float(greeks["delta"]) if greeks.get("delta") else None,
                    "iv":    float(snap["impliedVolatility"]) if snap.get("impliedVolatility") else None,
                }
            except (TypeError, ValueError, KeyError):
                pass
        time.sleep(CALL_DELAY)
    return result


def fetch_rsi(symbol: str) -> float | None:
    """
    Fetch Wilder's RSI(14) for the underlying from Alpaca equity bars.
    Returns None if insufficient data.
    """
    today  = date.today()
    start  = (today - timedelta(days=RSI_BARS_DAYS)).strftime("%Y-%m-%d")
    url    = (f"{DATA_BASE}/v2/stocks/{symbol}/bars"
              f"?timeframe=1Day&start={start}&end={today}&feed=iex"
              f"&limit=70&adjustment=all")
    data   = _get(url)
    if not data:
        return None
    bars   = data.get("bars", [])
    if len(bars) < RSI_PERIOD + 2:
        return None
    closes  = [b["c"] for b in bars]
    gains   = [max(closes[i] - closes[i-1], 0.0) for i in range(1, len(closes))]
    losses  = [max(closes[i-1] - closes[i], 0.0) for i in range(1, len(closes))]
    ag      = sum(gains[:RSI_PERIOD]) / RSI_PERIOD
    al      = sum(losses[:RSI_PERIOD]) / RSI_PERIOD
    for i in range(RSI_PERIOD, len(gains)):
        ag  = (ag * (RSI_PERIOD - 1) + gains[i]) / RSI_PERIOD
        al  = (al * (RSI_PERIOD - 1) + losses[i]) / RSI_PERIOD
    if al < 1e-10:
        return 100.0
    return round(100.0 - 100.0 / (1.0 + ag / al), 2)


# ==============================================================================
#  P&L computation
# ==============================================================================

def compute_pnl_pct(position: dict, snaps: dict) -> float | None:
    """
    Compute current P&L percentage for an open position.
    Positive = profit, negative = loss.

    Returns None if snapshot data unavailable.
    """
    strategy     = position.get("strategy", "CSP")
    entry_credit = position.get("entry_credit", 0)
    short_leg    = position.get("short_leg") or {}
    long_leg     = position.get("long_leg")
    qty          = position.get("qty", 1)

    short_contract = short_leg.get("contract") if short_leg else None
    long_contract  = long_leg.get("contract")  if long_leg  else None

    if strategy in ("CSP",):
        if not short_contract or short_contract not in snaps:
            return None
        current_bid = snaps[short_contract]["bid"]
        if entry_credit <= 0:
            return None
        return round((entry_credit - current_bid) / entry_credit, 4)

    elif strategy in ("PUT_SPREAD", "OTM_PUT_SPREAD"):
        if not short_contract or short_contract not in snaps:
            return None
        if not long_contract or long_contract not in snaps:
            return None
        # Cost to close: buy back short (at ask), sell long (at bid)
        short_ask  = snaps[short_contract]["ask"]
        long_bid   = snaps[long_contract]["bid"]
        net_debit_to_close = short_ask - long_bid
        if entry_credit <= 0:
            return None
        return round((entry_credit - net_debit_to_close) / entry_credit, 4)

    elif strategy == "CALL_SPREAD":
        # Debit spread: we paid premium upfront
        # long_leg = bought call, short_leg = sold call
        net_debit = abs(entry_credit)    # entry_credit is negative for debit spreads
        if not long_contract or long_contract not in snaps:
            return None
        if not short_contract or short_contract not in snaps:
            return None
        current_long_bid  = snaps[long_contract]["bid"]
        current_short_ask = snaps[short_contract]["ask"]
        current_value     = current_long_bid - current_short_ask
        if net_debit <= 0:
            return None
        return round((current_value - net_debit) / net_debit, 4)

    return None


def dte_remaining(expiry_str: str) -> int:
    """Days until expiry (negative if expired)."""
    expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
    return (expiry - date.today()).days


# ==============================================================================
#  Exit condition checkers
# ==============================================================================

def should_take_profit(position: dict, snaps: dict, config: dict) -> bool:
    """50% profit target: pnl_pct >= profit_target_pct."""
    pnl = compute_pnl_pct(position, snaps)
    if pnl is None:
        return False
    target = config.get("exits", {}).get("profit_target_pct", 0.50)
    return pnl >= target


def should_cut_loss(position: dict, snaps: dict, config: dict) -> bool:
    """Loss limit: pnl_pct <= -loss_limit_multiplier + 1."""
    pnl = compute_pnl_pct(position, snaps)
    if pnl is None:
        return False
    mult   = config.get("exits", {}).get("loss_limit_multiplier", 2.0)
    # pnl_pct of -1.0 means we've lost exactly the credit received
    # loss_limit_multiplier=2 means we close when we've lost 2x premium
    limit  = -(mult - 1.0)   # e.g. -1.0 for 2x multiplier
    return pnl <= limit


def should_close_for_dte(position: dict, config: dict) -> bool:
    """Close when DTE reaches the configured threshold."""
    dte = dte_remaining(position["expiry"])
    threshold = config.get("exits", {}).get("close_at_dte", 21)
    return dte <= threshold


def should_close_for_rsi(position: dict, config: dict) -> bool:
    """
    Close when underlying RSI has recovered above the exit threshold.
    RSI recovery means mean reversion is complete — the original signal is gone.
    """
    threshold = config.get("exits", {}).get("rsi_recovery_exit", 50)
    rsi = fetch_rsi(position["symbol"])
    if rsi is None:
        return False
    return rsi >= threshold


# ==============================================================================
#  Order placement (buy-to-close)
# ==============================================================================

def _place_close_order(contract: str, side: str, qty: int,
                       limit_price: float) -> dict | None:
    body = {
        "symbol":         contract,
        "qty":            str(qty),
        "side":           side,
        "type":           "limit",
        "time_in_force":  "day",
        "limit_price":    str(round(limit_price, 2)),
    }
    return _post(f"{TRADING_BASE}/orders", body)


def close_position(position: dict, reason: str,
                   snaps: dict, config: dict) -> dict:
    """
    Place buy-to-close order(s) and return updated position dict.
    Limit price = mid + 5% buffer (to encourage fill on paper engine).

    For simulated positions (execution_mode='simulated'), no Alpaca orders are
    placed — the exit debit and P&L are calculated from BSM-estimated prices
    already injected into snaps by _run_checks().
    """
    symbol      = position["symbol"]
    strategy    = position["strategy"]
    qty         = position.get("qty", 1)
    short_leg   = position.get("short_leg") or {}
    long_leg    = position.get("long_leg")
    is_sim      = position.get("execution_mode") == "simulated"

    print(f"    [{symbol}] CLOSING ({reason})  strategy={strategy}"
          + ("  [simulated]" if is_sim else ""))

    pnl_pct     = compute_pnl_pct(position, snaps)
    close_debit = None

    # Close short leg (buy to close)
    if short_leg and short_leg.get("contract") in snaps:
        sc  = short_leg["contract"]
        mid = snaps[sc]["mid"]
        if is_sim:
            print(f"      Short leg BTC: {sc}  ${mid:.2f}  [SIMULATED — BSM estimate]")
        else:
            lp  = round(mid * 1.05, 2)    # 5% above mid
            lp  = max(lp, 0.01)
            order = _place_close_order(sc, "buy", qty, lp)
            if order:
                print(f"      Short leg BTC: {sc}  ${lp:.2f}  order={order.get('id','?')[:8]}")
        close_debit = (close_debit or 0) + mid

    # Close long leg (sell to close, for spreads)
    if long_leg and long_leg.get("contract") in snaps:
        lc  = long_leg["contract"]
        mid = snaps[lc]["mid"]
        if is_sim:
            print(f"      Long leg STC:  {lc}  ${mid:.2f}  [SIMULATED — BSM estimate]")
        else:
            lp  = round(mid * 0.95, 2)    # 5% below mid
            lp  = max(lp, 0.01)
            order = _place_close_order(lc, "sell", qty, lp)
            if order:
                print(f"      Long leg STC:  {lc}  ${lp:.2f}  order={order.get('id','?')[:8]}")
        close_debit = (close_debit or 0) - mid   # reduce debit by long proceeds

    entry_credit = position.get("entry_credit", 0)
    pnl_dollars  = None
    if close_debit is not None and entry_credit:
        pnl_dollars = round((entry_credit - close_debit) * 100 * qty, 2)

    position.update({
        "status":      "closed",
        "exit_date":   date.today().strftime("%Y-%m-%d"),
        "exit_reason": reason,
        "exit_debit":  round(close_debit, 4) if close_debit is not None else None,
        "pnl_dollars": pnl_dollars,
        "pnl_pct":     pnl_pct,
    })
    return position


# ==============================================================================
#  Picks history update
# ==============================================================================

def update_picks_history(position: dict) -> None:
    """
    Mark the matching research pick as outcome_tracked=True and fill in exit fields.
    Matches by (symbol, screened_date).
    """
    history      = load_picks_history()
    entry_date   = position.get("entry_date") or position.get("id", "")[:8]
    screened_key = position.get("id", "").split("-")[1] if "-" in position.get("id","") else ""
    if screened_key:
        screened_key = f"{screened_key[:4]}-{screened_key[4:6]}-{screened_key[6:8]}"

    updated = False
    for pick in history:
        if (pick.get("symbol") == position["symbol"] and
                pick.get("screened_date") == screened_key):
            pick["outcome_tracked"] = True
            pick["exit_date"]       = position.get("exit_date")
            pick["exit_reason"]     = position.get("exit_reason")
            pick["pnl"]             = position.get("pnl_dollars")
            pick["returns"]["pnl_pct"] = position.get("pnl_pct")
            updated = True

    if updated:
        save_picks_history(history)


# ==============================================================================
#  Consecutive-loss circuit breaker
# ==============================================================================

def _update_loss_tracker(state: dict, exit_reason: str) -> None:
    """Track loss-limit exits; pause new entries after 3 in one week."""
    if exit_reason != "loss_limit":
        state["consecutive_losses"] = 0
        return
    state["consecutive_losses"] = state.get("consecutive_losses", 0) + 1
    if state["consecutive_losses"] >= 3:
        state["pause_new_entries"] = True
        print("  *** CIRCUIT BREAKER: 3 consecutive loss-limit exits ***")
        print("  *** New entries PAUSED. Set pause_new_entries:false to resume ***")


# ==============================================================================
#  Check and close one position
# ==============================================================================

def check_position(position: dict, snaps: dict, config: dict,
                   intraday: bool = False) -> tuple[bool, str]:
    """
    Check all exit conditions for one position.
    Returns (should_close, reason) or (False, "").

    intraday=True: only checks profit/loss (no DTE or RSI check).
    """
    symbol    = position["symbol"]
    pnl_pct   = compute_pnl_pct(position, snaps)
    pnl_str   = f"{pnl_pct*100:+.1f}%" if pnl_pct is not None else "n/a"

    # Update last-check fields in-place for logging
    position["last_check"]      = datetime.now(timezone.utc).isoformat()
    position["current_pnl_pct"] = pnl_pct
    short_leg = position.get("short_leg") or {}
    sc        = short_leg.get("contract")
    if sc and sc in snaps:
        position["current_bid"] = snaps[sc]["bid"]
    dte = dte_remaining(position.get("expiry", "9999-12-31"))
    position["dte_remaining"] = dte

    print(f"    [{symbol}] {position['strategy']}  "
          f"pnl={pnl_str}  dte={dte}  strategy={position['strategy']}")

    # 1. Profit target (intraday + daily)
    if should_take_profit(position, snaps, config):
        return True, "profit_target"

    # 2. Loss limit (intraday + daily)
    if should_cut_loss(position, snaps, config):
        return True, "loss_limit"

    if intraday:
        return False, ""

    # 3. DTE (daily only)
    if should_close_for_dte(position, config):
        return True, f"dte_{dte}"

    # 4. RSI recovery (daily only — one API call per position)
    if should_close_for_rsi(position, config):
        return True, "rsi_recovery"

    return False, ""


# ==============================================================================
#  Main entry points
# ==============================================================================

def _run_checks(intraday: bool = False) -> dict:
    config = load_config()
    state  = load_positions_state()

    open_positions = [p for p in state["positions"] if p.get("status") == "open"]
    if not open_positions:
        print("  No open positions to monitor")
        return {"checked": 0, "closed": 0}

    print(f"  Checking {len(open_positions)} open position(s)...")

    # Collect all contracts we need snapshots for
    contracts = []
    for pos in open_positions:
        sl = pos.get("short_leg") or {}
        ll = pos.get("long_leg")
        if sl.get("contract"):
            contracts.append(sl["contract"])
        if ll and ll.get("contract"):
            contracts.append(ll["contract"])

    snaps = fetch_option_snapshots(contracts)

    # Supplement with BSM estimates for simulated positions whose contracts
    # Alpaca doesn't list (monthly options on individual stocks).
    for pos in open_positions:
        if pos.get("execution_mode") == "simulated":
            bsm = _bsm_snaps_for_simulated(pos)
            for contract, data in bsm.items():
                if contract not in snaps:    # don't overwrite live data if present
                    snaps[contract] = data

    closed_count = 0
    for pos in open_positions:
        should_close, reason = check_position(pos, snaps, config, intraday=intraday)
        if should_close:
            close_position(pos, reason, snaps, config)
            _update_loss_tracker(state, reason)
            update_picks_history(pos)
            closed_count += 1
            print(f"    [{pos['symbol']}] closed: {reason}  "
                  f"pnl={pos.get('pnl_pct', 0)*100:+.1f}%  "
                  f"${pos.get('pnl_dollars', 0):+.2f}")

    # Move closed positions to archive
    state["positions"] = [p for p in state["positions"] if p.get("status") == "open"]
    newly_closed = [p for p in open_positions if p.get("status") == "closed"]
    state["archived"].extend(newly_closed)

    save_positions_state(state)

    print(f"\n  Positions checked: {len(open_positions)}")
    print(f"  Closed this run  : {closed_count}")
    return {"checked": len(open_positions), "closed": closed_count}


def check_exits_intraday() -> dict:
    """
    Intraday check: profit target and loss limit only.
    Called by the --intraday polling loop (options_main.py).
    """
    return _run_checks(intraday=True)


def run() -> dict:
    """
    Daily close check: all exit conditions (profit, loss, DTE, RSI).
    Called by options_main.py at end-of-day (before market close).
    """
    print(f"\n{'='*60}")
    print(f" Options Monitor  (daily close check)")
    print(f"{'='*60}")
    result = _run_checks(intraday=False)
    print()
    return result


if __name__ == "__main__":
    run()
