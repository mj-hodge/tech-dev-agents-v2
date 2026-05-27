## Morris Code Review — PR #200: `fix(dispatch): phase runner bypassed on every dispatch`

**Size:** Trivial (1 file, +9/−0) | **Verdict: APPROVE ✅**

### What It Fixes

`start_story._run_and_complete()` referenced `resumed_question_path` and `rework_of` as closure variables — but neither existed in scope. Every dispatch hit a `NameError`, silently fell back to single-shot mode, and bypassed multi-phase SDLC, phase enforcement, and PR creation guarantees. Surgical fix: add both as optional keyword parameters (default `None`) to `start_story()` and extract from the queue item / claim response at the call site.

### Findings

| Severity | Finding |
|----------|---------|
| MEDIUM | `poll_loop` call site also calls `start_story` without the new params. Safe (defaults to `None`) — but if `poll_loop` is an active dispatch path, reworks dispatched through it silently drop both fields. Audit in follow-up. |
| INFO | "Python contract + unit tests" CI failure is **pre-existing on `main`** — seeds missing required template sections. Unrelated to this 1-file change. Contract-critical invariant tests ✅ Full pytest ✅ Playwright ✅ |

### SDLC Compliance
Trivial scope — no phase artifacts required. ✅

**Next step:** `./deployment/vm/push-code.sh all` per the PR test plan once merged.
