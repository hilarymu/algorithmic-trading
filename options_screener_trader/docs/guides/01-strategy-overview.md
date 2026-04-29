# Strategy Overview — Plain English

This document explains what the Options Screener Trader does, why it's expected to make money,
and how the strategy works from signal to exit. No prior options knowledge assumed.

---

## The one-sentence pitch

> Find stocks that are temporarily beaten down, sell put options on them at a price you'd be
> happy to buy the stock, collect the premium, and let time decay work for you.

---

## Why options, not just buying the stock?

If you spot an oversold stock and buy it, you only make money if it goes up. You also tie up
capital equal to the full share price, and you earn nothing until it recovers.

Selling a **cash-secured put (CSP)** on the same stock gives you three advantages:
1. **You get paid upfront.** The option buyer pays you premium the moment you sell.
2. **You don't need the stock to go up** — it just needs to stay above your strike price.
3. **Time is on your side.** Every day that passes erodes the option's value (theta decay),
   which is income for you as the seller.

The tradeoff: your upside is capped at the premium collected. This is fine — this strategy
is designed to generate *consistent income*, not to hit home runs.

---

## The signal stack — what makes a candidate

Three conditions must align before the system considers selling a put:

### 1. RSI oversold (mean-reversion signal)
**RSI < 25** (14-period default) indicates the stock has sold off sharply and is statistically
likely to mean-revert. Historical equity screening confirms this edge. The lower the RSI,
the stronger the signal.

### 2. High implied volatility rank (options are "expensive")
**IV Rank ≥ 40** means options are priced above their historical average — the market is
nervous and pricing in more uncertainty than usual. When you sell options, you want to sell
them when they're *expensive*, so you collect more premium and benefit more when volatility
eventually returns to normal (vol crush).

IV Rank 0 = cheapest options have been in the past year.
IV Rank 100 = most expensive options have been in the past year.

We sell when IV Rank ≥ 40. We're indifferent to buying when IV Rank ≤ 30 (buying cheap
options for breakout or hedge plays).

### 3. Volume confirmation (not just noise)
**Volume ≥ 1.2× average** confirms that the sell-off has real conviction behind it, not just
a random quiet-day drift. Without volume confirmation, an RSI signal can be a false alarm.

### Bonus: earnings proximity (signal flag)
If earnings are within 7 days, the IV rank will be artificially elevated by earnings
uncertainty premium. This doesn't disqualify the trade but gets flagged. The optimizer
learns over time whether earnings-adjacent trades perform well or poorly.

---

## Strategy hierarchy — which options play to make

| Conditions | Strategy | Logic |
|---|---|---|
| RSI < 25, vol confirmed, bull regime, IV rank ≥ 40 | **Sell Cash-Secured Put (CSP)** | Primary play — collect maximum premium, defined risk at chosen strike |
| RSI < 20, extreme oversold | **Buy Call Debit Spread** | Aggressive recovery bet — buy call, sell higher call to cap cost |
| Mild correction, IV rank ≥ 50 | **Sell Put Credit Spread** | Like CSP but with defined risk cap — better for concentrated moves |
| After CSP assignment | **Sell Covered Call (Wheel)** | Already own stock at strike; sell calls to further reduce cost basis |
| Bear regime | **No new entries** | Trending down — mean-reversion signal becomes noise; sit out |

**The CSP is the workhorse.** The others activate in specific conditions or as follow-ons.

---

## How a trade works, start to finish

### Entry
1. Signal fires: TSCO has RSI = 20.5, IV rank = 100, volume = 1.99× average.
2. Screener adds it as a candidate with signal score 61.4/90.
3. Strategy selector picks the put option: 35-day DTE, 0.30-delta strike (e.g. $195 put).
4. Executor places a limit order to sell the $195 put at the mid price (~$3.20).
5. If filled: the account collects $320 per contract. The account now needs $19,500 as
   collateral (cash secured).

### While the position is open
- The stock is monitored daily (end of day) and intraday (every 15 min).
- Theta decay works in your favour — the put loses value each day.
- Exit conditions are checked: profit target, loss limit, DTE expiry window, RSI recovery.

### Exit — whichever comes first
| Condition | Action | Typical timing |
|---|---|---|
| Premium decays to 50% of entry (buy back at $1.60) | **Profit exit** — close, keep 50% | 10–20 days |
| Premium inflates to 2× entry (put worth $6.40) | **Loss limit exit** — close, cap loss | Variable |
| 21 DTE remaining | **DTE exit** — close before gamma risk accelerates | ~14 days before expiry |
| RSI of underlying recovers above 50 | **RSI recovery exit** — stock healed, no need to hold | Variable |
| Assignment at expiry | **Wheel begins** — sell covered call on the shares received | Expiry day |

---

## The self-improvement loop

After a few weeks of trading, the system starts learning from its own results:

```
closed positions
       ↓
options_signal_analyzer.py   ← scores candidates, aggregates outcomes
       ↓
options_signal_quality.json  ← win rate, avg hold, loss rate, by-IV-rank stats
       ↓
options_optimizer.py         ← compares outcomes against current config
       ↓
insights generated           ← "40-55 IV bucket win rate 30% — raise iv_rank_min_sell"
       ↓
options_config.json updated  ← auto-apply when n ≥ 50 and auto_optimize=true
```

**Gates:**
- 10+ closed positions → insights generated (suggestions only)
- 50+ closed positions → high-confidence insights applied automatically
- All changes respect hard bounds (e.g. IV rank min never goes below 20 or above 70)

The system is essentially paper-trading its way to a tuned parameter set before
any real money is at risk.

---

## Performance targets (12-month horizon)

| Metric | Target |
|---|---|
| Win rate | ≥ 70% |
| Average premium yield per trade | ≥ 2.5% of collateral |
| Annualized yield on NAV | ≥ 20% |
| Max drawdown | ≤ 15% |

See [architecture/10-quality-requirements.md](../architecture/10-quality-requirements.md)
for the full quality scenario table.

---

## What this system does NOT do

- It does **not** predict stock direction. The RSI signal is probabilistic, not certain.
- It does **not** use leverage. Every put is fully cash-secured.
- It does **not** trade earnings. Earnings-adjacent trades are flagged, not blocked.
- It does **not** run in real money mode yet. `auto_entry.enabled` uses Alpaca paper account.
- It does **not** have access to options historical price data (OPRA agreement required for
  live account). IV history is bootstrapped with HV30 proxy until real data accumulates.
  See [ADR-008](../architecture/adr/008-hv30-proxy-iv-backfill.md).
