# STORY-1005 — Morris `canon-check` skill — Feature Spec (Phase 6)

**Story:** STORY-1005
**Epic:** STORY-1000 (Cross-Repo Canon Alignment)
**Phase:** 6 (Design)
**Date:** 2026-05-18
**Status:** Locked

---

## 1. Goal recap

Cross-repo canon enforcement: when Morris reviews a PR in any `*-v2`
pipeline repo, byte-diff a fixed set of canonical files against
`gc-data-v2/pipeline-template/main` (or a pinned sha) and:

1. Post a structured PR comment naming each drifted file with a diff snippet
   and a `curl -fsSL` remediation command.
2. Set a required commit-status check named **`morris/canon-check`** to
   `FAILURE` (or `SUCCESS` when clean, `PENDING` while running).
3. Be idempotent — re-running on the same PR must update the existing
   Morris comment rather than stacking duplicates.

This story produces the skill **and** its pure-Python checker module.
Wiring into `review-prs/SKILL.md` step ordering is STORY-1007's job; we
expose a stable public API and a stable status-check name so STORY-1007
can consume them without further refactor.

---

## 2. Module layout

```
deployment/vm/skills/morris/canon-check/
└── SKILL.md                          # Morris-facing skill doc, frontmatter

tech_dev_agents/morris/canon_check/
├── __init__.py                       # re-exports the public surface
├── checker.py                        # public: check_pr_for_drift() + dataclasses
└── commenter.py                      # public: post_drift_comment() + update_status_check()

tests/morris/
└── test_canon_check.py               # unit tests (RED → GREEN)
```

**Why `checker.py`** (not `differ.py` as the seed suggested): the operating
prompt instructed `checker.py`. We honor the operating prompt; the seed
is informative on naming, the prompt is authoritative. The public function
`check_pr_for_drift` is unchanged, so consumers don't care.

**Tests live in `tests/morris/test_canon_check.py`** (not
`tech_dev_agents/morris/canon_check/tests/`) per the operating prompt and
the project's actual test layout (every other Morris test lives under
`tests/morris/`).

---

## 3. Public API

### 3.1 `tech_dev_agents.morris.canon_check.checker`

```python
from dataclasses import dataclass, field
from typing import Literal, Callable

DriftType = Literal["MODIFIED", "MISSING", "ASSERTION_FAILED"]
CheckStatus = Literal["SUCCESS", "FAILURE", "PENDING", "NA"]

V2_REPO_PATTERN = "*-v2"            # fnmatch pattern against repo.name
EXTRA_WATCHED_REPOS = frozenset({"api-advertising-amazon"})  # per PR template

@dataclass(frozen=True)
class WatchedFile:
    path: str                         # e.g. ".github/pull_request_template.md"
    mode: Literal["byte_match", "content_assert"]
    # For content_assert mode: substring that must appear in the PR file.
    assert_substring: str | None = None
    # If True and PR is missing the file but scaffold has it: drift.
    # If False: scaffold-only file (e.g. optional auth/* sample) — missing is fine.
    required: bool = True

WATCHED_FILES: tuple[WatchedFile, ...] = (
    WatchedFile(".github/pull_request_template.md", "byte_match"),
    WatchedFile(".github/workflows/canon-drift-check.yml", "byte_match"),
    WatchedFile(".github/workflows/pr-canon-readback.yml", "byte_match"),
    WatchedFile(
        ".github/workflows/deploy-function-app.yaml",
        "content_assert",
        assert_substring="--python-platform x86_64-manylinux_2_17",
        required=False,        # stub repos may not have it yet
    ),
)

STATUS_CHECK_NAME = "morris/canon-check"   # CONTRACT — referenced by STORY-1007 G7

@dataclass(frozen=True)
class DriftedFile:
    path: str
    drift_type: DriftType
    scaffold_url: str
    diff_snippet: str | None       # `diff -u` output, truncated to 40 lines
    remediation: str               # ready-to-paste curl command or assertion msg

@dataclass(frozen=True)
class CanonCheckResult:
    repo: str                      # full_name, e.g. "hpi-gorillacommerce/walmart-supplier-v2"
    pr_number: int
    head_sha: str | None
    scaffold_ref: str              # "main" or pinned sha
    drifted: tuple[DriftedFile, ...]
    status: CheckStatus            # NA when repo not in watch set
    skipped_reason: str | None = None   # populated when status == "NA"

def is_v2_repo(repo_full_name: str) -> bool: ...
def load_pinned_ref(pin_path: str | None = None) -> str: ...
def fetch_scaffold_file(ref: str, path: str, fetcher: Callable[[str], bytes | None] | None = None) -> bytes | None: ...
def fetch_pr_file(repo: str, pr_number: int, head_sha: str, path: str, fetcher: Callable[[str, str, str, str], bytes | None] | None = None) -> bytes | None: ...
def check_pr_for_drift(
    repo: str,
    pr_number: int,
    head_sha: str | None = None,
    scaffold_ref: str = "main",
    *,
    scaffold_fetcher: Callable[[str], bytes | None] | None = None,
    pr_file_fetcher: Callable[[str, str, str, str], bytes | None] | None = None,
    watched: tuple[WatchedFile, ...] = WATCHED_FILES,
) -> CanonCheckResult: ...
```

**Dependency injection:** `scaffold_fetcher` and `pr_file_fetcher` default
to real HTTP fetchers wrapping `urllib`. Tests inject in-memory dicts so
they need no network or `gh` CLI to be present.

### 3.2 `tech_dev_agents.morris.canon_check.commenter`

```python
COMMENT_MARKER = "<!-- morris-canon-check:v1 -->"     # idempotency token

def render_comment(result: CanonCheckResult, *, now_iso: str | None = None) -> str: ...
def find_existing_comment(repo: str, pr_number: int, runner: Runner | None = None) -> int | None: ...
def post_drift_comment(result: CanonCheckResult, *, runner: Runner | None = None) -> dict: ...
def update_status_check(repo: str, sha: str, state: str, description: str, *, runner: Runner | None = None) -> dict: ...
```

`Runner` is a tiny protocol (`def run(argv: list[str]) -> tuple[int, str, str]`)
that wraps `subprocess.run`. Tests pass a fake runner; production code
defaults to the real one. This keeps the unit tests pure-Python with no
`gh` binary required.

---

## 4. Repo detection logic

```python
def is_v2_repo(repo_full_name: str) -> bool:
    # repo_full_name = "owner/name"
    name = repo_full_name.split("/", 1)[-1]
    return fnmatch.fnmatch(name, V2_REPO_PATTERN) or name in EXTRA_WATCHED_REPOS
```

- `hpi-gorillacommerce/walmart-supplier-v2` → True (`*-v2` match)
- `hpi-gorillacommerce/api-advertising-amazon` → True (extra set)
- `hpi-gorillacommerce/tech-dev-agents` → False
- `hpi-gorillacommerce/gc-data-v2` → True (matches `*-v2`); harmless — it's
  the source of truth and won't drift from itself

---

## 5. Scaffold-vs-PR comparison algorithm

For each `WatchedFile w`:

1. `scaffold_bytes = fetch_scaffold_file(ref, w.path)`
   - If `None` (404 / network fail) and `w.required` is True: classify
     **`SKIPPED_UPSTREAM_404`** — log + needs_info escalation. Don't post a
     false "clean" verdict.
   - If `None` and `w.required` is False: silently skip this file.
2. `pr_bytes = fetch_pr_file(repo, pr_number, head_sha, w.path)`
   - If `None`: file missing in PR.
     - `byte_match` mode: emit `DriftedFile(drift_type="MISSING")`.
     - `content_assert` mode + required=False: skip (e.g. stub repos).
     - `content_assert` mode + required=True: `DriftedFile(MISSING)`.
3. Compare:
   - `byte_match`: `pr_bytes == scaffold_bytes`? If not → unified diff
     truncated to 40 lines → `DriftedFile(drift_type="MODIFIED")`.
   - `content_assert`: `w.assert_substring.encode() in pr_bytes`? If not →
     `DriftedFile(drift_type="ASSERTION_FAILED")` with an explanatory
     `remediation` pointing at `gc-data-v2/platform/failure-modes.md`.

Result status:
- `NA` → repo not in watch set (return early; nothing posted)
- `FAILURE` → ≥1 `DriftedFile`
- `SUCCESS` → empty drifted list AND no upstream-404 skips of required files
- `PENDING` → reserved for callers that want to set status before running
  the comparison (not produced by `check_pr_for_drift` itself)

---

## 6. Idempotency strategy

- Every Morris comment posted by this skill **must** include the
  literal HTML marker `<!-- morris-canon-check:v1 -->` on its first
  line. The marker is the durable identifier — comment body changes
  across runs, but the marker is stable.
- `find_existing_comment(repo, pr)` lists PR comments via
  `gh pr view --json comments`, filters by `author.login == "morris-bot"`
  AND `marker in body`, returns the comment `id` if exactly one match.
- `post_drift_comment`:
  - `result.status == "NA"`: return `{"action": "skipped", "reason": "non_v2_repo"}` — no API call.
  - `result.status == "SUCCESS"`: if an existing drift comment exists,
    update it to a "clean — re-checked" body (preserves audit trail);
    otherwise no-op.
  - `result.status == "FAILURE"`: render new body; if existing comment,
    `gh api -X PATCH .../issues/comments/<id>`; else `gh pr comment`.

The `v1` in the marker leaves room for future format changes without
losing idempotency on in-flight PRs.

---

## 7. Commit-status check contract

```
context: morris/canon-check                  # NEVER CHANGE — STORY-1007 G7 greps for this
state:   success | failure | pending | error
description: <≤140 chars summary>            # e.g. "3 canonical files drift"
target_url:  <optional link to Morris run log>
```

Posted via `gh api repos/{owner}/{repo}/statuses/{sha} -X POST`.

`update_status_check` is a thin shell over `gh api`. It returns the
parsed response body for assertions but tolerates non-zero exit codes
(GitHub may reject statuses on closed PRs — we log and continue).

---

## 8. Pin-file handling

```
/home/hermes/state/morris/canon-pins.yaml      # owned by STORY-1013 once it ships
---
gc_data_v2: deadbeefcafe1234...
```

`load_pinned_ref()`:
1. If env var `MORRIS_CANON_PIN` is set, return its value (test hook + ops override).
2. Else if the pin file exists, parse YAML and return `gc_data_v2` if present.
3. Else return `"main"`.

YAML parsing uses `pyyaml` if available; falls back to a regex extract
(`^gc_data_v2:\s*(\S+)`) so we don't add a new hard dep just for this.

---

## 9. Comment format (locked)

```
<!-- morris-canon-check:v1 -->
## Morris canon-check — DRIFT DETECTED

Canonical files in this PR drift from
`gc-data-v2/pipeline-template/<ref>`:

### `.github/pull_request_template.md`  — MODIFIED
```diff
<truncated 40-line unified diff>
```
**Remediation:**
```
curl -fsSL https://raw.githubusercontent.com/hpi-gorillacommerce/gc-data-v2/<ref>/pipeline-template/.github/pull_request_template.md -o .github/pull_request_template.md
```

### `.github/workflows/deploy-function-app.yaml` — ASSERTION_FAILED
Missing required pin `--python-platform x86_64-manylinux_2_17`.
See `gc-data-v2/platform/failure-modes.md` § "Deploy gate 2b".

---
*Posted by Morris (Engineering Manager). Re-runs on every poll cycle.*
*Last checked: <ISO8601 UTC>. Status check `morris/canon-check` will
remain FAILURE until all drifts are resolved.*
```

Clean-state body (only posted when updating an existing drift comment to "now clean"):

```
<!-- morris-canon-check:v1 -->
## Morris canon-check — CLEAN

All canonical files match `gc-data-v2/pipeline-template/<ref>` as of <ISO>.

*Status check `morris/canon-check` set to SUCCESS.*
```

---

## 10. Error handling & escalation

| Condition | Behaviour |
|---|---|
| Scaffold fetch 404 for a `required=True` watched file | `CanonCheckResult.status="FAILURE"`, with a `DriftedFile(drift_type="ASSERTION_FAILED")` whose `remediation` says "upstream scaffold returned 404 — investigate `gc-data-v2/pipeline-template/main`". Skill DM's Mark via existing Morris escalation channel (out of scope for this checker; the skill doc covers it). |
| GitHub API rate-limit (HTTP 403) | Caller catches, logs, skips PR for this cycle. Checker raises `CanonCheckTransientError`. |
| `gh` command not installed or unauthenticated | Commenter raises `RunnerError`; skill doc treats it as fatal and DM's Mark. |
| 3 consecutive cycles with no scaffold fetch | Out of scope for this story (lives in poll-loop layer / STORY-1007). |

---

## 11. Cron / invocation

- Invoked by `review-prs` poll loop (STORY-1007 wires the call site;
  we provide a stable entrypoint at
  `python -m tech_dev_agents.morris.canon_check <owner/repo> <pr_number>`).
- On-demand via `claude-sdk -p "Run canon-check on PR <N> in <repo>"
  -w /opt/agent` — the skill doc explains how.

---

## 12. Test coverage commitments

Tests in `tests/morris/test_canon_check.py` (Phase 7 produces these RED;
Phase 8 turns them GREEN):

1. `test_is_v2_repo_detects_v2_suffix` — positive + negative samples
2. `test_is_v2_repo_includes_api_advertising_amazon` — extra set hit
3. `test_check_pr_for_drift_non_v2_returns_NA` — `tech-dev-agents` repo → status `NA`, no fetcher calls
4. `test_check_pr_for_drift_matching_scaffold_returns_SUCCESS` — fake fetchers return identical bytes
5. `test_check_pr_for_drift_modified_pr_template_returns_FAILURE` — diff snippet present, drift_type=MODIFIED
6. `test_check_pr_for_drift_missing_required_file_returns_FAILURE` — drift_type=MISSING
7. `test_check_pr_for_drift_missing_glibc_pin_returns_FAILURE` — drift_type=ASSERTION_FAILED on deploy workflow
8. `test_check_pr_for_drift_scaffold_404_required_file_is_failure` — scaffold fetcher returns None → upstream-404 drift
9. `test_check_pr_for_drift_pin_sha_honored` — pinned ref passed in is the ref used in URLs (assert via captured fetcher calls)
10. `test_render_comment_includes_marker_and_diff` — body starts with `<!-- morris-canon-check:v1 -->`
11. `test_render_comment_clean_state` — SUCCESS render is also marked
12. `test_post_drift_comment_idempotent_updates_existing` — runner sees PATCH on second call, not duplicate POST
13. `test_post_drift_comment_skips_when_status_NA` — no runner calls
14. `test_update_status_check_uses_correct_context_name` — argv contains `morris/canon-check` exactly
15. `test_load_pinned_ref_env_override` — `MORRIS_CANON_PIN=abc` wins
16. `test_load_pinned_ref_falls_back_to_main` — no env, no file → `"main"`

Target: 16 GREEN tests, 0 failures, 0 errors.

---

## 13. Deviations from seed

| Seed said | We did | Why |
|---|---|---|
| `differ.py` | `checker.py` | Operating prompt is authoritative on filename. Public API unchanged. |
| `tech_dev_agents/morris/canon_check/tests/` | `tests/morris/test_canon_check.py` | Repo convention — every other Morris test is here; one discoverable suite. |
| 7 unit tests in SC-3 | 16 unit tests | Higher coverage (idempotency, status name contract, env override) — strict superset. |
| `posts comment via gh CLI` | `posts comment via `gh` runner protocol with DI | Same end behaviour; unit-testable without `gh` binary. |

All seed success criteria still satisfied. Status check name **`morris/canon-check`** is preserved verbatim — this is the STORY-1007 G7 contract.

---

## 14. Files this story creates / modifies

| Path | Action |
|---|---|
| `deployment/vm/skills/morris/canon-check/SKILL.md` | CREATE |
| `tech_dev_agents/morris/canon_check/__init__.py` | CREATE |
| `tech_dev_agents/morris/canon_check/checker.py` | CREATE |
| `tech_dev_agents/morris/canon_check/commenter.py` | CREATE |
| `tests/morris/test_canon_check.py` | CREATE |
| `features/story-1005-morris-canon-check/feature-spec.md` | CREATE (this file) |
| `features/story-1005-morris-canon-check/test-design.md` | CREATE in Phase 7 |

**Untouched** (per seed):
- `deployment/vm/skills/morris/review-prs/SKILL.md` (STORY-1007)
- `deployment/vm/skills/morris/merge/SKILL.md` (STORY-1007)
- `tech_dev_agents/morris/pre_dispatch/*` (STORY-1006/1009 — independent subagent)
- Any `*-v2` consumer repo
- `gc-data-v2/pipeline-template/**`

---

## 15. Contract surface (for downstream stories)

Stories 1007, 1009, and the epic test harness depend on these names. They are
stable and changes to them require a coordinated PR across stories:

- **Status check context:** `morris/canon-check`
- **Comment marker:** `<!-- morris-canon-check:v1 -->`
- **Public function:** `tech_dev_agents.morris.canon_check.checker.check_pr_for_drift`
- **Result dataclass:** `tech_dev_agents.morris.canon_check.checker.CanonCheckResult`
- **CLI:** `python -m tech_dev_agents.morris.canon_check <owner/repo> <pr_number>`
