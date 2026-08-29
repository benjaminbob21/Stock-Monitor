# Options Skew Map — Design, Mathematical Framework, and Architecture

Based on *"Build Your Own Skew Map — How to See What Options Traders Are Actually Paying For"* by berttrading.

---

## 1. Overview and Core Philosophy

Stock prices reflect where buyers and sellers agree on value *right now*. Implied volatility across the options surface reveals where market participants are willing to pay a premium for tail risk or upside exposure.

### The Skew Metric
$$\text{Raw Skew} = \sigma_{25\Delta,\text{put}} - \sigma_{25\Delta,\text{call}} \quad (\text{in percentage / vol points})$$

$$\text{Normalized Skew} = \frac{\sigma_{25\Delta,\text{put}} - \sigma_{25\Delta,\text{call}}}{\sigma_{\text{ATM}}}$$

- **Positive Skew ($\text{Skew} > 0$):** OTM Puts are priced at a higher IV than OTM Calls. Traders are paying up for downside protection.
- **Negative Skew ($\text{Skew} < 0$):** OTM Calls are priced at a higher IV than OTM Puts. Traders are aggressively bidding for upside calls.

### The 4 Quadrants (1-Month Return vs. Normalized Skew)

| Quadrant | 1M Price Return | 25Δ Skew | Interpretation & Action |
| :--- | :--- | :--- | :--- |
| **🎯 Contrarian Bid** | Negative ($\text{Ret} < 0$) | Negative ($\text{Calls Bid}$) | **Primary Watchlist:** Stock is pulling back, but options traders are quietly paying up for upside calls. Bullish divergence. |
| **⚠️ Chase** | Positive ($\text{Ret} \ge 0$) | Negative ($\text{Calls Bid}$) | **Exhaustion / Crowded:** Stock is surging and retail/momentum traders are aggressively chasing calls. High reversal risk. |
| **📈 Hedged Rally** | Positive ($\text{Ret} \ge 0$) | Positive ($\text{Puts Bid}$) | **Institutional Uptrend:** Uptrend remains healthy while institutions buy downside insurance. Hold trend, tighten trailing stops. |
| **🛡️ Fear** | Negative ($\text{Ret} < 0$) | Positive ($\text{Puts Bid}$) | **Capitulation / Avoid:** Both price and options market are in agreement to the downside. Avoid catching falling knives. |

---

## 2. The 7 Explicit Architectural Decisions (as advised by the guide)

1. **Delta Target:** Exact 25-delta put and 25-delta call ($\pm 0.25\Delta$), computed via standard Black-Scholes using each strike's own IV and linear interpolation between adjacent strikes.
2. **Expiration Selection Window:** 25 to 65 DTE (targeting the standard 30–45 DTE monthly expiration, preferring third Fridays with highest liquidity).
3. **Normalization Strategy:** Normalized by At-The-Money (ATM) IV ($\text{Raw Skew} / \text{ATM IV}$) to make low-beta utilities comparable with high-beta tech.
4. **Universe Strategy:** Two tiers:
   - `core` (~50 liquid mega-caps + sector benchmark ETFs)
   - `sp500` (full S&P 500 universe)
5. **Sector Benchmarking:** Sector averages computed dynamically for every snapshot to provide sector relative skew and sector agreement percentages.
6. **Temporal Principle:** *"The level is structural; the change is the signal."* Daily snapshots stored with Week-over-Week ($\Delta 7\text{d}$) deltas and quadrant migration flags.
7. **Fixed-Format Output:** Standardized verdict sentence for every ticker:
   > `"{TICKER} is {up/down} {X}% over 30d (vs SPY {Y}%). Options traders are paying {Z}% more for {calls/puts} (norm skew {S} vs {SECTOR} avg {SS}). {SECTOR} shows {A}% agreement. [{QUADRANT}]: {ACTION}"`

---

## 3. The 5 Pitfalls & Mitigations

1. **Trap #1: Strike Space vs. Delta Space** — Never compare fixed % out-of-the-money strikes (e.g. $\pm 5\%$) across different volatilities. We recover true Black-Scholes 25-delta implied volatilities.
2. **Trap #2: Ignoring Normalization** — High IV stocks have wide raw skew in absolute vol points. We normalize by ATM IV to ensure apples-to-apples sector ranking.
3. **Trap #3: Sector Skew Conflation** — Tech structurally trades at lower skew than Energy or Staples. We compute per-sector baseline skew and measure divergence against the sector mean.
4. **Trap #4: Stale or Liquidity-Distorted Quotes** — Chains with wide spreads, zero bids, ATM IV $< 2\%$ or $> 300\%$, or normalized skew $|S| > 2.0$ trigger sanity flags and warnings.
5. **Trap #5: Event Premium (Earnings)** — Earnings within the expiration cycle artificially inflate front-month IV. We flag upcoming earnings (`is_earnings_near`) and add warnings to verdicts.

---

## 4. Pipeline Architecture & Storage

- **Pure Math Core (`src/stock_monitor/skew_math.py`):** Standalone Black-Scholes, normal CDF, ATM IV finder, 25-delta interpolation, and quadrant classification.
- **Fetcher (`src/stock_monitor/skew_fetcher.py`):** Multi-threaded yfinance options chain downloader with rate-limit jitter and earnings detection.
- **Analytical Engine (`src/stock_monitor/skew_engine.py`):** Converts raw chains into structured `SkewRecord` and `SectorSummary` metrics.
- **Storage Layer (`src/stock_monitor/skew_store.py`):** DuckDB relational storage (`skew_daily`, `skew_sector_daily`) supporting idempotent snapshots and time-series queries.
- **CLI (`src/stock_monitor/cli.py`):** `stock-monitor skew run`, `stock-monitor skew report`, `stock-monitor skew export`.
- **API & Scheduler (`src/stock_monitor/api/app.py`, `scheduler.py`):** Endpoints `/skew/latest`, `/skew/sectors`, `/skew/changes`, `/skew/ticker/{ticker}`, and daily automated cron scanning.
- **Next.js Frontend (`web/components/SkewMap.tsx`, `web/app/page.tsx`):** Interactive 2D scatter map, quadrant filter cards, watchlist table, sector agreement matrix, and WoW shifts.

---

## 5. Usage Commands

```bash
# Run a skew scan for the core universe
stock-monitor skew run --universe core

# Print the formatted Skew Map console report
stock-monitor skew report

# Export snapshot to CSV
stock-monitor skew export --output data/skew_report.csv
```
