"""tech_dev_agents.morris.canon_check — STORY-1005.

Cross-repo canon enforcement for `*-v2` pipeline repos.

Public surface:
  - `checker.check_pr_for_drift(repo, pr_number, ...)` — pure-function diff
  - `checker.CanonCheckResult` / `DriftedFile` / `WatchedFile` — dataclasses
  - `checker.STATUS_CHECK_NAME` — `"morris/canon-check"` (CONTRACT, STORY-1007 G7)
  - `commenter.render_comment(result)` / `post_drift_comment(result)`
  - `commenter.update_status_check(repo, sha, state, description)`
  - `commenter.COMMENT_MARKER` — `<!-- morris-canon-check:v1 -->` (idempotency token)

The skill `deployment/vm/skills/morris/canon-check/SKILL.md` is the
Morris-facing entry point; this package is its Python implementation.
"""

from tech_dev_agents.morris.canon_check.checker import (
    STATUS_CHECK_NAME,
    WATCHED_FILES,
    CanonCheckResult,
    DriftedFile,
    WatchedFile,
    check_pr_for_drift,
    is_v2_repo,
    load_pinned_ref,
)
from tech_dev_agents.morris.canon_check.commenter import (
    COMMENT_MARKER,
    post_drift_comment,
    render_comment,
    update_status_check,
)

__all__ = [
    "STATUS_CHECK_NAME",
    "WATCHED_FILES",
    "CanonCheckResult",
    "COMMENT_MARKER",
    "DriftedFile",
    "WatchedFile",
    "check_pr_for_drift",
    "is_v2_repo",
    "load_pinned_ref",
    "post_drift_comment",
    "render_comment",
    "update_status_check",
]
