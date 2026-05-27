# Seed: STORY-556 — Fleet Reliability Test Backfill (Weekly Bug Patterns 2026-04-17 → 2026-04-23)

## Overview

| Field | Value |
|-------|-------|
| Mode | feature_add |
| Scope | small |
| Feature Name | Test backfill: unit tests covering the six bug-pattern gaps surfaced by fleet triage this week |
| Active Branch | `story-556/fleet-reliability-test-backfill` |
| Phase Path | 1 → 7 → 8 → Done |
| Parent Stories | STORY-528 (dispatch routing fix), STORY-505 (phase sequencing fix), STORY-532 (needs_info endpoint) |
| Repo | tech-dev-agents |

---

## 1. Idea / Trigger

This week (2026-04-17 through 2026-04-23) the fleet hit a cluster of bugs that all reached production-broken states. Some classes have been fixed (STORY-528/505/300 patch landed 2026-04-23 in commits `4473c80` + `2e9d231`), but the underlying code paths for the remaining classes still have **zero test coverage**, meaning any silent regression would be invisible until another story fails in production.

Mark's explicit guidance (saved 2026-04-23 in `feedback_test_coverage_critical.md`):

> Every code change to the agent fleet must ship with unit tests that exercise actual behavior, not just shape/structure. All edge cases need to be caught in code and not by deploys and story failures.

Today's triage produced a coverage matrix (run `grep -rlE '<pattern>' tests/ frontend/src/__tests__/ e2e/`). Six patterns have **zero** or **insufficient** coverage:

1. `credential.pool` — 0 files
2. `suppress_credential` — 0 files
3. `compression.*foundry` — 0 files
4. `rate.limit.*release` (release-not-fail path) — 0 files
5. `-w` flag / SDK invocation changes — 0 files
6. `daily.cap` ≤ 30s phantom-claim skip — only 1 file, needs deeper coverage

## 2. Problem Statement

Six bug classes hit prod this week, all for the same underlying reason — **the code path had no behavior test that would have caught the regression before deploy**:

| Date | Pattern | Incident (short) | Coverage before fix | Coverage after |
|------|---------|------------------|---------------------|-----------------|
| 2026-04-18 | Deploy-without-restart | Agents ran stale cached modules for a weekend; burned ~$1K/day | None | push-code.sh smoke test (post-deploy); still no unit test |
| 2026-04-20 | SDK `-w` flag change | All agents failed on every story for hours | None | push-code.sh smoke test; no unit test locking the invocation contract |
| 2026-04-21 | Rate-limit fail+re-claim loop | Devon 5× phantom claim in 4 min | Patched via STORY-538 "release not fail" | No test for `/api/dispatch/release` vs `/fail` decision |
| 2026-04-22 | Foundry Opus bleed ~$1K/day | Compression called Opus instead of Haiku | Morris's `/tmp/test_aux_compression_foundry.py` (NOT in repo) | Port to repo |
| 2026-04-23 | Credential pool contamination | Morris 401'd on every Teams reply because stale claude_code OAuth was in pool | `suppress_credential_source` API exists but no test | Nothing locks the suppression behavior |
| 2026-04-23 | Ghost Phase 8 completion | STORY-300/301 marked done with 0 commits | Fixed in `4473c80`; `TestPhase8GhostCommitGuard` (3 tests) lands with this PR | ✓ |

**Open gaps** after this week's fixes:

- **Credential pool integrity.** Morris's 401 incident was a 4-layer credential-pool bug: claude_code OAuth contaminated the pool, broke main-model routing, led to 401s on Teams replies. The fix (`suppress_credential_source`) has no test. Any regression would silently re-contaminate the pool.
- **Compression routing.** The `/tmp/test_aux_compression_foundry.py` tests Morris uses to verify Foundry-backed Haiku compression live ONLY on the Morris VM. They're not in the repo, not in CI, not run on deploy. The Opus bleed can recur if anyone re-factors `auxiliary_client.py`.
- **Rate-limit release path.** STORY-538 changed the rate-limited-story handling from "fail+retry" to "release+idle", which is load-bearing for cost control. There's a unit test for the fail path but not the release path.
- **SDK invocation contract.** `claude_sdk_tool.py` expects `-p <prompt> -w <workdir>` plus a specific env setup. One flag change broke everything. No test asserts the CLI argv shape.
- **Daily-cap phantom-claim guard.** Duration < 30s skip-retry: exists; covered for `exit_code != 429`. Need parametric tests across exit codes + duration boundaries (29s, 30s, 31s, 5s, 0s) + quota-exhausted paths.

## 3. Scope Classification

**Small.** This story adds tests only. No production-code changes. No schema changes. No new endpoints.

Specifically:
- **6 new test files** under `tests/deployment/` (or `tests/ops_console/` as appropriate)
- Each file exercises real behavior — not tuple shape or string content. Mock only external I/O.
- RED-first: every test must FAIL against the deployed-2026-04-22 agent code and PASS against the deployed-2026-04-23 code.

Estimated 4–6 hours.

## 4. Phase Path

```
1 (Seed)          — this file
7 (Test Design)   — test-design.md enumerating every behavior + its test
8 (Implementation) — test files under tests/ + green run
Done
```

No Phase 2/3/4/5/6 — scope is small and purely additive.

## 5. Acceptance Criteria

Must-contain tokens (the Acceptance Diff gate will verify these paths exist under `tests/`):

- `tests/deployment/test_credential_pool_suppression.py` — covers `suppress_credential_source` round-trip, pool-empty state, pool-rebuild-from-env, rejection of suppressed source on reload.
- `tests/deployment/test_compression_routing_foundry.py` — port of Morris's 3 tests: resolver-returns-AnthropicAuxiliaryClient, regression-guard-for-OpenAI-custom, live-call-shape. May need to mock the Foundry HTTP layer since agent VMs aren't reachable from CI.
- `tests/deployment/test_dispatch_poller_release_on_rate_limit.py` — story that fails with exit_code=429 calls `/api/dispatch/release/{id}` not `/api/dispatch/fail/{id}`; duration boundary tests (29s/30s/31s); rate-limited second story of a session stays released not claimed.
- `tests/deployment/test_sdk_tool_invocation_contract.py` — asserts `claude_sdk_tool.py` is invoked with `-p` + `-w` + expected env vars (`DISPATCHED_BY_POLLER=1`); locks the argv shape so a flag rename breaks a test, not a fleet deploy.
- `tests/deployment/test_dispatch_poller_daily_cap_guard.py` — parametric matrix: (exit_code ∈ {0, 1, 429, 143}) × (duration ∈ {0, 15, 29, 30, 31, 120}) → expected {retry, release, no-op}. Plus: quota-exhausted stays paused until reset; phantom-claim pattern (5× claim-fail in 4 min) cannot reproduce.
- `tests/deployment/test_deploy_smoke_contract.py` — locks the post-deploy smoke test shape (import check + claude CLI exit code + error scan). Since `push-code.sh` is a bash script, call out to it from pytest with a mocked SSH target.

Each test file must run under plain `pytest tests/deployment/<file>` without a VM or a live Foundry/OpenRouter key (mock all external HTTP).

## 6. Out of Scope

- Fixing any NEW bugs discovered while writing tests — file a follow-up story.
- Adding Playwright specs — frontend code untouched.
- Refactoring production code. If a test can't be written cleanly because the code is untestable, write the test RED against today's code and file a structural-refactor follow-up — do not refactor inline.
- EPIC-006 test coverage (stories 300–324). That's a separate advertising-amazon repo backfill.

## 7. Test Design Hint (for Phase 7)

Use the 2026-04-23 tests as the template for what "real behavior tests" looks like:

- `tests/deployment/test_phase_runner_story528_505_guards.py` — Groups A/B/C/D/E, parametric, mocks only external I/O.
- `tests/deployment/test_phases_large_scope.py` — contract tests against module constants.
- `tests/deployment/test_dispatch_poller_needs_info_guard.py` — decision-matrix tests.

Follow the same style: `@pytest.mark.parametrize` for tables, one class per behavior group, fixtures for mock setup, assertions against side effects and return values — not against string templates.

## 8. Target Branch

`main` (tech-dev-agents).

## 9. Dependencies

- PR #100 must be merged before Phase 8 of this story so the new `_get_remote_story_status`, phase-8 ghost-commit guard, and 3-tuple return shape are canonical.
- `credential_pool.py` and `auxiliary_client.py` live in `/opt/hermes-agent/` on the agent VMs, not in this repo. The compression-routing tests will need to mock the hermes module path or pytest-skip-if-unavailable.

## Test Criteria

Phase 7 produces `test-design.md` plus RED test modules under `tests/` covering:
- Compression routing between hermes auxiliary client and credential pool (mocked since `/opt/hermes-agent/` lives off-repo).
- 3-tuple return shape from `_get_remote_story_status` against fake GitHub states.
- Phase-8 ghost-commit guard — a claim with no new commits fails the gate.

All tests run without a live agent VM or real dispatch DB — mocks only, to stay inside the `python-tests` gating CI job.

## Validation

After Phase 8 lands:
1. `python-tests` CI is GREEN on the PR with the new module executed.
2. Contract test for phase-8 ghost-commit guard fires on a hand-crafted fixture that mimics the original STORY-506 failure pattern.
3. One manual dispatch of a tiny Small story to the live fleet completes end-to-end (claim → phase-8 → PR → /complete) without any operator intervention.
