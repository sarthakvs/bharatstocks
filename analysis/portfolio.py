"""Portfolio: import your real holdings/trades and get per-position guidance.

Storage is a JSON file (data/holdings.json). Import is broker-agnostic:
  * manual add (symbol, qty, avg price, optional buy date)
  * CSV / pasted rows — a flexible parser that detects columns by header name and
    handles both a HOLDINGS sheet (one row per stock) and a TRADEBOOK (buy/sell
    rows, which are netted into current positions).

For each holding we compute, from LIVE data: P&L, the current buy/sell signal,
a protective stop-loss + target, an entry-quality read (as of your buy date), and
a plain-English verdict. Nothing is fabricated — this is educational analysis,
not advice.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import threading
import uuid
from typing import Dict, List, Optional

import pandas as pd

from . import data, indicators, scoring, universe, yahoo

_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "holdings.json")
_LOCK = threading.RLock()


def _now_iso() -> str:
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    return dt.datetime.now(ist).strftime("%d %b %Y, %I:%M %p IST")


def _today():
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    return dt.datetime.now(ist).date()


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
def _load() -> List[Dict]:
    with _LOCK:
        if not os.path.exists(_FILE):
            return []
        try:
            with open(_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return []


def _save(rows: List[Dict]) -> None:
    with _LOCK:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2)
        os.replace(tmp, _FILE)


def list_holdings() -> List[Dict]:
    return _load()


def add_holding(symbol: str, qty: float, avg_price: float, buy_date: str = "",
                broker: str = "", note: str = "", name: str = "") -> Dict:
    sym = (symbol or "").strip().upper()
    h = {
        "id": uuid.uuid4().hex[:10],
        "symbol": sym,
        "name": name or universe.get_meta(sym).get("name", sym),
        "qty": float(qty),
        "avg_price": float(avg_price),
        "buy_date": _norm_date(buy_date),
        "broker": broker,
        "note": note,
        "created": _now_iso(),
    }
    with _LOCK:
        rows = _load()
        rows.append(h)
        _save(rows)
    return h


def delete_holding(hid: str) -> bool:
    with _LOCK:
        rows = _load()
        new = [r for r in rows if r["id"] != hid]
        if len(new) == len(rows):
            return False
        _save(new)
        return True


def clear_holdings() -> int:
    with _LOCK:
        n = len(_load())
        _save([])
        return n


# ---------------------------------------------------------------------------
# Flexible CSV / tradebook parser
# ---------------------------------------------------------------------------
_SYMBOL_KEYS = ["tradingsymbol", "symbol", "scrip", "stock symbol", "stock", "instrument",
                "security", "company", "scrip name", "stock name", "name"]
_QTY_KEYS = ["net qty", "netqty", "holding qty", "quantity", "qty", "units", "shares",
             "total qty", "qty."]
_PRICE_KEYS = ["avg price", "average price", "avg. price", "avg cost", "average cost",
               "buy price", "buy rate", "price", "rate", "cost", "avg"]
_DATE_KEYS = ["buy date", "trade date", "purchase date", "order date", "date"]
_ACTION_KEYS = ["action", "type", "buy/sell", "transaction type", "trade type", "b/s",
                "order type", "side"]


def _match(header_lower: str, keys: List[str]) -> bool:
    return any(k == header_lower or k in header_lower for k in keys)


def _pick_col(headers: List[str], keys: List[str]) -> Optional[int]:
    low = [h.strip().lower() for h in headers]
    # exact match first
    for i, h in enumerate(low):
        if h in keys:
            return i
    # then substring
    for i, h in enumerate(low):
        if _match(h, keys):
            return i
    return None


def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip().replace(",", "").replace("₹", "")
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _norm_date(v: str) -> str:
    if not v:
        return ""
    s = str(v).strip()
    if not s:
        return ""
    # ISO (year-first) -> parse as-is; otherwise assume Indian DD-MM-YYYY.
    iso = len(s) >= 4 and s[:4].isdigit()
    try:
        d = pd.to_datetime(s, dayfirst=not iso, errors="coerce")
        return "" if pd.isna(d) else d.strftime("%Y-%m-%d")
    except Exception:
        return ""


def _clean_symbol(s: str) -> str:
    s = (s or "").strip().upper()
    # strip common suffixes/exchange tags
    for suf in (".NS", ".BO", "-EQ", " EQ", "-BE"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return s.strip()


def parse_rows(text: str) -> Dict:
    """Parse CSV/TSV text (holdings or tradebook) into holdings.

    Returns {"holdings": [...], "kind": "holdings"|"tradebook", "skipped": n, "error": ...}
    """
    if not text or not text.strip():
        return {"holdings": [], "error": "Empty input."}

    # sniff delimiter (Excel copy-paste is tab; CSV is comma)
    sample = text[:2000]
    delim = "\t" if sample.count("\t") >= sample.count(",") else ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if len(rows) < 2:
        return {"holdings": [], "error": "Need a header row plus at least one data row."}

    # find the header row (first row that contains a symbol-like column)
    header_idx = 0
    for i, r in enumerate(rows[:5]):
        if _pick_col(r, _SYMBOL_KEYS) is not None:
            header_idx = i
            break
    headers = rows[header_idx]
    body = rows[header_idx + 1:]

    ci_sym = _pick_col(headers, _SYMBOL_KEYS)
    ci_qty = _pick_col(headers, _QTY_KEYS)
    ci_price = _pick_col(headers, _PRICE_KEYS)
    ci_date = _pick_col(headers, _DATE_KEYS)
    ci_act = _pick_col(headers, _ACTION_KEYS)

    if ci_sym is None or ci_qty is None or ci_price is None:
        return {"holdings": [], "error":
                "Couldn't find Symbol / Quantity / Price columns. "
                "Expected headers like 'Symbol', 'Quantity', 'Avg Price'. "
                "Share a sample export and I'll map it exactly."}

    is_tradebook = ci_act is not None
    skipped = 0

    def cell(r, i):
        return r[i] if (i is not None and i < len(r)) else None

    if is_tradebook:
        # net buy/sell rows into current positions per symbol
        agg: Dict[str, Dict] = {}
        for r in body:
            sym = _clean_symbol(cell(r, ci_sym))
            qty = _to_float(cell(r, ci_qty))
            price = _to_float(cell(r, ci_price))
            act = (str(cell(r, ci_act) or "")).strip().lower()
            if not sym or qty is None or price is None:
                skipped += 1
                continue
            side = 1 if act.startswith("b") else (-1 if act.startswith("s") else 0)
            if side == 0:
                skipped += 1
                continue
            a = agg.setdefault(sym, {"net": 0.0, "buy_qty": 0.0, "buy_val": 0.0,
                                     "first_date": ""})
            a["net"] += side * qty
            if side == 1:
                a["buy_qty"] += qty
                a["buy_val"] += qty * price
                d = _norm_date(cell(r, ci_date))
                if d and (not a["first_date"] or d < a["first_date"]):
                    a["first_date"] = d
        holdings = []
        for sym, a in agg.items():
            if a["net"] <= 0 or a["buy_qty"] <= 0:
                continue  # fully sold / net flat
            holdings.append({
                "symbol": sym, "qty": round(a["net"], 4),
                "avg_price": round(a["buy_val"] / a["buy_qty"], 2),
                "buy_date": a["first_date"],
            })
        return {"holdings": holdings, "kind": "tradebook", "skipped": skipped}

    # holdings sheet: one row per stock
    holdings = []
    for r in body:
        sym = _clean_symbol(cell(r, ci_sym))
        qty = _to_float(cell(r, ci_qty))
        price = _to_float(cell(r, ci_price))
        if not sym or qty is None or price is None or qty == 0:
            skipped += 1
            continue
        holdings.append({"symbol": sym, "qty": qty, "avg_price": price,
                         "buy_date": _norm_date(cell(r, ci_date))})
    return {"holdings": holdings, "kind": "holdings", "skipped": skipped}


def add_many(holdings: List[Dict]) -> int:
    """Add a list of {symbol, qty, avg_price[, buy_date]} dicts (e.g. from a broker)."""
    added = 0
    with _LOCK:
        rows = _load()
        for h in holdings:
            sym = (h.get("symbol") or "").strip().upper()
            if not sym:
                continue
            rows.append({
                "id": uuid.uuid4().hex[:10], "symbol": sym,
                "name": universe.get_meta(sym).get("name", sym),
                "qty": float(h["qty"]), "avg_price": float(h["avg_price"]),
                "buy_date": h.get("buy_date", ""), "broker": h.get("broker", ""),
                "note": "", "created": _now_iso(),
            })
            added += 1
        _save(rows)
    return added


def import_text(text: str) -> Dict:
    parsed = parse_rows(text)
    if parsed.get("error"):
        return {"ok": False, "error": parsed["error"]}
    added = 0
    with _LOCK:
        rows = _load()
        for h in parsed["holdings"]:
            rows.append({
                "id": uuid.uuid4().hex[:10],
                "symbol": h["symbol"],
                "name": universe.get_meta(h["symbol"]).get("name", h["symbol"]),
                "qty": h["qty"], "avg_price": h["avg_price"],
                "buy_date": h.get("buy_date", ""), "broker": "", "note": "",
                "created": _now_iso(),
            })
            added += 1
        _save(rows)
    return {"ok": True, "added": added, "kind": parsed.get("kind"),
            "skipped": parsed.get("skipped", 0)}


# ---------------------------------------------------------------------------
# Per-holding analysis
# ---------------------------------------------------------------------------
def _entry_quality(df: pd.DataFrame, buy_date: str):
    if not buy_date or df is None or df.empty:
        return None
    try:
        bd = pd.Timestamp(buy_date, tz=df.index.tz)
    except Exception:
        return None
    sub = df[df.index.normalize() <= bd]
    if len(sub) < 60:
        return None
    snap = indicators.compute(sub)
    if not snap.get("ok"):
        return None
    rsi, sma50, sma200 = snap.get("rsi14"), snap.get("sma50"), snap.get("sma200")
    p = snap.get("price")
    notes, quality = [], "fair"
    if rsi is not None and rsi > 72:
        notes.append(f"RSI was {rsi:.0f} — overbought"); quality = "stretched"
    if sma200 and p and p < sma200:
        notes.append("bought below the 200-DMA (downtrend)"); quality = "risky"
    if sma50 and p and (p - sma50) / sma50 * 100 > 15:
        notes.append(f"{(p - sma50) / sma50 * 100:.0f}% above the 50-DMA — extended"); quality = "stretched"
    if not notes and sma200 and p and p > sma200:
        notes.append("bought in an uptrend (above the 200-DMA)"); quality = "good"
    return {"quality": quality, "price_at_buy": p, "rsi_at_buy": rsi, "notes": notes}


def _verdict(action: str, pnl_pct: float, stop: Optional[float]):
    """Plain-English guidance from current signal + your P&L."""
    s = f" Suggested stop-loss ₹{stop}." if stop else ""
    if action in ("BUY",):
        return {"tone": "good", "text": "Hold / consider adding — signals are still bullish." + s}
    if action == "HOLD":
        return {"tone": "neutral", "text": "Hold — trend is neutral; protect with the stop." + s}
    # SELL / STRONG SELL
    if pnl_pct is not None and pnl_pct > 0:
        return {"tone": "warn", "text": "Momentum is fading while you're in profit — consider booking some / trailing the stop up." + s}
    return {"tone": "bad", "text": "Weak trend and underwater — consider cutting the loss at the stop." + s}


def analyze_holding(h: Dict, horizon: str = "long") -> Dict:
    sym = h["symbol"]
    # fetch enough history to cover the buy date for entry-quality
    period = "2y"
    if h.get("buy_date"):
        try:
            days = (_today() - pd.to_datetime(h["buy_date"]).date()).days
            period = "2y" if days < 500 else ("5y" if days < 1700 else "max")
        except Exception:
            period = "2y"

    df, meta = data.get_history_meta(sym, period, "1d")
    out = dict(h)
    out["invested"] = round(h["qty"] * h["avg_price"], 2)

    if df is None or df.empty:
        out.update({"ok": False, "error": "No live data for this symbol.",
                    "current_price": None, "pnl": None, "pnl_pct": None,
                    "verdict": {"tone": "neutral", "text": "Couldn't fetch live data — check the symbol."}})
        return out

    fundamentals = data.get_fundamentals(sym)
    live = meta.get("regularMarketPrice") or float(df["Close"].iloc[-1])
    res = scoring.analyze(sym, df, fundamentals, horizon,
                          universe.get_meta(sym), None, live)
    # A holder is LONG, so always use long-protective levels (stop BELOW price,
    # target ABOVE) regardless of which way the signal points.
    levels = scoring._levels("BUY", res.get("indicators") or {}) or {}
    current_value = round(h["qty"] * live, 2)
    pnl = round(current_value - out["invested"], 2)
    pnl_pct = round((live / h["avg_price"] - 1) * 100, 2) if h["avg_price"] else None

    out.update({
        "ok": True,
        "name": res.get("name") or h.get("name") or sym,
        "sector": res.get("sector"),
        "current_price": round(live, 2),
        "current_value": current_value,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
        "score": res.get("score"),
        "action": res.get("action"),
        "label": res.get("label"),
        "stop_loss": levels.get("stop_loss"),
        "target": levels.get("target1"),
        "summary": res.get("summary"),
        "verdict": _verdict(res.get("action"), pnl_pct, levels.get("stop_loss")),
        "entry_quality": _entry_quality(df, h.get("buy_date")),
    })
    return out


def analyze_portfolio(horizon: str = "long") -> Dict:
    rows = _load()
    analyzed = [analyze_holding(h, horizon) for h in rows]
    invested = sum(a.get("invested") or 0 for a in analyzed)
    current = sum((a.get("current_value") or a.get("invested") or 0) for a in analyzed)
    pnl = round(current - invested, 2)
    return {
        "holdings": analyzed,
        "summary": {
            "count": len(analyzed),
            "invested": round(invested, 2),
            "current_value": round(current, 2),
            "pnl": pnl,
            "pnl_pct": round(pnl / invested * 100, 2) if invested else None,
        },
        "as_of": _now_iso(),
    }
