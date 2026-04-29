"""
entry_executor.py
=================
Reads pending_entries.json (written by screener.py every Monday ~06:00 UTC) and
places market buy orders for all entries where skip=False and the symbol is not
already held.

When it runs
------------
Scheduled task: every Monday 09:15 ET (15 min before market open).
The 3.25-hour gap between screener run (06:00 UTC) and execution (09:15 UTC = 13:15 UTC)
gives you time to review pending_entries.json and set skip:true on any pick.

Safety checks (applied before every order)
------------------------------------------
- pending_entries.json status must be "pending" (not "cancelled" / "executed")
- Skips any symbol already held in current Alpaca positions
- Skips any symbol already in positions_state.json (managed by monitor.py)
- Respects planned_shares (user can edit this value before executor runs)
- --dry-run flag: preview orders without placing them

Dependency chain
----------------
screener.py -> pending_entries.json -> entry_executor.py -> positions_state.json (via monitor.py)
                                                         -> Alpaca paper account

Usage
-----
    py -3 entry_executor.py              # live mode
    py -3 entry_executor.py --dry-run    # preview only, no orders placed
"""

import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

# -- Paths (relative to this file -- no hardcoded user paths) --
PROJECT_DIR  = Path(__file__).parent
CONFIG_PATH  = PROJECT_DIR / "alpaca_config.json"
PENDING_PATH = PROJECT_DIR / "pending_entries.json"
STATE_PATH   = PROJECT_DIR / "positions_state.json"

DRY_RUN = "--dry-run" in sys.argv

with open(CONFIG_PATH) as f:
    cfg = json.load(f)

API_KEY    = cfg["api_key"]
API_SECRET = cfg["api_secret"]
BASE_URL   = "https://paper-api.alpaca.markets/v2"

HEADERS = {
    "APCA-API-KEY-ID":     API_KEY,
    "APCA-API-SECRET-KEY": API_SECRET,
    "Content-Type":        "application/json",
}


# -- HTTP helpers --

def api_get(url: str) -> object:
    """GET with no retry. Raises on any HTTP or network error."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def safe_get(url: str, retries: int = 2) -> object:
    """
    GET with retry on transient network errors.

    Returns the parsed JSON on success.
    Returns None on 404.
    Retries up to ``retries`` times on timeout / network errors (exponential backoff).
    Raises on other HTTP errors after exhausting retries.
    """
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


def api_post(url: str, body: dict) -> dict:
    """POST JSON body to ``url``. Returns parsed response. Raises on HTTP errors."""
    data = json.dumps(body).encode()
    req  = urllib.request.Request(url, data=data, headers=HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


# -- Place a market buy order --

def place_market_buy(symbol: str, shares: int) -> dict:
    """
    Place a market day order to buy ``shares`` of ``symbol``.

    Returns the Alpaca order dict on success. Raises urllib.error.HTTPError on failure.
    """
    body = {
        "symbol":        symbol,
        "qty":           str(shares),
        "side":          "buy",
        "type":          "market",
        "time_in_force": "day",
    }
    return api_post(f"{BASE_URL}/orders", body)


# -- Main --

def run_executor() -> None:
    """
    Execute pending option entries from pending_entries.json.

    Flow
    ----
    1. Load pending_entries.json; abort if status != "pending"
    2. Fetch current Alpaca positions (with retry) to skip already-held symbols
    3. Load positions_state.json to skip symbols already tracked by monitor.py
    4. For each un-skipped entry: place market buy (or print dry-run preview)
    5. Update pending_entries.json status -> "executed" with order details

    Writes
    ------
    pending_entries.json -- updated with executed/skipped entries and order IDs
    """
    now      = datetime.now(timezone.utc)
    mode_tag = "[DRY RUN]" if DRY_RUN else "[LIVE]"

    print(f"\n{'='*60}")
    print(f" ENTRY EXECUTOR  {mode_tag}  [{now.strftime('%Y-%m-%d %H:%M UTC')}]")
    print(f"{'='*60}")

    # -- Load pending entries --
    try:
        with open(PENDING_PATH) as f:
            pending = json.load(f)
    except FileNotFoundError:
        print("  ERROR: pending_entries.json not found.")
        print("  Has the screener run this week? Expected at 06:00 UTC Monday.")
        return

    status = pending.get("status", "pending")
    if status != "pending":
        print(f"  Status is '{status}' -- nothing to execute.")
        return

    entries    = pending.get("entries", [])
    exec_at    = pending.get("executes_at_utc", "")
    gen_at     = pending.get("generated_utc", "")

    print(f"  Generated  : {gen_at}")
    print(f"  Executes at: {exec_at}")
    print(f"  Entries    : {len(entries)} total")

    # -- Get currently held positions (with retry) --
    try:
        alpaca_positions = safe_get(f"{BASE_URL}/positions") or []
    except Exception as e:
        print(f"  ERROR: could not fetch Alpaca positions: {e}")
        print("  Aborting to avoid duplicate entries.")
        return
    held_symbols = {p["symbol"] for p in alpaca_positions}

    # Also check positions_state for anything already tracked by monitor.py
    try:
        with open(STATE_PATH) as f:
            state = json.load(f)
        tracked_symbols = set(state.get("positions", {}).keys())
    except FileNotFoundError:
        tracked_symbols = set()

    already_held = held_symbols | tracked_symbols

    if already_held:
        print(f"  Already held: {', '.join(sorted(already_held))}")

    # -- Process each entry --
    print(f"\n  {'Symbol':<7} {'Shares':>6}  {'~USD':>7}  Action")
    print(f"  {'-'*50}")

    executed = []
    skipped  = []

    for entry in entries:
        symbol    = entry["symbol"]
        shares    = entry.get("planned_shares", 1)
        price     = entry.get("screened_price", 0)
        approx_usd = round(shares * price, 0)

        # User-flagged skip
        if entry.get("skip", False):
            print(f"  {symbol:<7} {shares:>6}  ${approx_usd:>6.0f}  SKIPPED (user)")
            skipped.append({"symbol": symbol, "reason": "user_skip"})
            continue

        # Already in a position
        if symbol in already_held:
            print(f"  {symbol:<7} {shares:>6}  ${approx_usd:>6.0f}  SKIPPED (already held)")
            skipped.append({"symbol": symbol, "reason": "already_held"})
            continue

        # Place order
        if DRY_RUN:
            print(f"  {symbol:<7} {shares:>6}  ${approx_usd:>6.0f}  DRY RUN -- would buy {shares} shares")
            executed.append({"symbol": symbol, "shares": shares, "dry_run": True})
        else:
            try:
                order    = place_market_buy(symbol, shares)
                order_id = order.get("id", "unknown")
                print(f"  {symbol:<7} {shares:>6}  ${approx_usd:>6.0f}  "
                      f"ORDER PLACED -- {order_id[:8]}...")
                executed.append({
                    "symbol":   symbol,
                    "shares":   shares,
                    "order_id": order_id,
                    "status":   order.get("status"),
                })
                entry["order_id"]     = order_id
                entry["order_status"] = order.get("status")
            except urllib.error.HTTPError as e:
                body = e.read().decode() if hasattr(e, "read") else str(e)
                print(f"  {symbol:<7} {shares:>6}  ${approx_usd:>6.0f}  "
                      f"FAILED -- HTTP {e.code}: {body}")
                skipped.append({"symbol": symbol, "reason": f"order_error_{e.code}"})

    # -- Summary --
    print(f"\n  {'='*50}")
    print(f"  Executed : {len(executed)}")
    print(f"  Skipped  : {len(skipped)}")
    if not DRY_RUN and executed:
        print("  monitor.py will auto-initialise strategy state on next run.")
    print(f"  {'='*50}\n")

    # -- Update pending_entries.json status --
    if not DRY_RUN:
        pending["status"]       = "executed"
        pending["executed_utc"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        pending["executed"]     = executed
        pending["skipped"]      = skipped
        with open(PENDING_PATH, "w") as f:
            json.dump(pending, f, indent=2)
        print("  pending_entries.json updated -> status: executed")


if __name__ == "__main__":
    run_executor()
