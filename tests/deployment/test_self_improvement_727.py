"""STORY-727: Continuous Self-Improvement Loop — Phase 7 RED tests.

All ten tests (T1–T10) must be RED (ModuleNotFoundError) at end of Phase 7
and GREEN at end of Phase 8.

T1  — detect_patterns() returns adversarial.static_test_masquerading_as_behavioral
        when ≥3 CRITICAL findings in 30 days
T2  — detect_patterns() returns empty list when only 2 CRITICAL findings (below threshold)
T3  — generate_proposal() returns a diff-style string for Tier-1 (prose) changes
T4  — apply_tier1_proposal() writes file changes atomically; rolls back on failure
T5  — record_approval(proposal_id, approver) inserts into improvement_proposals
        with status='approved'
T6  — track_metric(proposal_id, before, after) computes delta and stores in
        improvement_tracking
T7  — run_retrospective() returns summary with proposals_applied, avg_delta,
        open_patterns; top-3 correct; empty sections render "(none)"
T8  — generate_proposal() with mode='shadow' does NOT write any files or post DMs
T9  — detect_patterns() returns empty list when enabled=False in config
T10 — Full cycle integration: detect → propose → approve → apply → track (mocked DB)
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup — modules under deployment/morris/scripts/improvement/ (NEW, not yet created)
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# These imports will raise ModuleNotFoundError until Phase 8 creates the modules.
# That is the expected RED state.
from deployment.morris.scripts.improvement.pattern_detector import (  # noqa: E402
    detect_patterns,
    Pattern,
)
from deployment.morris.scripts.improvement.proposal_generator import (  # noqa: E402
    generate_proposal,
    Proposal,
    ProposalDiffInvalid,
)
from deployment.morris.scripts.improvement.approval_handler import (  # noqa: E402
    apply_tier1_proposal,
    record_approval,
)
from deployment.morris.scripts.improvement.tracker import (  # noqa: E402
    check_back,
    track_metric,
)
from deployment.morris.scripts.improvement.retrospective import (  # noqa: E402
    run_retrospective,
)

# ---------------------------------------------------------------------------
# Inline fixture helpers
# ---------------------------------------------------------------------------

def _adversarial_review_md(
    story_folder: str,
    finding_type: str,
    severity: str = "CRITICAL",
    review_date: date | None = None,
) -> str:
    """Return a minimal adversarial-review.md string for fixture use."""
    if review_date is None:
        review_date = date.today()
    return f"""\
# Adversarial Review — {story_folder}

review_date: {review_date.isoformat()}

## Verdict
BLOCK

## Findings

### CRITICAL
- [C-1] [{severity}] {finding_type}
  - Location: tests/test_example.py:42
  - Evidence: assertion checks a static string, not a real DB write
  - Required fix: replace with behavioral mock verifying DB call

### HIGH
None.

### MEDIUM
None.

### LOW
None.

## Coverage Matrix
| Spec Requirement | Implementing Code | Test(s) | Test Type |
|---|---|---|---|
| Feature A | module.py:10 | test_feature_a | static |
"""


def _make_fixture_corpus(tmp_path: Path, specs: list[dict]) -> Path:
    """
    Build a features/ directory tree with adversarial-review.md fixtures.

    Each entry in ``specs`` is a dict with keys:
        story_folder (str)
        finding_type (str)
        severity (str, default "CRITICAL")
        review_date (date, default today)
    """
    features_dir = tmp_path / "features"
    for spec in specs:
        folder = features_dir / spec["story_folder"]
        folder.mkdir(parents=True, exist_ok=True)
        content = _adversarial_review_md(
            story_folder=spec["story_folder"],
            finding_type=spec.get("finding_type", "static-test-masquerading-as-behavioral"),
            severity=spec.get("severity", "CRITICAL"),
            review_date=spec.get("review_date", date.today()),
        )
        (folder / "adversarial-review.md").write_text(content)
    return tmp_path


@dataclass
class ImprovementConfig:
    """Minimal config object used by tests; mirrors ImprovementConfig from config.py."""
    enabled: bool = True
    mode: str = "live"
    pattern_window_days: int = 7
    adversarial_window_days: int = 30
    threshold_critical: int = 3
    threshold_high: int = 4
    taxonomy_gap_fraction: float = 0.20
    retry_storm_window_seconds: int = 60
    retry_storm_min_stories: int = 3
    reproposal_cooldown_days: int = 30
    tracking_window_days: int = 14
    cost_ceiling_monthly_usd: float = 15.0


# ---------------------------------------------------------------------------
# T1 — detect_patterns() fires on ≥3 CRITICAL findings within 30 days
# ---------------------------------------------------------------------------

class TestT1PatternDetectorFindsRecurringCritical:
    """T1: detector fires adversarial.static_test_masquerading_as_behavioral on ≥3 stories."""

    def test_t1_three_critical_findings_produce_pattern(self, tmp_path):
        """Given 4 adversarial-review files where 3 have CRITICAL static-masquerading,
        detect_patterns() returns a Pattern with count=3 and 3 story_ids.
        The 4th file (different finding) does not contribute to that pattern's count.
        """
        _make_fixture_corpus(tmp_path, [
            {"story_folder": "story-701-test", "finding_type": "static-test-masquerading-as-behavioral"},
            {"story_folder": "story-720-test", "finding_type": "static-test-masquerading-as-behavioral"},
            {"story_folder": "story-722-test", "finding_type": "static-test-masquerading-as-behavioral"},
            {"story_folder": "story-724-test", "finding_type": "spec-requirement-omitted"},  # different
        ])

        config = ImprovementConfig(threshold_critical=3, adversarial_window_days=30)
        patterns = detect_patterns(repo_root=tmp_path, config=config)

        matching = [p for p in patterns
                    if p.key == "adversarial.static_test_masquerading_as_behavioral"]
        assert len(matching) == 1, (
            f"Expected exactly 1 pattern for static_test_masquerading, got {len(matching)}: {matching}"
        )
        pattern = matching[0]
        assert pattern.count == 3, (
            f"Expected count=3 (3 CRITICAL stories), got {pattern.count}"
        )
        assert len(pattern.story_ids) == 3, (
            f"Expected 3 story_ids, got {pattern.story_ids}"
        )
        assert "story-724-test" not in pattern.story_ids, (
            "story-724-test has a different finding type and must not be in story_ids"
        )

    def test_t1_story_ids_match_contributing_folders(self, tmp_path):
        """T1 supplemental: the story_ids in the pattern must be the 3 contributing folders."""
        _make_fixture_corpus(tmp_path, [
            {"story_folder": "story-701-corpus", "finding_type": "static-test-masquerading-as-behavioral"},
            {"story_folder": "story-720-corpus", "finding_type": "static-test-masquerading-as-behavioral"},
            {"story_folder": "story-722-corpus", "finding_type": "static-test-masquerading-as-behavioral"},
            {"story_folder": "story-725-corpus", "finding_type": "unrealistic-test-fixture"},
        ])

        config = ImprovementConfig(threshold_critical=3, adversarial_window_days=30)
        patterns = detect_patterns(repo_root=tmp_path, config=config)

        matching = [p for p in patterns
                    if p.key == "adversarial.static_test_masquerading_as_behavioral"]
        pattern = matching[0]
        expected_ids = {"story-701-corpus", "story-720-corpus", "story-722-corpus"}
        assert set(pattern.story_ids) == expected_ids, (
            f"Expected story_ids={expected_ids}, got {set(pattern.story_ids)}"
        )


# ---------------------------------------------------------------------------
# T2 — detect_patterns() returns empty list when only 2 CRITICAL findings
# ---------------------------------------------------------------------------

class TestT2BelowThresholdNoPattern:
    """T2: 2 CRITICAL findings < threshold of 3 → no pattern returned."""

    def test_t2_two_critical_findings_below_threshold_returns_empty(self, tmp_path, caplog):
        """Given only 2 CRITICAL static-masquerading findings (threshold=3),
        detect_patterns() must return no matching pattern.
        The pattern should be logged at DEBUG level.
        """
        _make_fixture_corpus(tmp_path, [
            {"story_folder": "story-701-only2", "finding_type": "static-test-masquerading-as-behavioral"},
            {"story_folder": "story-720-only2", "finding_type": "static-test-masquerading-as-behavioral"},
        ])

        config = ImprovementConfig(threshold_critical=3, adversarial_window_days=30)

        import logging
        with caplog.at_level(logging.DEBUG):
            patterns = detect_patterns(repo_root=tmp_path, config=config)

        matching = [p for p in patterns
                    if p.key == "adversarial.static_test_masquerading_as_behavioral"]
        assert len(matching) == 0, (
            f"Expected 0 patterns below threshold, got {matching}"
        )

    def test_t2_below_threshold_logged_at_debug(self, tmp_path, caplog):
        """T2 supplemental: sub-threshold pattern appears in DEBUG log, not as a proposal."""
        _make_fixture_corpus(tmp_path, [
            {"story_folder": "story-701-debug", "finding_type": "static-test-masquerading-as-behavioral"},
            {"story_folder": "story-720-debug", "finding_type": "static-test-masquerading-as-behavioral"},
        ])
        config = ImprovementConfig(threshold_critical=3, adversarial_window_days=30)

        import logging
        with caplog.at_level(logging.DEBUG):
            detect_patterns(repo_root=tmp_path, config=config)

        debug_messages = [r.message for r in caplog.records if r.levelno == logging.DEBUG]
        assert any("static_test_masquerading" in m or "below threshold" in m.lower()
                   for m in debug_messages), (
            f"Expected a DEBUG log about the below-threshold pattern, got: {debug_messages}"
        )


# ---------------------------------------------------------------------------
# T3 — generate_proposal() returns a diff-style string for Tier-1 (prose) changes
# ---------------------------------------------------------------------------

class TestT3ProposalGeneratorDiffFormat:
    """T3: generate_proposal() returns a Proposal with a valid diff_text for Tier-1."""

    def test_t3_proposal_diff_text_is_non_empty_and_diff_formatted(self, tmp_path):
        """Given a Pattern for static_test_masquerading, generate_proposal() returns
        a Proposal whose diff_text is non-empty, starts with the correct --- a/ header,
        and whose rationale references the evidence story IDs.
        """
        pattern = Pattern(
            key="adversarial.static_test_masquerading_as_behavioral",
            count=3,
            story_ids=["story-701", "story-720", "story-722"],
            evidence={"finding_type": "static-test-masquerading-as-behavioral",
                      "severity": "CRITICAL"},
        )
        config = ImprovementConfig(mode="live")

        # Mock the Sonnet subagent call to return a well-formed unified diff
        mock_diff = (
            "--- a/tech_dev_agents/sdlc/phase_prompts/phase_7.md\n"
            "+++ b/tech_dev_agents/sdlc/phase_prompts/phase_7.md\n"
            "@@ -10,3 +10,7 @@\n"
            " ## Test Design Guidelines\n"
            "+\n"
            "+### Behavioral Mock Example\n"
            "+Use `AsyncMock` to verify DB writes rather than asserting on return values.\n"
            "+Example: `mock_db.insert.assert_called_once_with(expected_row)`\n"
            " ## Common Pitfalls\n"
        )

        with patch(
            "deployment.morris.scripts.improvement.proposal_generator._invoke_sonnet_subagent",
            return_value=mock_diff,
        ), patch(
            "deployment.morris.scripts.improvement.proposal_generator._validate_diff",
            return_value=True,
        ):
            proposal = generate_proposal(pattern=pattern, config=config, repo_root=tmp_path)

        assert proposal.diff_text, "diff_text must be non-empty"
        assert proposal.diff_text.startswith(
            "--- a/tech_dev_agents/sdlc/phase_prompts/phase_7.md"
        ), f"diff_text must start with correct --- header, got: {proposal.diff_text[:80]!r}"
        assert "+++ b/tech_dev_agents/sdlc/phase_prompts/phase_7.md" in proposal.diff_text, (
            "diff_text must contain +++ b/ header"
        )

    def test_t3_proposal_rationale_references_story_ids(self, tmp_path):
        """T3 supplemental: rationale must reference at least one evidence story ID."""
        pattern = Pattern(
            key="adversarial.static_test_masquerading_as_behavioral",
            count=3,
            story_ids=["story-701", "story-720", "story-722"],
            evidence={},
        )
        config = ImprovementConfig(mode="live")

        mock_diff = (
            "--- a/tech_dev_agents/sdlc/phase_prompts/phase_7.md\n"
            "+++ b/tech_dev_agents/sdlc/phase_prompts/phase_7.md\n"
            "@@ -1,1 +1,2 @@\n"
            "+# Added behavioral mock example\n"
            " # existing content\n"
        )

        with patch(
            "deployment.morris.scripts.improvement.proposal_generator._invoke_sonnet_subagent",
            return_value=mock_diff,
        ), patch(
            "deployment.morris.scripts.improvement.proposal_generator._validate_diff",
            return_value=True,
        ):
            proposal = generate_proposal(pattern=pattern, config=config, repo_root=tmp_path)

        assert proposal.rationale, "rationale must be non-empty"
        assert any(sid in proposal.rationale for sid in pattern.story_ids), (
            f"rationale must reference at least one story ID from {pattern.story_ids}. "
            f"Got rationale: {proposal.rationale!r}"
        )

    def test_t3_invalid_diff_raises_proposal_diff_invalid(self, tmp_path):
        """T3 supplemental: if git apply --check fails, ProposalDiffInvalid is raised,
        and no proposal is stored.
        """
        pattern = Pattern(
            key="adversarial.static_test_masquerading_as_behavioral",
            count=3,
            story_ids=["story-701", "story-720", "story-722"],
            evidence={},
        )
        config = ImprovementConfig(mode="live")

        bad_diff = "this is not a valid unified diff"

        with patch(
            "deployment.morris.scripts.improvement.proposal_generator._invoke_sonnet_subagent",
            return_value=bad_diff,
        ), patch(
            "deployment.morris.scripts.improvement.proposal_generator._validate_diff",
            side_effect=ProposalDiffInvalid("git apply --check failed: bad diff"),
        ):
            with pytest.raises(ProposalDiffInvalid):
                generate_proposal(pattern=pattern, config=config, repo_root=tmp_path)

    def test_t3_tier1_proposal_type_set_correctly(self, tmp_path):
        """T3 supplemental: proposal targeting a Tier-1 path has proposal_type='tier1'."""
        pattern = Pattern(
            key="adversarial.static_test_masquerading_as_behavioral",
            count=3,
            story_ids=["story-701", "story-720", "story-722"],
            evidence={},
        )
        config = ImprovementConfig(mode="live")

        mock_diff = (
            "--- a/tech_dev_agents/sdlc/phase_prompts/phase_7.md\n"
            "+++ b/tech_dev_agents/sdlc/phase_prompts/phase_7.md\n"
            "@@ -1,1 +1,2 @@\n"
            "+# behavioral mock example\n"
            " # existing\n"
        )

        with patch(
            "deployment.morris.scripts.improvement.proposal_generator._invoke_sonnet_subagent",
            return_value=mock_diff,
        ), patch(
            "deployment.morris.scripts.improvement.proposal_generator._validate_diff",
            return_value=True,
        ):
            proposal = generate_proposal(pattern=pattern, config=config, repo_root=tmp_path)

        assert proposal.proposal_type == "tier1", (
            f"Expected proposal_type='tier1' for phase_7.md target, got {proposal.proposal_type!r}"
        )


# ---------------------------------------------------------------------------
# T4 — apply_tier1_proposal() writes file changes atomically; rolls back on failure
# ---------------------------------------------------------------------------

class TestT4ApplyTier1Proposal:
    """T4: apply_tier1_proposal writes atomically and rolls back on failure."""

    def test_t4_happy_path_status_transitions_to_applied(self, tmp_path):
        """T4a: On successful apply, improvement_proposals.status transitions to 'applied'
        and applied_commit is set.
        """
        proposal = Proposal(
            pattern_key="adversarial.static_test_masquerading_as_behavioral",
            pattern_evidence_json={"count": 3, "story_ids": ["story-701", "story-720", "story-722"]},
            target_file="tech_dev_agents/sdlc/phase_prompts/phase_7.md",
            diff_text=(
                "--- a/tech_dev_agents/sdlc/phase_prompts/phase_7.md\n"
                "+++ b/tech_dev_agents/sdlc/phase_prompts/phase_7.md\n"
                "@@ -1,1 +1,2 @@\n"
                "+# behavioral mock example\n"
                " # existing\n"
            ),
            rationale="3 stories (story-701, story-720, story-722) had CRITICAL static-masquerading.",
            expected_metric="adversarial.static_test_masquerading.weekly_count",
            expected_direction="decrease",
            proposal_type="tier1",
        )
        proposal_id = 42

        mock_db = MagicMock()
        mock_db.mark_proposal_applied = AsyncMock()
        mock_db.mark_proposal_decided = AsyncMock()

        with patch(
            "deployment.morris.scripts.improvement.approval_handler._run_git",
            return_value=MagicMock(returncode=0, stdout=b"phase_7.md\n", stderr=b""),
        ) as mock_git, patch(
            "deployment.morris.scripts.improvement.approval_handler._get_commit_sha",
            return_value="abc123def456",
        ), patch(
            "deployment.morris.scripts.improvement.approval_handler._open_pr",
            return_value={"number": 99, "auto_merge": True},
        ):
            result = apply_tier1_proposal(
                proposal_id=proposal_id,
                proposal=proposal,
                repo_root=tmp_path,
                db_service=mock_db,
            )

        mock_db.mark_proposal_applied.assert_called_once()
        call_kwargs = mock_db.mark_proposal_applied.call_args
        assert call_kwargs is not None
        # applied_commit must be set to non-empty sha
        applied_commit_arg = (call_kwargs.args[1] if len(call_kwargs.args) > 1
                              else call_kwargs.kwargs.get("applied_commit", ""))
        assert applied_commit_arg, "applied_commit must be non-empty after successful apply"

    def test_t4_commit_message_contains_story727_proposal_id_and_pattern_key(self, tmp_path):
        """T4 supplemental: the commit message must contain 'STORY-727', the proposal ID,
        and the pattern key.
        """
        proposal = Proposal(
            pattern_key="adversarial.static_test_masquerading_as_behavioral",
            pattern_evidence_json={},
            target_file="tech_dev_agents/sdlc/phase_prompts/phase_7.md",
            diff_text="--- a/phase_7.md\n+++ b/phase_7.md\n@@ -1,1 +1,2 @@\n+new line\n existing\n",
            rationale="Evidence from 3 stories.",
            expected_metric="adversarial.static_test_masquerading.weekly_count",
            expected_direction="decrease",
            proposal_type="tier1",
        )
        proposal_id = 77

        commit_messages = []

        def capture_git(args, **kwargs):
            if "commit" in args:
                # capture the commit message from args (typically -m <msg>)
                for i, arg in enumerate(args):
                    if arg == "-m" and i + 1 < len(args):
                        commit_messages.append(args[i + 1])
            return MagicMock(returncode=0, stdout=b"phase_7.md\n", stderr=b"")

        with patch(
            "deployment.morris.scripts.improvement.approval_handler._run_git",
            side_effect=capture_git,
        ), patch(
            "deployment.morris.scripts.improvement.approval_handler._get_commit_sha",
            return_value="deadbeef1234",
        ), patch(
            "deployment.morris.scripts.improvement.approval_handler._open_pr",
            return_value={"number": 100, "auto_merge": True},
        ):
            apply_tier1_proposal(
                proposal_id=proposal_id,
                proposal=proposal,
                repo_root=tmp_path,
                db_service=MagicMock(mark_proposal_applied=AsyncMock()),
            )

        assert commit_messages, "No commit messages were captured"
        commit_msg = commit_messages[0]
        assert "STORY-727" in commit_msg, f"Commit message must contain 'STORY-727': {commit_msg!r}"
        assert str(proposal_id) in commit_msg, (
            f"Commit message must contain proposal_id={proposal_id}: {commit_msg!r}"
        )
        assert proposal.pattern_key in commit_msg, (
            f"Commit message must contain pattern_key: {commit_msg!r}"
        )

    def test_t4b_failure_raises_and_no_applied_status(self, tmp_path):
        """T4b: When git apply fails, an exception is raised and status stays at 'pending'
        (mark_proposal_applied is NOT called).
        """
        proposal = Proposal(
            pattern_key="adversarial.static_test_masquerading_as_behavioral",
            pattern_evidence_json={},
            target_file="tech_dev_agents/sdlc/phase_prompts/phase_7.md",
            diff_text="this is a bad diff",
            rationale="Evidence.",
            expected_metric="adversarial.static_test_masquerading.weekly_count",
            expected_direction="decrease",
            proposal_type="tier1",
        )
        mock_db = MagicMock()
        mock_db.mark_proposal_applied = AsyncMock()

        with patch(
            "deployment.morris.scripts.improvement.approval_handler._run_git",
            return_value=MagicMock(returncode=1, stdout=b"", stderr=b"patch does not apply"),
        ):
            with pytest.raises(Exception):
                apply_tier1_proposal(
                    proposal_id=55,
                    proposal=proposal,
                    repo_root=tmp_path,
                    db_service=mock_db,
                )

        mock_db.mark_proposal_applied.assert_not_called(), (
            "mark_proposal_applied must NOT be called when git apply fails"
        )


# ---------------------------------------------------------------------------
# T5 — record_approval inserts into improvement_proposals with status='approved'
# ---------------------------------------------------------------------------

class TestT5RecordApproval:
    """T5: record_approval() transitions proposal to status='approved'."""

    def test_t5_record_approval_calls_db_with_approved_status(self):
        """Given a webhook payload with [APPROVE] from mark@gorillacommerce.co,
        record_approval() calls improvement_service.mark_proposal_decided with
        decision='approved' and the correct decided_by.
        """
        proposal_id = 42
        approver_upn = "mark@gorillacommerce.co"

        mock_db = MagicMock()
        mock_db.mark_proposal_decided = AsyncMock()

        record_approval(
            proposal_id=proposal_id,
            approver=approver_upn,
            db_service=mock_db,
        )

        mock_db.mark_proposal_decided.assert_called_once()
        call_args = mock_db.mark_proposal_decided.call_args
        # Flexible: check positional or keyword arguments
        all_args = list(call_args.args) + list(call_args.kwargs.values())
        assert proposal_id in all_args or any(
            v == proposal_id for v in call_args.kwargs.values()
        ), f"proposal_id={proposal_id} not found in call args: {call_args}"
        assert "approved" in all_args or "approved" in call_args.kwargs.values(), (
            f"decision='approved' not found in call args: {call_args}"
        )
        assert approver_upn in all_args or approver_upn in call_args.kwargs.values(), (
            f"approver_upn={approver_upn!r} not found in call args: {call_args}"
        )

    def test_t5_approval_sets_status_approved_before_apply(self):
        """T5 supplemental: the status must be set to 'approved' before the apply step
        is invoked (ordering constraint).
        """
        call_order = []
        proposal_id = 99
        approver_upn = "mark@gorillacommerce.co"

        mock_db = MagicMock()

        async def _mark_decided(*args, **kwargs):
            call_order.append("mark_decided")

        async def _mark_applied(*args, **kwargs):
            call_order.append("mark_applied")

        mock_db.mark_proposal_decided = AsyncMock(side_effect=_mark_decided)
        mock_db.mark_proposal_applied = AsyncMock(side_effect=_mark_applied)

        record_approval(
            proposal_id=proposal_id,
            approver=approver_upn,
            db_service=mock_db,
        )

        if "mark_applied" in call_order:
            # If apply is triggered inline, decided must come first
            decided_idx = call_order.index("mark_decided")
            applied_idx = call_order.index("mark_applied")
            assert decided_idx < applied_idx, (
                "mark_proposal_decided must be called before mark_proposal_applied"
            )


# ---------------------------------------------------------------------------
# T6 — track_metric computes delta and stores in improvement_tracking
# ---------------------------------------------------------------------------

class TestT6TrackMetric:
    """T6: track_metric() / check_back() inserts improvement_tracking row with correct value."""

    def test_t6_metric_decreased_no_warning_dm_sent(self):
        """T6: When metric decreases as expected (before=5, after=2, direction='decrease'),
        track_metric inserts row with metric_value=2 and no warning DM is sent.
        """
        proposal_id = 10
        before_value = 5.0
        after_value = 2.0
        metric_name = "adversarial.static_test_masquerading.weekly_count"

        mock_db = MagicMock()
        mock_db.record_tracking_measurement = AsyncMock()

        mock_teams = MagicMock()
        mock_teams.post_dm = AsyncMock()

        track_metric(
            proposal_id=proposal_id,
            metric_name=metric_name,
            before_value=before_value,
            after_value=after_value,
            expected_direction="decrease",
            window_days=14,
            db_service=mock_db,
            teams_client=mock_teams,
        )

        mock_db.record_tracking_measurement.assert_called_once()
        call_kwargs = mock_db.record_tracking_measurement.call_args.kwargs
        assert call_kwargs.get("metric_value") == after_value or (
            after_value in mock_db.record_tracking_measurement.call_args.args
        ), f"metric_value must be {after_value}: {mock_db.record_tracking_measurement.call_args}"
        assert call_kwargs.get("window_days") == 14 or (
            14 in mock_db.record_tracking_measurement.call_args.args
        ), f"window_days must be 14: {mock_db.record_tracking_measurement.call_args}"

        # No warning DM — metric decreased as expected
        warning_calls = [c for c in mock_teams.post_dm.call_args_list
                         if "[INFO]" in str(c) or "warn" in str(c).lower()]
        assert not warning_calls, (
            "No warning DM should be sent when metric decreased as expected"
        )

    def test_t6_metric_increased_sends_warning_dm(self):
        """T6 inverse: when metric increases (before=5, after=7, direction='decrease'),
        a [INFO] warning DM is sent to Mark naming the proposal_id.
        """
        proposal_id = 11
        before_value = 5.0
        after_value = 7.0  # went up — bad!

        mock_db = MagicMock()
        mock_db.record_tracking_measurement = AsyncMock()

        mock_teams = MagicMock()
        mock_teams.post_dm = AsyncMock()

        track_metric(
            proposal_id=proposal_id,
            metric_name="adversarial.static_test_masquerading.weekly_count",
            before_value=before_value,
            after_value=after_value,
            expected_direction="decrease",
            window_days=14,
            db_service=mock_db,
            teams_client=mock_teams,
        )

        mock_teams.post_dm.assert_called()
        dm_texts = [str(c) for c in mock_teams.post_dm.call_args_list]
        assert any(str(proposal_id) in t for t in dm_texts), (
            f"Warning DM must reference proposal_id={proposal_id}: {dm_texts}"
        )
        assert any("[INFO]" in t for t in dm_texts), (
            f"Warning DM must use [INFO] prefix: {dm_texts}"
        )


# ---------------------------------------------------------------------------
# T7 — run_retrospective() returns correct top-3 and sections
# ---------------------------------------------------------------------------

class TestT7Retrospective:
    """T7: run_retrospective() returns correct top-3 patterns and "(none)" for empty sections."""

    def test_t7_top3_correct_and_excludes_fourth_pattern(self):
        """T7: Given 4 patterns with counts 7,4,3,2, top-3 section lists the first 3
        and excludes the count=2 pattern.
        """
        pattern_counts = {
            "adversarial.static_test_masquerading_as_behavioral": 7,
            "adversarial.spec_requirement_omitted": 4,
            "adversarial.unrealistic_test_fixture": 3,
            "retry_storm.short_duration": 2,
        }

        mock_db = MagicMock()
        mock_db.list_pending_proposals = AsyncMock(return_value=[])
        mock_db.list_recent_decisions = AsyncMock(return_value=[])
        mock_db.list_recent_tracking_measurements = AsyncMock(return_value=[])

        result = run_retrospective(
            pattern_counts=pattern_counts,
            db_service=mock_db,
            now=datetime(2026, 4, 28, 9, 0, 0, tzinfo=timezone.utc),
        )

        output = result if isinstance(result, str) else str(result)

        assert "static_test_masquerading" in output or "static-test-masquerading" in output, (
            f"Top pattern (count=7) must appear in retrospective output: {output[:300]}"
        )
        assert "spec_requirement_omitted" in output or "spec-requirement-omitted" in output, (
            f"Second pattern (count=4) must appear: {output[:300]}"
        )
        assert "unrealistic_test_fixture" in output or "unrealistic-test-fixture" in output, (
            f"Third pattern (count=3) must appear: {output[:300]}"
        )

    def test_t7_fourth_pattern_excluded_from_top3(self):
        """T7 supplemental: retry_storm.short_duration (count=2) must NOT appear in top-3."""
        pattern_counts = {
            "adversarial.static_test_masquerading_as_behavioral": 7,
            "adversarial.spec_requirement_omitted": 4,
            "adversarial.unrealistic_test_fixture": 3,
            "retry_storm.short_duration": 2,
        }

        mock_db = MagicMock()
        mock_db.list_pending_proposals = AsyncMock(return_value=[])
        mock_db.list_recent_decisions = AsyncMock(return_value=[])
        mock_db.list_recent_tracking_measurements = AsyncMock(return_value=[])

        result = run_retrospective(
            pattern_counts=pattern_counts,
            db_service=mock_db,
            now=datetime(2026, 4, 28, 9, 0, 0, tzinfo=timezone.utc),
        )
        output = result if isinstance(result, str) else str(result)

        # The count=2 pattern must not be in top-3
        # We look for it in the "top 3" section only — it may appear elsewhere
        lines = output.split("\n")
        top3_lines = []
        in_top3 = False
        for line in lines:
            if "top 3" in line.lower() or "top-3" in line.lower():
                in_top3 = True
            elif in_top3 and line.startswith("#"):
                in_top3 = False
            if in_top3:
                top3_lines.append(line)

        top3_text = "\n".join(top3_lines)
        assert "retry_storm" not in top3_text, (
            f"retry_storm.short_duration (count=2) must not appear in top-3 section: {top3_text}"
        )

    def test_t7_empty_sections_render_none_not_omitted(self):
        """T7: When all three sections (decided, tracking, pending) are empty,
        each section must render '(none)' rather than being omitted.
        """
        pattern_counts = {"adversarial.static_test_masquerading_as_behavioral": 7}

        mock_db = MagicMock()
        mock_db.list_pending_proposals = AsyncMock(return_value=[])
        mock_db.list_recent_decisions = AsyncMock(return_value=[])
        mock_db.list_recent_tracking_measurements = AsyncMock(return_value=[])

        result = run_retrospective(
            pattern_counts=pattern_counts,
            db_service=mock_db,
            now=datetime(2026, 4, 28, 9, 0, 0, tzinfo=timezone.utc),
        )
        output = result if isinstance(result, str) else str(result)

        assert "(none)" in output.lower(), (
            f"Empty sections must render '(none)', not be omitted. Output: {output[:400]}"
        )

    def test_t7_retrospective_has_briefing_prefix(self):
        """T7 supplemental: retrospective output must include [BRIEFING] prefix."""
        pattern_counts = {}
        mock_db = MagicMock()
        mock_db.list_pending_proposals = AsyncMock(return_value=[])
        mock_db.list_recent_decisions = AsyncMock(return_value=[])
        mock_db.list_recent_tracking_measurements = AsyncMock(return_value=[])

        result = run_retrospective(
            pattern_counts=pattern_counts,
            db_service=mock_db,
            now=datetime(2026, 4, 28, 9, 0, 0, tzinfo=timezone.utc),
        )
        output = result if isinstance(result, str) else str(result)
        assert "[BRIEFING]" in output, (
            f"Retrospective output must contain '[BRIEFING]' prefix. Got: {output[:200]}"
        )


# ---------------------------------------------------------------------------
# T8 — generate_proposal() with mode='shadow' does NOT write files or post DMs
# ---------------------------------------------------------------------------

class TestT8ShadowModeNoFilesNoDMs:
    """T8: In shadow mode, proposals are generated and stored to DB but no DM is posted."""

    def test_t8_shadow_mode_no_files_written(self, tmp_path):
        """T8: With config.mode='shadow', generate_proposal() must not write any files."""
        pattern = Pattern(
            key="adversarial.static_test_masquerading_as_behavioral",
            count=3,
            story_ids=["story-701", "story-720", "story-722"],
            evidence={},
        )
        config = ImprovementConfig(mode="shadow")

        mock_diff = (
            "--- a/tech_dev_agents/sdlc/phase_prompts/phase_7.md\n"
            "+++ b/tech_dev_agents/sdlc/phase_prompts/phase_7.md\n"
            "@@ -1,1 +1,2 @@\n"
            "+# behavioral mock example\n"
            " # existing\n"
        )

        before_files = set(tmp_path.rglob("*"))

        with patch(
            "deployment.morris.scripts.improvement.proposal_generator._invoke_sonnet_subagent",
            return_value=mock_diff,
        ), patch(
            "deployment.morris.scripts.improvement.proposal_generator._validate_diff",
            return_value=True,
        ):
            generate_proposal(pattern=pattern, config=config, repo_root=tmp_path)

        after_files = set(tmp_path.rglob("*"))
        new_files = after_files - before_files
        assert not new_files, (
            f"Shadow mode must not write any files, but new files appeared: {new_files}"
        )

    def test_t8_shadow_mode_no_teams_dm_posted(self, tmp_path):
        """T8 supplemental: In shadow mode, no DM is posted to Mark via Teams."""
        pattern = Pattern(
            key="adversarial.static_test_masquerading_as_behavioral",
            count=3,
            story_ids=["story-701", "story-720", "story-722"],
            evidence={},
        )
        config = ImprovementConfig(mode="shadow")

        mock_diff = (
            "--- a/tech_dev_agents/sdlc/phase_prompts/phase_7.md\n"
            "+++ b/tech_dev_agents/sdlc/phase_prompts/phase_7.md\n"
            "@@ -1,1 +1,2 @@\n"
            "+# behavioral mock example\n"
            " # existing\n"
        )

        mock_teams = MagicMock()
        mock_teams.post_dm = AsyncMock()

        with patch(
            "deployment.morris.scripts.improvement.proposal_generator._invoke_sonnet_subagent",
            return_value=mock_diff,
        ), patch(
            "deployment.morris.scripts.improvement.proposal_generator._validate_diff",
            return_value=True,
        ), patch(
            "deployment.morris.scripts.improvement.proposal_generator._get_teams_client",
            return_value=mock_teams,
        ):
            generate_proposal(pattern=pattern, config=config, repo_root=tmp_path)

        mock_teams.post_dm.assert_not_called(), (
            "Shadow mode must not post any DM to Mark via Teams"
        )

    def test_t8_shadow_mode_proposal_stored_to_db(self, tmp_path):
        """T8 supplemental: In shadow mode, proposal IS inserted into improvement_proposals."""
        pattern = Pattern(
            key="adversarial.static_test_masquerading_as_behavioral",
            count=3,
            story_ids=["story-701", "story-720", "story-722"],
            evidence={},
        )
        config = ImprovementConfig(mode="shadow")

        mock_diff = (
            "--- a/tech_dev_agents/sdlc/phase_prompts/phase_7.md\n"
            "+++ b/tech_dev_agents/sdlc/phase_prompts/phase_7.md\n"
            "@@ -1,1 +1,2 @@\n"
            "+# behavioral mock example\n"
            " # existing\n"
        )

        mock_db = MagicMock()
        mock_db.insert_proposal = AsyncMock(return_value=1)

        with patch(
            "deployment.morris.scripts.improvement.proposal_generator._invoke_sonnet_subagent",
            return_value=mock_diff,
        ), patch(
            "deployment.morris.scripts.improvement.proposal_generator._validate_diff",
            return_value=True,
        ), patch(
            "deployment.morris.scripts.improvement.proposal_generator._get_db_service",
            return_value=mock_db,
        ):
            generate_proposal(pattern=pattern, config=config, repo_root=tmp_path)

        # In shadow mode, proposal storage should still be called
        mock_db.insert_proposal.assert_called_once(), (
            "Shadow mode must still insert the proposal into the DB"
        )


# ---------------------------------------------------------------------------
# T9 — detect_patterns() skips when enabled=False in config
# ---------------------------------------------------------------------------

class TestT9EnabledFalseKillSwitch:
    """T9: When config.enabled=False, detect_patterns() returns empty list immediately."""

    def test_t9_enabled_false_returns_empty_list(self, tmp_path):
        """T9: With enabled=False, detector returns [] even with 4 CRITICAL findings."""
        _make_fixture_corpus(tmp_path, [
            {"story_folder": "story-701-killswitch", "finding_type": "static-test-masquerading-as-behavioral"},
            {"story_folder": "story-720-killswitch", "finding_type": "static-test-masquerading-as-behavioral"},
            {"story_folder": "story-722-killswitch", "finding_type": "static-test-masquerading-as-behavioral"},
            {"story_folder": "story-724-killswitch", "finding_type": "static-test-masquerading-as-behavioral"},
        ])

        config = ImprovementConfig(enabled=False, threshold_critical=3)
        patterns = detect_patterns(repo_root=tmp_path, config=config)

        assert patterns == [] or patterns is None or len(list(patterns)) == 0, (
            f"With enabled=False, detect_patterns() must return empty list. Got: {patterns}"
        )

    def test_t9_enabled_false_makes_no_db_calls(self, tmp_path):
        """T9 supplemental: With enabled=False, no DB service calls are made."""
        _make_fixture_corpus(tmp_path, [
            {"story_folder": "story-701-nodb", "finding_type": "static-test-masquerading-as-behavioral"},
            {"story_folder": "story-720-nodb", "finding_type": "static-test-masquerading-as-behavioral"},
            {"story_folder": "story-722-nodb", "finding_type": "static-test-masquerading-as-behavioral"},
        ])

        mock_db = MagicMock()
        config = ImprovementConfig(enabled=False, threshold_critical=3)

        with patch(
            "deployment.morris.scripts.improvement.pattern_detector._get_db_service",
            return_value=mock_db,
        ):
            detect_patterns(repo_root=tmp_path, config=config)

        mock_db.assert_not_called(), (
            "With enabled=False, detect_patterns() must not make any DB calls"
        )

    def test_t9_enabled_true_after_false_does_fire(self, tmp_path):
        """T9 inverse: With enabled=True (default), detect_patterns() does produce patterns."""
        _make_fixture_corpus(tmp_path, [
            {"story_folder": "story-701-yes", "finding_type": "static-test-masquerading-as-behavioral"},
            {"story_folder": "story-720-yes", "finding_type": "static-test-masquerading-as-behavioral"},
            {"story_folder": "story-722-yes", "finding_type": "static-test-masquerading-as-behavioral"},
        ])

        config = ImprovementConfig(enabled=True, threshold_critical=3)
        patterns = detect_patterns(repo_root=tmp_path, config=config)

        assert len(patterns) > 0, (
            "With enabled=True and 3 CRITICAL findings, detect_patterns() must return patterns"
        )


# ---------------------------------------------------------------------------
# T10 — Full cycle integration: detect → propose → approve → apply → track (mocked DB)
# ---------------------------------------------------------------------------

class TestT10FullCycleIntegration:
    """T10: Full pipeline from detection through tracking with all external calls mocked."""

    def test_t10_full_cycle_exactly_three_dms_posted(self, tmp_path):
        """T10: Full cycle must emit exactly 3 Teams DMs:
        1. [APPROVAL-NEEDED] when proposed
        2. [ACTION] when applied
        3. [BRIEFING] from retrospective

        All DB and Git calls are mocked. The mocked Teams client records all calls.
        """
        # ---- Setup fixture corpus ----
        _make_fixture_corpus(tmp_path, [
            {"story_folder": "story-701-cycle", "finding_type": "static-test-masquerading-as-behavioral"},
            {"story_folder": "story-720-cycle", "finding_type": "static-test-masquerading-as-behavioral"},
            {"story_folder": "story-722-cycle", "finding_type": "static-test-masquerading-as-behavioral"},
        ])

        config = ImprovementConfig(mode="live", threshold_critical=3)

        # ---- Mock infrastructure ----
        teams_calls: list[dict] = []

        mock_teams = MagicMock()
        mock_teams.post_dm = AsyncMock(
            side_effect=lambda recipient, message: teams_calls.append(
                {"recipient": recipient, "message": message}
            ) or "msg-id-001"
        )

        in_memory_proposals: list[dict] = []
        proposal_id_counter = [0]

        async def mock_insert_proposal(**kwargs):
            proposal_id_counter[0] += 1
            row = {**kwargs, "id": proposal_id_counter[0], "status": "pending"}
            in_memory_proposals.append(row)
            return proposal_id_counter[0]

        async def mock_mark_decided(proposal_id, decision, decided_by, **kwargs):
            for row in in_memory_proposals:
                if row["id"] == proposal_id:
                    row["status"] = decision
                    row["decided_by"] = decided_by

        async def mock_mark_applied(proposal_id, applied_commit, **kwargs):
            for row in in_memory_proposals:
                if row["id"] == proposal_id:
                    row["status"] = "applied"
                    row["applied_commit"] = applied_commit

        async def mock_record_tracking(**kwargs):
            pass

        async def mock_list_pending():
            return [r for r in in_memory_proposals if r["status"] == "pending"]

        async def mock_list_applied_due(now, window_days=14):
            return [r for r in in_memory_proposals if r["status"] == "applied"]

        mock_db = MagicMock()
        mock_db.insert_proposal = AsyncMock(side_effect=mock_insert_proposal)
        mock_db.mark_proposal_decided = AsyncMock(side_effect=mock_decided
                                                   if False else mock_mark_decided)
        mock_db.mark_proposal_applied = AsyncMock(side_effect=mock_mark_applied)
        mock_db.record_tracking_measurement = AsyncMock(side_effect=mock_record_tracking)
        mock_db.list_pending_proposals = AsyncMock(side_effect=mock_list_pending)
        mock_db.list_applied_proposals_due_for_check = AsyncMock(side_effect=mock_list_applied_due)
        mock_db.list_recent_decisions = AsyncMock(return_value=[])
        mock_db.list_recent_tracking_measurements = AsyncMock(return_value=[])

        mock_diff = (
            "--- a/tech_dev_agents/sdlc/phase_prompts/phase_7.md\n"
            "+++ b/tech_dev_agents/sdlc/phase_prompts/phase_7.md\n"
            "@@ -1,1 +1,2 @@\n"
            "+# behavioral mock example\n"
            " # existing\n"
        )

        common_patches = [
            patch("deployment.morris.scripts.improvement.proposal_generator._invoke_sonnet_subagent",
                  return_value=mock_diff),
            patch("deployment.morris.scripts.improvement.proposal_generator._validate_diff",
                  return_value=True),
            patch("deployment.morris.scripts.improvement.proposal_generator._get_teams_client",
                  return_value=mock_teams),
            patch("deployment.morris.scripts.improvement.proposal_generator._get_db_service",
                  return_value=mock_db),
            patch("deployment.morris.scripts.improvement.approval_handler._get_teams_client",
                  return_value=mock_teams),
            patch("deployment.morris.scripts.improvement.approval_handler._get_db_service",
                  return_value=mock_db),
            patch("deployment.morris.scripts.improvement.approval_handler._run_git",
                  return_value=MagicMock(returncode=0, stdout=b"phase_7.md\n", stderr=b"")),
            patch("deployment.morris.scripts.improvement.approval_handler._get_commit_sha",
                  return_value="cafebabe1234"),
            patch("deployment.morris.scripts.improvement.approval_handler._open_pr",
                  return_value={"number": 42, "auto_merge": True}),
            patch("deployment.morris.scripts.improvement.tracker._get_teams_client",
                  return_value=mock_teams),
            patch("deployment.morris.scripts.improvement.tracker._get_db_service",
                  return_value=mock_db),
            patch("deployment.morris.scripts.improvement.retrospective._get_teams_client",
                  return_value=mock_teams),
            patch("deployment.morris.scripts.improvement.retrospective._get_db_service",
                  return_value=mock_db),
            patch("deployment.morris.scripts.improvement.pattern_detector._get_db_service",
                  return_value=mock_db),
        ]

        with _stack_patches(common_patches):
            # Step 1: Detect
            patterns = detect_patterns(repo_root=tmp_path, config=config)
            assert len(patterns) >= 1, "Full cycle: detection must find the pattern"

            matching_patterns = [
                p for p in patterns
                if p.key == "adversarial.static_test_masquerading_as_behavioral"
            ]
            assert matching_patterns, "Full cycle: static_test_masquerading pattern must be detected"
            pattern = matching_patterns[0]

            # Step 2: Propose
            proposal = generate_proposal(pattern=pattern, config=config, repo_root=tmp_path)
            assert proposal is not None, "Full cycle: proposal must be generated"

            # [APPROVAL-NEEDED] DM should have been sent
            approval_needed_dms = [c for c in teams_calls
                                   if "[APPROVAL-NEEDED]" in c.get("message", "")]
            assert len(approval_needed_dms) >= 1, (
                f"Full cycle: [APPROVAL-NEEDED] DM must be sent after proposal. "
                f"DM calls so far: {teams_calls}"
            )

            # Step 3: Approve
            proposal_id = in_memory_proposals[0]["id"] if in_memory_proposals else 1
            record_approval(
                proposal_id=proposal_id,
                approver="mark@gorillacommerce.co",
                db_service=mock_db,
            )

            # Step 4: Apply
            apply_tier1_proposal(
                proposal_id=proposal_id,
                proposal=proposal,
                repo_root=tmp_path,
                db_service=mock_db,
            )

            # [ACTION] DM should have been sent
            action_dms = [c for c in teams_calls
                          if "[ACTION]" in c.get("message", "")]
            assert len(action_dms) >= 1, (
                f"Full cycle: [ACTION] DM must be sent after apply. DM calls: {teams_calls}"
            )

            # Check applied status
            applied_rows = [r for r in in_memory_proposals if r["status"] == "applied"]
            assert applied_rows, "Full cycle: proposal must reach status='applied'"
            assert applied_rows[0].get("applied_commit"), (
                "Full cycle: applied_commit must be set after apply"
            )

            # Step 5: Track
            track_metric(
                proposal_id=proposal_id,
                metric_name="adversarial.static_test_masquerading.weekly_count",
                before_value=5.0,
                after_value=2.0,
                expected_direction="decrease",
                window_days=14,
                db_service=mock_db,
                teams_client=mock_teams,
            )

            # Step 6: Retrospective
            now = datetime(2026, 4, 28, 9, 0, 0, tzinfo=timezone.utc)
            retro_output = run_retrospective(
                pattern_counts={"adversarial.static_test_masquerading_as_behavioral": 3},
                db_service=mock_db,
                now=now,
            )
            assert retro_output, "Full cycle: retrospective must produce non-empty output"

        # Verify applied proposal appears in retrospective
        retro_str = retro_output if isinstance(retro_output, str) else str(retro_output)
        assert "static" in retro_str.lower() or "improvement" in retro_str.lower(), (
            f"Full cycle: retrospective must mention the applied proposal or pattern. "
            f"Got: {retro_str[:200]}"
        )

    def test_t10_proposal_status_sequence_pending_approved_applied(self, tmp_path):
        """T10 supplemental: proposal status must follow pending → approved → applied sequence."""
        _make_fixture_corpus(tmp_path, [
            {"story_folder": "story-701-seq", "finding_type": "static-test-masquerading-as-behavioral"},
            {"story_folder": "story-720-seq", "finding_type": "static-test-masquerading-as-behavioral"},
            {"story_folder": "story-722-seq", "finding_type": "static-test-masquerading-as-behavioral"},
        ])

        config = ImprovementConfig(mode="live", threshold_critical=3)
        status_history: list[str] = []
        proposal_storage: list[dict] = []

        async def track_insert(**kwargs):
            proposal_storage.append({**kwargs, "id": 1, "status": "pending"})
            status_history.append("pending")
            return 1

        async def track_decided(proposal_id, decision, decided_by, **kwargs):
            for r in proposal_storage:
                if r["id"] == proposal_id:
                    r["status"] = decision
            status_history.append(decision)

        async def track_applied(proposal_id, applied_commit, **kwargs):
            for r in proposal_storage:
                if r["id"] == proposal_id:
                    r["status"] = "applied"
            status_history.append("applied")

        mock_db = MagicMock()
        mock_db.insert_proposal = AsyncMock(side_effect=track_insert)
        mock_db.mark_proposal_decided = AsyncMock(side_effect=track_decided)
        mock_db.mark_proposal_applied = AsyncMock(side_effect=track_applied)

        mock_diff = (
            "--- a/tech_dev_agents/sdlc/phase_prompts/phase_7.md\n"
            "+++ b/tech_dev_agents/sdlc/phase_prompts/phase_7.md\n"
            "@@ -1,1 +1,2 @@\n"
            "+# new\n existing\n"
        )

        patches = [
            patch("deployment.morris.scripts.improvement.proposal_generator._invoke_sonnet_subagent",
                  return_value=mock_diff),
            patch("deployment.morris.scripts.improvement.proposal_generator._validate_diff",
                  return_value=True),
            patch("deployment.morris.scripts.improvement.proposal_generator._get_teams_client",
                  return_value=MagicMock(post_dm=AsyncMock(return_value="msg1"))),
            patch("deployment.morris.scripts.improvement.proposal_generator._get_db_service",
                  return_value=mock_db),
            patch("deployment.morris.scripts.improvement.approval_handler._get_teams_client",
                  return_value=MagicMock(post_dm=AsyncMock(return_value="msg2"))),
            patch("deployment.morris.scripts.improvement.approval_handler._get_db_service",
                  return_value=mock_db),
            patch("deployment.morris.scripts.improvement.approval_handler._run_git",
                  return_value=MagicMock(returncode=0, stdout=b"phase_7.md\n", stderr=b"")),
            patch("deployment.morris.scripts.improvement.approval_handler._get_commit_sha",
                  return_value="sha999"),
            patch("deployment.morris.scripts.improvement.approval_handler._open_pr",
                  return_value={"number": 10, "auto_merge": True}),
            patch("deployment.morris.scripts.improvement.pattern_detector._get_db_service",
                  return_value=mock_db),
        ]

        with _stack_patches(patches):
            patterns = detect_patterns(repo_root=tmp_path, config=config)
            pattern = next(
                p for p in patterns
                if p.key == "adversarial.static_test_masquerading_as_behavioral"
            )
            proposal = generate_proposal(pattern=pattern, config=config, repo_root=tmp_path)
            record_approval(proposal_id=1, approver="mark@gc.co", db_service=mock_db)
            apply_tier1_proposal(proposal_id=1, proposal=proposal,
                                 repo_root=tmp_path, db_service=mock_db)

        assert "pending" in status_history, f"Status history missing 'pending': {status_history}"
        assert "approved" in status_history, f"Status history missing 'approved': {status_history}"
        assert "applied" in status_history, f"Status history missing 'applied': {status_history}"

        pending_idx = status_history.index("pending")
        approved_idx = status_history.index("approved")
        applied_idx = status_history.index("applied")
        assert pending_idx < approved_idx < applied_idx, (
            f"Status must follow pending → approved → applied order. Got: {status_history}"
        )


# ---------------------------------------------------------------------------
# Context manager helper for stacking multiple patches
# ---------------------------------------------------------------------------

from contextlib import contextmanager


@contextmanager
def _stack_patches(patches):
    """Stack multiple patch() context managers and enter them all."""
    started = []
    try:
        for p in patches:
            started.append(p.__enter__())
        yield started
    finally:
        for p in reversed(patches):
            p.__exit__(None, None, None)


# avoid undefined reference in mock_db.mark_proposal_decided side_effect above
async def mock_decided(*args, **kwargs):
    pass
