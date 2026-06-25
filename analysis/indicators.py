"""Technical indicators computed from real OHLCV data using pandas/numpy.

Implementations follow standard definitions (Wilder smoothing for RSI/ATR/ADX).
Each function operates on a DataFrame with columns: Open, High, Low, Close, Volume.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(window=n, min_periods=n).mean()


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(100 - (100 / (1 + 0)))  # if no losses -> ~100


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tr = true_range(df)
    return tr.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = sma(close, n)
    std = close.rolling(window=n, min_periods=n).std()
    upper = mid + k * std
    lower = mid - k * std
    return upper, mid, lower


def stochastic(df: pd.DataFrame, n: int = 14, d: int = 3):
    low_n = df["Low"].rolling(window=n, min_periods=n).min()
    high_n = df["High"].rolling(window=n, min_periods=n).max()
    k = 100 * (df["Close"] - low_n) / (high_n - low_n).replace(0, np.nan)
    k = k.clip(0, 100)
    d_line = k.rolling(window=d, min_periods=d).mean()
    return k, d_line


def adx(df: pd.DataFrame, n: int = 14):
    up = df["High"].diff()
    down = -df["Low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = true_range(df)
    atr_n = tr.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(
        alpha=1 / n, min_periods=n, adjust=False).mean() / atr_n
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(
        alpha=1 / n, min_periods=n, adjust=False).mean() / atr_n
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_line = dx.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    return adx_line, plus_di, minus_di


def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["Close"].diff().fillna(0))
    return (direction * df["Volume"].fillna(0)).cumsum()


def _round(v, nd=2):
    try:
        if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
            return None
        return round(float(v), nd)
    except (ValueError, TypeError):
        return None


def pct_return(close: pd.Series, periods: int) -> Optional[float]:
    if len(close) <= periods:
        return None
    past = close.iloc[-periods - 1]
    now = close.iloc[-1]
    if past == 0 or pd.isna(past):
        return None
    return _round((now / past - 1) * 100)


def compute(df: pd.DataFrame) -> Dict:
    """Compute a full indicator snapshot from an OHLCV frame.

    Returns a dict of the latest indicator values plus selected return windows.
    Returns {'ok': False} if there is not enough data.
    """
    if df is None or df.empty or len(df) < 20:
        return {"ok": False, "bars": 0 if df is None else len(df)}

    close = df["Close"]
    n = len(df)

    macd_line, signal_line, hist = macd(close)
    upper, mid, lower = bollinger(close)
    k, d = stochastic(df)
    adx_line, plus_di, minus_di = adx(df)
    obv_series = obv(df)
    atr_series = atr(df)
    rsi_series = rsi(close)

    last = -1

    def val(series):
        try:
            return series.iloc[last]
        except (IndexError, KeyError):
            return np.nan

    price = float(close.iloc[last])
    atr_val = val(atr_series)

    # OBV trend: slope over last ~20 bars
    obv_trend = None
    if n >= 21 and pd.notna(obv_series.iloc[last]):
        recent = obv_series.iloc[-min(20, n):]
        obv_trend = "up" if recent.iloc[-1] > recent.iloc[0] else "down"

    snapshot = {
        "ok": True,
        "bars": n,
        "price": _round(price),
        "sma20": _round(val(sma(close, 20))),
        "sma50": _round(val(sma(close, 50))) if n >= 50 else None,
        "sma200": _round(val(sma(close, 200))) if n >= 200 else None,
        "ema20": _round(val(ema(close, 20))),
        "ema50": _round(val(ema(close, 50))) if n >= 50 else None,
        "rsi14": _round(val(rsi_series)),
        "macd": _round(val(macd_line), 3),
        "macd_signal": _round(val(signal_line), 3),
        "macd_hist": _round(val(hist), 3),
        "bb_upper": _round(val(upper)),
        "bb_mid": _round(val(mid)),
        "bb_lower": _round(val(lower)),
        "stoch_k": _round(val(k)),
        "stoch_d": _round(val(d)),
        "adx": _round(val(adx_line)),
        "plus_di": _round(val(plus_di)),
        "minus_di": _round(val(minus_di)),
        "atr14": _round(atr_val),
        "atr_pct": _round((atr_val / price * 100) if (atr_val and price) else None),
        "obv_trend": obv_trend,
        "vol": int(df["Volume"].iloc[last]) if pd.notna(df["Volume"].iloc[last]) else None,
        "avg_vol20": int(df["Volume"].iloc[-20:].mean()) if n >= 20 else None,
        "ret_1d": pct_return(close, 1),
        "ret_1w": pct_return(close, 5),
        "ret_1m": pct_return(close, 21),
        "ret_3m": pct_return(close, 63),
        "ret_6m": pct_return(close, 126),
        "ret_1y": pct_return(close, 252),
        # recent swing levels for support/resistance & stop placement
        "recent_low": _round(df["Low"].iloc[-20:].min()) if n >= 20 else None,
        "recent_high": _round(df["High"].iloc[-20:].max()) if n >= 20 else None,
        "swing_low_50": _round(df["Low"].iloc[-50:].min()) if n >= 50 else None,
        "swing_high_50": _round(df["High"].iloc[-50:].max()) if n >= 50 else None,
    }
    return snapshot
