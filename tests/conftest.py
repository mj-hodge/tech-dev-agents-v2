"""Shared pytest configuration and custom marks."""

import asyncio
import functools

import httpx
import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "smoke: smoke tests for pre-deploy gate validation")
    config.addinivalue_line("markers", "integration: integration tests requiring real external services")
    config.addinivalue_line("markers", "slow: slow tests that may be skipped in fast CI runs")


# ---------------------------------------------------------------------------
# httpx compat: AsyncClient(app=...) was removed in httpx >=0.23.
# Restore it transparently so pre-written tests continue to work.
# ---------------------------------------------------------------------------

_original_async_client_init = httpx.AsyncClient.__init__


@functools.wraps(_original_async_client_init)
def _patched_async_client_init(self, *args, app=None, **kwargs):
    if app is not None:
        kwargs.setdefault("transport", httpx.ASGITransport(app=app))
    _original_async_client_init(self, *args, **kwargs)


httpx.AsyncClient.__init__ = _patched_async_client_init  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def _ensure_event_loop():
    """Ensure asyncio.get_event_loop() works in synchronous test methods.

    Python 3.12 / pytest-asyncio 0.21+ may leave the event loop unset between
    tests. Sync tests that call asyncio.get_event_loop().run_until_complete()
    (e.g. Q3 unit tests) fail with RuntimeError if there is no current loop.
    This autouse fixture creates and sets a loop for each test, then closes
    it after the test completes.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    try:
        loop.close()
    except Exception:
        pass
    asyncio.set_event_loop(None)
