"""
tests/morris/test_needs_info_pattern_check.py — STORY-773

RED-state tests for Check 16: needs_info Pattern Detection.

Tests verify:
  - SC-1: Fetch needs_info QUESTION.md content from dispatch DB + gh API
  - SC-2: Bigram-similarity scoring (Jaccard over word bigrams)
  - SC-3: Anchor-phrase detection
  - SC-4: CRIT threshold (cluster size ≥ 3)
  - SC-5: 6-hour DM suppression per cluster signature
  - SC-6: Logging format
  - SC-7: Integration with fleet-vigilance (fail-safe isolation)
  - SC-8: Zero regressions on Checks 0-15

Mocking strategy:
  - Mock subprocess.run for gh API calls (no live network)
  - Mock fetch_fn for DB queries (no live DB)
  - Use tmp_path for suppression state files
  - Fixture QUESTION.md content mirrors the 2026-04-30 incident
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Module loader (lazy — avoids import errors during RED state)
# ---------------------------------------------------------------------------
_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "deployment"
    / "morris"
    / "scripts"
    / "needs_info_pattern_check.py"
)


def _load_module():
    """Load needs_info_pattern_check.py; pytest.fail() if missing (RED state)."""
    if not _SCRIPT_PATH.exists():
        pytest.fail(
            f"needs_info_pattern_check.py not found at {_SCRIPT_PATH}. "
            "Expected during RED state — implementation pending in Phase 8."
        )
    spec = importlib.util.spec_from_file_location(
        "needs_info_pattern_check", _SCRIPT_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Fixture data — mirrors 2026-04-30 incident
# ---------------------------------------------------------------------------

# Four questions sharing the "phase path does not include" pattern
_QUESTION_PHASE_ROUTING_1 = """\
# QUESTION.md

**Story:** STORY-008
**Phase:** 2 (Research)
**Date:** 2026-04-29T12:38:53Z

Phase 2 (Research) was dispatched for STORY-008, but STORY-008's seed.md
specifies Phase Path `1 → 7 → 8 → Done` which does not include Phase 2.

Should I proceed with Phase 2 anyway, or should this story follow its
declared phase path?
"""

_QUESTION_PHASE_ROUTING_2 = """\
# QUESTION.md

**Story:** STORY-009
**Phase:** 2 (Research)
**Date:** 2026-04-29T13:15:22Z

Phase 2 (Research) was dispatched for STORY-009, but STORY-009's seed.md
specifies Phase Path `1 → 7 → 8 → Done` which does not include Phase 2.

I need guidance — should I skip this phase and go to Phase 7 instead?
"""

_QUESTION_PHASE_ROUTING_3 = """\
# QUESTION.md

**Story:** STORY-014
**Phase:** 4 (Analysis)
**Date:** 2026-04-29T14:02:11Z

Phase 4 (Analysis) was dispatched but my seed's Phase Path is
`1 → 7 → 8 → Done` and does not include Phase 4.

The phase path does not include this phase. Requesting clarification.
"""

_QUESTION_PHASE_ROUTING_4 = """\
# QUESTION.md

**Story:** STORY-015
**Phase:** 6 (Feature Spec)
**Date:** 2026-04-29T15:47:00Z

Phase 6 was dispatched for STORY-015 but the phase path does not include
Phase 6. Seed declares `1 → 7 → 8 → Done`.

What should I do?
"""

# One unrelated question (should NOT cluster with the above)
_QUESTION_UNRELATED = """\
# QUESTION.md

**Story:** STORY-020
**Phase:** 8 (Implementation)
**Date:** 2026-04-29T16:00:00Z

The API endpoint /api/v1/reports requires a tenant_id header but the
test fixtures don't include one. Should I add tenant_id to the test
helper or mock the auth middleware?
"""

# An anchor-phrase question (branch setup failure pattern)
_QUESTION_BRANCH_SETUP = """\
# QUESTION.md

**Story:** STORY-025
**Phase:** 8 (Implementation)
**Date:** 2026-04-29T18:00:00Z

branch_setup_failed — git checkout main failed with exit code 128.
The remote branch does not exist. Was this story dispatched before
the repository was initialized?
"""

# Simulated dispatch_items rows for needs_info stories
_DISPATCH_ROWS = [
    {
        "story_id": "STORY-008",
        "repo": "tech-dev-agents",
        "branch": "story-008/story-008",
        "status": "needs_info",
        "needs_info_path": "features/story-008/QUESTION.md",
        "paused_at": "2026-04-29T12:38:53+00:00",
    },
    {
        "story_id": "STORY-009",
        "repo": "tech-dev-agents",
        "branch": "story-009/story-009",
        "status": "needs_info",
        "needs_info_path": "features/story-009/QUESTION.md",
        "paused_at": "2026-04-29T13:15:22+00:00",
    },
    {
        "story_id": "STORY-014",
        "repo": "tech-dev-agents",
        "branch": "story-014/story-014",
        "status": "needs_info",
        "needs_info_path": "features/story-014/QUESTION.md",
        "paused_at": "2026-04-29T14:02:11+00:00",
    },
    {
        "story_id": "STORY-015",
        "repo": "tech-dev-agents",
        "branch": "story-015/story-015",
        "status": "needs_info",
        "needs_info_path": "features/story-015/QUESTION.md",
        "paused_at": "2026-04-29T15:47:00+00:00",
    },
    {
        "story_id": "STORY-020",
        "repo": "tech-dev-agents",
        "branch": "story-020/story-020",
        "status": "needs_info",
        "needs_info_path": "features/story-020/QUESTION.md",
        "paused_at": "2026-04-29T16:00:00+00:00",
    },
]

# Map story_id → QUESTION.md content for mock gh API responses
_QUESTION_CONTENT_MAP = {
    "STORY-008": _QUESTION_PHASE_ROUTING_1,
    "STORY-009": _QUESTION_PHASE_ROUTING_2,
    "STORY-014": _QUESTION_PHASE_ROUTING_3,
    "STORY-015": _QUESTION_PHASE_ROUTING_4,
    "STORY-020": _QUESTION_UNRELATED,
}


def _make_fetch_fn(rows):
    """Return a mock fetch_fn that returns the given dispatch rows."""

    def fetch_fn(sql, params=None):
        return rows

    return fetch_fn


def _make_run_fn(content_map):
    """Return a mock run_fn (subprocess.run replacement) for gh API calls.

    Inspects the command for story branch info to return the appropriate
    QUESTION.md content.
    """

    def run_fn(cmd, **kwargs):
        result = MagicMock()
        cmd_str = " ".join(str(c) for c in cmd) if isinstance(cmd, list) else str(cmd)

        # Match gh api calls — look for the branch/path to determine content
        for story_id, content in content_map.items():
            # The story_id appears in branch or path
            story_num = story_id.split("-")[1]  # e.g., "008"
            if f"story-{story_num}" in cmd_str or story_id.lower() in cmd_str.lower():
                result.returncode = 0
                result.stdout = content
                return result

        # Default: 404 not found
        result.returncode = 1
        result.stdout = ""
        result.stderr = "HTTP 404"
        return result

    return run_fn


# ===================================================================
# Group A: SC-1 — Fetch needs_info QUESTION.md content
# ===================================================================


class TestFetchNeedsInfoQuestions:
    """SC-1: Verify fetch of needs_info QUESTION.md content from DB + gh API."""

    def test_fetch_needs_info_questions(self, tmp_path):
        """Fetches QUESTION.md for all needs_info dispatch rows via gh API."""
        mod = _load_module()

        fetch_fn = _make_fetch_fn(_DISPATCH_ROWS)
        run_fn = _make_run_fn(_QUESTION_CONTENT_MAP)

        # run_check is the main entry point
        result = mod.run_check(
            fetch_fn=fetch_fn,
            run_fn=run_fn,
            suppression_path=str(tmp_path / "suppress.json"),
        )

        # Should have scanned all 5 stories
        assert result["check_id"] == 16
        assert "5" in result["status_line"] or "scanned" in result["status_line"].lower()

    def test_fetch_skips_failed_gh_api_calls(self, tmp_path):
        """AC-11: When a QUESTION.md fetch fails (404, branch deleted),
        skip that story and log; don't abort the check."""
        mod = _load_module()

        fetch_fn = _make_fetch_fn(_DISPATCH_ROWS)

        # All gh API calls fail
        def failing_run_fn(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 1
            r.stdout = ""
            r.stderr = "HTTP 404"
            return r

        result = mod.run_check(
            fetch_fn=fetch_fn,
            run_fn=failing_run_fn,
            suppression_path=str(tmp_path / "suppress.json"),
        )

        # Check should complete (not error), even if all fetches fail
        assert result["severity"] in ("ok", "warn", "crit", "error_unavailable")
        assert result["check_id"] == 16

    def test_fetch_empty_dispatch_queue(self, tmp_path):
        """When no needs_info stories exist, result is ok with 0 scanned."""
        mod = _load_module()

        fetch_fn = _make_fetch_fn([])  # empty queue

        result = mod.run_check(
            fetch_fn=fetch_fn,
            run_fn=_make_run_fn({}),
            suppression_path=str(tmp_path / "suppress.json"),
        )

        assert result["severity"] == "ok"
        assert result["check_id"] == 16


# ===================================================================
# Group B: SC-2 — Bigram-similarity scoring
# ===================================================================


class TestBigramSimilarityClustering:
    """SC-2: Verify Jaccard similarity over word bigrams clusters related questions."""

    def test_bigram_similarity_clustering(self, tmp_path):
        """Four phase-routing questions cluster together (similarity ≥ 0.40).
        One unrelated question stays separate."""
        mod = _load_module()

        fetch_fn = _make_fetch_fn(_DISPATCH_ROWS)
        run_fn = _make_run_fn(_QUESTION_CONTENT_MAP)

        result = mod.run_check(
            fetch_fn=fetch_fn,
            run_fn=run_fn,
            suppression_path=str(tmp_path / "suppress.json"),
        )

        # Should detect at least 1 cluster
        assert result["severity"] == "crit"
        # The cluster should contain the 4 phase-routing stories
        clusters = result.get("clusters", [])
        assert len(clusters) >= 1

        # Find the big cluster
        big_cluster = max(clusters, key=lambda c: len(c.get("story_ids", [])))
        assert len(big_cluster["story_ids"]) >= 3
        # All 4 phase-routing stories should be in this cluster
        for sid in ["STORY-008", "STORY-009", "STORY-014", "STORY-015"]:
            assert sid in big_cluster["story_ids"]
        # The unrelated story should NOT be in this cluster
        assert "STORY-020" not in big_cluster["story_ids"]

    def test_bigram_strips_markdown_headers_and_dates(self):
        """AC-3: Markdown headers, dates, ISO timestamps, story IDs are stripped
        before similarity computation."""
        mod = _load_module()

        # Access the normalization/stripping function directly
        normalize = mod.normalize_question_text

        raw = (
            "# QUESTION.md\n\n"
            "**Story:** STORY-008\n"
            "**Date:** 2026-04-29T12:38:53Z\n\n"
            "Phase 2 was dispatched but the phase path does not include it."
        )
        cleaned = normalize(raw)

        # Should not contain markdown headers, story IDs, or ISO timestamps
        assert "# QUESTION" not in cleaned
        assert "STORY-008" not in cleaned
        assert "2026-04-29" not in cleaned
        # But should contain the meaningful content
        assert "phase" in cleaned.lower()
        assert "dispatched" in cleaned.lower()

    def test_bigram_jaccard_computation(self):
        """Verify Jaccard similarity function is correct for known inputs."""
        mod = _load_module()

        compute_similarity = mod.bigram_jaccard_similarity

        # Identical strings → similarity = 1.0
        assert compute_similarity("hello world foo bar", "hello world foo bar") == 1.0

        # Completely different → similarity = 0.0
        assert compute_similarity("alpha beta gamma", "one two three four") == 0.0

        # Partial overlap → 0 < similarity < 1
        sim = compute_similarity(
            "the phase path does not include phase two",
            "the phase path does not include phase four",
        )
        assert 0.4 < sim < 1.0  # high overlap, not identical


# ===================================================================
# Group C: SC-3 — Anchor-phrase detection
# ===================================================================


class TestAnchorPhraseDetection:
    """SC-3: Scan questions for known systemic-bug anchor phrases."""

    def test_anchor_phrase_detection(self, tmp_path):
        """Questions containing anchor phrases like 'phase path does not include'
        are detected and contribute to clustering."""
        mod = _load_module()

        fetch_fn = _make_fetch_fn(_DISPATCH_ROWS)
        run_fn = _make_run_fn(_QUESTION_CONTENT_MAP)

        result = mod.run_check(
            fetch_fn=fetch_fn,
            run_fn=run_fn,
            suppression_path=str(tmp_path / "suppress.json"),
        )

        # The cluster should reference the dominant anchor phrase
        clusters = result.get("clusters", [])
        big_cluster = max(clusters, key=lambda c: len(c.get("story_ids", [])))
        anchor = big_cluster.get("dominant_anchor", "")
        assert "phase path does not include" in anchor.lower() or len(anchor) > 0

    def test_anchor_phrase_configurable_from_file(self, tmp_path):
        """AC-8: Anchor phrases are loaded from a file when available."""
        mod = _load_module()

        anchors_file = tmp_path / "custom_anchors.txt"
        anchors_file.write_text("custom anchor phrase one\ncustom anchor phrase two\n")

        anchors = mod.load_anchor_phrases(str(anchors_file))
        assert "custom anchor phrase one" in anchors
        assert "custom anchor phrase two" in anchors

    def test_anchor_phrase_default_set(self):
        """SC-3: Default anchor phrases include the 5 from the specification."""
        mod = _load_module()

        defaults = mod.load_anchor_phrases(None)  # None = use defaults
        expected_phrases = [
            "phase path does not include",
            "was dispatched but",
            "blocked on story-",
            "branch_setup_failed",
            "git checkout main failed",
        ]
        for phrase in expected_phrases:
            assert any(
                phrase.lower() in a.lower() for a in defaults
            ), f"Default anchors missing: {phrase}"

    def test_anchor_branch_setup_detection(self, tmp_path):
        """Anchor phrase 'branch_setup_failed' detected in question content."""
        mod = _load_module()

        # 3 stories with branch_setup_failed
        branch_rows = [
            {
                "story_id": f"STORY-0{i}0",
                "repo": "tech-dev-agents",
                "branch": f"story-0{i}0/story-0{i}0",
                "status": "needs_info",
                "needs_info_path": f"features/story-0{i}0/QUESTION.md",
                "paused_at": f"2026-04-29T1{i}:00:00+00:00",
            }
            for i in range(3, 6)
        ]

        branch_content = {
            f"STORY-0{i}0": _QUESTION_BRANCH_SETUP.replace("STORY-025", f"STORY-0{i}0")
            for i in range(3, 6)
        }

        result = mod.run_check(
            fetch_fn=_make_fetch_fn(branch_rows),
            run_fn=_make_run_fn(branch_content),
            suppression_path=str(tmp_path / "suppress.json"),
        )

        # 3 stories with same anchor phrase → CRIT
        assert result["severity"] == "crit"
        clusters = result.get("clusters", [])
        assert len(clusters) >= 1


# ===================================================================
# Group D: SC-4 — CRIT threshold (cluster size ≥ 3)
# ===================================================================


class TestCritThreshold:
    """SC-4: Cluster size ≥ 3 → CRIT. Cluster size 2 → WARN (no DM)."""

    def test_crit_threshold_three_or_more(self, tmp_path):
        """Four related stories → cluster size 4 → severity=crit, DM sent."""
        mod = _load_module()

        fetch_fn = _make_fetch_fn(_DISPATCH_ROWS)
        run_fn = _make_run_fn(_QUESTION_CONTENT_MAP)

        result = mod.run_check(
            fetch_fn=fetch_fn,
            run_fn=run_fn,
            suppression_path=str(tmp_path / "suppress.json"),
        )

        assert result["severity"] == "crit"
        assert result["dm_payload"] is not None
        # DM payload should include cluster size, story IDs, sample text
        dm = result["dm_payload"]
        assert dm.get("cluster_size", 0) >= 3 or "cluster" in str(dm).lower()

    def test_warn_threshold_two_stories(self, tmp_path):
        """AC-5: Cluster of 2 → WARN (logged, no DM)."""
        mod = _load_module()

        # Only 2 phase-routing stories
        two_rows = _DISPATCH_ROWS[:2]
        two_content = {
            k: v
            for k, v in _QUESTION_CONTENT_MAP.items()
            if k in ("STORY-008", "STORY-009")
        }

        result = mod.run_check(
            fetch_fn=_make_fetch_fn(two_rows),
            run_fn=_make_run_fn(two_content),
            suppression_path=str(tmp_path / "suppress.json"),
        )

        # Cluster of 2 → warn, not crit
        assert result["severity"] == "warn"
        # No DM for warn-level
        assert result.get("dm_payload") is None or result.get("dm_suppressed") is True

    def test_no_cluster_single_stories(self, tmp_path):
        """One story alone → no cluster → ok."""
        mod = _load_module()

        single_row = [_DISPATCH_ROWS[4]]  # STORY-020 (unrelated)
        single_content = {"STORY-020": _QUESTION_UNRELATED}

        result = mod.run_check(
            fetch_fn=_make_fetch_fn(single_row),
            run_fn=_make_run_fn(single_content),
            suppression_path=str(tmp_path / "suppress.json"),
        )

        assert result["severity"] == "ok"


# ===================================================================
# Group E: SC-5 — DM suppression (6 hours per cluster signature)
# ===================================================================


class TestDmSuppression:
    """SC-5: Same cluster signature shouldn't DM more than once per 6 hours."""

    def test_dm_suppression_six_hours(self, tmp_path):
        """After a CRIT DM is sent, the same cluster is suppressed for 6 hours."""
        mod = _load_module()

        fetch_fn = _make_fetch_fn(_DISPATCH_ROWS)
        run_fn = _make_run_fn(_QUESTION_CONTENT_MAP)
        suppress_path = str(tmp_path / "suppress.json")

        # First run → CRIT, DM sent
        result1 = mod.run_check(
            fetch_fn=fetch_fn,
            run_fn=run_fn,
            suppression_path=suppress_path,
        )
        assert result1["severity"] == "crit"
        assert result1.get("dm_suppressed") is not True  # First time → not suppressed

        # Second run (same cycle) → CRIT detected, but DM suppressed
        result2 = mod.run_check(
            fetch_fn=fetch_fn,
            run_fn=run_fn,
            suppression_path=suppress_path,
        )
        assert result2["severity"] == "crit"
        assert result2.get("dm_suppressed") is True

    def test_dm_suppression_expires_after_six_hours(self, tmp_path):
        """After 6+ hours, DM suppression expires and a new DM is sent."""
        mod = _load_module()

        suppress_path = str(tmp_path / "suppress.json")

        # Write a suppression entry from 7 hours ago
        seven_hours_ago = time.time() - (7 * 3600)
        # We need the cluster signature that run_check would compute
        # Write a suppression state with an old timestamp for check 16
        state = {"16": seven_hours_ago}
        with open(suppress_path, "w") as f:
            json.dump(state, f)

        fetch_fn = _make_fetch_fn(_DISPATCH_ROWS)
        run_fn = _make_run_fn(_QUESTION_CONTENT_MAP)

        result = mod.run_check(
            fetch_fn=fetch_fn,
            run_fn=run_fn,
            suppression_path=suppress_path,
        )

        # Suppression expired → DM should be sent again
        assert result["severity"] == "crit"
        assert result.get("dm_suppressed") is not True

    def test_suppression_file_corruption_handled(self, tmp_path):
        """Escalation contract: corrupted suppression file → treat as unsuppressed."""
        mod = _load_module()

        suppress_path = str(tmp_path / "suppress.json")
        # Write garbage to the file
        with open(suppress_path, "w") as f:
            f.write("{{{{not json!!!!")

        fetch_fn = _make_fetch_fn(_DISPATCH_ROWS)
        run_fn = _make_run_fn(_QUESTION_CONTENT_MAP)

        result = mod.run_check(
            fetch_fn=fetch_fn,
            run_fn=run_fn,
            suppression_path=suppress_path,
        )

        # Should still complete (not crash) and treat as unsuppressed
        assert result["check_id"] == 16
        assert result["severity"] == "crit"


# ===================================================================
# Group F: SC-6 — Logging format
# ===================================================================


class TestLogging:
    """SC-6 / AC-9: Verify logging format for Check 16."""

    def test_status_line_format(self, tmp_path):
        """AC-9: status_line includes '[FLEET-VIGILANCE Check 16] N scanned,
        M clusters, K CRIT, L suppressed'."""
        mod = _load_module()

        fetch_fn = _make_fetch_fn(_DISPATCH_ROWS)
        run_fn = _make_run_fn(_QUESTION_CONTENT_MAP)

        result = mod.run_check(
            fetch_fn=fetch_fn,
            run_fn=run_fn,
            suppression_path=str(tmp_path / "suppress.json"),
        )

        line = result["status_line"]
        assert "[FLEET-VIGILANCE Check 16]" in line or "Check 16" in line
        # Should contain counts
        assert "scanned" in line.lower() or "cluster" in line.lower()


# ===================================================================
# Group G: SC-7 — Integration / fail-safe isolation
# ===================================================================


class TestIntegration:
    """SC-7: Check 16 plugs into fleet-vigilance; errors don't abort the cycle."""

    def test_run_check_returns_correct_structure(self, tmp_path):
        """run_check returns a dict with all required keys."""
        mod = _load_module()

        result = mod.run_check(
            fetch_fn=_make_fetch_fn(_DISPATCH_ROWS),
            run_fn=_make_run_fn(_QUESTION_CONTENT_MAP),
            suppression_path=str(tmp_path / "suppress.json"),
        )

        # Required keys per blind_spot_checks.py convention
        assert "check_id" in result
        assert result["check_id"] == 16
        assert "severity" in result
        assert result["severity"] in ("ok", "warn", "crit", "error_unavailable")
        assert "status_line" in result
        assert "dm_payload" in result or result["severity"] == "ok"
        assert "dm_suppressed" in result

    def test_run_check_handles_fetch_fn_exception(self, tmp_path):
        """If fetch_fn raises, check returns error_unavailable (not crash)."""
        mod = _load_module()

        def exploding_fetch(sql, params=None):
            raise ConnectionError("DB connection refused")

        result = mod.run_check(
            fetch_fn=exploding_fetch,
            run_fn=_make_run_fn({}),
            suppression_path=str(tmp_path / "suppress.json"),
        )

        assert result["check_id"] == 16
        assert result["severity"] == "error_unavailable"


# ===================================================================
# Group H: Output-variance test (Stub Detection Gate)
# ===================================================================


class TestOutputVariance:
    """Gate: Two different inputs → two different outputs.
    Detects hardcoded stub implementations."""

    def test_output_varies_with_input(self, tmp_path):
        """Cluster of 4 similar questions vs 5 unrelated questions →
        different severity and cluster counts."""
        mod = _load_module()

        suppress_path = str(tmp_path / "suppress.json")

        # Input A: 4 similar + 1 unrelated → should CRIT
        result_a = mod.run_check(
            fetch_fn=_make_fetch_fn(_DISPATCH_ROWS),
            run_fn=_make_run_fn(_QUESTION_CONTENT_MAP),
            suppression_path=suppress_path,
        )

        # Reset suppression for clean second run
        if os.path.exists(suppress_path):
            os.remove(suppress_path)

        # Input B: 5 completely unrelated questions → should be ok
        unrelated_rows = [
            {
                "story_id": f"STORY-10{i}",
                "repo": "tech-dev-agents",
                "branch": f"story-10{i}/story-10{i}",
                "status": "needs_info",
                "needs_info_path": f"features/story-10{i}/QUESTION.md",
                "paused_at": f"2026-04-29T1{i}:00:00+00:00",
            }
            for i in range(5)
        ]
        unrelated_content = {
            "STORY-100": "How do I configure the Redis connection pool size?",
            "STORY-101": "The frontend build fails with a TypeScript error in UserCard.tsx",
            "STORY-102": "Should the CSV export include archived records?",
            "STORY-103": "The migration 008 conflicts with migration 007 on column rename",
            "STORY-104": "Do we need rate limiting on the public /health endpoint?",
        }

        result_b = mod.run_check(
            fetch_fn=_make_fetch_fn(unrelated_rows),
            run_fn=_make_run_fn(unrelated_content),
            suppression_path=suppress_path,
        )

        # Results must differ
        assert result_a["severity"] != result_b["severity"], (
            f"Output did not vary: both returned severity={result_a['severity']}"
        )
        # A should be crit, B should be ok
        assert result_a["severity"] == "crit"
        assert result_b["severity"] == "ok"


# ===================================================================
# Group I: Incident replay — 2026-04-30
# ===================================================================


class TestIncidentReplay:
    """Replay the 2026-04-30 incident: 4 similar + 1 unrelated needs_info question."""

    def test_phase_routing_incident_replay(self, tmp_path):
        """Full incident replay: 4 phase-routing questions detected,
        1 unrelated excluded. CRIT DM sent with correct payload."""
        mod = _load_module()

        result = mod.run_check(
            fetch_fn=_make_fetch_fn(_DISPATCH_ROWS),
            run_fn=_make_run_fn(_QUESTION_CONTENT_MAP),
            suppression_path=str(tmp_path / "suppress.json"),
        )

        assert result["severity"] == "crit"
        clusters = result.get("clusters", [])
        big_cluster = max(clusters, key=lambda c: len(c.get("story_ids", [])))

        # AC-6: DM payload includes required fields
        dm = result["dm_payload"]
        assert dm is not None
        # Cluster size
        assert big_cluster.get("size", len(big_cluster.get("story_ids", []))) >= 4
        # Story IDs present
        assert any(
            "STORY-008" in str(v) for v in dm.values()
        ) or "STORY-008" in str(dm)
        # Sample text present (200 chars max)
        sample = dm.get("sample_text", dm.get("sample", ""))
        assert len(sample) <= 200 or "phase" in str(dm).lower()

    def test_unrelated_questions_no_cluster(self, tmp_path):
        """5 unrelated questions → no clusters → ok."""
        mod = _load_module()

        unrelated_rows = [
            {
                "story_id": f"STORY-10{i}",
                "repo": "tech-dev-agents",
                "branch": f"story-10{i}/story-10{i}",
                "status": "needs_info",
                "needs_info_path": f"features/story-10{i}/QUESTION.md",
                "paused_at": f"2026-04-29T1{i}:00:00+00:00",
            }
            for i in range(5)
        ]
        unrelated_content = {
            "STORY-100": "How do I configure the Redis connection pool size?",
            "STORY-101": "The frontend build fails with a TypeScript error in UserCard.tsx",
            "STORY-102": "Should the CSV export include archived records?",
            "STORY-103": "The migration 008 conflicts with migration 007 on column rename",
            "STORY-104": "Do we need rate limiting on the public /health endpoint?",
        }

        result = mod.run_check(
            fetch_fn=_make_fetch_fn(unrelated_rows),
            run_fn=_make_run_fn(unrelated_content),
            suppression_path=str(tmp_path / "suppress.json"),
        )

        assert result["severity"] == "ok"
        clusters = result.get("clusters", [])
        # No cluster should have size ≥ 2
        for c in clusters:
            assert len(c.get("story_ids", [])) < 2
