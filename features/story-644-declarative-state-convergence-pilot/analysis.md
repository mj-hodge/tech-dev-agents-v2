# Analysis — STORY-644: Declarative VM-state Convergence Pilot

## Overview

| Field | Value |
|-------|-------|
| Story | STORY-644 |
| Scope | Medium |
| Phase | 1 (Seed) analysis — retroactive write for SDLC compliance |
| Author | Phase runner (retry 3) |

---

## 1. Problem Analysis

### Root Cause

The dominant bug class across the week of 2026-04-19 to 2026-04-25 is **VM state drift**. Specific incidents:

- **Git refspec drift** (34/56 claim failures on 2026-04-25): `remote.origin.fetch = +refs/heads/main:refs/remotes/origin/main` on daisy + devon (cloned single-branch). Fixed one-off by STORY-636's `converge_repo_config()` at poller startup — but only for that one config key.
- **Dirty `.project` files** blocking branch checkouts on all agent worktrees.
- **Missing AppArmor profile** for bwrap on agent VMs, breaking codex review.
- **`/tmp` permissions** on dan causing scp failures.
- **Legacy hermes JSON cron** on Morris (Foundry-billed).
- **Codex CLI version drift** between VMs.
- **Missing `safe.directory`** git config on certain agent worktrees.

### Impact

Every drift incident consumed 1–6 hours of operator time for triage and manual remediation. The 2026-04-25 incident alone caused 60% claim failure rate. No structural prevention existed — each drift category required a new ad-hoc fix.

### Why Existing Solutions Are Insufficient

| Approach | Limitation |
|----------|-----------|
| STORY-636 `converge_repo_config()` | Single config key, single trigger time (poller startup), not extensible |
| Manual `apparmor-bwrap.conf` install | One-off; no re-convergence if removed |
| Manual git-stash cleanups | Per-VM, per-incident |
| STORY-700 events table | Observability only — detects drift after the fact, doesn't prevent/fix |

### Structural Solution

A **YAML-defined canonical state** + **single idempotent converge script** + **30-min cron schedule** eliminates the entire drift bug class. Any configuration that drifts is detected and (where safe) auto-corrected within one cron cycle. Detection-only mode covers categories where auto-fix is risky (CLI versions, code deploy parity).

---

## 2. Scope Assessment

**Medium scope** is correct:
- 3 new files (`canonical-state.yaml`, `converge.py`, `test_converge.py`) + 2 supporting files (`run-converge.sh`, `.deploy-manifest.json`)
- Minor modification to `push-code.sh` (deploy list + manifest writer)
- No database, no API, no frontend, no schema migration
- Stays entirely within `deployment/vm/`
- Phase path: 1 → 6 → 7 → 8 → Done

---

## 3. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| converge.py breaks a working VM | Low | High | Idempotent design; fix() never called if check() returns OK; detection-only for risky categories |
| AppArmor reload causes service disruption | Low | Medium | Only reloads if content actually changed (sha256 comparison); requires_root guard |
| Cron runs during active agent work | Medium | Low | converge is read-mostly; git config changes are atomic; no service restarts in pilot |
| YAML schema evolution breaks old converge.py | Low | Medium | Version field with explicit supported-set check; unknown versions exit 2 |
| Wall-clock budget exceeded in large repos | Low | Low | 120s hard budget with exit 3; partial results emitted |

---

## 4. Dependencies

- **PyYAML**: sole non-stdlib dependency. Already available on all agent VMs (`apt install python3-yaml`).
- **agent-registry.json**: provides per-agent email/name for git config templates. Already maintained in repo.
- **push-code.sh**: must be updated to deploy converge files and write the deploy manifest. Backward-compatible change.
- **`/schedule-cron` skill**: used post-merge to install the cron entry on each VM. Not a code dependency.

---

## 5. Alternatives Considered

| Alternative | Rejected Because |
|-------------|-----------------|
| Ansible playbook per VM | Over-engineered for 5 VMs; adds Ansible dependency; harder to test in-repo |
| systemd unit with `ExecStartPre` checks | Couples convergence to service lifecycle; doesn't cover non-service drift |
| Git hooks for config enforcement | Only fires on git operations; doesn't catch drift that happens between operations |
| Expand STORY-636 pattern per-category | Would require ad-hoc code in multiple modules; no central YAML source of truth |

---

## 6. Decision

Proceed with the YAML + converge.py + cron approach as described in the seed. This is the highest-leverage prevention work available — it eliminates an entire class of bugs structurally rather than fixing them one by one.
