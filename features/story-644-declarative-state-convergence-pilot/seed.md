# Seed: STORY-644 — Declarative VM-state convergence pilot

## Overview

| Field | Value |
|-------|-------|
| Mode | new_feature |
| Scope | medium |
| Frontend | false |
| Feature Name | YAML-defined canonical VM state with idempotent converge script + 30-min cron, eliminating drift bugs structurally |
| Phase Path | 1 (Seed) → 6 (Design) → 7 (Test Design) → 8 (Implementation) → Done |
| Repo | tech-dev-agents |
| Target Branch | `story-644/declarative-state-convergence-pilot` |
| Status | Seed written 2026-04-26 ~01:35 UTC |
| Priority | 100 — single highest-leverage prevention work; eliminates entire bug class |

---

## 1. Idea / Trigger

The 2026-04-25 incident (60% claim failure rate) and the running thread of bugs across the past week are dominated by **state drift**. Specific incidents:

- `remote.origin.fetch = +refs/heads/main:refs/remotes/origin/main` on daisy + devon (cloned single-branch). Caused 34 of 56 claim failures on 2026-04-25.
- Dirty `.project` files left behind on every agent's worktree (10+ per agent), blocking subsequent branch checkouts.
- AppArmor profile for `/usr/bin/bwrap` missing on agent VMs, breaking codex review (already manually fixed in `deployment/vm/apparmor-bwrap.conf`).
- `/tmp` permission quirks specific to dan (project_file.py scp fails). Some non-canonical state.
- Hermes JSON cron used instead of crontab on Morris (Foundry-billed). Migrated manually.
- Codex CLI version drift between agent VMs (some had v0.121, others not).
- `safe.directory` git config missing on certain agent worktrees (caused permission-denied during git operations).

Every one of these is **drift from a canonical state**. None would survive an hour with a periodic convergence loop in place. README's Phase 4 vision describes this exactly:

> Single YAML defines each agent VM's canonical state (poller version, claude settings, plugins, permissions, env vars, hermes crons). A converge script renders this onto each VM idempotently every 30 min. Drift can't survive an hour. Whole classes of fragility (stale-deploy, plugin misconfig, settings drift) become structurally impossible.

This story is the **pilot** — start with the highest-leverage drift categories, ship the converge mechanism, then iteratively expand the YAML in follow-up stories.

## 2. Problem Statement

- **Drift bugs are the dominant cost.** Every fragile-state issue eats hours: today's session alone burned ~6 hours of operator time and an unknown amount of agent token budget on drift-class problems.
- **No structural prevention exists.** STORY-636 (refspec converge) added a one-shot at poller startup for ONE drift category. That's not the pattern — every category needs its own ad-hoc patch unless we generalize.
- **Observability stories don't fix this.** STORY-700 (events table) tells us drift happened but doesn't prevent it.
- **Existing one-off fixes won't scale.** apparmor-bwrap.conf, the converge_repo_config helper, manual git-stash cleanups, manual VM-by-VM fixes — accumulating ad-hoc remediation. A proper YAML-defined canonical state plus single converge script subsumes all of these.

## 3. Scope Classification

**Medium.** Phase path 1 → 6 → 7 → 8 → Done.

Files touched:
- `deployment/vm/canonical-state.yaml` (new) — the canonical state declaration
- `deployment/vm/converge.py` (new) — the converge engine (reads YAML, asserts state, fixes drift idempotently)
- `tests/deployment/test_converge.py` (new) — unit tests for each converge action
- A crontab entry per agent VM via the `/schedule-cron` skill (will be done as part of Phase 8 deployment)

No schema, no DB migration, no API. Stays in `deployment/vm/`.

## 4. Codebase Context

### What already exists (lessons learned for the converge mechanism)

- **`converge_repo_config()` in `deployment/hermes/dispatch_poller.py`** (PR #128, STORY-636): runs at poller startup, walks repos, asserts `remote.origin.fetch = +refs/heads/*:refs/remotes/origin/*`. Logs every drift. **This is the right pattern but limited to one config and one trigger time.** STORY-644 generalizes: YAML-driven, scheduled, multi-category.

- **`deployment/vm/apparmor-bwrap.conf`**: declarative AppArmor profile. Already shipped. Fold the install of this file into the converge script (idempotent: check `/etc/apparmor.d/bwrap` exists with matching content; install + reload if not).

- **`/schedule-cron` skill at `.sdlc/skills/schedule-cron/SKILL.md`**: how the converge script gets scheduled on each VM. Use this skill directly during Phase 8 deployment.

- **Existing systemd units** (e.g., `dispatch-poller.service`): the converge script must NOT touch active services unless the YAML says to (e.g., a `services_required: dispatch-poller` directive could verify it's running but a "restart if not" should be opt-in to avoid recovery cascades during incidents).

### What converge MUST handle in the pilot

These are the highest-frequency drift categories from the past week's incidents:

1. **Git config across all clones**:
   - `remote.origin.fetch = +refs/heads/*:refs/remotes/origin/*`
   - `user.email = <agent-bot-email>` (from agent-registry.json)
   - `user.name = <agent-display-name>`
   - `safe.directory = *` (or specific paths)
   - `core.autocrlf = false` (Linux agents)

2. **AppArmor profile for bwrap** (file content + parser-loaded state)

3. **Required CLI tools present** with min versions:
   - `gh` ≥ 2.40
   - `codex` ≥ 0.121 (the `--approval-mode full-auto` removal version)
   - `ccusage` (any version)
   - `claude` (Claude Code SDK — present in PATH)
   - `node` ≥ 20
   - `python3` ≥ 3.10

4. **Code deploy parity** — md5 of `/opt/agent/dispatch_poller.py`, `/opt/agent/sdlc_phase_runner.py`, `/opt/agent/claude_sdk_tool.py` matches the latest `main` HEAD's tree. If diverged, log a structured event (do NOT auto-replace — that's push-code.sh's job; this is detection only for the pilot).

5. **No Foundry-billed hermes JSON cron entries enabled** (assert `~/.hermes/cron/jobs.json` has no enabled jobs; legacy was deprecated 2026-04-24).

### Out of scope for the pilot (future stories)

- Replacing /opt/agent code automatically (requires push-code.sh integration; defer).
- Settings.json hash enforcement (requires careful per-agent customization vs canonical split; defer).
- Plugin list enforcement.
- Database schema verification (STORY-645 territory).
- Hermes scheduled cron drift detection (verify a known set of cron entries exist; defer).
- Health-check enforcement (probe each service; defer).

## 5. Design considerations (Phase 6 will deepen)

### YAML schema (proposed)

```yaml
version: 1
applies_to:
  - dan
  - derrick
  - daisy
  - devon
  - morris

git_config:
  scope: all_clones_under:
    - /home/hermes/workspace
    - /home/hermes/dev/hpi-gorillacommerce
  required:
    "remote.origin.fetch": "+refs/heads/*:refs/remotes/origin/*"
    "core.autocrlf": "false"
    "safe.directory": "*"
  per_agent:
    user.email: "{{agent_email}}"   # resolved from agent-registry.json
    user.name: "{{agent_name}}"

apparmor_profiles:
  - path: /etc/apparmor.d/bwrap
    content_source: deployment/vm/apparmor-bwrap.conf
    reload_command: apparmor_parser -r /etc/apparmor.d/bwrap

required_cli:
  - name: gh
    min_version: "2.40"
  - name: codex
    min_version: "0.121"
  - name: ccusage
  - name: claude
  - name: node
    min_version: "20"
  - name: python3
    min_version: "3.10"

code_deploy_parity:
  detection_only: true   # log drift, do NOT auto-fix in pilot
  files:
    - /opt/agent/dispatch_poller.py
    - /opt/agent/sdlc_phase_runner.py
    - /opt/agent/claude_sdk_tool.py

hermes_cron_disabled:
  jobs_json: /home/hermes/.hermes/cron/jobs.json
  assertion: no jobs have enabled=true   # crontab is the canonical path
```

### converge.py shape

- Read YAML.
- For each declared category, run a check function.
- If state matches: log `OK` line.
- If state drifts: log `DRIFT_DETECTED` with structured details, then run fix function (or skip-with-warning if `detection_only: true`).
- After fix: re-check; log `FIXED` or `FIX_FAILED`.
- Emit a single summary line: `converge: 8 checked, 0 drifts, 0 fixes` (the steady-state expectation).
- Idempotent. Re-running back-to-back must produce the same `OK` outcome.

### Scheduling

- Add a crontab entry on each agent VM via `/schedule-cron` skill: `every-30m converge.py`.
- The cron wrapper (`run_cron.sh`) already strips Foundry env vars (subscription billing).
- Output goes to `/var/log/<agent>/converge.log` per the wrapper's standard.

## 6. Out of Scope

- Cross-VM convergence orchestration (one VM converging another) — each VM converges itself.
- Self-healing of the converge script itself — if converge.py is broken, push-code.sh fixes it.
- A web UI for canonical state visualization — defer to a Phase 3 status surface story.
- Replacing `push-code.sh` — that script remains the deploy mechanism; converge is detection-and-config-fix only.
- Auto-opening PRs to update canonical-state.yaml when drift is detected — the YAML is human-curated; new categories get added by humans after retrospectives.

## 7. Test Criteria

Phase 7 produces `test-design.md` and RED tests at `tests/deployment/test_converge.py` covering:

| Test | Setup | Expectation |
|---|---|---|
| Git config drift detected and fixed | mock repo with `+refs/heads/main:refs/remotes/origin/main` | converge logs DRIFT, runs `git config --replace-all`, re-check OK |
| Git config already canonical | mock repo with wildcard refspec | converge logs OK, no fix attempted |
| AppArmor profile missing | mock `/etc/apparmor.d/bwrap` absent | converge installs from deployment/vm/apparmor-bwrap.conf, runs apparmor_parser, re-check OK |
| AppArmor profile already loaded with matching content | mock present + same content | converge logs OK, no parser reload |
| Required CLI missing | mock `which codex` returning empty | converge logs DRIFT_DETECTED, no fix in pilot (detection only for CLI versioning), structured event |
| Required CLI version too low | mock `codex --version` returning v0.120 | log DRIFT, no fix |
| Code deploy parity matches | mock md5 of /opt/agent files == HEAD tree | log OK |
| Code deploy parity drifted | mock md5 differs | log DRIFT_DETECTED, no auto-fix in pilot |
| Hermes cron has enabled job | mock jobs.json with `enabled: true` | log DRIFT, optionally disable (or detection-only) |
| YAML missing | configuration file absent | log error, exit 2 (don't silently skip) |
| Idempotency (run twice back-to-back) | converge returns OK | second run: all OK, no fixes |
| Per-agent email resolution from agent-registry | mock current hostname=daisy | git user.email = `tech-agent-daisy@gorillacommerce.co` |

All tests are unit-level with mocked subprocess and filesystem. No live VM required.

## 8. Validation

After Phase 8 lands + push-code.sh + crontab schedule:

1. Run converge.py on a deliberately-broken VM (e.g., reset `remote.origin.fetch` on a test repo) — verify drift detected, fix applied, re-check OK.
2. Crontab entry exists on all 5 VMs (4 dev + Morris): `crontab -l | grep converge`.
3. journalctl shows `converge: N checked, 0 drifts` lines every 30 min in steady state.
4. Manually break a category (e.g., remove apparmor-bwrap) — within 30 min, converge restores it; structured drift event recorded.

## Test Criteria
- `deployment/vm/converge.py` unit tests pass (18/18)
- `converge.py --dry-run` on each agent VM reports correct drift detection
- No VM state changes occur outside declared canonical-state.yaml categories

## Validation
- Run `python deployment/vm/converge.py --dry-run` on all 5 VMs and verify output matches expected state
- Confirm cron job runs every 30 minutes via `systemctl status vm-converge.timer`
- Verify idempotent: second run within 1 minute produces no changes

## 9. Dispatch Notes

- Repo: tech-dev-agents
- Branch: `story-644/declarative-state-convergence-pilot`
- Scope: medium (5 deliverables: seed.md, feature-spec.md, test-design.md + impl + tests)
- Priority: 100 (single highest-leverage prevention work)
- Expected runtime: Phase 6 ~25 min, Phase 7 ~15 min, Phase 8 ~45 min
- Implementing agent should: (a) read this seed and the existing `converge_repo_config()` in `dispatch_poller.py` for inspiration, (b) design the YAML schema in Phase 6 with extensibility in mind (next stories will add categories), (c) keep all checks idempotent (Phase 8), (d) cover all 12 test cases.
