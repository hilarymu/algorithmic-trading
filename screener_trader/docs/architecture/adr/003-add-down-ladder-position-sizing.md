# ADR-003: Add-Down Ladder for Position Sizing

**Status:** Accepted  
**Date:** 2025-Q1  
**Deciders:** Project owner

---

## Context

Mean-reversion entries are made at statistically oversold levels — but "oversold" stocks can continue falling further before reverting. The position sizing approach must handle the case where the initial entry proves premature.

Options considered:

**Option A — Fixed single-entry:** Enter 100% of intended position size at rung 1 only. Stop loss protects downside. Simple; no add-down complexity.

**Option B — Add-down ladder:** Enter a fraction of intended position at rung 1; automatically buy more if price falls to defined levels (rungs 2 and 3). Lower average cost basis; larger total exposure if price keeps falling.

**Option C — Options spreads for entry:** Use puts/call spreads instead of stock. Different strategy; deferred to options_screener_trader.

---

## Decision

**Implement a 3-rung add-down ladder for every stock position:**

| Rung | Trigger (drop from entry) | Dollar Size | Order Type |
|------|--------------------------|-------------|-----------|
| 1 (initial) | At screen/execution | $1,000 | Market |
| 2 | -15% from rung 1 | $1,000 | Limit |
| 3 | -30% from rung 1 | $1,000 | Limit |

Maximum total exposure per position: **$3,000** (3 rungs x $1,000).

**Stop loss:** A single trailing stop covers the entire position. The stop floor is set at -45% from rung 1 (a deliberate gap below the rung 3 trigger at -30%, providing room for the rung 3 fill to settle before the stop fires). As price recovers and makes new highs, the trailing stop rises to lock in profits.

**Exit rule:** When RSI recovers above the exit threshold (regime-dependent, typically 50-60), the monitor closes the entire position with a market sell.

---

## Rationale

**Why add-down rather than single-entry:**

- Mean-reversion strategy has highest edge when a stock is deeply oversold. Adding at -15% and -30% captures this: each additional rung buys at a more extreme oversold level where mean reversion probability is higher
- Averaging down lowers the cost basis, so even a partial recovery to the original entry price produces a profit on the combined position
- A single large entry at rung 1 carries full risk if the stock continues to fall through -45% (stop trigger); spreading across three rungs at different levels reduces cost basis before the stop fires

**Why $1,000 per rung (equal-sized rungs):**

- Equal rungs keep the math simple for cost basis calculation
- $1,000 per rung is appropriate for the paper account scale ($10,000–$30,000 total)
- At max 10 positions x $3,000 max exposure = $30,000 total — well within a typical paper account balance

**Why -15% and -30% trigger levels:**

- -15% is a meaningful but not catastrophic decline for an S&P 500 stock (large-cap stocks regularly correct 10-20% and recover)
- -30% represents a severe decline — historically, many large-cap stocks at -30% in an S&P 500 correction recover when the correction ends
- -45% stop is below the -30% rung 3 level, providing a floor that allows rung 3 to fill before the stop triggers on further decline

**Single stop for entire position:**

- Simpler to manage: one order in Alpaca per position, not three
- Monitor can raise the trailing stop as the position appreciates without needing to track multiple stop orders

---

## Consequences

**Positive:**
- Lower average cost basis than single-entry in declining markets
- Three entry points spread timing risk across the drawdown period
- Maximum exposure per position is bounded ($3,000 hard cap)

**Negative / trade-offs:**
- Position exposure increases as price falls — adds to a losing position. In a sustained bear market, all three rungs may fill and still hit the stop (maximum loss = ~45% of $3,000 = ~$1,350 per position)
- Ladder limit orders remain open until they fill or are cancelled — requires monitor to verify rung 2/3 orders are still live each cycle
- With 10 positions at 3 rungs each, up to 30 open orders exist simultaneously (20 rung limit orders + 10 stop orders)

**Accepted risks:**
- Maximum loss per position is bounded at ~$1,350 (45% stop on $3,000 max exposure). This is accepted as appropriate for a paper account strategy
- During sharp corrections, multiple positions may hit all three rungs simultaneously, concentrating capital deployment. The `max_positions=10` hard cap limits total capital at risk
