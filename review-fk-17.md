## Morris Code Review — PR #17: STORY-435 Observability Hardening

**Author:** @jphillips-gc | **Size:** Small | **Verdict: APPROVE ✅** (one HIGH advisory — please address before merging)

---

### SDLC Compliance: PASS ✅

| Artifact | Status |
|----------|--------|
| `.project` | ✅ Updated |
| `features/story-435-observability-hardening/seed.md` | ✅ Present |
| `features/story-435-observability-hardening/test-design.md` | ✅ Added in this PR |
| `backlog.md` | ✅ Updated |
| Phases 1, 7, 8 in `.project` Phase History | ✅ Fully chronicled |
| Single story scope | ✅ Closes #7, #8, #12 |

---

### Findings

| Severity | Finding | Location |
|----------|---------|----------|
| **HIGH** | `docs/sre-runbook.md` states `health_check.py` "exposes two HTTP endpoints used by ACA liveness and readiness probes." Factually incorrect — the script is CLI-only (`argparse`), does not bind a socket or serve HTTP. The `httpGet` probe YAML references paths that do not exist yet. STORY-028 adds the HTTP server. Risk: STORY-028 implementers will assume endpoints already exist. **Fix:** Add one sentence: *"This is the target ACA probe configuration. STORY-028 adds the HTTP server exposing these paths; until then, use an `exec` probe."* | `docs/sre-runbook.md` |
| MEDIUM | `_scrub_dsn` uses literal string replace — does not catch standalone password in exception messages (theoretical, confirmed shape covered). Log as P2 in `docs/review-log.md`. | `scripts/health_check.py` |
| MEDIUM | `import logging` inside `finally` block — move to top-level stdlib imports. One-line change. | `scripts/health_check.py` |
| LOW | `DB_CHECK_TIMEOUT` `ValueError` — already tracked as issue #9, out of scope per D1. No action. | — |
| LOW | No CI configured — tracked in STORY-029. | repo |
| NIT | Runbook anchor note references Bicep template that does not exist yet — word as forward-looking. | `docs/sre-runbook.md` |

---

### Code Correctness

| Check | Result |
|-------|--------|
| `engine.dispose()` in `finally` (AC-1.3) | ✅ Correct |
| DSN scrub for confirmed leak shape (AC-1.4) | ✅ Correct |
| Keepa API key scrub (AC-1.5) | ✅ Correct |
| XFAIL strict pattern | ✅ Correct |
| Test coverage | ✅ Thorough |

Implementation is correct. Security fixes address real confirmed leaks. SDLC artifacts complete.

---

### Action Required Before Merge

1. **HIGH:** Fix runbook wording — add one clarifying sentence about HTTP endpoints being the target config (not current state)
2. **MEDIUM/NIT:** Move `import logging` to top-level imports

Once these two items are pushed, this is clear to merge. Approving the code now.
