# Running Stock-Monitor locally (MacBook Pro 2019, Intel)

A step-by-step runbook to get the backend running on your **Intel MacBook Pro (32 GB RAM)**
and exposed to the internet with a **stable URL**, so the Vercel frontend (and your phone)
can reach it. Everything here is **$0/mo**.

> Target machine: macOS on **Intel** (x86_64). Homebrew installs to `/usr/local` on Intel
> (vs `/opt/homebrew` on Apple Silicon) — the commands below account for that. The whole
> Python stack has native x86_64 wheels, so nothing compiles from source.

## What you're setting up

```
phone / browser
      │  https
      ▼
  Vercel (Next.js)  ──server-side proxy (adds X-API-Key)──►  Tailscale Funnel URL
                                                                    │
                                                                    ▼
                                                     your Mac: uvicorn + scheduler
                                                     (single DuckDB owner, port 8137)
```

- **Backend + scheduler** run on this Mac via `scripts/run-local.sh`.
- **Tailscale Funnel** gives a permanent `https://<your-mac>.<tailnet>.ts.net` URL.
- **`API_SHARED_SECRET`** locks the public API; Vercel sends the matching key.

---

## 0. Prerequisites (one time)

**Xcode Command Line Tools** (needed by Homebrew and git):

```bash
xcode-select --install
```

**Homebrew** — if `brew` isn't installed:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
# Intel Macs install brew to /usr/local. Add it to your shell:
echo 'eval "$(/usr/local/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/usr/local/bin/brew shellenv)"
```

Verify:

```bash
brew --version        # should print a version
brew --prefix         # should print /usr/local  (Intel)
```

---

## 1. System packages

```bash
brew install python@3.12 libomp git tailscale
# optional: only if you also want the quick (random-URL) tunnel fallback
brew install cloudflared
# optional: only if you want to run the web app locally too (prod is on Vercel)
brew install node
```

- **`libomp`** is required — LightGBM won't import on macOS without the OpenMP runtime.
- **`tailscale`** gives the stable public URL (see step 6).

---

## 2. Get the code

```bash
cd ~/Downloads          # or wherever you keep projects
git clone https://github.com/benjaminbob21/Stock-Monitor.git
cd Stock-Monitor
```

(If it's already cloned, just `cd` into it and `git pull`.)

---

## 3. Python environment + dependencies

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"          # core app + lint/type/test tools
```

Sanity-check the install:

```bash
ruff check src tests && mypy src && pytest      # should all pass
```

> 32 GB RAM is plenty. If you ever want the better finance sentiment model, you can add
> `pip install -e ".[finbert]"` and set `SENTIMENT_BACKEND=finbert` — but the default
> `vader` needs nothing extra and is fine to start.

---

## 4. A trained model

The API needs a model file at `models/latest.joblib` (git-ignored, so a fresh clone won't have it).

**Option A — copy from your other Mac** (fastest): copy `models/latest.joblib`
(and `models/latest_3m.joblib` if present) into this repo's `models/` folder.

**Option B — train fresh on this machine:**

```bash
source .venv/bin/activate
stock-monitor-train
ls -la models/                    # confirm latest.joblib now exists
```

---

## 5. Configure secrets (`.env`)

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

| Var | What to put | Required? |
| --- | --- | --- |
| `SEC_USER_AGENT` | `"Stock-Monitor/0.1 (your-real-email@example.com)"` | ✅ yes (SEC policy) |
| `API_SHARED_SECRET` | a strong random string — generate with `openssl rand -hex 32` | ✅ yes (public tunnel!) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | from @BotFather / getUpdates | optional (alerts + heartbeat) |
| `FINNHUB_API_KEY` | free key from finnhub.io | optional (nicer news) |

Generate the secret:

```bash
openssl rand -hex 32
```

> ⚠️ **`API_SHARED_SECRET` is what stops strangers from reading your positions or POSTing
> trades once the API is on the public internet.** Don't leave it blank — the run script
> will warn you if you do. `.env` is git-ignored; never commit it.

---

## 6. Tailscale Funnel — the stable URL (one time)

This is the piece that failed earlier: on the Homebrew build, the **daemon must be started first**.

```bash
# 1) start the Tailscale daemon (this is what was missing before)
sudo brew services start tailscale

# 2) log in / join your tailnet (opens a browser)
sudo tailscale up
```

Then in the **Tailscale admin console** (https://login.tailscale.com/admin), one time:

1. **DNS** → enable **MagicDNS** and **HTTPS Certificates**.
2. **Access Controls** → make sure this machine may use Funnel by granting the
   `funnel` node attribute, e.g. add to the policy:
   ```json
   "nodeAttrs": [
     { "target": ["autogroup:member"], "attr": ["funnel"] }
   ]
   ```

Verify Funnel is usable:

```bash
tailscale funnel status      # should not error; may say "no funnel configured" yet
```

> Prefer a GUI? You can instead install the **Tailscale app** from the Mac App Store
> (it bundles the daemon), sign in there, then skip `brew services start tailscale`.

---

## 7. Run it

```bash
./scripts/run-local.sh
```

- Defaults to **stable** Tailscale Funnel and prints your permanent
  `https://<your-mac>.<tailnet>.ts.net` URL.
- Runs the backend **with the in-process scheduler** (daily scans + Telegram alerts).
- Keeps the Mac awake (`caffeinate`) while running.

Fallback (random URL, zero Tailscale setup) for a quick test:

```bash
TUNNEL=quick ./scripts/run-local.sh
```

Stop it with **Ctrl-C** (it shuts down cleanly and turns Funnel off).

---

## 8. Point Vercel at your Mac (one time)

In your Vercel project (**Root Directory = `web/`**), set two Environment Variables:

| Vercel env var | Value |
| --- | --- |
| `STOCK_MONITOR_API_URL` | your `https://<your-mac>.<tailnet>.ts.net` Funnel URL |
| `STOCK_MONITOR_API_KEY` | the **same** value as `API_SHARED_SECRET` in `.env` |

Redeploy once. Because the Funnel URL never changes, you only do this once.

---

## 9. Verify

```bash
# health endpoint is open (no key needed) — should return JSON with "status":"ok"
curl https://<your-mac>.<tailnet>.ts.net/health
```

Then open your **Vercel URL on your phone** — the opportunities/positions should load
(the Vercel proxy is adding the API key for you).

---

## 10. Leaving it running (important on a laptop)

- **Keep it plugged in and the lid open.** `caffeinate` stops *idle* sleep, but **closing
  the lid still sleeps** the Mac unless you're in clamshell mode (external display + power +
  keyboard/mouse). Lid open + charger is the simple path.
- macOS **System Settings → Battery → Options**: turn on *"Prevent automatic sleeping on
  power adapter when the display is off."*
- If the Mac sleeps, the scheduler pauses and the tunnel drops — your **Telegram heartbeat**
  (`HEARTBEAT_MAX_AGE_HOURS`) will alert you that scans stopped.

---

## 11. Second machine (failover) + keeping them in sync

You can run the backend on a second laptop as a **backup** and have Vercel reach whichever
one is up. **Only run one at a time** — the backend is a single DuckDB owner, so two live
schedulers would send duplicate alerts and diverge.

### Bring up the second machine

Repeat steps 0–7 on it, with two things kept identical/added:

1. **Same `API_SHARED_SECRET`** in its `.env` as the first machine — Vercel sends one
   `STOCK_MONITOR_API_KEY`, so both backends must accept it.
2. Its **own** Tailscale Funnel URL (each machine has a different `…ts.net` name).

Then add both URLs to Vercel as a **comma-separated** `STOCK_MONITOR_API_URL`:

```
https://benjamins-macbook-pro.tailfd4d8c.ts.net,https://<intel-mac>.tailfd4d8c.ts.net
```

The proxy tries them in order and uses whichever responds.

### Enable SSH between them (one time, for sync)

Easiest is Tailscale SSH — run on **both** machines:

```bash
sudo tailscale up --ssh
```

(Or enable macOS **Remote Login** in System Settings → General → Sharing.)

### Sync the data + models

DuckDB is a single file with a write-ahead log, so **stop the backend on both machines
first**, then run the sync from the machine that has the latest data:

```bash
# from the machine with the good data — push to the backup laptop:
./scripts/sync-data.sh <other-tailscale-host> push

# or pull the latest onto this machine:
./scripts/sync-data.sh <other-tailscale-host> pull
```

The script refuses to run if a backend is still holding the DB open. Do this whenever you
hand off from one laptop to the other.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `sudo tailscale up` exits 1 / "is tailscaled running?" | Start the daemon first: `sudo brew services start tailscale` (or use the Mac App Store app). |
| `tailscale funnel` says Funnel not allowed | Enable HTTPS + add the `funnel` node attribute in the admin console (step 6). |
| `import lightgbm` / OpenMP error | `brew install libomp`, then reinstall in the venv: `pip install -e ".[dev]"`. |
| `Address already in use` on 8137 | Another copy is running: `lsof -i :8137` then `kill <pid>`, or run with `PORT=8145 ./scripts/run-local.sh`. |
| API returns 401 from Vercel | `STOCK_MONITOR_API_KEY` (Vercel) must exactly equal `API_SHARED_SECRET` (`.env`). |
| No model / 500 on `/score` | Train or copy a model into `models/latest.joblib` (step 4). |
| `brew` not found after install | Add it to your shell: `eval "$(/usr/local/bin/brew shellenv)"` (Intel path). |
