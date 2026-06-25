"""Market-data access layer.

Routing (chosen from real benchmarks — see analysis/yahoo.py):
  * prices / history / live quote -> fast concurrent Yahoo v8 chart client
  * fundamentals (P/E, ROE, …)    -> yfinance (handles Yahoo's crumb auth)
  * symbol search                 -> Yahoo search endpoint (Indian listings)

Nothing here fabricates data: a failed fetch yields an empty frame / None and
the UI shows that, never a guess.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import pandas as pd

from . import yahoo
from .cache import cache_get, cache_set

# Re-export so existing callers keep working.
to_yahoo = yahoo.to_yahoo


# ---------------------------------------------------------------------------
# History (fast path: direct Yahoo chart client)
# ---------------------------------------------------------------------------
def get_history(symbol: str, period: str = "2y", interval: str = "1d",
                ttl: float = 600) -> pd.DataFrame:
    df, _ = yahoo.chart_df(symbol, period, interval, ttl)
    return df


def get_history_meta(symbol: str, period: str = "2y", interval: str = "1d",
                     ttl: float = 600) -> Tuple[pd.DataFrame, dict]:
    """History plus the chart `meta` (live price, prev close, 52w range)."""
    return yahoo.chart_df(symbol, period, interval, ttl)


def batch_history(symbols: List[str], period: str = "2y", interval: str = "1d",
                  ttl: float = 900) -> Dict[str, pd.DataFrame]:
    return yahoo.batch_chart(symbols, period, interval, ttl)


def batch_history_meta(symbols: List[str], period: str = "2y", interval: str = "1d",
                       ttl: float = 900) -> Dict[str, Tuple[pd.DataFrame, dict]]:
    return yahoo.batch_chart_meta(symbols, period, interval, ttl)


def quote_from_meta(meta: dict, hist: pd.DataFrame = None) -> dict:
    """Build a live-quote dict. The displayed price is Yahoo's live
    `regularMarketPrice`; the day-change is computed from the daily series
    (previous trading day's close) — NOT `chartPreviousClose`, which for a
    multi-period range is the close at the *start* of the range."""
    last_close = float(hist["Close"].iloc[-1]) if (hist is not None and not hist.empty) else None
    prev = float(hist["Close"].iloc[-2]) if (hist is not None and len(hist) >= 2) else None
    price = meta.get("regularMarketPrice")
    if price is None:
        price = last_close
    change = round(price - prev, 2) if (price is not None and prev) else None
    change_pct = round((price / prev - 1) * 100, 2) if (price is not None and prev) else None
    return {
        "price": round(price, 2) if price is not None else None,
        "change": change,
        "change_pct": change_pct,
        "currency": meta.get("currency", "INR"),
        "fifty_two_high": meta.get("fiftyTwoWeekHigh"),
        "fifty_two_low": meta.get("fiftyTwoWeekLow"),
        "exchange": meta.get("fullExchangeName") or meta.get("exchangeName"),
    }


# ---------------------------------------------------------------------------
# Fundamentals (yfinance — robust crumb handling, cached 1h)
# ---------------------------------------------------------------------------
def get_fundamentals(symbol: str, ttl: float = 3600) -> Dict:
    ysym = yahoo.to_yahoo(symbol)
    key = f"fund:{ysym}"
    cached = cache_get(key, ttl)
    if cached is not None:
        return cached

    info: Dict = {}
    try:
        import yfinance as yf
        raw = {}
        try:
            raw = yf.Ticker(ysym).get_info() or {}
        except Exception:
            raw = {}

        def pick(*keys):
            for k in keys:
                v = raw.get(k)
                if v not in (None, "", 0):
                    return v
            return None

        info = {
            "name": raw.get("longName") or raw.get("shortName"),
            "sector": raw.get("sector"),
            "industry": raw.get("industry"),
            "currency": raw.get("currency") or "INR",
            "last_price": raw.get("currentPrice"),
            "market_cap": raw.get("marketCap"),
            "pe": pick("trailingPE"),
            "forward_pe": pick("forwardPE"),
            "pb": pick("priceToBook"),
            "roe": pick("returnOnEquity"),
            "profit_margin": pick("profitMargins"),
            "debt_to_equity": pick("debtToEquity"),
            "dividend_yield": pick("dividendYield"),
            "earnings_growth": pick("earningsGrowth", "earningsQuarterlyGrowth"),
            "revenue_growth": pick("revenueGrowth"),
            "beta": pick("beta"),
            "fifty_two_high": raw.get("fiftyTwoWeekHigh"),
            "fifty_two_low": raw.get("fiftyTwoWeekLow"),
            "recommendation_key": raw.get("recommendationKey"),
            "target_mean": pick("targetMeanPrice"),
            "num_analysts": pick("numberOfAnalystOpinions"),
        }
    except Exception:
        info = {}

    cache_set(key, info)
    return info


# ---------------------------------------------------------------------------
# News Sentiment (VADER)
# ---------------------------------------------------------------------------
def get_news_sentiment(symbol: str, ttl: float = 3600) -> Dict:
    ysym = yahoo.to_yahoo(symbol)
    key = f"sentiment:{ysym}"
    cached = cache_get(key, ttl)
    if cached is not None:
        return cached
        
    result = {"score": 0.0, "articles": 0, "positive": 0, "negative": 0}
    try:
        import yfinance as yf
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        
        ticker = yf.Ticker(ysym)
        news = ticker.news or []
        if not news:
            cache_set(key, result)
            return result
            
        analyzer = SentimentIntensityAnalyzer()
        scores = []
        for item in news:
            content = item.get("content", {}) if isinstance(item, dict) and "content" in item else item
            title = content.get("title", "")
            summary = content.get("summary", "")
            if not title and not summary:
                continue
            
            text = f"{title}. {summary}"
            score = analyzer.polarity_scores(text)["compound"]
            scores.append(score)
            
        if scores:
            result["score"] = round(sum(scores) / len(scores), 3)
            result["articles"] = len(scores)
            result["positive"] = sum(1 for s in scores if s > 0.05)
            result["negative"] = sum(1 for s in scores if s < -0.05)
            
    except Exception as e:
        print(f"Sentiment fetch failed for {symbol}: {e}")
        # Return fallback neutral result
        
    cache_set(key, result)
    return result


# ---------------------------------------------------------------------------
# Symbol search (so ANY listed stock is findable)
# ---------------------------------------------------------------------------
def yahoo_search(query: str, ttl: float = 1800) -> List[Dict]:
    q = (query or "").strip()
    if not q:
        return []
    key = f"ysearch:{q.lower()}"
    cached = cache_get(key, ttl)
    if cached is not None:
        return cached

    results: List[Dict] = []
    try:
        resp = yahoo._SESSION.get(
            "https://query2.finance.yahoo.com/v1/finance/search",
            params={"q": q, "quotesCount": 12, "newsCount": 0},
            timeout=10,
        )
        if resp.ok:
            for item in resp.json().get("quotes", []):
                sym = item.get("symbol", "")
                if item.get("quoteType") != "EQUITY":
                    continue
                if not (sym.endswith(".NS") or sym.endswith(".BO")):
                    continue
                results.append({
                    "symbol": sym.replace(".NS", "").replace(".BO", ""),
                    "yahoo_symbol": sym,
                    "name": item.get("longname") or item.get("shortname") or sym,
                    "sector": item.get("sector", "-"),
                    "exchange": "NSE" if sym.endswith(".NS") else "BSE",
                })
    except Exception:
        results = []

    cache_set(key, results)
    return results


# ---------------------------------------------------------------------------
# Serialise OHLCV for the charting library
# ---------------------------------------------------------------------------
def history_to_records(df: pd.DataFrame, intraday: bool = False) -> List[Dict]:
    if df is None or df.empty:
        return []
    records = []
    for idx, row in df.iterrows():
        t = int(pd.Timestamp(idx).timestamp()) if intraday else pd.Timestamp(idx).strftime("%Y-%m-%d")
        try:
            records.append({
                "time": t,
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                "volume": int(row["Volume"]) if pd.notna(row.get("Volume")) else 0,
            })
        except (ValueError, TypeError):
            continue
    return records
