# Feature Spec — STORY-644: Declarative VM-state convergence pilot

## Overview

| Field | Value |
|-------|-------|
| Story | STORY-644 |
| Scope | Medium |
| Approach | YAML-driven canonical state + idempotent converge engine + 30-min cron |
| Frontend | No |
| API Contract Changes | None — VM-local script only |
| Repo | tech-dev-agents |
| Branch | `story-644/story-644` (worktree branch); seed referenced `story-644/declarative-state-convergence-pilot` (alias) |

This spec instantiates the seed's design considerations as a concrete, testable, deployable pilot. The key architectural choice — confirmed in the seed §5 — is a **registry of self-contained checks**, each declaring `name`, `detection_only`, `check()`, and `fix()` semantics. The pilot ships **5 categories** (git_config, apparmor, required_cli, code_deploy_parity, hermes_cron_disabled) covering every drift incident from the past week. Adding the 6th category in a follow-up story means: append one YAML stanza + one Python class to the registry, and bump the YAML schema `version` minor.

`converge_repo_config()` (PR #128, STORY-636) is the **direct ancestor** of this work. STORY-644 generalizes it: same logging discipline, same per-repo subprocess pattern, but YAML-driven, scheduled, multi-category, and called from cron rather than at poller startup. STORY-636's startup call **stays in place** — it's the boundary fix; the cron is the steady-state convergence loop.

---

## 1. Components

### 1.1 `deployment/vm/canonical-state.yaml`

The **single source of truth** for what an agent VM should look like. Human-curated, checked into git, deployed alongside `converge.py`.

```yaml
# deployment/vm/canonical-state.yaml
# Canonical state for tech-dev-agents VMs (dan, derrick, daisy, devon, morris).
# Edited by humans. Loaded by converge.py every 30 min (cron) on each agent VM.
#
# Schema version. Bump on backwards-incompatible changes; converge.py asserts
# its supported set.
version: 1

# Agents this file applies to. converge.py resolves the running agent via
# AGENT_NAME env var → hostname → /opt/agent/.env. Aborts if not in this list
# (prevents converge from running on a VM that isn't a target).
applies_to:
  - dan
  - derrick
  - daisy
  - devon
  - morris

# ---------- Category 1: git_config ----------
# Walks every git clone under search_roots and asserts each (key, value) pair.
# Per-agent values resolved from agent-registry.json by hostname/AGENT_NAME.
# Idempotent fix: `git config --replace-all <key> <value>` per repo.
git_config:
  search_roots:
    - /home/hermes/workspace
    - /home/hermes/dev/hpi-gorillacommerce
  required:
    remote.origin.fetch: "+refs/heads/*:refs/remotes/origin/*"
    core.autocrlf: "false"
  # safe.directory is multi-valued; converge ensures the listed values are
  # present (not exclusive). Use "*" to grant all paths.
  required_multivalue:
    safe.directory:
      - "*"
  # Per-agent values pulled from deployment/vm/agent-registry.json by name.
  per_agent:
    user.email: "{{ agent.email }}"
    user.name: "tech-agent-{{ agent.name }}"

# ---------- Category 2: apparmor_profiles ----------
# Files in /etc/apparmor.d/. converge compares content (sha256) against
# deployment/vm/<source>; if different or missing, copies + reloads via
# apparmor_parser -r. Skips reload if file already byte-identical.
apparmor_profiles:
  - target: /etc/apparmor.d/bwrap
    source: deployment/vm/apparmor-bwrap.conf
    reload_command: ["apparmor_parser", "-r", "/etc/apparmor.d/bwrap"]
    requires_root: true

# ---------- Category 3: required_cli ----------
# Detection only in the pilot (no auto-install). Logs DRIFT_DETECTED if
# missing or below min_version. Future story: add auto-install via apt/npm.
# Version check uses `<bin> --version` and a category-specific version_re.
required_cli:
  detection_only: true
  bins:
    - name: gh
      min_version: "2.40"
      version_cmd: ["gh", "--version"]
      version_re: 'gh version (\d+\.\d+\.\d+)'
    - name: codex
      min_version: "0.121"
      version_cmd: ["codex", "--version"]
      version_re: '(\d+\.\d+\.\d+)'
    - name: ccusage
      min_version: null  # presence-only; any version OK
      version_cmd: ["ccusage", "--version"]
      version_re: null
    - name: claude
      min_version: null
      version_cmd: ["claude", "--version"]
      version_re: null
    - name: node
      min_version: "20.0"
      version_cmd: ["node", "--version"]
      version_re: 'v(\d+\.\d+\.\d+)'
    - name: python3
      min_version: "3.10"
      version_cmd: ["python3", "--version"]
      version_re: 'Python (\d+\.\d+\.\d+)'

# ---------- Category 4: code_deploy_parity ----------
# Detection only — fix path is push-code.sh (separate concern). converge.py
# computes md5 of each file at runtime and reads the canonical hash from
# deployment/vm/.deploy-manifest.json (written by push-code.sh on each push).
# If manifest is missing OR mismatch, log DRIFT_DETECTED.
code_deploy_parity:
  detection_only: true
  manifest: deployment/vm/.deploy-manifest.json   # relative to repo root
  files:
    - /opt/agent/dispatch_poller.py
    - /opt/agent/sdlc_phase_runner.py
    - /opt/agent/claude_sdk_tool.py
    - /opt/agent/run_dispatch_poller.py
    - /opt/agent/terminal_guard.py
    - /opt/agent/project_file.py

# ---------- Category 5: hermes_cron_disabled ----------
# Asserts the legacy Hermes JSON cron is fully disabled; the canonical
# scheduling path is the system crontab (managed by /schedule-cron skill).
# Fix: rewrite jobs.json with all enabled flags set to false.
hermes_cron_disabled:
  detection_only: false
  jobs_json: /home/hermes/.hermes/cron/jobs.json
```

**Schema rules:**

- Top-level `version` is an integer. converge.py supports a fixed set (currently `{1}`); unknown versions cause `exit 2` with a clear error.
- Each category may declare `detection_only: true` to skip its `fix()` step. Missing key defaults to `false`.
- Per-agent placeholders use `{{ agent.<field> }}` and resolve from `agent-registry.json` (loaded once at startup).
- Unknown top-level keys are **rejected** (forces explicit schema evolution).

### 1.2 `deployment/vm/converge.py`

Single executable Python module. ~400 lines. Runs under the system Python (`/usr/bin/python3`, ≥3.10). Drops PyYAML as the sole non-stdlib dependency.

```python
# deployment/vm/converge.py
"""Idempotent VM-state convergence engine for tech-dev-agents.

Reads canonical-state.yaml, walks a registry of Check classes, logs every
result as a single-line structured event, and exits with a code that
encodes the worst outcome.

DESIGN CONTRACTS:
- Idempotent. Re-running back-to-back must produce identical OK results
  (no fix() side effects on a converged VM).
- Read-mostly. Each check's check() is pure (no mutation); only fix()
  mutates, and only when check() returned drift.
- Bounded. Every subprocess gets a timeout (default 10s). The whole run
  has a hard wall-clock budget (default 120s) — exceeded budget exits 3.
- Loud about drift, quiet about steady state. OK results are a single
  summary line; drift gets a structured event per affected resource.
- Never makes the situation worse. fix() failure leaves the system in
  its pre-call state and logs FIX_FAILED — it does not retry, escalate,
  or trigger downstream actions.

USAGE:
  converge.py                       # default: load canonical-state.yaml, run all checks
  converge.py --config PATH         # override YAML path
  converge.py --dry-run             # check only; never call fix()
  converge.py --check NAME [...]    # run only the named checks
  converge.py --json                # emit structured events only (no human text)

EXIT CODES:
  0  all checks OK or fixed-cleanly
  1  one or more checks logged FIX_FAILED or DRIFT_DETECTED with no fix path
  2  configuration error (YAML missing/parse-error/unknown version, agent not in applies_to)
  3  wall-clock budget exhausted
"""
```

### 1.2.1 Module structure

```
deployment/vm/converge.py
├── # Constants
│   SUPPORTED_SCHEMA_VERSIONS = {1}
│   DEFAULT_CONFIG_PATH       = "/opt/agent/canonical-state.yaml"
│   DEFAULT_REGISTRY_PATH     = "/opt/agent/agent-registry.json"
│   DEFAULT_SUBPROCESS_TIMEOUT_S = 10
│   DEFAULT_WALL_BUDGET_S        = 120
│
├── @dataclass(frozen=True) class CheckResult
│       name: str
│       resource: str              # e.g. repo path, file path, bin name
│       status: Literal["ok","drift_detected","fixed","fix_failed","skipped","error"]
│       detail: str                # human-readable
│       drift_value: str | None
│       fixed_to: str | None
│       error: str | None
│
├── @dataclass(frozen=True) class AgentContext
│       name: str                  # e.g. "daisy"
│       email: str                 # from agent-registry.json
│       hostname: str
│
├── @dataclass(frozen=True) class ConvergeContext
│       agent: AgentContext
│       config: dict[str, Any]     # parsed YAML
│       repo_root: Path             # tech-dev-agents repo root, for relative source files
│       now: Callable[[], float]   # injectable for tests
│       run: Callable[..., subprocess.CompletedProcess]  # injectable wrapper
│       fs: FsAdapter              # injectable filesystem (read/write/sha256/exists)
│
├── class FsAdapter
│       # Real filesystem; tests substitute an in-memory adapter.
│       def read_text(self, path: str) -> str: ...
│       def write_text(self, path: str, content: str) -> None: ...
│       def sha256(self, path: str) -> str | None: ...   # None if missing
│       def exists(self, path: str) -> bool: ...
│       def is_dir(self, path: str) -> bool: ...
│       def listdir(self, path: str) -> list[str]: ...
│
├── # Check registry — additive: new categories register here.
│   class Check(Protocol):
│       name: str
│       detection_only: bool
│       def applies(self, cfg_block: Any) -> bool: ...
│       def check(self, ctx: ConvergeContext, cfg_block: Any) -> list[CheckResult]: ...
│       def fix(self, ctx: ConvergeContext, results: list[CheckResult],
│                cfg_block: Any) -> list[CheckResult]: ...
│
├── class GitConfigCheck:               # category: git_config
├── class ApparmorProfileCheck:         # category: apparmor_profiles
├── class RequiredCliCheck:             # category: required_cli
├── class CodeDeployParityCheck:        # category: code_deploy_parity
├── class HermesCronDisabledCheck:      # category: hermes_cron_disabled
│
├── # Orchestration
│   class ConvergeRunner
│       def __init__(self, ctx: ConvergeContext, checks: Sequence[Check], wall_budget_s=120)
│       def run(self, only: set[str] | None = None, dry_run: bool = False) -> ConvergeReport
│
├── @dataclass(frozen=True) class ConvergeReport
│       results: tuple[CheckResult, ...]
│       checked: int
│       drifts: int
│       fixes: int
│       fix_failures: int
│       elapsed_s: float
│       def exit_code(self) -> int
│       def summary_line(self) -> str
│
├── # Logging
│   def emit_event(payload: dict[str, Any], *, json_mode: bool, sink=sys.stdout) -> None
│
├── # Config loader
│   def load_config(path: str, fs: FsAdapter) -> dict[str, Any]
│   def resolve_agent(env: Mapping[str, str], hostname: str,
│                     registry: list[dict]) -> AgentContext
│   def render_per_agent(value: str, agent: AgentContext) -> str   # {{ agent.email }} etc.
│
└── def main(argv: list[str] | None = None) -> int
```

### 1.2.2 Per-check semantics

Each check returns **one CheckResult per affected resource**. A category-level summary is computed by the runner (counts of OK/drift/fixed/fix_failed); a per-resource event is emitted for everything that isn't OK.

#### GitConfigCheck

- `applies(cfg)`: `cfg is not None`.
- `check()`:
  - For each `search_root`, list immediate child directories that contain `.git/`.
  - For each (repo, key) in `required` ∪ `per_agent`: run `git -C <repo> config --get <key>`.
    - missing or `value != expected` → `drift_detected`, `drift_value=<actual>`, `fixed_to=<expected>`.
    - else → `ok`.
  - For each (repo, key) in `required_multivalue`: run `git -C <repo> config --get-all <key>` and ensure every expected value is in the result set. Drift if any missing.
  - **Subprocess timeout 5s per call.** If a single repo errors (corrupted .git, locked index), emit one `error` result for that repo and continue.
- `fix()`:
  - For each drift result: `git -C <repo> config --replace-all <key> <expected>`.
  - For multivalue: `git -C <repo> config --add <key> <missing-value>` (idempotent — only adds missing values, doesn't dedupe).
  - On non-zero exit: `fix_failed`, propagate stderr to `error`.
  - Re-check after fix: convert `fixed` → success only if re-read confirms canonical value.

**Idempotency proof:** if all values already match canonical, `check()` returns all-OK and `fix()` is never called. The `--replace-all` semantic guarantees fix is itself idempotent — running it twice produces the same single-valued config entry.

#### ApparmorProfileCheck

- `check()`:
  - For each profile entry: compute sha256 of `target` (None if missing) and sha256 of `<repo_root>/<source>`.
  - Mismatch or missing → `drift_detected` with `drift_value="<actual_sha or 'absent'>"`.
- `fix()`:
  - `cp <source> <target>` (atomic via temp + rename).
  - Run `reload_command` via subprocess (10s timeout).
  - Requires root: when `requires_root: true` and EUID != 0, log `fix_failed` with detail `"requires root; rerun under sudo"` rather than attempting sudo internally (cron runs as root anyway; tests assert this guard).
  - Re-check sha256 after copy; emit `fixed` only on confirmed match.

#### RequiredCliCheck

- `check()`:
  - For each bin: `shutil.which(name)`. None → `drift_detected: missing`.
  - If found and `min_version`/`version_re` set: run `version_cmd`, regex-extract, compare via `packaging.version.Version` (bundled in stdlib via importlib? — actually use a tiny semver tuple compare to avoid the dependency: split on `.`, compare `tuple(int)`).
  - Below min → `drift_detected: version_below_min`, `drift_value=<actual>`, `fixed_to=>=<min>`.
- `fix()`: noop (category is `detection_only`). Emit nothing — the runner sees `detection_only=True` and skips fix entirely.
- **Why detection-only:** auto-installing CLIs introduces apt/npm/pip credentials and version-lock complexity. Pilot just surfaces drift; humans decide install path.

#### CodeDeployParityCheck

- `check()`:
  - Read `deployment/vm/.deploy-manifest.json` (committed by `push-code.sh` on every successful push). Schema:
    ```json
    {"generated_at": "2026-04-26T01:00:00Z",
     "files": {"/opt/agent/dispatch_poller.py": "<md5>", ...}}
    ```
  - For each declared file: compute md5; compare to manifest entry.
  - Manifest missing → `drift_detected: manifest_missing` (one result; no per-file drift).
  - File missing on disk → `drift_detected: file_missing`.
  - Hash mismatch → `drift_detected: stale`, `drift_value=<runtime_md5>`, `fixed_to=<manifest_md5>`.
- `fix()`: noop (detection only — push-code.sh is the fix path). Manifest writing is a small companion change to push-code.sh (see §3.5).
- **Why detection-only:** see seed §4. converge running an emergency redeploy at 30-min granularity could re-trigger the rate-limit cascades push-code.sh's --wait flag was built to avoid.

#### HermesCronDisabledCheck

- `check()`:
  - If `jobs_json` doesn't exist → `ok` (legacy file already removed).
  - Parse JSON. Find `jobs[].enabled == true` → `drift_detected` (one result per enabled job, plus a category-level summary).
- `fix()`:
  - Rewrite the file in place, setting every `enabled` to `false`.
  - Atomic write (temp + rename) to avoid partial-write windows.
  - Re-check; emit `fixed` if confirmed.
- Detection_only: `false` (this fix is mechanical and reversible).

### 1.2.3 Logging format

Every CheckResult that isn't `ok` produces a structured event line on stdout:

```json
{"ts":"2026-04-26T01:35:12Z","agent":"daisy","host":"vm-daisy-dev",
 "event":"drift_detected","check":"git_config",
 "resource":"/home/hermes/workspace/tech-dev-agents",
 "detail":"remote.origin.fetch","drift_value":"+refs/heads/main:refs/remotes/origin/main",
 "expected":"+refs/heads/*:refs/remotes/origin/*"}
```

A single summary line is always emitted last (text mode default; `--json` switches to JSON):

```
converge: agent=daisy schema=v1 checks=5 resources=14 ok=12 drift=2 fixed=2 fix_failed=0 elapsed=3.41s
```

Crontab redirects stdout to `/var/log/converge/<agent>.log` (rotated by logrotate; see §3.4). journalctl users can grep via `--json | jq` or grep raw lines.

### 1.2.4 CLI

```
usage: converge.py [-h] [--config PATH] [--registry PATH] [--dry-run]
                   [--check NAME [NAME ...]] [--json] [--detection-only-all]
                   [--wall-budget SECONDS]

Options:
  --config              Path to canonical-state.yaml. Default: /opt/agent/canonical-state.yaml.
  --registry            Path to agent-registry.json. Default: /opt/agent/agent-registry.json.
  --dry-run             Run check() but never fix(). Useful for human triage.
  --check NAME ...      Run only the named checks. Default: all.
  --json                Emit only structured events; suppress human summary.
  --detection-only-all  Override every category's fix() to no-op. For staged rollouts.
  --wall-budget         Override WALL_BUDGET_S (default 120s).
```

### 1.3 `tests/deployment/test_converge.py`

The single test module. Unit-level — no live VM, no network. Subprocess and filesystem are injected via the `ctx.run` callable and `FsAdapter`. Tests are organized by check class; one Group per category plus one cross-cutting orchestration group.

See §5 for the full 18-test matrix (12 from seed plus 6 supporting tests for the orchestrator/CLI/logging layer).

### 1.4 `deployment/vm/.deploy-manifest.json` (companion)

Generated by `push-code.sh` after a successful deploy. Map of `{abs_path: md5}` for the files copied to /opt/agent. **Not** human-edited. Committed to git only when `push-code.sh` is run via the canonical path; in CI it's regenerated, not asserted.

> **Trade-off accepted:** committing a generated artifact is normally a smell. The alternative — converge fetching the latest main HEAD tree from GitHub — adds network dependency, GitHub rate limits, and an auth path on every cron tick. The manifest-in-repo approach is cron-safe and unblocked. We re-evaluate in retrospective if it produces noisy diffs.

### 1.5 push-code.sh changes (§3.5)

A small block at the end of push-code.sh writes the manifest. Two lines: compute md5, append to the JSON. Documented in §3.5; not a separate component.

---

## 2. Data flow

```
                   ┌──────────────────────────────────┐
                   │  deployment/vm/canonical-state.yaml │  (committed, human-edited)
                   └────────────────┬─────────────────┘
                                    │ scp via push-code.sh
                                    ▼
                       /opt/agent/canonical-state.yaml
                                    │
                                    │ read each cron tick
                                    ▼
   ┌────────────────────────────────────────────────────────────┐
   │  /opt/agent/converge.py                                    │
   │                                                            │
   │   1. load_config(yaml) ──────▶ dict, validate version      │
   │   2. resolve_agent(env, hostname, registry) ──▶ AgentContext│
   │   3. for each check in registry:                           │
   │        results += check.check(ctx, cfg_block)              │
   │        if not detection_only and drift:                    │
   │            check.fix(ctx, drift_results, cfg_block)        │
   │   4. emit per-resource drift/fixed events (JSON)           │
   │   5. emit summary line                                     │
   │   6. exit(report.exit_code())                              │
   └────────────────────────────────────────────────────────────┘
                                    │
                                    │ stdout
                                    ▼
                    /var/log/converge/<agent>.log
                                    │
                                    ▼ promtail tails (existing)
                                    Loki ──▶ Grafana dashboard
```

**Trigger:** crontab entry on each agent VM, scheduled via `/schedule-cron` skill in Phase 8 deployment:

```cron
# /etc/cron.d/converge-tech-dev-agents (created by /schedule-cron)
*/30 * * * * hermes /opt/agent/run-converge.sh
```

`run-converge.sh` is a thin wrapper that strips Foundry env vars (subscription billing — same pattern as existing `run_cron.sh`) and redirects to the log:

```bash
#!/usr/bin/env bash
set -euo pipefail
unset ANTHROPIC_BEDROCK_BASE_URL CLAUDE_CODE_USE_BEDROCK AWS_REGION FOUNDRY_AGENT_ID
exec /usr/bin/python3 /opt/agent/converge.py >> /var/log/converge/$(hostname -s).log 2>&1
```

---

## 3. Implementation plan (Phase 8 build order)

Five commits, each leaves all tests GREEN. Each commit is independently reviewable.

### Commit 1 — Schema, dataclasses, runner, logging skeleton

- `deployment/vm/converge.py`: dataclasses (CheckResult, AgentContext, ConvergeContext, ConvergeReport), FsAdapter, `load_config`, `resolve_agent`, `render_per_agent`, `emit_event`, `ConvergeRunner.run` (orchestrating an empty registry), `main()` argparse boilerplate.
- `deployment/vm/canonical-state.yaml`: file as in §1.1 with all 5 categories declared.
- `tests/deployment/__init__.py` (if missing) + `tests/deployment/test_converge.py` skeleton with config-loading and CLI tests (T1, T11).
- **GREEN gate:** YAML loads, version validated, agent resolves, runner returns empty report.

### Commit 2 — GitConfigCheck

- Implement `GitConfigCheck.check()` and `.fix()`. Register in main.
- Tests T1 (drift detected + fixed), T2 (already canonical), T12 (per-agent email resolution).
- **GREEN gate:** all 3 git_config tests pass; previous tests still pass.

### Commit 3 — ApparmorProfileCheck + HermesCronDisabledCheck

- Both checks share an "edit a file on disk" pattern; bundling them keeps the commit cohesive.
- Tests T3, T4 (apparmor missing + already loaded), T9 (hermes cron drift + fix).
- **GREEN gate:** all apparmor + hermes_cron tests pass.

### Commit 4 — RequiredCliCheck + CodeDeployParityCheck

- Both are detection-only.
- Tests T5, T6 (CLI missing + version low), T7, T8 (parity match + drift).
- **GREEN gate:** detection-only categories all pass; idempotency test (T13) passes.

### Commit 5 — Wrapper script + push-code.sh manifest emission + canonical-state.yaml deployment

- Add `deployment/vm/run-converge.sh` (env-strip + log redirect).
- Add manifest writer to `push-code.sh` (post-deploy hash collection → `deployment/vm/.deploy-manifest.json`).
- Add `converge.py`, `canonical-state.yaml`, `run-converge.sh` to push-code.sh's deploy file list (`/opt/agent/`).
- Add `agent-registry.json` to push-code.sh's deploy list (currently it's read locally only; converge needs it on the VM).
- Tests: extend `test_push_code_safety.sh` to assert the manifest is written and the new files appear in the file list (T14, T15).
- **GREEN gate:** push-code.sh tests pass; full converge.py test suite green (18/18).

---

## 4. Deployment plan (post-merge)

1. **Merge PR.** Branch `story-644/story-644` → main.
2. **Push to one VM (canary).** `./deployment/vm/push-code.sh devon` (devon was second-most-broken on 2026-04-25 and easy to revert).
3. **Manual smoke.** SSH to devon: `python3 /opt/agent/converge.py --dry-run --json`. Expect 5 categories run; OK or drift detected (no fixes).
4. **Real run.** `python3 /opt/agent/converge.py`. Expect drifts fixed; re-run shows all OK.
5. **Schedule cron via `/schedule-cron` skill.** One invocation per VM:
   ```
   /schedule-cron agent=devon name=converge schedule="*/30 * * * *" \
       command="/opt/agent/run-converge.sh" rationale="STORY-644 pilot"
   ```
6. **Repeat for all 5 VMs** once devon is stable for ≥1 hour (two converge cycles).
7. **Validation gates** (all from seed §8):
   - Manually break refspec on a test repo on daisy → next cron tick fixes it; structured event in `/var/log/converge/daisy.log`.
   - `crontab -l` (or `cat /etc/cron.d/converge-tech-dev-agents`) on every VM shows the entry.
   - Steady-state: `journalctl` / log file shows `converge: ... ok=N drift=0 fixed=0` lines every 30 min.
   - Remove `/etc/apparmor.d/bwrap` on devon (test only) → next tick reinstalls + reloads.

**Rollback:** `crontab -l | grep -v converge | crontab -` per VM, or remove `/etc/cron.d/converge-tech-dev-agents`. converge.py itself is inert without the schedule.

---

## 5. Test matrix

All tests live in `tests/deployment/test_converge.py`. Every test injects `FsAdapter` and `ctx.run` so no real filesystem or subprocess is touched. **18 tests** across 6 groups (12 from seed §7 plus 6 cross-cutting).

| # | Group | Test name | Setup | Assertion |
|---|-------|-----------|-------|-----------|
| T1  | A: git_config | `test_git_config_drift_detected_and_fixed` | Mock repo with `remote.origin.fetch=+refs/heads/main:refs/remotes/origin/main` | `check()` returns drift; `fix()` calls `git config --replace-all`; re-check returns ok |
| T2  | A: git_config | `test_git_config_already_canonical` | Mock repo with wildcard refspec | `check()` returns ok for that key; `fix()` not invoked for that key |
| T3  | B: apparmor | `test_apparmor_profile_missing_installs_and_reloads` | Mock target absent; mock source content | `fix()` writes target with matching sha256, runs `apparmor_parser -r`; re-check ok |
| T4  | B: apparmor | `test_apparmor_profile_already_loaded_is_noop` | Mock target present with matching sha256 | `check()` returns ok; `fix()` and `apparmor_parser` never invoked |
| T5  | C: required_cli | `test_required_cli_missing_logged_no_fix` | Mock `which codex` returns None | `check()` returns drift_detected; runner sees detection_only; emits structured event; `fix()` not called |
| T6  | C: required_cli | `test_required_cli_version_below_min` | Mock `codex --version` → `0.120.0` | drift_detected with drift_value="0.120.0", expected=">=0.121" |
| T7  | D: code_parity | `test_code_deploy_parity_matches` | Mock manifest md5 == filesystem md5 | All ok |
| T8  | D: code_parity | `test_code_deploy_parity_drift_detected_no_fix` | Mock manifest md5 ≠ filesystem md5 | drift_detected with both md5s in payload; no fix attempted |
| T9  | E: hermes_cron | `test_hermes_cron_enabled_job_disabled_by_fix` | Mock `jobs.json` with one job `enabled:true` | `check()` returns drift; `fix()` rewrites file with `enabled:false`; re-check ok |
| T10 | F: orchestration | `test_yaml_missing_exits_2` | Config path doesn't exist | `main()` returns 2; structured error event emitted |
| T11 | F: orchestration | `test_yaml_unknown_version_exits_2` | YAML with `version: 99` | `main()` returns 2; error event mentions supported set |
| T12 | A: git_config | `test_per_agent_email_resolution_from_registry` | Hostname/AGENT_NAME=daisy; registry loaded | git config user.email = `tech-agent-daisy@gorillacommerce.co` |
| T13 | F: orchestration | `test_idempotency_two_consecutive_runs` | First run fixes drift; second run on same in-memory state | Second run: every result is ok; fix_count == 0; exit 0 |
| T14 | F: orchestration | `test_summary_line_format` | Run with mixed ok/drift results | Summary line matches regex `^converge: agent=\w+ schema=v\d+ checks=\d+ resources=\d+ ok=\d+ drift=\d+ fixed=\d+ fix_failed=\d+ elapsed=\d+\.\d+s$` |
| T15 | F: orchestration | `test_dry_run_never_calls_fix` | Drift present; `--dry-run` flag | Every `Check.fix` mock asserts `not called`; exit 1 (drift remained) |
| T16 | F: orchestration | `test_check_filter_runs_only_named` | `--check git_config` set | Only GitConfigCheck.check invoked; other checks skipped, no events emitted |
| T17 | F: orchestration | `test_wall_budget_exhausted_exits_3` | First check sleeps past wall budget | `main()` returns 3; remaining checks skipped; partial summary emitted |
| T18 | F: orchestration | `test_agent_not_in_applies_to_exits_2` | Hostname=randomvm; YAML `applies_to: [dan, daisy]` | `main()` returns 2; clear error event |

**Coverage of seed §7 12-row table:** rows 1, 2 → T1, T2; row 3, 4 → T3, T4; row 5, 6 → T5, T6; row 7, 8 → T7, T8; row 9 → T9; row 10 → T10; row 11 → T13; row 12 → T12. **All 12 covered**, plus 6 supporting tests for the orchestration layer (CLI, logging, idempotency, schema validation, agent gating, wall budget).

---

## 6. Error handling and failure modes

| Failure | Detection | Behavior | Exit code | Test |
|---------|-----------|----------|-----------|------|
| YAML missing/unreadable | `load_config` `FileNotFoundError`/`PermissionError` | Emit `error` event, exit 2 | 2 | T10 |
| YAML parse error | `yaml.YAMLError` | Emit `error` event with line if available, exit 2 | 2 | (covered by T10 family — extends easily; not a separate row) |
| Unknown schema version | post-load assertion | Emit `error` event listing supported versions, exit 2 | 2 | T11 |
| Agent not in `applies_to` | post-resolve assertion | Emit `error` event, exit 2 | 2 | T18 |
| `agent-registry.json` missing | `resolve_agent` | Emit `error` event, exit 2 | 2 | (incidental — exits before checks run) |
| Subprocess timeout in `check` | `TimeoutExpired` from `ctx.run` | Per-resource `error` result; runner continues | 1 | (asserted in T1's negative path coverage; see test file) |
| Subprocess non-zero in `fix` | `CalledProcessError` | Per-resource `fix_failed` result with stderr; runner continues | 1 | T9 negative branch |
| Wall budget exhausted | runner timer | Skip remaining checks; emit `error` event with names skipped; exit 3 | 3 | T17 |
| Permission denied on filesystem fix | `OSError` in `fs.write_text` or `apparmor_parser` non-zero with EUID!=0 | `fix_failed`; runner continues | 1 | T3 negative (covered: `requires_root` guard) |
| Per-agent placeholder unresolved | `render_per_agent` `KeyError` | Per-resource `error`; check returns drift but fix skipped | 1 | (negative branch of T12) |

**The cardinal rule:** converge never makes the system worse. Any unrecoverable error is logged and skipped. The cron will run again in 30 min. A loud, visible drift is always preferable to an aggressive fix that breaks more.

---

## 7. Security and operational considerations

(Pilot is medium scope; no Phase 6b/6c/6d. Notes here cover the obvious risks.)

- **Runs as root via cron.** `apparmor_parser -r` and writes to `/etc/apparmor.d/` require root. The cron entry runs as `hermes` and `apparmor_parser` is invoked via `sudo` with a tightly-scoped sudoers rule (`hermes ALL=(ALL) NOPASSWD: /usr/sbin/apparmor_parser -r /etc/apparmor.d/bwrap`). This rule is a deployment-time concern documented in §4 and should be added to `deploy-agent.sh` in a follow-up; for the pilot it can be added manually per VM.
- **No shell expansion on YAML values.** Every subprocess call uses list-form arguments. Per-agent `{{ agent.<field> }}` placeholders are resolved by string substitution in Python — never via shell.
- **YAML loader uses `yaml.safe_load`.** Never `yaml.load` (which can deserialize arbitrary Python objects).
- **No secrets in YAML.** Passwords, tokens, API keys are explicitly out of scope. The YAML may reference `agent-registry.json` (already on disk) for emails, which are not secret.
- **Cron schedule jitter.** Five VMs ticking at exactly `:00` and `:30` would cause five concurrent git-config sweeps on shared NFS… except there is no NFS — each VM has its own disk. No coordination needed.
- **Drift event volume.** Steady state: ≤1 line per VM per 30 min × 5 VMs = 240 lines/day. Drift bursts during incidents may produce 10–20 lines per tick — still trivial for Loki ingestion.
- **Failure to converge means stale state, not corruption.** A converge.py exception leaves the VM in its current state. The next tick retries.

---

## 8. Follow-ups (out of scope; tracked here)

- **STORY-XYZ:** Add `--repair` mode to `push-code.sh` that, on detecting `code_deploy_parity` drift via converge, automatically redeploys. Requires careful interlocking with the SDK-active check.
- **STORY-XYZ:** Settings.json hash enforcement — needs canonical/per-agent split design.
- **STORY-XYZ:** Plugin list enforcement (claude `plugins/` directory).
- **STORY-XYZ:** Hermes scheduled cron drift detection — assert a known set of cron entries exist (vs. just asserting the legacy JSON is disabled).
- **STORY-XYZ:** Service health enforcement (restart `dispatch-poller` if not running) — requires careful safety interlock with rate-limit pause flag.
- **Possible refactor:** if the registry grows beyond 8–10 categories, split `converge.py` into a package (`deployment/vm/converge/{__init__.py, runner.py, checks/*.py}`). Pilot stays single-file because the test surface is small and the module boundaries aren't yet stable.

---

## 9. Acceptance — what "done" looks like

- `feature-spec.md` (this file) committed under `features/story-644-declarative-state-convergence-pilot/`.
- Phase 7 produces `test-design.md` + the 18 RED tests in `tests/deployment/test_converge.py`.
- Phase 8 produces:
  - `deployment/vm/canonical-state.yaml` (5 categories declared)
  - `deployment/vm/converge.py` (~400 LOC, 5 checks registered)
  - `deployment/vm/run-converge.sh` (env-strip wrapper)
  - `deployment/vm/.deploy-manifest.json` (regenerated by push-code.sh)
  - `deployment/vm/push-code.sh` updates (deploy list + manifest writer)
  - All 18 tests GREEN.
  - PR opened against main.
- Post-merge deployment per §4 lands the cron on all 5 VMs and validates a deliberately-injected drift gets fixed within one tick.
