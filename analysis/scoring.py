"""Recommendation / scoring engine.

Turns a real indicator snapshot (+ optional fundamentals) into:
  * a 0-100 score and a BUY / SELL / HOLD label
  * a short, human-readable analysis (the signals that drove the score)
  * suggested entry, ATR-based stop-loss and risk/reward targets

Two horizon profiles are supported:
  * 'short'  -> swing/short-term: momentum & fast trend dominate
  * 'long'   -> investing: long trend, cross & fundamentals dominate

This is rule-based technical analysis, NOT a price prediction or guarantee.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from . import indicators

# Per-signal weights for each horizon. Higher weight => bigger influence.
WEIGHTS = {
    "short": {
        "price_vs_sma20": 1.0, "sma20_vs_sma50": 1.1, "price_vs_sma200": 0.4,
        "golden_cross": 0.5, "rsi": 1.2, "macd": 1.3, "stoch": 0.8,
        "adx": 0.9, "bollinger": 0.6, "obv": 0.7, "momentum": 1.3,
        "fundamentals": 0.3, "sentiment": 1.0,
    },
    "long": {
        "price_vs_sma20": 0.4, "sma20_vs_sma50": 0.5, "price_vs_sma200": 1.5,
        "golden_cross": 1.2, "rsi": 0.5, "macd": 0.6, "stoch": 0.2,
        "adx": 0.7, "bollinger": 0.2, "obv": 0.5, "momentum": 1.1,
        "fundamentals": 1.7, "sentiment": 0.4,
    },
}


def _clamp(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _sig(signals, name, value, reason, weights):
    w = weights.get(name, 0.0)
    if w <= 0:
        return
    signals.append({"name": name, "value": _clamp(value), "weight": w, "reason": reason})


def build_signals(snap: Dict, fundamentals: Optional[Dict], horizon: str, sentiment: Optional[Dict] = None) -> List[Dict]:
    w = WEIGHTS.get(horizon, WEIGHTS["short"])
    s: List[Dict] = []
    price = snap.get("price")

    # --- Trend: price vs moving averages ---
    if price and snap.get("sma20"):
        d = (price - snap["sma20"]) / snap["sma20"] * 100
        _sig(s, "price_vs_sma20", _clamp(d / 3),
             ("Price above 20-DMA (short-term uptrend)" if d > 0
              else "Price below 20-DMA (short-term weakness)"), w)
    if price and snap.get("sma50") and snap.get("sma20"):
        bull = snap["sma20"] > snap["sma50"]
        _sig(s, "sma20_vs_sma50", 0.8 if bull else -0.8,
             ("20-DMA above 50-DMA (bullish alignment)" if bull
              else "20-DMA below 50-DMA (bearish alignment)"), w)
    if price and snap.get("sma200"):
        above = price > snap["sma200"]
        d = (price - snap["sma200"]) / snap["sma200"] * 100
        _sig(s, "price_vs_sma200", _clamp(d / 10),
             ("Trading above 200-DMA (long-term uptrend)" if above
              else "Trading below 200-DMA (long-term downtrend)"), w)
    if snap.get("sma50") and snap.get("sma200"):
        golden = snap["sma50"] > snap["sma200"]
        _sig(s, "golden_cross", 0.9 if golden else -0.9,
             ("Golden-cross regime (50-DMA > 200-DMA)" if golden
              else "Death-cross regime (50-DMA < 200-DMA)"), w)

    # --- RSI (momentum / overbought-oversold) ---
    rsi = snap.get("rsi14")
    if rsi is not None:
        if rsi >= 80:
            _sig(s, "rsi", -0.6, f"RSI {rsi:.0f} — strongly overbought (pullback risk)", w)
        elif rsi >= 70:
            _sig(s, "rsi", -0.2, f"RSI {rsi:.0f} — overbought", w)
        elif rsi >= 55:
            _sig(s, "rsi", 0.7, f"RSI {rsi:.0f} — healthy bullish momentum", w)
        elif rsi >= 45:
            _sig(s, "rsi", 0.0, f"RSI {rsi:.0f} — neutral momentum", w)
        elif rsi >= 30:
            _sig(s, "rsi", -0.5, f"RSI {rsi:.0f} — weak momentum", w)
        else:
            _sig(s, "rsi", 0.3, f"RSI {rsi:.0f} — oversold (possible bounce)", w)

    # --- MACD ---
    macd, sigl, hist = snap.get("macd"), snap.get("macd_signal"), snap.get("macd_hist")
    if macd is not None and sigl is not None and hist is not None:
        v = 0.7 if hist > 0 else -0.7
        if macd > 0 and hist > 0:
            v = 0.9
        elif macd < 0 and hist < 0:
            v = -0.9
        _sig(s, "macd", v,
             ("MACD above signal line (bullish)" if hist > 0
              else "MACD below signal line (bearish)"), w)

    # --- Stochastic ---
    k, dd = snap.get("stoch_k"), snap.get("stoch_d")
    if k is not None and dd is not None:
        if k > 80:
            _sig(s, "stoch", -0.4, f"Stochastic {k:.0f} — overbought", w)
        elif k < 20:
            _sig(s, "stoch", 0.4, f"Stochastic {k:.0f} — oversold", w)
        else:
            up = k > dd
            _sig(s, "stoch", 0.5 if up else -0.5,
                 ("Stochastic turning up" if up else "Stochastic turning down"), w)

    # --- ADX (trend strength + direction) ---
    adx, pdi, mdi = snap.get("adx"), snap.get("plus_di"), snap.get("minus_di")
    if adx is not None and pdi is not None and mdi is not None:
        strength = _clamp(adx / 40)
        if adx >= 20:
            if pdi > mdi:
                _sig(s, "adx", strength, f"ADX {adx:.0f} — strong uptrend (+DI>−DI)", w)
            else:
                _sig(s, "adx", -strength, f"ADX {adx:.0f} — strong downtrend (−DI>+DI)", w)
        else:
            _sig(s, "adx", 0.0, f"ADX {adx:.0f} — weak/range-bound trend", w)

    # --- Bollinger position ---
    if price and snap.get("bb_upper") and snap.get("bb_lower"):
        if price <= snap["bb_lower"]:
            _sig(s, "bollinger", 0.5, "At/below lower Bollinger band (oversold)", w)
        elif price >= snap["bb_upper"]:
            _sig(s, "bollinger", -0.3, "At/above upper Bollinger band (stretched)", w)

    # --- OBV (volume flow) ---
    if snap.get("obv_trend"):
        up = snap["obv_trend"] == "up"
        _sig(s, "obv", 0.6 if up else -0.6,
             ("On-balance volume rising (accumulation)" if up
              else "On-balance volume falling (distribution)"), w)

    # --- Momentum (returns over the relevant window) ---
    if horizon == "short":
        rets = [snap.get("ret_1w"), snap.get("ret_1m")]
        ref = 8.0
        label_win = "1-week/1-month"
    else:
        rets = [snap.get("ret_6m"), snap.get("ret_1y")]
        ref = 25.0
        label_win = "6-month/1-year"
    rets = [r for r in rets if r is not None]
    if rets:
        avg = sum(rets) / len(rets)
        _sig(s, "momentum", _clamp(avg / ref),
             f"{label_win} momentum {avg:+.1f}%", w)

    # --- Fundamentals (best-effort; only counts what we actually have) ---
    if fundamentals:
        f_vals, f_reasons = [], []
        pe = fundamentals.get("pe")
        if pe is not None:
            if pe <= 0:
                f_vals.append(-0.7); f_reasons.append("Negative earnings (P/E<0)")
            elif pe < 20:
                f_vals.append(0.6); f_reasons.append(f"Reasonable valuation (P/E {pe:.1f})")
            elif pe < 40:
                f_vals.append(0.0); f_reasons.append(f"Moderate valuation (P/E {pe:.1f})")
            else:
                f_vals.append(-0.4); f_reasons.append(f"Rich valuation (P/E {pe:.1f})")
        roe = fundamentals.get("roe")
        if roe is not None:
            if roe >= 0.15:
                f_vals.append(0.7); f_reasons.append(f"Strong ROE ({roe*100:.0f}%)")
            elif roe < 0.05:
                f_vals.append(-0.5); f_reasons.append(f"Weak ROE ({roe*100:.0f}%)")
        eg = fundamentals.get("earnings_growth")
        if eg is not None:
            if eg > 0.10:
                f_vals.append(0.6); f_reasons.append(f"Earnings growing ({eg*100:.0f}%)")
            elif eg < 0:
                f_vals.append(-0.6); f_reasons.append(f"Earnings declining ({eg*100:.0f}%)")
        de = fundamentals.get("debt_to_equity")
        if de is not None:
            if de < 50:
                f_vals.append(0.4); f_reasons.append("Low debt (D/E < 0.5)")
            elif de > 150:
                f_vals.append(-0.5); f_reasons.append("High leverage (D/E > 1.5)")
        rk = (fundamentals.get("recommendation_key") or "").lower()
        if rk in ("buy", "strong_buy"):
            f_vals.append(0.5); f_reasons.append(f"Street consensus: {rk.replace('_', ' ')}")
        elif rk in ("sell", "strong_sell", "underperform"):
            f_vals.append(-0.5); f_reasons.append(f"Street consensus: {rk.replace('_', ' ')}")
        if f_vals:
            avg = sum(f_vals) / len(f_vals)
            _sig(s, "fundamentals", avg, "; ".join(f_reasons[:3]), w)

    # --- Sentiment ---
    if sentiment and sentiment.get("articles", 0) > 0:
        score = sentiment.get("score", 0.0)
        if score >= 0.15:
            _sig(s, "sentiment", 0.7, f"Positive news sentiment (score: {score:+.2f})", w)
        elif score <= -0.15:
            _sig(s, "sentiment", -0.7, f"Negative news sentiment (score: {score:+.2f})", w)

    return s


def _label(score: float):
    if score >= 75:
        return "STRONG BUY", "BUY"
    if score >= 60:
        return "BUY", "BUY"
    if score >= 45:
        return "HOLD", "HOLD"
    if score >= 30:
        return "SELL", "SELL"
    return "STRONG SELL", "SELL"


def _levels(action: str, snap: Dict) -> Optional[Dict]:
    """ATR + structure based stop-loss and 2:1 / 3:1 targets."""
    price = snap.get("price")
    atr = snap.get("atr14")
    if not price or not atr or atr <= 0:
        return None

    if action == "BUY":
        atr_stop = price - 2 * atr
        struct = snap.get("recent_low")
        stop = atr_stop
        if struct and atr_stop < struct < price:
            stop = struct  # respect nearby support (tighter, still below price)
        stop = min(stop, price - 0.5 * atr)  # never above ~0.5 ATR from price
        risk = price - stop
        t1, t2 = price + 2 * risk, price + 3 * risk
        resistance = snap.get("recent_high")
    elif action == "SELL":
        atr_stop = price + 2 * atr
        struct = snap.get("recent_high")
        stop = atr_stop
        if struct and price < struct < atr_stop:
            stop = struct
        stop = max(stop, price + 0.5 * atr)
        risk = stop - price
        t1, t2 = price - 2 * risk, price - 3 * risk
        resistance = snap.get("recent_low")
    else:  # HOLD -> protective stop for an existing long position
        stop = price - 2 * atr
        risk = price - stop
        t1 = t2 = None
        resistance = snap.get("recent_high")

    return {
        "entry": round(price, 2),
        "stop_loss": round(stop, 2),
        "risk_per_share": round(risk, 2),
        "risk_pct": round(risk / price * 100, 2),
        "target1": round(t1, 2) if t1 else None,
        "target2": round(t2, 2) if t2 else None,
        "risk_reward": 2.0 if t1 else None,
        "key_level": resistance,
    }


def backtest(df: pd.DataFrame, fundamentals: Optional[Dict], horizon: str) -> Dict:
    """Run a simplified backtest over the last 1 year (252 days) to find the win rate
    of BUY signals over a 20-day (1 month) holding period.
    """
    if df is None or len(df) < 260:
        return {"win_rate": None, "trades": 0, "wins": 0}
        
    trades = 0
    wins = 0
    
    # Pre-calculate indicators for the entire dataframe ONCE (Extremely fast)
    close = df["Close"]
    macd_line, signal_line, hist = indicators.macd(close)
    upper, mid, lower = indicators.bollinger(close)
    k, d = indicators.stochastic(df)
    adx_line, plus_di, minus_di = indicators.adx(df)
    obv_series = indicators.obv(df)
    rsi_series = indicators.rsi(close)
    sma20 = indicators.sma(close, 20)
    sma50 = indicators.sma(close, 50)
    sma200 = indicators.sma(close, 200)

    start_idx = len(df) - 252
    end_idx = len(df) - 20
    
    if start_idx < 200:
        start_idx = 200
        
    for i in range(start_idx, end_idx):
        price = float(close.iloc[i])
        
        snap = {
            "price": price,
            "sma20": float(sma20.iloc[i]) if pd.notna(sma20.iloc[i]) else None,
            "sma50": float(sma50.iloc[i]) if pd.notna(sma50.iloc[i]) else None,
            "sma200": float(sma200.iloc[i]) if pd.notna(sma200.iloc[i]) else None,
            "rsi14": float(rsi_series.iloc[i]) if pd.notna(rsi_series.iloc[i]) else None,
            "macd": float(macd_line.iloc[i]) if pd.notna(macd_line.iloc[i]) else None,
            "macd_signal": float(signal_line.iloc[i]) if pd.notna(signal_line.iloc[i]) else None,
            "macd_hist": float(hist.iloc[i]) if pd.notna(hist.iloc[i]) else None,
            "stoch_k": float(k.iloc[i]) if pd.notna(k.iloc[i]) else None,
            "stoch_d": float(d.iloc[i]) if pd.notna(d.iloc[i]) else None,
            "adx": float(adx_line.iloc[i]) if pd.notna(adx_line.iloc[i]) else None,
            "plus_di": float(plus_di.iloc[i]) if pd.notna(plus_di.iloc[i]) else None,
            "minus_di": float(minus_di.iloc[i]) if pd.notna(minus_di.iloc[i]) else None,
            "bb_upper": float(upper.iloc[i]) if pd.notna(upper.iloc[i]) else None,
            "bb_lower": float(lower.iloc[i]) if pd.notna(lower.iloc[i]) else None,
        }
        
        if i >= 5: snap["ret_1w"] = float((price / close.iloc[i-5] - 1) * 100)
        if i >= 21: snap["ret_1m"] = float((price / close.iloc[i-21] - 1) * 100)
        if i >= 126: snap["ret_6m"] = float((price / close.iloc[i-126] - 1) * 100)
        if i >= 252: snap["ret_1y"] = float((price / close.iloc[i-252] - 1) * 100)

        recent_obv = obv_series.iloc[i-20:i]
        if len(recent_obv) > 0 and pd.notna(recent_obv.iloc[-1]):
            snap["obv_trend"] = "up" if recent_obv.iloc[-1] > recent_obv.iloc[0] else "down"

        signals = build_signals(snap, fundamentals, horizon)
        total_w = sum(x["weight"] for x in signals) or 1.0
        raw = sum(x["value"] * x["weight"] for x in signals) / total_w
        score = round((raw + 1) / 2 * 100)
        _, action = _label(score)
        
        if action == "BUY":
            trades += 1
            exit_price = float(close.iloc[i+20])
            if exit_price > price:
                wins += 1
                
    win_rate = round((wins / trades * 100), 1) if trades > 0 else None
    return {"win_rate": win_rate, "trades": trades, "wins": wins}


def analyze(symbol: str, df, fundamentals: Optional[Dict] = None,
            horizon: str = "short", meta: Optional[Dict] = None,
            sentiment: Optional[Dict] = None, live_price: Optional[float] = None) -> Dict:
    """Full analysis for one stock. `df` is an OHLCV DataFrame."""
    snap = indicators.compute(df)
    meta = meta or {}
    base = {
        "symbol": symbol.upper(),
        "name": (fundamentals or {}).get("name") or meta.get("name") or symbol.upper(),
        "sector": (fundamentals or {}).get("sector") or meta.get("sector") or "-",
        "horizon": horizon,
    }
    if not snap.get("ok"):
        base.update({"ok": False, "reason": "Not enough price history to analyse.",
                     "score": None, "label": "NO DATA", "action": "HOLD",
                     "indicators": snap})
        return base

    # Anchor the reference price to the LIVE traded price when we have it, so the
    # dashboard table and the detail page never disagree, and entry/stop/targets
    # are based on the current price rather than the (possibly lagging) last
    # daily candle close. Indicator values (RSI, SMA, returns) stay on close.
    lp = live_price if live_price is not None else meta.get("regularMarketPrice")
    if lp:
        snap = dict(snap)
        snap["last_close"] = snap.get("price")
        snap["price"] = round(float(lp), 2)

    signals = build_signals(snap, fundamentals, horizon, sentiment)
    total_w = sum(x["weight"] for x in signals) or 1.0
    raw = sum(x["value"] * x["weight"] for x in signals) / total_w  # -1..1
    score = round((raw + 1) / 2 * 100)
    label, action = _label(score)

    # Confidence: distance from neutral + trend strength + data depth
    conf = abs(score - 50) / 50  # 0..1
    if snap.get("adx"):
        conf = min(1.0, conf * (0.7 + min(snap["adx"], 40) / 80))
    confidence = round(40 + conf * 55)  # 40..95

    # Top drivers, split into supporting / opposing the call
    ranked = sorted(signals, key=lambda x: abs(x["value"] * x["weight"]), reverse=True)
    positives = [x["reason"] for x in ranked if x["value"] > 0.05][:5]
    negatives = [x["reason"] for x in ranked if x["value"] < -0.05][:5]

    summary = (
        f"{label} ({score}/100) for {horizon}-term. "
        + (f"Drivers: {positives[0]}. " if positives else "")
        + (f"Watch: {negatives[0]}." if negatives else "")
    ).strip()

    base.update({
        "ok": True,
        "score": score,
        "label": label,
        "action": action,
        "confidence": confidence,
        "summary": summary,
        "positives": positives,
        "negatives": negatives,
        "levels": _levels(action, snap),
        "indicators": snap,
    })
    return base


def scan(data_by_symbol: Dict, horizon: str, fundamentals_by_symbol: Optional[Dict] = None,
         meta_by_symbol: Optional[Dict] = None) -> List[Dict]:
    """Analyse a batch of stocks and return compact ranking rows (sorted by score desc)."""
    fundamentals_by_symbol = fundamentals_by_symbol or {}
    meta_by_symbol = meta_by_symbol or {}
    rows = []
    for sym, df in data_by_symbol.items():
        res = analyze(sym, df, fundamentals_by_symbol.get(sym),
                      horizon, meta_by_symbol.get(sym))
        if not res.get("ok"):
            continue
        snap = res["indicators"]
        rows.append({
            "symbol": res["symbol"],
            "name": res["name"],
            "sector": res["sector"],
            "score": res["score"],
            "label": res["label"],
            "action": res["action"],
            "confidence": res["confidence"],
            "price": snap.get("price"),
            "ret_1d": snap.get("ret_1d"),
            "ret_1w": snap.get("ret_1w"),
            "ret_1m": snap.get("ret_1m"),
            "ret_3m": snap.get("ret_3m"),
            "ret_6m": snap.get("ret_6m"),
            "ret_1y": snap.get("ret_1y"),
            "rsi14": snap.get("rsi14"),
            "stop_loss": (res.get("levels") or {}).get("stop_loss"),
            "target1": (res.get("levels") or {}).get("target1"),
            "reason": (res["positives"][0] if res["positives"]
                       else (res["negatives"][0] if res["negatives"] else "")),
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows


# ---------------------------------------------------------------------------
# Steady-accumulation screener
# ---------------------------------------------------------------------------
# Idea (credit: a seasoned trader): the news everyone sees is already priced in.
# The edge is spotting stocks that are climbing QUIETLY and STEADILY — a smooth,
# low-volatility uptrend on rising volume (accumulation) that hasn't yet had its
# big news-driven pop. This scores exactly that profile.

def steady_score(df, lookback: int = 40) -> Optional[Dict]:
    """Score how 'steadily and quietly' a stock is trending up over `lookback`
    trading days. Returns None if there isn't enough data."""
    if df is None or len(df) < lookback + 12:
        return None
    close = df["Close"]
    window = close.iloc[-lookback:]
    y = window.values.astype(float)
    n = len(y)
    x = np.arange(n)

    # Linear fit -> drift (slope) and smoothness (R^2).
    slope, intercept = np.polyfit(x, y, 1)
    yhat = slope * x + intercept
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2)) or 1e-9
    r2 = max(0.0, 1.0 - ss_res / ss_tot)
    mean_price = float(y.mean()) or 1e-9
    slope_pct_day = slope / mean_price * 100.0          # % drift per day

    rets = window.pct_change().dropna().values * 100.0
    up_days = float(np.mean(rets > 0)) if len(rets) else 0.0      # 0..1
    daily_vol = float(np.std(rets)) if len(rets) else 0.0        # % std
    max_day = float(np.max(np.abs(rets))) if len(rets) else 0.0  # biggest 1-day move

    # Accumulation: OBV rising + volume picking up in the recent third.
    obv = indicators.obv(df).iloc[-lookback:]
    obv_up = bool(obv.iloc[-1] > obv.iloc[0])
    v = df["Volume"].iloc[-lookback:].fillna(0).values.astype(float)
    seg = max(5, n // 3)
    vol_ratio = float(v[-seg:].mean() / (v[:seg].mean() or 1e-9))

    # Context — are we already overextended / has the pop already happened?
    sma50 = indicators.sma(close, 50)
    price = float(close.iloc[-1])
    dist_50 = ((price - float(sma50.iloc[-1])) / float(sma50.iloc[-1]) * 100.0
               if pd.notna(sma50.iloc[-1]) else 0.0)
    rsi_val = float(indicators.rsi(close).iloc[-1])
    period_return = (price / float(window.iloc[0]) - 1.0) * 100.0

    # Sub-scores (0..1)
    trend = _clamp(slope_pct_day / 0.25, 0, 1)            # ~0.25%/day = strong steady drift
    smooth = r2 if slope_pct_day > 0 else 0.0             # smoothness only counts if rising
    consistency = _clamp((up_days - 0.45) / 0.30, 0, 1)  # 45% up-days -> 0, 75%+ -> 1
    calm = _clamp(1 - daily_vol / 2.5, 0, 1)             # < 2.5% daily vol is calm
    accumulation = (0.5 if obv_up else 0.0) + 0.5 * _clamp((vol_ratio - 1) / 0.4, 0, 1)

    comps = [(trend, 1.0), (smooth, 1.6), (consistency, 1.3), (calm, 1.0), (accumulation, 1.2)]
    raw = sum(val * wt for val, wt in comps) / sum(wt for _, wt in comps)

    # Penalise the "already popped / overextended" profiles (we want PRE-news).
    penalty = 1.0
    if rsi_val > 72:
        penalty *= 0.6
    if dist_50 > 18:
        penalty *= 0.7
    if max_day > 9:
        penalty *= 0.65   # a big single-day jump = the news may already be out
    if slope_pct_day <= 0:
        penalty *= 0.15

    score = int(round(_clamp(raw * penalty, 0, 1) * 100))

    if score >= 70:
        label = "Strong steady climb"
    elif score >= 55:
        label = "Steady riser"
    elif score >= 40:
        label = "Mild uptrend"
    else:
        label = "Not steady"

    bits = []
    bits.append(f"{up_days*100:.0f}% up-days")
    bits.append(f"smoothness {r2:.2f}")
    if obv_up and vol_ratio > 1.1:
        bits.append("volume building (accumulation)")
    elif obv_up:
        bits.append("OBV rising")
    if rsi_val <= 65 and dist_50 <= 12:
        bits.append("not overextended")

    return {
        "steady_score": score,
        "label": label,
        "slope_pct_day": round(slope_pct_day, 3),
        "slope_pct_month": round(slope_pct_day * 21, 1),
        "r2": round(r2, 2),
        "up_days_pct": round(up_days * 100, 0),
        "daily_vol_pct": round(daily_vol, 2),
        "vol_ratio": round(vol_ratio, 2),
        "obv_up": obv_up,
        "dist_50dma_pct": round(dist_50, 1),
        "rsi": round(rsi_val, 0),
        "period_return": round(period_return, 1),
        "lookback": lookback,
        "reason": ", ".join(bits),
    }


def scan_steady(data_by_symbol: Dict, meta_by_symbol: Optional[Dict] = None,
                lookback: int = 40) -> List[Dict]:
    """Rank a batch of stocks by their steady-accumulation score (risers only)."""
    meta_by_symbol = meta_by_symbol or {}
    rows = []
    for sym, df in data_by_symbol.items():
        s = steady_score(df, lookback)
        if not s or s["slope_pct_day"] <= 0:
            continue  # only quietly-RISING stocks belong here
        m = meta_by_symbol.get(sym) or {}
        live = m.get("regularMarketPrice")
        price = round(float(live), 2) if live else round(float(df["Close"].iloc[-1]), 2)
        rows.append({
            "symbol": sym.upper(),
            "name": m.get("name") or sym.upper(),
            "sector": m.get("sector", "-"),
            "price": price,
            **s,
        })
    rows.sort(key=lambda r: r["steady_score"], reverse=True)
    return rows
