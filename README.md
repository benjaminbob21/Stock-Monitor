# Stock-Monitor

A personal, **ML-powered stock research & recommendation tool**. It scans the market, **recommends when to buy and when to sell**, tracks the positions you hold (or agreed to buy), and continuously monitors news + signals to flag exits — all with a transparent explanation of _why_ — so investment research doesn't require constant manual effort.

> **You execute every trade.** The tool recommends (buy _and_ sell), explains, tracks your holdings, and alerts — but it never places trades automatically; you make and execute the final call. It is a personal tool, **not** a licensed financial advisor, and **not** advice for anyone else.

## What it does

- **Buy _and_ sell recommendations** — not just scores: actionable "consider buying / hold / consider trimming / consider selling" calls, each with reasoning.
- **Daily automated scan** of a broad universe (S&P 500 → wider) to surface high-conviction _entry_ opportunities.
- **Position & portfolio tracking** — knows what you hold (or agreed to buy) and watches those names continuously.
- **Exit monitoring** — watches news, fundamentals, and signals on your holdings to flag _when to sell or trim_, not just when to buy.
- **On-demand lookup** — search any ticker for an instant, explained conviction score.
- **Explainable ML** — a calibrated conviction score (0–100) with a per-stock breakdown of the top contributing factors (no black boxes).
- **Alerts** on entry signals, exit/sell signals, sharp moves, and approaching earnings.

## How it works

1. **Features** — fundamentals, valuation, technicals, sentiment, macro.
2. **Label** — forward return (e.g. beat the S&P over the next 6–12 months); history supplies labels automatically.
3. **Model** — gradient boosting (XGBoost/LightGBM) + **SHAP** for explainability; FinBERT for news sentiment.
4. **Trust** — walk-forward (time-aware) validation, out-of-sample testing, confidence calibration, paper mode before any real reliance.
5. **Position monitoring & exits** — held positions (and agreed buys) are re-scored continuously; a falling conviction or material negative news triggers a **sell/trim recommendation**. Both _entry_ and _exit_ are modeled — the tool tells you when to onload **and** offload.

## Tech stack

- **Backend / ML:** Python — pandas, numpy, pandas-ta, XGBoost/LightGBM, SHAP, FinBERT, scikit-learn
- **API:** FastAPI
- **Frontend:** Next.js
- **Scheduling:** APScheduler → Kubernetes `CronJob`
- **Alerts:** Telegram (+ optional email digest)
- **Storage:** SQLite/DuckDB → Postgres/TimescaleDB
- **Deploy:** localhost → kind (k8s) → optional AKS / Vercel
- **Observability:** Prometheus + Grafana

## Status

🚧 Early development. **Phases 0–2 done** (explainable engine, multi-factor pipeline, walk-forward validation + calibration + backtest). **Phase 3 essentially complete**: a daily **universe scan → ranked "buy now" list** with hard risk-flag caps, a sparse **Recommendations** view (high-confidence only), **Telegram/logging alerts**, an **APScheduler** tiered scheduler + heartbeat, and retry/backoff. **Phase 4 started**: a **Tracked positions** tab — add a stock, snapshot the model's call, and get live price-vs-entry + a hold/trim/sell signal with an expert-style read. A **Next.js dashboard** (tabs: Opportunities / Recommendations / Tracked) fronts it all. Remaining: earnings calendar + the FinBERT news pillar.

### Roadmap

- **Phase 0** — CLI: watchlist → basic ML score → print.
- **Phase 1** — Feature pipeline + gradient-boosting model + DuckDB + FastAPI + Next.js dashboard (on-demand lookups).
- **Phase 2** — Backtesting + walk-forward validation + confidence calibration.
- **Phase 3** — Full-universe daily scan + ranked opportunities + Telegram alerts + risk flags + earnings calendar.
- **Phase 4** — **Position tracking + exit/sell recommendations** (monitor holdings, news-driven sell signals) + FinBERT news-sentiment pillar; containerize → kind → Prometheus/Grafana.
- **Phase 5** — Paid data tier, AKS/Vercel deploy, polish, advanced models.

## Setup

Requires Python 3.11+ (developed on 3.12) and Node 18+ for the web app. On macOS, LightGBM needs the OpenMP runtime.

```bash
# macOS: LightGBM's OpenMP dependency
brew install libomp

python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env   # then set SEC_USER_AGENT to "YourApp/0.1 (your-email)"
```

Run the Phase 0 CLI and the quality gates:

```bash
stock-monitor --watchlist AAPL MSFT NVDA KO
ruff check src tests && mypy src && pytest
```

Train a model, serve the API, and run the dashboard (Phase 1):

```bash
stock-monitor-train --watchlist AAPL MSFT NVDA KO   # ingest → validate → store → train → MLflow
uvicorn stock_monitor.api.app:app --port 8137       # FastAPI: GET /score/{ticker}

cd web && npm install && npm run dev                # Next.js dashboard on http://localhost:3000
```

Check the model honestly (Phase 2 — trust):

```bash
stock-monitor-validate --watchlist AAPL MSFT NVDA KO   # purged walk-forward: Brier, AUC, reliability
stock-monitor-backtest --watchlist AAPL MSFT NVDA KO   # cost-aware backtest: return/CAGR vs SPY, drawdown
```

Scan the universe and (optionally) run the scheduler (Phase 3):

```bash
stock-monitor-scan                    # rank the universe → powers the dashboard's Opportunities/Recommendations
stock-monitor-scheduler               # tiered jobs: universe daily, watchlist hourly, heartbeat hourly
```

Secrets/API keys live only in the local `.env` (never committed).


## Disclaimer

Personal tool for the author's own use — it provides buy/sell recommendations **to its user**, who executes every trade manually. It is **not** a licensed financial advisor and is **not** financial advice for anyone else. Markets carry risk; scores and recommendations are estimates, not guarantees.
