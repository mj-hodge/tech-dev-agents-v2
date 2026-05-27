"""STORY-734: Morris Operating Modes — unit tests.

Tests cover all 12 test criteria from the seed plus additional edge-case
coverage for mode_controller, set_mode CLI, and mode gates in existing scripts.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

# ---------------------------------------------------------------------------
# Import-path bootstrap — make deployment/morris/scripts importable
# ---------------------------------------------------------------------------
_SCRIPTS_DIR = str(Path(__file__).resolve().parents[2] / "deployment" / "morris" / "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_mode_state(tmp_path: Path):
    """Provide a temporary mode state file and patch MODE_STATE_PATH."""
    state_file = tmp_path / "mode_state.json"
    with mock.patch("mode_controller.MODE_STATE_PATH", state_file):
        yield state_file


@pytest.fixture()
def base_config() -> dict:
    """Minimal orchestrator config for mode_controller tests."""
    return {
        "ops_console": {"url": "https://ops.example.com", "api_key": "test-key"},
        "teams": {"mark_chat_id": "test-chat-id"},
        "operating_modes": {
            "light_quota_threshold": 0.70,
            "minimal_quota_threshold": 0.90,
            "recovery_to_light_threshold": 0.60,
            "recovery_to_full_threshold": 0.50,
            "mark_cooldown_minutes": 120,
            "overnight_start_utc": "05:00",
            "overnight_end_utc": "12:00",
        },
    }


def _write_mode_state(state_file: Path, mode: str, reason: str = "test",
                       actor: str = "auto", set_at: str | None = None,
                       quota_pct: float | None = None) -> None:
    """Helper: write a mode state file."""
    if set_at is None:
        set_at = datetime.now(timezone.utc).isoformat()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({
        "mode": mode,
        "set_at": set_at,
        "reason": reason,
        "actor": actor,
        "quota_pct_at_set": quota_pct,
    }))


# ---------------------------------------------------------------------------
# TC-1: Auto-transition to light at 73%
# ---------------------------------------------------------------------------


class TestTC1AutoTransitionToLight:
    def test_auto_transition_to_light_at_73_pct(self, tmp_mode_state, base_config):
        import mode_controller

        _write_mode_state(tmp_mode_state, "full")

        with mock.patch.object(mode_controller, "get_quota_pct", return_value=0.73), \
             mock.patch.object(mode_controller, "_post_transition_dm") as mock_dm:
            result = mode_controller.check_and_auto_transition(base_config)

        assert result == "light"
        state = json.loads(tmp_mode_state.read_text())
        assert state["mode"] == "light"
        assert state["reason"] == "quota_approaching"
        assert state["actor"] == "auto"
        mock_dm.assert_called_once()
        args = mock_dm.call_args
        assert args[0][0] == "light"  # mode
        assert "quota_approaching" in args[0][1]  # reason


# ---------------------------------------------------------------------------
# TC-2: Auto-transition to minimal at 92%
# ---------------------------------------------------------------------------


class TestTC2AutoTransitionToMinimal:
    def test_from_full(self, tmp_mode_state, base_config):
        import mode_controller
        _write_mode_state(tmp_mode_state, "full")

        with mock.patch.object(mode_controller, "get_quota_pct", return_value=0.92), \
             mock.patch.object(mode_controller, "_post_transition_dm"):
            result = mode_controller.check_and_auto_transition(base_config)

        assert result == "minimal"
        assert json.loads(tmp_mode_state.read_text())["reason"] == "quota_critical"

    def test_from_light(self, tmp_mode_state, base_config):
        import mode_controller
        _write_mode_state(tmp_mode_state, "light")

        with mock.patch.object(mode_controller, "get_quota_pct", return_value=0.92), \
             mock.patch.object(mode_controller, "_post_transition_dm"):
            result = mode_controller.check_and_auto_transition(base_config)

        assert result == "minimal"


# ---------------------------------------------------------------------------
# TC-3: Hysteresis — minimal stays at 88%
# ---------------------------------------------------------------------------


class TestTC3Hysteresis:
    def test_minimal_stays_at_88_pct(self, tmp_mode_state, base_config):
        import mode_controller
        _write_mode_state(tmp_mode_state, "minimal")

        with mock.patch.object(mode_controller, "get_quota_pct", return_value=0.88), \
             mock.patch.object(mode_controller, "_post_transition_dm") as mock_dm:
            result = mode_controller.check_and_auto_transition(base_config)

        assert result == "minimal"
        mock_dm.assert_not_called()


# ---------------------------------------------------------------------------
# TC-4: Recovery to light from minimal at 55%
# ---------------------------------------------------------------------------


class TestTC4RecoveryToLight:
    def test_recovery_minimal_to_light_at_55_pct(self, tmp_mode_state, base_config):
        import mode_controller
        _write_mode_state(tmp_mode_state, "minimal")

        with mock.patch.object(mode_controller, "get_quota_pct", return_value=0.55), \
             mock.patch.object(mode_controller, "_post_transition_dm"):
            result = mode_controller.check_and_auto_transition(base_config)

        assert result == "light"
        state = json.loads(tmp_mode_state.read_text())
        assert state["reason"] == "quota_recovering"


# ---------------------------------------------------------------------------
# TC-5: Recovery to full from light at 45%
# ---------------------------------------------------------------------------


class TestTC5RecoveryToFull:
    def test_recovery_light_to_full_at_45_pct(self, tmp_mode_state, base_config):
        import mode_controller
        _write_mode_state(tmp_mode_state, "light")

        with mock.patch.object(mode_controller, "get_quota_pct", return_value=0.45), \
             mock.patch.object(mode_controller, "overnight_window", return_value=False), \
             mock.patch.object(mode_controller, "_post_transition_dm"):
            result = mode_controller.check_and_auto_transition(base_config)

        assert result == "full"
        state = json.loads(tmp_mode_state.read_text())
        assert state["reason"] == "quota_healthy"


# ---------------------------------------------------------------------------
# TC-6: Overnight gate
# ---------------------------------------------------------------------------


class TestTC6OvernightGate:
    def test_overnight_window_returns_true_during_overnight(self, base_config):
        """06:00 UTC (01:00 ET) should be inside overnight window."""
        import mode_controller
        fake_now = datetime(2026, 4, 27, 6, 0, tzinfo=timezone.utc)
        with mock.patch("mode_controller.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.fromisoformat = datetime.fromisoformat
            result = mode_controller.overnight_window(base_config)
        assert result is True

    def test_overnight_window_returns_false_outside(self, base_config):
        """14:00 UTC (09:00 ET) should be outside overnight window."""
        import mode_controller
        fake_now = datetime(2026, 4, 27, 14, 0, tzinfo=timezone.utc)
        with mock.patch("mode_controller.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            mock_dt.fromisoformat = datetime.fromisoformat
            result = mode_controller.overnight_window(base_config)
        assert result is False

    def test_overnight_blocks_light_to_full_recovery(self, tmp_mode_state, base_config):
        """Even at 45% quota, light→full should NOT happen during overnight."""
        import mode_controller
        _write_mode_state(tmp_mode_state, "light")

        with mock.patch.object(mode_controller, "get_quota_pct", return_value=0.45), \
             mock.patch.object(mode_controller, "overnight_window", return_value=True), \
             mock.patch.object(mode_controller, "_post_transition_dm") as mock_dm:
            result = mode_controller.check_and_auto_transition(base_config)

        assert result == "light"  # Stays light during overnight
        mock_dm.assert_not_called()


# ---------------------------------------------------------------------------
# TC-7: Quota unavailable → no transition
# ---------------------------------------------------------------------------


class TestTC7QuotaUnavailable:
    def test_quota_unavailable_skips_transition(self, tmp_mode_state, base_config):
        import mode_controller
        _write_mode_state(tmp_mode_state, "full")

        with mock.patch.object(mode_controller, "get_quota_pct", return_value=None), \
             mock.patch.object(mode_controller, "_post_transition_dm") as mock_dm:
            result = mode_controller.check_and_auto_transition(base_config)

        assert result == "full"
        mock_dm.assert_not_called()


# ---------------------------------------------------------------------------
# TC-8: Mark cooldown
# ---------------------------------------------------------------------------


class TestTC8MarkCooldown:
    def test_mark_cooldown_prevents_auto_override(self, tmp_mode_state, base_config):
        import mode_controller
        set_at = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
        _write_mode_state(tmp_mode_state, "minimal", actor="mark", set_at=set_at)

        with mock.patch.object(mode_controller, "get_quota_pct", return_value=0.40), \
             mock.patch.object(mode_controller, "_post_transition_dm") as mock_dm:
            result = mode_controller.check_and_auto_transition(base_config)

        assert result == "minimal"  # Mark's choice respected
        mock_dm.assert_not_called()

    def test_mark_cooldown_expires_after_120_min(self, tmp_mode_state, base_config):
        import mode_controller
        set_at = (datetime.now(timezone.utc) - timedelta(minutes=121)).isoformat()
        _write_mode_state(tmp_mode_state, "minimal", actor="mark", set_at=set_at)

        with mock.patch.object(mode_controller, "get_quota_pct", return_value=0.40), \
             mock.patch.object(mode_controller, "_post_transition_dm"):
            result = mode_controller.check_and_auto_transition(base_config)

        # After cooldown expires, auto-transition should fire (0.40 < 0.60 → recover to light)
        assert result == "light"


# ---------------------------------------------------------------------------
# TC-9: Light mode suppresses subagents
# ---------------------------------------------------------------------------


class TestTC9LightModeSuppressesSubagents:
    def test_light_mode_suppresses_rebase_subagent(self, base_config):
        """invoke_rebase_subagent should NOT be called when mode=light."""
        from unittest.mock import MagicMock, patch
        import orchestrator_loop

        # Create a mock PR conflict finding
        mock_finding = MagicMock()
        mock_finding.agent_owned = True
        mock_finding.pr_number = 42
        mock_finding.repo = "test/repo"
        mock_finding.author = "bot"
        mock_finding.title = "test PR"

        findings = {"pr_conflicts": [mock_finding], "stale_never_started": [],
                     "stale_heartbeat": [], "stale_phase": [],
                     "repeated_failures": [], "needs_info_decay": []}

        config = {**base_config, "interventions": {"rebase": {"enabled": True}}}

        # Patch at the interventions module level (where execute_interventions imports from)
        with patch("interventions.invoke_rebase_subagent") as mock_rebase, \
             patch("interventions.release_claim"), \
             patch("interventions.post_approval_needed"), \
             patch("interventions.post_dm"), \
             patch("interventions.post_needs_info_surface"), \
             patch("interventions.post_load_imbalance_dm"):

            count = orchestrator_loop.execute_interventions(
                findings, [], MagicMock(), config, False, mode="light"
            )

            mock_rebase.assert_not_called()


# ---------------------------------------------------------------------------
# TC-10: Shell jobs unaffected
# ---------------------------------------------------------------------------


class TestTC10ShellJobsUnaffected:
    def test_shell_infra_allowed_in_all_modes(self):
        from mode_controller import mode_allows
        assert mode_allows("full", "shell_infra") is True
        assert mode_allows("light", "shell_infra") is True
        assert mode_allows("minimal", "shell_infra") is True


# ---------------------------------------------------------------------------
# TC-11: Teams DM on every transition
# ---------------------------------------------------------------------------


class TestTC11TeamsDMOnTransition:
    def test_teams_dm_posted_on_set_mode(self, tmp_mode_state, base_config):
        import mode_controller

        with mock.patch.object(mode_controller, "_post_transition_dm") as mock_dm:
            mode_controller.set_mode("light", "test_reason", "auto",
                                      config=base_config, quota_pct=0.73)
            mode_controller.set_mode("minimal", "test_critical", "auto",
                                      config=base_config, quota_pct=0.92)
            mode_controller.set_mode("full", "test_recovery", "auto",
                                      config=base_config, quota_pct=0.40)

        assert mock_dm.call_count == 3

    def test_dm_contains_mode_info(self, tmp_mode_state, base_config):
        import mode_controller

        with mock.patch.object(mode_controller, "_post_transition_dm") as mock_dm:
            mode_controller.set_mode("light", "quota_approaching", "auto",
                                      config=base_config, quota_pct=0.73)

        mock_dm.assert_called_once()
        call_args = mock_dm.call_args
        assert call_args[0][0] == "light"  # mode
        assert "quota_approaching" in call_args[0][1]  # reason


# ---------------------------------------------------------------------------
# TC-12: Overnight crons idempotent
# ---------------------------------------------------------------------------


class TestTC12OvernightCronsIdempotent:
    def test_cron_block_has_marker(self):
        """Verify the install script contains the idempotent marker."""
        cron_script = Path(__file__).resolve().parents[2] / "deployment" / "morris" / "scripts" / "install-orchestrator-cron.sh"
        content = cron_script.read_text()
        assert "# morris-operating-modes-734" in content
        assert "install_cron_block" in content
        # The marker-based install_cron_block ensures idempotency
        assert content.count("morris-operating-modes-734") >= 2  # marker + label ref


# ---------------------------------------------------------------------------
# Additional tests: mode_controller edge cases
# ---------------------------------------------------------------------------


class TestModeControllerEdgeCases:
    def test_get_mode_returns_full_on_missing_file(self, tmp_mode_state):
        from mode_controller import get_mode
        # tmp_mode_state points to a non-existent file
        assert get_mode() == "full"

    def test_get_mode_returns_full_on_corrupt_json(self, tmp_mode_state):
        from mode_controller import get_mode
        tmp_mode_state.write_text("not valid json {{{")
        assert get_mode() == "full"

    def test_set_mode_creates_file(self, tmp_mode_state):
        from mode_controller import set_mode, get_mode
        assert not tmp_mode_state.exists()
        set_mode("light", "test", "auto")
        assert tmp_mode_state.exists()
        assert get_mode() == "light"

    def test_set_mode_rejects_invalid_mode(self, tmp_mode_state):
        from mode_controller import set_mode
        with pytest.raises(ValueError, match="Invalid mode"):
            set_mode("turbo", "test", "auto")

    def test_mode_allows_job_class_matrix(self):
        """Exhaustive check of all mode × job_class combinations."""
        from mode_controller import mode_allows

        # full: everything runs
        assert mode_allows("full", "shell_infra")
        assert mode_allows("full", "orchestrator_poll")
        assert mode_allows("full", "briefing")
        assert mode_allows("full", "fleet_check_llm")
        assert mode_allows("full", "self_improvement")
        assert mode_allows("full", "contact_summary")

        # light: shell_infra + orchestrator_poll only
        assert mode_allows("light", "shell_infra")
        assert mode_allows("light", "orchestrator_poll")
        assert not mode_allows("light", "briefing")
        assert not mode_allows("light", "fleet_check_llm")
        assert not mode_allows("light", "self_improvement")
        assert not mode_allows("light", "contact_summary")

        # minimal: shell_infra only
        assert mode_allows("minimal", "shell_infra")
        assert not mode_allows("minimal", "orchestrator_poll")
        assert not mode_allows("minimal", "briefing")
        assert not mode_allows("minimal", "fleet_check_llm")
        assert not mode_allows("minimal", "self_improvement")
        assert not mode_allows("minimal", "contact_summary")

    def test_get_quota_pct_returns_none_on_error(self, base_config):
        from mode_controller import get_quota_pct
        mock_session = mock.MagicMock()
        mock_session.get.side_effect = ConnectionError("timeout")
        result = get_quota_pct(base_config, mock_session)
        assert result is None

    def test_get_quota_pct_returns_correct_value(self, base_config):
        from mode_controller import get_quota_pct
        mock_session = mock.MagicMock()
        mock_resp = mock.MagicMock()
        mock_resp.json.return_value = {"percent_used": 0.73}
        mock_session.get.return_value = mock_resp
        result = get_quota_pct(base_config, mock_session)
        assert result == 0.73

    def test_get_quota_pct_returns_none_on_unavailable_source(self, base_config):
        from mode_controller import get_quota_pct
        mock_session = mock.MagicMock()
        mock_resp = mock.MagicMock()
        mock_resp.json.return_value = {"percent_used": 0.5, "source": "unavailable"}
        mock_session.get.return_value = mock_resp
        result = get_quota_pct(base_config, mock_session)
        assert result is None


# ---------------------------------------------------------------------------
# Additional tests: set_mode CLI
# ---------------------------------------------------------------------------


class TestSetModeCLI:
    def test_set_mode_cli_validates_mode(self):
        """Invalid mode should cause SystemExit."""
        from set_mode import main
        with pytest.raises(SystemExit):
            main(["invalid", "--reason", "test"])

    def test_set_mode_cli_sets_correct_mode(self, tmp_mode_state, base_config):
        import mode_controller
        import set_mode as set_mode_mod
        _write_mode_state(tmp_mode_state, "full")

        # Patch the imports that set_mode.py uses from orchestrator_loop
        with mock.patch.object(set_mode_mod, "load_config", create=True, return_value=base_config), \
             mock.patch.object(set_mode_mod, "build_session", create=True, return_value=mock.MagicMock()), \
             mock.patch.object(mode_controller, "get_quota_pct", return_value=0.50), \
             mock.patch.object(mode_controller, "_post_transition_dm"):
            set_mode_mod.main(["light", "--reason", "overnight_schedule"])

        state = json.loads(tmp_mode_state.read_text())
        assert state["mode"] == "light"
        assert state["reason"] == "overnight_schedule"
        assert state["actor"] == "cron"  # default actor


# ---------------------------------------------------------------------------
# Additional tests: orchestrator_loop integration
# ---------------------------------------------------------------------------


class TestOrchestratorLoopModeGates:
    def test_orchestrator_minimal_mode_skips_poll(self, base_config, tmp_path):
        """In minimal mode, main() should return early without fetching queue."""
        import orchestrator_loop

        config = {
            **base_config,
            "agents": {"github_logins": {}, "pr_repos": []},
            "thresholds": {},
            "interventions": {"rebase": {"enabled": False}, "release": {"enabled": True}},
            "workdir": str(tmp_path),
            "log_path": str(tmp_path / "test.log"),
            "lock_path": str(tmp_path / "test.lock"),
        }

        with mock.patch.object(orchestrator_loop, "load_config", return_value=config), \
             mock.patch.object(orchestrator_loop, "build_session") as mock_session, \
             mock.patch("mode_controller.check_and_auto_transition", return_value="minimal"), \
             mock.patch("mode_controller.mode_allows", side_effect=lambda m, j: j == "shell_infra"), \
             mock.patch.object(orchestrator_loop, "fetch_queue") as mock_fetch, \
             mock.patch.object(orchestrator_loop, "setup_logging"), \
             mock.patch("fcntl.flock"):

            orchestrator_loop.main(["--config", "dummy"])

            mock_fetch.assert_not_called()

    def test_orchestrator_briefing_gate_in_light_mode(self, base_config, tmp_path):
        """In light mode, --briefing-only should exit without calling run_briefing."""
        import orchestrator_loop

        config = {
            **base_config,
            "agents": {"github_logins": {}, "pr_repos": []},
            "thresholds": {},
            "interventions": {"rebase": {"enabled": False}, "release": {"enabled": True}},
            "workdir": str(tmp_path),
            "log_path": str(tmp_path / "test.log"),
            "lock_path": str(tmp_path / "test.lock"),
        }

        with mock.patch.object(orchestrator_loop, "load_config", return_value=config), \
             mock.patch.object(orchestrator_loop, "build_session"), \
             mock.patch("mode_controller.check_and_auto_transition", return_value="light"), \
             mock.patch("mode_controller.mode_allows", side_effect=lambda m, j: j in ("shell_infra", "orchestrator_poll")), \
             mock.patch.object(orchestrator_loop, "run_briefing") as mock_briefing, \
             mock.patch.object(orchestrator_loop, "setup_logging"), \
             mock.patch("fcntl.flock"):

            orchestrator_loop.main(["--briefing-only", "--config", "dummy"])

            mock_briefing.assert_not_called()
