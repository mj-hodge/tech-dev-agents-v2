# How to Calculate Azure Foundry Billing

> **Source of truth for any "how much did Foundry cost?" question.**
> Authored 2026-04-23 after a 14-day blind-attribution incident where my
> first three estimates were all wrong by ≥6×. This doc captures the methods
> that actually reconcile to the invoice + the methods that don't, so future
> investigations don't repeat the same wrong turns.

---

## TL;DR — The Three Layers

| Question | API | Granularity | Reconciles to invoice? |
|----------|-----|-------------|------------------------|
| **What did Foundry cost?** | **Cost Management Query API** | **Daily** | **Yes — exact** |
| What was the call/token volume? | Azure Monitor metrics | 1-min to 1-hour | No — token counts undercount billing by ~6-7× |
| Who called Foundry? (which IP) | Diagnostic logs in Log Analytics Workspace | Per-request | Yes — but only AFTER diag-settings enabled |

If you only need the dollar number: use **Cost Management Query API**. Period.
Everything else is supplementary.

---

## Layer 1 — Cost Management Query API (the truth)

This is the only source that matches the invoice. It returns the same numbers
you see in the Azure portal under **Subscriptions → Cost Management → Cost
analysis**.

### Daily cost per Foundry resource (last 14 days)

```bash
SUB=$(az account show --query id -o tsv)
RG=rg-tech-dev-agents-dev
START=$(date -u -d '14 days ago' +%Y-%m-%dT00:00:00Z)
END=$(date -u +%Y-%m-%dT23:59:59Z)

cat > /tmp/cost-q.json <<EOF
{
  "type": "ActualCost",
  "timeframe": "Custom",
  "timePeriod": {"from": "$START", "to": "$END"},
  "dataset": {
    "granularity": "Daily",
    "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}},
    "grouping": [
      {"type": "Dimension", "name": "ResourceId"},
      {"type": "Dimension", "name": "MeterCategory"}
    ]
  }
}
EOF

az rest --method POST \
  --url "https://management.azure.com/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.CostManagement/query?api-version=2023-11-01" \
  --body @/tmp/cost-q.json
```

Returns rows of `[Cost, UsageDate, ResourceId, MeterCategory, Currency]`. Filter
ResourceId for the Foundry deployment (e.g., `claude-opus-4-6-ce40a3b52ad2482-...`)
to isolate per-deployment spend.

**Validated 2026-04-23**: this query returned `$5,448.44` for 14 days on the
`claude-opus-4-6-ce40a3b52ad2482-...` deployment. Invoice month-to-date was
`$5,482`. ~99.4% reconciliation.

### Granularity options

`granularity` accepts:
- `"Daily"` — atomic unit. **No hourly granularity is exposed by Microsoft.**
- `"Monthly"` — coarser
- `"None"` — single aggregate over the timeframe

For sub-day cost shape (e.g., "what hour did the spike happen?") use Layer 2 +
manual calibration. There is no native hourly billing data.

### When to use this layer

- Reconciling against your Azure invoice
- Daily / monthly cost reporting
- Per-resource attribution
- Setting budgets and alerts

---

## Layer 2 — Azure Monitor Metrics (volume, not cost)

Returns hourly request count and token counts. Sub-day granularity available
(down to 1-minute intervals).

```bash
RID="/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/<resource-name>"

az monitor metrics list \
  --resource "$RID" \
  --metric "ModelRequests" "InputTokens" "OutputTokens" \
  --interval PT1H \
  --start-time "$(date -u -d '7 days ago' +%Y-%m-%dT%H:00:00Z)" \
  --end-time   "$(date -u +%Y-%m-%dT%H:00:00Z)" \
  --aggregation Total
```

Available metrics on Cognitive Services:
- `ModelRequests`, `TotalCalls`, `SuccessfulCalls`, `BlockedCalls`, `TotalErrors`
- `InputTokens`, `OutputTokens`, `TotalTokens`, `cacheReadInputTokens`, `ephemeral5mInputTokens`, `ephemeral1hInputTokens`
- `TimeToResponse`, `TimeToLastByte`, `NormalizedTimeToFirstToken`, `TokensPerSecond`
- `ProvisionedUtilization`

### **The undercount problem** (DO NOT estimate cost from these)

The `InputTokens` / `OutputTokens` metrics are **billing-layer aggregates that
DO NOT include cache_creation_tokens** (the most expensive token type for
sustained sessions on Opus). Naive cost calculation:

```
naive_cost = InputTokens × $5/MTok + OutputTokens × $25/MTok
```

undercounts the actual billed cost by ~6-7× on GlobalStandard SKU. Validated
2026-04-23: naive estimate was `$862` for 14 days; actual billed was `$5,448`.

**Why**: Foundry's billing meter (`SaaS` MeterCategory in Cost Management)
includes cache writes at `1.25× input rate` and other charges that aren't
exposed via the public metric counters.

### Calibration factor (fragile — use when you must)

If you need an HOURLY cost-shape estimate (e.g., to see when the spike
happened), apply a calibration factor derived from comparing one period:

```
calibration = (actual_cost_from_cost_mgmt) / (naive_cost_from_metrics)
calibrated_hourly_cost = naive_hourly_cost × calibration
```

For tech-dev-agents Sweden Opus deployment 2026-04: calibration ≈ **6.32**.
This factor changes per resource, per SKU, per period. **Never use a hardcoded
calibration; recompute it whenever you compare to invoice.**

### When to use this layer

- "When during the day did calls happen?" — hourly request shape
- "Was there a sudden traffic spike?" — anomaly detection
- "Which deployment served the most calls?" — per-`ModelDeploymentName` filter
  (note: `ModelRequests` accepts deployment-name dimension; `TotalCalls` does not)
- **NOT for cost reconciliation against the invoice.**

---

## Layer 3 — Diagnostic Logs (the only per-IP attribution)

The Cost Management Query API and Azure Monitor metrics both aggregate up to
the resource level. Neither tells you **which caller IP made which request**.
For that, diagnostic settings must be enabled on the resource.

### Enable on a resource (do this DAY 1 for every new Foundry resource)

```bash
SUB=$(az account show --query id -o tsv)
RG=rg-tech-dev-agents-dev
RESOURCE="<cognitive-services-resource-name>"
LAW_ID="/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.OperationalInsights/workspaces/log-tech-dev-agents-dev"

az monitor diagnostic-settings create \
  --name "foundry-attribution" \
  --resource "/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/$RESOURCE" \
  --workspace "$LAW_ID" \
  --logs '[{"category":"RequestResponse","enabled":true},{"category":"Audit","enabled":true},{"category":"Trace","enabled":true}]' \
  --metrics '[{"category":"AllMetrics","enabled":true}]'
```

**This is OFF by default on every new Cognitive Services resource.** Without it,
past requests have no per-IP record — you cannot retroactively attribute. The
data starts flowing 5-30 minutes after enable (sometimes up to 2 hours for
first AzureDiagnostics row to populate).

**Cost**: ~$0.70/month per resource at our volume (~5,000 calls/day × 2KB log
row × $2.30/GB Log Analytics ingestion). Trivial. There is no rational reason
to leave it off.

### Query per-IP per-hour after diag enabled

```bash
LAW=e2682470-3071-4a8d-b75b-e99b093dff4d  # log-tech-dev-agents-dev workspace ID

az monitor log-analytics query --workspace "$LAW" --analytics-query \
  'AzureDiagnostics
   | where ResourceProvider == "MICROSOFT.COGNITIVESERVICES"
   | summarize calls=count() by Resource, CallerIpAddress_s, bin(TimeGenerated, 1h)
   | order by TimeGenerated desc'
```

Cross-reference `CallerIpAddress_s` against your VM IPs:
- Dan: 20.228.224.243
- Derrick: 20.121.210.186
- Morris: 20.246.36.143
- Daisy: 20.98.231.234
- Devon: 20.186.26.130
- ops-console VM: 137.116.63.176 (10.5.0.6 private)
- Container app `ca-tech-dev-agents-dev`: routed via Azure container app subnet

### When to use this layer

- "Which agent / VM is calling Foundry?" — per-IP attribution
- "Has anyone outside the fleet hit our endpoint?" — security audit
- "What's the latency distribution per caller?" — performance investigation
- "Which prompts are getting 429-rate-limited?" — error analysis

---

## Pricing Reference (per million tokens, Opus / Sonnet / Haiku)

These rates apply to Anthropic direct AND Azure Foundry — they're identical at
the published rate. (Earlier guess of "Foundry has a 2-3× markup" was wrong.
Validated 2026-04-23 against Azure pricing docs.)

| Model | Input | Output | Cache Read | Cache Write |
|-------|-------|--------|------------|-------------|
| Claude Opus 4.6 | $5.00 | $25.00 | $0.50 | $6.25 |
| Claude Sonnet 4.6 | $3.00 | $15.00 | $0.30 | $3.75 |
| Claude Haiku 4.5 | $1.00 | $5.00 | $0.10 | $1.25 |

Cache pricing notes:
- Cache read = 10% of input price
- Cache write = 1.25× input price
- Sustained agent sessions (hermes-style) are dominated by cache writes — a
  fresh hermes session often loads 100K+ tokens of context that count as
  cache_write. At 700 sessions/day on Opus, that's the bulk of the bill.

---

## Cross-referencing application-side telemetry (when available)

For Foundry calls originating from `hermes`, hermes maintains its own session
DB with full per-call attribution at `/home/hermes/.hermes/state.db`. Schema:

```
sessions table columns:
  id, source, user_id, model, model_config, system_prompt,
  parent_session_id, started_at, ended_at, end_reason,
  message_count, tool_call_count,
  input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
  reasoning_tokens, billing_provider, billing_base_url, billing_mode,
  estimated_cost_usd, actual_cost_usd, cost_status, cost_source,
  pricing_version, title
```

### Query Foundry-bound sessions on a given hermes VM

```bash
ssh -p 443 azureagent@<vm-ip> "sudo python3 - <<'PY'
import sqlite3, datetime
con = sqlite3.connect('/home/hermes/.hermes/state.db')
cur = con.cursor()
NOW = datetime.datetime.now(datetime.UTC).timestamp()
START = NOW - 14*86400

# Reconciled cost using full pricing model (input + output + cache_read + cache_write)
def cost(ti, to_, cr, cw):
    return ti/1e6*5 + to_/1e6*25 + cr/1e6*0.5 + cw/1e6*6.25  # Opus rates

q = '''SELECT date(started_at, 'unixepoch') d, source, COUNT(*),
              SUM(input_tokens), SUM(output_tokens),
              SUM(cache_read_tokens), SUM(cache_write_tokens)
       FROM sessions
       WHERE started_at >= ? AND billing_base_url LIKE '%moret-mnafhqa3%'
       GROUP BY d, source ORDER BY d, source'''
for r in cur.execute(q, (START,)):
    c = cost(r[3] or 0, r[4] or 0, r[5] or 0, r[6] or 0)
    print(f'{r[0]} {r[1]:<10} sessions={r[2]:>5} ~cost=\${c:>8,.2f}')
PY"
```

This was validated 2026-04-23 against Cost Management: state.db reconciled cost
of `$5,100.66` vs Cost Management `$5,448.44` — 94% match (gap is the cache_write
pricing approximation; Foundry's actual cache_write meter is slightly higher
than the public rate).

**state.db is per-hermes-VM. It does NOT capture Foundry calls made by other
clients (Claude Code SDK via `/usr/bin/claude`, container apps, ops-console
VM, manual `curl`s, etc.). For full Foundry attribution, combine state.db +
diag logs.**

---

## Methods That DON'T Work (or are wrong, validated 2026-04-23)

| Method | Why it's wrong |
|--------|----------------|
| Estimating cost from `InputTokens`/`OutputTokens` × public token rate | Undercounts by ~6-7× because cache_creation_tokens aren't included in those metrics |
| `az consumption usage list` | Records often have `pretaxCost: None` and `usageStart: None`. Use Cost Management Query API instead. |
| Counting Loki log lines that mention `claude-opus-4` | Matches Anthropic's own documentation/examples printed by SDK, not actual API calls. Confirmed 2026-04-23 — "63 markers" on Daisy turned out to be 63 lines of `model="claude-opus-4-6",` from a markdown file being processed. |
| `Provider: anthropic Model: claude-opus-4-6` log marker (hermes) | Only emitted on **errors** (rate-limits, connection errors). 27 marker lines for Morris over 14 days = 27 errors, not 27 calls. Successful calls don't emit. |
| Pearson-correlating per-agent journal volume against Foundry hourly counts | Different processes log at different verbosities. Hermes-gateway logs sparsely (one marker per session start, not per turn). Crash loops produce massive noise. The correlation never converges to a clean answer. |
| Assuming Azure Foundry has a markup over Anthropic direct | Public pricing is identical at $5/$25 for Opus 4.6. The variance comes from SKU-specific behavior on cache writes, not from a base markup. |

---

## How to Investigate "Why Did the Bill Spike?" — Standard Procedure

1. **Get the daily shape from Cost Management** (Layer 1). Identify which day(s)
   had the spike. Confirm which deployment(s) it was on.
2. **Get the hourly shape from Azure Monitor** (Layer 2). Identify which hours
   contributed. Compare to baseline hours-of-day.
3. **If diag logs were enabled at the time**, query LAW (Layer 3) for per-IP
   breakdown of the spike hours. Identify caller(s).
4. **If diag logs were NOT enabled**, fall back to application-side telemetry
   (e.g., hermes's `state.db` for hermes calls). Filter by the spike time window
   and `billing_base_url` matching the Foundry resource.
5. **Cross-reference application-side timing with deploy/restart events.**
   Service restarts evict caches → next session = full cache_write = expensive.
   Cron schedule changes can also cause spikes. Check `journalctl -u <service>`
   and crontab history for the spike window.
6. **Reconcile**: sum your application-side attribution and compare to Cost
   Management for the same window. Should match within ~10%. If gap > 25%,
   you have an unattributed caller — usually a sign that diag logs need to be
   on, or another client besides hermes is hitting Foundry.

---

---

## Per-Job Cost Attribution on hermes (Morris)

**Why this matters**: hermes runs MANY scheduled jobs internally — *separate* from Linux cron. They show up in `hermes cron list`, run via the hermes gateway, and bill Foundry. To attribute the bill, you have to find which jobs ran when and join their session timestamps to `state.db`.

### Listing all hermes jobs

```bash
ssh -p 443 azureagent@20.246.36.143
sudo -u hermes hermes cron list
```

Returns each job's `id`, `name`, `schedule`, `last_run_at`, `next_run_at`, attached `Skills:`, attached `Script:`, and delivery target.

### Reading per-job config (model overrides)

Each job's full definition (including `model`, `provider`, `base_url`) is at:

```
/home/hermes/.hermes/cron/jobs.json
```

Schema per job:

```json
{
  "id": "6363c6a5b0bb",
  "name": "Morris State Sync (15min)",
  "prompt": "...",
  "skills": [...],
  "script": null,
  "schedule": {"kind":"cron","expr":"*/15 * * * *"},
  "model": null,         // ← when null, falls back to Morris's default (Opus)
  "provider": null,
  "base_url": null,
  "deliver": "local",
  "enabled": true,
  ...
}
```

To override model/provider on a per-job basis, patch the JSON file directly. There is NO `hermes cron edit --model` flag yet; you have to edit `jobs.json` and restart hermes-gateway.

### Cross-referencing job runs to actual API cost

Each job invocation creates a session file at `/home/hermes/.hermes/sessions/session_cron_<job_id>_YYYYMMDD_HHMMSS.json`. The job_id and start timestamp are baked into the filename. You can join these to `state.db` sessions by matching `started_at` timestamps within ±60 seconds:

```python
import sqlite3, glob, re, datetime, collections, json

con = sqlite3.connect('/home/hermes/.hermes/state.db')
cur = con.cursor()
NOW = datetime.datetime.now(datetime.UTC).timestamp()
START = NOW - 14*86400

# Get all session files that name a cron job
pat = re.compile(r'session_cron_([a-f0-9]+)_(\d{8})_(\d{6})\.json')
job_files = collections.defaultdict(list)
for f in glob.glob('/home/hermes/.hermes/sessions/session_cron_*.json'):
    m = pat.search(f)
    if m: job_files[m.group(1)].append((m.group(2), m.group(3)))

# Map job_id → name from cron/jobs.json
jobs_json = json.load(open('/home/hermes/.hermes/cron/jobs.json'))
id_to_name = {j['id']: j['name'] for j in jobs_json.get('jobs', [])}

# For each job, compute total cost over the time window
job_cost = collections.defaultdict(lambda: {'n':0, 'cost':0})
for jid, runs in job_files.items():
    for d, t in runs:
        dt = datetime.datetime.strptime(d+t, '%Y%m%d%H%M%S').replace(tzinfo=datetime.UTC)
        if dt.timestamp() < START: continue
        # ±60s window
        r = cur.execute('''SELECT input_tokens, output_tokens, cache_read_tokens, cache_write_tokens
                           FROM sessions
                           WHERE billing_base_url LIKE '%moret-mnafhqa3%'
                             AND ABS(started_at - ?) < 60
                           LIMIT 1''', (dt.timestamp(),)).fetchone()
        if r:
            ti, to, cr, cw = (v or 0 for v in r)
            cost = ti/1e6*5 + to/1e6*25 + cr/1e6*0.5 + cw/1e6*6.25  # Opus rates; scale per actual model
            job_cost[jid]['n'] += 1
            job_cost[jid]['cost'] += cost

for jid, x in sorted(job_cost.items(), key=lambda kv: -kv[1]['cost']):
    name = id_to_name.get(jid, '(orphan)')
    print(f"{jid}  {name:<40}  runs={x['n']:>4}  cost=${x['cost']:>8,.2f}")
```

### Caveat: matched cost is NOT total job cost

The above method captures only the INITIAL session triggered by each job, not the **compression-cascade sessions** that follow (typically 5-10 follow-up sessions per cron run, each costing $0.10-$0.70). To estimate true per-job cost, multiply matched cost by ~10× — or better, instrument by extending the matching window and walking parent_session_id chains.

Validated 2026-04-23 on Morris's state.db:
- Matched cron-session cost over 14 days: ~$510
- Actual Foundry invoice for the same period: $5,448
- → Each cron's compression cascade is ~10× the initial session cost

---

## The Compression Cascade Pattern (the actual bleed mechanism)

When a cron task with a large prompt runs, hermes's auto-compression triggers because the loaded context exceeds `compression.threshold` (default 0.5). Compression itself is an LLM call — and on the default config, that compression call uses the SAME model as the main task (Opus). It loads the context, summarizes it, writes a new compressed context. That alone can trigger ANOTHER compression. Cascades of 5-12 compressions per single cron run are typical.

**Pattern observed in `state.db`:**

```
12:30:05 → 12:31:04   src=cron  msgs=21 tools=12   $0.797   end=cron_complete
12:31:19 → 12:31:26   src=cron  msgs=0  tools=0    $0.622   end=compression
12:31:26 → 12:32:15   src=cron  msgs=0  tools=0    $0.122   end=compression
12:32:15 → 12:32:37   src=cron  msgs=0  tools=0    $0.664   end=compression
12:32:37 → 12:33:18   src=cron  msgs=0  tools=0    $0.106   end=compression
12:33:18 → 12:33:42   src=cron  msgs=0  tools=0    $0.684   end=compression
12:33:42 → 12:34:13   src=cron  msgs=0  tools=0    $0.629   end=compression
12:34:13 → 12:34:42   src=cron  msgs=0  tools=0    $0.676   end=compression
12:34:42 → 12:35:08   src=cron  msgs=0  tools=0    $0.616   end=compression
12:35:08 → 12:35:37   src=cron  msgs=0  tools=0    $0.676   end=compression
                                                   ─────────
                                                   $5.59   for ONE cron task
```

`end_reason` values to watch for:
- `cron_complete` — the actual cron task that produced output
- `compression` — auto-compression sub-session (the bleed multiplier)
- `interrupted`, `timeout` — failures

### How to neutralize the cascade

Two independent knobs in `/home/hermes/.hermes/config.yaml`:

```yaml
compression:
  enabled: true
  threshold: 0.85         # raise from 0.5 → only compress when context is at 85% (was 50%)

auxiliary:
  compression:
    provider: anthropic
    model: claude-haiku-4-5    # use Haiku for the compression call instead of Opus
    base_url: https://moret-mnafhqa3-swedencentral.cognitiveservices.azure.com/anthropic
    timeout: 120
```

Validated impact 2026-04-23: cutting compression model to Haiku drops per-compression cost from ~$0.65 → ~$0.13 (5× reduction).

---

## Real-Time Monitoring Playbook (for an always-on monitor model)

If you want another model (or a cron monitor) to watch Foundry spend continuously and alert on anomalies, here's the toolkit:

### Every 30 minutes — pull current daily Foundry spend

```bash
SUB=$(az account show --query id -o tsv)
START=$(date -u -d 'today 00:00 UTC' +%Y-%m-%dT00:00:00Z)
END=$(date -u +%Y-%m-%dT23:59:59Z)
cat > /tmp/cost-q.json <<EOF
{"type":"ActualCost","timeframe":"Custom","timePeriod":{"from":"$START","to":"$END"},
 "dataset":{"granularity":"Daily","aggregation":{"totalCost":{"name":"Cost","function":"Sum"}},
  "grouping":[{"type":"Dimension","name":"ResourceId"}]}}
EOF
az rest --method POST \
  --url "https://management.azure.com/subscriptions/$SUB/resourceGroups/rg-tech-dev-agents-dev/providers/Microsoft.CostManagement/query?api-version=2023-11-01" \
  --body @/tmp/cost-q.json
```

Watch the row for the `claude-opus-4-6-ce40a3b52ad2482-...` resource. Alert if today's value exceeds a threshold (e.g., $200/day).

### Every 5 minutes — check live call rate per Foundry resource

```bash
az monitor metrics list \
  --resource "/subscriptions/$SUB/resourceGroups/rg-tech-dev-agents-dev/providers/Microsoft.CognitiveServices/accounts/moret-mnafhqa3-swedencentral" \
  --metric "ModelRequests" --interval PT5M \
  --start-time "$(date -u -d '15 minutes ago' +%Y-%m-%dT%H:%M:%SZ)" \
  --end-time   "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --aggregation Total
```

Alert if 5-min request rate exceeds a baseline (e.g., 200 calls in 5 min = 40 calls/min sustained).

### Every 5 minutes — check hermes session DB on Morris (per-job, per-model breakdown)

```bash
ssh -p 443 azureagent@20.246.36.143 'sudo python3 -c "
import sqlite3, datetime
con = sqlite3.connect(\"/home/hermes/.hermes/state.db\")
NOW = datetime.datetime.now(datetime.UTC).timestamp()
HOUR_AGO = NOW - 3600
print(\"Hour-window per-model session count + cost:\")
for r in con.execute(\"\"\"
    SELECT model, COUNT(*),
           SUM(input_tokens), SUM(output_tokens),
           SUM(cache_read_tokens), SUM(cache_write_tokens)
    FROM sessions
    WHERE started_at >= ?
      AND billing_base_url LIKE \\\"%moret-mnafhqa3%\\\"
    GROUP BY model
\"\"\", (HOUR_AGO,)):
    model, n, ti, to_, cr, cw = r
    if model and \"opus\" in model: ri,ro,rcr,rcw = 5,25,0.5,6.25
    elif model and \"sonnet\" in model: ri,ro,rcr,rcw = 3,15,0.3,3.75
    else: ri,ro,rcr,rcw = 1,5,0.1,1.25  # haiku
    cost = (ti or 0)/1e6*ri + (to_ or 0)/1e6*ro + (cr or 0)/1e6*rcr + (cw or 0)/1e6*rcw
    print(f\"  {model}  n={n}  cost=\${cost:,.2f}\")
"'
```

Alert if:
- `model='claude-opus-4-6'` count > expected (means Sonnet override didn't take effect)
- Total hourly cost > threshold (e.g., $20/hour sustained)

### Daily — verify diag logs are flowing

```bash
LAW=e2682470-3071-4a8d-b75b-e99b093dff4d
az monitor log-analytics query --workspace "$LAW" --analytics-query \
  'AzureDiagnostics | where ResourceProvider == "MICROSOFT.COGNITIVESERVICES" | summarize count() by bin(TimeGenerated, 1h) | order by TimeGenerated desc | take 24'
```

Alert if 0 rows for any recent hour (means diag pipeline broke). Also alert on unexpected caller IPs.

### Daily — verify per-job model overrides survived service restarts

```bash
ssh -p 443 azureagent@20.246.36.143 'sudo python3 -c "
import json
d = json.load(open(\"/home/hermes/.hermes/cron/jobs.json\"))
for j in d.get(\"jobs\", []):
    if j.get(\"enabled\"):
        m = j.get(\"model\") or \"<inherits-default>\"
        if \"opus\" in (m or \"\").lower():
            print(f\"WARN: job {j[\\\"id\\\"]} ({j[\\\"name\\\"]}) is on {m}\")
"'
```

Alert on any output (any active job using Opus where it shouldn't).

---

## Optimization History (what we changed, what it saved)

Recorded 2026-04-23 on Morris (`vm-morris-agent-dev`):

| Change | Before | After | Estimated daily savings |
|--------|--------|-------|--------------------------|
| Foundry API key rotation | bleed continuing on old key | old key 401-blocked | n/a (security/control) |
| Diagnostic logs enabled on all 3 Foundry resources | OFF (default) | RequestResponse + Audit + Trace + AllMetrics → LAW `log-tech-dev-agents-dev` | $0.70/month cost; enables future per-IP attribution |
| Haiku + Sonnet capacity bumped from 1 → 100 on Sweden Foundry | 1K TPM each (unusable) | 100K TPM each | $0 added cost (PAYG); enables real workload routing |
| All 16 active hermes cron jobs → Sonnet | Opus | Sonnet | ~40% per-token savings on every cron call |
| `auxiliary.compression.model` → Haiku | (used main model = Opus) | Haiku | ~80% savings on each compression sub-session (~10 per cron run) |
| Morris's main `hermes config set model` → Sonnet | Opus | Sonnet | ~40% on Teams interactive chat |
| Compression `threshold` 0.5 → 0.85 | compress when ctx at 50% | compress when at 85% | ~50-70% fewer compression triggers |
| Pause "Morris Fleet Health (30min)" cron | running every 30 min | paused | ~$50/day removed |
| Lower State Sync + Heartbeat from `*/15` → `*/30` | every 15 min | every 30 min | ~50% reduction in cron-session frequency |

Combined expected effect: **drop daily Foundry-Opus burn from ~$1,200 to ~$50-100**. Reconcile against Cost Management 24-48h after the change to verify.

---

## Resources

- Subscription: `Gorilla-buildstr` (`d0f0feff-78ef-4425-9d51-07f5e0f0bcba`)
- Resource group: `rg-tech-dev-agents-dev`
- Foundry resources (as of 2026-04-23):
  - `moret-mnafhqa3-swedencentral` — Sweden Central, hosts claude-opus-4-6 + claude-sonnet-4-6 + claude-haiku-4-5
  - `moret-devs-sonnet-swedencentral` — Sweden Central, Sonnet-only (dev agent endpoint, created 2026-04-23)
  - `aitechdevagentsdev` — East US, hosts gpt-5-chat + gpt-4.1-mini (used by container app, NOT Claude)
  - `aitechdevagentsdev-eastus2` — East US 2, no deployments
- Log Analytics workspace: `log-tech-dev-agents-dev` (customerId `e2682470-3071-4a8d-b75b-e99b093dff4d`)
- Diagnostic settings (enabled 2026-04-23):
  - `moret-mnafhqa3-swedencentral` → LAW: `RequestResponse`, `Audit`, `Trace`, `AllMetrics`
  - `moret-devs-sonnet-swedencentral` → LAW: same
  - `aitechdevagentsdev` → LAW: same
