"""Tests for STORY-010: Teams Webhook Auth Hardening & Replay Protection.

Phase 7 — Test Design (RED state until Phase 8 implementation).
"""

import time

import pytest

from tech_dev_agents.webhook_auth import (
    AuthConfig,
    AuthResult,
    ReplayCache,
    TokenClaims,
    authenticate_webhook,
    build_default_auth_config,
    build_token_claims,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

DEFAULT_ISSUER = "https://api.botframework.com"
DEFAULT_AUDIENCE = "test-bot-app-id"
DEFAULT_NOW = 1_700_000_000.0  # Arbitrary fixed timestamp


class FakeClock:
    """Injectable clock for deterministic testing."""

    def __init__(self, now: float = DEFAULT_NOW) -> None:
        self._now = now

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def make_test_claims(
    *,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
    expiration: float | None = None,
    issued_at: float | None = None,
    token_id: str = "test-jti-001",
    service_url: str | None = None,
) -> TokenClaims:
    """Build TokenClaims for testing."""
    return TokenClaims(
        issuer=issuer,
        audience=audience,
        expiration=expiration if expiration is not None else DEFAULT_NOW + 3600,
        issued_at=issued_at if issued_at is not None else DEFAULT_NOW - 60,
        token_id=token_id,
        service_url=service_url,
        raw_claims={
            "iss": issuer,
            "aud": audience,
            "exp": expiration if expiration is not None else DEFAULT_NOW + 3600,
            "iat": issued_at if issued_at is not None else DEFAULT_NOW - 60,
            "jti": token_id,
        },
    )


def make_fake_verifier(
    claims: TokenClaims | None = None,
    error: Exception | None = None,
):
    """Create a fake token verifier callback."""

    def verifier(token: str) -> TokenClaims | None:
        if error is not None:
            raise error
        return claims

    return verifier


def make_config(
    bot_app_id: str = DEFAULT_AUDIENCE,
    **overrides,
) -> AuthConfig:
    """Build an AuthConfig for testing."""
    defaults = {
        "bot_app_id": bot_app_id,
        "allowed_issuers": (DEFAULT_ISSUER, "https://login.microsoftonline.com/botframework.com/v2.0"),
        "clock_skew_seconds": 300,
        "replay_window_seconds": 300,
        "max_replay_cache_size": 10_000,
    }
    defaults.update(overrides)
    return AuthConfig(**defaults)


# ---------------------------------------------------------------------------
# Group 1 — Happy Path
# ---------------------------------------------------------------------------


def test_valid_token_authenticates_successfully():
    """T01: Valid token with correct issuer, audience, and expiry authenticates."""
    clock = FakeClock()
    claims = make_test_claims()
    config = make_config()
    verifier = make_fake_verifier(claims=claims)

    result = authenticate_webhook(
        token="valid.jwt.token",
        config=config,
        token_verifier=verifier,
        clock=clock,
    )

    assert result.is_authenticated is True
    assert result.failure_reason is None
    assert result.claims is not None
    assert result.claims.issuer == DEFAULT_ISSUER
    assert result.claims.audience == DEFAULT_AUDIENCE


def test_verifier_returning_none_rejects_token():
    """T02: Verifier returning None means signature verification failed."""
    clock = FakeClock()
    config = make_config()
    verifier = make_fake_verifier(claims=None)

    result = authenticate_webhook(
        token="bad.jwt.token",
        config=config,
        token_verifier=verifier,
        clock=clock,
    )

    assert result.is_authenticated is False
    assert result.failure_reason is not None
    assert "signature" in result.failure_reason.lower()


# ---------------------------------------------------------------------------
# Group 2 — Claims Validation
# ---------------------------------------------------------------------------


def test_wrong_issuer_rejected():
    """T03: Token from an unauthorized issuer is rejected."""
    clock = FakeClock()
    claims = make_test_claims(issuer="https://evil.example.com")
    config = make_config()
    verifier = make_fake_verifier(claims=claims)

    result = authenticate_webhook(
        token="wrong.issuer.token",
        config=config,
        token_verifier=verifier,
        clock=clock,
    )

    assert result.is_authenticated is False
    assert "issuer" in result.failure_reason.lower()


def test_wrong_audience_rejected():
    """T04: Token with wrong audience (different app ID) is rejected."""
    clock = FakeClock()
    claims = make_test_claims(audience="wrong-app-id")
    config = make_config()
    verifier = make_fake_verifier(claims=claims)

    result = authenticate_webhook(
        token="wrong.audience.token",
        config=config,
        token_verifier=verifier,
        clock=clock,
    )

    assert result.is_authenticated is False
    assert "audience" in result.failure_reason.lower()


def test_expired_token_rejected():
    """T05: Token expired beyond clock skew tolerance is rejected."""
    clock = FakeClock()
    # Token expired 600 seconds ago, clock skew is 300 — should fail
    claims = make_test_claims(expiration=DEFAULT_NOW - 600)
    config = make_config(clock_skew_seconds=300)
    verifier = make_fake_verifier(claims=claims)

    result = authenticate_webhook(
        token="expired.jwt.token",
        config=config,
        token_verifier=verifier,
        clock=clock,
    )

    assert result.is_authenticated is False
    assert "expired" in result.failure_reason.lower()


def test_token_within_clock_skew_accepted():
    """T06: Token expired slightly but within clock skew is accepted."""
    clock = FakeClock()
    # Token expired 100 seconds ago, clock skew is 300 — should pass
    claims = make_test_claims(expiration=DEFAULT_NOW - 100)
    config = make_config(clock_skew_seconds=300)
    verifier = make_fake_verifier(claims=claims)

    result = authenticate_webhook(
        token="slightly.expired.token",
        config=config,
        token_verifier=verifier,
        clock=clock,
    )

    assert result.is_authenticated is True


# ---------------------------------------------------------------------------
# Group 3 — Replay Detection
# ---------------------------------------------------------------------------


def test_replay_detected_for_duplicate_token():
    """T07: Same token submitted twice within replay window is rejected."""
    clock = FakeClock()
    claims = make_test_claims(token_id="unique-jti-123")
    config = make_config()
    verifier = make_fake_verifier(claims=claims)
    cache = ReplayCache(window_seconds=300, max_size=100, clock=clock)

    # First submission — should succeed
    result1 = authenticate_webhook(
        token="token1",
        config=config,
        token_verifier=verifier,
        clock=clock,
        replay_cache=cache,
    )
    assert result1.is_authenticated is True

    # Second submission — same token_id — should be rejected as replay
    result2 = authenticate_webhook(
        token="token1",
        config=config,
        token_verifier=verifier,
        clock=clock,
        replay_cache=cache,
    )
    assert result2.is_authenticated is False
    assert "replay" in result2.failure_reason.lower()


def test_different_tokens_not_flagged_as_replay():
    """T08: Two different tokens are both accepted (not flagged as replay)."""
    clock = FakeClock()
    claims1 = make_test_claims(token_id="jti-aaa")
    claims2 = make_test_claims(token_id="jti-bbb")
    config = make_config()
    cache = ReplayCache(window_seconds=300, max_size=100, clock=clock)

    result1 = authenticate_webhook(
        token="token-a",
        config=config,
        token_verifier=make_fake_verifier(claims=claims1),
        clock=clock,
        replay_cache=cache,
    )
    result2 = authenticate_webhook(
        token="token-b",
        config=config,
        token_verifier=make_fake_verifier(claims=claims2),
        clock=clock,
        replay_cache=cache,
    )

    assert result1.is_authenticated is True
    assert result2.is_authenticated is True


# ---------------------------------------------------------------------------
# Group 4 — Replay Cache Mechanics
# ---------------------------------------------------------------------------


def test_replay_cache_evicts_expired_entries():
    """T09: Expired entries are removed from the cache."""
    clock = FakeClock()
    cache = ReplayCache(window_seconds=60, max_size=100, clock=clock)

    # Record some entries
    assert cache.check_and_record("token-1") is False
    assert cache.check_and_record("token-2") is False
    assert cache.size() == 2

    # Advance past the window
    clock.advance(120)

    # Trigger eviction by recording a new entry
    assert cache.check_and_record("token-3") is False

    # After eviction, old entries should be gone; token-3 should be present
    # Re-submitting token-1 should NOT be flagged (it was evicted)
    assert cache.check_and_record("token-1") is False


def test_replay_cache_respects_max_size():
    """T10: Cache does not grow beyond max_size."""
    clock = FakeClock()
    cache = ReplayCache(window_seconds=300, max_size=50, clock=clock)

    for i in range(100):
        cache.check_and_record(f"token-{i}")
        clock.advance(0.1)  # Small time advance to make entries unique

    assert cache.size() <= 50


def test_replay_cache_allows_reuse_after_window_expires():
    """T11: Same token ID accepted after the replay window expires."""
    clock = FakeClock()
    cache = ReplayCache(window_seconds=60, max_size=100, clock=clock)

    # First use
    assert cache.check_and_record("token-x") is False

    # Within window — replay detected
    clock.advance(30)
    assert cache.check_and_record("token-x") is True

    # Past window — should be accepted again
    clock.advance(60)
    assert cache.check_and_record("token-x") is False


# ---------------------------------------------------------------------------
# Group 5 — Edge Cases & Serialization
# ---------------------------------------------------------------------------


def test_empty_token_rejected():
    """T12: Empty or whitespace token is rejected immediately."""
    clock = FakeClock()
    config = make_config()
    verifier = make_fake_verifier(claims=make_test_claims())

    result = authenticate_webhook(
        token="   ",
        config=config,
        token_verifier=verifier,
        clock=clock,
    )

    assert result.is_authenticated is False
    assert "missing" in result.failure_reason.lower()


def test_auth_result_to_audit_dict_contains_all_fields():
    """T13: to_audit_dict() returns dict with all required audit fields."""
    clock = FakeClock()
    claims = make_test_claims()
    config = make_config()
    verifier = make_fake_verifier(claims=claims)

    result = authenticate_webhook(
        token="good.token",
        config=config,
        token_verifier=verifier,
        clock=clock,
    )
    audit = result.to_audit_dict()

    assert isinstance(audit, dict)
    expected_keys = {
        "is_authenticated",
        "failure_reason",
        "token_id",
        "checked_at",
        "issuer",
        "audience",
    }
    assert expected_keys.issubset(set(audit.keys()))
    assert audit["is_authenticated"] is True
    assert audit["issuer"] == DEFAULT_ISSUER


def test_build_default_auth_config_has_sensible_defaults():
    """T14: Factory function returns config with standard Bot Framework defaults."""
    config = build_default_auth_config("my-bot-id")

    assert config.bot_app_id == "my-bot-id"
    assert config.clock_skew_seconds == 300
    assert config.replay_window_seconds == 300
    assert config.max_replay_cache_size == 10_000
    assert len(config.allowed_issuers) >= 2
    assert "https://api.botframework.com" in config.allowed_issuers


def test_verifier_exception_returns_auth_failure():
    """T15: If the token verifier raises an exception, auth fails gracefully."""
    clock = FakeClock()
    config = make_config()
    verifier = make_fake_verifier(error=RuntimeError("key fetch failed"))

    result = authenticate_webhook(
        token="crash.token",
        config=config,
        token_verifier=verifier,
        clock=clock,
    )

    assert result.is_authenticated is False
    assert "error" in result.failure_reason.lower()


def test_token_claims_are_frozen():
    """T16: TokenClaims dataclass is frozen — attribute assignment raises."""
    claims = make_test_claims()
    with pytest.raises(AttributeError):
        claims.issuer = "hacked"  # type: ignore[misc]
