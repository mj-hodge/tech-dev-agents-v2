"""Simple in-memory TTL cache for the ops console."""

from __future__ import annotations

import time
from typing import Any


class TTLCache:
    """Simple in-memory TTL cache."""

    def __init__(self, ttl_seconds: float, maxsize: int = 128):
        self._ttl = ttl_seconds
        self.maxsize = maxsize
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        if key in self._store:
            value, cached_at = self._store[key]
            if time.time() - cached_at < self._ttl:
                return value
            del self._store[key]
        return None

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (value, time.time())

    def clear(self) -> None:
        self._store.clear()
