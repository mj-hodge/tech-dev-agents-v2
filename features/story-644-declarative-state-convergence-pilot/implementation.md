# Phase 8 Implementation — STORY-644: Declarative VM-state Convergence Pilot

## Summary

| Field | Value |
|-------|-------|
| Story | STORY-644 |
| Phase | 8 (Implementation) |
| Branch | `story-644/story-644` |
| PR | #145 — feat(STORY-644): declarative VM-state convergence pilot |
| Tests | 18/18 GREEN (`tests/deployment/test_converge.py`) |
| Commits | 5 (including manifest path fix) |

---

## Deliverables

| File | Description |
|------|-------------|
| `deployment/vm/converge.py` | ~400 LOC — FsAdapter + ConvergeContext + 5 checks + ConvergeRunner + main() |
| `deployment/vm/canonical-state.yaml` | 5-category VM state declaration for all 5 agent VMs |
| `deployment/vm/run-converge.sh` | Cron wrapper — strips Foundry billing env vars, redirects to log |
| `deployment/vm/.deploy-manifest.json` | Seed manifest (6 files, real md5 hashes) — updated by push-code.sh on each deploy |
| `deployment/vm/push-code.sh` | Added converge files to deploy list + manifest writer (heredoc `__file__` bug fixed) |
| `tests/deployment/test_converge.py` | 18 unit tests covering all 5 check categories + orchestration layer |

---

## Commits

1. **`b913de9`** `phase 8(STORY-644): converge.py — full implementation, 18/18 tests GREEN`
   - Full `converge.py`: all 5 check classes (`GitConfigCheck`, `ApparmorProfileCheck`, `RequiredCliCheck`, `CodeDeployParityCheck`, `HermesCronDisabledCheck`), `ConvergeRunner`, `ConvergeReport`, `main()`
   - `FsAdapter` + injectable `ConvergeContext` for test isolation
   - 4 exit codes (0/1/2/3), wall-budget guard, dry_run, check filter, per-agent template resolution

2. **`837425a`** `phase 8(STORY-644): canonical-state.yaml — 5-category VM state declaration`
   - Full 5-category YAML for all 5 agent VMs
   - All per-agent templates (`{{ agent.email }}`, `{{ agent.name }}`)

3. **`4330c99`** `phase 8(STORY-644): run-converge.sh + push-code.sh deploy list + manifest writer`
   - `run-converge.sh`: Foundry env-strip wrapper, log redirect
   - `push-code.sh`: added `converge.py`, `canonical-state.yaml`, `run-converge.sh`, `agent-registry.json` to deploy file list
   - Manifest writer (initial version — bug present, fixed in commit 5)

4. **`85edca7`** `phase 8(STORY-644): tracking docs — phase 8 complete`
   - `.project`, `backlog.md`, `development-tasks.md` updated

5. **`1abb9f9`** `phase 8(STORY-644): fix manifest path — move to deployment/vm/ + fix heredoc __file__ bug`
   - **Root cause:** `__file__` in Python heredoc resolves to `<stdin>` → `abspath` uses CWD (repo root) instead of `SCRIPT_DIR` (`deployment/vm/`)
   - **Fix:** `python3 - "$SCRIPT_DIR"` + `sys.argv[1]` instead of `__file__`
   - Moved manifest from `.deploy-manifest.json` (root) to `deployment/vm/.deploy-manifest.json` (spec-correct location)

---

## Test Results

```
========================= 18 passed in 0.12s ==========================
tests/deployment/test_converge.py::TestGitConfigCheck::test_git_config_drift_detected_and_fixed PASSED
tests/deployment/test_converge.py::TestGitConfigCheck::test_git_config_already_canonical PASSED
tests/deployment/test_converge.py::TestGitConfigCheck::test_per_agent_email_resolution_from_registry PASSED
tests/deployment/test_converge.py::TestApparmorProfileCheck::test_apparmor_profile_missing_installs_and_reloads PASSED
tests/deployment/test_converge.py::TestApparmorProfileCheck::test_apparmor_profile_already_loaded_is_noop PASSED
tests/deployment/test_converge.py::TestRequiredCliCheck::test_required_cli_missing_logged_no_fix PASSED
tests/deployment/test_converge.py::TestRequiredCliCheck::test_required_cli_version_below_min PASSED
tests/deployment/test_converge.py::TestCodeDeployParityCheck::test_code_deploy_parity_matches PASSED
tests/deployment/test_converge.py::TestCodeDeployParityCheck::test_code_deploy_parity_drift_detected_no_fix PASSED
tests/deployment/test_converge.py::TestHermesCronDisabledCheck::test_hermes_cron_enabled_job_disabled_by_fix PASSED
tests/deployment/test_converge.py::TestOrchestration::test_yaml_missing_exits_2 PASSED
tests/deployment/test_converge.py::TestOrchestration::test_yaml_unknown_version_exits_2 PASSED
tests/deployment/test_converge.py::TestOrchestration::test_idempotency_two_consecutive_runs PASSED
tests/deployment/test_converge.py::TestOrchestration::test_summary_line_format PASSED
tests/deployment/test_converge.py::TestOrchestration::test_dry_run_never_calls_fix PASSED
tests/deployment/test_converge.py::TestOrchestration::test_check_filter_runs_only_named PASSED
tests/deployment/test_converge.py::TestOrchestration::test_wall_budget_exhausted_exits_3 PASSED
tests/deployment/test_converge.py::TestOrchestration::test_agent_not_in_applies_to_exits_2 PASSED
```

---

## Test Integrity

- [ ] No tests were modified to make them pass
- [ ] Test changes: none — all 18 tests from Phase 7 pass against the implementation as written
- [ ] All fixes applied to implementation, not tests

---

## Post-merge Deployment (feature-spec §4)

1. `./deployment/vm/push-code.sh devon` — canary deploy
2. SSH to devon: `python3 /opt/agent/converge.py --dry-run --json` — verify 5 categories
3. `python3 /opt/agent/converge.py` — first real run; expect drift fixed
4. `/schedule-cron` skill per VM: `name=converge schedule="*/30 * * * *" command="/opt/agent/run-converge.sh"`
5. Validate on all 5 VMs once devon stable for ≥1 hour

---

## Follow-ups

- `push-code.sh` sudoers rule for `apparmor_parser -r` on each VM (documented in feature-spec §7)
- STORY-645: migration auto-apply (referenced in prevention trio seed)
- STORY-646: rework-aware test fixture (referenced in prevention trio seed)
