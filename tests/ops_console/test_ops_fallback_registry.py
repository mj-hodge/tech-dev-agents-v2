"""STORY-1010: Ops-skill fallback registry tests.

SC-1: Registry returns runnable SQL within 60s.
SC-2: All 5 operator skills registered.

RED reasons (until Phase 8):
  - ops_skill_fallback_registry module does not exist
  - REGISTRY dict is empty
  - render_fallback() not implemented
  - API route /api/ops/fallback/{skill_name} not mounted
"""

from __future__ import annotations

import time

import pytest

from tech_dev_agents.ops_console.ops_skill_fallback_registry import (
    REGISTRY,
    Fallback,
    get_fallback,
    register_fallback,
    render_fallback,
)


# ---------------------------------------------------------------------------
# A: SC-2 — all five operator skills registered
# ---------------------------------------------------------------------------


class TestAllFiveSkillsRegistered:
    """SC-2: The 5 highest-leverage operator skills are registered at import time."""

    EXPECTED_SKILLS = sorted([
        "dead-letter-purge",
        "dispatch-recovery",
        "force-claim",
        "release-stale-claim",
        "requeue-failed",
    ])

    def test_all_five_skills_registered(self):
        assert sorted(REGISTRY.keys()) == self.EXPECTED_SKILLS

    def test_each_entry_is_fallback_dataclass(self):
        for skill_name, fb in REGISTRY.items():
            assert isinstance(fb, Fallback), f"{skill_name} should be a Fallback instance"
            assert fb.skill_name == skill_name
            assert fb.sql_template, f"{skill_name} sql_template must be non-empty"
            assert fb.runbook_url, f"{skill_name} runbook_url must be non-empty"
            assert fb.description, f"{skill_name} description must be non-empty"


# ---------------------------------------------------------------------------
# B: SC-1 — render_fallback returns runnable SQL within 60s
# ---------------------------------------------------------------------------


class TestRenderFallback:
    """SC-1: render_fallback produces operator-facing SQL block quickly."""

    def test_requeue_failed_fallback_renders(self):
        result = render_fallback("requeue-failed")
        assert result is not None
        assert "UPDATE" in result or "update" in result.lower()
        assert "dispatch_state_current" in result.lower() or "dispatch" in result.lower()
        assert "enqueued" in result.lower() or "requeue" in result.lower()

    def test_requeue_failed_includes_runbook_url(self):
        result = render_fallback("requeue-failed")
        assert "http" in result  # contains a URL

    def test_render_fallback_completes_within_60s(self):
        start = time.monotonic()
        render_fallback("requeue-failed")
        elapsed = time.monotonic() - start
        assert elapsed < 60, f"render_fallback took {elapsed:.1f}s — must be < 60s"

    def test_render_fallback_with_params(self):
        result = render_fallback("requeue-failed", repo="test-repo")
        assert result is not None

    def test_render_fallback_unknown_skill_returns_none(self):
        result = render_fallback("nonexistent-skill")
        assert result is None


# ---------------------------------------------------------------------------
# C: get_fallback / register_fallback API
# ---------------------------------------------------------------------------


class TestGetFallback:
    """Verify get_fallback returns correct Fallback objects."""

    def test_get_known_skill(self):
        fb = get_fallback("requeue-failed")
        assert fb is not None
        assert fb.skill_name == "requeue-failed"
        assert isinstance(fb.required_params, list)

    def test_get_unknown_skill_returns_none(self):
        fb = get_fallback("nonexistent-skill")
        assert fb is None


# ---------------------------------------------------------------------------
# D: All 5 SQL templates are syntactically reasonable
# ---------------------------------------------------------------------------


class TestSQLTemplates:
    """Each registered fallback has a SQL template that looks like valid SQL."""

    @pytest.mark.parametrize("skill", [
        "requeue-failed",
        "dispatch-recovery",
        "release-stale-claim",
        "dead-letter-purge",
        "force-claim",
    ])
    def test_sql_template_contains_sql_keyword(self, skill):
        fb = get_fallback(skill)
        assert fb is not None, f"{skill} not registered"
        sql = fb.sql_template.upper()
        assert any(kw in sql for kw in ("UPDATE", "DELETE", "INSERT", "SELECT")), (
            f"{skill} sql_template should contain a SQL keyword"
        )
