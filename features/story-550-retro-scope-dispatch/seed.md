# STORY-550: Retro-Scope Dispatch Support

## Problem Statement

The `/retro` skill (`.sdlc/skills/retro/SKILL.md`) is fully defined and has
been run successfully in interactive Claude Code sessions — two historical
retros exist in this repo (`features/retro-week-2026-03-27/`,
`features/retro-ops-2026-04-02/`). But **it cannot be dispatched** via the
agent queue today.

Root cause: `deployment/hermes/dispatch_poller.py` appends an SDLC compliance
block to every dispatched prompt, keyed by `scope`. The block currently
handles `small`, `medium`, `large`, and `research`:

```python
_SDLC_REQUIRED = {
    "small": ["seed.md", "test-design.md"],
    "medium": ["seed.md", "analysis.md", "feature-spec.md", "test-design.md"],
    "large": ["seed.md", "analysis.md", "feature-spec.md", "test-design.md"],
    "research": ["research.md"],  # STORY-539: no seed, no tests, no PR
}
required_files = _SDLC_REQUIRED.get(scope, _SDLC_REQUIRED["small"])
```

There is no `"retro"` key. An enqueued retro would fall back to `small`,
which demands `seed.md`, `test-design.md`, and a PR — none of which apply
to a retrospective. The agent would spin trying to satisfy that gate and
either fail or produce the wrong artifacts.

Retros produce `retrospective.md` + `retro-proposal.yaml` and **do not**
produce code, tests, or PRs. They are doc-only artifacts pushed to a
branch for human review by the framework owner.

The fix is mechanical — mirror the research-scope handling from
STORY-539 / commit `c43f363`.

## Target Users

- **Mark** — wants to enqueue `/retro` runs the same way as any other
  dispatched work, instead of opening an interactive Claude Code session
  and running `/retro` manually each time.
- **Morris** — once epic-completion auto-triggers exist (separate story),
  Morris will want to enqueue retros programmatically.
- **Agents (Daisy, Devon)** — will execute the retro skill against the
  framework-deployed `.sdlc/skills/retro/SKILL.md` without the compliance
  block fighting them.

## Acceptance Criteria

- [ ] `_SDLC_REQUIRED` in `deployment/hermes/dispatch_poller.py` includes
  a `"retro"` key mapping to `["retrospective.md", "retro-proposal.yaml"]`.
- [ ] When `scope == "retro"`, the appended SDLC compliance block text:
  - Names the two required deliverables (`retrospective.md`,
    `retro-proposal.yaml`) and the target folder
    (`features/<epic-folder>/` or `features/retro-<name>/`).
  - Instructs the agent to read `.sdlc/skills/retro/SKILL.md` and follow
    it exactly.
  - States that retro scope is branch-only — push the branch when done,
    do NOT create a PR (the proposal yaml is reviewed out-of-band by the
    framework owner via `/retro-apply`).
  - Omits the TDD note and the phase-runner NOTE (identical policy to
    `research` scope).
- [ ] A dispatch with `scope=retro` does not get the `phase-runner` NOTE
  appended (match the `research` scope conditional).
- [ ] Unit test in `tests/deployment/test_dispatch_poller_compliance.py`
  (or equivalent) covers:
  - `scope="retro"` produces a compliance block naming both deliverables.
  - `scope="retro"` does NOT mention `seed.md`, `test-design.md`, or PR.
  - `scope="retro"` does NOT append the phase-runner NOTE.
  - The fallback-to-small behavior is preserved for unknown scopes.
- [ ] Deploy to all four agent VMs via `./deployment/vm/push-code.sh all`
  and verify each has the new code (`grep -c retro /opt/agent/dispatch_poller.py`
  returns a positive number matching the source on all VMs).

## Scope Classification

**Small** — one file modified (`dispatch_poller.py`), one test file added
or extended, zero DB / config / auth changes. Fits the Small phase path:
`1 → 7 → 8 → Done`. Deliverables: `seed.md`, `test-design.md`,
implementation commit + PR.

## Technical Notes

### Reference commit

`c43f363 fix(dispatch): add research scope to SDLC compliance block in poller`

That commit added the `"research"` key and a `scope == "research"` branch.
This story copies that pattern for `"retro"`.

### Exact code locations

`deployment/hermes/dispatch_poller.py` around line 522:

```python
_SDLC_REQUIRED = {
    "small": ["seed.md", "test-design.md"],
    "medium": ["seed.md", "analysis.md", "feature-spec.md", "test-design.md"],
    "large": ["seed.md", "analysis.md", "feature-spec.md", "test-design.md"],
    "research": ["research.md"],  # STORY-539: no seed, no tests, no PR
    # ADD:
    "retro": ["retrospective.md", "retro-proposal.yaml"],  # STORY-550
}
```

Around line 529, extend the scope-conditional block:

```python
if scope == "research":
    sdlc_block = (...)  # existing
elif scope == "retro":
    sdlc_block = (
        f"Story scope: retro. You MUST produce these deliverables in "
        f"features/<epic-folder>/:\n"
        f"  retrospective.md\n"
        f"  retro-proposal.yaml\n\n"
        f"Read and follow .sdlc/skills/retro/SKILL.md exactly. "
        f"Push the branch when done. Do NOT create a PR — retro scope is "
        f"branch-only; the proposal yaml is reviewed out-of-band by the "
        f"framework owner.\n"
    )
else:
    sdlc_block = (...)  # existing small/medium/large block
```

Around line 551, guard the phase-runner NOTE:

```python
if scope not in ("research", "retro"):
    suffix += phase_runner_note
```

### Out of scope

- Auto-triggering retros when an epic's last story hits Done. That's a
  separate story (likely STORY-551 or later) — this one just unblocks
  manual enqueue.
- Changes to `.sdlc/skills/retro/SKILL.md`. The skill works; only the
  dispatch compliance block is broken.
- `/retro-apply` dispatch support. That skill operates on framework
  source (coding-ai-config), not per-project — out of scope here.

## Dependencies

- **Existing:** `.sdlc/skills/retro/SKILL.md` already deployed to every
  agent VM (via the .sdlc submodule). Confirmed today — all four agents
  have it.
- **None new.**

## Recommended Next Phase

**Phase 7 (Test Design)** — Small scope path is `1 → 7 → 8 → Done`.
Phase 7 will write the RED tests against `_build_prompt_with_sdlc`
(or whichever function appends the compliance block). Phase 8 will
implement the two-line + branch addition to pass those tests, then
deploy.

## Test Criteria

Phase 7 produces `test-design.md` plus RED tests under `tests/deployment/` and `tests/skills/` covering:
- The compliance-block helper appends the retro-dispatch reminder when `.project` declares `scope: retro`.
- The helper is a no-op for all other scopes (regression guard — no accidental appends on small/medium/large).
- Branch and target-role selection match the retro-dispatch convention documented in the skill.

## Validation

After Phase 8 lands:
1. `python-tests` CI is GREEN on the PR.
2. A hand-run of the skill against a fixture `.project` with `scope: retro` produces the expected block with no other diff.
3. Running the skill against a `scope: small` fixture produces no new diff (idempotency check).
