# STORY-903 Investigation: Dispatch Poller PR Detection

**Date:** 2026-05-06  
**Author:** Morris (investigation phase)  
**Files examined:**
- `deployment/hermes/dispatch_poller_v2.py`
- `deployment/hermes/sdlc_phase_runner.py`
- `tech_dev_agents/ops_console/services/dispatch_v2_service.py`
- `tech_dev_agents/ops_console/routes/dispatch_v2.py`
- `tests/deployment/test_dispatch_poller_v2_regressions.py`

---

## 1. Where PR detection lives (exact lines)

### The regex (line 527)
```python
_PR_NUMBER_RE = re.compile(r"\bPR\s*#?\s*(\d+)\b", re.IGNORECASE)
```
Matches: `PR #341`, `PR 341`, `PR#341`  
Does NOT match: `https://github.com/org/repo/pull/341`

### The extractor (lines 611–622)
```python
def _extract_pr_number_from_output(output: str) -> int | None:
    """Extract PR number from SDK output text (e.g. 'PR #341', 'PR 341')."""
    if not output:
        return None
    m = _PR_NUMBER_RE.search(output)
    if not m:
        return None
    try:
        n = int(m.group(1))
        return n if n >= 1 else None
    except Exception:
        return None
```

### The success payload builder (lines 625–639)
```python
def _success_transition_payload(output: str) -> tuple[str, dict]:
    pr_number = _extract_pr_number_from_output(output)
    if pr_number is not None:
        return "submitted", {"pr_number": pr_number, "output_summary": output[:500]}
    return "needs_info", {
        "kind": "question",
        "question": "Missing PR linkage: unable to detect PR number from successful run output.",
        "reason": "missing_pr_linkage",
        "output_summary": output[:500],
    }
```

### Called in poll_loop (lines 1126–1134)
```python
if success:
    event_type, event_data = _success_transition_payload(output)
    ok = transition_claim(claim, event_type=event_type, event_data=event_data, ...)
```

No URL regex. No GitHub REST fallback. Regex → submitted OR needs_info. That is the complete detection path.

---

## 2. Why the regex fails ~93% of the time

### The actual agent output format

When `sdlc_phase_runner.py` creates a PR (line 3565–3566):
```python
pr_url = (create_proc.stdout or "").strip().splitlines()[-1] if create_proc.stdout else ""
print(f"[DISPATCH] PR created: {pr_url}", flush=True)
```

`gh pr create` returns a bare URL on stdout:
```
https://github.com/hpi-gorillacommerce/tech-dev-agents/pull/341
```

So the phase runner prints:
```
[DISPATCH] PR created: https://github.com/hpi-gorillacommerce/tech-dev-agents/pull/341
```

The regex `\bPR\s*#?\s*(\d+)` **does not match this line**:
- It requires the literal text `PR` followed by optional `#` and digits
- The URL contains `/pull/341` — no `PR` text before the number
- `pull/341` has no `PR` word boundary trigger

**This is the root cause.** The regex only fires if the agent also prints something like "Opened PR #341" in conversation text, which happens occasionally (~7%) when the agent narrates its work.

### Why 2 of 27 DO work

The 2 successful rows likely result from agents that explicitly narrate PR creation in output text, e.g.:
> "I've opened PR #341 for review."

These are organic variations in agent output, not guaranteed by the protocol.

---

## 3. What happens when detection fails

When regex misses → `_success_transition_payload` returns `"needs_info"` with `reason="missing_pr_linkage"`.

The v2 state machine:
- `needs_info` event → story moves to `needs_info` state (human_queue)
- `submitted` event → story moves to `in_review` state (in_review lane)

So jobs with undetected PR go to **needs_info / human_queue** rather than **in_review**. They wait there for a human response that never comes (no human is monitoring the missing_pr_linkage question). This means they're effectively stuck — not in `in_review` as originally described in the story, but in `needs_info`.

The "2 of 27 in_review rows have pr_number set" stat likely refers to `dispatch_jobs.pr_number` being NULL on 25 rows — those 25 may have been submitted by the old code path (before the needs_info guard was added) or by manual manager operations that set `in_review` without pr_number.

Regardless: the core failure is that **pr_number is never populated** for ~93% of successfully completed agents because the regex can't extract a number from a bare GitHub URL.

---

## 4. Does a fallback gh query exist?

**No.** There is no GitHub REST fallback anywhere in `dispatch_poller_v2.py`. The only path is:
1. `_extract_pr_number_from_output(output)` — regex
2. If None → `needs_info` immediately

---

## 5. The fix

### New function: `_lookup_pr_by_branch`
Use `urllib.request` (same as `sdlc_phase_runner.py` style) to query:
```
GET https://api.github.com/repos/hpi-gorillacommerce/{repo}/pulls
    ?head=hpi-gorillacommerce:{branch}&state=all&per_page=5
```

Branch resolution order:
1. Use `claim.branch` if set (populated by STORY-860 orchestration from dispatch_jobs.branch column)
2. Derive `story-{N}/work` from `story_id` via `_STORY_NUM_RE` (same convention as `_extract_story_branch`)

Return the most recent PR's `number` field, or `None` on any error.

### Modified: `_success_transition_payload`
Add optional `story_id`, `repo`, `branch` kwargs. After regex miss, attempt REST fallback before returning `needs_info`.

### Resolution order (as specified in story)
```
regex → gh REST query → null (→ needs_info)
```

### Call site
In `poll_loop`, pass `claim.story_id`, `claim.repo`, `claim.branch` to `_success_transition_payload`.

---

## 6. Style reference

Pattern mirrors `deployment/hermes/sdlc_phase_runner.py` (lines 638–660) which uses `urllib.request.Request` + `urllib.request.urlopen` for REST API calls within agent VMs. The ops_console routes use `httpx` (async), but the deployment/hermes code uses stdlib `urllib.request` to avoid new dependencies.

`GITHUB_TOKEN` is available in agent VM env (confirmed: `deployment/hermes/hermes-env:23`, `entrypoint.sh:100`).

---

## 7. Constraints confirmed

- `_PR_NUMBER_RE` and `_extract_pr_number_from_output` must remain **unchanged** (pure addition)
- `urllib.request` (stdlib) — no new dependencies
- `structlog` already used in the module? Actually no — checking the module imports: uses `logging.getLogger(...)`. Will use `logger.warning/info` (stdlib logging, same as rest of file).
- `GITHUB_TOKEN` from env
- No v2 state machine or migration SQL changes
