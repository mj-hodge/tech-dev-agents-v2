"""Tests for tech_dev_agents/morris/canon_check — STORY-1005 Phase 7 (RED).

Suite covers:
  - Repo detection (`*-v2` + `api-advertising-amazon`)
  - check_pr_for_drift core behaviour across 7 scenarios
  - Comment body rendering (marker + diff + clean state)
  - Idempotent commenter + status-check contract name
  - Pin resolution (env override + fallback)

All I/O is dependency-injected — no network, no `gh` CLI, no live filesystem
mutations outside `tmp_path` / `monkeypatch`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure repo root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tech_dev_agents.morris.canon_check.checker import (  # noqa: E402
    STATUS_CHECK_NAME,
    WATCHED_FILES,
    CanonCheckResult,
    DriftedFile,
    WatchedFile,
    check_pr_for_drift,
    is_v2_repo,
    load_pinned_ref,
)
from tech_dev_agents.morris.canon_check.commenter import (  # noqa: E402
    COMMENT_MARKER,
    post_drift_comment,
    render_comment,
    update_status_check,
)

# ---------------------------------------------------------------------------
# Fixtures — canonical scaffold bytes that match the real pipeline-template
# ---------------------------------------------------------------------------

_GOOD_PR_TEMPLATE = b"""## Summary
<!-- 1-3 bullets -->

## Continuous-improvement checklist

### 1. Canon-doc impact
### 2. Scaffold backport
### 3. Sibling-pipeline sweep
"""

_GOOD_DRIFT_CHECK_WF = b"""name: canon-drift-check
on:
  pull_request:
    branches: [main]
jobs:
  drift-check:
    runs-on: ubuntu-latest
"""

_GOOD_READBACK_WF = b"""name: pr-canon-readback
on:
  pull_request:
"""

_GOOD_DEPLOY_WF_WITH_PIN = b"""name: deploy
jobs:
  deploy:
    steps:
      - run: uv pip install --python-platform x86_64-manylinux_2_17 -r requirements.txt
"""

_DEPLOY_WF_NO_PIN = b"""name: deploy
jobs:
  deploy:
    steps:
      - run: pip install -r requirements.txt
"""

_TAMPERED_PR_TEMPLATE = b"""## Summary
<!-- 1-3 bullets -->

(Continuous-improvement checklist deleted by repo author)
"""


def _good_scaffold() -> dict[str, bytes]:
    """Map of watched-file path -> canonical scaffold bytes (all clean)."""
    return {
        ".github/pull_request_template.md": _GOOD_PR_TEMPLATE,
        ".github/workflows/canon-drift-check.yml": _GOOD_DRIFT_CHECK_WF,
        ".github/workflows/pr-canon-readback.yml": _GOOD_READBACK_WF,
        ".github/workflows/deploy-function-app.yaml": _GOOD_DEPLOY_WF_WITH_PIN,
    }


def _make_fetchers(
    scaffold_files: dict[str, bytes],
    pr_files: dict[str, bytes],
    *,
    record: list[tuple[str, str]] | None = None,
):
    """Build (scaffold_fetcher, pr_file_fetcher) backed by in-memory dicts.

    If `record` is supplied, every fetcher invocation appends a tuple
    ('scaffold' | 'pr', url-or-path) — used to assert URL composition.
    """
    def _scaffold_fetcher(url: str) -> bytes | None:
        if record is not None:
            record.append(("scaffold", url))
        # URL ends with "/pipeline-template/<path>" — slice off the prefix.
        marker = "/pipeline-template/"
        idx = url.find(marker)
        if idx < 0:
            return None
        path = url[idx + len(marker):]
        return scaffold_files.get(path)

    def _pr_fetcher(repo: str, pr_number: int, head_sha: str, path: str) -> bytes | None:
        if record is not None:
            record.append(("pr", path))
        return pr_files.get(path)

    return _scaffold_fetcher, _pr_fetcher


# ---------------------------------------------------------------------------
# Suite A — Repo detection
# ---------------------------------------------------------------------------


def test_is_v2_repo_detects_v2_suffix():
    assert is_v2_repo("hpi-gorillacommerce/walmart-supplier-v2") is True
    assert is_v2_repo("hpi-gorillacommerce/spapi-reports-v2") is True
    assert is_v2_repo("hpi-gorillacommerce/levanta-v2") is True
    assert is_v2_repo("hpi-gorillacommerce/tech-dev-agents") is False
    assert is_v2_repo("hpi-gorillacommerce/gc-data") is False
    assert is_v2_repo("hpi-gorillacommerce/some-v2-mid-name") is False


def test_is_v2_repo_includes_api_advertising_amazon():
    # Per pull_request_template.md sibling list — non-v2-suffix watched repo
    assert is_v2_repo("hpi-gorillacommerce/api-advertising-amazon") is True


# ---------------------------------------------------------------------------
# Suite B — check_pr_for_drift core
# ---------------------------------------------------------------------------


def test_check_pr_for_drift_non_v2_returns_NA():
    record: list = []
    sf, pf = _make_fetchers({}, {}, record=record)
    result = check_pr_for_drift(
        "hpi-gorillacommerce/tech-dev-agents",
        42,
        scaffold_fetcher=sf,
        pr_file_fetcher=pf,
    )
    assert result.status == "NA"
    assert result.drifted == ()
    assert result.skipped_reason == "non_v2_repo"
    # No fetcher calls — we should bail before any I/O
    assert record == []


def test_check_pr_for_drift_matching_scaffold_returns_SUCCESS():
    scaffold = _good_scaffold()
    pr_files = dict(scaffold)  # identical bytes
    sf, pf = _make_fetchers(scaffold, pr_files)
    result = check_pr_for_drift(
        "hpi-gorillacommerce/walmart-supplier-v2",
        7,
        head_sha="abc123",
        scaffold_fetcher=sf,
        pr_file_fetcher=pf,
    )
    assert result.status == "SUCCESS"
    assert result.drifted == ()
    assert result.scaffold_ref == "main"


def test_check_pr_for_drift_modified_pr_template_returns_FAILURE():
    scaffold = _good_scaffold()
    pr_files = dict(scaffold)
    pr_files[".github/pull_request_template.md"] = _TAMPERED_PR_TEMPLATE
    sf, pf = _make_fetchers(scaffold, pr_files)
    result = check_pr_for_drift(
        "hpi-gorillacommerce/walmart-supplier-v2",
        7,
        head_sha="abc123",
        scaffold_fetcher=sf,
        pr_file_fetcher=pf,
    )
    assert result.status == "FAILURE"
    drifted_paths = [d.path for d in result.drifted]
    assert ".github/pull_request_template.md" in drifted_paths
    drift = next(d for d in result.drifted if d.path == ".github/pull_request_template.md")
    assert drift.drift_type == "MODIFIED"
    assert drift.diff_snippet is not None
    # Diff should mention the deleted checklist heading
    assert "Continuous-improvement" in drift.diff_snippet or "checklist" in drift.diff_snippet
    # Remediation should be a copy-paste curl command pointed at the canonical URL
    assert "curl" in drift.remediation
    assert "pull_request_template.md" in drift.remediation


def test_check_pr_for_drift_missing_required_file_returns_FAILURE():
    scaffold = _good_scaffold()
    pr_files = dict(scaffold)
    pr_files.pop(".github/workflows/canon-drift-check.yml")
    sf, pf = _make_fetchers(scaffold, pr_files)
    result = check_pr_for_drift(
        "hpi-gorillacommerce/walmart-supplier-v2",
        9,
        head_sha="def456",
        scaffold_fetcher=sf,
        pr_file_fetcher=pf,
    )
    assert result.status == "FAILURE"
    drift = next(
        d for d in result.drifted if d.path == ".github/workflows/canon-drift-check.yml"
    )
    assert drift.drift_type == "MISSING"
    assert "curl" in drift.remediation


def test_check_pr_for_drift_missing_glibc_pin_returns_FAILURE():
    scaffold = _good_scaffold()
    pr_files = dict(scaffold)
    pr_files[".github/workflows/deploy-function-app.yaml"] = _DEPLOY_WF_NO_PIN
    sf, pf = _make_fetchers(scaffold, pr_files)
    result = check_pr_for_drift(
        "hpi-gorillacommerce/walmart-supplier-v2",
        11,
        head_sha="cafef00d",
        scaffold_fetcher=sf,
        pr_file_fetcher=pf,
    )
    assert result.status == "FAILURE"
    drift = next(
        d for d in result.drifted if d.path == ".github/workflows/deploy-function-app.yaml"
    )
    assert drift.drift_type == "ASSERTION_FAILED"
    assert "manylinux_2_17" in drift.remediation


def test_check_pr_for_drift_scaffold_404_required_file_is_failure():
    """If scaffold returns None for a required file, it's drift, not 'clean'."""
    scaffold = _good_scaffold()
    # Simulate upstream 404 by removing one entry from scaffold dict
    scaffold.pop(".github/pull_request_template.md")
    pr_files = {
        ".github/pull_request_template.md": _GOOD_PR_TEMPLATE,
        ".github/workflows/canon-drift-check.yml": _GOOD_DRIFT_CHECK_WF,
        ".github/workflows/pr-canon-readback.yml": _GOOD_READBACK_WF,
        ".github/workflows/deploy-function-app.yaml": _GOOD_DEPLOY_WF_WITH_PIN,
    }
    sf, pf = _make_fetchers(scaffold, pr_files)
    result = check_pr_for_drift(
        "hpi-gorillacommerce/walmart-supplier-v2",
        13,
        head_sha="beefbeef",
        scaffold_fetcher=sf,
        pr_file_fetcher=pf,
    )
    assert result.status == "FAILURE"
    drift = next(
        d for d in result.drifted if d.path == ".github/pull_request_template.md"
    )
    assert drift.drift_type == "ASSERTION_FAILED"
    assert "scaffold" in drift.remediation.lower() or "404" in drift.remediation


def test_check_pr_for_drift_pin_sha_honored():
    scaffold = _good_scaffold()
    pr_files = dict(scaffold)
    record: list = []
    sf, pf = _make_fetchers(scaffold, pr_files, record=record)
    pinned = "deadbeefcafe1234567890abcdef"
    result = check_pr_for_drift(
        "hpi-gorillacommerce/walmart-supplier-v2",
        17,
        head_sha="aaa",
        scaffold_ref=pinned,
        scaffold_fetcher=sf,
        pr_file_fetcher=pf,
    )
    assert result.status == "SUCCESS"
    assert result.scaffold_ref == pinned
    # Every scaffold-side fetch URL must include the pinned sha
    scaffold_calls = [u for kind, u in record if kind == "scaffold"]
    assert scaffold_calls, "expected at least one scaffold fetch"
    for url in scaffold_calls:
        assert pinned in url, f"pinned sha missing from URL: {url}"


# ---------------------------------------------------------------------------
# Suite C — Comment rendering
# ---------------------------------------------------------------------------


def test_render_comment_includes_marker_and_diff():
    drifted = DriftedFile(
        path=".github/pull_request_template.md",
        drift_type="MODIFIED",
        scaffold_url="https://raw.githubusercontent.com/hpi-gorillacommerce/gc-data-v2/main/pipeline-template/.github/pull_request_template.md",
        diff_snippet="--- a/pr_template\n+++ b/pr_template\n@@ -3,5 +3,3 @@\n-deleted line",
        remediation="curl -fsSL https://raw.githubusercontent.com/.../pull_request_template.md -o .github/pull_request_template.md",
    )
    result = CanonCheckResult(
        repo="hpi-gorillacommerce/walmart-supplier-v2",
        pr_number=42,
        head_sha="abc123",
        scaffold_ref="main",
        drifted=(drifted,),
        status="FAILURE",
    )
    body = render_comment(result, now_iso="2026-05-18T12:00:00Z")
    # First line must be the marker (idempotency contract)
    assert body.splitlines()[0].strip() == COMMENT_MARKER
    assert "DRIFT DETECTED" in body
    assert ".github/pull_request_template.md" in body
    assert "deleted line" in body  # diff snippet present
    assert "curl" in body
    assert "2026-05-18T12:00:00Z" in body


def test_render_comment_clean_state():
    result = CanonCheckResult(
        repo="hpi-gorillacommerce/walmart-supplier-v2",
        pr_number=42,
        head_sha="abc123",
        scaffold_ref="main",
        drifted=(),
        status="SUCCESS",
    )
    body = render_comment(result, now_iso="2026-05-18T12:00:00Z")
    assert body.splitlines()[0].strip() == COMMENT_MARKER
    assert "CLEAN" in body.upper() or "no drift" in body.lower()


# ---------------------------------------------------------------------------
# Suite D — Commenter + status check
# ---------------------------------------------------------------------------


class _FakeRunner:
    """Records every argv invocation and returns canned (rc, stdout, stderr)."""

    def __init__(self):
        self.calls: list[list[str]] = []
        # Map of argv-tuple-prefix -> (rc, stdout, stderr); falls back to default.
        self.responses: dict[tuple[str, ...], tuple[int, str, str]] = {}
        self.default = (0, "", "")

    def respond(self, argv_prefix: tuple[str, ...], rc: int, stdout: str, stderr: str = ""):
        self.responses[argv_prefix] = (rc, stdout, stderr)

    def run(self, argv: list[str]) -> tuple[int, str, str]:
        self.calls.append(list(argv))
        for prefix, resp in self.responses.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return resp
        return self.default


def _failure_result() -> CanonCheckResult:
    drifted = DriftedFile(
        path=".github/pull_request_template.md",
        drift_type="MODIFIED",
        scaffold_url="https://example/pipeline-template/.github/pull_request_template.md",
        diff_snippet="--- a\n+++ b\n@@\n-x\n+y",
        remediation="curl -fsSL https://example/pipeline-template/.github/pull_request_template.md -o .github/pull_request_template.md",
    )
    return CanonCheckResult(
        repo="hpi-gorillacommerce/walmart-supplier-v2",
        pr_number=42,
        head_sha="abc123",
        scaffold_ref="main",
        drifted=(drifted,),
        status="FAILURE",
    )


def test_post_drift_comment_idempotent_updates_existing():
    runner = _FakeRunner()
    # First call: no existing comment → POST new
    runner.respond(
        ("gh", "pr", "view"),
        rc=0,
        stdout=json.dumps({"comments": []}),
    )
    result = _failure_result()
    first = post_drift_comment(result, runner=runner)
    assert first["action"] in ("posted", "created")

    # Reset for second call: existing morris comment is present
    runner2 = _FakeRunner()
    existing = {
        "comments": [
            {
                "id": 999888,
                "author": {"login": "morris-bot"},
                "body": f"{COMMENT_MARKER}\n## Morris canon-check — DRIFT DETECTED\nstale",
            }
        ]
    }
    runner2.respond(("gh", "pr", "view"), rc=0, stdout=json.dumps(existing))
    second = post_drift_comment(result, runner=runner2)
    assert second["action"] == "updated"
    # Must have called the PATCH endpoint with the existing comment id
    patch_calls = [c for c in runner2.calls if "PATCH" in c or "-X" in c and "PATCH" in c]
    assert any(
        "999888" in " ".join(c) for c in runner2.calls
    ), f"expected comment id 999888 in some call; got {runner2.calls}"


def test_post_drift_comment_skips_when_status_NA():
    runner = _FakeRunner()
    result = CanonCheckResult(
        repo="hpi-gorillacommerce/tech-dev-agents",
        pr_number=1,
        head_sha=None,
        scaffold_ref="main",
        drifted=(),
        status="NA",
        skipped_reason="non_v2_repo",
    )
    out = post_drift_comment(result, runner=runner)
    assert out["action"] == "skipped"
    assert runner.calls == []  # zero shell-outs


def test_update_status_check_uses_correct_context_name():
    runner = _FakeRunner()
    update_status_check(
        repo="hpi-gorillacommerce/walmart-supplier-v2",
        sha="abc123",
        state="failure",
        description="3 files drift",
        runner=runner,
    )
    # Status check name is a CONTRACT — STORY-1007 G7 greps for this exact name.
    flattened = " ".join(c for call in runner.calls for c in call)
    assert STATUS_CHECK_NAME == "morris/canon-check"
    assert "morris/canon-check" in flattened
    # Sha must be in the endpoint path
    assert "abc123" in flattened


# ---------------------------------------------------------------------------
# Suite E — Pin resolution
# ---------------------------------------------------------------------------


def test_load_pinned_ref_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MORRIS_CANON_PIN", "feedface1234")
    # File exists but env should win
    pin_file = tmp_path / "canon-pins.yaml"
    pin_file.write_text("gc_data_v2: shouldNotWin\n")
    assert load_pinned_ref(str(pin_file)) == "feedface1234"


def test_load_pinned_ref_falls_back_to_main(monkeypatch, tmp_path):
    monkeypatch.delenv("MORRIS_CANON_PIN", raising=False)
    missing_pin = tmp_path / "does-not-exist.yaml"
    assert load_pinned_ref(str(missing_pin)) == "main"
