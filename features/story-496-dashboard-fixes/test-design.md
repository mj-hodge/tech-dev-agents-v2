# STORY-496: Test Design — Dashboard Fix

## Test Cases

### Issue 1: Quota Visibility Per Agent

**All quota tests MUST run for EVERY agent (dan, derrick, daisy, devon, morris). Use parameterized tests — one test function, iterated over all 5 agents. If any agent is missing quota data, the test fails for that agent specifically.**

**T1.1: Quota bar renders with percentage (per agent)**
- For each agent in [dan, derrick, daisy, devon, morris]:
  - Given: agent API returns `quota: {percent_used: 45, remaining_tokens: 70000000, reset_in_minutes: 180}`
  - Then: that agent's AgentCard shows a progress bar at 45%, green color, text "45% · resets in 3h"

**T1.2: Quota bar turns yellow at 50% (per agent)**
- For each agent in [dan, derrick, daisy, devon, morris]:
  - Given: `percent_used: 65`
  - Then: that agent's progress bar is yellow/amber

**T1.3: Quota bar turns red at 80% (per agent)**
- For each agent in [dan, derrick, daisy, devon, morris]:
  - Given: `percent_used: 90`
  - Then: that agent's progress bar is red

**T1.4: Quota shows "Rate Limited" when agent is out of capacity (per agent)**
- For each agent in [dan, derrick, daisy, devon, morris]:
  - Given: `quota: null` or `active_block: null` and agent is rate-limited
  - Then: that agent's card shows "Rate Limited" instead of a progress bar

**T1.5: FleetOverviewBar shows aggregate quota health across all agents**
- Given: dan rate-limited, derrick rate-limited, daisy at 45%, devon at 75%, morris at 10%
- Then: overview shows "Quota: 2 OK, 1 WARN, 2 Limited"

**T1.6: Every agent card has a quota section — none are missing**
- Given: fleet API returns all 5 agents
- Then: every rendered AgentCard contains a quota element (progress bar or rate-limited text) — zero agents have a blank/missing quota section

### Issue 2: Cost Display is Azure Foundry Only

**T2.1: Agent card shows "Foundry: $X.XX"**
- Given: `today_foundry_usd: 12.50, today_sdk_usd: 45.00`
- Then: card displays "Foundry: $12.50" — SDK cost is NOT shown

**T2.2: FleetOverviewBar daily spend is Foundry only**
- Given: `total_daily_spend_usd: 25.00` (Foundry)
- Then: bar shows "Daily Foundry Spend: $25.00"

**T2.3: No SDK cost anywhere in the default view**
- Then: no element on the page contains "SDK" or "sdk" in cost context

### Issue 3: Presence/Status Accuracy

**All status tests MUST run for EVERY agent (dan, derrick, daisy, devon, morris). Use parameterized tests. Each agent's status must be individually correct — one wrong status is a test failure for that specific agent.**

**T3.1: Rate-limited agent shows "Rate Limited" (per agent)**
- For each agent in [dan, derrick, daisy, devon, morris]:
  - Given: that agent's status = "rate_limited", reset_time = "Apr 23, 7pm UTC"
  - Then: that agent's StatusBadge shows red/amber "Rate Limited — resets Apr 23, 7pm"

**T3.2: Working agent shows story + phase (per agent)**
- For each agent in [dan, derrick, daisy, devon, morris]:
  - Given: that agent's status = "working", current_story = "STORY-447", current_phase = "Phase 8 (Implementation)"
  - Then: that agent's StatusBadge shows green "Working"
  - And: that agent's card shows "STORY-447 Phase 8 (Impl)"

**T3.3: Idle agent shows "Idle" (per agent)**
- For each agent in [dan, derrick, daisy, devon, morris]:
  - Given: that agent's status = "idle"
  - Then: that agent's StatusBadge shows blue "Idle"

**T3.4: Offline agent shows "Offline" (per agent)**
- For each agent in [dan, derrick, daisy, devon, morris]:
  - Given: that agent's status = "offline"
  - Then: that agent's StatusBadge shows gray "Offline"

**T3.5: "Busy (SDK active/waiting)" text does NOT appear for any agent**
- Given: fleet renders all 5 agent cards
- Then: no element on the page contains "Busy (SDK active/waiting)"

**T3.6: Mixed fleet state renders correctly for all agents simultaneously**
- Given: dan=rate_limited, derrick=rate_limited, daisy=working(STORY-447 Phase 8), devon=working(STORY-443 Phase 7), morris=idle
- Then: each of the 5 agent cards shows the correct status for THAT agent — no agent inherits another agent's status

**T3.7: Every agent card has a status badge — none are missing**
- Given: fleet API returns all 5 agents
- Then: every rendered AgentCard contains a StatusBadge element — zero agents have blank/missing status

### Issue 4: FleetOverviewBar Accurate Counts

**T4.1: Active agents count is correct**
- Given: 4 agents total, Dan/Derrick rate-limited, Daisy/Devon working
- Then: "Active: 2/4"

**T4.2: Busy agents count matches working agents**
- Given: Daisy working, Devon working, Dan rate-limited, Derrick rate-limited
- Then: "Busy: 2"

**T4.3: Active stories count matches claimed in queue**
- Given: dispatch queue has 2 claimed items
- Then: "Active Stories: 2"

**T4.4: Queued stories count matches pending in queue**
- Given: dispatch queue has 3 pending items
- Then: "Queued: 3"

**T4.5: All-zero state does NOT happen when agents are online**
- Given: at least 1 agent has recent Loki activity
- Then: active_agents > 0

### Issue 5: Queue Three-Tab Display

**T5.1: Queue shows Pending tab**
- Given: 2 pending stories in queue
- Then: Pending tab shows 2 items with story ID, repo, scope, age

**T5.2: Queue shows In Progress tab**
- Given: 1 claimed story with SDK running
- Then: In Progress tab shows 1 item with agent name, story ID, phase, duration

**T5.3: Queue shows In Review tab**
- Given: 1 story with status "in_review" (PR created, waiting for review)
- Then: In Review tab shows 1 item with PR number/link

**T5.4: Completed stories are NOT in the queue view**
- Given: story status = "completed"
- Then: not visible in any tab

### Issue 6: Work Detail Shows Story + Phase + Duration

**T6.1: Working agent shows specific work detail**
- Given: Loki shows `[DISPATCH] Phase 8 (Implementation) for STORY-447 — starting SDK` at 14:00, current time 14:12
- Then: agent card shows "STORY-447 Phase 8 (Impl) — 12m"

**T6.2: Idle agent shows last completed story**
- Given: Loki shows `[DISPATCH] STORY-400 COMPLETE in 662s` 5 minutes ago
- Then: agent card shows "Idle — last: STORY-400 (5m ago)"

**T6.3: Rate-limited agent shows rate limit info**
- Given: agent is rate-limited, reset = "Apr 23, 7pm UTC"
- Then: agent card shows "Rate Limited — resets Apr 23, 7pm"

**T6.4: No "CONTEXT FROM DISPATCH" visible anywhere**
- Then: no element contains "CONTEXT FROM DISPATCH"

## Test Files

### Backend Tests
- `tests/ops_console/test_story496_quota_endpoint.py` — T1.1-T1.5 (API returns correct quota data)
- `tests/ops_console/test_story496_agent_status.py` — T3.1-T3.5 (status classification from Loki)
- `tests/ops_console/test_story496_fleet_overview.py` — T4.1-T4.5 (accurate fleet counts)

### Frontend Tests  
- `frontend/src/__tests__/AgentCard.story496.test.tsx` — T1.1-T1.4, T2.1, T3.1-T3.5, T6.1-T6.4
- `frontend/src/__tests__/FleetOverviewBar.story496.test.tsx` — T1.5, T2.2, T4.1-T4.5
- `frontend/src/__tests__/DispatchQueue.story496.test.tsx` — T5.1-T5.4
- `frontend/src/__tests__/StatusBadge.story496.test.tsx` — T3.1-T3.5
