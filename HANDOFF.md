# HANDOFF — resume here (build continues on Mac)

> **Purpose:** a machine-independent pickup point so the build continues seamlessly on another computer without relying on any chat history. Read this first, then the design docs linked below.
> **Written:** 2026-07-03 (from Windows) — **next machine: Mac (Apple Silicon).**

## TL;DR — what to do on the Mac

1. Clone/open this repo: `git clone https://github.com/benjaminbob21/Stock-Monitor.git` → open in VS Code.
2. The CODE30 vault is already on the Mac via **iCloud Drive** — open it too (design context lives there).
3. Start a fresh Copilot chat and tell it: *"Read HANDOFF.md, the README, and the vault's projects/stock-monitor/build-plan.md, then continue Phase 0."*
4. Create the env and install deps (full stack works natively on Apple Silicon — no compiler needed):
   ```bash
   cd Stock-Monitor
   python3 -m venv .venv && source .venv/bin/activate
   python -m pip install --upgrade pip
   python -m pip install numpy pandas scikit-learn lightgbm shap yfinance requests requests-cache \
       pydantic pydantic-settings pandera pytest ruff mypy
   ```
   (`lightgbm` + `shap` install cleanly on macOS arm64 — the Windows blocker below does not apply.)

## Where the real context lives (read these)

- **Design spec / source of truth:** vault → `projects/stock-monitor/README.md`
- **Research + full build plan + phased todo + cost analysis:** vault → `projects/stock-monitor/build-plan.md`
- **Mission context (why, guardrails, visa constraints):** vault → `context.md`
- **This repo's overview:** `README.md`

> The vault is the private iCloud CODE30 folder. This code repo stays generic (zero personal/financial data, secrets in a gitignored `.env`).

## State as of this handoff

- ✅ Repo initialized (README, `.gitignore`).
- ✅ Phase 0 **kicked off**; decided to build the engine before any infra.
- ✅ **Phase 0 built on Mac (2026-07-03).** `pyproject.toml`, `src/stock_monitor/*`, `tests/*` all in place; ruff + mypy + pytest green; CLI runs end-to-end (see below).
- ℹ️ `.venv/` is created fresh on Mac with **Python 3.12** (`brew install python@3.12`) and is gitignored. macOS LightGBM also needs `brew install libomp`.

## Why we switched machines (the blocker — for the record)

- Dev machine was **Windows on ARM64**. No prebuilt wheels exist there for `lightgbm`, `shap`, `numba`, `llvmlite` → pip tried to **compile from source**, which needs MSVC C++ Build Tools + LLVM. Painful and fragile on Windows-ARM.
- Core stack (`numpy`, `pandas`, `scikit-learn`, `yfinance`) installed fine — only the ML libs failed.
- **Deploy target is Linux** (Oracle Cloud Always Free), where all these wheels exist. So this was purely a local-dev-on-Windows-ARM issue, not a design problem.
- **Decision:** develop on **Mac (Apple Silicon)** where the whole stack installs natively; production stays Linux.

## Phase 0 — next steps (the actual work to do on Mac)

From `build-plan.md` §7. Keep the guardrails: human-in-the-loop, no auto-trading, confidence = confluence + proof + transparency.

- [x] `pyproject.toml` + deps; wire `ruff` + `mypy` + `pytest`.
- [x] `.env.example`; load config via `pydantic-settings` (secrets in gitignored `.env`).
- [x] **Provider interface** (abstract `DataProvider`) + first impls: yfinance (prices) + SEC EDGAR (fundamentals).
- [x] Pull a hardcoded watchlist → assemble a basic feature row (a few fundamentals + 12-1 momentum).
- [x] Train a quick **LightGBM** on a small labeled sample → CLI prints a conviction score + **top-3 SHAP drivers** per ticker.
- [x] **Design point-in-time correctness now:** store the "known-on" (filing) date with every fundamental — the #1 anti-look-ahead-bias rule.
- [x] Lock the **forward-return label window = 12 months** (long-term, cleaner signal).

### Run it

```bash
cd Stock-Monitor
source .venv/bin/activate            # Python 3.12 venv
cp .env.example .env                 # set SEC_USER_AGENT (name + contact email)
stock-monitor --watchlist AAPL MSFT NVDA KO
ruff check src tests && mypy src && pytest   # quality gates
```

### Next: Phase 1 (from build-plan §7)

Feature pipeline + Pandera validation at ingestion + SQLite/DuckDB store + MLflow run logging + FastAPI `/score/{ticker}` + Next.js lookup card. Calibration/walk-forward remain Phase 2 — Phase 0 conviction is intentionally uncalibrated.

## Suggested first structure (build on Mac)

```
Stock-Monitor/
  pyproject.toml
  .env.example
  src/stock_monitor/
    config.py            # pydantic-settings
    providers/
      base.py            # DataProvider ABC
      yfinance_provider.py
      edgar_provider.py
    features/builder.py   # assemble feature row (+ known-on date)
    models/scorer.py      # LightGBM train + score + SHAP top-3
    cli.py               # watchlist -> score -> print
  tests/
```
