# Code Review — STORY-644: Declarative VM-state Convergence Pilot

## Overview

| Field | Value |
|-------|-------|
| Story | STORY-644 |
| Phase | Code Review (retroactive — PR #145 already merged) |
| PR | #145 — feat(STORY-644): declarative VM-state convergence pilot |
| Branch | `story-644/story-644` → `main` |
| Files Reviewed | `deployment/vm/converge.py` (~1075 LOC), `deployment/vm/canonical-state.yaml`, `deployment/vm/run-converge.sh`, `tests/deployment/test_converge.py` (~908 LOC) |
| Tests | 18/18 GREEN |
| Verdict | **APPROVED** — implementation is solid for a pilot; follow-up items noted below |

---

## 1. Architecture & Design

### Strengths

- **Clean separation of concerns**: `Check` protocol with `check()` (pure read) and `fix()` (mutation) is well-structured and extensible. Adding a new check category requires only a new class + YAML stanza.
- **Dependency injection**: `FsAdapter`, `ctx.run`, and injectable `now()` callable make the entire engine testable without any real filesystem or subprocess access. This is the right pattern.
- **Frozen dataclasses**: `CheckResult`, `AgentContext`, `ConvergeReport` are all immutable — prevents accidental mutation of results between check and reporting phases.
- **Idempotency by design**: check-then-fix-then-recheck pattern ensures fix() is only called on actual drift, and success is confirmed. Re-running produces all-OK with no side effects.
- **Structured logging**: Every non-OK result produces a JSON event line with consistent schema. Cron output is directly consumable by Loki/Grafana.
- **Exit code semantics**: 0/1/2/3 exit codes encode outcome precisely. Cron monitoring can alert on non-zero without parsing output.
- **Wall-clock budget**: Hard 120s timeout prevents runaway convergence from blocking subsequent cron ticks.

### Trade-offs Accepted

- **Single-file module** (~1075 LOC): Acceptable for 5 check categories. The spec notes refactoring into a package if the registry grows past 8-10 categories.
- **PyYAML dependency**: Only non-stdlib dependency. Already available on all VMs. Acceptable for the pilot.
- **MD5 for deploy parity**: Cryptographically weak but used only for integrity detection (not security). SHA256 would be better; noted as follow-up.

---

## 2. Check Category Review

### GitConfigCheck
- Correctly walks `search_roots` and checks `.git/` directories
- Handles `required` (single-value) and `required_multivalue` keys separately
- `--replace-all` is correct for single-value fix; `--add` for multivalue
- Per-agent template resolution via `{{ agent.email }}` / `{{ agent.name }}` works correctly
- **Note**: No validation that git config keys are valid git configuration keys — acceptable for pilot since YAML is human-curated

### ApparmorProfileCheck
- SHA256 comparison for content check is correct
- `requires_root` guard prevents futile fix attempts when running as non-root
- **Observation**: File write is not atomic (no temp+rename). Low risk since cron runs every 30 min and partial write would be detected on next tick.

### RequiredCliCheck
- Detection-only (correct decision for pilot — auto-install is complex)
- Version comparison via tuple of ints is sufficient (avoids `packaging.version` dependency)
- Handles presence-only checks (no `min_version`) correctly

### CodeDeployParityCheck
- Detection-only (correct — fix path is push-code.sh)
- Reads manifest from `.deploy-manifest.json` written by push-code.sh
- Handles missing manifest gracefully (logs drift, doesn't crash)

### HermesCronDisabledCheck
- Only non-detection-only check besides git_config and apparmor
- Rewrites jobs.json with all `enabled: false` — correct and reversible
- Handles missing jobs.json as OK (legacy file already removed)

---

## 3. Test Assessment

### Coverage Summary

| Group | Tests | Categories Covered | Verdict |
|-------|-------|--------------------|---------|
| A: git_config | T1, T2, T12 | drift+fix, already-canonical, per-agent template | Adequate |
| B: apparmor | T3, T4 | missing+install, already-loaded noop | Adequate |
| C: required_cli | T5, T6 | missing binary, version below min | Adequate |
| D: code_parity | T7, T8 | matching hashes, drift detected | Adequate |
| E: hermes_cron | T9 | enabled→disabled fix | Adequate |
| F: orchestration | T10-T18 | YAML errors, idempotency, dry-run, filter, wall-budget, agent gating | Good |

### Test Quality

- All tests use `MemFsAdapter` and mock subprocess — no real filesystem or network access
- Tests are focused and well-named; each tests exactly one behavior
- RED-then-GREEN methodology confirmed (Phase 7 produced all-RED stubs, Phase 8 turned them green)

### Coverage Gaps (acceptable for pilot, noted for follow-ups)

1. **No multivalue git config test** — `required_multivalue` (e.g., `safe.directory: ["*"]`) is untested
2. **No test for AppArmor reload_command failure** — what happens if `apparmor_parser -r` returns non-zero
3. **No test for missing deploy manifest** — `CodeDeployParityCheck` when `.deploy-manifest.json` is absent
4. **No test for `HermesCronDisabledCheck` when jobs.json is absent** — should return OK
5. **No test for check() raising an unexpected exception** — runner's broad catch is untested

---

## 4. Security Review

### YAML Safety
- Uses `yaml.safe_load()` — correct. No deserialization of arbitrary Python objects.
- Unknown top-level keys are rejected (explicit schema evolution).
- Per-agent placeholders are resolved by string substitution in Python — no shell expansion.

### Subprocess Safety
- All subprocess calls use list-form arguments — no shell injection possible.
- `reload_command` from YAML is passed as a list to `ctx.run()` — safe as long as YAML is trusted (it is: committed to git, human-curated).

### Filesystem Safety
- AppArmor profile writes target paths from YAML config — constrained by the YAML being a trusted, version-controlled file.
- `requires_root` guard prevents fix attempts when EUID != 0 (except in cron where it runs as root via the wrapper).

### Risk Assessment
- **Low risk**: YAML is human-curated and checked into git. Attack surface requires repo write access (which already implies full VM access).
- **Acceptable for pilot**: No secrets in YAML. No network calls. No database access.

---

## 5. Operational Readiness

### Deployment Path
- `push-code.sh` updated to deploy `converge.py`, `canonical-state.yaml`, `run-converge.sh`, and `agent-registry.json` to `/opt/agent/`
- `run-converge.sh` strips Foundry billing env vars (correct — prevents converge from being billed)
- Cron scheduling via `/schedule-cron` skill (post-merge manual step)

### Observability
- Structured JSON events on stdout → piped to log file by cron wrapper → tailed by promtail → Loki/Grafana
- Summary line per tick: `converge: agent=X schema=v1 checks=5 resources=N ok=N drift=N fixed=N fix_failed=N elapsed=Xs`
- Non-zero exit code → cron monitoring alerts

### Rollback
- Remove cron entry (`crontab -l | grep -v converge | crontab -`) or delete `/etc/cron.d/converge-tech-dev-agents`
- converge.py is inert without the schedule

---

## 6. Follow-up Items

| Priority | Item | Reason |
|----------|------|--------|
| Low | Atomic file writes for AppArmor profile | Temp+rename prevents partial-write window |
| Low | Switch MD5 to SHA256 for deploy parity | MD5 is cryptographically weak (though only used for integrity) |
| Low | Add tests for multivalue git config | Expand test coverage for `required_multivalue` |
| Low | Add test for missing deploy manifest | Edge case in `CodeDeployParityCheck` |
| Low | Refactor to package if checks > 8 | Single-file module is fine for 5 checks |

---

## 7. Verdict

**APPROVED.** The implementation correctly fulfills the seed requirements:

- 5 drift categories covered (git_config, apparmor, required_cli, code_deploy_parity, hermes_cron)
- Idempotent convergence loop with structured logging
- 18/18 tests GREEN with good coverage of happy paths and key error paths
- Clean separation of detection-only vs. auto-fix categories
- Proper exit code semantics and wall-clock budget enforcement
- Deployment-ready with cron wrapper and push-code.sh integration

The implementation quality is appropriate for a pilot. The follow-up items are all low-priority enhancements that can be addressed as the check registry grows.
