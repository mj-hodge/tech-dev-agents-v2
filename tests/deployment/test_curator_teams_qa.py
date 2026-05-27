"""Tests for Curator Teams Q&A delivery — STORY-340.

Covers: message formatting, reply parsing, default application,
Q&A archiving, timeout logic, auto-merge decision, and integration cycle.
"""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Import curator modules from deployment path (non-package layout)
# ---------------------------------------------------------------------------

_CURATOR_DIR = (
    Path(__file__).resolve().parents[2]
    / "deployment"
    / "vm"
    / "skills"
    / "morris"
    / "curator"
)

# Load curator.py first (teams_qa depends on it via relative import)
_curator_path = _CURATOR_DIR / "curator.py"
_curator_spec = importlib.util.spec_from_file_location("curator", _curator_path)
_curator_mod = importlib.util.module_from_spec(_curator_spec)
sys.modules["curator"] = _curator_mod
_curator_spec.loader.exec_module(_curator_mod)

# Patch the package so relative imports work in teams_qa
# Create a fake package for the curator directory
import types

_pkg_name = "deployment.vm.skills.morris.curator"
_pkg_parts = _pkg_name.split(".")
for i in range(len(_pkg_parts)):
    partial = ".".join(_pkg_parts[: i + 1])
    if partial not in sys.modules:
        sys.modules[partial] = types.ModuleType(partial)

# Point the curator package to the actual module
sys.modules[_pkg_name] = _curator_mod
sys.modules[f"{_pkg_name}.curator"] = _curator_mod

# Now load teams_qa — patch its relative import
_teams_qa_path = _CURATOR_DIR / "teams_qa.py"
_teams_qa_spec = importlib.util.spec_from_file_location(
    f"{_pkg_name}.teams_qa", _teams_qa_path,
    submodule_search_locations=[],
)
_teams_qa_mod = importlib.util.module_from_spec(_teams_qa_spec)

# Inject the curator module so "from .curator import Question" works
_teams_qa_mod.__package__ = _pkg_name
sys.modules[f"{_pkg_name}.teams_qa"] = _teams_qa_mod
_teams_qa_spec.loader.exec_module(_teams_qa_mod)

# Extract symbols
Question = _curator_mod.Question
CurationPlan = _curator_mod.CurationPlan

format_digest_message = _teams_qa_mod.format_digest_message
parse_reply = _teams_qa_mod.parse_reply
apply_defaults = _teams_qa_mod.apply_defaults
build_archive_files = _teams_qa_mod.build_archive_files
is_timed_out = _teams_qa_mod.is_timed_out
should_auto_merge = _teams_qa_mod.should_auto_merge


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_questions() -> list[Question]:
    return [
        Question(
            id="q1",
            context="sources mention 'periodic' but no specific frequency",
            question="CLR cadence — is it twice a year?",
            proposed_default="twice a year",
        ),
        Question(
            id="q2",
            context="saw both terms in different docs",
            question="tech-datawarehouse vs tech-data-platform — same thing?",
            proposed_default="same thing, canonical name = tech-datawarehouse",
        ),
        Question(
            id="q3",
            context="wiki/processes/settlement-processing.md is unowned",
            question="Settlement processing owner — attribute to Janet?",
            proposed_default="yes, attribute to Janet",
        ),
        Question(
            id="q4",
            context="scratch/BUSINESS_CONTEXT.md has the company overview",
            question="Promote to wiki or merge into gorilla-commerce.md?",
            proposed_default="merge into gorilla-commerce.md",
        ),
    ]


@pytest.fixture
def sample_stats() -> dict:
    return {"new": 5, "updated": 3, "dedup": 2}


# ---------------------------------------------------------------------------
# 1. Message Formatter
# ---------------------------------------------------------------------------


class TestFormatDigestMessage:
    """T1-T6: format_digest_message produces valid Teams markdown."""

    def test_empty_questions(self) -> None:
        """T1: Empty questions list produces message with 0 questions."""
        msg = format_digest_message(
            questions=[], pr_number=42, stats={"new": 1, "updated": 0, "dedup": 0}
        )
        assert "0 questions" in msg.lower() or "no questions" in msg.lower()
        assert "Q1" not in msg

    def test_single_question(self, sample_questions: list[Question]) -> None:
        """T2: Single question renders Q1 block."""
        msg = format_digest_message(
            questions=sample_questions[:1], pr_number=42, stats={"new": 1, "updated": 0, "dedup": 0}
        )
        assert "Q1" in msg
        assert "CLR cadence" in msg
        assert "twice a year" in msg
        # Only Q1 should appear as a question block (bold header)
        assert "**Q2:" not in msg

    def test_multiple_questions(self, sample_questions: list[Question]) -> None:
        """T3: 4 questions all numbered Q1-Q4."""
        msg = format_digest_message(
            questions=sample_questions, pr_number=42, stats={"new": 5, "updated": 3, "dedup": 2}
        )
        for i in range(1, 5):
            assert f"Q{i}" in msg

    def test_stats_in_header(self, sample_questions: list[Question], sample_stats: dict) -> None:
        """T4: Header shows stats correctly."""
        msg = format_digest_message(
            questions=sample_questions, pr_number=42, stats=sample_stats
        )
        assert "5" in msg  # new pages
        assert "3" in msg  # updated
        assert "2" in msg  # dedup

    def test_reply_format_hint(self, sample_questions: list[Question]) -> None:
        """T5: Message ends with reply format instruction."""
        msg = format_digest_message(
            questions=sample_questions, pr_number=42, stats={"new": 0, "updated": 0, "dedup": 0}
        )
        assert "Q1:" in msg.split("Reply")[-1] or "defaults" in msg.lower()

    def test_pr_link_included(self, sample_questions: list[Question]) -> None:
        """T6: PR number included in message."""
        msg = format_digest_message(
            questions=sample_questions, pr_number=99, stats={"new": 0, "updated": 0, "dedup": 0}
        )
        assert "99" in msg


# ---------------------------------------------------------------------------
# 2. Reply Parser
# ---------------------------------------------------------------------------


class TestParseReply:
    """T7-T15: parse_reply handles all reply formats."""

    def test_terse_answers(self) -> None:
        """T7: Q1: yes, Q2: no."""
        result = parse_reply("Q1: yes, Q2: no")
        assert result == {"q1": "yes", "q2": "no"}

    def test_verbose_answer(self) -> None:
        """T8: Long answer preserved."""
        result = parse_reply("Q1: actually it's quarterly not monthly")
        assert result == {"q1": "actually it's quarterly not monthly"}

    def test_skip_keyword(self) -> None:
        """T9: skip keyword preserved."""
        result = parse_reply("Q1: skip, Q2: yes")
        assert result == {"q1": "skip", "q2": "yes"}

    def test_defaults_keyword(self) -> None:
        """T10: 'defaults' signals accept all."""
        result = parse_reply("defaults")
        assert result == {"_all": "defaults"}

    def test_case_insensitive(self) -> None:
        """T11: Case-insensitive Q labels."""
        result = parse_reply("q1: YES, Q2: No")
        assert result["q1"] == "YES"
        assert result["q2"] == "No"

    def test_whitespace_tolerance(self) -> None:
        """T12: Extra whitespace stripped."""
        result = parse_reply("  Q1:  yes ,  Q2: no  ")
        assert result == {"q1": "yes", "q2": "no"}

    def test_free_form_fallback(self) -> None:
        """T13: Unstructured text captured as _raw."""
        result = parse_reply("use the scratch version for Q1 and skip Q2")
        assert "_raw" in result

    def test_empty_string(self) -> None:
        """T14: Empty string returns empty dict."""
        result = parse_reply("")
        assert result == {}

    def test_multiline_reply(self) -> None:
        """T15: Newline-separated answers parsed."""
        result = parse_reply("Q1: yes\nQ2: no\nQ3: skip")
        assert result == {"q1": "yes", "q2": "no", "q3": "skip"}


# ---------------------------------------------------------------------------
# 3. Default Application
# ---------------------------------------------------------------------------


class TestApplyDefaults:
    """T16-T20: apply_defaults merges answers with proposed defaults."""

    def test_all_answered(self, sample_questions: list[Question]) -> None:
        """T16: All answered — no defaults applied."""
        answers = {"q1": "yes", "q2": "no", "q3": "Janet", "q4": "merge"}
        resolved, explicit = apply_defaults(sample_questions, answers)
        assert resolved["q1"] == "yes"
        assert resolved["q2"] == "no"
        assert resolved["q3"] == "Janet"
        assert resolved["q4"] == "merge"
        assert explicit == {"q1", "q2", "q3", "q4"}

    def test_partial_answers(self, sample_questions: list[Question]) -> None:
        """T17: Unanswered Qs get proposed_default."""
        answers = {"q1": "yes"}
        resolved, explicit = apply_defaults(sample_questions, answers)
        assert resolved["q1"] == "yes"
        assert resolved["q2"] == "same thing, canonical name = tech-datawarehouse"
        assert resolved["q3"] == "yes, attribute to Janet"
        assert resolved["q4"] == "merge into gorilla-commerce.md"
        assert explicit == {"q1"}

    def test_all_defaults_keyword(self, sample_questions: list[Question]) -> None:
        """T18: _all=defaults applies all proposed defaults."""
        answers = {"_all": "defaults"}
        resolved, explicit = apply_defaults(sample_questions, answers)
        for q in sample_questions:
            assert resolved[q.id] == q.proposed_default
        assert explicit == set()

    def test_skip_uses_default(self, sample_questions: list[Question]) -> None:
        """T19: 'skip' answer uses proposed_default."""
        answers = {"q1": "skip", "q2": "no"}
        resolved, explicit = apply_defaults(sample_questions, answers)
        assert resolved["q1"] == "twice a year"  # proposed_default
        assert resolved["q2"] == "no"  # explicit answer preserved
        assert "q1" not in explicit
        assert "q2" in explicit

    def test_no_answers_timeout(self, sample_questions: list[Question]) -> None:
        """T20: No answers → all defaults."""
        resolved, explicit = apply_defaults(sample_questions, {})
        for q in sample_questions:
            assert resolved[q.id] == q.proposed_default
        assert explicit == set()


# ---------------------------------------------------------------------------
# 4. Q&A Archive
# ---------------------------------------------------------------------------


class TestBuildArchiveFiles:
    """T21-T26: build_archive_files produces correct file structure."""

    def test_archive_structure(self, sample_questions: list[Question]) -> None:
        """T21: Returns dict with 3 keys."""
        answers = {"q1": "yes", "q2": "no", "q3": "Janet", "q4": "merge"}
        files = build_archive_files(
            questions=sample_questions,
            answers=answers,
            pr_number=42,
            session_date="2026-04-22",
            session_number=1,
        )
        assert "session-001-questions.md" in files
        assert "session-001-answers.md" in files
        assert "session-001-summary.md" in files

    def test_questions_file_content(self, sample_questions: list[Question]) -> None:
        """T22: Questions file has numbered Qs with context and defaults."""
        files = build_archive_files(
            questions=sample_questions,
            answers={"q1": "yes"},
            pr_number=42,
            session_date="2026-04-22",
            session_number=1,
        )
        content = files["session-001-questions.md"]
        assert "Q1" in content
        assert "Q4" in content
        assert "CLR cadence" in content
        assert "twice a year" in content

    def test_answers_file_content(self, sample_questions: list[Question]) -> None:
        """T23: Answers file has Q/A pairs, defaults flagged."""
        answers = {"q1": "yes", "q2": "no"}
        resolved, explicit = apply_defaults(sample_questions, answers)
        files = build_archive_files(
            questions=sample_questions,
            answers=resolved,
            pr_number=42,
            session_date="2026-04-22",
            session_number=1,
            explicitly_answered=explicit,
        )
        content = files["session-001-answers.md"]
        assert "Q1" in content
        assert "yes" in content

    def test_summary_file_content(self, sample_questions: list[Question]) -> None:
        """T24: Summary file has date, PR#, stats."""
        files = build_archive_files(
            questions=sample_questions,
            answers={"q1": "yes", "q2": "no", "q3": "Janet", "q4": "merge"},
            pr_number=42,
            session_date="2026-04-22",
            session_number=1,
        )
        content = files["session-001-summary.md"]
        assert "2026-04-22" in content
        assert "42" in content

    def test_date_based_path(self, sample_questions: list[Question]) -> None:
        """T25: Path prefix includes date."""
        files = build_archive_files(
            questions=sample_questions,
            answers={"q1": "yes"},
            pr_number=42,
            session_date="2026-04-22",
            session_number=1,
            include_path_prefix=True,
        )
        for key in files:
            assert key.startswith("sources/2026-04-22-curator-q-and-a/")

    def test_session_numbering(self, sample_questions: list[Question]) -> None:
        """T26: Session number zero-padded to 3 digits."""
        files = build_archive_files(
            questions=sample_questions,
            answers={"q1": "yes"},
            pr_number=42,
            session_date="2026-04-22",
            session_number=1,
        )
        assert "session-001-questions.md" in files


# ---------------------------------------------------------------------------
# 5. Timeout Check
# ---------------------------------------------------------------------------


class TestIsTimedOut:
    """T27-T30: is_timed_out checks 24h boundary."""

    def test_before_timeout(self) -> None:
        """T27: 1h ago → not timed out."""
        sent_at = datetime.now(timezone.utc) - timedelta(hours=1)
        assert is_timed_out(sent_at) is False

    def test_at_timeout(self) -> None:
        """T28: Exactly 24h ago → timed out."""
        sent_at = datetime.now(timezone.utc) - timedelta(hours=24)
        assert is_timed_out(sent_at) is True

    def test_after_timeout(self) -> None:
        """T29: 25h ago → timed out."""
        sent_at = datetime.now(timezone.utc) - timedelta(hours=25)
        assert is_timed_out(sent_at) is True

    def test_custom_timeout(self) -> None:
        """T30: Custom timeout of 1h, sent 2h ago → timed out."""
        sent_at = datetime.now(timezone.utc) - timedelta(hours=2)
        assert is_timed_out(sent_at, timeout_hours=1) is True


# ---------------------------------------------------------------------------
# 6. Auto-Merge Decision
# ---------------------------------------------------------------------------


class TestShouldAutoMerge:
    """T31-T34: should_auto_merge checks conditions."""

    def test_all_resolved_ci_green(self) -> None:
        """T31: 0 outstanding + CI green → merge."""
        assert should_auto_merge(outstanding_questions=0, ci_green=True) is True

    def test_outstanding_questions(self) -> None:
        """T32: Outstanding Qs → no merge."""
        assert should_auto_merge(outstanding_questions=2, ci_green=True) is False

    def test_ci_failing(self) -> None:
        """T33: CI red → no merge."""
        assert should_auto_merge(outstanding_questions=0, ci_green=False) is False

    def test_both_bad(self) -> None:
        """T34: Outstanding + CI red → no merge."""
        assert should_auto_merge(outstanding_questions=1, ci_green=False) is False


# ---------------------------------------------------------------------------
# 7. Integration: Full Cycle
# ---------------------------------------------------------------------------


class TestIntegrationCycle:
    """T35-T37: End-to-end flow with mocked dependencies."""

    def test_send_receive_archive(self, sample_questions: list[Question]) -> None:
        """T35: Format → parse reply → build archive."""
        # Step 1: Format the digest message
        msg = format_digest_message(
            questions=sample_questions,
            pr_number=42,
            stats={"new": 5, "updated": 3, "dedup": 2},
        )
        assert "Q1" in msg
        assert "Q4" in msg

        # Step 2: Simulate Mark's reply
        reply_text = "Q1: yes, Q2: same thing, Q3: yes Janet, Q4: merge"
        answers = parse_reply(reply_text)
        assert len(answers) == 4

        # Step 3: Apply defaults (none needed — all answered)
        resolved, explicit = apply_defaults(sample_questions, answers)
        assert all(q.id in resolved for q in sample_questions)

        # Step 4: Build archive
        files = build_archive_files(
            questions=sample_questions,
            answers=resolved,
            pr_number=42,
            session_date="2026-04-22",
            session_number=1,
            explicitly_answered=explicit,
        )
        assert len(files) == 3

    def test_timeout_defaults(self, sample_questions: list[Question]) -> None:
        """T36: No reply → timeout → defaults applied."""
        sent_at = datetime.now(timezone.utc) - timedelta(hours=25)
        assert is_timed_out(sent_at) is True

        # No answers received
        resolved, explicit = apply_defaults(sample_questions, {})
        for q in sample_questions:
            assert resolved[q.id] == q.proposed_default
        assert explicit == set()

        # Should auto-merge after defaults applied
        assert should_auto_merge(outstanding_questions=0, ci_green=True) is True

    def test_zero_questions_auto_merge(self) -> None:
        """T37: No questions → immediately mergeable."""
        msg = format_digest_message(
            questions=[], pr_number=42, stats={"new": 3, "updated": 1, "dedup": 0}
        )
        assert should_auto_merge(outstanding_questions=0, ci_green=True) is True


# ---------------------------------------------------------------------------
# STORY-343 — Bug fix tests
# ---------------------------------------------------------------------------


class TestBuildArchiveDefaultDetection:
    """T40-T42: build_archive_files correctly tags defaults after apply_defaults()."""

    def test_t40_default_tag_after_apply_defaults(self, sample_questions: list[Question]) -> None:
        """T40: Defaulted answers show _(default applied)_ in archive."""
        answers = {"q1": "yes"}  # Only Q1 answered; Q2-Q4 will get defaults
        resolved, explicit = apply_defaults(sample_questions, answers)
        files = build_archive_files(
            questions=sample_questions,
            answers=resolved,
            pr_number=42,
            session_date="2026-04-22",
            session_number=1,
            explicitly_answered=explicit,
        )
        content = files["session-001-answers.md"]
        # Q1 was explicitly answered — should NOT have default tag
        q1_section = content.split("## Q1:")[1].split("## Q2:")[0]
        assert "_(default applied)_" not in q1_section
        # Q2 was defaulted — MUST have default tag
        q2_section = content.split("## Q2:")[1].split("## Q3:")[0]
        assert "_(default applied)_" in q2_section

    def test_t41_summary_count_after_apply_defaults(self, sample_questions: list[Question]) -> None:
        """T41: Summary answered_count matches only explicitly answered Qs."""
        answers = {"q1": "yes", "q2": "no"}  # 2 explicit, 2 defaulted
        resolved, explicit = apply_defaults(sample_questions, answers)
        files = build_archive_files(
            questions=sample_questions,
            answers=resolved,
            pr_number=42,
            session_date="2026-04-22",
            session_number=1,
            explicitly_answered=explicit,
        )
        content = files["session-001-summary.md"]
        assert "**Answered by Mark:** 2" in content
        assert "**Defaults applied:** 2" in content

    def test_t42_all_explicit_no_default_tags(self, sample_questions: list[Question]) -> None:
        """T42: All answers explicit — zero default tags."""
        answers = {"q1": "yes", "q2": "no", "q3": "Janet", "q4": "merge"}
        resolved, explicit = apply_defaults(sample_questions, answers)
        files = build_archive_files(
            questions=sample_questions,
            answers=resolved,
            pr_number=42,
            session_date="2026-04-22",
            session_number=1,
            explicitly_answered=explicit,
        )
        content = files["session-001-answers.md"]
        assert "_(default applied)_" not in content

    def test_t46_heuristic_fallback_without_explicit_set(self, sample_questions: list[Question]) -> None:
        """T46: When explicitly_answered=None, heuristic detects defaults by comparing to proposed_default."""
        # Simulate calling build_archive_files WITHOUT the explicit set (legacy callers).
        # Q1 has a non-default answer; Q2-Q4 keep their defaults.
        resolved, _ = apply_defaults(sample_questions, {"q1": "yes"})
        files = build_archive_files(
            questions=sample_questions,
            answers=resolved,
            pr_number=42,
            session_date="2026-04-22",
            session_number=1,
            explicitly_answered=None,  # Force heuristic path
        )
        content = files["session-001-answers.md"]
        # Q1 ("yes") differs from proposed default ("twice a year") → explicit
        q1_section = content.split("## Q1:")[1].split("## Q2:")[0]
        assert "_(default applied)_" not in q1_section
        # Q2 answer equals proposed default → heuristic marks it as defaulted
        q2_section = content.split("## Q2:")[1].split("## Q3:")[0]
        assert "_(default applied)_" in q2_section


class TestParseReplyCommaEdgeCases:
    """T43-T44: parse_reply handles answers containing commas."""

    def test_t43_last_answer_with_commas(self) -> None:
        """T43: Last answer containing commas is not truncated."""
        result = parse_reply("Q1: yes, Q2: same thing, canonical name = tech-datawarehouse")
        assert result["q2"] == "same thing, canonical name = tech-datawarehouse"

    def test_t44_first_answer_with_commas(self) -> None:
        """T44: First answer with commas, second answer simple."""
        result = parse_reply("Q1: a, b, c, Q2: d")
        assert result["q1"] == "a, b, c"
        assert result["q2"] == "d"


# ---------------------------------------------------------------------------
# 8. Curate Route
# ---------------------------------------------------------------------------


class TestCurateRoute:
    """T38-T39: POST /api/morris/curate endpoint.

    Tests the route handler logic directly (without full auth stack)
    by overriding the require_auth dependency.
    """

    def test_trigger_returns_202(self) -> None:
        """T38: Authenticated POST returns 202."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from tech_dev_agents.ops_console.auth import require_auth
        from tech_dev_agents.ops_console.routes.curate import router

        app = FastAPI()
        # Override auth to always pass
        app.dependency_overrides[require_auth] = lambda: None
        app.include_router(router, prefix="/api")

        mock_dispatch = AsyncMock()
        mock_dispatch.enqueue = AsyncMock(return_value={
            "story_id": "curator-on-demand-20260422",
            "repo": "tech-gc-knowledgebase",
            "scope": "small",
            "prompt": "Run curator skill",
            "enqueued_at": "2026-04-22T13:00:00Z",
            "enqueued_by": "mark",
            "title": "On-demand curation",
        })

        app.state.dispatch_db_service = mock_dispatch

        client = TestClient(app)
        resp = client.post("/api/morris/curate")
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "accepted"
        assert "story_id" in data

    def test_missing_dispatch_service(self) -> None:
        """T39: Missing dispatch service returns 503."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from tech_dev_agents.ops_console.auth import require_auth
        from tech_dev_agents.ops_console.routes.curate import router

        app = FastAPI()
        app.dependency_overrides[require_auth] = lambda: None
        app.include_router(router, prefix="/api")

        # No dispatch_db_service on app.state
        client = TestClient(app)
        resp = client.post("/api/morris/curate")
        assert resp.status_code == 503

    def test_t45_exception_leak_suppressed(self) -> None:
        """T45: Enqueue failure returns 500 without leaking exception details."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from tech_dev_agents.ops_console.auth import require_auth
        from tech_dev_agents.ops_console.routes.curate import router

        app = FastAPI()
        app.dependency_overrides[require_auth] = lambda: None
        app.include_router(router, prefix="/api")

        mock_dispatch = AsyncMock()
        mock_dispatch.enqueue = AsyncMock(
            side_effect=RuntimeError("connection refused to db:5432")
        )
        app.state.dispatch_db_service = mock_dispatch

        client = TestClient(app)
        resp = client.post("/api/morris/curate")
        assert resp.status_code == 500
        body = resp.text
        assert "connection refused" not in body
        assert "db:5432" not in body
