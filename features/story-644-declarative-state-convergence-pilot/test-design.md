# Test Design — STORY-644: Declarative VM-state Convergence Pilot

## Overview

| Field | Value |
|-------|-------|
| Story | STORY-644 |
| Scope | Medium |
| Coverage Target | 80% (all 5 check categories + orchestration layer) |
| Frontend | No |
| External API | No (VM-local only — Gate 2a not applicable) |
| Test File | `tests/deployment/test_converge.py` |
| Stub File | `deployment/vm/converge.py` (Phase 7 skeleton) |
| Package Init | `deployment/vm/__init__.py` (new — makes deployment.vm importable) |
| Total Tests | 18 |
| Groups | 6 (A–F) |
| RED State | **18 FAIL / 0 PASS** — confirmed `python3 -m pytest tests/deployment/test_converge.py` |

---

## RED State Verification

```
============================= test session starts ==============================
collected 18 items

tests/deployment/test_converge.py FFFFFFFFFFFFFFFFFF   [100%]

18 failed in 0.16s
```

All 18 tests FAIL (not ERROR). The stub `converge.py` provides importable interfaces with stubbed bodies:
- Check classes: `check()` returns `[CheckResult(..., status="skipped")]` — assertions on `drift_detected`/`ok`/`fixed` FAIL.
- `fix()`: returns results unchanged — assertions on `status == "fixed"` FAIL.
- `ConvergeRunner.run()`: returns empty 0-checked report — assertions on checked counts and fix calls FAIL.
- `main()`: always returns 0 — assertions on exit codes 2 and 3 FAIL.
- `resolve_agent()`: returns stub email — email equality assertion FAILS.
- `render_per_agent()`: returns value unchanged — placeholder assertion FAILS.

---

## Test Matrix

| Group | ID | Test Name | Type | RED Reason |
|-------|----|-----------|------|------------|
| A: git_config | T1 | `test_git_config_drift_detected_and_fixed` | Unit (mock subprocess + MemFsAdapter) | stub returns `skipped`, not `drift_detected` |
| A: git_config | T2 | `test_git_config_already_canonical` | Unit (mock subprocess) | stub returns `[skipped]`; assert all-ok on non-empty list FAILS (skipped ≠ ok) |
| A: git_config | T12 | `test_per_agent_email_resolution_from_registry` | Unit (mock env) | stub resolve_agent returns `stub@example.com`; render_per_agent doesn't substitute |
| B: apparmor | T3 | `test_apparmor_profile_missing_installs_and_reloads` | Unit (MemFsAdapter + mock run) | stub returns `skipped`; no file written, no parser called |
| B: apparmor | T4 | `test_apparmor_profile_already_loaded_is_noop` | Unit (MemFsAdapter) | stub returns `skipped`; assert all-ok FAILS (skipped ≠ ok) |
| C: required_cli | T5 | `test_required_cli_missing_logged_no_fix` | Unit (mock run) | stub returns `skipped`, not `drift_detected` |
| C: required_cli | T6 | `test_required_cli_version_below_min` | Unit (mock run) | stub returns `skipped`; drift_value=None |
| D: code_parity | T7 | `test_code_deploy_parity_matches` | Unit (MemFsAdapter) | stub returns `skipped`, not `ok`; assert on non-empty results FAILS |
| D: code_parity | T8 | `test_code_deploy_parity_drift_detected_no_fix` | Unit (MemFsAdapter) | stub returns `skipped`; drift_value/fixed_to None |
| E: hermes_cron | T9 | `test_hermes_cron_enabled_job_disabled_by_fix` | Unit (MemFsAdapter) | stub returns `skipped`; file not rewritten |
| F: orchestration | T10 | `test_yaml_missing_exits_2` | CLI (tmp_path) | stub main() returns 0; expected 2 |
| F: orchestration | T11 | `test_yaml_unknown_version_exits_2` | CLI (tmp_path) | stub main() returns 0; expected 2 |
| F: orchestration | T13 | `test_idempotency_two_consecutive_runs` | Unit (ConvergeRunner) | stub runner returns drifts=0; first run should show fixes |
| F: orchestration | T14 | `test_summary_line_format` | Unit (ConvergeRunner) | stub runner reports checked=0; expected ≥1 |
| F: orchestration | T15 | `test_dry_run_never_calls_fix` | Unit (mock check) | stub runner never calls check(); assert_called_once FAILS |
| F: orchestration | T16 | `test_check_filter_runs_only_named` | Unit (mock checks) | stub runner never calls check(); assert_called_once FAILS |
| F: orchestration | T17 | `test_wall_budget_exhausted_exits_3` | Unit (injected now) | stub runner returns exit_code=0; expected 3 |
| F: orchestration | T18 | `test_agent_not_in_applies_to_exits_2` | CLI (tmp_path + env) | stub main() returns 0; expected 2 |

---

## Group Details

### Group A: git_config (T1, T2, T12)

All use `MemFsAdapter` populated with a fake `.git/config` path under a search root. Subprocess is mocked via `ctx.run` — no real git invocations.

**T1 — `test_git_config_drift_detected_and_fixed`**
- Arrange: MemFsAdapter with `/home/hermes/workspace/tech-dev-agents/.git/config`; mock run returns narrow refspec on first call, wildcard on re-check
- Act: `check.check(ctx, cfg_block)` → expect `drift_detected`; then `check.fix(ctx, drifts, cfg)` → expect `fixed`
- Assert:
  - `any(r.status == "drift_detected" for r in check_results)`
  - `any(r.status == "fixed" for r in fix_results)`

**T2 — `test_git_config_already_canonical`**
- Arrange: mock run returns wildcard refspec immediately
- Act: `check.check(ctx, cfg_block)`
- Assert:
  - `results` is non-empty (at least one result per repo)
  - `all(r.status == "ok" for r in results)`

**T12 — `test_per_agent_email_resolution_from_registry`**
- Arrange: AGENT_NAME=daisy in env; REGISTRY_CONTENT fixture
- Act: `resolve_agent({"AGENT_NAME": "daisy"}, "vm-daisy-dev", registry)` → `render_per_agent("{{ agent.email }}", agent)`
- Assert:
  - `agent.email == "tech-agent-daisy@gorillacommerce.co"`
  - `agent.name == "daisy"`
  - `render_per_agent("{{ agent.email }}", agent) == "tech-agent-daisy@gorillacommerce.co"`

---

### Group B: apparmor_profiles (T3, T4)

Uses MemFsAdapter to simulate `/etc/apparmor.d/bwrap` (absent or present) and `/repo/deployment/vm/apparmor-bwrap.conf` (always present). `requires_root: False` in cfg to avoid EUID checks in tests.

**T3 — `test_apparmor_profile_missing_installs_and_reloads`**
- Arrange: MemFsAdapter has source; target absent; mock run records reload calls
- Act: `check.check(ctx, cfg_block)` → `drift_detected`; `check.fix(ctx, drifts, cfg)`
- Assert:
  - `any(r.status == "drift_detected" for r in check_results)`
  - `fs.exists(target_path)` after fix
  - `fs.get(target_path) == APPARMOR_CONTENT`
  - `any("apparmor_parser" in str(cmd) for cmd in reload_calls)`
  - `any(r.status == "fixed" for r in fix_results)`

**T4 — `test_apparmor_profile_already_loaded_is_noop`**
- Arrange: both source and target in MemFsAdapter with identical content
- Act: `check.check(ctx, cfg_block)`
- Assert:
  - `results` non-empty
  - `all(r.status == "ok" for r in results)`
  - `mock_run.assert_not_called()`

---

### Group C: required_cli (T5, T6)

Detection-only checks. `ctx.run` is mocked to simulate `which` and `--version` calls.

**T5 — `test_required_cli_missing_logged_no_fix`**
- Arrange: mock run returns exit code 1 for which (binary absent)
- Act: `check.check(ctx, cfg_block)` → `fix(ctx, drift_results, cfg)`
- Assert:
  - `any(r.status == "drift_detected" for r in results)` (binary missing)
  - `check.detection_only is True`
  - `not any(r.status == "fixed" for r in fix_results)` (no fix permitted)

**T6 — `test_required_cli_version_below_min`**
- Arrange: mock run: which succeeds; `codex --version` → "0.120.0"
- Act: `check.check(ctx, cfg_block)`
- Assert:
  - `any(r.status == "drift_detected" for r in results)`
  - `drift.drift_value is not None`
  - `"0.120" in drift.drift_value`

---

### Group D: code_deploy_parity (T7, T8)

MemFsAdapter holds both the manifest JSON and the /opt/agent file content. md5 is computed by MemFsAdapter from stored content.

**T7 — `test_code_deploy_parity_matches`**
- Arrange: MemFsAdapter with file content + manifest showing matching md5
- Act: `check.check(ctx, cfg_block)`
- Assert:
  - `results` non-empty
  - `all(r.status == "ok" for r in results)`

**T8 — `test_code_deploy_parity_drift_detected_no_fix`**
- Arrange: MemFsAdapter with file content + manifest with a different md5 hex
- Act: `check.check(ctx, cfg_block)` → `fix(ctx, drifts, cfg)`
- Assert:
  - `any(r.status == "drift_detected" for r in results)`
  - `drift.drift_value is not None` (runtime md5)
  - `drift.fixed_to is not None` (manifest md5)
  - `check.detection_only is True`
  - `not any(r.status == "fixed" for r in fix_results)`

---

### Group E: hermes_cron_disabled (T9)

MemFsAdapter holds a jobs.json. fix() must atomically rewrite it.

**T9 — `test_hermes_cron_enabled_job_disabled_by_fix`**
- Arrange: jobs.json with one `enabled: true` job + one `enabled: false` job
- Act: `check.check(ctx, cfg_block)` → `fix(ctx, drifts, cfg)`
- Assert:
  - `any(r.status == "drift_detected" for r in check_results)`
  - After fix: `fs.get(jobs_json_path)` is non-None and parses cleanly
  - `all(not job["enabled"] for job in parsed["jobs"])`
  - `any(r.status == "fixed" for r in fix_results)`

---

### Group F: orchestration / CLI (T10, T11, T13, T14, T15, T16, T17, T18)

T10, T11, T18 use `main(argv)` with temp YAML files written by the test. T13–T17 call `ConvergeRunner.run()` directly with mock/real checks.

**T10 — `test_yaml_missing_exits_2`**
- Arrange: config path = `/tmp/no-such-file.yaml` (absent)
- Assert: `main(["--config", path]) == 2`

**T11 — `test_yaml_unknown_version_exits_2`**
- Arrange: YAML with `version: 99` written to tmp file
- Assert: `main(["--config", str(path)]) == 2`

**T13 — `test_idempotency_two_consecutive_runs`**
- Arrange: MemFsAdapter with a jobs.json that has `enabled: true`; ctx with HermesCronDisabledCheck only
- Act: `runner.run()` twice on the same ctx (MemFsAdapter is mutable — fix rewrites it)
- Assert:
  - First run: `report1.drifts > 0 or report1.fixes > 0`
  - Second run: `report2.drifts == 0` and `report2.exit_code() == 0`

**T14 — `test_summary_line_format`**
- Arrange: ConvergeRunner with 1 real check (GitConfigCheck, empty search roots)
- Act: `runner.run()` → `report.summary_line()`
- Assert:
  - `report.checked >= 1` (runner attempted at least 1 check)
  - `re.match(pattern, line)` where pattern matches the documented format
  - `"agent=daisy" in line`
  - `"checks=1" in line`

**T15 — `test_dry_run_never_calls_fix`**
- Arrange: MagicMock check whose `check()` returns a `drift_detected` result
- Act: `runner.run(dry_run=True)`
- Assert:
  - `mock_check.check.assert_called_once()`
  - `mock_check.fix.assert_not_called()`
  - `report.exit_code() == 1` (drift present, unfixed)

**T16 — `test_check_filter_runs_only_named`**
- Arrange: Two mock checks (git_config, apparmor_profiles); runner has both
- Act: `runner.run(only={"git_config"})`
- Assert:
  - `git_check.check.assert_called_once()`
  - `apparmor_check.check.assert_not_called()`

**T17 — `test_wall_budget_exhausted_exits_3`**
- Arrange: ConvergeContext with injected `now` Callable that returns 0 then 200 (past 120s budget); slow_check that returns OK but is called after the time jump
- Act: `runner.run()`
- Assert: `report.exit_code() == 3`

**T18 — `test_agent_not_in_applies_to_exits_2`**
- Arrange: YAML with `applies_to: [dan, daisy]`; env var AGENT_NAME=random-vm
- Assert: `main(["--config", str(path)]) == 2`

---

## Infrastructure

### MemFsAdapter

In-memory filesystem substituting `FsAdapter` for all tests. Key behaviours:
- `read_text(path)` → raises `FileNotFoundError` if path absent (enables T10's real load_config path)
- `write_text(path, content)` → stores in dict (enables T9's atomic-write assertion)
- `sha256(path)` / `md5(path)` → computed from stored content (enables T3/T4/T7/T8)
- `exists(path)` / `listdir(path)` → derived from dict keys

### make_context()

Factory that creates a `ConvergeContext` with:
- Named agent (resolves preset email from a test fixture dict)
- Custom config dict (default: minimal v1 + applies_to)
- `MemFsAdapter` instance
- `MagicMock` or callable injected as `ctx.run`

### REGISTRY_CONTENT

Full agent-registry.json fixture matching production values (5 agents with emails).

---

## Files Created / Modified

| Path | Action |
|------|--------|
| `deployment/vm/__init__.py` | Created (package init — enables `deployment.vm.converge` imports) |
| `deployment/vm/converge.py` | Created (Phase 7 stub — importable skeleton, all bodies return wrong/empty values) |
| `tests/deployment/test_converge.py` | Created (18 RED tests) |

---

## Phase 8 guide

Implementation order follows the feature-spec §3 build plan:

1. **Commit 1:** `load_config` (FileNotFoundError → exit 2; version check → exit 2; `applies_to` gate → exit 2), `resolve_agent` (registry lookup + email), `render_per_agent` (placeholder substitution), `ConvergeRunner.run` skeleton (iterates checks, honours `only` filter, respects `dry_run`, tracks wall budget). T10, T11, T18 → GREEN.

2. **Commit 2:** `GitConfigCheck.check` (walk search_roots, per-key subprocess comparison) + `GitConfigCheck.fix` (`git config --replace-all`). `render_per_agent` substitution. T1, T2, T12 → GREEN.

3. **Commit 3:** `ApparmorProfileCheck.check` (sha256 comparison) + `.fix` (copy + reload). `HermesCronDisabledCheck.check` + `.fix` (atomic JSON rewrite). T3, T4, T9 → GREEN.

4. **Commit 4:** `RequiredCliCheck.check` (which + version_cmd + semver compare). `CodeDeployParityCheck.check` (md5 + manifest). T5, T6, T7, T8 → GREEN.

5. **Commit 5:** `ConvergeRunner.run` (wall_budget exit 3, `dry_run` guard, summary_line, checked counter). T13, T14, T15, T16, T17 → GREEN.

**All 18 tests GREEN after Commit 5.**
