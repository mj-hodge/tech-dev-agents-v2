"""Contract test conftest — marker registration and shared fixtures.

STORY-544: Registers the contract_critical pytest marker and provides
the no_network safety fixture to prevent accidental real I/O.
"""
import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "contract_critical: marks tests as contract-critical invariant checks (STORY-544)",
    )


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Prevent accidental real network I/O in contract tests.

    Patches socket.socket to block AF_INET/AF_INET6 connections while
    allowing AF_UNIX and internal socketpair (needed by asyncio event loop).
    This is defense-in-depth — primary determinism comes from mock injection.
    """
    import socket

    _original_socket = socket.socket

    class _GuardedSocket(_original_socket):
        def __init__(self, family=-1, type=-1, proto=-1, fileno=None):
            # Allow AF_UNIX and internal sockets (asyncio uses socketpair)
            if family in (socket.AF_INET, socket.AF_INET6):
                raise RuntimeError(
                    "Contract tests must not make real network calls. "
                    "Use mock fixtures from tests/contracts/fixtures/."
                )
            super().__init__(family, type, proto, fileno)

    monkeypatch.setattr(socket, "socket", _GuardedSocket)
