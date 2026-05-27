"""Adversarial review gate — STORY-723.

Spawns a Sonnet subagent to review test coverage quality after Phase 8.
Parses the structured output and enforces gating policy:
  - BLOCK (any CRITICAL finding): re-opens Phase 8, dispatches a fix task
  - APPROVE_WITH_CAVEATS (HIGH/MEDIUM only): advances to Done, logs findings
  - APPROVE: advances to Done silently

Integration: called from dispatch_poller.start_story after run_sdlc_phases
returns success=True, before _report_complete, for scope ∈ (medium, large, new).

Skip scopes: small, trivial (no Phase 6 spec to review against).
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import time

_REVIEWER_SCOPES = ("medium", "large", "new")
_REVIEW_FILENAME = "adversarial-review.md"

_PHASE_INPUTS = {
    "feature-spec.md": True,
    "specification.md": True,
    "architecture.md": True,
    "implementation-plan.md": True,
}

_REVIEWER_PROMPT_TEMPLATE = """\
You are a skeptical senior software engineer doing an adversarial review of a \
Phase-8 implementation for {story_id}. Your goal is to find gaps that passing \
tests cannot detect.

## Story folder: features/{story_folder}/

## Spec file(s) to review against:
{spec_content}

## Git diff (implementation changes):
{diff_content}

## Test file contents:
{test_content}

---

For each of the following checks, produce concrete findings:

1. **Spec completeness**: Enumerate every concrete requirement in the spec \
(numbered lists, tables, "must"/"shall" language). For each, locate the \
implementing code. Flag requirements with NO implementation as CRITICAL.

2. **Behavioral vs static tests**: Classify each test as Behavioral \
(instantiates SUT, calls with inputs, asserts on outputs/mock calls/state) \
or Static (imports module, regex-matches source, checks symbol existence). \
Static tests covering DB writes / API calls / state transitions are CRITICAL.

3. **Fixture realism**: For synthetic IDs/hashes in tests, check if the \
production code could generate them. Unreachable fixtures are HIGH \
(CRITICAL if sole guard for a behavior).

4. **Exception-path coverage**: Every raise/except in the implementation \
needs at least one test that traverses it. Missing = HIGH.

5. **Idempotency**: If spec says "happens once" (dedup, single-write), the \
second-invocation path must be tested with realistic state, not a synthetic \
fixture. Missing = HIGH to CRITICAL.

## Output format (STRICT — do not deviate):

# Adversarial Review: {story_id}

## Verdict
APPROVE | APPROVE_WITH_CAVEATS | BLOCK

## Findings

### CRITICAL
- [C-1] <one-line summary>
  - Location: <file:line or spec-section>
  - Evidence: <quote>
  - Required fix: <action>

### HIGH
- [H-1] ...

### MEDIUM
- [M-1] ...

### LOW
- [L-1] ...

## Coverage Matrix
| Spec Requirement | Implementing Code | Test(s) | Test Type |
|---|---|---|---|
| ... | ... | ... | behavioral / static / missing |

Rules:
- Use BLOCK if any CRITICAL finding exists.
- Use APPROVE_WITH_CAVEATS if HIGH findings exist but no CRITICAL.
- Use APPROVE only if zero CRITICAL and zero HIGH findings.
- If a section has no findings, write "None."
"""


def parse_adversarial_review(content: str) -> dict:
    """Parse adversarial-review.md output into a structured result dict.

    Returns:
        verdict: APPROVE | APPROVE_WITH_CAVEATS | BLOCK | UNKNOWN
        critical_count, high_count, medium_count, low_count: int
        findings_by_severity: dict[str, list[str]]
        should_merge: bool (True unless BLOCK)
        dispatch_fix_task: bool (True only on BLOCK)
    """
    verdict = None
    # Look for verdict line immediately after "## Verdict" header
    lines = content.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "## Verdict" and i + 1 < len(lines):
            candidate = lines[i + 1].strip()
            if candidate in ("APPROVE", "APPROVE_WITH_CAVEATS", "BLOCK"):
                verdict = candidate
                break

    counts: dict[str, int] = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    findings: dict[str, list[str]] = {"CRITICAL": [], "HIGH": [], "MEDIUM": [], "LOW": []}
    current_severity: str | None = None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("### "):
            sev = stripped[4:].strip()
            current_severity = sev if sev in counts else None
        elif current_severity and re.match(r"^- \[", stripped):
            counts[current_severity] += 1
            findings[current_severity].append(stripped)

    verdict = verdict or "UNKNOWN"
    should_merge = verdict in ("APPROVE", "APPROVE_WITH_CAVEATS")
    dispatch_fix_task = verdict == "BLOCK"

    return {
        "verdict": verdict,
        "critical_count": counts["CRITICAL"],
        "high_count": counts["HIGH"],
        "medium_count": counts["MEDIUM"],
        "low_count": counts["LOW"],
        "findings_by_severity": findings,
        "should_merge": should_merge,
        "dispatch_fix_task": dispatch_fix_task,
    }


def _read_spec(story_folder: str, workdir: str) -> str:
    """Read the first available spec file from the story folder."""
    for filename in ("feature-spec.md", "specification.md", "architecture.md"):
        path = os.path.join(workdir, "features", story_folder, filename)
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return f"### {filename}\n\n" + f.read()
            except OSError:
                pass
    return "(no spec file found — Phase 6 may not have run)"


def _get_branch_diff(workdir: str) -> str:
    """Get the git diff of HEAD vs main for the current branch."""
    try:
        result = subprocess.run(
            ["git", "diff", "origin/main...HEAD", "--stat", "--unified=3"],
            capture_output=True, text=True, cwd=workdir, timeout=30,
        )
        diff = result.stdout.strip()
        if len(diff) > 12000:
            diff = diff[:12000] + "\n\n[...diff truncated at 12000 chars]"
        return diff or "(empty diff)"
    except Exception as exc:
        return f"(could not get diff: {exc})"


def _read_test_files(story_folder: str, workdir: str) -> str:
    """Find and read test files related to the story."""
    story_num = story_folder.split("-")[1] if "-" in story_folder else ""
    test_dir = os.path.join(workdir, "tests")
    chunks = []
    try:
        for root, _, files in os.walk(test_dir):
            for fname in sorted(files):
                if fname.endswith(".py") and story_num and story_num in fname:
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath) as f:
                            content = f.read()
                        rel = os.path.relpath(fpath, workdir)
                        chunks.append(f"### {rel}\n\n```python\n{content[:6000]}\n```")
                    except OSError:
                        pass
    except Exception:
        pass
    return "\n\n".join(chunks) or "(no story-specific test files found)"


def build_reviewer_prompt(story_id: str, story_folder: str, workdir: str) -> str:
    """Assemble the reviewer prompt with spec, diff, and test content."""
    spec_content = _read_spec(story_folder, workdir)
    diff_content = _get_branch_diff(workdir)
    test_content = _read_test_files(story_folder, workdir)

    return _REVIEWER_PROMPT_TEMPLATE.format(
        story_id=story_id,
        story_folder=story_folder,
        spec_content=spec_content,
        diff_content=diff_content,
        test_content=test_content,
    )


def _call_anthropic_api(prompt: str) -> str:
    """Call Anthropic Messages API with the review prompt. Returns response text."""
    import requests as _requests

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "## Verdict\nAPPROVE\n\n## Findings\n\n### CRITICAL\nNone.\n\n### HIGH\nNone.\n\n## Coverage Matrix\n| (skipped — no ANTHROPIC_API_KEY) |\n"

    payload = {
        "model": "claude-sonnet-4-5-20251001",
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    try:
        resp = _requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]
    except Exception as exc:
        logging.warning("adversarial_reviewer: API call failed: %s", exc)
        return f"## Verdict\nAPPROVE_WITH_CAVEATS\n\n## Findings\n\n### HIGH\n- [H-1] Review skipped due to API error: {exc}\n\n### CRITICAL\nNone.\n\n## Coverage Matrix\n| (review incomplete) |\n"


def _get_current_sha(workdir: str) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, cwd=workdir, timeout=10,
        )
        return result.stdout.strip()[:12]
    except Exception:
        return "unknown"


def run_adversarial_review(
    story_id: str,
    story_folder: str,
    scope: str,
    workdir: str,
) -> dict:
    """Run adversarial review gate. Returns parsed result dict.

    Skips for small/trivial scope (no Phase 6 spec to review against).
    Idempotent: returns cached result if adversarial-review.md already
    contains the current commit SHA.
    """
    if scope not in _REVIEWER_SCOPES:
        print(f"[REVIEW] {story_id} scope={scope} — adversarial review skipped (small/trivial)", flush=True)
        return {"verdict": "SKIP", "should_merge": True, "dispatch_fix_task": False,
                "critical_count": 0, "high_count": 0, "medium_count": 0, "low_count": 0,
                "findings_by_severity": {}}

    review_path = os.path.join(workdir, "features", story_folder, _REVIEW_FILENAME)
    current_sha = _get_current_sha(workdir)

    # Idempotency: if review exists for this SHA, return cached
    if os.path.exists(review_path):
        try:
            with open(review_path) as f:
                cached = f.read()
            if current_sha != "unknown" and f"sha:{current_sha}" in cached:
                print(f"[REVIEW] {story_id} returning cached adversarial review (sha:{current_sha})", flush=True)
                return parse_adversarial_review(cached)
        except OSError:
            pass

    print(f"[REVIEW] {story_id} running adversarial review (sha:{current_sha})", flush=True)
    start = time.time()

    prompt = build_reviewer_prompt(story_id, story_folder, workdir)
    raw_output = _call_anthropic_api(prompt)

    # Prepend SHA header for idempotency on future runs
    output_with_header = f"<!-- sha:{current_sha} -->\n{raw_output}"

    # H-3: Atomic write — write to a temp file in the same directory, then
    # rename. This prevents concurrent reads from seeing a partial file
    # (e.g., SHA header written but verdict not yet flushed).
    try:
        dest_dir = os.path.dirname(review_path)
        os.makedirs(dest_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=dest_dir, prefix=".review-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(output_with_header)
            os.replace(tmp_path, review_path)  # atomic on POSIX
        except BaseException:
            # Clean up temp file on any failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        print(f"[REVIEW] wrote {review_path}", flush=True)
    except OSError as exc:
        logging.warning("adversarial_reviewer: could not write review file: %s", exc)

    result = parse_adversarial_review(raw_output)
    elapsed = int(time.time() - start)
    print(
        f"[REVIEW] {story_id} verdict={result['verdict']} "
        f"C={result['critical_count']} H={result['high_count']} "
        f"M={result['medium_count']} ({elapsed}s)",
        flush=True,
    )
    return result
