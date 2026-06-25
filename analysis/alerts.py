"""Price alerts: notify (by email) when a stock hits its target or stop-loss.

Storage is a small JSON file (data/alerts.json). A background thread polls the
live price every few minutes and emails when a level is crossed; you can also
trigger a check via the /api/alerts/check endpoint (handy on cloud hosts that
sleep — point a free cron service at it).

Email uses SMTP configured via environment variables (see send_email). SMS is
intentionally not built in — every reliable SMS gateway (Twilio etc.) is paid;
email is the free, dependable channel.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import smtplib
import threading
import uuid
from email.message import EmailMessage
from typing import Dict, List, Optional

from . import yahoo

_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "alerts.json")
_LOCK = threading.RLock()
_SCHED_STARTED = False


def _now() -> str:
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    return dt.datetime.now(ist).strftime("%d %b %Y, %I:%M %p IST")


def _load() -> List[Dict]:
    with _LOCK:
        if not os.path.exists(_FILE):
            return []
        try:
            with open(_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return []


def _save(alerts: List[Dict]) -> None:
    with _LOCK:
        os.makedirs(os.path.dirname(_FILE), exist_ok=True)
        tmp = _FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(alerts, fh, indent=2)
        os.replace(tmp, _FILE)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def list_alerts() -> List[Dict]:
    return sorted(_load(), key=lambda a: a.get("created", ""), reverse=True)


def create_alert(symbol: str, target: Optional[float], stop: Optional[float],
                 email: str, direction: str = "long", name: str = "",
                 note: str = "") -> Dict:
    alert = {
        "id": uuid.uuid4().hex[:10],
        "symbol": symbol.upper(),
        "name": name or symbol.upper(),
        "target": float(target) if target not in (None, "") else None,
        "stop": float(stop) if stop not in (None, "") else None,
        "email": email.strip(),
        "direction": "short" if direction == "short" else "long",
        "note": note,
        "created": _now(),
        "status": "active",
        "triggered_kind": None,
        "triggered_price": None,
        "triggered_at": None,
    }
    with _LOCK:
        alerts = _load()
        alerts.append(alert)
        _save(alerts)
    return alert


def delete_alert(alert_id: str) -> bool:
    with _LOCK:
        alerts = _load()
        new = [a for a in alerts if a["id"] != alert_id]
        if len(new) == len(alerts):
            return False
        _save(new)
        return True


# ---------------------------------------------------------------------------
# Checking + email
# ---------------------------------------------------------------------------
def _crossed(alert: Dict, price: float):
    """Return 'target' | 'stop' | None for the given live price."""
    tgt, stp, d = alert.get("target"), alert.get("stop"), alert.get("direction", "long")
    if d == "short":
        if tgt is not None and price <= tgt:
            return "target"
        if stp is not None and price >= stp:
            return "stop"
    else:  # long
        if tgt is not None and price >= tgt:
            return "target"
        if stp is not None and price <= stp:
            return "stop"
    return None


def check_alerts() -> Dict:
    """Check all active alerts against live prices; email + mark any that fire.
    Returns a small summary dict."""
    alerts = _load()
    active = [a for a in alerts if a.get("status") == "active"]
    if not active:
        return {"checked": 0, "triggered": 0}

    symbols = sorted({a["symbol"] for a in active})
    meta_map = yahoo.batch_chart_meta(symbols, rng="5d", interval="1d", ttl=20)

    fired = 0
    with _LOCK:
        alerts = _load()  # reload under lock to avoid clobbering concurrent edits
        for a in alerts:
            if a.get("status") != "active":
                continue
            pair = meta_map.get(a["symbol"])
            if not pair:
                continue
            price = (pair[1] or {}).get("regularMarketPrice")
            if price is None:
                continue
            kind = _crossed(a, float(price))
            if kind:
                a["status"] = "triggered"
                a["triggered_kind"] = kind
                a["triggered_price"] = round(float(price), 2)
                a["triggered_at"] = _now()
                fired += 1
                try:
                    _email_alert(a)
                    a["email_sent"] = True
                except Exception as e:
                    a["email_sent"] = False
                    a["email_error"] = str(e)[:160]
        if fired:
            _save(alerts)
    return {"checked": len(active), "triggered": fired}


def _email_alert(alert: Dict) -> None:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", 587))
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASS")
    sender = os.environ.get("ALERT_FROM", user)
    to = alert.get("email") or os.environ.get("ALERT_TO")
    if not (user and pwd and to):
        raise RuntimeError("SMTP not configured (set SMTP_USER, SMTP_PASS, recipient email)")

    kind = alert["triggered_kind"].upper()
    sym = alert["symbol"]
    price = alert["triggered_price"]
    level = alert["target"] if alert["triggered_kind"] == "target" else alert["stop"]

    msg = EmailMessage()
    emoji = "🎯" if alert["triggered_kind"] == "target" else "🛑"
    msg["Subject"] = f"{emoji} {sym} hit {kind} — ₹{price}"
    msg["From"] = sender
    msg["To"] = to
    body = (
        f"{sym} ({alert.get('name','')}) has hit its {kind}.\n\n"
        f"  Live price : ₹{price}\n"
        f"  {kind} level: ₹{level}\n"
        f"  Direction  : {alert.get('direction','long')}\n"
        f"  Time       : {alert['triggered_at']}\n"
    )
    if alert.get("note"):
        body += f"  Note       : {alert['note']}\n"
    body += "\n— BharatStocks (educational alert, not investment advice)."
    msg.set_content(body)

    with smtplib.SMTP(host, port, timeout=20) as s:
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg)


def email_configured() -> bool:
    return bool(os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASS"))


# ---------------------------------------------------------------------------
# Background scheduler
# ---------------------------------------------------------------------------
def start_scheduler(interval: int = 300) -> None:
    """Start a daemon thread that checks alerts every `interval` seconds.
    Safe to call once at app startup; no-op if already started."""
    global _SCHED_STARTED
    with _LOCK:
        if _SCHED_STARTED:
            return
        _SCHED_STARTED = True

    stop_evt = threading.Event()

    def _loop():
        while not stop_evt.wait(interval):
            try:
                check_alerts()
            except Exception:
                pass

    t = threading.Thread(target=_loop, name="alert-scheduler", daemon=True)
    t.start()
