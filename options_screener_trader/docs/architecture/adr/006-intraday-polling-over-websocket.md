# ADR-006: Use polling loop over Alpaca websocket for intraday exit monitoring

**Date:** 2026-04-24  
**Status:** Accepted

---

## Decision

Implement intraday exit monitoring as a polling loop (every 15 minutes during market
hours) rather than a persistent Alpaca websocket connection.

---

## Context

Options exit conditions (50% profit, loss limit, RSI recovery) can be triggered intraday.
Running only at 16:30 ET risks missing favourable exit prices — e.g. the 50% profit
target is hit at 11:00 AM but the position is not closed until after-hours IV widens again.
A mechanism is needed to check exit conditions during market hours without manual
intervention.

---

## Options Considered

### Option A — Polling loop (15-minute interval)

A single long-running process launched at 09:30 ET via `run_options_monitor_intraday.bat`.
Calls `check_exits_intraday()` every 15 minutes, then sleeps.
Self-terminates at 16:00 ET.

**Pros:**
- Same error-handling patterns as the rest of the codebase (HTTP + retry)
- Single scheduled task; simple to monitor via log file
- 15-minute latency acceptable for theta-decay strategy (not HFT)
- Trivially testable — just call the function directly

**Cons:**
- Will miss a price event that occurs and reverses within the 15-minute window
- Process must survive all day without crashing (watchdog not implemented)

### Option B — Alpaca WebSocket (event-driven)

Subscribe to Alpaca's trade updates and quote stream. React instantly to fills,
position changes, option price moves.

**Pros:**
- Zero latency on fills/events
- More efficient than polling (no wasted API calls when nothing is happening)

**Cons:**
- Persistent async process — a new pattern vs the rest of the codebase (sync/REST)
- Error recovery (reconnect on drop) adds significant complexity
- Overkill for a paper-trading theta-decay strategy with daily cadence

### Option C — Additional Task Scheduler triggers (every 30 minutes)

Set 14 Task Scheduler triggers per day (09:30, 10:00, 10:30, …, 16:00).
Each runs a short-lived Python process that checks exits once and exits.

**Pros:**
- No long-running process; crash-safe by design

**Cons:**
- 14 scheduled tasks is maintenance overhead
- Startup cost (import, auth, state load) paid 14 times per day
- Windows Task Scheduler UI becomes cluttered

---

## Decision Outcome

**Chosen option: A** — polling loop. The strategy's risk profile (theta decay, 15-minute
granularity sufficient) doesn't justify websocket complexity. Pattern consistency with the
rest of the codebase matters more than sub-minute latency.

Interval is configurable via `INTRADAY_INTERVAL_MIN` in `options_main.py`.
Default: 15 minutes. Can tighten to 5 minutes without architectural change.

---

## Consequences

- ✅ Intraday exits captured within 15 minutes of threshold breach
- ✅ Single long-running process; one log file per day
- ✅ Same `safe_get` + retry pattern as all other API calls
- ✅ `--intraday` flag wires Phase 2 monitor without changing the Phase 1 daily task
- ⚠️ Price events that occur and reverse within the 15-minute window are missed
- ⚠️ Process must not crash during market hours — monitoring via log file only
- ❌ Not suitable if strategy ever requires sub-minute reaction time
