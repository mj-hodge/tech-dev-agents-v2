"""Tests for TTLCache — T56-T59."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from tech_dev_agents.ops_console.cache import TTLCache


class TestTTLCache:
    """T56-T59: In-memory TTL cache behaviour."""

    def test_cache_set_and_get(self):
        """T56: set(key, value) followed by get(key) returns value within TTL."""
        cache = TTLCache(ttl_seconds=60)
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_cache_expired_returns_none(self):
        """T57: get(key) returns None after TTL has elapsed (uses time mock)."""
        cache = TTLCache(ttl_seconds=5)
        cache.set("key1", "value1")

        # Simulate time passing beyond TTL
        original_time = time.time
        with patch("tech_dev_agents.ops_console.cache.time") as mock_time:
            mock_time.time.return_value = original_time() + 10  # 10s > 5s TTL
            result = cache.get("key1")

        assert result is None

    def test_cache_overwrite(self):
        """T58: set(key, new_value) overwrites previous value and resets TTL."""
        cache = TTLCache(ttl_seconds=60)
        cache.set("key1", "old_value")
        cache.set("key1", "new_value")
        assert cache.get("key1") == "new_value"

    def test_cache_different_keys_independent(self):
        """T59: Different keys have independent TTLs and values."""
        cache = TTLCache(ttl_seconds=60)
        cache.set("key1", "value1")
        cache.set("key2", "value2")

        assert cache.get("key1") == "value1"
        assert cache.get("key2") == "value2"

        # Expire only key1 by mocking time for key1's entry
        cache._store["key1"] = ("value1", time.time() - 100)
        assert cache.get("key1") is None
        assert cache.get("key2") == "value2"
