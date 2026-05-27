# STORY-903: Test Design — Deterministic PR Detection

**Phase:** 7 — Test Design  
**State:** RED (tests written before implementation)  
**Test file:** `tests/deployment/test_poller_pr_detection.py`

---

## What we're testing

`dispatch_poller_v2._success_transition_payload` and the new `_lookup_pr_by_branch` helper.

The production change is:
1. `_success_transition_payload(output, *, story_id, repo, branch)` gains optional kwargs
2. After regex miss → calls `_lookup_pr_by_branch(story_id, repo, branch)`
3. If REST returns a number → emit `submitted` + pr_number
4. Otherwise → emit `needs_info` (existing behaviour)

---

## Test Groups

### Group A — Fast path: regex wins
**TC-1** `regex_match_returns_submitted_without_gh_call`
- Input: output containing "Opened PR #341"
- Assert: event_type == "submitted", event_data["pr_number"] == 341
- Assert: `_lookup_pr_by_branch` NOT called (patch confirms no REST call)

### Group B — Fallback path: regex misses, REST returns PR
**TC-2** `gh_fallback_called_when_regex_misses`
- Input: output containing bare URL `https://github.com/org/repo/pull/278`
  (regex can't match this — it's `/pull/278`, not `PR #278`)
- Patch `_lookup_pr_by_branch` to return 278
- Assert: event_type == "submitted", event_data["pr_number"] == 278

**TC-3** `gh_fallback_uses_claim_branch_when_set`
- Verify `_lookup_pr_by_branch` is called with `branch="story-99/some-slug"` when
  that's what was passed in

### Group C — Both miss
**TC-4** `both_miss_returns_needs_info`
- Input: output with no PR text
- Patch `_lookup_pr_by_branch` to return None
- Assert: event_type == "needs_info", event_data["reason"] == "missing_pr_linkage"
- Assert: poller does NOT crash, story still transitions

### Group D — Error handling in REST fallback
**TC-5** `gh_api_http_error_returns_none_does_not_crash`
- Patch `urllib.request.urlopen` to raise `urllib.error.HTTPError(code=403, ...)`
- Call `_lookup_pr_by_branch(story_id="STORY-903", repo="tech-dev-agents")`
- Assert: returns None (does not raise)

**TC-6** `gh_api_network_error_returns_none_does_not_crash`
- Patch `urllib.request.urlopen` to raise `OSError("Connection refused")`
- Assert: returns None

**TC-7** `gh_api_5xx_returns_none`
- Patch to raise `HTTPError(code=500, ...)`
- Assert: returns None

### Group E — Branch derivation
**TC-8** `branch_derived_from_story_id_when_not_provided`
- Call `_lookup_pr_by_branch(story_id="STORY-123", repo="repo")` with no branch arg
- Patch urlopen to capture the URL argument
- Assert URL contains `head=hpi-gorillacommerce%3Astory-123` (story-123/work)

**TC-9** `no_story_number_in_story_id_skips_rest_call`
- `story_id="unknown"` with no branch arg
- Assert `_lookup_pr_by_branch` returns None, no HTTP call made

**TC-10** `explicit_branch_overrides_derived_branch`
- Call with `story_id="STORY-123"` and `branch="story-123/poller-pr-detection"`
- Patch urlopen to capture URL
- Assert URL uses the explicit branch, not `story-123/work`

### Group F — REST response parsing
**TC-11** `picks_most_recent_pr_by_created_at`
- urlopen returns JSON: `[{"number": 10, "created_at": "2026-01-01T00:00:00Z"}, {"number": 20, "created_at": "2026-02-01T00:00:00Z"}]`
- Assert: returns 20 (higher created_at)

**TC-12** `empty_list_response_returns_none`
- urlopen returns `[]`
- Assert: returns None

---

## Regression tests (existing — must remain GREEN)

From `tests/deployment/test_dispatch_poller_v2_regressions.py`:
- `test_success_transition_payload_submitted_when_pr_present` — must still pass
  (positional `output` arg, no kwargs → falls back to None for story_id/repo, regex still works)
- `test_success_transition_payload_needs_info_when_pr_missing` — must still pass
  (no story_id/repo → no REST fallback → still needs_info)

---

## Test count

12 new tests in `tests/deployment/test_poller_pr_detection.py`  
2 existing regression tests pinned in `test_dispatch_poller_v2_regressions.py`

Total: 12 new + existing regressions GREEN = acceptance
