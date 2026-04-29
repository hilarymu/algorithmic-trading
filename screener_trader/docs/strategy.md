# Trading Strategy — RSI Mean Reversion

## Core Thesis

S&P 500 stocks that become severely oversold (RSI < 20, below Bollinger Band,
on elevated volume) tend to snap back toward their mean. The strategy captures
this reversion move — it does not predict direction, it waits for the setup.

---

## Entry Conditions (all 4 required)

| Filter | Default | Rationale |
|--------|---------|-----------|
| RSI(14) < 20 | 20 | Severely oversold, not just weak |
| Price below lower BB(20, 2σ) | required | Statistically extended below mean |
| Volume > 2× 20-day avg | 2.0× | Confirms panic/forced selling, not drift |
| Price > 200-day MA | disabled | Removed by optimizer — below 200MA actually outperforms |

> **Note:** Thresholds are auto-tuned weekly by the optimizer based on 1,332 historical picks.
> The optimizer targets maximum 5-day forward return hit rate.

---

## Position Sizing

- **Initial buy:** $1,000 per position (configurable in `screener_config.json`)
- **Max positions:** 10 simultaneous
- **Entry timing:** Market order at 09:15 Monday (queues before open, fills at 09:30)

---

## Exit Strategy

### Primary Exit — RSI Recovery (mean reversion complete)
- Monitor checks RSI(14) every 15 minutes during market hours
- **Trigger:** RSI ≥ 50 (neutral midpoint = reversion complete)
- **Action:** Cancel stop + all ladders → market sell entire position
- Rationale: Holding past RSI 50 adds trend/momentum risk the strategy isn't designed for

### Secondary Exit — Trailing Stop (profit protection)
- **Activates:** Price ≥ entry × 1.10 (+10%)
- **Floor:** High water mark × 0.95 (5% below peak)
- **Behaviour:** Floor rises with the stock, never falls
- Prevents giving back gains if RSI exit is delayed

### Tertiary Exit — Hard Stop (loss limit)
- **Level:** Entry × 0.90 (-10%)
- **Type:** Stop-limit order, always live in Alpaca
- **Superseded by** trailing stop once +10% is reached

---

## Ladder — Averaging Down

4 limit buy orders placed below entry at position open:

| Rung | Drop from Entry | Shares Multiplier | Purpose |
|------|----------------|-------------------|---------|
| 1 | −15% | 1.5× | Mild extension, small add |
| 2 | −25% | 2.5× | Moderate dip, meaningful add |
| 3 | −35% | 3.5× | Deep oversold, large add |
| 4 | −45% | 2.0× | Extreme, reduce size (tail risk) |

**Why ladder?** Mean-reverting stocks often overshoot before bouncing. Buying more
at lower prices lowers the average cost so the eventual bounce is more profitable.
If the stock bounces immediately, the ladder orders never fill — no harm done.

---

## Regime-Aware Behaviour

The optimizer uses regime data to tune aggressiveness:

| Regime | 5d Hit Rate | Avg 5d Return | Strategy behaviour |
|--------|------------|---------------|--------------------|
| `correction` | 86% | +4.58% | Most aggressive — strategy excels here |
| `recovery` | 78% | +3.18% | Aggressive |
| `mild_correction` | 66% | +0.95% | Moderate |
| `bull` | 53% | +0.21% | Conservative — oversold in bull often = weak stock |
| `bear` | — | — | No data yet; defaults to very conservative |

Current regime (April 2026): **bull**

---

## Gemini Research Layer

Before Monday entries, Gemini 2.5 Flash reviews all oversold candidates and filters out:
- Stocks with pending binary events (earnings, FDA decisions, legal rulings)
- Fundamental deterioration vs temporary panic (earnings miss, guidance cut)
- Very high short interest (potential short squeeze distortion)

This adds a qualitative layer the mechanical screener cannot provide.

---

## Performance Summary (1,332 historical picks, April 2024–April 2026)

| Metric | Value |
|--------|-------|
| Total picks tracked | 1,332 |
| Overall 5d hit rate | ~60% |
| Overall avg 5d return | +1.04% |
| Overall avg 20d return | +2.66% |
| Best regime (correction) | 86% hit rate, +4.58% avg |
| Worst regime (bull) | 53% hit rate, +0.21% avg |
| RSI < 20 vs RSI 20–35 | Significantly outperforms |
| Below 200MA vs above | Below outperforms (+1.20% vs +0.49%) |
