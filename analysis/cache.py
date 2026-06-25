"""Tiny thread-safe TTL cache shared across the data layer."""
from __future__ import annotations

import threading
import time

_CACHE = {}
_LOCK = threading.Lock()


def cache_get(key: str, ttl: float):
    with _LOCK:
        item = _CACHE.get(key)
        if item is None:
            return None
        ts, value = item
        if time.time() - ts > ttl:
            _CACHE.pop(key, None)
            return None
        return value


def cache_set(key: str, value) -> None:
    with _LOCK:
        _CACHE[key] = (time.time(), value)


def cache_clear() -> None:
    with _LOCK:
        _CACHE.clear()
