"""Tests for stall_sla_notifier — Phase A SLA escalation.

Covers:
- _dedupe_key produces stable per-day key per (story_id, reason)
- _format_dm picks the right severity label per stall_reason
- run() skips already-notified items
- run() loads/saves state idempotently
- run() never raises on transient HTTP / decode errors
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    p = tmp_path / "stall-state.json"
    monkeypatch.setenv("STALL_NOTIFIER_STATE", str(p))
    return p


def _stall(**overrides) -> dict:
    base = {
        "story_id": "STORY-NOTIFY-1",
        "repo": "tech-dev-agents",
        "stall_reason": "awaiting_human",
        "last_action": "drafted QUESTION.md",
        "stalled_since": "2026-05-06T10:00:00+00:00",
        "leased_by": "dan",
    }
    base.update(overrides)
    return base


def _import_module():
    sys.path.insert(
        0,
        str(Path(__file__).resolve().parents[2] / "deployment/morris/scripts"),
    )
    import stall_sla_notifier  # type: ignore
    return stall_sla_notifier


# ---------------------------------------------------------------------------
# _dedupe_key & _format_dm
# ---------------------------------------------------------------------------


class TestKeyAndFormat:
    def test_dedupe_key_is_per_story_reason_day(self, state_path):
        mod = _import_module()
        a = _stall(story_id="STORY-1", stall_reason="awaiting_human")
        b = _stall(story_id="STORY-1", stall_reason="silent_stall")
        c = _stall(story_id="STORY-2", stall_reason="awaiting_human")
        ka, kb, kc = mod._dedupe_key(a), mod._dedupe_key(b), mod._dedupe_key(c)
        assert ka != kb, "different reason same story should not dedupe"
        assert ka != kc, "different story same reason should not dedupe"
        assert ka == mod._dedupe_key(a), "same item should produce same key"

    def test_format_dm_picks_severity_per_reason(self, state_path):
        mod = _import_module()
        for reason, expected_token in [
            ("silent_stall", "[ALERT]"),
            ("awaiting_human", "[WARN]"),
            ("awaiting_human_critical", "[ESCALATION]"),
            ("stale_dispatch", "[INFO]"),
            ("review_stuck", "[WARN]"),
        ]:
            label, _ = mod._format_dm(_stall(stall_reason=reason))
            assert expected_token in label, (
                f"reason={reason} expected severity {expected_token} in '{label}'"
            )

    def test_format_dm_includes_action_and_age(self, state_path):
        mod = _import_module()
        label, bullets = mod._format_dm(_stall(
            last_action="committed abc123 (Phase 8)",
            stalled_since="2026-05-06T09:00:00+00:00",
        ))
        joined = "\n".join(bullets)
        assert "committed abc123 (Phase 8)" in joined
        assert "2026-05-06T09:00:00+00:00" in joined
        assert "/pm STORY-N" in joined
        assert "STORY-NOTIFY-1" in label


# ---------------------------------------------------------------------------
# run() — orchestration
# ---------------------------------------------------------------------------


class TestRun:
    def test_dry_run_does_not_persist_state(self, state_path):
        mod = _import_module()
        items = [_stall()]
        with patch.object(mod, "_fetch_stalls", return_value=items):
            rc = mod.run(dry_run=True)
        assert rc == 0
        assert not state_path.exists(), "dry-run must not write state"

    def test_first_run_notifies_and_persists(self, state_path, capsys):
        mod = _import_module()
        items = [_stall(story_id="STORY-A")]
        with patch.object(mod, "_fetch_stalls", return_value=items):
            rc = mod.run(dry_run=False)
        assert rc == 0
        assert state_path.exists()
        state = json.loads(state_path.read_text())
        notified = state["notified"]
        assert len(notified) == 1
        # The dedupe key starts with the story ID
        assert any(k.startswith("STORY-A") for k in notified)
        captured = capsys.readouterr().out
        assert "STORY-A" in captured

    def test_repeat_run_skips_already_notified(self, state_path, capsys):
        mod = _import_module()
        items = [_stall(story_id="STORY-B", stall_reason="awaiting_human")]
        with patch.object(mod, "_fetch_stalls", return_value=items):
            mod.run(dry_run=False)
            capsys.readouterr()  # discard first run
            mod.run(dry_run=False)
        captured = capsys.readouterr().out
        # Second run: nothing new should be printed for STORY-B
        # (the headline contains "STORY-B"; absence proves dedupe).
        assert "STORY-B" not in captured

    def test_fetch_failure_does_not_crash(self, state_path):
        mod = _import_module()
        with patch.object(mod, "_fetch_stalls", return_value=[]):
            rc = mod.run(dry_run=False)
        assert rc == 0

    def test_main_returns_zero_on_inner_exception(self, state_path):
        """If the run loop raises, main() must still exit 0 to keep cron alive."""
        mod = _import_module()
        with patch.object(mod, "run", side_effect=RuntimeError("boom")):
            with patch.object(sys, "argv", ["stall_sla_notifier.py"]):
                rc = mod.main()
        assert rc == 0


# ---------------------------------------------------------------------------
# _fetch_stalls — wraps urllib defensively
# ---------------------------------------------------------------------------


class TestFetchStalls:
    def test_no_api_key_returns_empty(self, monkeypatch):
        monkeypatch.delenv("OPS_CONSOLE_API_KEY", raising=False)
        mod = _import_module()
        assert mod._fetch_stalls() == []

    def test_http_error_returns_empty(self, monkeypatch):
        monkeypatch.setenv("OPS_CONSOLE_API_KEY", "k")
        mod = _import_module()
        with patch.object(
            mod.urllib.request, "urlopen",
            side_effect=urllib.error.HTTPError("u", 503, "x", {}, None),
        ):
            assert mod._fetch_stalls() == []

    def test_url_error_returns_empty(self, monkeypatch):
        monkeypatch.setenv("OPS_CONSOLE_API_KEY", "k")
        mod = _import_module()
        with patch.object(
            mod.urllib.request, "urlopen",
            side_effect=urllib.error.URLError("offline"),
        ):
            assert mod._fetch_stalls() == []

    def test_returns_items_list_from_response(self, monkeypatch):
        monkeypatch.setenv("OPS_CONSOLE_API_KEY", "k")
        mod = _import_module()
        fake_resp = MagicMock()
        fake_resp.__enter__ = lambda self: fake_resp
        fake_resp.__exit__ = lambda self, *a: False
        # urlopen with json.load(resp) — make resp behave like a file
        import io
        body = io.BytesIO(json.dumps({"items": [_stall()]}).encode())
        fake_resp.read = body.read
        # json.load(resp) calls resp.read(); easiest path: monkeypatch json.load
        with patch.object(mod.urllib.request, "urlopen", return_value=fake_resp):
            with patch.object(mod.json, "load", return_value={"items": [_stall()]}):
                items = mod._fetch_stalls()
        assert len(items) == 1
        assert items[0]["story_id"] == "STORY-NOTIFY-1"
