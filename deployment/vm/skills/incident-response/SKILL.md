---
name: incident-response
description: >
  Diagnose production issues by reading Grafana/Loki logs, analyzing error
  patterns, and proposing fixes. Use when user says "something is broken",
  "check the logs", "why is [service] down", "investigate error", or
  "incident in [service]".
category: operations
---

# Incident Response

Reads logs from Grafana/Loki, diagnoses issues, proposes fixes, and
optionally implements them via Claude Code.

## Prerequisites

- Grafana/Loki accessible at the configured LOKI_URL
- `curl` available for API calls
- Project repos cloned locally for code-level investigation

---

## Step 1: Gather Context

Ask the user (if not already clear):
- What service/system is affected?
- When did it start?
- What symptoms are visible? (errors, slowness, outage)

---

## Step 2: Pull Logs

Query Loki for recent errors. Adapt the query to the service:

```
terminal(command="curl -sG 'https://grafana.gorillacommerce.ai/loki/api/v1/query_range' --data-urlencode 'query={agent=\"dan\"} |~ \"(?i)error|exception|fatal|panic|crash\"' --data-urlencode 'start=$(date -d '1 hour ago' +%s)000000000' --data-urlencode 'end=$(date +%s)000000000' --data-urlencode 'limit=50' | python3 -m json.tool | head -100", pty=false)
```

For a specific service:
```
terminal(command="curl -sG 'https://grafana.gorillacommerce.ai/loki/api/v1/query_range' --data-urlencode 'query={job=\"[SERVICE_NAME]\"} |~ \"(?i)error|exception|500|404\"' --data-urlencode 'start=$(date -d '30 minutes ago' +%s)000000000' --data-urlencode 'limit=100' | python3 -m json.tool | head -200", pty=false)
```

---

## Step 3: Analyze

1. Identify error patterns: repeated errors, stack traces, error codes
2. Determine timeline: when did errors start? Is it ongoing?
3. Look for root cause indicators:
   - Dependency failures (DB, API, auth)
   - Code errors (null reference, type error, import)
   - Infrastructure (OOM, disk, network)
   - Configuration (missing env var, wrong endpoint)

---

## Step 4: Investigate Code (if applicable)

If the error points to a specific file/function:

```
terminal(command="claude -p 'Read [FILE_PATH] and analyze the code around line [LINE]. The error is: [ERROR_MESSAGE]. What could cause this? Suggest a fix.' --max-turns 3 --output-format text 2>&1", workdir="[PROJECT_DIR]", pty=false)
```

---

## Step 5: Report to User

Send a structured incident report:

```
**Incident Report**

**Service:** [name]
**Status:** [ongoing/resolved/investigating]
**Started:** [timestamp]
**Duration:** [time]

**Symptoms:**
- [what's broken]

**Root Cause:**
- [diagnosis]

**Evidence:**
- [key log lines]

**Recommended Fix:**
- [specific action]

**Risk:** [low/medium/high] — [why]
```

---

## Step 6: Fix (with approval)

**Never auto-fix production issues.** Always present the fix and wait for user approval.

If user approves:
1. Create a fix branch
2. Use claude-code-sdlc skill to implement the fix
3. Run tests
4. Create a PR
5. Report back

---

## Monitoring Mode

If user says "keep watching" or "monitor":
- Check logs every 5 minutes
- Only alert if new errors appear
- Send brief updates: "No new errors in last 15 min" or "New error pattern detected: [summary]"

---

## Error Handling

- Loki unreachable: tell user, suggest checking Grafana directly
- No logs found: widen time range, check if logging is configured
- Ambiguous errors: present top 3 theories, ask user which to investigate
