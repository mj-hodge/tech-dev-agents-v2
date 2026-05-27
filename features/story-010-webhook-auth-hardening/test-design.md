# Test Design: Teams Webhook Auth Hardening & Replay Protection (STORY-010)

> Phase 7 — Test Design
> Date: 2026-03-31
> Story: STORY-010
> Scope: Medium

---

## Test Strategy

All tests are unit tests using pytest. No network calls, no real JWT libraries. Tests inject fake `token_verifier` callbacks that return pre-built `TokenClaims` or `None`.

## AC-to-Test Mapping

| AC | Test ID(s) | Description |
|----|-----------|-------------|
| AC1: JWT signature validation | T01, T02 | Valid verifier returns claims; failing verifier returns None |
| AC2: Issuer validation | T03 | Wrong issuer rejected |
| AC3: Audience validation | T04 | Wrong audience rejected |
| AC4: Expiration check | T05, T06 | Expired token rejected; token within clock skew accepted |
| AC5: Replay protection | T07, T08 | Duplicate token rejected; different tokens accepted |
| AC6: Structured auth result | T12, T13 | AuthResult fields correct; to_audit_dict() contains all fields |
| AC7: Auth configuration | T14 | build_default_auth_config() returns sensible defaults |
| AC8: Replay cache bounded | T09, T10 | Cache evicts expired entries; cache respects max size |
| AC9: Integration hook | T01 (implicitly) | authenticate_webhook() is the middleware entry point |
| AC10: Unit tests | All | This file defines the complete test suite |

## Test Cases

### Group 1 — Happy Path

| ID | Name | Setup | Assert |
|----|------|-------|--------|
| T01 | `test_valid_token_authenticates_successfully` | Valid claims, correct issuer/audience/expiry | `is_authenticated=True`, `failure_reason=None`, claims populated |
| T02 | `test_verifier_returning_none_rejects_token` | Verifier returns None | `is_authenticated=False`, `failure_reason` contains "signature" |

### Group 2 — Claims Validation

| ID | Name | Setup | Assert |
|----|------|-------|--------|
| T03 | `test_wrong_issuer_rejected` | Claims with issuer not in allowed_issuers | `is_authenticated=False`, `failure_reason` contains "issuer" |
| T04 | `test_wrong_audience_rejected` | Claims with audience != bot_app_id | `is_authenticated=False`, `failure_reason` contains "audience" |
| T05 | `test_expired_token_rejected` | Claims with `exp` in the past beyond clock skew | `is_authenticated=False`, `failure_reason` contains "expired" |
| T06 | `test_token_within_clock_skew_accepted` | Claims with `exp` slightly in the past but within 300s | `is_authenticated=True` |

### Group 3 — Replay Detection

| ID | Name | Setup | Assert |
|----|------|-------|--------|
| T07 | `test_replay_detected_for_duplicate_token` | Same token submitted twice within replay window | Second call: `is_authenticated=False`, `failure_reason` contains "replay" |
| T08 | `test_different_tokens_not_flagged_as_replay` | Two different tokens submitted | Both authenticate successfully |

### Group 4 — Replay Cache Mechanics

| ID | Name | Setup | Assert |
|----|------|-------|--------|
| T09 | `test_replay_cache_evicts_expired_entries` | Insert entries, advance clock past window, insert new | Expired entries evicted; cache size reduced |
| T10 | `test_replay_cache_respects_max_size` | Insert max_size + 1 entries | Cache size <= max_size after eviction |
| T11 | `test_replay_cache_allows_reuse_after_window_expires` | Submit token, advance clock past window, submit same token | Second submission succeeds (not flagged as replay) |

### Group 5 — Edge Cases & Serialization

| ID | Name | Setup | Assert |
|----|------|-------|--------|
| T12 | `test_empty_token_rejected` | Empty string token | `is_authenticated=False`, `failure_reason` contains "missing" |
| T13 | `test_auth_result_to_audit_dict_contains_all_fields` | Successful auth | Dict has: is_authenticated, failure_reason, token_id, checked_at, issuer, audience |
| T14 | `test_build_default_auth_config_has_sensible_defaults` | Call factory | clock_skew=300, replay_window=300, max_cache=10000, two issuers |
| T15 | `test_verifier_exception_returns_auth_failure` | Verifier raises RuntimeError | `is_authenticated=False`, `failure_reason` contains "error" |
| T16 | `test_token_claims_are_frozen` | Attempt to mutate TokenClaims | `AttributeError` raised |

## Test Infrastructure

### Fake Token Verifier

```python
def make_fake_verifier(claims: TokenClaims | None = None, error: Exception | None = None):
    def verifier(token: str) -> TokenClaims | None:
        if error:
            raise error
        return claims
    return verifier
```

### Fake Clock

```python
class FakeClock:
    def __init__(self, now: float):
        self._now = now
    def __call__(self) -> float:
        return self._now
    def advance(self, seconds: float):
        self._now += seconds
```

### Helper: Build Test Claims

```python
def make_test_claims(
    issuer="https://api.botframework.com",
    audience="test-bot-app-id",
    expiration=None,  # default: now + 3600
    token_id="test-jti-001",
    **overrides,
) -> TokenClaims: ...
```

## Expected Test Count

16 tests total, covering all 10 acceptance criteria.
