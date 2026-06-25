"""Lean, fast Yahoo Finance client.

Benchmarks (this machine) showed the public v8 chart endpoint is the fastest
free source for Indian equities: ~32 ms for a single 2y history, and a
concurrent batch of 30 symbols in ~580 ms (≈2x faster than yfinance's batch
download). This module talks to that endpoint directly with a pooled session,
parses to pandas, and applies split/dividend adjustment so the output matches
yfinance's `auto_adjust=True`.

Live last price, previous close and 52-week range come back in the same chart
response (`meta`) — no extra request needed. Fundamentals (P/E, ROE, …) still
go through yfinance, which transparently handles Yahoo's crumb/cookie auth that
the raw quoteSummary endpoint now requires.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from requests.adapters import HTTPAdapter

from .cache import cache_get, cache_set

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json",
})
_SESSION.mount("https://", HTTPAdapter(pool_connections=24, pool_maxsize=24, max_retries=0))

# query1/query2 are mirrors; alternating helps dodge transient rate limits.
_BASES = (
    "https://query1.finance.yahoo.com/v8/finance/chart/",
    "https://query2.finance.yahoo.com/v8/finance/chart/",
)


def to_yahoo(symbol: str) -> str:
    """Plain NSE symbol -> Yahoo ticker (append .NS if no exchange given)."""
    s = symbol.strip().upper()
    return s if "." in s else f"{s}.NS"


def _parse(result: dict) -> Tuple[pd.DataFrame, dict]:
    meta = result.get("meta", {}) or {}
    ts = result.get("timestamp") or []
    quote = ((result.get("indicators", {}) or {}).get("quote") or [{}])[0] or {}
    closes = quote.get("close") or []
    if not ts or not closes:
        return pd.DataFrame(), meta

    idx = pd.to_datetime(ts, unit="s", utc=True).tz_convert("Asia/Kolkata")
    df = pd.DataFrame(
        {
            "Open": quote.get("open"),
            "High": quote.get("high"),
            "Low": quote.get("low"),
            "Close": closes,
            "Volume": quote.get("volume"),
        },
        index=idx,
    )

    # Apply adjusted-close factor to OHLC (split/dividend handling).
    adj = (result.get("indicators", {}) or {}).get("adjclose") or []
    adjc = adj[0].get("adjclose") if adj and adj[0] else None
    if adjc:
        ser = pd.Series(adjc, index=idx)
        factor = (ser / df["Close"]).replace([float("inf"), float("-inf")], 1).fillna(1)
        for c in ("Open", "High", "Low"):
            df[c] = df[c] * factor
        df["Close"] = ser

    return df.dropna(how="all"), meta


def fetch_raw(symbol: str, rng: str, interval: str, timeout: int = 15) -> Optional[dict]:
    ysym = to_yahoo(symbol)
    params = {"range": rng, "interval": interval,
              "includePrePost": "false", "events": "div,splits"}
    for base in _BASES:
        try:
            r = _SESSION.get(base + ysym, params=params, timeout=timeout)
            if r.status_code == 200:
                res = (r.json().get("chart", {}) or {}).get("result")
                if res:
                    return res[0]
        except Exception:
            continue
    return None


def chart_df(symbol: str, rng: str = "2y", interval: str = "1d",
             ttl: float = 600) -> Tuple[pd.DataFrame, dict]:
    """Return (OHLCV DataFrame, meta dict). Empty frame on failure."""
    key = f"yc:{to_yahoo(symbol)}:{rng}:{interval}"
    cached = cache_get(key, ttl)
    if cached is not None:
        return cached
    res = fetch_raw(symbol, rng, interval)
    out = _parse(res) if res else (pd.DataFrame(), {})
    cache_set(key, out)
    return out


def batch_chart_meta(symbols: List[str], rng: str = "2y", interval: str = "1d",
                     ttl: float = 900, workers: int = 16) -> Dict[str, Tuple[pd.DataFrame, dict]]:
    key = f"ybatch_m:{interval}:{rng}:" + ",".join(sorted(to_yahoo(s) for s in symbols))
    cached = cache_get(key, ttl)
    if cached is not None:
        return cached

    def one(sym):
        res = fetch_raw(sym, rng, interval)
        if res:
            df, meta = _parse(res)
            if not df.empty:
                # Share this snapshot with chart_df's per-symbol cache so that
                # opening a just-scanned stock shows the SAME price (and skips a
                # redundant network request).
                cache_set(f"yc:{to_yahoo(sym)}:{rng}:{interval}", (df, meta))
                return sym.upper(), df, meta
        return None

    out = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for r in ex.map(one, symbols):
            if r:
                out[r[0]] = (r[1], r[2])
    cache_set(key, out)
    return out

def batch_chart(symbols: List[str], rng: str = "2y", interval: str = "1d",
                ttl: float = 900, workers: int = 16) -> Dict[str, pd.DataFrame]:
    """Concurrently fetch many symbols. Failures are omitted (never faked)."""
    res = batch_chart_meta(symbols, rng, interval, ttl, workers)
    return {k: v[0] for k, v in res.items()}
