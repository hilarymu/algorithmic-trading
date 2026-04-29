# Copy Trading Strategy — Congressional Disclosures

## Core Thesis

Members of Congress have historically outperformed the market — they have access to
material non-public information through committee work, regulatory briefings, and
legislative preview. The STOCK Act (2012) requires disclosure within 45 days.
This system mirrors those disclosures as quickly as possible after they appear.

---

## How It Works

1. **Politician files a trade disclosure** with the SEC (required by STOCK Act)
2. **Capitol Trades publishes** the disclosure (typically within hours of SEC filing)
3. **copy_trader.ps1 detects** the new `_txId` on its next 30-minute check
4. **Mirror trade placed** on Alpaca — buy if politician bought, sell if politician sold

---

## Trade Execution

| Action | Politician did | Bot does |
|--------|---------------|---------|
| Buy | Bought any amount | Market buy $2,000 USD of stock |
| Sell | Sold any position | Market sell full Alpaca position |
| Sell (no position) | Sold stock we don't hold | Skip (log: "no position held") |

**Order type:** Market order — fills immediately at best available price

---

## Politicians Followed

Selected on a composite score (0–100) weighting:
- **Trading frequency** — more disclosures = more actionable signals
- **Disclosure speed** — faster filers give more time-sensitive entries
- **Estimated alpha** — quality of historical calls (where data available)

Primary follow: **Josh Gottheimer (D-NJ)** — most active trader in Congress
with 1,400+ lifetime disclosures and $185M+ in disclosed volume.

---

## Known Limitations

### Disclosure Lag
The STOCK Act allows up to 45 days to report. In practice, Gottheimer and other
frequent traders often disclose within days, but the trade itself is already done.
The stock has usually already moved. This strategy is best viewed as a learning
exercise and research tool — not a reliable alpha source without further analysis.

### Position Sizing
All buys are fixed at $2,000 regardless of how much the politician traded.
A senator buying $50,000 and a senator buying $1,000 both trigger the same $2,000 buy.

### No Fundamental Filter
The bot mirrors blindly — if a politician buys a stock for non-financial reasons
(e.g., political optics, index fund rebalancing), the bot follows. The dashboard
shows all activity so you can monitor and override via `pending_entries.json`.

### Paper Account Only
All trading is on Alpaca's paper account. No real money is at risk.

---

## Monitoring

Check the live dashboard at **http://localhost:8765/** for:
- Portfolio value and today's P&L
- All open positions with unrealized returns
- Each politician's recent trades with Copied/Pending status
- Recent order history
- Bot activity log (ORDER OK, SKIP, errors)

Check `copy_trader.log` for the full detailed activity log.
