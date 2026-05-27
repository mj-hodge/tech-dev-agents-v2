# Feature Specification — STORY-803

## Fix the needs_info loop: override bypass + git-add bug + Phase-8-no-commits self-pause

> **Phase 6 deliverable.** Approach A — "Harden & Guard" — selected in `analysis.md` (weighted 3.55).
> Two hardening patches applied: (1) **R-A-6** fast-failure on Phase 8 retry; (2) **AC-3 re-injection scope** clarified — `_apply_override_directives` is already called inside the per-phase loop (line 3102), so AC-3 reduces to a verification test rather than new state.

---

## 1. Goals

| # | Goal | Measured by |
|---|------|-------------|
| G1 | Override directives cannot be silently ignored by an agent's seed-trigger pause | Zero `directive_bypass_attempted` events in 24h soak; AC-1, AC-2, AC-3 |
| G2 | `QUESTION.md` always lands on the branch when an agent pauses for clarification | Zero `question_commit_failed` events in 24h soak; AC-4, AC-5 |
| G3 | Phase-8 silent exits route to `failed`, never to `needs_info` | Zero `phase8_no_commits` events landing in `needs_info`; AC-6 |
| G4 | All 6 cancelled stories (008, 013, 015, 016, 017, 644) replay successfully under new code | Each completes, fails explicitly, OR lands in needs_info with a Mark-answerable question — NOT a self-diagnostic; AC-7 |
| G5 | Loki dashboard panel surfaces all three failure modes | Panel deployed showing 24h count of three event types; AC-8 |

---

## 2. Surface map

All work is in **`deployment/hermes/`** (agent VM Python) and **`tech_dev_agents/ops_console/routes/dispatch.py`** (server-side guard).

| File | Function/Block | Change type |
|------|----------------|-------------|
| `deployment/hermes/sdlc_phase_runner.py` | `_apply_override_directives` (line 499) | **Strengthen wording** — replace preamble |
| `deployment/hermes/sdlc_phase_runner.py` | NEW `_has_directive_file(workdir, story_folder)` helper | **Add** |
| `deployment/hermes/sdlc_phase_runner.py` | `_commit_and_push_question` (line 276) | **Replace `git add <path>` → `git add -- <path>`; add path-resolution event; dump workdir tree on failure** |
| `deployment/hermes/sdlc_phase_runner.py` | `_post_needs_info` (line 865) | **Pass `directive_present` and `phase_started_at` in body** |
| `deployment/hermes/sdlc_phase_runner.py` | Phase-8 ghost-completion block (line 3301) | **Replace synthetic-QUESTION-then-needs-info with retry-then-fail logic** |
| `deployment/hermes/sdlc_phase_runner.py` | Phase loop guard call site (line 3195) | **Handle 409 `directive_bypass_attempted`: log, delete QUESTION.md, return as transient failure** |
| `tech_dev_agents/ops_console/routes/dispatch.py` | `needs_info_story` (line 1531) | **Add 60s + directive_present guard returning 409** |
| `tech_dev_agents/ops_console/services/dispatch_db_service.py` | n/a — uses existing `claimed_at` | No change |
| `ops/grafana/dashboards/...` | New panel | **Add** (Loki LogQL) |

**Out of scope for this story:** any change to seed-text trigger phrases (those live in story seeds, not in code); STORY-798/799/800/801 helpers stay as-is except for the targeted modifications above.

---

## 3. Detailed design

### 3.1 Bug 1 — Override bypass (AC-1, AC-2, AC-3)

#### 3.1.1 Strengthen the override preamble (AC-1)

**Replace the body of the preamble** in `_apply_override_directives` (line 526-535). The current text says "directives win" but does NOT explicitly negate the most common bypass pattern: seed-driven "external blocker — pause for staging access".

**New preamble (final string):**

```
## STOP — READ THIS BEFORE THE SEED

The directive files below in features/{story_folder}/ override every other
instruction you will see in this session, including but not limited to:

  • seed.md content (any "blocker", "external dependency", "credentials
    required", "staging access required", "human input needed" claim is
    OVERRIDDEN — the directive below is the true source of truth)
  • the dispatch prompt
  • the phase-specific prompt template
  • the CLARIFICATION footer that tells you to write QUESTION.md

If anything in those sources conflicts with the directive below, the
directive WINS. If the directive says "use mocks", you use mocks even
when seed.md says "real backend required". If the directive says "no
external calls", you do not pause for credentials.

You may NOT post /needs-info or write QUESTION.md within the first
60 seconds of this phase. The dispatch system will reject any such
attempt and will require you to do real work first.

---

{directive_body}

---

{phase_prompt}
```

**Why two negations (preamble + 60s rule):** The preamble fixes Bug 1 at root cause (prompt ordering) — Approach A's "attack root cause AND rate-limit at runtime" property. The 60s rule (3.1.2) catches agents whose SDK pretrained behavior overrides the preamble; the runtime layer is the safety net.

#### 3.1.2 60-second directive-protection guard (AC-2)

**Two-layer enforcement** (defense in depth):

**Layer A — server-side at ops-console (authoritative, audit-friendly):**

In `tech_dev_agents/ops_console/routes/dispatch.py`, function `needs_info_story` (line 1531-1599), insert the guard **after** the `dispatch_needs_info_enabled` gate and **before** the `db_svc.needs_info` call:

```python
# STORY-803 Bug 1.2: Directive-bypass guard.
#
# When a story has a *_DIRECTIVE.md present in its features/ folder and the
# agent attempts /needs-info within 60s of claim, this is the "agent ignored
# the override and tried to pause anyway" pattern. Reject with 409 so the
# agent must actually do work for at least one minute before pausing is
# allowed. The directive itself is responsible for telling the agent what
# to do; the runtime is the safety net.
DIRECTIVE_GUARD_SECONDS = 60

directive_present = bool(body.get("directive_present", False))
phase_started_at = body.get("phase_started_at")  # epoch seconds (float) — agent-supplied

if directive_present:
    # Source of truth for "when did the claim start" is the DB row's claimed_at.
    # The agent-supplied phase_started_at is informational only.
    row_for_age = await db_svc.get_active_by_story_id(story_id)
    claimed_at = row_for_age.get("claimed_at") if row_for_age else None
    if claimed_at:
        seconds_since_claim = (datetime.now(tz=timezone.utc) - claimed_at).total_seconds()
        if seconds_since_claim < DIRECTIVE_GUARD_SECONDS:
            await emit_event(
                story_id, row_for_age.get("repo", ""), "directive_bypass_attempted",
                agent=agent_name,
                phase_num=current_phase,
                payload={
                    "seconds_since_claim": round(seconds_since_claim, 1),
                    "guard_threshold_s": DIRECTIVE_GUARD_SECONDS,
                    "question_file_path": question_file_path,
                    "phase_started_at": phase_started_at,
                },
            )
            raise HTTPException(
                409,
                f"directive_bypass_attempted: directive present and only "
                f"{seconds_since_claim:.0f}s since claim (guard: "
                f"{DIRECTIVE_GUARD_SECONDS}s). Agent must do real work first.",
            )
```

**Layer B — agent-side pre-check** (reduces network round-trip noise; not authoritative):

In `sdlc_phase_runner.py`, the Phase loop block at line 3195 currently calls `_post_needs_info` unconditionally. Add a pre-check:

```python
# STORY-803 Bug 1.2: client-side directive-bypass pre-check. Server is
# authoritative (via 409), but a fast local check avoids the unnecessary
# POST and keeps the [DISPATCH] log readable.
directive_present = _has_directive_file(workdir, story_folder)
seconds_since_phase_start = time.time() - _phase_start_ts
if directive_present and seconds_since_phase_start < 60:
    _emit_event(
        "directive_bypass_attempted",
        story_id=story_id,
        phase=phase_num,
        agent=os.environ.get("AGENT_NAME", "unknown"),
        seconds_since_phase_start=int(seconds_since_phase_start),
        question_file_path=question_path,
        layer="client",
    )
    # Drop the QUESTION.md so the next claim doesn't see a stale one
    try:
        os.remove(os.path.join(workdir, question_path))
    except OSError:
        pass
    NEEDS_INFO_STORIES.discard(story_id)
    return False, None, "directive_bypass_blocked"
```

**Helper to add (just below `_read_override_directives`):**

```python
def _has_directive_file(workdir: str, story_folder: str) -> bool:
    """Return True if features/<story_folder>/ contains OVERRIDE.md, DIRECTIVE.md,
    or any *_DIRECTIVE.md (matches _read_override_directives' file set).

    Used by the directive-bypass guard. Cheap dirent scan — no file reads.
    """
    feature_dir = os.path.join(workdir, "features", story_folder)
    if not os.path.isdir(feature_dir):
        return False
    try:
        for name in os.listdir(feature_dir):
            upper = name.upper()
            if upper in ("OVERRIDE.MD", "DIRECTIVE.MD") or upper.endswith("_DIRECTIVE.MD"):
                return True
    except OSError:
        return False
    return False
```

**`_post_needs_info` body update:**

```python
body_bytes = _json_mod.dumps({
    "agent": agent_name,
    "question_file_path": question_file_path,
    "phase": phase,
    # STORY-803: pass directive context so server can apply the 60s guard.
    "directive_present": directive_present,
    "phase_started_at": phase_started_at,
}).encode("utf-8")
```

The signature gets two new optional parameters with sensible defaults so existing callers (lines 3281, 3354) don't break:

```python
def _post_needs_info(
    story_id: str,
    question_file_path: str,
    agent_name: str,
    phase: int,
    *,
    directive_present: bool = False,   # STORY-803 Bug 1.2
    phase_started_at: float | None = None,
) -> bool:
```

**Phase loop call site (line 3195) is updated:**

```python
posted = _post_needs_info(
    story_id=story_id,
    question_file_path=question_path,
    agent_name=os.environ.get("AGENT_NAME", "unknown"),
    phase=phase_num,
    directive_present=directive_present,
    phase_started_at=_phase_start_ts,
)
```

The synthetic-QUESTION-on-missing-deliverable site at line 3281 and (post-fix) the legacy Phase 8 paths inherit `directive_present=False` (these aren't agent-driven /needs-info pauses, so the guard doesn't apply).

#### 3.1.3 Re-injection at every phase boundary (AC-3) — verification only

**Investigation result:** `_apply_override_directives` is already called inside the per-phase loop at line 3102. Each phase iteration produces a fresh `phase_prompt`, then wraps it. Therefore:
- AC-3 is already satisfied structurally for the multi-phase case (each phase reads the directive file and prepends).
- The "post-`/clear`" concern is not a re-injection problem because `/clear` is not used inside `_run_phase_sdk` — every phase invokes a fresh SDK process with the wrapped prompt.

**What we add for AC-3:** a regression test that asserts the wrap fires once per phase iteration and that the wrap content differs only by the per-phase prompt body (directive body is constant). See test T-3.

### 3.2 Bug 2 — `_commit_and_push_question` path/staging (AC-4, AC-5)

#### 3.2.1 Path-resolution precheck (AC-4)

The function already does `os.path.isfile(abs_path)` at line 308 and emits `stage="precheck"` on miss. **Strengthen the failure event** to dump the workdir tree (one level deep) under `features/<story_folder>/` so we can see what file the agent actually wrote.

**New helper `_features_subtree_snapshot`:**

```python
def _features_subtree_snapshot(workdir: str, story_folder: str, max_entries: int = 50) -> list[str]:
    """Return up to max_entries relative paths under features/<story_folder>/, one level deep.

    Used by question_commit_failed events so we can diagnose path mismatches without
    SSH access. Names only; no file contents.
    """
    feature_dir = os.path.join(workdir, "features", story_folder)
    if not os.path.isdir(feature_dir):
        return []
    out: list[str] = []
    try:
        for name in sorted(os.listdir(feature_dir))[:max_entries]:
            full = os.path.join(feature_dir, name)
            if os.path.isdir(full):
                out.append(name + "/")
            else:
                out.append(name)
    except OSError:
        pass
    return out
```

#### 3.2.2 `git add -- <path>` (AC-4 cont'd)

**Replace** the `git add <path>` invocation at line 318-321 with literal-path form:

```python
add = subprocess.run(
    ["git", "-C", workdir, "add", "--", question_path],  # STORY-803 Bug 2.2: literal path, no glob
    capture_output=True, text=True, timeout=10,
)
```

**Why `--` and not `-A`:** The seed offered `git add -A <path>` OR `git add -- <path>`. We choose `--` because (a) `-A` adds *all* tracked changes recursively, which would scoop up unrelated agent edits the operator did not intend to commit; (b) `--` solves the actual root cause (path containing dashes mis-interpreted as a flag); (c) the question files are not gitignored in any current workdir, so `-A`'s force-add advantage is not needed.

**Verify post-commit (already exists):** The push step at line 349 already verifies the commit reached origin. No change.

#### 3.2.3 Workdir tree dump on commit failure (AC-5)

In each `question_commit_failed` event emission inside `_commit_and_push_question` (lines 309-316, 322-330, 339-347, 354-362, 372-377), include a `feature_subtree` field:

```python
_emit_event(
    "question_commit_failed",
    story_id=story_id,
    stage="precheck",
    reason="QUESTION.md not present at expected path",
    question_path=question_path,
    feature_subtree=_features_subtree_snapshot(workdir, _derived_story_folder(question_path)),
)
```

Where `_derived_story_folder(question_path)` parses `features/<folder>/QUESTION.md` from the relative path. If parsing fails, omit the field.

**Helper:**

```python
def _derived_story_folder(question_path: str) -> str:
    """Extract story_folder from a 'features/<story-folder>/QUESTION.md' path.

    Returns "" if the path doesn't match the expected shape.
    """
    parts = question_path.replace("\\", "/").split("/")
    if len(parts) >= 3 and parts[0] == "features" and parts[-1] == "QUESTION.md":
        return parts[1]
    return ""
```

**Why the precheck stage gets the dump too:** That's the diagnostic we need most — it's where the path mismatch (Bug 2 Mode 2 from STORY-644) manifests. Without it, we'd still be SSH-ing in to look around.

### 3.3 Bug 3 — Phase 8 zero-commits → retry → fail (AC-6)

**Replace** the existing Phase-8 ghost-completion block (lines 3301-3370 in `sdlc_phase_runner.py`).

**New behavior:**

```
1. Phase 8 exits rc=0. Compute new_commits = git rev-list --count origin/main..HEAD.
2. If new_commits > 0  →  proceed (existing behavior).
3. If rev-list fails (sentinel -1) → distinguish from zero-commits:
       _emit_event("phase8_commit_check_failed", ...)
       return False, None, "phase8_commit_check_failed"  (do NOT retry, do NOT needs_info)
4. If new_commits == 0:
     a. R-A-6 fast-failure heuristic: if the original Phase 8 attempt
        finished in < 90 seconds (phase_duration_s = time.time() -
        _phase_start_ts), do NOT retry. Skip directly to step 5d.
     b. Emit phase8_no_commits event with attempt=1.
     c. Re-invoke _run_phase_sdk with an enhanced prompt (see 3.3.1)
        and a single-attempt retry budget (max_turns from phase config).
     d. After retry: re-check new_commits.
        - If new_commits > 0 → proceed (success on retry).
        - If retry exited rc=-429 or rc<0 (rate limit / kill) → return
          (False, None, "phase_8_failed: retry hit rate limit") so the
          poller routes to its existing rate-limit handler.
        - Otherwise (still 0 commits) → step 5.
5. Transition to FAILED:
     a. Do NOT write a synthetic QUESTION.md.
     b. Do NOT call _post_needs_info.
     c. Emit phase8_no_commits event with attempt=2 (or attempt=1 if
        fast-failure fired).
     d. Notify Teams with a "failed" message (not "needs operator input").
     e. Return (False, None, "phase_8_failed: phase8_silent_exit").
```

The poller's existing failure-classification logic (`_classify_failure`) at `dispatch_poller.py` already routes `phase_N_failed` reasons through `NEVER_RETRY_CLASSES` if matched; we add `phase8_silent_exit` to that set so the poller marks the row as terminally `failed` (not `pending` for retry).

#### 3.3.1 Enhanced retry prompt

```python
RETRY_NUDGE = (
    "\n\n## RETRY — PHASE 8 IMPLEMENTATION\n\n"
    "Your previous Phase 8 attempt returned rc=0 but produced ZERO commits "
    "on this branch versus origin/main. That means you exited without "
    "writing code. This is your second and final attempt.\n\n"
    "1. Read features/<story_folder>/test-design.md for the acceptance "
    "criteria. The tests are RED — make them GREEN.\n"
    "2. Implement the code. Commit each logical unit with a clear message.\n"
    "3. Push the branch.\n"
    "4. If you genuinely cannot proceed, write QUESTION.md describing the "
    "SPECIFIC technical blocker (file paths, error messages, missing "
    "fixtures). DO NOT write 'I might be misinterpreting the prompt' — "
    "that is not actionable. Be specific or do not pause.\n\n"
    "If this attempt also produces zero commits, the story will be "
    "marked FAILED (not needs_info), and an operator will diagnose."
)
retry_prompt = phase_prompt + RETRY_NUDGE
rc_retry, output_retry = _run_phase_sdk(
    story_id=story_id,
    repo=repo,
    phase_num=8,
    phase_name="Implementation (retry)",
    prompt=retry_prompt,
    workdir=workdir,
    max_turns=max_turns,  # same budget — fast-failure fires if retry also exits short
    env=env,
    scope=scope,
    rework_of=rework_of,
    last_output_ts=_hb_last_output_ts,
)
```

**Token-burn ceiling:** Worst case is 1× full Phase 8 budget on retry. R-A-6 fast-failure caps the cost on the *original* attempt. Total worst case = 2× max_turns; with fast-failure firing on the original (<90s exit), total = 1× max_turns + 1× retry budget. The fast-failure heuristic on the original attempt is what pays the 50% reduction.

#### 3.3.2 Failed-state metadata

Pass `failure_reason="phase8_silent_exit"` through to the poller's `_report_fail` path so it lands in `dispatch_items.failure_reason`. The dashboard's failed-queue view already renders `failure_reason`. Operators see "phase8_silent_exit" and know to dispatch to a stronger model or fix the test-design.md.

### 3.4 Loki dashboard panel (AC-8)

**Panel:** "needs_info loop guards (24h)" — placed adjacent to existing dispatch panels in `ops/grafana/dashboards/dispatch-overview.json` (or whichever JSON the deployment uses).

**Three time-series queries (LogQL):**

```logql
# directive bypass attempts (server + client layer)
sum by (story_id) (
  count_over_time({app="ops-console"} |= "directive_bypass_attempted" [24h])
  + count_over_time({job=~"agent-.*"} |= "directive_bypass_attempted" [24h])
)

# question commit failures
sum by (stage) (
  count_over_time({job=~"agent-.*"} |= "question_commit_failed" [24h])
)

# phase 8 silent exits
sum by (attempt) (
  count_over_time({job=~"agent-.*"} |= "phase8_no_commits" [24h])
)
```

**Single-stat panels:** Add three single-stat panels (24h totals) with thresholds: green=0, yellow=1-3, red=4+. Goal per Done = 0.

**Alert rules** (Grafana → Alerting):

- `STORY-803-A1`: `count_over_time({app="ops-console"} |= "directive_bypass_attempted" [1h]) > 5` → page Mark.
- `STORY-803-A2`: `count_over_time({job=~"agent-.*"} |= "phase8_no_commits" [1h]) > 3` → notify Slack.

(Threshold tuning happens in Phase 10. For Phase 8 we land the panel + a single warn alert per signal.)

---

## 4. Build order (Phase 8)

Each commit is a single logical unit (per CLAUDE.md "Commit after each logical unit"):

1. **Tests RED** — Phase 7 deliverable. All tests below land first, all fail.
2. **`_has_directive_file` helper + `_features_subtree_snapshot` helper + `_derived_story_folder` helper.**
3. **`_apply_override_directives` preamble rewrite.** Test T-1 GREEN.
4. **`_commit_and_push_question` `git add --` + dump fields.** Tests T-4, T-5 GREEN.
5. **`_post_needs_info` body fields + signature.** No standalone test (verified via T-2).
6. **Phase-runner phase-loop directive-bypass pre-check + 409 handling.** T-2 client-layer GREEN.
7. **Server-side guard in `dispatch.py` `needs_info_story` route.** T-2 server-layer GREEN.
8. **Phase-8 zero-commits retry + fast-failure + transition to failed.** T-6 GREEN.
9. **`NEVER_RETRY_CLASSES` adds `phase8_silent_exit`.** T-7 GREEN.
10. **Grafana dashboard JSON.** Structural test T-8 GREEN.

---

## 5. Test matrix (12 tests, all in `tests/deployment/` and `tests/ops_console/`)

| ID | Bug | Test | File |
|----|-----|------|------|
| T-1 | 1 | `test_apply_override_directives_strengthened_preamble`: asserts new preamble contains "STOP — READ THIS BEFORE THE SEED" and the explicit staging-blocker negation phrase | `tests/deployment/test_phase_runner_override_directive.py` (extend) |
| T-2a | 1 | `test_post_needs_info_sends_directive_present_and_phase_started_at`: asserts the JSON body posted to ops-console includes both fields | `tests/deployment/test_phase_runner_needs_info.py` (extend) |
| T-2b | 1 | `test_needs_info_endpoint_rejects_under_60s_with_directive`: hits the live endpoint with a synthetic dispatch row whose `claimed_at = now() - 5s`, body `directive_present=true` → expects 409 + `directive_bypass_attempted` event | `tests/ops_console/test_dispatch_needs_info_directive_guard.py` (NEW) |
| T-2c | 1 | `test_needs_info_endpoint_allows_under_60s_without_directive`: same setup but `directive_present=false` → expects 200 (existing behavior preserved) | (NEW, same file) |
| T-2d | 1 | `test_needs_info_endpoint_allows_after_60s_with_directive`: `claimed_at = now() - 90s`, `directive_present=true` → expects 200 | (NEW, same file) |
| T-3 | 1 | `test_phase_runner_reapplies_override_per_phase`: drives the per-phase loop with 3 phases and a directive file; asserts `_apply_override_directives` is called 3× and `override_directive_applied` event fires 3× | `tests/deployment/test_phase_runner_override_directive.py` (extend) |
| T-4 | 2 | `test_commit_and_push_question_handles_dashed_path`: uses a temp git repo with `features/story-644-bsr-competitor-category-monitor/QUESTION.md`; asserts the helper returns True and the file is on origin | `tests/deployment/test_commit_and_push_question.py` (NEW) |
| T-5 | 2 | `test_commit_and_push_question_failure_dumps_subtree`: forces a precheck miss; asserts the emitted event includes `feature_subtree` listing the actual contents | (NEW, same file) |
| T-6a | 3 | `test_phase8_zero_commits_retries_with_enhanced_prompt`: mocks `_run_phase_sdk` to return rc=0; asserts a second call is made with the RETRY_NUDGE in the prompt | `tests/deployment/test_phase_runner_phase8_silent_exit.py` (NEW) |
| T-6b | 3 | `test_phase8_zero_commits_fast_failure_skips_retry`: mocks `_run_phase_sdk` to exit rc=0 in <90s; asserts only ONE call (no retry) and return reason `phase8_silent_exit` | (NEW, same file) |
| T-6c | 3 | `test_phase8_retry_zero_commits_transitions_to_failed`: mocks both calls returning rc=0 with 0 commits; asserts `phase_8_failed: phase8_silent_exit` reason and NO synthetic QUESTION.md written | (NEW, same file) |
| T-7 | 3 | `test_dispatch_poller_phase8_silent_exit_in_never_retry_classes`: asserts `phase8_silent_exit` is in `NEVER_RETRY_CLASSES` and that the failure-classification function returns `failed` (not `pending`) for that reason | `tests/deployment/test_dispatch_poller_retry_classification.py` (extend) |
| T-8 | 5 | `test_grafana_dashboard_has_story_803_panel`: parses the dashboard JSON and asserts a panel titled "needs_info loop guards (24h)" exists with three target queries | `tests/ops/test_grafana_dashboard_story_803.py` (NEW) |

**Shape of T-2b (server-side guard) — sketched because it's the linchpin:**

```python
async def test_needs_info_endpoint_rejects_under_60s_with_directive(
    client, db_with_claimed_row, monkeypatch,
):
    # Arrange: row claimed 5 seconds ago
    db_with_claimed_row(story_id="STORY-TEST", claimed_at_offset_s=-5)

    # Act
    resp = await client.post(
        "/api/dispatch/needs-info/STORY-TEST",
        json={
            "agent": "test-agent",
            "question_file_path": "features/story-test/QUESTION.md",
            "phase": 4,
            "directive_present": True,
            "phase_started_at": time.time() - 4,
        },
    )

    # Assert
    assert resp.status_code == 409
    assert "directive_bypass_attempted" in resp.text
    # Event was emitted
    events = get_emitted_events("STORY-TEST")
    assert any(e["event"] == "directive_bypass_attempted" for e in events)
    # State did NOT transition
    row = await db_svc.get_active_by_story_id("STORY-TEST")
    assert row["status"] == "claimed"
    assert row.get("needs_info_path") is None
```

---

## 6. Error handling & failure modes

| Scenario | Detected by | Outcome |
|----------|-------------|---------|
| `*_DIRECTIVE.md` in features/<folder> but agent /needs-info posted at t<60s | Server: `claimed_at` check; Client: `_phase_start_ts` check | 409 from server, client returns `directive_bypass_blocked`; row stays `claimed`; poller's normal failure-retry policy applies (one retry permitted by classification) |
| `_has_directive_file` race — directive file appears between phase start and the post | Either layer | Same as above (correct behavior — directive WAS present at post-time) |
| `_features_subtree_snapshot` errors (workdir gone, permission denied) | try/except inside helper | Returns `[]`; event still fires |
| `git add -- <path>` fails (file truly missing despite precheck) | `add.returncode != 0` | `question_commit_failed: stage=add` event with `feature_subtree` field; helper returns False |
| Phase 8 retry hits rate limit (rc=-429) | `_run_phase_sdk` return code | Bubble through existing `quota_exceeded` reason; do NOT mark `phase8_silent_exit` |
| Phase 8 retry SDK crashes (rc<0 non-429) | `_run_phase_sdk` return code | Mark `phase_8_failed: rc={rc}` (existing path); do NOT mark `phase8_silent_exit` |
| `git rev-list origin/main..HEAD` fails (network, missing remote) | exception in commit-count check | Sentinel rc=-1 → emit `phase8_commit_check_failed`; return `False, None, "phase8_commit_check_failed"`; do NOT retry, do NOT needs_info (R-A-5 mitigation) |
| Server-side guard active but agent did not pass `directive_present` (older agent on a partial deploy) | `body.get("directive_present", False)` defaults False | Guard does not fire; existing behavior preserved (graceful degradation during rolling deploy) |

---

## 7. Deployment plan

**Order:** Test on **derrick** first (per seed § Hard constraints), 24h soak, then fleet rollout.

1. Merge PR. CI green required.
2. `./deployment/vm/push-code.sh derrick` — push + restart + smoke test (CLAUDE.md § Code Deployment).
3. Manually re-dispatch one cancelled story (STORY-644 — has dashed folder, exercises Bug 2 fix) to derrick.
4. Verify in Loki:
   - `override_directive_applied` event fires
   - Either story progresses to commits OR a `directive_bypass_attempted` 409 fires (and the story re-enters claim — which is correct behavior)
   - No `question_commit_failed` events
5. If derrick is clean for 6 hours: `./deployment/vm/push-code.sh dan devon daisy hermes` for fleet rollout.
6. Replay all 6 cancelled stories (008/013/015/016/017/644) per AC-7. Document outcomes in `verification.md`.
7. Watch Loki dashboard for 24h soak (AC-8). Goal: zero of all three event types in the 24h window starting from fleet-rollout completion.

**Rollback plan:** Revert PR → `./deployment/vm/push-code.sh all`. The server-side guard reads `directive_present` from request body which defaults False, so an older deployment of agent code is forward-compatible with the new ops-console (and vice versa).

---

## 8. Acceptance criteria mapping

| AC | Spec section | Tests |
|----|--------------|-------|
| AC-1 | 3.1.1 — preamble rewrite | T-1 |
| AC-2 | 3.1.2 — 60s guard (both layers) | T-2a, T-2b, T-2c, T-2d |
| AC-3 | 3.1.3 — re-injection (verification only) | T-3 |
| AC-4 | 3.2.1 + 3.2.2 — precheck + `git add --` + contract test | T-4, T-5 |
| AC-5 | 3.2.3 — workdir tree dump | T-5 |
| AC-6 | 3.3 — retry + fast-failure + failed transition | T-6a, T-6b, T-6c, T-7 |
| AC-7 | § 7 step 6 — 6-story replay → `verification.md` | (manual; not unit-tested) |
| AC-8 | 3.4 — Loki dashboard panel + alerts | T-8 |

---

## 9. Risks & mitigations carried from analysis.md

| ID | Risk | Mitigation in spec |
|----|------|--------------------|
| R-A-1 | Guard must be conditional on directive presence, not blanket 60s rate-limit | Spec § 3.1.2 — `if directive_present:` is the outer condition |
| R-A-2 | Cross-story directive contamination via slug mismatch | T-3 verifies `_extract_story_folder` returns the correct folder; the existing `override_directive_applied` event already includes `story_folder` in payload |
| R-A-3 | Double-injection if `_apply_override_directives` is called at start AND at boundary | Spec § 3.1.3 — single call site at line 3102 inside the per-phase loop, no second injection point added |
| R-A-5 | `git rev-list` fails on fresh branches → sentinel -1 swallows real zero-commit | Spec § 6 — explicit `phase8_commit_check_failed` event distinguishes the two cases |
| R-A-6 | Auto-retry doubles token cost for deterministic failures | Spec § 3.3 step 4a — fast-failure heuristic skips retry when original Phase 8 exited <90s with zero commits |
| R-B-5 | 60s guard requires `claim_start_ts` threading | Spec § 3.1.2 — server-side computes from DB `claimed_at`; agent passes `phase_started_at` for diagnostic only. No threading change needed in upstream callers |

---

## 10. Hard constraints (from seed) — verified

- ✅ STORY-798/799/800/801 helpers untouched except for targeted modifications (preamble text rewrite in 799's `_apply_override_directives`; `git add --` flag fix in 798's `_commit_and_push_question`). All four helpers retain their function signatures and their primary behavior.
- ✅ Test on derrick first (deployment plan § 7 step 2).
- ✅ No skips of `/cancel` — code-only fix.

---

## 11. Follow-ups (out of scope for this story)

- **Per-VM heuristic on directive freshness:** if the directive file mtime is older than the seed's mtime, warn that the directive may be stale. Tracked separately.
- **Server-side check for fast-failure:** a future enhancement could let ops-console inspect `_run_phase_sdk` durations directly via the dispatch_events stream and short-circuit retry budgets centrally. Today the heuristic lives in the agent's phase runner.
- **Operator UI for `phase8_silent_exit`:** the failed-queue view already shows failure_reason; consider adding an "explain" button that links to the agent's last 100 log lines for triage.
- **Removing the synthetic-QUESTION.md path entirely:** Phase 6's `_verify_deliverable` block at line 3241 still falls into a synthetic-QUESTION → needs_info path. This is intentional for Phase 6 (an operator CAN answer "what should the deliverable look like?") but worth re-evaluating once the dashboard shows zero of these events for 30 days.
