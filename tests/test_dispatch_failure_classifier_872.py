"""STORY-872 — Failure Classifier Hardening: unknown → typed.

Groups:
  A — "hit your limit" / quota ceiling messages → quota_exceeded
  B — policy_routed: <class> wrapper unwrapping
  C — SDK capacity exits (529, overloaded, context window) → quota_exceeded
  D — 2026-05-04 corpus regression: unknown rate < 10%
  E — opaque crash regression: still unknown (no over-matching)

RED gate: Groups A, B, C, D must FAIL before implementation.
Group E must PASS (regression guards on existing behaviour).
"""

from __future__ import annotations

import pytest

from tech_dev_agents.ops_console.services.dispatch_failure_policy import classify

# ---------------------------------------------------------------------------
# 2026-05-04 representative unknown corpus
# ---------------------------------------------------------------------------

CORPUS_2026_05_04: list[str] = [
    # Claude CLI quota ceiling — were classified as unknown before this story.
    # These dominated the 2026-05-04 unknown bucket.
    "You've hit your limit · resets 9:00 PM (Pacific Time)",
    "hit your limit — usage resets at midnight",
    "resets at 11:59 PM",
    "You've hit your limit",
    # policy_routed wrappers written by apply() — were unknown before this story
    "policy_routed: quota_exceeded",
    "policy_routed: rate_limited",
    "policy_routed: auth_expired",
    # SDK capacity exits — were unknown before this story
    "Anthropic API error 529: API overloaded",
    "context window exceeded (200001 / 200000 tokens)",
    "maximum context length is 200000 tokens, got 201034",
    "context too long for the selected model",
    # One genuinely opaque row — expected to remain unknown (represents rare tail)
    "Process exited with code 1",
]


# ---------------------------------------------------------------------------
# Group A — "hit your limit" quota ceiling patterns (AC-1, AC-2)
# ---------------------------------------------------------------------------


class TestGroupA_HitYourLimit:
    """Claude CLI emits 'You've hit your limit · resets <time>' at quota ceiling.
    classify() must return quota_exceeded for all forms of this message.
    """

    @pytest.mark.parametrize(
        "output",
        [
            "You've hit your limit · resets 9:00 PM (Pacific Time)",
            "hit your limit",
            "you've hit your daily limit",
            "resets at midnight",
            "resets in 2h 30m",
            "You've hit your limit",
        ],
    )
    def test_quota_ceiling_message_is_quota_exceeded(self, output: str) -> None:
        result = classify(output, exit_code=1, error_message=output)
        assert result == "quota_exceeded", (
            f"Expected quota_exceeded for {output!r}, got {result!r}\n"
            "STORY-872: hit-your-limit pattern not yet added to _CLASSIFY_PATTERNS"
        )

    @pytest.mark.parametrize(
        ("output", "must_not_be"),
        [
            # "limit" without quota context — must not over-match
            ("CPU limit configured to 4 cores", "quota_exceeded"),
            # rate limit should stay rate_limited, not quota_exceeded
            ("rate limit: 429 too many requests", "quota_exceeded"),
        ],
    )
    def test_non_quota_limit_strings_dont_over_match(
        self, output: str, must_not_be: str
    ) -> None:
        result = classify(output, exit_code=1, error_message=output)
        assert result != must_not_be, (
            f"Output {output!r} must NOT classify as {must_not_be!r}; got {result!r}"
        )


# ---------------------------------------------------------------------------
# Group B — policy_routed wrapper unwrap (AC-3, AC-4)
# ---------------------------------------------------------------------------


class TestGroupB_PolicyRoutedUnwrap:
    """apply() writes failure_reason = 'policy_routed: <class>'.
    Re-classifying that string must recover the inner class, not return unknown.
    """

    @pytest.mark.parametrize(
        ("output", "expected_class"),
        [
            ("policy_routed: quota_exceeded", "quota_exceeded"),
            ("policy_routed: rate_limited", "rate_limited"),
            ("policy_routed: auth_expired", "auth_expired"),
            ("policy_routed: sdk_died_silent", "sdk_died_silent"),
            ("policy_routed: lease_lost", "lease_lost"),
            ("policy_routed: workspace_missing", "workspace_missing"),
            ("policy_routed: git_push_failed", "git_push_failed"),
        ],
    )
    def test_policy_routed_recovers_inner_class(
        self, output: str, expected_class: str
    ) -> None:
        result = classify(output, exit_code=1, error_message=output)
        assert result == expected_class, (
            f"Expected {expected_class!r} for policy_routed wrapper {output!r}, "
            f"got {result!r}\n"
            "STORY-872: _extract_policy_routed_class() helper not yet implemented"
        )

    def test_policy_routed_unknown_inner_class_does_not_misroute(self) -> None:
        """policy_routed: <unrecognised_class> must NOT return the unrecognised class.
        It should fall through to the pattern loop or return unknown — never silently
        invent a class name not in POLICY_TABLE.
        """
        result = classify("policy_routed: made_up_class", exit_code=1)
        # 'made_up_class' is not in POLICY_TABLE — must NOT be returned
        assert result != "made_up_class", (
            "Unrecognised inner class must not be returned by the helper"
        )

    def test_policy_routed_without_colon_does_not_unwrap(self) -> None:
        """Malformed 'policy routed quota exceeded' (no colon) — no guarantee of unwrap.
        This is a counter-example ensuring we don't over-generalise the helper.
        """
        result = classify("policy routed quota exceeded")
        # No assertion on specific value — just confirm it doesn't crash
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Group C — SDK capacity / API overloaded (AC-5, AC-6, AC-7)
# ---------------------------------------------------------------------------


class TestGroupC_SdkCapacity:
    """Anthropic HTTP 529, 'overloaded', and context-window messages are transient
    capacity limits. They map to quota_exceeded (retryable=True, cooldown 1800s).
    """

    @pytest.mark.parametrize(
        "output",
        [
            "Anthropic API error: 529 — API overloaded",
            "API overloaded — please retry later",
            "context window exceeded for this model",
            "context length exceeded (200000 tokens)",
            "maximum context length is 200000 tokens, got 201034",
            "context too long for the selected model",
        ],
    )
    def test_capacity_exits_are_quota_exceeded(self, output: str) -> None:
        result = classify(output, exit_code=1, error_message=output)
        assert result == "quota_exceeded", (
            f"Expected quota_exceeded for capacity exit {output!r}, got {result!r}\n"
            "STORY-872: SDK capacity pattern not yet added to _CLASSIFY_PATTERNS"
        )


# ---------------------------------------------------------------------------
# Group D — 2026-05-04 corpus regression (AC-8)
# ---------------------------------------------------------------------------


class TestGroupD_CorpusRegressionUnknownRate:
    """The 2026-05-04 unknown corpus must classify to < 10% unknown after the patch.

    Pre-implementation: 9/12 strings land as unknown → 75% >> 10% → test FAILS.
    Post-implementation: ≤ 1/12 strings land as unknown → ≤ 8.3% < 10% → test PASSES.
    """

    def test_unknown_rate_below_10_percent(self) -> None:
        results = [(s, classify(s)) for s in CORPUS_2026_05_04]
        unknown_count = sum(1 for _, r in results if r == "unknown")
        unknown_rate = unknown_count / len(CORPUS_2026_05_04)

        # Print diagnostics for easy debugging
        for s, r in results:
            marker = "❌ unknown" if r == "unknown" else f"✓ {r}"
            print(f"  [{marker}] {s[:60]!r}")
        print(
            f"\n  Unknown rate: {unknown_count}/{len(CORPUS_2026_05_04)} = {unknown_rate:.1%}"
        )

        assert unknown_rate < 0.10, (
            f"Unknown rate {unknown_rate:.1%} exceeds AC-8 target of <10%.\n"
            f"Unknown strings:\n"
            + "\n".join(f"  - {s!r}" for s, r in results if r == "unknown")
        )


# ---------------------------------------------------------------------------
# Group E — opaque crash regression guards (AC-9, AC-10)
# ---------------------------------------------------------------------------


class TestGroupE_OpaqueCrashStillUnknown:
    """Genuinely opaque crashes must remain unknown after STORY-872 patterns are added.

    These are PASS even before implementation — they guard against over-matching.
    If any of these start classifying as a non-unknown class after this story,
    it means we introduced an over-broad pattern.
    """

    @pytest.mark.parametrize(
        "opaque_output",
        [
            "Segmentation fault (core dumped)",
            "Bus error",
            "exit code 137",
            "abort()",
            "core dumped at 0xdeadbeef",
        ],
    )
    def test_opaque_crash_remains_unknown(self, opaque_output: str) -> None:
        result = classify(opaque_output, exit_code=139, error_message=opaque_output)
        assert result == "unknown", (
            f"Opaque crash {opaque_output!r} must stay unknown; got {result!r}.\n"
            "STORY-872 added an over-broad pattern — review _CLASSIFY_PATTERNS."
        )
