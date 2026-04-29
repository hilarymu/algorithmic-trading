"""
Generic Multi-Position Monitor
================================
Monitors ALL open Alpaca positions using a shared strategy ruleset.
Each position gets:
  - Hard stop at entry * (1 + hard_stop_pct)        default: -10%
  - Trailing stop once price >= entry * (1 + trail_activates_pct)  default: +10%
    -> floor = high_water_mark * (1 + trail_floor_pct)             default: -5%
    -> floor only moves up, never down
  - Ladder buy orders at configurable % drops from entry
  - RSI exit: market sell when RSI(14) recovers to rsi_exit_threshold (default: 50)
    -> signals mean reversion is complete; cancels stop + all ladder orders

State is persisted in positions_state.json.
New positions found in Alpaca but not in state are auto-initialised.
Closed positions (no longer in Alpaca) are archived in state.

Usage:
    python3 monitor.py
"""

import json
import math
import urllib.request
import urllib.error
import time
from datetime import datetime, timezone, timedelta
from copy import deepcopy
from pathlib import Path

# -- RSI constants -------------------------------------------------------------
RSI_PERIOD      = 14
RSI_BARS_NEEDED = 50   # enough bars for stable Wilder's RSI

# -- Config paths -------------------------------------------------------------
_HERE       = Path(__file__).parent
CONFIG_PATH = _HERE / "alpaca_config.json"
STATE_PATH  = _HERE / "positions_state.json"

with open(CONFIG_PATH) as f:
    cfg = json.load(f)

API_KEY    = cfg["api_key"]
API_SECRET = cfg["api_secret"]
BASE_URL   = "https://paper-api.alpaca.markets/v2"
DATA_URL   = "https://data.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID":     API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
    "Content-Type":        "application/json",
}

# -- HTTP helpers --------------------------------------------------------------
def api_get(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def api_post(url, body):
    data = json.dumps(body).encode()
    req  = urllib.request.Request(url, data=data, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def api_delete(url):
    req = urllib.request.Request(url, headers=HEADERS, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code

def safe_get(url, retries=2):
    """GET that returns None on 404, retries on network errors, raises other HTTP errors."""
    for attempt in range(retries + 1):
        try:
            return api_get(url)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt < retries:
                time.sleep(2 ** (attempt + 1))
                continue
            raise

# -- State I/O -----------------------------------------------------------------
def load_state():
    with open(STATE_PATH) as f:
        return json.load(f)

def save_state(state):
    state_copy = deepcopy(state)
    # Touch last_updated on every position
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for pos in state_copy["positions"].values():
        pos["last_updated"] = today
    with open(STATE_PATH, "w") as f:
        json.dump(state_copy, f, indent=2)

# -- Auto-initialise a new position --------------------------------------------
def init_position(symbol, alpaca_pos, defaults):
    """
    Create a strategy state entry for a position we haven't seen before.
    Ladder share counts are scaled from entry shares using the multipliers.
    """
    entry  = float(alpaca_pos["avg_entry_price"])
    shares = int(float(alpaca_pos["qty"]))

    ladder = []
    for rung in defaults["ladder"]:
        ladder_shares = max(1, round(shares * rung["shares_multiplier"]))
        ladder.append({
            "rung":     rung["rung"],
            "drop_pct": rung["drop_pct"],
            "price":    round(entry * (1 + rung["drop_pct"]), 2),
            "shares":   ladder_shares,
            "order_id": None,
            "status":   "pending",
        })

    return {
        "symbol":               symbol,
        "entry_price":          round(entry, 4),
        "entry_shares":         shares,
        "entry_date":           datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "source":               "auto",
        "high_water_mark":      round(entry, 4),
        "trailing_active":      False,
        "trailing_activates_at": round(entry * (1 + defaults["trail_activates_pct"]), 2),
        "hard_stop_pct":        defaults["hard_stop_pct"],
        "trail_activates_pct":  defaults["trail_activates_pct"],
        "trail_floor_pct":      defaults["trail_floor_pct"],
        "rsi_exit_threshold":   defaults.get("rsi_exit_threshold", 50),
        "stop_order":           None,
        "ladder":               ladder,
        "last_updated":         datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }

# -- RSI helpers --------------------------------------------------------------

def fetch_bars_for_rsi(symbol):
    """Fetch last RSI_BARS_NEEDED daily bars for RSI computation."""
    end_dt   = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=85)   # ~85 calendar days >= 60 trading days
    url = (
        f"{DATA_URL}/stocks/{symbol}/bars"
        f"?timeframe=1Day"
        f"&start={start_dt.strftime('%Y-%m-%d')}"
        f"&end={end_dt.strftime('%Y-%m-%d')}"
        f"&limit={RSI_BARS_NEEDED + 5}&feed=iex&adjustment=all"
    )
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
        bars = sorted(data.get("bars", []), key=lambda b: b["t"])
        return bars[-RSI_BARS_NEEDED:] if len(bars) >= RSI_PERIOD + 2 else []
    except Exception:
        return []


def compute_rsi(bars, period=RSI_PERIOD):
    """Wilder's smoothed RSI(14). Returns float or None."""
    if len(bars) < period + 2:
        return None
    closes = [b["c"] for b in bars]
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(d, 0.0) for d in deltas]
    losses = [abs(min(d, 0.0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    return round(100 - 100 / (1 + avg_gain / avg_loss), 2)


# -- Latest price --------------------------------------------------------------
def get_latest_price(symbol):
    data = safe_get(f"{DATA_URL}/stocks/{symbol}/trades/latest?feed=iex")
    if data and "trade" in data:
        return float(data["trade"]["p"])
    return None

# -- Place / replace stop order ------------------------------------------------
def place_stop(symbol, qty, stop_price, limit_price=None):
    """
    Place a GTC stop-limit sell order.
    limit_price defaults to stop_price * 0.995 (0.5% slippage buffer).
    Returns the new order dict or None on failure.
    """
    if limit_price is None:
        limit_price = round(stop_price * 0.995, 2)

    body = {
        "symbol":        symbol,
        "qty":           str(qty),
        "side":          "sell",
        "type":          "stop_limit",
        "stop_price":    str(round(stop_price, 2)),
        "limit_price":   str(round(limit_price, 2)),
        "time_in_force": "gtc",
    }
    try:
        return api_post(f"{BASE_URL}/orders", body)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if hasattr(e, "read") else str(e)
        print(f"    [WARN] Stop order failed for {symbol}: {e.code} {body_text}")
        return None

def cancel_order(order_id):
    return api_delete(f"{BASE_URL}/orders/{order_id}")

# -- Place a ladder limit buy --------------------------------------------------
def place_ladder_buy(symbol, shares, price):
    body = {
        "symbol":        symbol,
        "qty":           str(shares),
        "side":          "buy",
        "type":          "limit",
        "limit_price":   str(round(price, 2)),
        "time_in_force": "gtc",
    }
    try:
        return api_post(f"{BASE_URL}/orders", body)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if hasattr(e, "read") else str(e)
        print(f"    [WARN] Ladder buy failed for {symbol} @ {price}: {e.code} {body_text}")
        return None

# -- Get order status ----------------------------------------------------------
def get_order(order_id):
    return safe_get(f"{BASE_URL}/orders/{order_id}")

# -- Monitor one position ------------------------------------------------------
def monitor_position(symbol, pos_state, alpaca_pos, open_orders_by_id, defaults=None):
    """
    Run all checks for a single position.
    Returns (updated_pos_state, list_of_action_strings).
    """
    if defaults is None:
        defaults = {}

    actions = []
    state   = deepcopy(pos_state)

    entry          = state["entry_price"]
    entry_shares   = state["entry_shares"]
    current        = get_latest_price(symbol)
    if current is None:
        current = float(alpaca_pos["current_price"])

    qty            = int(float(alpaca_pos["qty"]))
    hard_stop_pct  = state["hard_stop_pct"]
    trail_pct      = state["trail_floor_pct"]
    trail_at       = state["trailing_activates_at"]

    hard_stop_price = round(entry * (1 + hard_stop_pct), 2)
    pnl             = (current - entry) * qty
    pnl_pct         = (current / entry - 1) * 100

    # -- 0. RSI exit check  --  mean reversion complete? ----------------------
    rsi_threshold = state.get(
        "rsi_exit_threshold",
        defaults.get("rsi_exit_threshold", 50),
    )
    rsi_bars    = fetch_bars_for_rsi(symbol)
    rsi_current = compute_rsi(rsi_bars) if rsi_bars else None

    if rsi_current is not None and rsi_current >= rsi_threshold:
        print(f"\n  {symbol}  RSI EXIT: RSI={rsi_current} >= {rsi_threshold}  --  mean reversion complete")
        print(f"    P&L: ${pnl:+.0f} ({pnl_pct:+.1f}%)  |  Closing {qty} shares at market")

        # Cancel hard stop
        stop_oid = (state.get("stop_order") or {}).get("order_id")
        if stop_oid:
            cancel_order(stop_oid)

        # Cancel all open ladder orders
        for rung in state.get("ladder", []):
            if rung.get("order_id") and rung.get("status") not in ("filled", "cancelled_rsi_exit"):
                cancel_order(rung["order_id"])
                rung["status"] = "cancelled_rsi_exit"

        # Place market sell for entire position
        sell_body = {
            "symbol":        symbol,
            "qty":           str(qty),
            "side":          "sell",
            "type":          "market",
            "time_in_force": "day",
        }
        try:
            sell_order = api_post(f"{BASE_URL}/orders", sell_body)
            actions.append(
                f"RSI EXIT: RSI={rsi_current:.1f} >= {rsi_threshold}  --  "
                f"MARKET SELL {qty} shares (P&L {pnl_pct:+.1f}%)"
            )
            state["rsi_exit_triggered"] = True
            state["rsi_at_exit"]        = rsi_current
        except Exception as e:
            actions.append(f"RSI EXIT SELL FAILED: {e}")

        return state, actions

    # -- 1. Update high water mark -----------------------------------------
    if current > state["high_water_mark"]:
        state["high_water_mark"] = round(current, 4)

    # -- 2. Trailing stop logic --------------------------------------------
    if current >= trail_at:
        state["trailing_active"] = True

    if state["trailing_active"]:
        new_floor = round(state["high_water_mark"] * (1 + trail_pct), 2)
        current_stop = (state["stop_order"] or {}).get("stop_price", 0)
        target_stop  = max(new_floor, current_stop)   # floor only moves up

        # Bypass detection: price is below the stop floor but position is still open.
        # This happens when a stop-limit order triggers (stop hit) but the limit price
        # was not reached because the stock gapped through it. The limit order sits in
        # Alpaca above current price and provides no downside protection until recovery.
        if current_stop > 0 and current < current_stop:
            stop_oid = (state["stop_order"] or {}).get("order_id")
            order_in_open = open_orders_by_id.get(stop_oid) if stop_oid else None
            # If Alpaca shows the order as a limit (stop already triggered) or missing
            # (filled/cancelled) while position is still open, the stop was bypassed.
            if order_in_open is None or order_in_open.get("type") == "limit":
                lim = (state["stop_order"] or {}).get("limit_price")
                lim_str = f"${lim:.2f}" if lim else "unknown"
                actions.append(
                    f"*** TRAILING STOP BYPASSED ***  "
                    f"stop ${current_stop:.2f} triggered but limit {lim_str} not filled. "
                    f"Price now ${current:.2f} -- position has NO downside protection. "
                    f"ACTION REQUIRED: close at market OR place new stop below ${current:.2f}."
                )

        if target_stop > current_stop:
            # Safety: if price is already at or below the existing stop the order
            # may be in the process of triggering. Never cancel it in that state.
            if current_stop and current < current_stop:
                actions.append(
                    f"TRAILING STOP: price ${current:.2f} < stop ${current_stop:.2f}"
                    " -- skipping update, stop may be executing"
                )
            else:
                # Cancel old stop, wait for Alpaca to free the shares, then place new one
                if state["stop_order"] and state["stop_order"].get("order_id"):
                    old_oid = state["stop_order"]["order_id"]
                    cancel_order(old_oid)
                    time.sleep(2)   # give Alpaca time to process the cancel
                    # Confirm it's gone before we try to reuse the shares
                    still_live = safe_get(f"{BASE_URL}/orders/{old_oid}")
                    if still_live and still_live.get("status") in (
                            "new", "accepted", "pending_new", "pending_cancel"):
                        # Cancel hasn't settled -- skip this update, try again next cycle
                        actions.append("TRAILING STOP: cancel pending, will retry next cycle")
                        still_live = True
                    else:
                        state["stop_order"] = None
                        still_live = False
                else:
                    still_live = False

                if not still_live:
                    new_order = place_stop(symbol, qty, target_stop)
                    if new_order:
                        state["stop_order"] = {
                            "order_id":  new_order["id"],
                            "type":      "stop_limit",
                            "stop_price": float(new_order["stop_price"]),
                            "limit_price": float(new_order["limit_price"]),
                            "qty":       qty,
                        }
                        actions.append(f"TRAILING STOP RAISED to ${target_stop:.2f} "
                                        f"(HWM ${state['high_water_mark']:.2f})")
    else:
        # Hard stop mode  --  verify it exists
        current_stop_id = (state["stop_order"] or {}).get("order_id")
        stop_exists = (current_stop_id and
                       current_stop_id in open_orders_by_id and
                       open_orders_by_id[current_stop_id]["status"] in ("new","accepted","pending_new"))

        if not stop_exists:
            new_order = place_stop(symbol, qty, hard_stop_price)
            if new_order:
                state["stop_order"] = {
                    "order_id":   new_order["id"],
                    "type":       "stop_limit",
                    "stop_price": float(new_order["stop_price"]),
                    "limit_price": float(new_order["limit_price"]),
                    "qty":        qty,
                }
                actions.append(f"HARD STOP (RE)PLACED at ${hard_stop_price:.2f}")

    # -- 3. Ladder integrity -----------------------------------------------
    for rung in state["ladder"]:
        if rung["status"] == "filled":
            continue

        oid = rung.get("order_id")

        # Check if filled
        if oid and oid in open_orders_by_id:
            order = open_orders_by_id[oid]
            if order["status"] == "filled":
                rung["status"]    = "filled"
                rung["fill_price"] = float(order.get("filled_avg_price") or rung["price"])
                actions.append(f"RUNG {rung['rung']} FILLED  --  "
                                f"{rung['shares']} shares @ ${rung['fill_price']:.2f}")
                continue

        # Check if order exists and is open
        order_open = (oid and
                      oid in open_orders_by_id and
                      open_orders_by_id[oid]["status"] in ("new","accepted","pending_new"))

        if not order_open:
            # Fetch from Alpaca in case it's filled but not in open list
            if oid:
                fetched = get_order(oid)
                if fetched and fetched["status"] == "filled":
                    rung["status"]    = "filled"
                    rung["fill_price"] = float(fetched.get("filled_avg_price") or rung["price"])
                    actions.append(f"RUNG {rung['rung']} FILLED  --  "
                                   f"{rung['shares']} shares @ ${rung['fill_price']:.2f}")
                    continue

            # Re-place missing/cancelled ladder buy
            new_order = place_ladder_buy(symbol, rung["shares"], rung["price"])
            if new_order:
                rung["order_id"] = new_order["id"]
                rung["status"]   = "open"
                actions.append(f"RUNG {rung['rung']} RE-PLACED @ ${rung['price']:.2f} "
                                f"x{rung['shares']}")
            else:
                rung["status"] = "pending"

    # -- 4. Build status line ----------------------------------------------
    stop_price = (state["stop_order"] or {}).get("stop_price", hard_stop_price)
    trail_flag = "YES" if state["trailing_active"] else "NO "

    print(f"\n  {symbol}")
    print(f"    Position : {qty} shares | Entry ${entry:.2f} | "
          f"Now ${current:.2f} | P&L ${pnl:+.0f} ({pnl_pct:+.1f}%)")
    print(f"    Stop     : ${stop_price:.2f}  Trailing: {trail_flag}  "
          f"Activates at ${trail_at:.2f}")
    for rung in state["ladder"]:
        status_str = rung["status"].upper()
        if rung["status"] == "filled":
            status_str += f" @ ${rung.get('fill_price', rung['price']):.2f}"
        print(f"    Rung {rung['rung']}   : ${rung['price']:.2f} x{rung['shares']} "
              f"-- {status_str}")
    if actions:
        for a in actions:
            print(f"    ACTION   : {a}")
    else:
        print(f"    ACTION   : None")

    return state, actions


# -- Main ----------------------------------------------------------------------
def run_monitor():
    now = datetime.now(timezone.utc)
    print(f"\n{'='*60}")
    print(f" POSITION MONITOR  [{now.strftime('%Y-%m-%d %H:%M UTC')}]")
    print(f"{'='*60}")

    # Load persisted state
    state = load_state()
    defaults = state["strategy_defaults"]

    # Fetch live data from Alpaca
    try:
        alpaca_positions = safe_get(f"{BASE_URL}/positions") or []
        open_orders      = safe_get(f"{BASE_URL}/orders?status=open&limit=100") or []
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"  ERROR: Alpaca API unreachable after retries: {e}")
        print("  Monitor aborted -- no state changes written. Will retry next cycle.")
        return
    alpaca_by_symbol   = {p["symbol"]: p for p in alpaca_positions}
    open_orders_by_id  = {o["id"]: o for o in open_orders}

    if not alpaca_positions:
        print("  No open positions.")
        return

    print(f"  {len(alpaca_positions)} open position(s): "
          f"{', '.join(alpaca_by_symbol.keys())}")

    all_actions = {}

    # -- Process each live position ----------------------------------------
    for symbol, alpaca_pos in alpaca_by_symbol.items():
        # Auto-init if we've never seen this symbol
        if symbol not in state["positions"]:
            print(f"\n  [NEW] {symbol}  --  auto-initialising strategy state")
            state["positions"][symbol] = init_position(symbol, alpaca_pos, defaults)

        updated, actions = monitor_position(
            symbol,
            state["positions"][symbol],
            alpaca_pos,
            open_orders_by_id,
            defaults,
        )
        state["positions"][symbol] = updated
        all_actions[symbol] = actions

    # -- Archive positions that are no longer open -------------------------
    closed = [sym for sym in state["positions"] if sym not in alpaca_by_symbol]
    for sym in closed:
        pos = state["positions"].pop(sym)
        if "_archive" not in state:
            state["_archive"] = {}
        pos["closed_date"] = now.strftime("%Y-%m-%d")
        state["_archive"][f"{sym}_{now.strftime('%Y%m%d')}"] = pos
        print(f"\n  [CLOSED] {sym}  --  archived from active positions")

    # -- Summary -----------------------------------------------------------
    print(f"\n{'='*60}")
    total_actions = sum(len(v) for v in all_actions.values())
    print(f"  Positions monitored : {len(alpaca_by_symbol)}")
    print(f"  Actions taken       : {total_actions}")
    print(f"{'='*60}\n")

    # Persist updated state
    save_state(state)


if __name__ == "__main__":
    run_monitor()

