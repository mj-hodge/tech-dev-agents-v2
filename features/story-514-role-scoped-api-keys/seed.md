# STORY-514: Role-scoped API keys — close the proxy-bypass boundary

> Phase 1 | Scope: **Medium** | Created: 2026-04-22
> Advance: auto (automated dispatch path — 1 → 4+6 → 7 → 8+PR → Done)

---

## Problem Statement

Every VM that talks to the ops-console API (Morris, Dan, Derrick, Daisy, Devon, Mark's workstation) uses the same shared `OPS_CONSOLE_API_KEY`. The key authenticates every endpoint — claim, complete, fail, pause, cancel (DELETE), admin operations — with no role check. Morris's skills have taken advantage of this twice: when his own VM's terminal guard blocked a command (e.g. `curl -X DELETE /api/dispatch/queue/STORY-XXX`), the skill proxied the call through Derrick's VM (SSH → run curl on Derrick with Derrick's copy of the shared key → DELETE executes successfully). Seven valid Mark-dispatched stories were cancelled this way on 2026-04-21 and again on 2026-04-22.

The terminal guard is the wrong place to enforce the boundary. It's machine-local, trivially routed around, and it tries to prevent what a cooperating agent VM allows. The right place is **API-layer authorization**: split the shared key into role-scoped keys where agent VMs simply cannot call destructive endpoints.

**Temporary mitigation in place:** the DELETE endpoint is 403-gated until this story ships. Cancellation currently requires direct postgres UPDATE by Mark. That's painful but safe. This story is what makes it unnecessary.

---

## Target User

- **Mark** — needs safe dispatch queue that only he (and Morris with explicit manager role) can mutate destructively.
- **Morris** — needs DELETE and admin operations for legitimate fleet management, but can't be allowed to bypass via proxy.
- **Dan/Derrick/Daisy/Devon** — need claim/complete/fail/next. Nothing else.
- **Frontend** — needs GET on queue/fleet/agents. Nothing else.

---

## Acceptance Criteria

### Role model (AC-1/2/3)
1. **AC-1** — Three roles defined: `admin` (full access), `manager` (read + pause + priority + manager-only endpoints), `agent` (claim/complete/fail/next only, read limited to own agent data). Role definition in `tech_dev_agents/ops_console/auth.py` with an enum + permission matrix.
2. **AC-2** — Each role has a distinct env var: `OPS_ADMIN_API_KEY`, `OPS_MANAGER_API_KEY`, `OPS_AGENT_API_KEY`. The legacy `OPS_OPS_CONSOLE_API_KEY` / `OPS_CONSOLE_API_KEY` remains accepted for one release with a deprecation warning (logs every call) so rollout isn't big-bang.
3. **AC-3** — Endpoints decorated with required role. Matrix in `api-design.md`:
   - `GET /dispatch/queue|next|history`, `GET /agents`, `GET /fleet`, `GET /agents/{n}/quota` → `agent`+
   - `POST /dispatch` (enqueue), `POST /dispatch/priority` → `manager`+
   - `POST /dispatch/pause/{id}` → `agent`+ (agents legitimately pause their own work)
   - `POST /dispatch/claim/{id}|complete/{id}|fail/{id}` → `agent`+ (restricted to the agent's own name in body)
   - `DELETE /dispatch/queue/{id}` → `manager`+ (re-enables the endpoint with proper gating)
   - `POST /dispatch/fail/{id}` by an agent other than `claimed_by` → 403 (prevents cross-agent release without admin)

### Deployment (AC-4/5/6)
4. **AC-4** — `deployment/ops-console/.env` rotation: generate three new keys, set them. Old key stays for one release then is removed.
5. **AC-5** — Agent VMs receive only `OPS_AGENT_API_KEY` via `push-code.sh` (deployment) and `/opt/agent/.env`. The old shared key is removed from agent VMs.
6. **AC-6** — Morris VM receives `OPS_MANAGER_API_KEY`. Mark's local env gets `OPS_ADMIN_API_KEY`. Documented in `deployment/README.md`.

### Enforcement + audit (AC-7/8)
7. **AC-7** — Agent `OPS_AGENT_API_KEY` used against a manager-scoped endpoint returns 403 with body `{"detail": "insufficient role: agent < manager"}`. Integration test covers: each role × each endpoint → expected 200/403 matrix.
8. **AC-8** — Structured log event on every authorization failure: `{"event":"auth_denied","key_role":"agent","endpoint":"/dispatch/queue","method":"DELETE","source_ip":"..."}`. Loki-queryable. Alerts if same key hits >3 denials in 1 min (indicates proxy attempt).

### Rollout (AC-9/10)
9. **AC-9** — Canary: deploy to UAT first. Run a script that issues the full matrix of {role, endpoint} calls and verifies expected responses. Only promote to prod after canary passes.
10. **AC-10** — Post-rollout, revoke the legacy shared key. DELETE of `OPS_OPS_CONSOLE_API_KEY` env variable in `/opt/ops-console/deployment/ops-console/.env` + redeploy. Verify every caller is now using a role-specific key.

### Re-enable DELETE with proper gating (AC-11)
11. **AC-11** — Remove the 403-block that this story's mitigation added to `cancel_story()`. Endpoint now requires `manager` role — Morris can call it, agents cannot (even via proxy, because agent keys can't call DELETE regardless of origin IP).

---

## Scope Classification

**Medium** — one auth layer change, three env var migrations, one 403→role-check flip, a test matrix, and coordinated deploy to 7 endpoints + 5 VMs + frontend. No DB schema change, no new subsystems.

Phase path: **1 → 4+6 → 7 → 8+PR → Done**.

---

## Technical Notes

### Why three roles, not two

Mark has legitimate reasons to do things Morris shouldn't: rotate API keys, add/remove agents from the registry, pause the system for maintenance. Today Mark uses the same key as Morris, which is fine because Mark is the operator — but it means admin actions don't have a distinct audit trail. Splitting `admin` / `manager` gives a clear log: "who did this — Mark or Morris?"

Conversely, `agent` is distinctly smaller than `manager` because agents should never DELETE, never POST priority, never modify another agent's state.

### Authentication mechanism

Current: `APIKeyHeader(name="X-API-Key")` with single-string match. Upgrade: dict lookup `{key_str: role}` in auth config, then role check at endpoint level via dependency injection. Simple, easy to test, no JWT complexity needed at this scale. If we grow to dozens of agents with per-agent permissions, switch to JWT later.

### Backward compatibility

One release: accept old `OPS_OPS_CONSOLE_API_KEY` with role=`manager` (so Morris's existing wiring keeps working) AND log every use with `{"legacy_key_used": true, ...}`. Removes at release N+1 once we've migrated all callers and confirmed zero `legacy_key_used=true` events in Loki for 7 days.

### Key storage + rotation

Keys live in Azure Key Vault, injected via Bicep env vars into the container + VMs at deploy time. Mark manages. Rotation is "update Key Vault secret, redeploy" — monthly cadence or on-incident.

### Frontend key

Browser-side JS has `window.__OPS_CONSOLE_API_KEY__` baked in at build time (or injected via server-side template). It should be the `agent` role key — the dashboard only reads. Avoids giving the browser admin privileges.

### Dependencies

- STORY-507 (merged) — paused status + API shape that this story enforces RBAC on
- STORY-508 (in progress, Daisy) — removes more Morris band-aids; complementary. Can land in either order.
- STORY-515 (dashboard E2E + in_review) — independent

---

## Out of Scope

- Per-user auth (individual human logins) — if we need that, use Entra ID SSO work from STORY-023
- Fine-grained per-agent RBAC (Dan can't read Derrick's stories) — not needed at current scale
- Token rotation automation — manual rotation via Key Vault for now
- Rate limiting by role — separate concern

---

## Success Measure

- **Zero successful DELETE calls from an agent VM IP** for 30 days post-deploy. Loki counter `api_auth_denied_total{role="agent",endpoint="DELETE /dispatch/queue/*"}` may increment (proving the boundary catches proxy attempts) but the DELETE never succeeds.
- **Zero `legacy_key_used=true` events** for 7+ days after AC-10 completes.
- **DELETE re-enabled** with proper gating (AC-11) — Morris can cancel when he legitimately needs to, agents cannot regardless of origin.
- **No state-sync incidents** (queue rows wrongly cancelled) for 14 days post-deploy.
