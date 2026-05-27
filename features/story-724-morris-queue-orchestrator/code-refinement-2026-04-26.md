# Code Refinement Review — 2026-04-26

Scope: all production code committed today across worktrees wt-723 (STORY-723
adversarial reviewer), wt-724 (STORY-724 morris orchestrator), wt-726 (STORY-726
parallel coordination patches to dispatch_poller / db_service / routes), and
wt-727 (STORY-727 self-improvement pipeline).

Files reviewed:
- `wt-724/deployment/morris/scripts/detectors.py`
- `wt-724/deployment/morris/scripts/interventions.py`
- `wt-724/deployment/morris/scripts/orchestrator_loop.py`
- `wt-723/deployment/hermes/adversarial_reviewer.py`
- `wt-727/deployment/morris/scripts/improvement/{pattern_detector,proposal_generator,approval_handler,tracker,retrospective}.py`
- `wt-726/deployment/hermes/dispatch_poller.py` (jitter / completions / scope diff)
- `wt-726/tech_dev_agents/ops_console/{routes/dispatch.py,services/dispatch_db_service.py}`

---

## Fixed in place

### F1. `_run_async` extracted to shared module (wt-727)

**Where:** `deployment/morris/scripts/improvement/_async_util.py` (new)
and the 4 modules that previously each carried their own copy.

**Problem:** `approval_handler.py`, `tracker.py`, and `retrospective.py` each
contained an identical 14-line `_run_async` helper.  `proposal_generator.py`
contained two longer (~40-line) inline copies of the same idea with subtly
different fallback behavior on closed loops. Four implementations of the
same async-driver pattern is a maintenance hazard — when the asyncio
deprecation lands in Py 3.13 they will need to be patched four times.

**Fix:** Created `_async_util.run_async(coro, *, timeout=10.0)` as the single
source of truth. The 3 simple-copy modules now `import run_async as _run_async`
to preserve the underscore-prefixed alias the test suite expects (no patching
of `_run_async` is done — tests patch `_get_db_service` / `_get_teams_client`
per-module). `proposal_generator.py`'s two ~25-line inline blocks collapse to
2 lines each.

**Net delta:** −116 / +60 lines, all 27 STORY-727 tests still GREEN.

**Commit:** `f15654e` on `story-727/continuous-self-improvement`.

### F2. `run_briefing` reuses `post_dm` instead of duplicating the Graph payload (wt-724)

**Where:** `deployment/morris/scripts/orchestrator_loop.py` `run_briefing`.

**Problem:** `run_briefing` had its own `_graph_dm_url` helper and inlined the
same `body` / `contentType` payload that `post_dm` builds. Two functions
constructing identical Graph API payloads, two URL helpers (`_graph_url` in
interventions.py and `_graph_dm_url` here) — a classic DRY violation.

**Fix:** Replaced the bottom of `run_briefing` with a `post_dm("[BRIEFING]", …,
bullets, session, config)` call and deleted `_graph_dm_url`.

**Status:** Edit applied to working tree but **not committed** — wt-724 has
unrelated WIP edits in the same file plus interventions.py and detectors.py
being made by the operator. Leaving the edit staged for the next commit pass
in that worktree (the operator can include it in their next commit, or revert
if they decide differently). Tests stayed GREEN through the edit (45/45 passing
at the time of review).

---

## Quick fix (one short story each)

### Q1. `post_approval_needed` builds two parallel lists, uses one (interventions.py)

**Where:** `deployment/morris/scripts/interventions.py:270–290`.

```python
n = len(failures)
bullets: list[str] = [f"Story {story_id} has failed {n} times in 24h", ""]
bullets.append("Failure history:")
for f in failures:
    ...
    bullets.append(f"  {ts}: exit {code} — {reason_str}")
bullets.append("")
bullets.append("Recommended: respond CANCEL/RETRY/SKIP-{story_id} via ops console.".format(story_id=story_id))

url = _graph_url(config)
body_lines = [f"[APPROVAL-NEEDED] Story {story_id} has failed {n} times in 24h", ""]
body_lines.append("Failure history:")
for f in failures:
    ...
    body_lines.append(f"- {ts}: exit {code} — {reason_str}")
```

`bullets` is built then thrown away — `body_lines` is the one that ships. Both
loops format the same data. The function should call `post_dm("[APPROVAL-NEEDED]",
…, bullets, session, config)` like every other DM function in the module.

**Why this is a "quick fix" not a fixed-in-place:** the file currently has
uncommitted WIP from a separate resilience-hardening pass (try/except wrappers
around session.post). Editing on top of that WIP risks merge friction. Should
be cleaned up immediately after the WIP is committed.

### Q2. `_record_completion` doesn't use the established atomic-write pattern (wt-726)

**Where:** `deployment/hermes/dispatch_poller.py:1726–1740` (new STORY-726 code).

```python
tmp = _COMPLETIONS_FILE + ".tmp"
os.makedirs(os.path.dirname(_COMPLETIONS_FILE), exist_ok=True)
with open(tmp, "w") as f:
    json.dump(data, f, indent=2)
os.replace(tmp, _COMPLETIONS_FILE)  # atomic on Linux
```

The repo already has an established atomic-write pattern in
`scripts/work_queue.py` `_save()`: `tempfile.mkstemp(dir=parent, suffix=".tmp")`
+ `os.replace` + `fcntl.flock` on a separate `.lock` file. The new
`_record_completion`:

1. Uses a fixed `+ ".tmp"` suffix instead of `tempfile.mkstemp` (race window if
   two pollers ever both write — currently impossible because only one poller
   per VM, but that's a runtime invariant not enforced by the code).
2. Has no flock — if the SDK subprocess and the poller thread both call
   `_record_completion` concurrently (they do, transitively, via `start_story`)
   one write can win.
3. The temp file lives next to the data file, but `os.makedirs` is called
   each time even when the parent already exists.

**Recommended follow-up:** factor the atomic-write helper out of
`scripts/work_queue.py` into a `scripts/_atomic_json.py` (or similar) and use
it from both call sites. Small, isolated, one session.

### Q3. `parse_dt` is reimplemented inline in 5+ places across the repo

**Where:** `wt-724/deployment/morris/scripts/detectors.py:60-70` plus
`scripts/quota_check.py:60`, `scripts/quota_ccusage.py:268,275`,
`tests/ops_console/test_loki_client_quota.py` (8x), and the new
`dispatch_poller.py` completion code (`datetime.fromisoformat(ts.replace("Z",
"+00:00"))`).

**Recommended follow-up:** add a `tech_dev_agents/datetime_utils.py` with a
single `parse_iso_utc(s) -> datetime` and migrate callers. Not urgent — every
copy is correct in isolation — but a small story to converge them would prevent
divergent bugs (e.g. some don't normalize the `Z` suffix, some assume it's
present).

### Q4. Adversarial reviewer's `_read_seed_for_story_by_folder` lookalike

**Where:** `wt-723/deployment/hermes/adversarial_reviewer.py:_read_spec` and
`_read_test_files` reproduce the "look up by story_folder, fall back to
story-NNN-* prefix search" logic that `sdlc_phase_runner.py` already has in
`_read_seed_for_story_by_folder` (line 1368). Different filenames, same pattern.

**Recommended follow-up:** extract `find_story_file(workdir, story_folder,
filename)` to a small `story_paths.py` shared between the SDLC runner and the
adversarial reviewer. Saves ~30 lines and ensures the fallback contract stays
consistent.

---

## Medium refactor (needs planning)

### M1. Improvement modules' Teams DM duplicates VM `teams_m365_deployed.py`

**Where:** `wt-727/deployment/morris/scripts/improvement/proposal_generator.py`
`_post_approval_needed_dm`, `tracker.py`'s wrong-direction DM,
`retrospective.py`'s briefing post, and `approval_handler.py`'s [ACTION]
post all do the same thing: build a string, call `teams.post_dm(recipient="mark@gorillacommerce.co",
message=…)`. The recipient is hard-coded in 4 places.

The orchestrator (wt-724/`interventions.py`) does its DMs synchronously via
`session.post(graph_url, json=payload)`. The improvement modules do them via
`teams_client.post_dm(recipient=, message=)` (an async coroutine on a yet-to-be-defined
`teams_client`). The VM service `deployment/vm/teams_m365_deployed.py` has a
full `TeamsAdapter` class with HTML conversion and Graph API + m365 CLI auth.

**Three different DM call patterns** in code committed today, none of which can
share an implementation easily:

| Module                                | Auth      | API     | Style |
|---------------------------------------|-----------|---------|-------|
| `interventions.py`                    | API key on `session` (caller-built) | Graph (sync requests) | sync |
| `improvement/*`                       | "teams_client" (not wired) | unknown post_dm coroutine | async |
| `vm/teams_m365_deployed.py`           | m365 CLI token | Graph (aiohttp) | async, full adapter |

**Recommended:** before STORY-727 ships to live mode (`mode == "live"`), define
a single `TeamsDM.post(severity, headline, bullets)` interface backed by ONE of
the existing implementations. Otherwise we will have three subtly different
Mark-DM endpoints to keep working.  Plan as Phase-6 design work, not a same-day
refactor.

### M2. Adversarial reviewer's hard-coded model + retry-free Anthropic call

**Where:** `wt-723/deployment/hermes/adversarial_reviewer.py:_call_anthropic_api`.

```python
"model": "claude-sonnet-4-5-20251001",
```

The model id is pinned at literal — stale already (knowledge cutoff Jan 2026 is
post-Sonnet-4.6 / 4.7). The function is also missing:
- Prompt caching (system prompt + spec + test files would benefit hugely).
- Retry on 429 / overload.
- Max-tokens 4096 may truncate large coverage matrices.
- No streaming → 120s timeout is the only ceiling.

**Recommended:** wrap behind the same Anthropic SDK helper used elsewhere in
the codebase, parameterise the model, add caching breakpoints (system prompt =
the static reviewer-prompt template; user prompt = spec/diff/test variable
content). Plan as a small follow-up story.

### M3. `improvement/` module location overlaps `deployment/hermes/`

**Where:** `wt-727/deployment/morris/scripts/improvement/`.

The improvement modules live under `deployment/morris/scripts/` but several
target files they propose to modify live in `deployment/hermes/` (e.g.
`failure.agent_died_preflight` → `deployment/hermes/dispatch_poller.py`).
They also call DB services that live in `tech_dev_agents/ops_console/`.
Logically the self-improvement pipeline isn't morris-specific — it operates on
data the hermes pipeline produces.

**Recommended:** when adding the runner / cron entry-points (currently
unreached), reconsider whether `tech_dev_agents/improvement/` is the right
home. Don't move yet — wait until tests + DB schema are in main, then plan a
move story before the cron lands.

---

## Accepted as-is

### A1. `_get_teams_client` / `_get_db_service` stub duplication across improvement modules

The 4 modules each have a 4-line `_get_teams_client()` returning None and a
`_get_db_service()` returning None. The duplication is **intentional** — the
test suite uses `unittest.mock.patch("deployment.morris.scripts.improvement.<module>._get_db_service",
lambda: ...)` to inject mocks at module boundaries. Centralising the stubs
into a shared module would invalidate every patch. Leave as-is until / unless
the modules actually need shared dependency injection.

### A2. `parse_dt` lives in `detectors.py` (not a shared util)

Although the same idiom recurs across the codebase (Q3), within the orchestrator
the helper is genuinely a private detector concern (the API timestamps come
from a single source). Hoisting it to a shared util is part of Q3, not a
cross-cutting change to make today.

### A3. Synchronous `requests.Session` in interventions.py vs async `TeamsAdapter`

These run in two different runtime contexts: `interventions.py` is a cron
script (one-shot, synchronous), while `TeamsAdapter` is a long-lived async
service. Sharing code between them isn't desirable — they have different auth
strategies (API key vs m365 CLI token), different retry semantics, and
different lifetimes. Accept the divergence; revisit only as part of M1.

### A4. `MAX_RETRIES = 3` + jitter back-off uses inline RNG, not a shared helper

The new STORY-726 jitter code does
`random.uniform(0.5, _CLAIM_BACKOFF_BASE) * (attempt + 1)` inline. There is no
shared back-off util in the repo; introducing one for a 4-line block would be
overkill. Accept inline.

---

## Naming-and-style notes (no fix needed)

- `EscalateRecord` (verb) is inconsistent with sibling dataclass names that are
  noun-form (`StaleClaimRecord`, `ConflictRecord`, `NeedsInfoRecord`). Should
  be `RepeatedFailureRecord` for grep-ability. Could be renamed in a 5-line
  follow-up but not worth a standalone story.
- `_REVIEWER_SCOPES` in adversarial_reviewer.py is a tuple constant.
  `_REVIEWER_PROMPT_TEMPLATE` is a string constant. Both at module top — fine.
- `dispatch_poller.py`'s new `_COMPLETIONS_FILE` is a module-level computed
  string while `_LOCALLY_COMPLETED` is a global set. Mixing styles, but
  consistent with the rest of `dispatch_poller.py` which already has
  `MAX_RETRIES`, `_RESET_PATTERNS` etc. at module level.
- `interventions.py` uses `print` nowhere and `logger` everywhere — good. The
  adversarial reviewer mixes `print(..., flush=True)` and `logging.warning`
  for similar events. Inconsistent but matches the broader `dispatch_poller.py`
  convention of `print(..., flush=True)` for operator-visible lines and
  `logging` for module-internal events.

---

## Test outcomes after the fixed-in-place changes

| Worktree | Suite                                       | Before | After |
|----------|---------------------------------------------|--------|-------|
| wt-727   | `tests/deployment/test_self_improvement_727.py` | 27 ✅ | 27 ✅ |
| wt-724   | `tests/deployment/test_morris_orchestrator_724.py` | 31 ✅ | 45 ✅ (operator added 14 more) |
| wt-723   | `tests/deployment/test_adversarial_reviewer_723.py` | 15 ✅ | 15 ✅ (no edits) |
| wt-726   | `tests/deployment/test_parallel_coordination_726.py` | 8 ✅ | 8 ✅ (no edits) |

No regressions introduced.
