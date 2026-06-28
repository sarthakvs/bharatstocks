"""OPTIONAL live broker import (Kotak Neo, ICICI Direct Breeze).

This is an opt-in convenience. It is NOT required — CSV/paste import covers the
same ground with zero credentials. Use it only if you've registered for your
broker's developer API.

⚠️  SECURITY: broker API keys/sessions can PLACE TRADES. This module only ever
calls read-only holdings methods, and the credentials you pass are used once and
never stored. Still: run this LOCALLY, never put broker keys on a shared/cloud
host, and never commit them.

The SDKs are imported lazily, so the app runs fine without them installed:
    pip install neo-api-client      # Kotak Neo
    pip install breeze-connect      # ICICI Direct
"""
from __future__ import annotations

from typing import Dict, List

# Field-name heuristics — broker schemas vary, so map flexibly (no assumptions).
_SYM_KEYS = ("tradingsymbol", "displaysymbol", "stock_code", "symbol", "scrip", "stockcode", "stock")
_QTY_KEYS = ("quantity", "qty", "netqty", "holdingquantity", "totalqty")
_PRICE_KEYS = ("averageprice", "average_price", "avgprice", "avg_price", "buyavg", "averagecost", "price")


def _get(rec: Dict, keys) -> object:
    low = {str(k).lower().replace(" ", ""): v for k, v in rec.items()}
    for k in keys:
        if k in low and low[k] not in (None, "", 0, "0"):
            return low[k]
    return None


def _to_float(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def normalize(records: List[Dict]) -> List[Dict]:
    """Map raw broker holding dicts to {symbol, qty, avg_price}."""
    out = []
    for r in records or []:
        if not isinstance(r, dict):
            continue
        sym = _get(r, _SYM_KEYS)
        qty = _to_float(_get(r, _QTY_KEYS))
        avg = _to_float(_get(r, _PRICE_KEYS))
        if not sym or not qty or avg is None:
            continue
        s = str(sym).strip().upper()
        for suf in (".NS", ".BO", "-EQ"):
            if s.endswith(suf):
                s = s[: -len(suf)]
        out.append({"symbol": s.strip(), "qty": qty, "avg_price": avg})
    return out


def available() -> Dict[str, bool]:
    def has(mod):
        try:
            __import__(mod)
            return True
        except Exception:
            return False
    return {"kotak": has("neo_api_client"), "icici": has("breeze_connect")}


# ---------------------------------------------------------------------------
# ICICI Direct — Breeze  (cleanest: 3 params)
# ---------------------------------------------------------------------------
def import_icici(api_key: str, api_secret: str, session_token: str) -> List[Dict]:
    """Fetch ICICI Direct holdings via Breeze.
    Get session_token from https://api.icicidirect.com/apiuser/login?api_key=YOUR_KEY
    """
    from breeze_connect import BreezeConnect  # lazy
    breeze = BreezeConnect(api_key=api_key)
    breeze.generate_session(api_secret=api_secret, session_token=session_token)
    resp = breeze.get_portfolio_holdings(
        exchange_code="NSE", from_date="", to_date="", stock_code="", portfolio_type="")
    records = resp.get("Success") if isinstance(resp, dict) else resp
    return normalize(records or [])


# ---------------------------------------------------------------------------
# Kotak Neo  (v2 SDK — TOTP based)
# ---------------------------------------------------------------------------
def _raise_if_error(resp, what: str):
    """Best-effort: surface a broker error message from a response dict."""
    if isinstance(resp, dict):
        for k in ("error", "emsg", "errMsg", "errorMessage", "message"):
            v = resp.get(k)
            if v and str(v).strip() and str(resp.get("stat", "")).lower() != "ok":
                raise RuntimeError(f"{what}: {v}")
        if str(resp.get("stat", "")).lower() in ("not_ok", "error"):
            raise RuntimeError(f"{what}: {resp.get('emsg') or resp}")


def import_kotak(consumer_key: str, mobile_number: str = "", ucc: str = "",
                 totp: str = "", mpin: str = "") -> List[Dict]:
    """Fetch Kotak Neo holdings via the v2 SDK's TOTP flow.

    Steps (see https://github.com/Kotak-Neo/Kotak-neo-api-v2):
      NeoAPI(consumer_key=…) → totp_login(mobile_number|ucc, totp) → totp_validate(mpin) → holdings()

    One-time setup: get the Consumer Key from the Kotak Neo app (Invest → Trade
    API) and register TOTP at kotaksecurities.com. `totp` is the current 6-digit
    code from your authenticator; `mpin` is your trading MPIN.
    """
    from neo_api_client import NeoAPI  # lazy
    client = NeoAPI(environment="prod", access_token=None, neo_fin_key=None,
                    consumer_key=consumer_key)
    _raise_if_error(client.totp_login(mobile_number=mobile_number or None,
                                      ucc=ucc or None, totp=totp), "TOTP login failed")
    _raise_if_error(client.totp_validate(mpin=mpin), "MPIN validation failed")

    resp = client.holdings()
    _raise_if_error(resp, "Holdings fetch failed")
    records = resp.get("data") if isinstance(resp, dict) else resp
    normalized = normalize(records or [])
    if records and not normalized:
        keys = sorted({k for rec in records[:1] if isinstance(rec, dict) for k in rec})
        raise RuntimeError("Fetched holdings but couldn't map the columns. "
                           "Found fields: " + ", ".join(keys) + ". Send these to tune mapping.")
    return normalized
