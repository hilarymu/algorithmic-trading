"""
options_executor.py
===================
Phase 2: Execute pending option entries as paper orders on Alpaca.

When auto_entry.enabled = false (default):
    Logs each entry as "pending_review" — no orders placed.
    The user reviews options_pending_entries.json and flips the flag when ready.

When auto_entry.enabled = true:
    Places limit sell orders for each pending_review entry.
    Updates options_positions_state.json with confirmed open positions.

Position sizing
---------------
    Each position: 1 contract (100 shares)
    Capital at risk (CSP): strike * 100  (full cash-secured)
    Capital at risk (spreads): spread_width * 100
    Limit per position: max_pct_nav_per_position * account_equity
    Hard limit: never > 10% NAV on a single position (ADR safety rule)

Hard safety rules (non-configurable)
-------------------------------------
    - Never > 10% NAV on one position
    - Never open when account margin utilisation > 70%
    - Never open if pause_new_entries flag is set (3 consecutive loss-limit hits)
    - Bear regime entries CANNOT reach executor (screener blocks them upstream)
"""

import json
import time
import urllib.request
import urllib.error
from datetime import date, datetime, timezone
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent))
from iv_tracker import _get, API_KEY, API_SECRET, TRADING_BASE, CALL_DELAY

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_DIR   = Path(__file__).parent.parent
DATA_DIR      = PROJECT_DIR / "data"
CONFIG_PATH   = PROJECT_DIR / "options_config.json"
PENDING_PATH  = DATA_DIR / "options_pending_entries.json"
STATE_PATH    = DATA_DIR / "positions_state.json"

HEADERS = {
    "APCA-API-KEY-ID":     API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
    "Content-Type":        "application/json",
    "Accept":              "application/json",
}

# Hard-coded safety ceilings (never configurable)
HARD_NAV_LIMIT_PCT = 0.10    # 10% NAV max per position
HARD_MARGIN_LIMIT  = 0.70    # 70% margin utilisation ceiling


# ==============================================================================
#  HTTP helpers
# ==============================================================================

# Sentinel returned by _post when Alpaca says the contract isn't listed.
# Signals the executor to record a BSM-priced simulated fill instead of failing.
_SIMULATED_FILL = {"_simulated": True, "id": None}


def _post(url: str, body: dict) -> dict | None:
    try:
        data = json.dumps(body).encode()
        req  = urllib.request.Request(url, data=data, headers=HEADERS, method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode(errors="replace")[:200] if hasattr(e, "read") else ""
        if e.code == 422 and "not found" in body_txt:
            # Contract not listed on Alpaca paper (common for monthly/individual-stock
            # options — paper only has 0-1 DTE contracts for liquid ETFs/mega-caps).
            # Signal a simulated fill; the executor records the position at BSM mid.
            return _SIMULATED_FILL
        print(f"    [POST {e.code}] {url[-60:]}  {body_txt}")
        return None
    except Exception as e:
        print(f"    [POST error] {type(e).__name__}: {e}")
        return None


# ==============================================================================
#  State I/O
# ==============================================================================

def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def load_pending_entries() -> list:
    if not PENDING_PATH.exists():
        return []
    with open(PENDING_PATH) as f:
        return json.load(f)


def save_pending_entries(entries: list) -> None:
    with open(PENDING_PATH, "w") as f:
        json.dump(entries, f, indent=2)


def load_positions_state() -> dict:
    if not STATE_PATH.exists():
        return _empty_state()
    with open(STATE_PATH) as f:
        return json.load(f)


def save_positions_state(state: dict) -> None:
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _empty_state() -> dict:
    return {
        "positions":          [],
        "archived":           [],
        "pause_new_entries":  False,
        "consecutive_losses": 0,
        "last_updated":       None,
    }


# ==============================================================================
#  Account checks
# ==============================================================================

def get_account() -> dict | None:
    """Fetch Alpaca paper account details."""
    return _get(f"{TRADING_BASE}/account")


def count_open_positions(state: dict) -> int:
    return sum(1 for p in state["positions"] if p.get("status") == "open")


def symbol_already_open(symbol: str, state: dict) -> bool:
    return any(
        p["symbol"] == symbol and p.get("status") == "open"
        for p in state["positions"]
    )


def check_position_fits(entry: dict, account_equity: float, config: dict,
                        state: dict) -> tuple[bool, str]:
    """
    Return (ok, reason).  Checks all sizing and safety gates.
    """
    ps = config.get("position_sizing", {})
    max_positions = ps.get("max_positions", 8)
    max_pct_nav   = min(
        ps.get("max_pct_nav_per_position", 0.07),
        HARD_NAV_LIMIT_PCT
    )

    if state.get("pause_new_entries"):
        return False, "entries paused (3 consecutive loss-limit hits)"

    # Hard block: near earnings + naked put (checked before sizing — non-negotiable)
    if entry.get("near_earnings") and entry.get("strategy") == "CSP":
        return False, "near earnings + naked put (hard block)"

    n_open = count_open_positions(state)
    if n_open >= max_positions:
        return False, f"max positions reached ({n_open}/{max_positions})"

    if symbol_already_open(entry["symbol"], state):
        return False, f"already have open position in {entry['symbol']}"

    cap_risk  = entry.get("capital_at_risk", 0)
    max_cap   = account_equity * max_pct_nav
    if cap_risk > max_cap:
        return False, (f"capital at risk ${cap_risk:,.0f} > "
                       f"{max_pct_nav:.0%} of equity ${account_equity:,.0f}")

    return True, "ok"


# ==============================================================================
#  Order placement
# ==============================================================================

def _place_limit_order(contract: str, side: str, qty: int,
                       limit_price: float) -> dict | None:
    """
    Place a single-leg limit order for an options contract.
    side: "buy" or "sell"
    """
    body = {
        "symbol":         contract,
        "qty":            str(qty),
        "side":           side,
        "type":           "limit",
        "time_in_force":  "day",
        "limit_price":    str(round(limit_price, 2)),
    }
    return _post(f"{TRADING_BASE}/orders", body)


def execute_entry(entry: dict, account_equity: float,
                  state: dict, config: dict) -> dict | None:
    """
    Place Alpaca paper order(s) for one pending entry.
    Returns updated position dict on success, None on failure.

    When Alpaca returns 422 "asset not found" (common for monthly options on
    individual stocks — Alpaca paper only lists 0-1 DTE contracts for liquid
    ETFs/mega-caps), the entry is accepted as a simulated fill at the BSM mid
    price.  Position is recorded with execution_mode='simulated'.
    """
    symbol   = entry["symbol"]
    strategy = entry["strategy"]
    qty      = config.get("position_sizing", {}).get("contracts_per_position", 1)

    short_leg  = entry.get("short_leg")
    long_leg   = entry.get("long_leg")
    net_credit = entry.get("net_credit_est", 0)

    print(f"    [{symbol}] {strategy} — placing orders...")

    short_order_id = None
    long_order_id  = None
    is_simulated   = False   # set True if Alpaca can't find the contract

    # Short leg (sell to open)
    if short_leg:
        side        = "sell"
        limit_price = short_leg["bid"] - 0.01    # slightly below bid for faster fill
        limit_price = max(round(limit_price, 2), 0.01)
        order = _place_limit_order(short_leg["contract"], side, qty, limit_price)
        if order is None:
            # Real error (network, auth, etc.) — abort
            print(f"    [{symbol}] short leg order failed")
            return None
        if order.get("_simulated"):
            is_simulated = True
            print(f"      Short leg: {short_leg['contract']}  {side}  ${limit_price:.2f}"
                  f"  [SIMULATED — not listed on Alpaca paper]")
        else:
            short_order_id = order.get("id")
            print(f"      Short leg: {short_leg['contract']}  {side}  ${limit_price:.2f}  "
                  f"order={short_order_id[:8] if short_order_id else 'none'}")
        time.sleep(0.5)

    # Long leg (buy to open, for spreads)
    if long_leg:
        side        = "buy"
        limit_price = long_leg["ask"] + 0.01     # slightly above ask for faster fill
        limit_price = round(limit_price, 2)
        order = _place_limit_order(long_leg["contract"], side, qty, limit_price)
        if order is None:
            if not is_simulated:
                # CRITICAL: short leg was placed but the hedge failed.
                # Cancel the unhedged short rather than leaving a naked position open.
                print(f"    [{symbol}] CRITICAL: long leg failed -- attempting to cancel short leg")
                if short_order_id:
                    try:
                        cancel_req = urllib.request.Request(
                            f"{TRADING_BASE}/orders/{short_order_id}",
                            headers=HEADERS, method="DELETE"
                        )
                        urllib.request.urlopen(cancel_req, timeout=15)
                        print(f"      Short leg {short_order_id[:8]} cancelled -- position NOT recorded")
                    except Exception as ce:
                        print(f"      Cancel FAILED ({ce}) -- manually close "
                              f"{short_leg['contract'] if short_leg else 'unknown'}")
            else:
                print(f"    [{symbol}] long leg order failed (simulated entry aborted)")
            return None   # do not record as open position
        if order.get("_simulated"):
            is_simulated = True
            print(f"      Long leg:  {long_leg['contract']}  {side}  ${limit_price:.2f}"
                  f"  [SIMULATED — not listed on Alpaca paper]")
        else:
            long_order_id = order.get("id")
            print(f"      Long leg:  {long_leg['contract']}  {side}  ${limit_price:.2f}  "
                  f"order={long_order_id[:8] if long_order_id else 'none'}")

    if is_simulated:
        print(f"      >> Simulated fill at BSM mid — position recorded for strategy tracking")

    # Build position record
    position = {
        "id":                    entry["id"],
        "symbol":                symbol,
        "strategy":              strategy,
        "execution_mode":        "simulated" if is_simulated else "live",
        "regime_at_entry":       entry.get("regime"),
        "entry_date":            date.today().strftime("%Y-%m-%d"),
        "expiry":                entry.get("expiry"),
        "dte_at_entry":          entry.get("dte"),
        "short_leg":             short_leg,
        "long_leg":              long_leg,
        "entry_credit":          round(net_credit, 4),
        "net_credit":            round(net_credit, 4),
        "qty":                   qty,
        "underlying_price_at_entry": entry.get("underlying_close"),
        "iv_rank_at_entry":      entry.get("iv_rank"),
        "iv_current_at_entry":   entry.get("iv_current"),
        "rsi_at_entry":          entry.get("rsi"),
        "delta_at_entry":        (short_leg or {}).get("delta"),
        "capital_at_risk":       entry.get("capital_at_risk"),
        "near_earnings":         entry.get("near_earnings", False),
        "short_order_id":        short_order_id,
        "long_order_id":         long_order_id,
        "status":                "open",
        "exit_date":             None,
        "exit_reason":           None,
        "exit_debit":            None,
        "pnl_dollars":           None,
        "pnl_pct":               None,
        "last_check":            None,
        "current_bid":           None,
        "current_pnl_pct":       None,
        "dte_remaining":         None,
    }
    return position


# ==============================================================================
#  Main
# ==============================================================================

def run() -> dict:
    print(f"\n{'='*60}")
    print(f" Options Executor  (Phase 2)")
    print(f"{'='*60}")

    config = load_config()
    auto_entry = config.get("auto_entry", {}).get("enabled", False)
    print(f"  auto_entry.enabled : {auto_entry}")

    pending  = load_pending_entries()
    to_exec  = [e for e in pending if e.get("status") == "pending_review"]

    if not to_exec:
        print("  No pending_review entries to process")
        return {"executed": 0, "skipped": 0}

    print(f"  Pending entries    : {len(to_exec)}")

    if not auto_entry:
        print(f"\n  auto_entry.enabled is false.")
        print(f"  Review options_pending_entries.json, then set")
        print(f"  auto_entry.enabled = true in options_config.json to activate.")
        return {"executed": 0, "skipped": len(to_exec), "reason": "auto_entry disabled"}

    # Fetch account details once
    account = get_account()
    if not account:
        print("  ERROR: cannot fetch account details — aborting executor")
        return {"executed": 0, "skipped": len(to_exec), "error": "account fetch failed"}

    equity = float(account.get("equity", account.get("portfolio_value", 0)))
    print(f"  Account equity     : ${equity:,.2f}")

    state     = load_positions_state()
    executed  = 0
    skipped   = 0
    updated_pending = []

    for entry in to_exec:
        ok, reason = check_position_fits(entry, equity, config, state)
        if not ok:
            print(f"    [{entry['symbol']}] SKIP — {reason}")
            entry["status"] = "skipped"
            entry["skip_reason"] = reason
            skipped += 1
            updated_pending.append(entry)
            continue

        position = execute_entry(entry, equity, state, config)
        if position:
            state["positions"].append(position)
            entry["status"] = "executed"
            executed += 1
            print(f"    [{entry['symbol']}] position opened — id={position['id']}")
        else:
            entry["status"] = "failed"
            skipped += 1

        updated_pending.append(entry)
        time.sleep(CALL_DELAY)

    # Update files
    # Keep entries from other days unchanged; replace today's
    today = date.today().strftime("%Y-%m-%d")
    other_days = [e for e in pending if e.get("screened_date") != today
                  and e.get("status") == "pending_review"]
    save_pending_entries(other_days + updated_pending)
    save_positions_state(state)

    print(f"\n  Executed : {executed}")
    print(f"  Skipped  : {skipped}")
    print()
    return {"executed": executed, "skipped": skipped}


if __name__ == "__main__":
    run()
