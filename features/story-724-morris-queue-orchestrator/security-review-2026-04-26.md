# Security Review — Morris Queue Orchestrator System

**Review Date:** 2026-04-26
**Reviewer:** tech-agent-derrick (automated security review)
**Scope:** Agent VM access control, Morris→Teams command channel, ops-console
auth, autonomous action scope.
**Branch reviewed:** `story-337/fix-cost-display-regression` @ HEAD

---

## Scope Note (IMPORTANT — read first)

The original review prompt asks about several files that **do not yet exist
in the repository**:

| Referenced in prompt | Actual state |
|---|---|
| `deployment/morris/scripts/orchestrator_loop.py` | Not present. `deployment/morris/scripts/` contains only `__pycache__/`. |
| `deployment/morris/scripts/interventions.py` | Not present. |
| `deployment/morris/scripts/proposal_generator.py` | Not present. |
| `invoke_rebase_subagent` | No such symbol in the repo (`grep -rn` returns 0 hits). |
| `post_approval_needed` | No such symbol in the repo. |
| `orchestrator_config.yaml` | Not present. The only `config.yaml` is the project SDLC config and a template. |
| `features/story-724-morris-queue-orchestrator/` | Did not exist before this review (created to host this document). |

These files are **planned, not yet implemented.** This review therefore covers:
1. The actual current implementation of the system (Morris fleet-vigilance,
   dispatch poller, agent-push, ops-console auth, terminal guard, Teams adapter).
2. Forward-looking guidance about the design constraints that any future
   `orchestrator_loop.py` / `interventions.py` / `proposal_generator.py` will
   need to honor.

Where a finding is forward-looking (about code that hasn't been written),
it is tagged **[FORWARD-LOOKING]**.

---

## Summary of findings

| ID | Severity | Title |
|---|---|---|
| F-01 | CRITICAL | Teams DM sender identity is not authenticated — any chat participant can issue commands |
| F-02 | CRITICAL | All agent VMs share a single GitHub PAT; compromise of one VM compromises all repo write access |
| F-03 | CRITICAL | `claude_sdk_tool.py` runs every agent prompt with `permission_mode="bypassPermissions"` — agent prompts are effectively shell-equivalent |
| F-04 | HIGH     | API_SERVER_HOST=0.0.0.0 with hardcoded bridge key in cloud-init (`dan-internal-bridge-key`) |
| F-05 | HIGH     | Entra ID group-membership check is logged-but-not-enforced (relaxed-until-confirmed) |
| F-06 | HIGH     | Dispatch retry path (`/api/dispatch` re-enqueue) accepts attacker-controlled `prompt`, `repo`, `pr_branch`, `base_story_id` from the SDK process running with `bypassPermissions` |
| F-07 | HIGH     | Undefined variable `branch_slug` in dispatch_poller.py validation path (line 618) — silent fallback masks failures |
| F-08 | HIGH     | `morris-fleet-check.sh` exfiltrates the OPS_CONSOLE_API_KEY via `sudo grep` of `.env` and embeds it in subprocess argv |
| F-09 | HIGH     | `agent-push.sh` uses `StrictHostKeyChecking=no` on every SSH/SCP — first-use TOFU is disabled |
| F-10 | MEDIUM   | `work_history` and `health` ops-console routes lack auth dependency (information disclosure surface limited but inconsistent) |
| F-11 | MEDIUM   | Cloud-init writes `/opt/agent/.env` with full secret payload before boot — relies entirely on Azure Key Vault and Custom Data confidentiality |
| F-12 | MEDIUM   | Auto-registration on `/dispatch/next` (X-Agent-Name header) lets any caller with the API key inject arbitrary agent names into the agents table |
| F-13 | MEDIUM   | `_github_commit_exists` falls back to anonymous GitHub API when `github_token` is falsy; rate-limit blackouts cause fail-closed behavior that is correct but tightly coupled |
| F-14 | MEDIUM   | Failure flag files written under `/home/hermes/state/<agent>/failed-stories/` from inside untrusted prompts — directory traversal risk if `AGENT_NAME` env is ever attacker-controlled |
| F-15 | LOW      | `cloud-init.yaml` installs `claude-code@latest` and `codex@latest` at provision time — supply chain pin is missing |
| F-16 | LOW      | M365 token cached in process memory for 1800s with no revocation hook |
| F-17 | LOW      | Reset-time parsing (`_parse_reset_time`) accepts attacker-controlled strings from rate-limit log content; worst case is bad pause duration |
| F-18 | INFO     | Shared GitHub PAT means git commit author attribution is decorative (any agent can commit as "Bot Derrick") |

---

## Review Area 1 — Agent VM Access Control

### F-09 [HIGH] — `StrictHostKeyChecking=no` everywhere

**File:** `deployment/vm/agent-push.sh:32, 37`
**File:** `deployment/vm/morris-fleet-check.sh:47, 80, 88`

Every SSH and SCP call in the deployment and management scripts disables host
key verification:

```
ssh -p "$port" -o StrictHostKeyChecking=no -o ConnectTimeout=5 ...
scp -P "$port" -o StrictHostKeyChecking=no ...
```

**Threat:** An attacker on-path between the operator's workstation (or Morris
VM) and an agent VM can mount a MITM, accepting connections on the agent's IP
and serving a different host key. The attacker would receive any pushed
configs (including the `.env` the deploy script may push), plus authenticated
SSH commands. With agent VMs on public IPs (per `agent-registry.json`), this
attack is reachable from anywhere.

**Mitigation:**
1. Pin `~/.ssh/known_hosts` for each agent IP at provision time and use
   `StrictHostKeyChecking=yes` (or at least `accept-new`). Bake the host pubkeys
   into a registry alongside `agent-registry.json`.
2. Rotate host keys whenever a VM is rebuilt; update the registry.
3. Use a bastion/jumphost or Azure Bastion for management plane access rather
   than direct public SSH on port 443.

### F-02 [CRITICAL] — Shared GitHub PAT across all agent VMs

**File:** `deployment/vm/SETUP-CHECKLIST.md:81-101`

```
All agents share a **single GitHub PAT** (currently from Dan's `agent-dan-gc`
account)…
```

**Threat:** If any agent VM is compromised — through a supply chain bug in
`@anthropic-ai/claude-code@latest` (installed unpinned), an exploited Hermes
gateway, a hijacked claude-sdk session, or a rogue prompt routing through the
SDK — the attacker exfiltrates a PAT that grants write access to ALL
`hpi-gorillacommerce/*` repositories. Because the PAT is shared, blast radius
is "all repos" not "this agent's repos." There is also no per-agent auditability:
git commit author is set to the agent name but the *PAT identity* is the same
across the fleet.

**Mitigation:**
1. Move to a GitHub App with per-installation tokens (short-lived, automatically
   rotated, scoped to the fleet org).
2. If staying on PATs, give each agent its own PAT scoped to the repos it
   actually needs.
3. Store PATs in Azure Key Vault and have the agent fetch a fresh token at
   startup using the VM's managed identity, never persist on disk.

### F-03 [CRITICAL] — `bypassPermissions` is the default for every SDK invocation

**File:** `deployment/vm/claude_sdk_tool.py:166`
**File:** `deployment/vm/morris-fleet-check.sh:148`

```python
opts = ClaudeAgentOptions(
    cwd=workdir,
    permission_mode="bypassPermissions",
)
```

The wrapper claims to enforce a `can_use_tool` callback (`SAFE_TOOLS`,
`DENY_PATTERNS`), but with `permission_mode="bypassPermissions"` the SDK
**does not call the callback at all** — bypassPermissions is the SDK's
"do not gate tools" flag. Setup notes (`SETUP-CHECKLIST.md:14`) even reference
this confusion: "SDK tool uses `permission_mode='acceptEdits'` … `canUseTool`
causes stream errors."

The DENY_PATTERNS list ("rm -rf /", "DROP TABLE", "git push --force",
"sudo rm -rf") is **defense theater**: it is never evaluated for tools that
take the bypass path.

**Threat:** Any prompt that reaches `claude_sdk_tool.py` — including
prompts re-enqueued by the auto-retry path with attacker-influenced text,
or prompts dispatched from `morris-fleet-check.sh`'s Stage-2 prompt
construction — can execute arbitrary Bash, write any file, run any tool,
without the deny list ever firing.

**Mitigation:**
1. Switch `permission_mode` to `"acceptEdits"` (or `"default"`) so that
   `can_use_tool` is invoked on Bash/Write/Edit. Fix the `canUseTool stream
   error` properly rather than disabling the gate.
2. If `bypassPermissions` must remain, delete `can_use_tool`, `SAFE_TOOLS`,
   and `DENY_PATTERNS` from the file so the next reviewer doesn't believe
   they are active controls.
3. Move the deny list into a pre-prompt sandbox (e.g., bubblewrap, firejail,
   or a Linux `seccomp-bpf` policy on the SDK subprocess) where it is
   actually enforceable.
4. For Morris's fleet-check, scope `--add-dir` minimally and avoid passing
   `--permission-mode bypassPermissions` for routine state-file updates.

### F-15 [LOW] — Unpinned npm install at boot

**File:** `deployment/vm/cloud-init.yaml:144`

```
npm install -g @anthropic-ai/claude-code@latest @openai/codex@latest
```

`@latest` resolves at provision time. A compromise of either npm package
upstream becomes a silent compromise of every newly-provisioned VM.

**Mitigation:** Pin to specific versions, mirror to an internal npm registry,
or use sigstore/npm provenance verification.

### F-04 [HIGH] — Internal bridge key hardcoded in cloud-init; bound to 0.0.0.0

**File:** `deployment/vm/cloud-init.yaml:60-62`

```
API_SERVER_KEY=dan-internal-bridge-key
API_SERVER_PORT=8642
API_SERVER_HOST=0.0.0.0
```

The "Hermes API server" listens on all interfaces with a hardcoded, weak,
cleartext-known key. Any process on the VM (or anyone who reaches port 8642
via Azure NSG misconfiguration) can call the bridge.

**Mitigation:**
1. Bind to `127.0.0.1` only.
2. Generate a per-VM random key and store in the .env (already partially done —
   `SETUP-CHECKLIST.md:141` says `<name>-internal-bridge-key`, but cloud-init
   defaults to the static value).
3. Verify Azure NSGs explicitly DROP inbound 8642 from the public internet.

---

## Review Area 2 — Morris → Teams Command Channel

### F-01 [CRITICAL] — Teams DM sender identity is not authenticated

**File:** `deployment/vm/teams_m365_deployed.py:348-391`

`_process_message()` extracts `sender_id` and `sender_name` from the Graph
API payload, then constructs a `MessageEvent` and hands it to
`self.handle_message(event)`. There is **no allowlist of authorized senders.**
The bot will obey commands from anyone in the chat — that includes any user
who is added to the chat by Microsoft's chat-creation rules, any guest
account, and (if Teams federation is enabled at the tenant level) any
external account that can DM the bot's UPN.

If Mark adds a teammate to a Morris DM, that teammate can run `CANCEL
STORY-XXX`, `dispatch a new story`, or any other Morris-routed command.
Worse: if Mark's account is compromised, an attacker can steer the entire
fleet from a single Teams session.

This is the single biggest "honor system" gap in the system. The user
prompt's phrasing ("is the reply parsed and authenticated? Or is it an
honor system?") is exactly correct: it is an honor system.

**Mitigation:**
1. Maintain `MORRIS_AUTHORIZED_SENDERS` (Entra object IDs) in env or
   `agent-registry.json` and reject inbound messages whose `sender_id`
   is not on the list.
2. For high-impact commands (`CANCEL`, `MERGE`, `DISPATCH`, anything that
   mutates state), require a second-factor: a Teams adaptive card with
   an "Approve" button that re-validates via Graph (`/me`) or a typed
   confirmation phrase that Morris records and re-checks.
3. Disable Teams federation for the bot's UPN unless explicitly required.
4. Audit every Morris session log for messages from non-allowlisted senders.

### F-16 [LOW] — M365 token cached in memory; no revocation hook

**File:** `deployment/vm/teams_m365_deployed.py:166-191`

The Graph token is cached for 1800 seconds and refreshed by calling out to
the local `m365` CLI subprocess. There is no hook to clear the token if
M365 CLI is logged out, no signed-out-event listener, and no token
introspection check before high-impact send.

**Mitigation:** Acceptable risk for now. If automated revocation matters
(e.g., compromised bot account), drop the cache and re-fetch on each
high-impact action, or add a `/me` probe before each `setPresence` /
high-stakes send.

### F-17 [LOW] — Rate-limit reset string is parsed from log file

**File:** `deployment/hermes/dispatch_poller.py:512-540`

The poller reads `/tmp/claude-sdlc-logs/session-<ts>.log` and regex-extracts
"hit your limit … resets <time>", then writes that string into
`/var/run/dispatch-poller-paused-until` and uses `_parse_reset_time` to
schedule auto-unpause. The log is owned by the SDK subprocess, which runs
in `bypassPermissions` and could be coerced (via prompt injection in a
PR description, e.g.) into writing a forged "hit your limit" line. Worst
case: the agent stays paused for up to 1 hour (the conservative cap) or
unpauses early.

**Mitigation:** Parse only the SDK's structured stderr/exit signal rather
than scanning the unstructured log file. Or restrict the reset-time
parsing to an allowlist (HH:MM UTC only) and discard anything else.

---

## Review Area 3 — API Authentication and Secrets

### F-05 [HIGH] — Entra group-membership check logs but does not enforce

**File:** `tech_dev_agents/ops_console/auth.py:170-179`

```python
required_group = "04284f3f-51db-46f4-a5d8-3d1bd17efb7c"
if required_group not in groups:
    logger.warning(
        "User %s not in Technology Agents group (groups=%s). Allowing for now.",
        ...
    )
```

The comment says "Relaxed until groups claim is confirmed in production
tokens." Any authenticated Entra user in the tenant — including non-
engineering staff, a compromised employee account, or an external guest
— can therefore call privileged ops-console endpoints (`/dispatch`,
`/dispatch/cancel`, `/dispatch/queue`, `/agents/register`).

**Mitigation:**
1. Confirm the groups claim is present in production tokens (App
   Registration → Token Configuration → optional claims → groups).
2. Flip the check from warning to enforcement: `raise HTTPException(403, …)`
   when the group is missing.
3. Add a unit test that ensures the check rejects a token lacking the
   required group ID.

### F-10 [MEDIUM] — `work_history` and `health` routes lack auth dependency

**File:** `tech_dev_agents/ops_console/routes/work_history.py:17`
**File:** `tech_dev_agents/ops_console/routes/health.py:14`

```python
router = APIRouter()  # no Depends(require_auth)
```

`/api/work_history` calls GitHub's API as the ops-console's PAT and returns
PR details across 7 repos. While it's a read-only proxy, it's also an
unauthenticated GitHub-PAT-backed endpoint exposed on the ops console. An
unauthenticated attacker can fingerprint PR activity. The `health` route
is intentionally public for liveness probes — that's fine, but be explicit
about which routes are intentionally public.

**Mitigation:**
1. Add `dependencies=[Depends(require_auth)]` to `work_history`.
2. For `health`, leave open but ensure it never returns sensitive payload.
3. Add a router-level test that asserts every router *except* `health`
   has a `require_auth` dependency.

### F-12 [MEDIUM] — Auto-registration via X-Agent-Name header

**File:** `tech_dev_agents/ops_console/routes/dispatch.py:172-181`

The `/dispatch/next` endpoint reads `X-Agent-Name` from request headers and
silently registers any new value into the `agents` table. While the route
requires API key / Entra auth, anyone with the API key (or a valid Entra
token, given F-05) can pollute the `agents` table with arbitrary names —
including names that collide with real agents, names with SQL-control
characters that downstream consumers might not escape, or names long enough
to cause storage issues.

**Mitigation:**
1. Validate `X-Agent-Name` against a regex (`^[a-z0-9-]{1,32}$`) before passing
   to `register_agent`.
2. Log a warning when an unknown agent name registers; alert if outside
   working hours.

### F-11 [MEDIUM] — `.env` distribution model leans entirely on Azure Key Vault

**File:** `deployment/vm/deploy-agent.sh:23-27`
**File:** `deployment/vm/cloud-init.yaml:36-72`

Cloud-init renders `/opt/agent/.env` at first boot with all secrets baked
in (mode 0600). If the Custom Data blob is logged anywhere (Azure Activity
Log, Resource Manager templates, terraform state files), the secrets are
exposed. The `.env` lives on the VM disk for the lifetime of the VM.

**Mitigation:**
1. Use Azure VM Managed Identity to pull secrets at runtime from Key Vault
   instead of baking them into Custom Data.
2. Verify Azure Activity Log retention is configured to NOT capture Custom
   Data payloads.
3. Add an `agent-rotate-secrets.sh` script and run it on a schedule.

### Positive findings (what's done correctly)

- `validate_api_key` uses `hmac.compare_digest` (constant-time), preventing
  timing attacks against the API key (`tech_dev_agents/health_api.py:54`).
- All dispatch DB queries use parameterized asyncpg `$N` placeholders — no
  string interpolation into SQL. Good.
- `dispatch_db_service.complete()` uses `WHERE story_id=$1 AND status='claimed'`
  for atomic state transitions — prevents double-completion races.
- `complete_story` route fails closed on GitHub API outages (won't accept
  fraudulent commits during a network hiccup) — `dispatch.py:281-283, 297-299`.
- The terminal_guard.py whitelist + deny patterns are sensible for the
  Hermes-native terminal path (separate from the SDK path).
- `agent-push.sh` uses `chown hermes:hermes` on every pushed file — file
  ownership is enforced.
- API routes correctly use `Depends(require_auth)` at the router level for
  every privileged surface (dispatch, agents, alerts, fleet, messages,
  context).

---

## Review Area 4 — Autonomous Action Scope

### F-06 [HIGH] — Dispatch retry path accepts attacker-controlled fields from the SDK

**File:** `deployment/hermes/dispatch_poller.py:325-351, 660-668`

When the SDK exits with a non-zero return code, `_report_fail` re-enqueues
the story with a body that includes `prompt`, `repo`, `scope`, `pr_branch`,
and `base_story_id` taken **from the prompt and dispatch payload that the
SDK itself executed under bypassPermissions**. Because the SDK can write
to its own log files and to `/home/hermes/state/.../failed-stories/`, a
prompt-injection attack against the SDK could plausibly maneuver these
fields into the next enqueue request — for example, swapping `repo` to a
different repo, or turning a normal dispatch into a `pr_branch` rework that
checks out an attacker-controlled branch.

The risk is amplified because Morris (and the auto-retry logic) trust these
fields without re-validating against an allowlist of known repos.

**Mitigation:**
1. Validate `repo` against an allowlist of `hpi-gorillacommerce/*` repos on
   both the poller side (before re-enqueue) and the ops-console side (on
   POST /api/dispatch).
2. Validate `pr_branch` matches `^[a-z0-9-]+/[a-z0-9-]+$` and refers to a
   branch that already exists on `origin`.
3. Strip any control characters from `prompt` before re-enqueue.
4. Add a numeric attempt counter alongside the `[RETRY N/3]` tag so the
   retry budget can't be reset by stripping the tag.

### F-07 [HIGH] — Undefined variable in dispatch poller validation path

**File:** `deployment/hermes/dispatch_poller.py:618`

```python
pr_check = subprocess.run(
    ["bash", "-c", f"cd {workdir} && gh pr list --head '*{branch_slug}*' …"],
    …
)
```

`branch_slug` is never defined in this function. The earlier code defines
`branch_glob` and `branch_pattern`. This is a NameError that gets swallowed
by the `except Exception as val_exc: validation_passed = False` on line 644.
Effect: validation always silently fails for stories that reach this code
path, the story is re-enqueued for retry, and the agent burns turns redoing
work that already shipped — which can mask the actual fraud detection logic.

**Mitigation:**
1. Replace `branch_slug` with `branch_glob` (or whatever was intended).
2. Promote the swallowed NameError to a logged ERROR with the full traceback
   so this kind of bug surfaces.
3. Add a unit test that runs the validation path with a known-good workdir.

Also note: the bash interpolation `f"cd {workdir} && gh pr list --head
'*{branch_slug}*'"` is shell injection-adjacent. `workdir` is constructed
from `repo` (controlled by the dispatch payload, see F-06). A repo name
containing shell metacharacters (`;`, backticks, `$()`) would execute on
the VM. Use `subprocess.run` with `cwd=workdir` and the args list rather
than `bash -c`.

### F-08 [HIGH] — OPS_CONSOLE_API_KEY exfiltrated via sudo grep, embedded in argv

**File:** `deployment/vm/morris-fleet-check.sh:32`

```
OPS_KEY="$(sudo grep -oP '(?<=OPS_CONSOLE_API_KEY=).+' /opt/agent/.env | head -1)"
```

This pattern:
1. Requires Morris to have passwordless sudo to root (he does — `cloud-init.yaml:31`),
   which is itself a privilege boundary issue.
2. Puts the API key into shell variable expansion, which becomes part of the
   `curl` argv on line 42 — visible to any local user via `ps auxe`.
3. Couples Morris's correctness to having `.env` present and parseable by
   regex; any malformed line breaks fleet-check silently.

**Mitigation:**
1. Pass the key via `curl --header @<(echo "X-API-Key: $OPS_KEY")` or
   stdin/file rather than a plain header arg, to keep it out of `ps`.
2. Source the .env directly via `set -a; source /opt/agent/.env; set +a`
   without grep. (Still requires sudo, but cleaner.)
3. Better: drop the `.env` parse entirely and have Morris's systemd unit
   provide `EnvironmentFile=/opt/agent/.env` so the key is injected as an
   env var at process spawn — no exfiltration step needed.

### F-13 [MEDIUM] — Anonymous GitHub API fallback in commit verification

**File:** `tech_dev_agents/ops_console/routes/dispatch.py:264-283`

If `github_token` is empty/falsy, `_github_commit_exists` calls GitHub's
public API anonymously (60 req/hr/IP). Under load (or coincident with a
neighboring tenant on the same egress IP), the rate limit triggers, the
function returns False (fail-closed), and **all completions stall.** That
is the correct fail-mode for fraud prevention but a bad availability
property. A targeted DoS (an attacker burning the egress IP's rate limit
on purpose) becomes a complete dispatch outage.

**Mitigation:**
1. Treat empty `github_token` as a deployment misconfiguration and refuse
   to start the ops console without one.
2. Add an alert when commit verification fails for a network reason
   (separate from a "commit-not-found" reason).

### F-14 [MEDIUM] — Failure flag path traversal surface

**File:** `deployment/hermes/dispatch_poller.py:316`

```python
agent_name = os.environ.get("AGENT_NAME", "unknown")
flag_path = f"/home/hermes/state/{agent_name}/failed-stories/{story_id}.txt"
os.makedirs(os.path.dirname(flag_path), exist_ok=True)
```

Both `AGENT_NAME` and `story_id` are interpolated into a filesystem path
without sanitization. `AGENT_NAME` is set by the systemd unit (low risk),
but `story_id` flows from the dispatch queue. A `story_id` of `../../../tmp/x`
would write outside the intended directory. The dispatch model `DispatchRequest`
should already constrain story_id, but defense-in-depth says: validate at
the file-write site too.

**Mitigation:**
1. `if not re.fullmatch(r"STORY-\d+", story_id): return`
2. Use `os.path.realpath` to confirm the resolved path is under
   `/home/hermes/state/<AGENT_NAME>/failed-stories/`.

### F-18 [INFO] — Shared PAT means commit author is decorative

**File:** `deployment/vm/SETUP-CHECKLIST.md:83-101`

Because all agents authenticate to GitHub with the same PAT, the only
attribution between "Bot Dan" and "Bot Derrick" is the local git
`user.name` config. An adversary on any agent VM (or any process with
sudo on a VM) can `git config user.name 'Bot Mark'` and commit anything,
attributable to anyone. This is acceptable for an internal-only fleet
but should be documented as a non-control.

---

## [FORWARD-LOOKING] Guidance for the planned orchestrator

The following pieces are referenced in the prompt but not yet present.
When they're built, the design must address:

### `orchestrator_loop.py` (planned)

- **Rate limit / circuit breaker on autonomous actions.** If the
  orchestrator can call `release_claim`, it MUST:
  - Cap releases to ≤N per hour (e.g., 3) and ≤M per day (e.g., 10).
  - Halt and DM Mark if the cap is hit — do not blindly continue.
  - Persist a counter in the database so a process restart doesn't reset
    the budget.
- **Idempotency keys** on every state-changing call so retries don't
  release the same claim twice.
- **Audit log:** every autonomous action gets a row in
  `orchestrator_actions(ts, action, story_id, reason, prior_state, new_state)`,
  reviewed weekly.

### `interventions.py` (planned)

- Define a finite, enumerated list of `Intervention` types. Reject any
  action not on the list.
- For each intervention, document: trigger, blast radius, undo procedure,
  rate cap, and approval requirement.
- An "auto-rebase" intervention that runs `claude_sdk_tool.py` with
  `bypassPermissions` is high-risk; require:
  - Workdir validated against the dispatch repo allowlist.
  - SDK subprocess running under a separate Linux user (not `hermes`)
    so a prompt-injection compromise doesn't get credentials of the rest
    of the fleet.
  - Network egress restricted to GitHub + ops-console only (firewall rules).
  - All commits must be signed with a key Morris controls; an unsigned
    push is the canary that the SDK was abused.

### `proposal_generator.py` (planned, self-improvement loop)

- **Path traversal:** any "atomic file change" function MUST validate paths
  against an allowlist (e.g., only `features/`, `deployment/`, never
  `/etc/`, `/opt/agent/.env`, `~/.git-credentials`, `/home/hermes/.hermes/`).
- **No write-into-running-code without human approval:** changes to the
  ops console, the dispatch poller, the SDK tool, or the terminal guard
  must NEVER be auto-applied. They must open a PR for human review.
- **Sandboxed test runs:** any "test the proposal" step must run in a
  container or worktree, never against the live `/opt/agent` install.
- **Budget cap:** total LLM spend per proposal < $X, total per day < $Y;
  exceeding the cap halts the loop.

### `post_approval_needed` / DM-reply parsing (planned)

- See F-01: replies MUST be authenticated against a sender allowlist.
- Reply parsing should require an explicit token (e.g., "APPROVE
  <story-id> <hash>") so a typo or quoted-text reflection can't
  accidentally approve.
- Time-box the approval window (e.g., 30 minutes); auto-cancel if Mark
  doesn't reply.
- Never act on the first message that *looks like* approval — re-confirm
  by sending a "you said APPROVE STORY-X — confirm Y/N" round-trip.

---

## Recommended fix order

**This sprint (CRITICAL/HIGH):**
1. F-01 — Add Teams sender allowlist; gate any state-changing command on it.
2. F-02 — Move to GitHub App per-installation tokens, or per-agent PATs.
3. F-03 — Switch SDK to `permission_mode="acceptEdits"`, fix the
   `canUseTool` stream bug properly, restore the deny list as an active control.
4. F-04 — Bind API server to 127.0.0.1; rotate the bridge key per agent.
5. F-07 — Fix the `branch_slug` NameError; un-swallow the exception.
6. F-08 — Inject OPS_CONSOLE_API_KEY via systemd EnvironmentFile, not sudo grep.
7. F-09 — Pin SSH host keys for all agent VMs; remove `StrictHostKeyChecking=no`.

**Next sprint (HIGH/MEDIUM):**
8. F-05 — Enforce the Entra group-membership check.
9. F-06 — Allowlist `repo`, `pr_branch`, `base_story_id` on enqueue + retry.
10. F-10 — Add `require_auth` to `work_history`; document `health` as
    intentionally-public.
11. F-11 — Move .env secrets to runtime fetch via VM Managed Identity.
12. F-12 — Validate `X-Agent-Name` against a regex.
13. F-14 — Sanitize `story_id` and `AGENT_NAME` at file-write sites.

**Backlog (LOW/INFO):**
14. F-15 — Pin npm package versions in cloud-init.
15. F-16 — Drop the M365 token cache or add revocation hook.
16. F-17 — Tighten reset-time parsing.
17. F-18 — Document shared-PAT attribution as a non-control.

**Before any orchestrator/intervention/proposal-generator code lands:**
18. Implement the [FORWARD-LOOKING] mitigations above as design constraints
    in the seed.md / feature-spec.md for those stories.
