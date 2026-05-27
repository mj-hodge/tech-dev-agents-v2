"""STORY-511 follow-up (Mark 2026-04-22): completion gate accepts the bare
``features/story-{N}/`` folder form too, not only ``features/story-{N}-<slug>/``.

The gate's job is "did the agent produce the SDLC deliverables" — not "did
they name the folder with a slug." STORY-521 and STORY-526 both shipped code
but hit 422 because the agent wrote to ``features/story-521/`` and the gate
required ``features/story-521-*/``. Spirit over letter.

This is a mock-only contract test against the route handler's file-list
filtering logic. It doesn't stand up a full FastAPI app or real Postgres —
it reproduces the specific line-range that decides 422 vs 200.
"""

from __future__ import annotations

import pathlib
import re


GATE_SOURCE = pathlib.Path(
    __file__
).resolve().parent.parent.parent / "tech_dev_agents" / "ops_console" / "routes" / "dispatch.py"


def test_gate_accepts_bare_story_folder():
    """Folder ``features/story-521/seed.md`` must pass the gate for a
    scope=small story with story_id=STORY-521 (no slug required)."""
    source = GATE_SOURCE.read_text()
    # The relaxed gate should filter paths that start with EITHER the
    # slug-prefix OR the bare-prefix. Look for both prefixes in the code.
    assert 'slug_prefix = f"features/story-{story_num}-"' in source, (
        "slug_prefix missing from completion gate — rename back or update test"
    )
    assert 'bare_prefix = f"features/story-{story_num}/"' in source, (
        "gate no longer accepts the bare features/story-{N}/ form — STORY-521/526 "
        "will start failing 422 again on legitimate completions"
    )
    # The list-comprehension must test both prefixes.
    # Flatten whitespace for the assertion.
    flat = re.sub(r"\s+", " ", source)
    assert "p.startswith(slug_prefix) or p.startswith(bare_prefix)" in flat, (
        "gate filter only checks one prefix — the relaxation regressed"
    )


def test_gate_error_message_mentions_both_forms():
    """The 422 detail string should tell the caller EITHER form is OK, so
    an agent reading the error message knows they aren't required to use
    the slug form (reduces false-retry churn when the error is surfaced
    back into the retry prompt)."""
    source = GATE_SOURCE.read_text()
    flat = re.sub(r"\s+", " ", source)
    assert "Required in features/story-{story_num}/" in flat, (
        "422 error does not mention the bare folder form — agent reading "
        "the error will think slug form is mandatory"
    )
