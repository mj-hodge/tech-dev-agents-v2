"""
src/cache/cache_first.py — @cache_first decorator for 3-tier read caching.

STORY-095: Eliminates per-tool inline cache logic with a single decorator.

Architecture:
    1. In-memory TtlCache → return on hit
    2. PostgreSQL api_entity_cache → return on hit (populate in-memory)
    3. Call wrapped function (live API) on miss
    4. Write result to DB + in-memory
    5. Apply client-side filtering (filter_fields) and pagination (offset/limit)
    6. Return response with source metadata

Registry:
    _cache_first_registry — dict mapping qualified function paths to True.
    Each decorated function gets a `_is_cache_first = True` attribute.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
from typing import Any, Callable

import structlog

_logger = structlog.stdlib.get_logger(__name__)

# Registry: maps "src.tools.sp_tools.sp_list_campaigns" -> True
_cache_first_registry: dict[str, bool] = {}

# DB handle — injected at server startup (same pattern as tool modules)
db: Any = None


def _compute_params_hash(kwargs: dict, cache_params_from: list[str]) -> str:
    """SHA-256 based hash of the selected kwargs for cache key differentiation."""
    if not cache_params_from:
        return "none"
    params = {k: kwargs.get(k) for k in sorted(cache_params_from)}
    # Filter out None values to match "no param" == "none"
    filtered = {k: v for k, v in params.items() if v is not None}
    if not filtered:
        return "none"
    serialized = json.dumps(filtered, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def cache_first(
    operation: str,
    result_key: str,
    cache_params_from: list[str] | None = None,
    filter_fields: dict[str, str] | None = None,
    datalake: bool = False,
    ttl_seconds: int = 1800,
) -> Callable:
    """Decorator that adds 3-tier cache-first behavior to a GET tool function.

    Args:
        operation: Cache operation name (e.g., "sp_list_campaigns").
        result_key: Key in the response dict that holds the list data (e.g., "campaigns").
        cache_params_from: List of kwarg names that differentiate cache entries.
        filter_fields: Dict mapping tool param name -> API field name for client-side filtering.
            e.g., {"campaign_id": "campaignId"} means filter cached data where
            item["campaignId"] == kwargs["campaign_id"].
        datalake: If True, also persist to datalake table on live fetch.
        ttl_seconds: Cache TTL in seconds (default 30 min).
    """
    _cache_params_from = cache_params_from or []

    def decorator(fn: Callable) -> Callable:
        # Register the function
        module = fn.__module__
        qualname = f"{module}.{fn.__name__}"
        _cache_first_registry[qualname] = True

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> dict:
            from src.cache.ttl_cache import api_cache

            # Extract standard params
            profile_id = kwargs.get("profile_id") or (args[0] if args else "")
            limit = kwargs.get("limit", 50)
            offset = kwargs.get("offset", 0)
            effective_limit = min(limit, 500)

            # Get user_id from the tool module's pattern
            user_id = _get_user_id_safe()

            # Compute params hash from specified kwargs
            params_hash = _compute_params_hash(kwargs, _cache_params_from)
            # Build cache_params for TtlCache key — filter out None values
            # so that (status_filter=None) matches (no params) in the cache key
            if _cache_params_from:
                raw_params = {k: kwargs.get(k) for k in _cache_params_from}
                cache_params = {k: v for k, v in raw_params.items() if v is not None} or None
            else:
                cache_params = None

            # ── Tier 1: In-memory TtlCache ──
            hit, cached_data = await api_cache.async_get(
                user_id, profile_id, operation, cache_params
            )
            if hit and cached_data is not None:
                items = cached_data if isinstance(cached_data, list) else cached_data
                age = api_cache.cache_age(user_id, profile_id, operation, cache_params) or 0.0
                return _build_response(
                    items, result_key, offset, effective_limit,
                    source="memory_cache", age=age,
                    filter_fields=filter_fields, kwargs=kwargs,
                )

            # ── Tier 2: PostgreSQL api_entity_cache ──
            db_ref = db or _get_db_from_tools()
            if db_ref is not None:
                try:
                    from src.services.entity_cache_service import read_entity
                    db_hit, db_data, db_age = await read_entity(
                        db_ref, profile_id, operation, params_hash
                    )
                    if db_hit and db_data is not None:
                        # Populate in-memory cache
                        await api_cache.async_set(
                            user_id, profile_id, operation, db_data, cache_params,
                            ttl=float(ttl_seconds),
                        )
                        return _build_response(
                            db_data, result_key, offset, effective_limit,
                            source="db_cache", age=db_age,
                            filter_fields=filter_fields, kwargs=kwargs,
                        )
                except Exception:
                    _logger.exception("cache_first_db_read_error operation=%s", operation)

            # ── STORY-401 note: cache-only gating was previously here but is
            # now enforced exclusively at the server level (_rfc7807_tool in
            # server.py). Removing the decorator-level gate ensures that:
            #   1. Auth/ownership checks inside the wrapped function always run
            #      (DA-05/06 security invariant)
            #   2. Direct function calls (tests, internal code) execute the full
            #      function body — BIZ-7 empty-result tests and hardening tests
            #      can verify inner-function behavior.
            # See STORY-551 analysis.md for full rationale.

            # ── Tier 3: Live API call ──
            # Rate limiting is handled inside the wrapped function body,
            # AFTER auth/ownership checks (DA-06 invariant).
            result = await fn(*args, **kwargs)

            # Extract the list data from the live result
            if isinstance(result, dict) and result_key in result:
                live_data = result[result_key]
            elif isinstance(result, list):
                live_data = result
            else:
                # If result contains an error, pass through
                if isinstance(result, dict) and "error" in result:
                    return result
                live_data = result.get(result_key, []) if isinstance(result, dict) else []

            # Never cache empty results — prevents poisoning the cache with zeros
            if not live_data:
                return _build_response(
                    live_data, result_key, offset, effective_limit,
                    source="live_api", age=0.0,
                    filter_fields=filter_fields, kwargs=kwargs,
                )

            # Write to in-memory
            await api_cache.async_set(
                user_id, profile_id, operation, live_data, cache_params,
                ttl=float(ttl_seconds),
            )

            # Write to DB
            if db_ref is not None:
                try:
                    from src.services.entity_cache_service import write_entity
                    await write_entity(
                        db_ref, profile_id, operation, live_data,
                        params_hash=params_hash, ttl_seconds=ttl_seconds,
                    )
                except Exception:
                    _logger.exception("cache_first_db_write_error operation=%s", operation)

            # Datalake export (opt-in)
            if datalake and db_ref is not None:
                try:
                    await _write_datalake(db_ref, operation, live_data)
                except Exception:
                    _logger.exception("cache_first_datalake_error operation=%s", operation)

            return _build_response(
                live_data, result_key, offset, effective_limit,
                source="live_api", age=0.0,
                filter_fields=filter_fields, kwargs=kwargs,
            )

        # Mark as cache-first for meta-test
        wrapper._is_cache_first = True  # type: ignore[attr-defined]
        return wrapper

    return decorator


def _build_response(
    data: Any,
    result_key: str,
    offset: int,
    limit: int,
    source: str,
    age: float,
    filter_fields: dict[str, str] | None = None,
    kwargs: dict | None = None,
) -> dict:
    """Build a standardized response with pagination and filtering."""
    items = data if isinstance(data, list) else []

    # Apply client-side filtering
    if filter_fields and kwargs:
        for param_name, api_field in filter_fields.items():
            filter_value = kwargs.get(param_name)
            if filter_value is not None:
                items = [
                    item for item in items
                    if str(item.get(api_field, "")) == str(filter_value)
                ]

    total_count = len(items)
    page = items[offset: offset + limit]

    return {
        result_key: page,
        "total_count": total_count,
        "source": source,
        "cached": source != "live_api",
        "cache_age_seconds": round(age, 1),
    }


def _get_user_id_safe() -> int | None:
    """Get user_id without importing tool modules at module load time."""
    try:
        from src.auth.helpers import get_user_id_from_context
        return get_user_id_from_context()
    except (ImportError, RuntimeError):
        return None


def _get_db_from_tools() -> Any:
    """Try to get the db reference from tool modules if not set on this module."""
    # Try common tool modules that have db injected
    for mod_path in ("src.tools.sp_tools", "src.tools.portfolio_tools", "src.tools.sb_tools"):
        try:
            import importlib
            mod = importlib.import_module(mod_path)
            _db = getattr(mod, "db", None)
            if _db is not None:
                return _db
        except Exception:
            pass
    return None


def _get_rate_limiter_from_tools(fn: Callable) -> Any:
    """Resolve the rate_limiter from the tool module that owns the wrapped function."""
    try:
        import importlib
        mod = importlib.import_module(fn.__module__)
        rl = getattr(mod, "rate_limiter", None)
        return rl
    except Exception:
        return None


def _is_stub_backed(dep: Any) -> bool:
    """True when the dependency (or its redis) is an uninitialized stub."""
    redis = getattr(dep, "redis", None)
    return redis is not None and redis.__class__.__name__ == "_UninitializedStub"


async def _write_datalake(db: Any, operation: str, data: list) -> None:
    """Placeholder for datalake export — implemented per-operation as needed."""
    # Future: route to appropriate datalake table based on operation
    pass
