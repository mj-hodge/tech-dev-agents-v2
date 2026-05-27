"""STORY-508: Skill file structure and script existence tests.

Phase 7 — RED state.

Covers:
- AC-5:  alert-handler/SKILL.md — 5 playbooks + audit log reference
- AC-7:  alert-handler/SKILL.md references /home/hermes/state/morris/alert-log.md
- AC-8:  update-baselines.sh script existence and content
- AC-9:  weekly-fleet-report/SKILL.md structure and KPIs
- AC-10: morris-fleet-check.sh cron cadence comment (*/30 → 0 */2)
- AC-11: alert-handler/SKILL.md fallback guard (60min webhook + 90min cron → polling)

RED reasons:
- All referenced files do not yet exist (alert-handler/SKILL.md, update-baselines.sh,
  weekly-fleet-report/SKILL.md)
- morris-fleet-check.sh does not yet have a STORY-508 cadence comment

All tests pass after Phase 8 creates these files.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SKILLS_DIR = REPO_ROOT / "deployment" / "vm" / "skills"
VM_DIR = REPO_ROOT / "deployment" / "vm"
SCRIPTS_DIR = REPO_ROOT / "deployment" / "vm" / "scripts"

# alert-handler may live directly under skills/ or under skills/morris/
_ALERT_HANDLER_CANDIDATES = [
    SKILLS_DIR / "alert-handler" / "SKILL.md",
    SKILLS_DIR / "morris" / "alert-handler" / "SKILL.md",
]

# weekly-fleet-report may live directly under skills/ or under skills/morris/
_WEEKLY_REPORT_CANDIDATES = [
    SKILLS_DIR / "weekly-fleet-report" / "SKILL.md",
    SKILLS_DIR / "morris" / "weekly-fleet-report" / "SKILL.md",
]


def _find_alert_handler() -> Path | None:
    for p in _ALERT_HANDLER_CANDIDATES:
        if p.exists():
            return p
    return None


def _find_weekly_report() -> Path | None:
    for p in _WEEKLY_REPORT_CANDIDATES:
        if p.exists():
            return p
    return None


# ---------------------------------------------------------------------------
# AC-5: alert-handler/SKILL.md — playbook sections
# ---------------------------------------------------------------------------


class TestAlertHandlerSkill:
    """AC-5: alert-handler/SKILL.md must exist with 5 named playbooks."""

    def test_alert_handler_skill_exists(self):
        """AC-5: alert-handler/SKILL.md must be created.

        RED: File does not yet exist.
        GREEN after: Create deployment/vm/skills/alert-handler/SKILL.md.

        The file may live at either:
          - deployment/vm/skills/alert-handler/SKILL.md
          - deployment/vm/skills/morris/alert-handler/SKILL.md
        """
        found = _find_alert_handler()
        assert found is not None, (
            "alert-handler/SKILL.md not found in either:\n"
            f"  {_ALERT_HANDLER_CANDIDATES[0]}\n"
            f"  {_ALERT_HANDLER_CANDIDATES[1]}\n"
            "AC-5 requires creating this skill with 5 playbook sections: "
            "ClaimTimeoutsHigh, PartialPROpens, Phase8P95High, PausedOver24h, RateLimitDeferralSpike."
        )

    def test_alert_handler_has_yaml_frontmatter_name(self):
        """AC-5: SKILL.md must have YAML frontmatter with name: alert-handler."""
        found = _find_alert_handler()
        if found is None:
            pytest.skip("alert-handler/SKILL.md not yet created")
        content = found.read_text()
        assert content.startswith("---"), (
            "alert-handler/SKILL.md must start with YAML frontmatter (---)."
        )
        assert "name: alert-handler" in content, (
            "YAML frontmatter must include 'name: alert-handler'. "
            "This allows the dispatch poller to identify and invoke the skill."
        )

    def test_alert_handler_has_triggers(self):
        """AC-5: SKILL.md frontmatter must have a triggers section."""
        found = _find_alert_handler()
        if found is None:
            pytest.skip("alert-handler/SKILL.md not yet created")
        content = found.read_text()
        assert "triggers:" in content, (
            "alert-handler/SKILL.md frontmatter must include a 'triggers:' section. "
            "Expected triggers: grafana alert, alert received, webhook alert, etc."
        )

    def test_alert_handler_has_claim_timeouts_high_playbook(self):
        """AC-5: ClaimTimeoutsHigh playbook section must be present.

        RED: File not yet created.

        ClaimTimeoutsHigh fires when claim timeout rate spikes.
        Playbook should: check queue for claims older than 30min, DM Mark, optionally unclaim.
        """
        found = _find_alert_handler()
        if found is None:
            pytest.skip("alert-handler/SKILL.md not yet created")
        content = found.read_text()
        assert "ClaimTimeoutsHigh" in content, (
            "alert-handler/SKILL.md missing 'ClaimTimeoutsHigh' playbook section. "
            "AC-5 requires this playbook for handling claim timeout alerts from Grafana."
        )

    def test_alert_handler_has_partial_pr_opens_playbook(self):
        """AC-5: PartialPROpens playbook must be present and instruct NOT to auto-remediate.

        RED: File not yet created.

        CRITICAL: This playbook must explicitly say NOT to auto-close/merge partial PRs.
        STORY-507 AC-4 manages partial-PR lifecycle — alert-handler observes only.
        """
        found = _find_alert_handler()
        if found is None:
            pytest.skip("alert-handler/SKILL.md not yet created")
        content = found.read_text()
        assert "PartialPROpens" in content, (
            "alert-handler/SKILL.md missing 'PartialPROpens' playbook section. "
            "AC-5 requires this playbook. It must say: do NOT auto-close or merge partial PRs. "
            "Only log, DM Mark, and observe — STORY-507 AC-4 handles partial-PR lifecycle."
        )

    def test_alert_handler_partial_pr_playbook_says_no_auto_remediate(self):
        """AC-5: PartialPROpens playbook must explicitly forbid auto-remediation."""
        found = _find_alert_handler()
        if found is None:
            pytest.skip("alert-handler/SKILL.md not yet created")
        content = found.read_text()
        if "PartialPROpens" not in content:
            pytest.skip("PartialPROpens playbook not yet present")

        # Find the PartialPROpens section and check within a reasonable window
        idx = content.find("PartialPROpens")
        window = content[idx : idx + 600].lower()
        has_no_action = (
            "do not" in window
            or "don't" in window
            or "never" in window
            or "observe only" in window
            or "no auto" in window
        )
        assert has_no_action, (
            "PartialPROpens playbook should explicitly say 'Do NOT auto-close/merge partial PRs'. "
            "STORY-507 AC-4 manages the lifecycle — Morris should only log and DM Mark."
        )

    def test_alert_handler_has_phase8_p95_high_playbook(self):
        """AC-5: Phase8P95High playbook must be present.

        RED: File not yet created.

        Phase8P95High fires when Phase 8 execution P95 latency is above threshold.
        Playbook should: pull structured logs from Loki for the slow stories,
        identify bottleneck, DM Mark with analysis.
        """
        found = _find_alert_handler()
        if found is None:
            pytest.skip("alert-handler/SKILL.md not yet created")
        content = found.read_text()
        assert "Phase8P95High" in content, (
            "alert-handler/SKILL.md missing 'Phase8P95High' playbook section. "
            "AC-5 requires this playbook to pull structured logs when P95 execution time spikes."
        )

    def test_alert_handler_has_paused_over_24h_playbook(self):
        """AC-5: PausedOver24h playbook must be present.

        RED: File not yet created.

        PausedOver24h fires when a story has been in 'paused' status for >24 hours.
        Playbook should: force unclaim via API + bump priority to 90.
        """
        found = _find_alert_handler()
        if found is None:
            pytest.skip("alert-handler/SKILL.md not yet created")
        content = found.read_text()
        assert "PausedOver24h" in content, (
            "alert-handler/SKILL.md missing 'PausedOver24h' playbook section. "
            "AC-5 requires this playbook to handle stories paused >24h: "
            "force unclaim + set priority=90 via POST /api/dispatch/priority."
        )

    def test_alert_handler_has_rate_limit_deferral_spike_playbook(self):
        """AC-5: RateLimitDeferralSpike playbook must be present.

        RED: File not yet created.

        RateLimitDeferralSpike fires when the rate of rate-limit deferrals in the dispatch
        poller spikes. Playbook should: DM Mark with agent breakdown, consider pausing low-priority stories.
        """
        found = _find_alert_handler()
        if found is None:
            pytest.skip("alert-handler/SKILL.md not yet created")
        content = found.read_text()
        assert "RateLimitDeferralSpike" in content, (
            "alert-handler/SKILL.md missing 'RateLimitDeferralSpike' playbook section. "
            "AC-5 requires this playbook for rate-limit deferral spike handling."
        )

    def test_alert_handler_has_audit_log_section(self):
        """AC-5: SKILL.md must reference audit logging (alert-log.md).

        Playbooks must log every alert they handle to the alert log.
        RED: File not yet created.
        """
        found = _find_alert_handler()
        if found is None:
            pytest.skip("alert-handler/SKILL.md not yet created")
        content = found.read_text()
        has_log_ref = "alert-log" in content or "alert_log" in content
        assert has_log_ref, (
            "alert-handler/SKILL.md has no reference to the alert log. "
            "AC-5 requires every playbook to log actions to alert-log.md for audit trail."
        )


# ---------------------------------------------------------------------------
# AC-7 (skill side): alert-handler references alert-log.md path
# ---------------------------------------------------------------------------


class TestAlertHandlerLogPath:
    """AC-7: alert-handler/SKILL.md must reference the canonical alert-log.md path."""

    def test_alert_handler_references_alert_log_path(self):
        """AC-7: SKILL.md must reference /home/hermes/state/morris/alert-log.md.

        RED: File not yet created.
        GREEN after: Add log path reference to alert-handler/SKILL.md.
        """
        found = _find_alert_handler()
        if found is None:
            pytest.skip("alert-handler/SKILL.md not yet created")
        content = found.read_text()
        assert "/home/hermes/state/morris/alert-log.md" in content, (
            "alert-handler/SKILL.md does not reference the canonical alert log path. "
            "AC-7 requires the skill to write log entries to: /home/hermes/state/morris/alert-log.md"
        )


# ---------------------------------------------------------------------------
# AC-8: update-baselines.sh
# ---------------------------------------------------------------------------


class TestUpdateBaselinesScript:
    """AC-8: update-baselines.sh must exist as an executable script."""

    SCRIPT_PATH = SCRIPTS_DIR / "update-baselines.sh"

    # Alternative location at vm/ root
    SCRIPT_ALT_PATH = VM_DIR / "update-baselines.sh"

    def _find_script(self) -> Path | None:
        for p in [self.SCRIPT_PATH, self.SCRIPT_ALT_PATH]:
            if p.exists():
                return p
        return None

    def test_update_baselines_script_exists(self):
        """AC-8: update-baselines.sh must be created.

        RED: File does not yet exist.
        GREEN after: Create deployment/vm/scripts/update-baselines.sh.

        This script queries Prometheus for current P50/P95 metrics and writes
        updated baselines to /home/hermes/state/morris/metrics-baselines.md.
        """
        found = self._find_script()
        assert found is not None, (
            f"update-baselines.sh not found at:\n"
            f"  {self.SCRIPT_PATH}\n"
            f"  {self.SCRIPT_ALT_PATH}\n"
            "AC-8 requires creating this script. It should query Prometheus for fleet KPIs "
            "and write updated baselines to /home/hermes/state/morris/metrics-baselines.md."
        )

    def test_update_baselines_script_is_executable(self):
        """AC-8: update-baselines.sh must be executable.

        RED: File does not yet exist.
        """
        found = self._find_script()
        if found is None:
            pytest.skip("update-baselines.sh not yet created")
        assert os.access(str(found), os.X_OK), (
            f"{found} exists but is not executable. "
            "Run: chmod +x deployment/vm/scripts/update-baselines.sh"
        )

    def test_update_baselines_references_prometheus_url(self):
        """AC-8: Script must reference PROMETHEUS_URL variable.

        RED: File does not yet exist.
        """
        found = self._find_script()
        if found is None:
            pytest.skip("update-baselines.sh not yet created")
        content = found.read_text()
        assert "PROMETHEUS_URL" in content, (
            "update-baselines.sh does not reference PROMETHEUS_URL. "
            "The script must query Prometheus via its URL (e.g., PROMETHEUS_URL=${PROMETHEUS_URL:-http://...})."
        )

    def test_update_baselines_references_baselines_file(self):
        """AC-8: Script must reference /home/hermes/state/morris/metrics-baselines.md.

        RED: File does not yet exist.
        """
        found = self._find_script()
        if found is None:
            pytest.skip("update-baselines.sh not yet created")
        content = found.read_text()
        assert "metrics-baselines" in content, (
            "update-baselines.sh does not reference the baselines output file. "
            "The script must write to /home/hermes/state/morris/metrics-baselines.md."
        )

    def test_update_baselines_references_p95_or_histogram(self):
        """AC-8: Script must contain P95 or histogram_quantile concept.

        RED: File does not yet exist.
        """
        found = self._find_script()
        if found is None:
            pytest.skip("update-baselines.sh not yet created")
        content = found.read_text()
        has_p95 = "P95" in content or "p95" in content or "histogram_quantile" in content or "0.95" in content
        assert has_p95, (
            "update-baselines.sh does not reference P95 or histogram_quantile. "
            "The script should capture P50 and P95 latency percentiles as baselines."
        )


# ---------------------------------------------------------------------------
# AC-9: weekly-fleet-report/SKILL.md
# ---------------------------------------------------------------------------


class TestWeeklyFleetReportSkill:
    """AC-9: weekly-fleet-report/SKILL.md must exist with all required KPI sections."""

    def test_weekly_fleet_report_skill_exists(self):
        """AC-9: weekly-fleet-report/SKILL.md must be created.

        RED: File does not yet exist.
        GREEN after: Create deployment/vm/skills/weekly-fleet-report/SKILL.md.

        The file may live at either:
          - deployment/vm/skills/weekly-fleet-report/SKILL.md
          - deployment/vm/skills/morris/weekly-fleet-report/SKILL.md
        """
        found = _find_weekly_report()
        assert found is not None, (
            "weekly-fleet-report/SKILL.md not found in either:\n"
            f"  {_WEEKLY_REPORT_CANDIDATES[0]}\n"
            f"  {_WEEKLY_REPORT_CANDIDATES[1]}\n"
            "AC-9 requires creating this skill with KPI sections for the weekly fleet report."
        )

    def test_weekly_report_has_partial_pr_kpi(self):
        """AC-9: SKILL.md must include a 'partial' PR count KPI section.

        RED: File not yet created.
        """
        found = _find_weekly_report()
        if found is None:
            pytest.skip("weekly-fleet-report/SKILL.md not yet created")
        content = found.read_text().lower()
        assert "partial" in content, (
            "weekly-fleet-report/SKILL.md missing 'partial' PR count KPI. "
            "AC-9 requires a section reporting the number of partial PRs opened during the week."
        )

    def test_weekly_report_has_completion_rate_kpi(self):
        """AC-9: SKILL.md must include a completion rate KPI section.

        RED: File not yet created.
        """
        found = _find_weekly_report()
        if found is None:
            pytest.skip("weekly-fleet-report/SKILL.md not yet created")
        content = found.read_text().lower()
        assert "completion rate" in content or "completion_rate" in content, (
            "weekly-fleet-report/SKILL.md missing 'completion rate' KPI. "
            "AC-9 requires a section reporting story completion rate (completed / total dispatched)."
        )

    def test_weekly_report_has_p50_metric(self):
        """AC-9: SKILL.md must include P50 dispatch-to-merge metric.

        RED: File not yet created.
        """
        found = _find_weekly_report()
        if found is None:
            pytest.skip("weekly-fleet-report/SKILL.md not yet created")
        content = found.read_text()
        assert "P50" in content or "p50" in content.lower(), (
            "weekly-fleet-report/SKILL.md missing P50 dispatch-to-merge metric. "
            "AC-9 requires P50 (median) latency from dispatch to merge as a KPI."
        )

    def test_weekly_report_has_alert_count_section(self):
        """AC-9: SKILL.md must include an alert count section.

        RED: File not yet created.
        """
        found = _find_weekly_report()
        if found is None:
            pytest.skip("weekly-fleet-report/SKILL.md not yet created")
        content = found.read_text().lower()
        has_alerts = "alert" in content and ("count" in content or "total" in content or "fired" in content)
        assert has_alerts, (
            "weekly-fleet-report/SKILL.md missing alert count KPI. "
            "AC-9 requires a section counting alerts fired during the week by severity."
        )

    def test_weekly_report_has_cron_schedule(self):
        """AC-9: SKILL.md must contain a cron schedule (Friday morning = 0 10 * * 5 or similar).

        RED: File not yet created.
        """
        found = _find_weekly_report()
        if found is None:
            pytest.skip("weekly-fleet-report/SKILL.md not yet created")
        content = found.read_text()
        # Accept common Friday schedule patterns
        has_cron = (
            "0 10 * * 5" in content
            or "* * 5" in content
            or "Friday" in content
            or "friday" in content.lower()
            or "weekly" in content.lower()
        )
        assert has_cron, (
            "weekly-fleet-report/SKILL.md missing cron schedule. "
            "AC-9 requires a scheduled execution definition (e.g., '0 10 * * 5' = Friday 10:00 UTC). "
            "Add a 'triggers:' entry or cron section."
        )

    def test_weekly_report_has_teams_dm_delivery(self):
        """AC-9: SKILL.md must include a Teams DM delivery instruction.

        RED: File not yet created.
        """
        found = _find_weekly_report()
        if found is None:
            pytest.skip("weekly-fleet-report/SKILL.md not yet created")
        content = found.read_text().lower()
        has_teams = "teams" in content or "dm mark" in content or "direct message" in content
        assert has_teams, (
            "weekly-fleet-report/SKILL.md missing Teams DM delivery instruction. "
            "AC-9 requires the report to be delivered as a Teams DM to Mark."
        )

    def test_weekly_report_has_archive_path(self):
        """AC-9: SKILL.md must reference the weekly-reports/ archive directory.

        RED: File not yet created.
        """
        found = _find_weekly_report()
        if found is None:
            pytest.skip("weekly-fleet-report/SKILL.md not yet created")
        content = found.read_text()
        assert "weekly-reports" in content or "weekly_reports" in content, (
            "weekly-fleet-report/SKILL.md missing archive path reference. "
            "AC-9 requires reports to be archived at /home/hermes/state/morris/weekly-reports/."
        )


# ---------------------------------------------------------------------------
# AC-10: Cron cadence change comment in morris-fleet-check.sh
# ---------------------------------------------------------------------------


class TestFleetCheckCronCadence:
    """AC-10: morris-fleet-check.sh must document the cadence change from */30 to 0 */2."""

    FLEET_CHECK_SCRIPT = VM_DIR / "morris-fleet-check.sh"

    def test_fleet_check_script_exists(self):
        """Prerequisite: morris-fleet-check.sh must exist (it already does)."""
        assert self.FLEET_CHECK_SCRIPT.exists(), (
            f"morris-fleet-check.sh not found at {self.FLEET_CHECK_SCRIPT}. "
            "This is a pre-existing file — it should not have been deleted."
        )

    def test_fleet_check_has_cadence_comment(self):
        """AC-10: morris-fleet-check.sh must have a comment about the 2-hour cadence change.

        RED: The script currently says '*/30 * * * *' with no STORY-508 note.
        GREEN after: Add a comment explaining the cadence change to '0 */2 * * *'.

        The new schedule fires at the top of every even hour:
          Old: */30 * * * *  (every 30 minutes)
          New: 0 */2 * * *   (top of every 2nd hour)

        Rationale: Fleet checks are now data-light (Grafana handles alerting);
        reducing frequency from 48/day to 12/day saves ~72% token cost on fleet overhead.
        """
        if not self.FLEET_CHECK_SCRIPT.exists():
            pytest.skip("morris-fleet-check.sh not found")
        content = self.FLEET_CHECK_SCRIPT.read_text()

        # Check for any indication of the cadence change
        has_cadence_note = (
            "0 */2" in content
            or "every 2 hours" in content.lower()
            or "every two hours" in content.lower()
            or "STORY-508" in content
            or ("*/30" in content and "0 */2" in content)
        )
        assert has_cadence_note, (
            "morris-fleet-check.sh does not document the cadence change. "
            "AC-10 requires updating the cron comment in the script header from:\n"
            "  # Cron: */30 * * * *\n"
            "to:\n"
            "  # Cron: 0 */2 * * *  (STORY-508: reduced from */30 to every 2h, "
            "Grafana handles real-time alerting)\n"
            "Also update /etc/cron.d/morris-fleet-check on the Morris VM."
        )


# ---------------------------------------------------------------------------
# AC-11: Fallback guard in alert-handler/SKILL.md
# ---------------------------------------------------------------------------


class TestAlertHandlerFallbackGuard:
    """AC-11: alert-handler/SKILL.md must contain a fallback guard section.

    If no Grafana webhook arrives in 60 minutes AND no fleet-check cron fires in 90 minutes,
    Morris must fall back to polling mode and DM Mark.
    """

    def test_alert_handler_has_fallback_section(self):
        """AC-11: SKILL.md must contain a 'fallback' or 'Fallback' section.

        RED: File not yet created.
        GREEN after: Add a 'Fallback Guard' section to alert-handler/SKILL.md.
        """
        found = _find_alert_handler()
        if found is None:
            pytest.skip("alert-handler/SKILL.md not yet created")
        content = found.read_text()
        has_fallback = "fallback" in content.lower() or "Fallback" in content
        assert has_fallback, (
            "alert-handler/SKILL.md missing fallback guard section. "
            "AC-11 requires a section describing what Morris does when "
            "both the Grafana webhook AND the fleet-check cron go silent."
        )

    def test_alert_handler_fallback_references_60_minutes(self):
        """AC-11: Fallback section must reference 60-minute webhook silence threshold.

        RED: File not yet created.
        """
        found = _find_alert_handler()
        if found is None:
            pytest.skip("alert-handler/SKILL.md not yet created")
        content = found.read_text()
        has_60min = (
            "60" in content
            or "60 min" in content.lower()
            or "60-min" in content.lower()
            or "one hour" in content.lower()
        )
        assert has_60min, (
            "alert-handler/SKILL.md fallback guard must reference the 60-minute threshold. "
            "AC-11: If no Grafana webhook has arrived in 60 minutes, start fallback check."
        )

    def test_alert_handler_fallback_references_90_minutes(self):
        """AC-11: Fallback section must reference 90-minute cron silence threshold.

        RED: File not yet created.
        """
        found = _find_alert_handler()
        if found is None:
            pytest.skip("alert-handler/SKILL.md not yet created")
        content = found.read_text()
        has_90min = (
            "90" in content
            or "90 min" in content.lower()
            or "90-min" in content.lower()
        )
        assert has_90min, (
            "alert-handler/SKILL.md fallback guard must reference the 90-minute threshold. "
            "AC-11: If no fleet-check cron has fired in 90 minutes, enter polling mode."
        )

    def test_alert_handler_fallback_references_polling_mode(self):
        """AC-11: Fallback section must describe polling mode behavior.

        RED: File not yet created.
        """
        found = _find_alert_handler()
        if found is None:
            pytest.skip("alert-handler/SKILL.md not yet created")
        content = found.read_text().lower()
        has_polling = "polling" in content or "poll " in content or "manual check" in content
        assert has_polling, (
            "alert-handler/SKILL.md fallback guard must describe polling mode. "
            "AC-11: When both alert sources are silent, fall back to direct queue/agent polling."
        )

    def test_alert_handler_fallback_mentions_dm_mark(self):
        """AC-11: Fallback section must say to DM Mark when entering fallback mode.

        RED: File not yet created.
        """
        found = _find_alert_handler()
        if found is None:
            pytest.skip("alert-handler/SKILL.md not yet created")
        content = found.read_text()
        # Within the fallback section, there should be a mention of DM Mark
        fallback_idx = content.lower().find("fallback")
        if fallback_idx == -1:
            pytest.skip("No fallback section found yet")
        fallback_window = content[fallback_idx : fallback_idx + 800].lower()
        has_dm = (
            "dm mark" in fallback_window
            or "direct message" in fallback_window
            or "message mark" in fallback_window
            or "notify mark" in fallback_window
            or "teams" in fallback_window
        )
        assert has_dm, (
            "alert-handler/SKILL.md fallback guard must instruct Morris to DM Mark "
            "when entering fallback mode. "
            "AC-11: 'DM Mark in Teams: alert system silent for >60min — running manual fleet check'"
        )
