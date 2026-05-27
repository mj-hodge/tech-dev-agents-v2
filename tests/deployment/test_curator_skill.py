"""Tests for the Cole the Curator skill helpers.

Covers build_curation_plan(), format_questions_for_teams(), and a mocked
full-cycle integration test.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import curator module from deployment path
# ---------------------------------------------------------------------------

CURATOR_PATH = (
    Path(__file__).resolve().parents[2]
    / "deployment"
    / "vm"
    / "skills"
    / "morris"
    / "curator"
    / "curator.py"
)
spec = importlib.util.spec_from_file_location("curator", CURATOR_PATH)
_curator = importlib.util.module_from_spec(spec)
sys.modules["curator"] = _curator
spec.loader.exec_module(_curator)

build_curation_plan = _curator.build_curation_plan
format_questions_for_teams = _curator.format_questions_for_teams
_infer_category = _curator._infer_category
Question = _curator.Question
CurationPlan = _curator.CurationPlan
MAX_QUESTIONS_PER_BATCH = _curator.MAX_QUESTIONS_PER_BATCH


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TODAY = date(2026, 4, 16)


@pytest.fixture
def empty_scratch():
    return {}


@pytest.fixture
def empty_wiki():
    return {}


@pytest.fixture
def sample_wiki():
    return {
        "wiki/systems/tech-datawarehouse.md": (
            "# Tech Datawarehouse\n\n"
            "ETL runs daily _(as of 2024-01-15)_.\n\n"
            "See also [[nonexistent-page]] for details.\n"
        ),
        "wiki/processes/existing-process.md": (
            "# Existing Process\n\nThis is documented.\n"
        ),
    }


@pytest.fixture
def sample_index():
    return ["wiki/systems/tech-datawarehouse.md"]


@pytest.fixture
def sample_scratch():
    return {
        "scratch/sdlc/new-process.md": "# New Process\n\nA brand new process.\n",
        "scratch/projects/tech-datawarehouse.md": (
            "# Tech Datawarehouse\n\nETL now runs weekly.\n"
        ),
    }


# ===========================================================================
# build_curation_plan — Unit Tests
# ===========================================================================


class TestNewPageDetected:
    def test_scratch_file_not_in_wiki_creates_new_page(self, sample_wiki, sample_index):
        scratch = {"scratch/sdlc/new-process.md": "# New\n\nContent.\n"}
        plan = build_curation_plan(scratch, sample_wiki, sample_index, today=TODAY)
        assert len(plan.new_pages) >= 1
        paths = [p.path for p in plan.new_pages]
        assert any("new-process.md" in p for p in paths)

    def test_new_page_has_source(self, sample_wiki, sample_index):
        scratch = {"scratch/sdlc/new-process.md": "# New\n"}
        plan = build_curation_plan(scratch, sample_wiki, sample_index, today=TODAY)
        for page in plan.new_pages:
            if "new-process.md" in page.path:
                assert "scratch/sdlc/new-process.md" in page.sources


class TestInferCategory:
    """Edge cases for _infer_category — PR #39 review fix."""

    def test_flat_scratch_path_returns_uncategorised(self):
        """scratch/file.md should return 'uncategorised', not 'scratch'."""
        assert _infer_category("scratch/file.md") == "uncategorised"

    def test_nested_scratch_path_returns_category(self):
        assert _infer_category("scratch/sdlc/file.md") == "sdlc"

    def test_non_scratch_nested_path_returns_first_dir(self):
        assert _infer_category("notes/file.md") == "notes"

    def test_bare_filename_returns_uncategorised(self):
        assert _infer_category("file.md") == "uncategorised"

    def test_flat_scratch_new_page_uses_uncategorised(self, sample_wiki, sample_index):
        """Integration: flat scratch path should produce wiki/uncategorised/... target."""
        scratch = {"scratch/flat-note.md": "# Flat Note\n\nContent.\n"}
        plan = build_curation_plan(scratch, sample_wiki, sample_index, today=TODAY)
        paths = [p.path for p in plan.new_pages]
        assert any(p.startswith("wiki/uncategorised/") for p in paths)


class TestUpdatedPageDetected:
    def test_scratch_overlapping_wiki_creates_update(self, sample_wiki, sample_index):
        scratch = {
            "scratch/projects/tech-datawarehouse.md": "# Updated content\n"
        }
        plan = build_curation_plan(scratch, sample_wiki, sample_index, today=TODAY)
        assert len(plan.updated_pages) >= 1
        paths = [p.path for p in plan.updated_pages]
        assert any("tech-datawarehouse" in p for p in paths)


class TestDedupDetected:
    def test_same_stem_in_scratch_and_wiki(self, sample_wiki, sample_index):
        scratch = {
            "scratch/projects/tech-datawarehouse.md": "# Dup content\n"
        }
        plan = build_curation_plan(scratch, sample_wiki, sample_index, today=TODAY)
        assert len(plan.dedup_actions) >= 1
        assert plan.dedup_actions[0].canonical == "wiki/systems/tech-datawarehouse.md"
        assert "scratch/projects/tech-datawarehouse.md" in plan.dedup_actions[0].merge_in


class TestStaleClaim:
    def test_old_date_flagged(self, sample_wiki, sample_index):
        plan = build_curation_plan({}, sample_wiki, sample_index, today=TODAY)
        stale = [f for f in plan.lint_findings if f.type == "stale_claim"]
        assert len(stale) >= 1
        assert "2024-01-15" in stale[0].detail

    def test_recent_date_not_flagged(self, sample_index):
        wiki = {
            "wiki/systems/foo.md": f"Updated _(as of {TODAY.isoformat()})_.\n"
        }
        plan = build_curation_plan({}, wiki, ["wiki/systems/foo.md"], today=TODAY)
        stale = [f for f in plan.lint_findings if f.type == "stale_claim"]
        assert len(stale) == 0


class TestBrokenXref:
    def test_nonexistent_link_flagged(self, sample_wiki, sample_index):
        plan = build_curation_plan({}, sample_wiki, sample_index, today=TODAY)
        broken = [f for f in plan.lint_findings if f.type == "broken_xref"]
        assert len(broken) >= 1
        assert "nonexistent-page" in broken[0].detail

    def test_valid_link_not_flagged(self, sample_index):
        wiki = {
            "wiki/a.md": "See [[b]] for more.\n",
            "wiki/b.md": "# B\n",
        }
        plan = build_curation_plan({}, wiki, ["wiki/a.md", "wiki/b.md"], today=TODAY)
        broken = [f for f in plan.lint_findings if f.type == "broken_xref"]
        assert len(broken) == 0


class TestOrphanDetected:
    def test_file_not_in_index_flagged(self, sample_wiki, sample_index):
        plan = build_curation_plan({}, sample_wiki, sample_index, today=TODAY)
        orphans = [f for f in plan.lint_findings if f.type == "orphan"]
        # existing-process.md is not in sample_index
        assert len(orphans) >= 1
        orphan_files = [o.file for o in orphans]
        assert any("existing-process" in f for f in orphan_files)


class TestEmptyScratch:
    def test_empty_scratch_produces_empty_plan(self, empty_scratch, sample_wiki, sample_index):
        plan = build_curation_plan(empty_scratch, sample_wiki, sample_index, today=TODAY)
        assert len(plan.new_pages) == 0
        # Lint may still find issues in wiki
        assert plan.version == "1.0"


class TestPlanSchemaValid:
    def test_plan_serialises_to_valid_json(self, sample_scratch, sample_wiki, sample_index):
        plan = build_curation_plan(sample_scratch, sample_wiki, sample_index, today=TODAY)
        raw = plan.to_json()
        parsed = json.loads(raw)
        assert parsed["version"] == "1.0"
        assert "created_at" in parsed
        assert isinstance(parsed["new_pages"], list)
        assert isinstance(parsed["updated_pages"], list)
        assert isinstance(parsed["dedup_actions"], list)
        assert isinstance(parsed["lint_findings"], list)
        assert isinstance(parsed["questions"], list)

    def test_all_new_pages_start_with_wiki(self, sample_scratch, sample_wiki, sample_index):
        plan = build_curation_plan(sample_scratch, sample_wiki, sample_index, today=TODAY)
        for page in plan.new_pages:
            assert page.path.startswith("wiki/"), f"Path does not start with wiki/: {page.path}"

    def test_question_ids_follow_pattern(self, sample_scratch, sample_wiki, sample_index):
        plan = build_curation_plan(sample_scratch, sample_wiki, sample_index, today=TODAY)
        import re
        for q in plan.questions:
            assert re.match(r"^q\d+$", q.id), f"Invalid question id: {q.id}"


class TestQuestionGeneration:
    def test_conflicting_content_generates_question(self, sample_scratch, sample_wiki, sample_index):
        plan = build_curation_plan(sample_scratch, sample_wiki, sample_index, today=TODAY)
        assert len(plan.questions) >= 1

    def test_question_max_10(self):
        """15 conflicting scratch files should produce exactly 10 questions."""
        wiki = {}
        scratch = {}
        index = []
        for i in range(15):
            wiki[f"wiki/topic-{i}.md"] = f"# Topic {i}\n\nOriginal content.\n"
            scratch[f"scratch/topic-{i}.md"] = f"# Topic {i}\n\nDifferent content.\n"
            index.append(f"wiki/topic-{i}.md")

        plan = build_curation_plan(scratch, wiki, index, today=TODAY)
        assert len(plan.questions) == MAX_QUESTIONS_PER_BATCH
        assert len(plan.questions) == 10


# ===========================================================================
# format_questions_for_teams — Unit Tests
# ===========================================================================


class TestFormatSingleQuestion:
    def test_single_question(self):
        qs = [Question(id="q1", context="ctx", question="What?", proposed_default="yes")]
        result = format_questions_for_teams(qs)
        assert result is not None
        assert "Q1:" in result
        assert "What?" in result
        assert "ctx" in result
        assert "yes" in result


class TestFormatMultipleQuestions:
    def test_five_questions(self):
        qs = [
            Question(id=f"q{i}", context=f"ctx-{i}", question=f"Q {i}?", proposed_default=f"d{i}")
            for i in range(1, 6)
        ]
        result = format_questions_for_teams(qs)
        assert result is not None
        for i in range(1, 6):
            assert f"Q{i}:" in result


class TestFormatEmptyQuestions:
    def test_empty_returns_none(self):
        assert format_questions_for_teams([]) is None


class TestFormatReplyInstructions:
    def test_reply_format_included(self):
        qs = [Question(id="q1", context="c", question="q?", proposed_default="d")]
        result = format_questions_for_teams(qs)
        assert "Reply format" in result
        assert "Q1: yes" in result


# ===========================================================================
# Integration Test (Mocked)
# ===========================================================================


class TestFullCurationCycle:
    """End-to-end test with a mock filesystem — no real I/O."""

    def test_full_cycle(self):
        scratch = {
            "scratch/sdlc/new-process.md": "# New Process\n\nBrand new content.\n",
            "scratch/projects/tech-datawarehouse.md": (
                "# Tech Datawarehouse\n\nETL runs weekly now.\n"
            ),
        }
        wiki = {
            "wiki/systems/tech-datawarehouse.md": (
                "# Tech Datawarehouse\n\n"
                "ETL runs daily _(as of 2024-01-15)_.\n\n"
                "See [[missing-page]] for pipeline docs.\n"
            ),
            "wiki/processes/existing.md": (
                "# Existing\n\nAlready documented.\n"
            ),
        }
        index = ["wiki/systems/tech-datawarehouse.md"]
        sources = {"sources/2026-04-01/meeting-notes.md": "Notes from meeting.\n"}

        plan = build_curation_plan(scratch, wiki, index, sources, today=TODAY)

        # New pages: new-process.md should be promoted
        assert len(plan.new_pages) >= 1
        new_paths = [p.path for p in plan.new_pages]
        assert any("new-process" in p for p in new_paths)

        # Dedup: tech-datawarehouse exists in both scratch and wiki
        assert len(plan.dedup_actions) >= 1

        # Lint: stale claim + orphan + broken xref
        finding_types = {f.type for f in plan.lint_findings}
        assert "stale_claim" in finding_types
        assert "orphan" in finding_types
        assert "broken_xref" in finding_types

        # Questions: conflicting ETL schedule
        assert len(plan.questions) >= 1

        # Schema: serialises cleanly
        parsed = json.loads(plan.to_json())
        assert parsed["version"] == "1.0"
        assert len(parsed["questions"]) <= 10

        # Teams formatting works
        msg = format_questions_for_teams(plan.questions)
        assert msg is not None
        assert "Cole (Morris-as-curator)" in msg
