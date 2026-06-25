# 📈 BharatStocks — Indian Stock Market Analyzer & Recommender

A self-hosted web app that analyses **NSE / BSE** stocks using **live market data**
and rule-based **technical analysis** to produce a 0–100 buy/sell **score**, a
clear **BUY / SELL / HOLD** call, and concrete **entry / stop-loss / target**
levels — for both **short-term** (swing) and **long-term** (investing) horizons.

> ⚠️ **Not investment advice.** This is an educational technical-analysis tool.
> Every number is computed from live public data — nothing is fabricated — but
> markets carry risk. Verify independently and consult a SEBI-registered advisor.

---

## What it does

| Feature | Details |
|---|---|
| 🔍 **Search any stock** | Type a symbol or company name (e.g. `RELIANCE`, `Infosys`, `HDFC Bank`). Bundled NSE universe + live Yahoo fallback so *any* listed stock is findable. |
| 🎯 **Buy/Sell score** | Each stock gets a **0–100 score**, a label (STRONG BUY → STRONG SELL), a confidence %, and a short plain-English analysis of *why*. |
| 📊 **Levels** | Suggested **entry**, **ATR-based stop-loss**, **Target 1 / Target 2** (2:1 & 3:1 risk-reward), and risk %. |
| 📈 **Interactive chart** | Candlestick + volume + 20/50/200-DMA overlays. Ranges: 1D (intraday) · 1M · 3M · 6M · 1Y · 5Y. |
| 🏆 **Top picks** | Scans Nifty 50 / Nifty 100 and ranks **Top to BUY** and **Avoid / SELL**, for short or long horizon, over 1D / 1W / 1M / 3M / 6M / 1Y. |
| 🧮 **Full technicals** | RSI, MACD, ADX, Stochastic, Bollinger, ATR, OBV + fundamentals (P/E, P/B, ROE, market cap, 52-week range, analyst target). |

---

## Data source & why this stack (benchmark-driven)

The data routing was chosen by **measuring**, not guessing (latencies on the dev machine):

| Approach | Result | Verdict |
|---|---|---|
| **Direct Yahoo v8 chart endpoint** | 32 ms single · **30 syms in ~0.58 s** (16 threads) | ✅ **primary** for prices/history/live quote |
| `yfinance.download` batch | 30 syms in ~1.2 s | ~2× slower → not used for scans |
| `yfinance.get_info()` fundamentals | ~63 ms warm (handles Yahoo crumb auth) | ✅ kept for P/E, ROE, etc. |
| Raw Yahoo `quoteSummary` / `/v7/quote` | **HTTP 401** (needs crumb) | ❌ let yfinance handle it |
| `nsepython` / `jugaad-data` (NSE direct) | **failed** (NSE blocks non-residential IPs) | ❌ too fragile for an advisor |
| screener.in | no official API (ToS + brittle scraping) | ❌ avoided |

So: a **lean concurrent Yahoo chart client** (`analysis/yahoo.py`) does the hot path,
**yfinance** supplies fundamentals only, and the bundled `data/nse_stocks.json` is
**reference metadata only** (ticker → name → sector). All prices, returns, indicators
and recommendations are fetched/computed live — nothing is assumed.

> "Yahoo is slow" is a myth: it's the *fastest free option*. The slowness people
> report is yfinance's batch overhead + first-call crumb fetch, not Yahoo itself.

**Frontend choice:** kept as a Flask single-page app with **TradingView Lightweight
Charts** rather than Streamlit/Plotly — a real SPA gives instant client-side
interactions (search, horizon/range toggles) with no server round-trip, and
Lightweight Charts renders candlesticks far lighter than Plotly.

### Measured performance
- Single stock: **~26 ms warm**, ~1.3 s cold (first fundamentals crumb fetch).
- Nifty 50 scan: **~1.4 s cold** (was ~4.5 s), Nifty 100: ~2.1 s cold / ~0.9 s warm.
- Results cached (history 15 min, fundamentals 1 h) so repeat views are instant.

---

## How the recommendation works

It is a transparent, weighted **rule-based** model (no black box):

1. Compute standard indicators from real OHLCV history (RSI/ATR/ADX use Wilder
   smoothing).
2. Convert each into a signal in the range −1 … +1 (e.g. *price vs 200-DMA*,
   *golden/death cross*, *MACD vs signal*, *ADX trend strength*, *momentum*,
   and — for long-term — *fundamentals*).
3. Blend signals with **horizon-specific weights** → normalise to **0–100**.
   - **Short-term** weights momentum, RSI/MACD/Stochastic and fast moving averages.
   - **Long-term** weights the 200-DMA trend, 50/200 cross, long momentum and fundamentals.
4. Map score → label/action, and derive **stop-loss & targets** from ATR and
   recent swing levels.

The UI shows the exact signals (supporting vs risk) behind every score, so you
can judge the reasoning yourself.

---

## Run it (Windows)

```bat
:: 1) one-time: install dependencies
setup.bat

:: 2) start the app (opens http://127.0.0.1:5000 in your browser)
run.bat
```

Or manually:

```bash
python -m pip install -r requirements.txt
python app.py
# open http://127.0.0.1:5000
```

Requires **Python 3.10+** and an internet connection (for live data).

---

## Deploy to the cloud (always-on)

The app is containerized and runs under **gunicorn**. It serves the UI + API
from a single process (the in-memory cache and `ThreadPoolExecutor` assume one
shared process), so deploy it as **1 worker with threads** — that's already the
default in the `Dockerfile`/`Procfile`.

> ⚠️ **Heads-up for cloud:** the app uses Yahoo Finance's public endpoints.
> Datacenter/cloud IPs get rate-limited more aggressively than home IPs, so you
> may see occasional gaps/slow scans on a cloud host (it degrades gracefully —
> failed symbols are skipped, never faked). If reliability matters most, prefer
> self-hosting on a home connection.

### Environment variables

| Var | Purpose | Default |
|---|---|---|
| `APP_PASSWORD` | **Set this!** Enables HTTP Basic Auth so the app isn't public. If unset, the app is open. | _(unset = open)_ |
| `APP_USERNAME` | Basic-auth username | `admin` |
| `PORT` | Port to bind | `8000` (Docker) / platform-provided |
| `WEB_CONCURRENCY` | gunicorn workers (keep at 1 for a shared cache) | `1` |
| `THREADS` | gunicorn threads per worker | `8` |

`/api/health` is intentionally left unauthenticated so platform health checks work.

### Option 1 — Render (easiest, has a free tier)

1. Push this folder to a GitHub repo.
2. Render → **New ＋ → Blueprint**, select the repo (it reads `render.yaml`).
3. In the service's **Environment**, set `APP_PASSWORD` to a strong value.
4. Deploy. Your app is at `https://<name>.onrender.com` (log in with `admin` / your password).

> Free tier = 512 MB RAM and it **sleeps when idle** (first request after idle is a slow cold start). For an always-warm box, use a paid instance or a small VPS.

### Option 2 — Docker anywhere (VPS, Fly.io, Railway, Cloud Run, etc.)

```bash
docker build -t bharatstocks .
docker run -d -p 80:8000 -e APP_PASSWORD='choose-a-strong-password' \
  --restart unless-stopped --name bharatstocks bharatstocks
# open http://<server-ip>/  (login: admin / your password)
```

- **Railway / Fly.io:** point them at the repo (they detect the `Dockerfile`); set `APP_PASSWORD` in their env settings.
- **Behind a domain/TLS:** put it behind a reverse proxy (Caddy/Nginx/Cloudflare) for HTTPS.

### Local production test (Windows)

```bash
python -m pip install -r requirements.txt
set APP_PASSWORD=test123
waitress-serve --listen=0.0.0.0:8000 app:app
```

---

## API (JSON)

| Endpoint | Purpose |
|---|---|
| `GET /api/search?q=<text>` | Symbol/name search (bundled + Yahoo fallback). |
| `GET /api/stock/<symbol>?horizon=short\|long&range=1d\|1mo\|3mo\|6mo\|1y\|5y` | Full analysis, levels, chart series, technicals & fundamentals. |
| `GET /api/top?horizon=short\|long&period=1d\|1w\|1mo\|3mo\|6mo\|1y&universe=nifty50\|nifty100\|all&sort=score\|return` | Ranked Top-Buy / Top-Sell lists. |
| `GET /api/health` | Liveness + timestamp. |

---

## Project layout

```
indian-stock-analyzer/
├── app.py                  Flask app + REST API
├── analysis/
│   ├── yahoo.py            fast concurrent Yahoo v8 chart client (prices/history/quote)
│   ├── data.py             data routing: history->yahoo, fundamentals->yfinance, search
│   ├── cache.py            thread-safe TTL cache
│   ├── indicators.py       RSI, MACD, SMA/EMA, ATR, Bollinger, Stoch, ADX, OBV
│   ├── scoring.py          weighted recommendation engine + stop-loss/targets
│   └── universe.py         bundled NSE universe + search
├── data/nse_stocks.json    ticker → name → sector (reference metadata)
├── templates/index.html    single-page UI
├── static/                 app.js, styles.css
├── requirements.txt
├── setup.bat / run.bat
└── README.md
```

---

## Notes & limits

- Yahoo Finance is unofficial and may rate-limit or briefly miss data; the app
  degrades gracefully (shows `—` / skips a symbol rather than guessing).
- Intraday (1D) data depends on what Yahoo exposes for the symbol and market hours.
- Fundamentals are skipped during the multi-stock *scan* for speed; they are
  always included on the single-stock detail page.
- Adjust the scan universe by editing `data/nse_stocks.json`.
