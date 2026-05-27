# STORY-527: Test Design — Real quota-based session cap

> Phase 7 | RED-state tests (written before implementation)

---

## Test Group A: _check_daily_session_cap quota logic

All tests mock `subprocess.run` to simulate `ccusage blocks --json` output.
Target function: `deployment.hermes.sdlc_phase_runner._check_daily_session_cap`

---

### A-1: test_cap_allows_when_tokens_plentiful

**Scenario:** High session count (150) but ccusage shows abundant tokens remaining.

**Given:**
- Session count file contains `2026-04-22:150`
- `DAILY_SESSION_CAP` set to 300 (new default)
- `ccusage blocks --json` returns an active block with 50M tokens remaining, resets_at in 3 hours

**Then:** `_check_daily_session_cap()` returns `True` (allowed to proceed).

**Why:** Validates that the function no longer pauses on session count alone when real quota is plentiful.

```python
import json
import os
import time
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture
def session_count_file(tmp_path):
    """Create a temporary session count file."""
    count_file = tmp_path / "state" / "testbot" / "sdk-sessions-today.count"
    count_file.parent.mkdir(parents=True, exist_ok=True)
    return count_file


def _make_ccusage_result(remaining_tokens=50_000_000, resets_in_seconds=10800, is_active=True, returncode=0):
    """Helper to build a mock subprocess.CompletedProcess for ccusage."""
    block = {
        "isActive": is_active,
        "totalTokens": 80_000_000 - remaining_tokens,
        "remainingTokens": remaining_tokens,
        "resets_at": time.time() + resets_in_seconds,
    }
    return MagicMock(
        returncode=returncode,
        stdout=json.dumps([block]),
        stderr="",
    )


def test_cap_allows_when_tokens_plentiful(session_count_file, monkeypatch):
    """150 sessions + 50M tokens remaining → allow."""
    today = time.strftime("%Y-%m-%d")
    session_count_file.write_text(f"{today}:150")

    monkeypatch.setenv("AGENT_NAME", "testbot")
    monkeypatch.setenv("DAILY_SESSION_CAP", "300")

    with patch("deployment.hermes.sdlc_phase_runner._SESSION_COUNT_FILE", str(session_count_file)):
        with patch("subprocess.run", return_value=_make_ccusage_result(
            remaining_tokens=50_000_000,
            resets_in_seconds=10800,  # 3 hours
        )):
            from deployment.hermes.sdlc_phase_runner import _check_daily_session_cap
            result = _check_daily_session_cap()

    assert result is True
```

---

### A-2: test_cap_pauses_when_tokens_exhausted

**Scenario:** Low session count (20) but ccusage shows tokens nearly exhausted with time remaining.

**Given:**
- Session count file contains `2026-04-22:20`
- `ccusage blocks --json` returns an active block with 2M tokens remaining, resets_at in 2 hours

**Then:** `_check_daily_session_cap()` returns `False` (paused).

**Why:** Validates that real quota exhaustion triggers a pause even when session count is low.

```python
def test_cap_pauses_when_tokens_exhausted(session_count_file, monkeypatch):
    """20 sessions + 2M tokens remaining + >15min to reset → pause."""
    today = time.strftime("%Y-%m-%d")
    session_count_file.write_text(f"{today}:20")

    monkeypatch.setenv("AGENT_NAME", "testbot")
    monkeypatch.setenv("DAILY_SESSION_CAP", "300")

    with patch("deployment.hermes.sdlc_phase_runner._SESSION_COUNT_FILE", str(session_count_file)):
        with patch("subprocess.run", return_value=_make_ccusage_result(
            remaining_tokens=2_000_000,
            resets_in_seconds=7200,  # 2 hours — plenty of time left, but tokens are low
        )):
            from deployment.hermes.sdlc_phase_runner import _check_daily_session_cap
            result = _check_daily_session_cap()

    assert result is False
```

---

### A-3: test_cap_pauses_when_reset_imminent

**Scenario:** Plenty of tokens but the billing block resets in under 15 minutes.

**Given:**
- Session count file contains `2026-04-22:5`
- `ccusage blocks --json` returns an active block with 10M tokens remaining, resets_at in 10 minutes

**Then:** `_check_daily_session_cap()` returns `False` (paused — don't start work that can't finish).

**Why:** Validates the "reset imminent" guard that prevents starting a story phase that will be interrupted by a rate-limit reset.

```python
def test_cap_pauses_when_reset_imminent(session_count_file, monkeypatch):
    """5 sessions + 10M tokens + <15min to reset → pause."""
    today = time.strftime("%Y-%m-%d")
    session_count_file.write_text(f"{today}:5")

    monkeypatch.setenv("AGENT_NAME", "testbot")
    monkeypatch.setenv("DAILY_SESSION_CAP", "300")

    with patch("deployment.hermes.sdlc_phase_runner._SESSION_COUNT_FILE", str(session_count_file)):
        with patch("subprocess.run", return_value=_make_ccusage_result(
            remaining_tokens=10_000_000,
            resets_in_seconds=600,  # 10 minutes — below 15min threshold
        )):
            from deployment.hermes.sdlc_phase_runner import _check_daily_session_cap
            result = _check_daily_session_cap()

    assert result is False
```

---

### A-4: test_fail_open_on_ccusage_error

**Scenario:** `ccusage` binary is unavailable or returns an error.

**Given:**
- `subprocess.run` raises `FileNotFoundError` (binary not found) or returns non-zero exit code

**Then:** `_check_daily_session_cap()` returns `True` (fail-open) and logs a warning.

**Why:** Validates that instrumentation failures never block legitimate work.

```python
def test_fail_open_on_ccusage_error(session_count_file, monkeypatch):
    """ccusage subprocess error → allow (fail-open)."""
    today = time.strftime("%Y-%m-%d")
    session_count_file.write_text(f"{today}:10")

    monkeypatch.setenv("AGENT_NAME", "testbot")
    monkeypatch.setenv("DAILY_SESSION_CAP", "300")

    with patch("deployment.hermes.sdlc_phase_runner._SESSION_COUNT_FILE", str(session_count_file)):
        with patch("subprocess.run", side_effect=FileNotFoundError("ccusage not found")):
            from deployment.hermes.sdlc_phase_runner import _check_daily_session_cap
            result = _check_daily_session_cap()

    assert result is True
```

---

### A-5: test_no_active_block

**Scenario:** `ccusage` returns valid JSON but no block has `isActive: true`.

**Given:**
- `ccusage blocks --json` returns `[]` (empty array) or blocks where all have `isActive: false`

**Then:** `_check_daily_session_cap()` returns `True` and logs a warning.

**Why:** Validates handling of the rare edge case where no billing block is active (e.g., between blocks, fresh account).

```python
def test_no_active_block(session_count_file, monkeypatch):
    """ccusage returns no active block → allow, log warning."""
    today = time.strftime("%Y-%m-%d")
    session_count_file.write_text(f"{today}:10")

    monkeypatch.setenv("AGENT_NAME", "testbot")
    monkeypatch.setenv("DAILY_SESSION_CAP", "300")

    empty_result = MagicMock(returncode=0, stdout="[]", stderr="")

    with patch("deployment.hermes.sdlc_phase_runner._SESSION_COUNT_FILE", str(session_count_file)):
        with patch("subprocess.run", return_value=empty_result):
            from deployment.hermes.sdlc_phase_runner import _check_daily_session_cap
            result = _check_daily_session_cap()

    assert result is True
```

---

## Test Group B: Fallback session ceiling

### B-1: test_fallback_ceiling_blocks_runaway

**Scenario:** ccusage shows plentiful tokens but session count exceeds the fallback ceiling (300).

**Given:**
- Session count file contains `2026-04-22:301`
- `ccusage blocks --json` returns 50M tokens remaining

**Then:** `_check_daily_session_cap()` returns `False`.

**Why:** The fallback ceiling catches genuinely runaway loops that burn sessions regardless of token budget.

```python
def test_fallback_ceiling_blocks_runaway(session_count_file, monkeypatch):
    """301 sessions even with plentiful tokens → pause (runaway protection)."""
    today = time.strftime("%Y-%m-%d")
    session_count_file.write_text(f"{today}:301")

    monkeypatch.setenv("AGENT_NAME", "testbot")
    monkeypatch.setenv("DAILY_SESSION_CAP", "300")

    with patch("deployment.hermes.sdlc_phase_runner._SESSION_COUNT_FILE", str(session_count_file)):
        with patch("subprocess.run", return_value=_make_ccusage_result(
            remaining_tokens=50_000_000,
            resets_in_seconds=10800,
        )):
            from deployment.hermes.sdlc_phase_runner import _check_daily_session_cap
            result = _check_daily_session_cap()

    assert result is False
```

---

## E2E Validation Plan (post-deploy)

1. On an agent VM, manually set session count to 150 by writing `2026-04-22:150` to the state file.
2. Confirm the poller still processes stories (doesn't auto-pause) — ccusage should show plentiful tokens.
3. Exhaust the ccusage quota artificially (or wait for a natural high-usage window) and confirm the poller DOES pause only when real quota is low.
4. Observe zero false-positive pauses for 4 hours with real Medium-scope stories.
