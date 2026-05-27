"""STORY-523: Cross-story reference validator — Phase 7 RED-state tests.

Tests for the `_prompt_references_other_story` validator added to
`POST /api/dispatch` (enqueue_story in dispatch.py).

All mismatch-detection tests are RED until Phase 8 adds:
  - `cross_story_reference: bool = False` field on DispatchRequest
  - `_prompt_references_other_story(prompt, story_id) -> list[str]` helper
  - 422 guard in `enqueue_story` when mismatches found and cross_story_reference=False
  - WARNING log when cross_story_reference=True and mismatches found

RED/GREEN state summary
-----------------------
  AC-1  (mismatch -> 422)              FAIL  <- validator not implemented
  AC-2  (matching prompt -> 201)       PASS  <- happy-path regression guard
  AC-3  (cross_story_reference=True    FAIL  <- WARNING not logged (no validator)
         + mismatch -> 201+WARNING)
  AC-4a (regression STORY-518 -> 422)  FAIL  <- validator not implemented
  AC-4b (regression STORY-520 -> 422)  FAIL  <- validator not implemented
  AC-5  (output-variance -> two 422s   FAIL  <- both return 201 currently
         with distinct details)
  AC-6  (no STORY-N in prompt -> 201)  PASS  <- happy-path regression guard
  AC-7  (self-reference -> 201)        PASS  <- happy-path regression guard
  AC-8  (multiple mismatches -> 422)   FAIL  <- validator not implemented
"""

from __future__ import annotations

import logging

import pytest

from tests.ops_console.conftest import inject_mock_services
from tech_dev_agents.ops_console.services.dispatch_service import DispatchFallbackService

# ---------------------------------------------------------------------------
# Per-test dispatch service injection
# ---------------------------------------------------------------------------


def _inject_dispatch(app, tmp_path):
    """Create a JSON-backed DispatchFallbackService and inject into app.state."""
    svc = DispatchFallbackService(str(tmp_path / "dispatch-queue.json"))
    inject_mock_services(app, dispatch_db_service=svc)
    return svc


# ---------------------------------------------------------------------------
# Base payload helper
# ---------------------------------------------------------------------------

_BASE = {
    "story_id": "STORY-500",
    "repo": "tech-dev-agents",
    "scope": "small",
    "enqueued_by": "mark",
}


def _payload(**overrides) -> dict:
    """Build a minimal valid dispatch payload, applying keyword overrides."""
    data = dict(_BASE)
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# AC-1: mismatch (no cross_story_reference flag) -> 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac1_mismatch_returns_422(client, app, tmp_path):
    """AC-1: Prompt mentioning a different STORY-N than story_id raises 422.

    Arrange: story_id=STORY-500, prompt references STORY-267.
    Act:     POST /api/dispatch without cross_story_reference flag.
    Assert:  HTTP 422; error body names both STORY-267 and STORY-500.
    """
    _inject_dispatch(app, tmp_path)
    payload = _payload(
        story_id="STORY-500",
        prompt="Please do STORY-267 work as part of this dispatch.",
    )
    resp = await client.post("/api/dispatch", json=payload)

    assert resp.status_code == 422, (
        f"Expected 422 for cross-story mismatch, got {resp.status_code}. "
        "Validator not yet implemented."
    )
    body = resp.text
    assert "STORY-267" in body, "Error body must identify the mismatching story"
    assert "STORY-500" in body, "Error body must identify the requested story_id"


# ---------------------------------------------------------------------------
# AC-2: matching prompt -> 201 (regression guard, expected GREEN)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac2_matching_prompt_returns_201(client, app, tmp_path):
    """AC-2: Prompt that only mentions the same story_id -> 201 (no regression).

    Arrange: story_id=STORY-500, prompt mentions STORY-500 explicitly.
    Act:     POST /api/dispatch.
    Assert:  HTTP 201 (no false positive from validator).
    """
    _inject_dispatch(app, tmp_path)
    payload = _payload(
        story_id="STORY-500",
        prompt="STORY-500: implement the feature spec. Start Phase 7.",
    )
    resp = await client.post("/api/dispatch", json=payload)

    assert resp.status_code == 201, (
        f"Expected 201 for matching story reference, got {resp.status_code}."
    )


# ---------------------------------------------------------------------------
# AC-3: cross_story_reference=True + mismatch -> 201 with WARNING log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac3_cross_story_reference_flag_logs_warning(client, app, tmp_path, caplog):
    """AC-3: cross_story_reference=True overrides 422; WARNING must be logged.

    Arrange: story_id=STORY-500, prompt mentions STORY-267, cross_story_reference=True.
    Act:     POST /api/dispatch.
    Assert:  HTTP 201; at least one WARNING log line mentions the mismatch.
    """
    _inject_dispatch(app, tmp_path)
    payload = _payload(
        story_id="STORY-500",
        prompt="Handle STORY-267 work here.",
        cross_story_reference=True,
    )

    with caplog.at_level(logging.WARNING, logger="tech_dev_agents.ops_console.routes.dispatch"):
        resp = await client.post("/api/dispatch", json=payload)

    assert resp.status_code == 201, (
        f"Expected 201 when cross_story_reference=True, got {resp.status_code}."
    )
    warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("STORY-267" in m for m in warning_msgs), (
        "Expected a WARNING log naming the mismatching story STORY-267. "
        "Validator warning log not yet implemented. "
        f"Captured WARNING records: {warning_msgs}"
    )


# ---------------------------------------------------------------------------
# AC-4a: regression -- STORY-518 incident
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac4a_regression_story_518(client, app, tmp_path):
    """AC-4a: Exact regression fixture from the 2026-04-22 STORY-518 incident.

    Arrange: story_id=STORY-518, prompt references STORY-267.
    Act:     POST /api/dispatch.
    Assert:  HTTP 422 (agent must never receive contradictory story prompt).
    """
    _inject_dispatch(app, tmp_path)
    payload = _payload(
        story_id="STORY-518",
        prompt=(
            "You are working on STORY-267. "
            "Start Phase 1 and produce seed.md for the agent dashboard story."
        ),
    )
    resp = await client.post("/api/dispatch", json=payload)

    assert resp.status_code == 422, (
        f"Regression STORY-518: expected 422 for story_id=STORY-518 with "
        f"STORY-267 in prompt, got {resp.status_code}."
    )


# ---------------------------------------------------------------------------
# AC-4b: regression -- STORY-520 incident
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac4b_regression_story_520(client, app, tmp_path):
    """AC-4b: Exact regression fixture from the 2026-04-22 STORY-520 incident.

    Arrange: story_id=STORY-520, prompt references STORY-517.
    Act:     POST /api/dispatch.
    Assert:  HTTP 422.
    """
    _inject_dispatch(app, tmp_path)
    payload = _payload(
        story_id="STORY-520",
        prompt=(
            "Continue work on STORY-517. "
            "The agent should pick up where Phase 6 left off."
        ),
    )
    resp = await client.post("/api/dispatch", json=payload)

    assert resp.status_code == 422, (
        f"Regression STORY-520: expected 422 for story_id=STORY-520 with "
        f"STORY-517 in prompt, got {resp.status_code}."
    )


# ---------------------------------------------------------------------------
# AC-5: output-variance -- two mismatches produce distinct error details
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac5_output_variance_different_mismatches(client, app, tmp_path):
    """AC-5: Two different cross-story mismatches produce distinguishably different errors.

    Guards against a stub that always returns the same error body regardless
    of which stories are referenced.

    Arrange:
      - Payload A: story_id=STORY-500, prompt mentions STORY-101
      - Payload B: story_id=STORY-500, prompt mentions STORY-999
    Act:     POST each payload.
    Assert:  Both return 422; error bodies contain the respective mismatching IDs
             (STORY-101 vs STORY-999), confirming per-input variance.
    """
    _inject_dispatch(app, tmp_path)
    payload_a = _payload(
        story_id="STORY-500",
        prompt="Please implement STORY-101 features here.",
    )
    payload_b = _payload(
        story_id="STORY-500",
        prompt="Please implement STORY-999 features here.",
    )

    resp_a = await client.post("/api/dispatch", json=payload_a)
    resp_b = await client.post("/api/dispatch", json=payload_b)

    assert resp_a.status_code == 422, (
        f"Expected 422 for STORY-101 mismatch, got {resp_a.status_code}."
    )
    assert resp_b.status_code == 422, (
        f"Expected 422 for STORY-999 mismatch, got {resp_b.status_code}."
    )
    assert resp_a.text != resp_b.text, (
        "Error bodies must differ between STORY-101 and STORY-999 mismatches "
        "(guards against a constant-error stub)."
    )
    assert "STORY-101" in resp_a.text, "Error A must name STORY-101"
    assert "STORY-999" in resp_b.text, "Error B must name STORY-999"


# ---------------------------------------------------------------------------
# AC-6: no STORY-N token in prompt -> 201 (expected GREEN)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac6_no_story_token_returns_201(client, app, tmp_path):
    """AC-6: Prompt with no STORY-N token at all -> 201 (no false positive).

    Arrange: story_id=STORY-500, prompt contains no STORY-<digits> pattern.
    Act:     POST /api/dispatch.
    Assert:  HTTP 201 (regex returns empty; validator must not fire).
    """
    _inject_dispatch(app, tmp_path)
    payload = _payload(
        story_id="STORY-500",
        prompt="Implement the enqueue validator feature. Start Phase 7.",
    )
    resp = await client.post("/api/dispatch", json=payload)

    assert resp.status_code == 201, (
        f"Expected 201 when prompt contains no STORY-N token, got {resp.status_code}."
    )


# ---------------------------------------------------------------------------
# AC-7: self-reference in prompt -> 201 (expected GREEN)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac7_self_reference_in_prompt_returns_201(client, app, tmp_path):
    """AC-7: Prompt explicitly mentioning the same story_id -> 201 (self-ref allowed).

    Arrange: story_id=STORY-500, prompt mentions STORY-500 multiple times.
    Act:     POST /api/dispatch.
    Assert:  HTTP 201 (same-story reference is not a cross-story mismatch).
    """
    _inject_dispatch(app, tmp_path)
    payload = _payload(
        story_id="STORY-500",
        prompt="STORY-500: implement STORY-500 validator. See STORY-500 spec.",
    )
    resp = await client.post("/api/dispatch", json=payload)

    assert resp.status_code == 201, (
        f"Expected 201 for self-reference (STORY-500 matches story_id), "
        f"got {resp.status_code}."
    )


# ---------------------------------------------------------------------------
# AC-8: multiple mismatching stories in prompt -> 422 listing all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ac8_multiple_mismatches_returns_422_with_all_ids(client, app, tmp_path):
    """AC-8: Prompt referencing two different wrong stories -> 422, error lists all.

    Arrange: story_id=STORY-500, prompt references STORY-111 and STORY-222.
    Act:     POST /api/dispatch.
    Assert:  HTTP 422; error body names both STORY-111 and STORY-222.
    """
    _inject_dispatch(app, tmp_path)
    payload = _payload(
        story_id="STORY-500",
        prompt=(
            "This work covers STORY-111 and STORY-222. "
            "Implement both as part of the same dispatch."
        ),
    )
    resp = await client.post("/api/dispatch", json=payload)

    assert resp.status_code == 422, (
        f"Expected 422 for multiple mismatches (STORY-111, STORY-222), "
        f"got {resp.status_code}."
    )
    body = resp.text
    assert "STORY-111" in body, "Error body must list STORY-111"
    assert "STORY-222" in body, "Error body must list STORY-222"
