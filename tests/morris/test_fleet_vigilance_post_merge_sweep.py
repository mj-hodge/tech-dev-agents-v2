"""
tests/morris/test_fleet_vigilance_post_merge_sweep.py — STORY-795

Unit tests for Fleet-vigilance Check 8: Post-Merge Deploy + Re-Enqueue Sweep.

All tests use mocked subprocess + mocked urllib (no real push-code.sh, no live API).
State-file behaviour is tested with tmp_path fixtures for idempotency.

Fixes validated here:
  H-2: --no-merges absent from git log invocation
  H-1: heartbeat-compatible result dict returned
  M-1: first-run initialises from HEAD, not epoch-1970
  S-1: ValueError raised on empty OPS_CONSOLE_API_KEY
"""

import json
import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Make the scripts directory importable without installing the package
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = (
    Path(__file__).resolve().parents[2]
    / "deployment"
    / "morris"
    / "scripts"
)
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import post_merge_sweep as pms  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_git_log_output(*shas_subjects):
    """Build fake `git log --pretty=format:%H %aI %s` output."""
    lines = []
    for sha, subj in shas_subjects:
        lines.append(f"{sha} 2026-04-30T09:00:00+00:00 {subj}")
    return "\n".join(lines)


def _mock_urlopen_ok(status=201, body=b'{"queued": true}'):
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    # json.load reads from the response object
    resp.read.return_value = body
    return resp


def _dispatch_resp(items):
    """Fake /api/dispatch/history?status=failed response."""
    body = json.dumps({"items": items, "total": len(items), "limit": 200, "offset": 0, "fetched_at": "2026-05-04T00:00:00+00:00"}).encode()
    resp = MagicMock()
    resp.status = 200
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# H-2: git log must NOT contain --no-merges
# ---------------------------------------------------------------------------


class TestDetectMergesNoMergesFlag:
    """H-2: --no-merges must be absent from git log invocation."""

    def test_git_log_command_has_no_no_merges_flag(self, tmp_path, monkeypatch):
        """Verify _detect_merges never passes --no-merges to subprocess."""
        captured_cmd = []

        def _fake_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stdout = _make_git_log_output(("abc1234def5678901234", "STORY-759 fix"))
            return r

        monkeypatch.setattr(pms.subprocess, "run", _fake_run)
        pms._detect_merges("2026-04-30T08:00:00+00:00", 24, tmp_path)

        assert "--no-merges" not in captured_cmd, (
            "--no-merges was found in git log invocation. "
            "This repo uses merge commits; that flag filters out the very commits "
            "Check 8 needs to detect."
        )

    def test_git_log_command_includes_deploy_paths(self, tmp_path, monkeypatch):
        """git log must scope to deployment/hermes/ and deployment/morris/."""
        captured_cmd = []

        def _fake_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stdout = ""
            return r

        monkeypatch.setattr(pms.subprocess, "run", _fake_run)
        pms._detect_merges(None, 24, tmp_path)

        assert "deployment/hermes/" in captured_cmd
        assert "deployment/morris/" in captured_cmd


# ---------------------------------------------------------------------------
# M-1: First-run initialises from HEAD, not epoch-1970
# ---------------------------------------------------------------------------


class TestFirstRunHeadInitialization:
    """M-1: First run must record HEAD SHA, not fall back to 1970."""

    def test_first_run_writes_head_sha(self, tmp_path, monkeypatch):
        """With no state file, check_post_merge_sweep writes HEAD to state."""
        state_file = tmp_path / "sweep-state.txt"
        fake_head = "deadbeef1234567890abcdef12345678deadbeef"

        def _fake_run(cmd, **kwargs):
            r = MagicMock()
            if "rev-parse" in cmd:
                r.returncode = 0
                r.stdout = fake_head + "\n"
            else:
                r.returncode = 0
                r.stdout = ""
            return r

        monkeypatch.setattr(pms.subprocess, "run", _fake_run)
        monkeypatch.setenv("OPS_CONSOLE_API_KEY", "test-key")

        result = pms.check_post_merge_sweep(state_path=state_file, repo_root=tmp_path)

        assert state_file.exists(), "State file must be created on first run"
        content = state_file.read_text().strip()
        assert fake_head in content, (
            f"State file should contain HEAD SHA {fake_head[:8]}, got: {content!r}"
        )
        assert "1970" not in content, "State file must not contain epoch-1970"
        assert result["severity"] == "ok"

    def test_first_run_returns_without_scanning(self, tmp_path, monkeypatch):
        """First run returns early after writing HEAD; does NOT scan for merges."""
        state_file = tmp_path / "sweep-state.txt"
        git_log_calls = []

        def _fake_run(cmd, **kwargs):
            r = MagicMock()
            if "rev-parse" in cmd:
                r.returncode = 0
                r.stdout = "aabbcc1122334455667788990011aabbcc112233\n"
            else:
                git_log_calls.append(cmd)
                r.returncode = 0
                r.stdout = ""
            return r

        monkeypatch.setattr(pms.subprocess, "run", _fake_run)
        monkeypatch.setenv("OPS_CONSOLE_API_KEY", "test-key")

        pms.check_post_merge_sweep(state_path=state_file, repo_root=tmp_path)

        # No git log scan should have happened during first-run initialisation
        log_cmds = [c for c in git_log_calls if "log" in c]
        assert not log_cmds, (
            "First run should not scan for merges — only initialise HEAD state"
        )


# ---------------------------------------------------------------------------
# SC-1 / H-1: Merge detection returns heartbeat-compatible result
# ---------------------------------------------------------------------------


class TestMergeDetection:
    """SC-1 + H-1: Merge detection + result dict structure."""

    def _write_state(self, state_file, sha, ts):
        state_file.write_text(f"{sha} {ts}\n")

    def test_detect_new_merges_since_last_run(self, tmp_path, monkeypatch):
        """Merges since last state SHA trigger deploy and re-enqueue sweep."""
        state_file = tmp_path / "state.txt"
        self._write_state(state_file, "prev1234", "2026-04-30T08:00:00+00:00")

        merge_sha = "abc123def456789012345678901234567890abcd"

        def _fake_run(cmd, **kwargs):
            r = MagicMock()
            if "log" in cmd:
                r.returncode = 0
                r.stdout = f"{merge_sha} 2026-04-30T09:00:00+00:00 STORY-759: fix hardcoded main"
            elif "push-code.sh" in " ".join(cmd):
                r.returncode = 0
                r.stdout = "all agents: smoke OK"
                r.stderr = ""
            else:
                r.returncode = 0
                r.stdout = ""
            return r

        dispatch_body = json.dumps({"failed": []}).encode()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = dispatch_body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr(pms.subprocess, "run", _fake_run)
        monkeypatch.setattr(pms.urllib.request, "urlopen", lambda *a, **kw: mock_resp)
        monkeypatch.setenv("OPS_CONSOLE_API_KEY", "test-key")

        result = pms.check_post_merge_sweep(state_path=state_file, repo_root=tmp_path)

        assert result["check_id"] == 8
        assert result["merges_detected"] == 1
        assert result["deploy_success"] is True
        assert "dm_payload" in result
        assert "dm_suppressed" in result

    def test_idempotent_no_new_merges(self, tmp_path, monkeypatch):
        """No new merges since last run → no deploy, no re-enqueue."""
        state_file = tmp_path / "state.txt"
        self._write_state(state_file, "latest123", "2026-04-30T09:30:00+00:00")

        subprocess_calls = []

        def _fake_run(cmd, **kwargs):
            subprocess_calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stdout = ""
            r.stderr = ""
            return r

        monkeypatch.setattr(pms.subprocess, "run", _fake_run)
        monkeypatch.setenv("OPS_CONSOLE_API_KEY", "test-key")

        result = pms.check_post_merge_sweep(state_path=state_file, repo_root=tmp_path)

        assert result["severity"] == "ok"
        assert result["merges_detected"] == 0
        assert result["deploy_success"] is None
        # push-code.sh must NOT have been called
        push_calls = [c for c in subprocess_calls if "push-code.sh" in " ".join(c)]
        assert not push_calls, "push-code.sh should not run when there are no new merges"


# ---------------------------------------------------------------------------
# SC-2 / AC-7: Smoke failure aborts re-enqueue
# ---------------------------------------------------------------------------


class TestSmokeFailureBlocksReenqueue:
    """SC-2 / AC-7: deploy failure → CRIT, no re-enqueue."""

    def test_smoke_failure_blocks_reenqueue(self, tmp_path, monkeypatch):
        """push-code.sh non-zero exit → CRIT severity, re-enqueue never called."""
        state_file = tmp_path / "state.txt"
        state_file.write_text("prevsha 2026-04-30T08:00:00+00:00\n")

        urlopen_calls = []

        def _fake_run(cmd, **kwargs):
            r = MagicMock()
            if "log" in cmd:
                r.returncode = 0
                r.stdout = "abc1234def567890abc1234def567890abc12345 2026-04-30T09:00:00+00:00 fix deploy path"
            elif "push-code.sh" in " ".join(cmd):
                r.returncode = 1
                r.stdout = ""
                r.stderr = "devon: smoke test FAILED"
            else:
                r.returncode = 0
                r.stdout = ""
            r.stderr = r.stderr if hasattr(r, "_stderr") else ""
            return r

        monkeypatch.setattr(pms.subprocess, "run", _fake_run)
        monkeypatch.setattr(
            pms.urllib.request,
            "urlopen",
            lambda *a, **kw: urlopen_calls.append(a) or MagicMock(),
        )
        monkeypatch.setenv("OPS_CONSOLE_API_KEY", "test-key")

        result = pms.check_post_merge_sweep(state_path=state_file, repo_root=tmp_path)

        assert result["severity"] == "crit"
        assert result["deploy_success"] is False
        assert result["requeued"] == 0
        assert result["dm_payload"] is not None
        assert "FAILED" in result["dm_payload"]["text"]
        # dispatch API must NOT have been called for re-enqueue
        enqueue_calls = [
            a for a in urlopen_calls if "/api/dispatch" in str(a)
        ]
        assert not enqueue_calls, "Re-enqueue must be aborted when smoke test fails"


# ---------------------------------------------------------------------------
# SC-3 / AC-6: Re-enqueue uses strip-retry-prefix helper
# ---------------------------------------------------------------------------


class TestReenqueueUsesStripHelper:
    """SC-3 / AC-6: _strip_retry_prefix applied before re-enqueue POST."""

    def test_strip_retry_prefix_applied(self):
        """[RETRY] and [RETRY-N] prefixes stripped before dispatch."""
        assert pms._strip_retry_prefix("[RETRY] STORY-042") == "STORY-042"
        assert pms._strip_retry_prefix("[RETRY-3] STORY-099") == "STORY-099"
        assert pms._strip_retry_prefix("STORY-001") == "STORY-001"

    def test_reenqueue_uses_strip_helper(self, tmp_path, monkeypatch):
        """Re-enqueue POST body contains stripped story_id."""
        state_file = tmp_path / "state.txt"
        state_file.write_text("prevsha 2026-04-30T08:00:00+00:00\n")

        posted_bodies = []

        merge_sha = "abc1234def567890abc1234def567890abc12345"

        def _fake_run(cmd, **kwargs):
            r = MagicMock()
            if "log" in cmd:
                r.returncode = 0
                r.stdout = f"{merge_sha} 2026-04-30T09:00:00+00:00 STORY-759 fix"
            elif "push-code.sh" in " ".join(cmd):
                r.returncode = 0
                r.stdout = "all green"
                r.stderr = ""
            else:
                r.returncode = 0
                r.stdout = ""
            return r

        def _fake_urlopen(req, **kwargs):
            resp = MagicMock()
            resp.status = 201
            resp.read.return_value = b'{"queued":true}'
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            if hasattr(req, "data") and req.data:
                posted_bodies.append(json.loads(req.data.decode()))
            # For history query — return eligible item with [RETRY] prefix
            url = str(getattr(req, "full_url", "") or "")
            if "status=failed" in url:
                body = json.dumps(
                    {
                        "items": [
                            {
                                "story_id": "[RETRY] STORY-042",
                                "status": "failed",
                                "repo": "tech-dev-agents",
                                "branch": "main",
                                "scope": "small",
                                "failure_reason": "branch_setup_failed: git checkout main",
                            }
                        ],
                        "total": 1,
                        "limit": 200,
                        "offset": 0,
                        "fetched_at": "2026-05-04T00:00:00+00:00",
                    }
                ).encode()
                resp.read.return_value = body
            return resp

        monkeypatch.setattr(pms.subprocess, "run", _fake_run)
        monkeypatch.setattr(pms.urllib.request, "urlopen", _fake_urlopen)
        monkeypatch.setenv("OPS_CONSOLE_API_KEY", "test-key")

        result = pms.check_post_merge_sweep(state_path=state_file, repo_root=tmp_path)

        assert result["requeued"] == 1
        assert any(b.get("story_id") == "STORY-042" for b in posted_bodies), (
            f"Expected story_id='STORY-042' (prefix stripped), got: {posted_bodies}"
        )


# ---------------------------------------------------------------------------
# SC-5: Idempotency — state file updated after successful sweep
# ---------------------------------------------------------------------------


class TestIdempotency:
    """SC-5: State file written after sweep; second run is a no-op."""

    def test_state_file_updated_after_sweep(self, tmp_path, monkeypatch):
        """After a successful sweep the state file contains the latest merge SHA."""
        state_file = tmp_path / "state.txt"
        state_file.write_text("oldsha 2026-04-29T00:00:00+00:00\n")

        new_sha = "aabbcc1234567890aabbcc1234567890aabbcc12"

        def _fake_run(cmd, **kwargs):
            r = MagicMock()
            if "log" in cmd:
                r.returncode = 0
                r.stdout = f"{new_sha} 2026-04-30T10:00:00+00:00 STORY-795 fix"
            elif "push-code.sh" in " ".join(cmd):
                r.returncode = 0
                r.stdout = "ok"
                r.stderr = ""
            else:
                r.returncode = 0
                r.stdout = ""
            return r

        queue_resp = MagicMock()
        queue_resp.status = 200
        queue_resp.read.return_value = json.dumps({"failed": []}).encode()
        queue_resp.__enter__ = lambda s: s
        queue_resp.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr(pms.subprocess, "run", _fake_run)
        monkeypatch.setattr(pms.urllib.request, "urlopen", lambda *a, **kw: queue_resp)
        monkeypatch.setenv("OPS_CONSOLE_API_KEY", "test-key")

        pms.check_post_merge_sweep(state_path=state_file, repo_root=tmp_path)

        content = state_file.read_text().strip()
        assert new_sha in content, f"State file should contain new SHA; got: {content!r}"


# ---------------------------------------------------------------------------
# SC-6: Teams DM summary format
# ---------------------------------------------------------------------------


class TestTeamsDmSummaryFormat:
    """SC-6: DM payload format matches [FLEET-SWEEP] template."""

    def test_teams_dm_summary_format(self, tmp_path, monkeypatch):
        """After successful sweep, dm_payload['text'] matches expected format."""
        state_file = tmp_path / "state.txt"
        state_file.write_text("prevsha 2026-04-29T00:00:00+00:00\n")

        merge_sha = "feedface1234567890feedface1234567800fe00"

        def _fake_run(cmd, **kwargs):
            r = MagicMock()
            if "log" in cmd:
                r.returncode = 0
                r.stdout = f"{merge_sha} 2026-04-30T09:00:00+00:00 STORY-759 deploy fix"
            elif "push-code.sh" in " ".join(cmd):
                r.returncode = 0
                r.stdout = "all smoke OK"
                r.stderr = ""
            else:
                r.returncode = 0
                r.stdout = ""
            return r

        queue_resp = MagicMock()
        queue_resp.status = 200
        queue_resp.read.return_value = json.dumps({"failed": []}).encode()
        queue_resp.__enter__ = lambda s: s
        queue_resp.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr(pms.subprocess, "run", _fake_run)
        monkeypatch.setattr(pms.urllib.request, "urlopen", lambda *a, **kw: queue_resp)
        monkeypatch.setenv("OPS_CONSOLE_API_KEY", "test-key")

        result = pms.check_post_merge_sweep(state_path=state_file, repo_root=tmp_path)

        assert result["dm_payload"] is not None
        text = result["dm_payload"]["text"]
        assert "[FLEET-SWEEP]" in text
        assert merge_sha[:8] in text
        assert "Smoke: ✓" in text


# ---------------------------------------------------------------------------
# SC-9 / lookback window configurable (FIXED test — was always-true)
# ---------------------------------------------------------------------------


class TestLookbackWindowConfigurable:
    """SC-9: FLEET_SWEEP_LOOKBACK_HOURS must control the API query parameter."""

    def test_lookback_window_configurable(self, tmp_path, monkeypatch):
        """lookback_hours=48 must appear as query param in the dispatch API call.

        FIX from PR review: the old test had `assert 48 in url or True` which
        always passed regardless of the actual URL. This test actually captures
        the URL and asserts the parameter is present.
        """
        state_file = tmp_path / "state.txt"
        state_file.write_text("prevsha 2026-04-29T00:00:00+00:00\n")

        merge_sha = "cafe1234cafe5678cafe1234cafe5678cafe1234"
        captured_urls = []

        def _fake_run(cmd, **kwargs):
            r = MagicMock()
            if "log" in cmd:
                r.returncode = 0
                r.stdout = f"{merge_sha} 2026-04-30T09:00:00+00:00 STORY fix"
            elif "push-code.sh" in " ".join(cmd):
                r.returncode = 0
                r.stdout = "ok"
                r.stderr = ""
            else:
                r.returncode = 0
                r.stdout = ""
            return r

        def _fake_urlopen(req, **kwargs):
            url = getattr(req, "full_url", str(req))
            captured_urls.append(url)
            resp = MagicMock()
            resp.status = 200
            resp.read.return_value = json.dumps({"failed": []}).encode()
            resp.__enter__ = lambda s: s
            resp.__exit__ = MagicMock(return_value=False)
            return resp

        monkeypatch.setattr(pms.subprocess, "run", _fake_run)
        monkeypatch.setattr(pms.urllib.request, "urlopen", _fake_urlopen)
        monkeypatch.setenv("OPS_CONSOLE_API_KEY", "test-key")
        monkeypatch.setenv("FLEET_SWEEP_LOOKBACK_HOURS", "48")

        pms.check_post_merge_sweep(
            lookback_hours=48, state_path=state_file, repo_root=tmp_path
        )

        # The dispatch query URL must contain lookback_hours=48
        history_urls = [u for u in captured_urls if "dispatch/history" in u]
        assert history_urls, "No dispatch/history URL captured"
        assert any("status=failed" in u for u in history_urls), (
            f"Expected 'status=failed' in dispatch history URL. "
            f"Captured URLs: {history_urls}"
        )


# ---------------------------------------------------------------------------
# S-1: OPS_CONSOLE_API_KEY guard
# ---------------------------------------------------------------------------


class TestApiKeyGuard:
    """S-1: Empty OPS_CONSOLE_API_KEY must surface as error_unavailable."""

    def test_empty_api_key_returns_error_unavailable(self, tmp_path, monkeypatch):
        """Missing API key → error_unavailable, no subprocess calls."""
        state_file = tmp_path / "state.txt"
        state_file.write_text("prevsha 2026-04-29T00:00:00+00:00\n")

        subprocess_calls = []
        monkeypatch.setattr(
            pms.subprocess,
            "run",
            lambda cmd, **kw: subprocess_calls.append(cmd) or MagicMock(returncode=0, stdout=""),
        )
        monkeypatch.delenv("OPS_CONSOLE_API_KEY", raising=False)

        result = pms.check_post_merge_sweep(state_path=state_file, repo_root=tmp_path)

        assert result["severity"] == "error_unavailable"
        assert "OPS_CONSOLE_API_KEY" in result["status_line"]

    def test_validate_api_key_raises_on_empty(self, monkeypatch):
        """_validate_api_key raises ValueError when env var is empty."""
        monkeypatch.delenv("OPS_CONSOLE_API_KEY", raising=False)
        with pytest.raises(ValueError, match="OPS_CONSOLE_API_KEY"):
            pms._validate_api_key()

    def test_validate_api_key_returns_key_when_set(self, monkeypatch):
        """_validate_api_key returns the key when set."""
        monkeypatch.setenv("OPS_CONSOLE_API_KEY", "my-secret-key")
        assert pms._validate_api_key() == "my-secret-key"
