# Stock-Monitor

A personal, **ML-powered stock research tool**. It scans the market, scores each stock's conviction with a transparent explanation of _why_, and sends alerts — so investment research doesn't require constant manual effort.

> **Decision-support only.** This tool ranks, explains, and alerts. It never places trades — every buy/sell decision stays with the user. Not financial advice.

## What it does

- **Daily automated scan** of a broad universe (S&P 500 → wider) to surface high-conviction opportunities.
- **On-demand lookup** — search any ticker for an instant, explained conviction score.
- **Explainable ML** — a calibrated conviction score (0–100) with a per-stock breakdown of the top contributing factors (no black boxes).
- **Alerts** when a stock enters high conviction, a watchlist name moves sharply, or earnings approach.

## How it works

1. **Features** — fundamentals, valuation, technicals, sentiment, macro.
2. **Label** — forward return (e.g. beat the S&P over the next 6–12 months); history supplies labels automatically.
3. **Model** — gradient boosting (XGBoost/LightGBM) + **SHAP** for explainability; FinBERT for news sentiment.
4. **Trust** — walk-forward (time-aware) validation, out-of-sample testing, confidence calibration, paper mode before any real reliance.

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

🚧 Early development — see the roadmap. Currently bootstrapping **Phase 0** (CLI: pull a watchlist, print an explainable ML score).

### Roadmap

- **Phase 0** — CLI: watchlist → basic ML score → print.
- **Phase 1** — Feature pipeline + gradient-boosting model + SQLite + Next.js dashboard (on-demand lookups).
- **Phase 2** — Backtesting + walk-forward validation + confidence calibration.
- **Phase 3** — Full-universe daily scan + ranked opportunities + Telegram alerts + risk flags + earnings calendar.
- **Phase 4** — FinBERT news-sentiment pillar; containerize → kind → Prometheus/Grafana.
- **Phase 5** — Paid data tier, AKS/Vercel deploy, polish, advanced models.

## Setup

_TBD — populated as Phase 0 lands. Secrets/API keys go in a local `.env` (never committed)._

## Disclaimer

For personal, educational use. Not investment advice. Markets carry risk; the tool's scores are estimates, not guarantees.
