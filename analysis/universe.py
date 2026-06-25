"""Stock universe: the bundled list of NSE symbols used for fast search
autocomplete and for the "top stocks" scan.

The list is reference metadata only (company name + ticker + sector). All
market data (prices, returns, indicators) is fetched live elsewhere -- nothing
here is assumed about price or performance.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import List, Dict

_DATA_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "nse_stocks.json")


@lru_cache(maxsize=1)
def load_universe() -> List[Dict]:
    """Load and normalise the bundled stock universe."""
    with open(_DATA_FILE, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    out = []
    for row in raw:
        out.append(
            {
                "symbol": row["symbol"].upper(),
                "name": row.get("name", row["symbol"]),
                "sector": row.get("sector", "-"),
                "indices": row.get("indices", []),
            }
        )
    return out


@lru_cache(maxsize=1)
def _symbol_index() -> Dict[str, Dict]:
    return {row["symbol"]: row for row in load_universe()}


def get_meta(symbol: str) -> Dict:
    """Return bundled metadata for a symbol, or a stub if unknown."""
    symbol = symbol.upper()
    return _symbol_index().get(
        symbol, {"symbol": symbol, "name": symbol, "sector": "-", "indices": []}
    )


def scan_symbols(universe: str = "nifty50") -> List[str]:
    """Return the list of symbols to scan for the 'top stocks' feature.

    universe: 'nifty50' (default, fast) | 'nifty100' | 'all'
    """
    rows = load_universe()
    if universe == "all":
        return [r["symbol"] for r in rows]
    return [r["symbol"] for r in rows if universe in r["indices"]] or [
        r["symbol"] for r in rows
    ]


def search_local(query: str, limit: int = 12) -> List[Dict]:
    """Search the bundled universe by symbol or company name.

    Ranking: exact symbol > symbol prefix > name prefix > substring match.
    """
    q = (query or "").strip().upper()
    if not q:
        return []
    scored = []
    for row in load_universe():
        sym = row["symbol"]
        name = row["name"].upper()
        score = None
        if sym == q:
            score = 0
        elif sym.startswith(q):
            score = 1
        elif name.startswith(q):
            score = 2
        elif q in sym:
            score = 3
        elif q in name:
            score = 4
        if score is not None:
            scored.append((score, len(sym), row))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [r for _, _, r in scored[:limit]]
