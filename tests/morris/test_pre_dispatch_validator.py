"""STORY-1006 — Phase 7 RED tests for the pre-dispatch validator module.

Covers the structural-completeness rule set plus the three REPLAY-2 scenarios
mapped from the 2026-05-12 manual surgeries (dispatch_795/803/804.py).

Tests are written against the public surface re-exported from
`tech_dev_agents.morris.pre_dispatch`. Adding rules without adding tests will
break the suite — that's the point.
"""

from __future__ import annotations

import pytest

from tech_dev_agents.morris.pre_dispatch import (
    ValidationResult,
    extract_required_sections,
    validate_dispatch_seed,
    validate_seed_completeness,
)


# ---------------------------------------------------------------------------
# Fixture: a complete, well-formed Medium-scope seed
# ---------------------------------------------------------------------------


_COMPLETE_MEDIUM_SEED = """# STORY-9001 — Example seed

## Problem
Things are broken in a real way.

## Goal
Make them less broken with a deterministic check.

## Success criteria
- SC-1: a test passes.

## Files to modify
- foo.py

## Files to NOT modify
- bar.py

## Verification plan
Run `pytest tests/foo.py -v` and confirm GREEN.

## Boundaries

| Always do | Ask first | Never do |
|---|---|---|
| Be safe | Be slow | Be unsafe |

## Do not do
- Don't skip the test.

## Done looks like
Tests pass on CI.

## Escalation contract
Escalate to Mark if blocked.
"""


_SMALL_MINIMAL_SEED = """# Tiny seed

## Problem
Trivial fix.

## Goal
Land it.

## Success criteria
- It works.

## Files to modify
- one.py

## Files to NOT modify
- two.py

## Verification plan
Run pytest.

## Boundaries
Never break main.

## Done looks like
PR merged.

## Escalation contract
Escalate to Mark.
"""


def _strip_section(seed: str, heading_substring: str) -> str:
    """Remove a markdown section by its heading substring (case-insensitive)."""
    out_lines: list[str] = []
    skipping = False
    for line in seed.splitlines():
        if line.startswith("#"):
            if heading_substring.lower() in line.lower():
                skipping = True
                continue
            else:
                skipping = False
        if not skipping:
            out_lines.append(line)
    return "\n".join(out_lines)


# ---------------------------------------------------------------------------
# Section extractor
# ---------------------------------------------------------------------------


class TestExtractRequiredSections:
    def test_extracts_all_canonical_sections(self):
        sections = extract_required_sections(_COMPLETE_MEDIUM_SEED)
        for key in (
            "problem", "goal", "success_criteria",
            "files_to_modify", "files_to_not_modify",
            "verification_plan", "boundaries",
            "done_looks_like", "escalation_contract",
        ):
            assert key in sections, f"expected {key} in extracted sections, got: {list(sections)}"

    def test_empty_input_returns_empty_map(self):
        assert extract_required_sections("") == {}

    def test_case_insensitive_heading_match(self):
        seed = "### PROBLEM\nbody\n## goal\nmore"
        sections = extract_required_sections(seed)
        assert "problem" in sections
        assert "goal" in sections


# ---------------------------------------------------------------------------
# Section-completeness — one test per REQUIRED_SECTIONS key
# ---------------------------------------------------------------------------


class TestSeedCompleteness:
    def test_complete_seed_passes(self):
        result = validate_seed_completeness(_COMPLETE_MEDIUM_SEED, scope="medium")
        assert result.ok, f"expected ok=True, got missing={result.missing}"

    @pytest.mark.parametrize(
        "heading_substring, expected_missing",
        [
            ("## Problem", "problem"),
            ("## Goal", "goal"),
            ("## Success criteria", "success_criteria"),
            ("## Files to modify", "files_to_modify"),
            ("## Files to NOT modify", "files_to_not_modify"),
            ("## Verification plan", "verification_plan"),
            ("## Boundaries", "boundaries"),
            ("## Done looks like", "done_looks_like"),
            ("## Escalation contract", "escalation_contract"),
        ],
    )
    def test_missing_each_required_section_fails(self, heading_substring, expected_missing):
        stripped = _strip_section(_COMPLETE_MEDIUM_SEED, heading_substring)
        result = validate_seed_completeness(stripped, scope="medium")
        assert not result.ok, f"expected ok=False after stripping {heading_substring}"
        assert expected_missing in result.missing, (
            f"expected {expected_missing!r} in missing, got {result.missing}"
        )


# ---------------------------------------------------------------------------
# Scope-specific rules
# ---------------------------------------------------------------------------


class TestScopeRules:
    def test_medium_missing_donotdo_fails(self):
        # _COMPLETE_MEDIUM_SEED has 'Do not do' AND 'Never do' in Boundaries
        # Strip both signals.
        seed = _COMPLETE_MEDIUM_SEED.replace("## Do not do\n- Don't skip the test.\n\n", "")
        seed = seed.replace("Never do", "Don't")
        result = validate_seed_completeness(seed, scope="medium")
        assert "do_not_do" in result.missing, f"got missing={result.missing}"

    def test_small_minimal_seed_passes(self):
        result = validate_seed_completeness(_SMALL_MINIMAL_SEED, scope="small")
        assert result.ok, f"small seed should pass, missing={result.missing}"

    def test_pipeline_missing_gc_data_v2_ref_fails(self):
        # The complete medium seed has no gc-data-v2 references.
        result = validate_seed_completeness(
            _COMPLETE_MEDIUM_SEED, scope="medium", build_type="pipeline",
        )
        assert "pipeline_canon_refs" in result.missing, (
            f"pipeline seed missing canon refs should fail, got missing={result.missing}"
        )

    def test_pipeline_with_canon_refs_passes(self):
        seed = _COMPLETE_MEDIUM_SEED + "\n\nSee gc-data-v2/sources/example and gc-data-v2/platform/pipeline-standard.md."
        result = validate_seed_completeness(seed, scope="medium", build_type="pipeline")
        assert result.ok, f"pipeline seed with refs should pass, missing={result.missing}"

    def test_repo_pattern_infers_pipeline_build_type(self):
        result = validate_seed_completeness(
            _COMPLETE_MEDIUM_SEED, scope="medium", repo="walmart-supplier-v2",
        )
        assert "pipeline_canon_refs" in result.missing


# ---------------------------------------------------------------------------
# Verification-plan coverage rule (Medium+)
# ---------------------------------------------------------------------------


class TestVerificationPlanCoverage:
    def test_medium_verification_plan_without_runnable_command_fails(self):
        seed = _COMPLETE_MEDIUM_SEED.replace(
            "Run `pytest tests/foo.py -v` and confirm GREEN.",
            "Just eyeball it.",
        )
        result = validate_seed_completeness(seed, scope="medium")
        assert "verification_plan_coverage" in result.missing

    def test_small_verification_plan_without_command_still_passes(self):
        # Small scope: we do not enforce verification-plan coverage rule.
        seed = _SMALL_MINIMAL_SEED.replace("Run pytest.", "Look at it.")
        result = validate_seed_completeness(seed, scope="small")
        assert result.ok, f"small seed should still pass, missing={result.missing}"


# ---------------------------------------------------------------------------
# Payload-level gate (STORY-1009 contract)
# ---------------------------------------------------------------------------


class TestValidateDispatchSeedPayload:
    def test_missing_do_not_do_fails(self):
        result = validate_dispatch_seed({
            "story_id": "STORY-X",
            "scope": "medium",
            "verification_plan": "pytest tests/x.py",
            "red_test_paths": ["tests/x.py"],
            "acceptance_criteria": "GREEN.",
        })
        assert "do_not_do" in result.missing
        assert not result.ok

    def test_missing_verification_plan_fails(self):
        result = validate_dispatch_seed({
            "story_id": "STORY-X",
            "scope": "medium",
            "do_not_do": "don't",
            "red_test_paths": ["tests/x.py"],
            "acceptance_criteria": "GREEN.",
        })
        assert "verification_plan" in result.missing

    def test_missing_red_test_paths_fails(self):
        result = validate_dispatch_seed({
            "story_id": "STORY-X",
            "scope": "medium",
            "do_not_do": "don't",
            "verification_plan": "pytest tests/x.py",
            "acceptance_criteria": "GREEN.",
        })
        assert "red_test_paths" in result.missing

    def test_missing_acceptance_criteria_fails(self):
        result = validate_dispatch_seed({
            "story_id": "STORY-X",
            "scope": "medium",
            "do_not_do": "don't",
            "verification_plan": "pytest",
            "red_test_paths": ["x"],
        })
        assert "acceptance_criteria" in result.missing

    def test_complete_payload_passes(self):
        result = validate_dispatch_seed({
            "story_id": "STORY-X",
            "scope": "medium",
            "do_not_do": "don't",
            "verification_plan": "pytest",
            "red_test_paths": ["tests/x.py"],
            "acceptance_criteria": "GREEN.",
        })
        assert result.ok, f"expected ok=True, missing={result.missing}"

    def test_small_scope_skips_payload_field_checks(self):
        # Small-scope payloads are intentionally lighter (STORY-1006 SC-9).
        result = validate_dispatch_seed({"story_id": "STORY-X", "scope": "small"})
        assert result.ok, f"small payload should pass, missing={result.missing}"

    def test_rework_endpoint_requires_failure_list(self):
        result = validate_dispatch_seed({
            "story_id": "STORY-X",
            "scope": "small",
            "_endpoint": "rework",
            "do_not_do": "x", "verification_plan": "x",
            "red_test_paths": ["x"], "acceptance_criteria": "x",
            "failure_list": [],
        })
        assert "failure_list" in result.missing


# ---------------------------------------------------------------------------
# REPLAY-2a / 2b / 2c — incident replay fixtures
# ---------------------------------------------------------------------------


# Reconstructed STORY-738 seed (pre-dispatch_804.py surgery).
# The surgery added partial-gate documentation — gap: do_not_do + verification_plan.
_REPLAY_2A_SEED_STORY_738 = """# STORY-738 — Operator UI for needs_info answers

## Problem
Operators have no way to answer needs_info from the UI.

## Goal
Add GET + POST retrieval endpoints behind a feature gate.

## Success criteria
- SC-1: endpoint returns 200 when enabled.

## Files to modify
- routes/operator.py

## Files to NOT modify
- v1 dispatch route.
"""


# Reconstructed STORY-766 seed (pre-dispatch_795.py surgery).
# Surgery added 6 fixes; pre-surgery seed was missing do_not_do + verification_plan + tests.
_REPLAY_2B_SEED_STORY_766 = """# STORY-766 — Fleet vigilance post-merge sweep

## Problem
Merges to deploy-relevant paths don't trigger post-merge deploys.

## Goal
Add Check 9 that detects merge commits and triggers deploys.

## Success criteria
- SC-1: Check 9 detects relevant merges.

## Files to modify
- fleet_vigilance/check_9.py

## Files to NOT modify
- check_1.py through check_8.py
"""


# Reconstructed STORY-802 seed (pre-dispatch_803.py surgery).
# Surgery fixed contract-test rename + LokiClient method + PR body.
_REPLAY_2C_SEED_STORY_802 = """# STORY-802 — Cost alert pipeline contract

## Problem
Cost alert pipeline has no contract test.

## Goal
Add LokiClient.query_cost_alerts() and a contract test.

## Success criteria
- SC-1: contract test passes.

## Files to modify
- loki_client.py

## Files to NOT modify
- prod LokiClient deploy.

## Verification plan
Eyeball the metrics dashboard.
"""


class TestReplay2_ManualSurgeries:
    """REPLAY-2 — the three 2026-05-12 manual surgeries must be blocked by the gate."""

    def test_replay_2a_story_738_pre_surgery_seed_rejected(self):
        """dispatch_804.py rework was caused by missing partial-gate documentation.

        At pre-surgery time, STORY-738 seed lacked Do-Not-Do, Boundaries,
        Verification plan, Done looks like, Escalation contract.
        """
        result = validate_seed_completeness(_REPLAY_2A_SEED_STORY_738, scope="medium")
        assert not result.ok, "STORY-738 pre-surgery seed should fail validation"
        # The dispatch_804.py surgery's core was do_not_do / boundary clarification.
        assert "verification_plan" in result.missing
        assert "boundaries" in result.missing
        assert "do_not_do" in result.missing  # medium scope rule

    def test_replay_2b_story_766_pre_surgery_seed_rejected(self):
        """dispatch_795.py surgery added 6 fixes; the seed had no verification path."""
        result = validate_seed_completeness(_REPLAY_2B_SEED_STORY_766, scope="medium")
        assert not result.ok, "STORY-766 pre-surgery seed should fail validation"
        assert "verification_plan" in result.missing
        assert "boundaries" in result.missing
        assert "do_not_do" in result.missing

    def test_replay_2c_story_802_pre_surgery_seed_rejected(self):
        """dispatch_803.py surgery fixed contract test + LokiClient missing method."""
        result = validate_seed_completeness(_REPLAY_2C_SEED_STORY_802, scope="medium")
        assert not result.ok, "STORY-802 pre-surgery seed should fail validation"
        # Verification plan was eyeball-only — no pytest/curl/gh.
        assert (
            "verification_plan_coverage" in result.missing
            or "verification_plan" in result.missing
        ), f"expected verification gap, got {result.missing}"
        assert "boundaries" in result.missing or "done_looks_like" in result.missing


# ---------------------------------------------------------------------------
# Error-payload shape
# ---------------------------------------------------------------------------


class TestValidationResultErrorPayload:
    def test_as_error_payload_shape(self):
        r = ValidationResult(ok=False, missing=["do_not_do", "verification_plan"])
        body = r.as_error_payload(story_id="STORY-X")
        assert body["error"] == "seed_validation"
        assert body["missing"] == ["do_not_do", "verification_plan"]
        assert body["story_id"] == "STORY-X"

    def test_as_error_payload_includes_structured(self):
        r = ValidationResult(
            ok=False,
            missing=["pipeline_canon_refs"],
            structured={"expected_pipeline_refs": ["gc-data-v2/sources/"]},
        )
        body = r.as_error_payload()
        assert "structured" in body
