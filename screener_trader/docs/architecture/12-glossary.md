# 12. Glossary

## Trading Terms

| Term | Definition |
|------|-----------|
| **Add-down** | Buying more shares of a position as the price falls, to lower the average cost basis. Also called "averaging down." screener_trader implements this as a 3-rung ladder at -15%, -30%, -45% |
| **Bollinger Band** | A volatility envelope around a simple moving average. The lower band = MA(20) - 2*stddev(20). Price closing below the lower band is the primary screener filter |
| **Composite Score** | A weighted sum of RSI distance, BB distance, and volume ratio used to rank oversold candidates. Lower score = stronger oversold signal. Range ~0–3 (lower = better) |
| **Correction** | A market decline of ≥10% from a recent peak. screener_trader's mean-reversion strategy performs best during corrections because oversold stocks in a correction tend to recover when the broader market stabilises |
| **Cost Basis** | The weighted average entry price across all rungs of a ladder position. Used to calculate P&L at exit |
| **Fill** | An executed trade. A limit order "fills" when the market price reaches the limit price and a counterparty takes the other side |
| **Hard Stop** | A stop order permanently live in Alpaca for every open position. If price falls to the stop level, the position is sold automatically. Non-negotiable — no position ever lacks a stop |
| **HWM (High Water Mark)** | The highest closing price recorded for a position since entry. The trailing stop floor is calculated as a percentage below the HWM |
| **Ladder** | A multi-rung position sizing strategy. Each rung adds a fixed dollar amount at a lower price level, reducing average cost basis while maintaining a single stop |
| **Limit Order** | A buy or sell order that executes only at a specified price or better. Ladder buys use limit orders placed at the target rung price |
| **Market Order** | A buy order that executes immediately at the current ask price. Entry executor uses market orders for initial rung entries |
| **Mean Reversion** | The tendency of an asset's price to return to its historical average after an extreme move. The core hypothesis of screener_trader: stocks that are oversold relative to recent history tend to recover |
| **Naked Short** | A short position without a corresponding long hedge. options_executor guards against leaving a naked short if the long (hedge) leg fails to fill |
| **Oversold** | A condition where a stock's price has fallen significantly relative to recent history. Measured by RSI < threshold and price below lower Bollinger Band |
| **P&L (Profit and Loss)** | The dollar gain or loss on a closed position. Calculated as (exit_price - avg_cost_basis) * shares |
| **Paper Account** | A simulated brokerage account with fake money. Alpaca paper accounts execute orders against real market prices but with no real capital at risk |
| **Rung** | A single buy order within a ladder position. Rung 1 = initial entry; Rungs 2-3 = add-down buys at lower prices |
| **Slippage** | The difference between the expected fill price and the actual fill price. Relevant for stop-limit orders where the stop triggers but the limit may not fill in a fast-moving market |
| **Split-leg Spread** | An options strategy entered as two separate orders: a short leg (sell) and a long leg (buy hedge). The long leg must follow immediately after the short to avoid a naked short |
| **Stop-Limit Order** | A stop order that, when triggered, becomes a limit order rather than a market order. screener_trader uses stop-limit with a 0.5% gap to avoid extreme slippage |
| **Trailing Stop** | A stop order whose floor price rises with the stock price (at a fixed percentage below the HWM), but never falls. Locks in profits as a position moves up |

---

## Technical / Code Terms

| Term | Definition |
|------|-----------|
| **ADR (Architecture Decision Record)** | A document capturing a significant architecture decision: context, options considered, decision made, and rationale |
| **arc42** | A structured template for software architecture documentation with 12 standard sections plus ADRs |
| **Backoff (exponential)** | A retry strategy where wait time doubles with each attempt (2s, 4s, 8s ...) to avoid overwhelming a rate-limited API |
| **BB Distance** | How far below the lower Bollinger Band the closing price is, expressed as a fraction. Larger = more oversold |
| **Bucket** | A partition of historical picks grouped by regime + RSI tier + volume tier used by signal_analyzer.py to compute per-bucket win rates |
| **Config History** | `config_history.json` — append-only log of every parameter change made by the optimizer, with before/after values and timestamp |
| **Encoding Corruption** | UTF-8 multibyte sequences misread as Windows-1252, producing garbage characters like `â€"` for em-dash or `â"€` for box-drawing |
| **Gemini** | Google's Gemini 2.5 Flash LLM, used as the optional research layer to apply qualitative filtering on top of mechanical screener results |
| **Hardcoded Path** | An absolute file path baked into source code (e.g. `C:/Users/<username>/...`). Breaks if the project is moved or cloned. All paths should use `Path(__file__).parent` instead |
| **JSON State File** | A JSON file used as persistent storage between pipeline runs. Each file has exactly one writer process. Critical files: `positions_state.json`, `pending_entries.json`, `picks_history.json` |
| **os.replace()** | Python's atomic file-replace operation. Writes to a `.tmp` file then atomically renames it, preventing partial-write corruption |
| **Picks History** | `picks_history.json` — the growing record of every screener pick with entry indicators and forward returns. Powers the self-optimization loop |
| **Rate Limit (HTTP 429)** | Alpaca or Gemini API response indicating too many requests. Handled by sleeping and retrying |
| **Regime** | Classification of current market conditions used to adjust parameter aggressiveness. Detected from SPY/VIXY signals. Values: `bull`, `mild_correction`, `correction`, `recovery`, `geopolitical_shock`, `bear` |
| **RSI (Relative Strength Index)** | A momentum oscillator (0–100) measuring the speed of price changes. Values below 30 (or lower thresholds) indicate oversold conditions. screener_trader uses Wilder's smoothed RSI with period 14 |
| **Safe_get()** | A wrapper around API GET calls that retries on network errors with exponential backoff and returns None on 404 |
| **Signal Quality** | `signal_quality.json` — output of signal_analyzer.py; per-bucket win rates and average returns used by the optimizer |
| **Wilder Smoothing** | RSI smoothing method: seed with simple average of first N closes, then `RS_smooth = RS_prev * (N-1)/N + current_RS * 1/N`. Matches TradingView and standard RSI implementations |
| **Worker Thread** | A thread in a `ThreadPoolExecutor` that fetches data for one symbol concurrently. performance_tracker uses 8 workers; risk of hitting API rate limits |
