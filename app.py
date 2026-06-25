"""Indian Stock Analyzer -- Flask backend.

Serves the single-page UI and a small JSON API. Every figure returned is
computed from live Yahoo Finance data; the server never fabricates prices or
recommendations. This is an educational technical-analysis tool, NOT financial
advice.
"""
from __future__ import annotations

import datetime as dt
import hmac
import os
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
from flask import Flask, Response, jsonify, render_template, request

from analysis import alerts, data, indicators, scoring, universe, yahoo

app = Flask(__name__)
# Re-read templates from disk when they change (negligible cost; avoids serving
# a stale cached index.html after edits).
app.config["TEMPLATES_AUTO_RELOAD"] = True

# --- Optional password protection (enabled only when APP_PASSWORD is set) ----
# For any non-local deployment, set APP_PASSWORD (and optionally APP_USERNAME)
# so the app isn't open to the world. Local dev with no env set stays open.
APP_USERNAME = os.environ.get("APP_USERNAME", "admin")
APP_PASSWORD = os.environ.get("APP_PASSWORD")


@app.before_request
def _require_auth():
    if not APP_PASSWORD:
        return  # auth disabled (local development)
    if request.path in ("/api/health", "/api/alerts/check"):
        return  # health checks + external cron trigger allowed unauthenticated
    auth = request.authorization
    ok = (
        auth is not None
        and auth.username == APP_USERNAME
        and hmac.compare_digest(auth.password or "", APP_PASSWORD)
    )
    if not ok:
        return Response(
            "Authentication required.", 401,
            {"WWW-Authenticate": 'Basic realm="BharatStocks"'},
        )

# Map a chart "range" selector to a Yahoo (range, interval).
# Yahoo has no native 3-day range, so "3d" fetches 5 days of 5-min bars and is
# sliced to the last 3 trading days below.
CHART_RANGES = {
    "1d": ("1d", "5m"),
    "3d": ("5d", "5m"),
    "1w": ("5d", "15m"),
    "5d": ("5d", "15m"),
    "1mo": ("1mo", "1d"),
    "3mo": ("3mo", "1d"),
    "6mo": ("6mo", "1d"),
    "1y": ("1y", "1d"),
    "2y": ("2y", "1d"),
    "5y": ("5y", "1wk"),
}

DISCLAIMER = (
    "Educational technical analysis based on live public market data. "
    "Not investment advice. Markets carry risk; verify independently and "
    "consult a SEBI-registered advisor before trading."
)


def _now_ist() -> str:
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    return dt.datetime.now(ist).strftime("%d %b %Y, %I:%M %p IST")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "as_of": _now_ist()})


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"results": []})
    seen, results = set(), []
    for row in universe.search_local(q):
        if row["symbol"] not in seen:
            seen.add(row["symbol"])
            results.append({**row, "source": "nse"})
    if len(results) < 6:  # widen with a live Yahoo lookup so ANY stock is findable
        for row in data.yahoo_search(q):
            if row["symbol"] not in seen:
                seen.add(row["symbol"])
                results.append({**row, "source": "yahoo"})
    return jsonify({"results": results[:15], "query": q})


@app.route("/api/stock/<path:symbol>")
def api_stock(symbol):
    symbol = symbol.strip().upper()
    horizon = request.args.get("horizon", "short")
    chart_range = request.args.get("range", "6mo")
    if chart_range not in CHART_RANGES:
        chart_range = "6mo"

    # Fetch 2y history, fundamentals, and sentiment concurrently.
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_hist = ex.submit(yahoo.chart_df, symbol, "2y", "1d", 15.0)
        f_fund = ex.submit(data.get_fundamentals, symbol)
        f_sent = ex.submit(data.get_news_sentiment, symbol)
        hist, chart_meta = f_hist.result()
        fundamentals = f_fund.result()
        sentiment = f_sent.result()

    if hist is None or hist.empty:
        return jsonify({
            "ok": False,
            "error": f"No market data found for '{symbol}'. Check the symbol "
                     f"(NSE tickers, e.g. TCS, INFY) or try the search box.",
        }), 404

    meta = universe.get_meta(symbol)
    live_price = chart_meta.get("regularMarketPrice")

    analysis_short = scoring.analyze(symbol, hist, fundamentals, "short", meta, sentiment, live_price)
    analysis_long = scoring.analyze(symbol, hist, fundamentals, "long", meta, sentiment, live_price)
    # Historical win-rate backtest — only on the detail page (too heavy for the scan).
    analysis_short["backtest"] = scoring.backtest(hist, fundamentals, "short")
    analysis_long["backtest"] = scoring.backtest(hist, fundamentals, "long")
    primary = analysis_long if horizon == "long" else analysis_short

    # Chart series for the requested range.
    period, interval = CHART_RANGES[chart_range]
    intraday = interval.endswith("m") or interval.endswith("h")
    if intraday:
        chart_df = data.get_history(symbol, period=period, interval=interval, ttl=120)
        if chart_range == "3d" and not chart_df.empty:
            # Keep only the last 3 trading days (Yahoo has no native 3d range).
            last_days = sorted(set(chart_df.index.normalize()))[-3:]
            chart_df = chart_df[chart_df.index.normalize().isin(last_days)]
    elif chart_range in ("5y",):
        chart_df = data.get_history(symbol, period=period, interval=interval)
    else:  # slice from the 2y daily frame we already have
        spans = {"1mo": 22, "3mo": 66, "6mo": 132, "1y": 252, "2y": len(hist)}
        chart_df = hist.tail(spans.get(chart_range, 132))

    candles = data.history_to_records(chart_df, intraday=intraday)

    # Moving-average overlays aligned to the chart candles (daily ranges only).
    overlays = {}
    if not intraday and not chart_df.empty:
        close = chart_df["Close"]
        for label, win in (("sma20", 20), ("sma50", 50), ("sma200", 200)):
            if len(chart_df) >= win or label == "sma20":
                ma = indicators.sma(close, win)
                series = [
                    {"time": pd.Timestamp(idx).strftime("%Y-%m-%d"),
                     "value": round(float(v), 2)}
                    for idx, v in ma.items() if pd.notna(v)
                ]
                if series:
                    overlays[label] = series

    snap = primary["indicators"]
    quote = data.quote_from_meta(chart_meta, hist)
    quote["market_cap"] = fundamentals.get("market_cap")
    if not quote.get("fifty_two_high"):
        quote["fifty_two_high"] = fundamentals.get("fifty_two_high")
    if not quote.get("fifty_two_low"):
        quote["fifty_two_low"] = fundamentals.get("fifty_two_low")

    return jsonify({
        "ok": True,
        "symbol": symbol,
        "name": primary["name"],
        "sector": primary["sector"],
        "as_of": _now_ist(),
        "quote": quote,
        "fundamentals": fundamentals,
        "sentiment": sentiment,
        "indicators": snap,
        "analysis": {"short": analysis_short, "long": analysis_long},
        "primary_horizon": horizon,
        "chart": {
            "range": chart_range,
            "intraday": intraday,
            "candles": candles,
            "overlays": overlays,
        },
        "disclaimer": DISCLAIMER,
    })


@app.route("/api/top")
def api_top():
    horizon = request.args.get("horizon", "short")
    period = request.args.get("period", "1w" if horizon == "short" else "1y")
    uni = request.args.get("universe", "nifty50")
    sort = request.args.get("sort", "score")
    try:
        limit = max(3, min(25, int(request.args.get("limit", 10))))
    except ValueError:
        limit = 10

    symbols = universe.scan_symbols(uni)
    # 2y daily, one batched + cached request (15s TTL). Returns (df, meta) per
    # symbol so we can use the live price for ranking rows.
    hist_meta_map = data.batch_history_meta(symbols, period="2y", interval="1d", ttl=15.0)

    hist_map = {s: v[0] for s, v in hist_meta_map.items()}
    meta_map = {}
    for s in symbols:
        m = dict(universe.get_meta(s))  # copy so we never mutate the cached universe
        if s in hist_meta_map:
            m["regularMarketPrice"] = hist_meta_map[s][1].get("regularMarketPrice")
        meta_map[s] = m

    rows = scoring.scan(hist_map, horizon, meta_by_symbol=meta_map)

    ret_key = {
        "1d": "ret_1d", "1w": "ret_1w", "1mo": "ret_1m",
        "3mo": "ret_3m", "6mo": "ret_6m", "1y": "ret_1y",
    }.get(period, "ret_1w")

    if sort == "return":
        rows_sorted = sorted(
            rows, key=lambda r: (r.get(ret_key) if r.get(ret_key) is not None else -1e9),
            reverse=True,
        )
    else:
        rows_sorted = rows  # already score-desc

    buys = [r for r in rows_sorted if r["action"] == "BUY"][:limit]
    if len(buys) < limit:  # top up with best non-buys so the list is never empty
        buys = rows_sorted[:limit]
    sells = sorted(rows, key=lambda r: r["score"])[:limit]

    return jsonify({
        "ok": True,
        "horizon": horizon,
        "period": period,
        "period_return_key": ret_key,
        "universe": uni,
        "sort": sort,
        "scanned": len(rows),
        "as_of": _now_ist(),
        "buys": buys,
        "sells": sells,
        "disclaimer": DISCLAIMER,
    })


@app.route("/api/steady")
def api_steady():
    """Steady-accumulation screener: stocks quietly trending up (pre-news)."""
    uni = request.args.get("universe", "nifty100")
    try:
        limit = max(3, min(40, int(request.args.get("limit", 15))))
    except ValueError:
        limit = 15

    symbols = universe.scan_symbols(uni)
    hist_meta_map = data.batch_history_meta(symbols, period="2y", interval="1d", ttl=900)
    hist_map = {s: v[0] for s, v in hist_meta_map.items()}
    meta_map = {}
    for s in symbols:
        m = dict(universe.get_meta(s))
        if s in hist_meta_map:
            m["regularMarketPrice"] = hist_meta_map[s][1].get("regularMarketPrice")
        meta_map[s] = m

    rows = scoring.scan_steady(hist_map, meta_by_symbol=meta_map)[:limit]
    return jsonify({
        "ok": True, "universe": uni, "count": len(rows),
        "as_of": _now_ist(), "rows": rows, "disclaimer": DISCLAIMER,
    })


@app.route("/api/config")
def api_config():
    return jsonify({
        "email_configured": alerts.email_configured(),
        "default_email": os.environ.get("ALERT_TO") or os.environ.get("SMTP_USER") or "",
    })


@app.route("/api/alerts", methods=["GET", "POST"])
def api_alerts():
    if request.method == "POST":
        body = request.get_json(silent=True) or {}
        symbol = (body.get("symbol") or "").strip().upper()
        if not symbol:
            return jsonify({"ok": False, "error": "symbol is required"}), 400
        target, stop = body.get("target"), body.get("stop")
        if target in (None, "") and stop in (None, ""):
            return jsonify({"ok": False, "error": "Set a target and/or a stop-loss."}), 400
        email = (body.get("email") or os.environ.get("ALERT_TO")
                 or os.environ.get("SMTP_USER") or "").strip()
        if not email:
            return jsonify({"ok": False, "error": "An email address is required."}), 400
        a = alerts.create_alert(symbol, target, stop, email,
                                body.get("direction", "long"),
                                body.get("name", ""), body.get("note", ""))
        return jsonify({"ok": True, "alert": a})
    return jsonify({"ok": True, "alerts": alerts.list_alerts(),
                    "email_configured": alerts.email_configured()})


@app.route("/api/alerts/<alert_id>", methods=["DELETE"])
def api_alert_delete(alert_id):
    return jsonify({"ok": alerts.delete_alert(alert_id)})


@app.route("/api/alerts/check", methods=["GET", "POST"])
def api_alerts_check():
    """Manual / external-cron trigger to check alerts now."""
    return jsonify({"ok": True, **alerts.check_alerts()})


# Start the background alert poller (no-op if disabled). Runs under gunicorn too.
if os.environ.get("ALERTS_ENABLED", "1") != "0":
    alerts.start_scheduler(interval=int(os.environ.get("ALERTS_INTERVAL", 300)))


if __name__ == "__main__":
    # Dev entrypoint. In production a WSGI server (gunicorn/waitress) imports
    # `app:app` directly, so this block is not used there.
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", 5000))
    app.run(host=host, port=port, debug=False, threaded=True)
