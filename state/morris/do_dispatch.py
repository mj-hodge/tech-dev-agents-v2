import urllib.request, json

with open("/opt/agent/.env") as f:
    for line in f:
        if line.startswith("OPS_CONSOLE_API_KEY="):
            key = line.strip().split("=", 1)[1]

base = "https://tech-dev-agents.gorillacommerce.ai/api/dispatch"

stories = [
    {"story_id": "STORY-929", "repo": "advertising-amazon", "scope": "small",
     "cross_story_reference": True, "enqueued_by": "morris",
     "title": "Rebase PR #398 (STORY-707 OODA Scorer)",
     "prompt": "Rebase PR #398 (STORY-707 OODA Scorer) onto main and push. This PR is APPROVED but has merge conflicts in tracking files (.project, backlog.md, development-tasks.md, CHANGELOG.md). Steps: 1) git fetch origin main 2) git checkout story-707/ooda-scorer 3) git rebase origin/main 4) Resolve ALL conflicts keeping ALL story rows from BOTH sides, deduplicate by story ID 5) git push --force-with-lease origin story-707/ooda-scorer (use --force if needed) 6) Verify push succeeded. Rebase-only task. Do NOT create new deliverables or branch. Cross-reference: STORY-707"},
    {"story_id": "STORY-930", "repo": "api-retail-target", "scope": "small",
     "cross_story_reference": True, "enqueued_by": "morris",
     "title": "Rebase PR #17 (STORY-008 Item Setup)",
     "prompt": "Rebase PR #17 (STORY-008 Item Setup Phase 6) onto master and push. IMPORTANT: This repo uses master NOT main. This PR is APPROVED but has merge conflicts in tracking files. Steps: 1) git fetch origin master 2) git checkout story-008/story-008 3) git rebase origin/master 4) Resolve ALL conflicts keeping ALL story rows from BOTH sides, deduplicate by story ID 5) git push --force-with-lease origin story-008/story-008 (use --force if needed) 6) Verify push succeeded. Rebase-only task. Do NOT create new deliverables or branch. Cross-reference: STORY-008"},
    {"story_id": "STORY-931", "repo": "api-retail-target", "scope": "small",
     "cross_story_reference": True, "enqueued_by": "morris",
     "title": "Rebase PR #11 (STORY-007 Discovery Harness)",
     "prompt": "Rebase PR #11 (STORY-007 Discovery Harness) onto master and push. IMPORTANT: This repo uses master NOT main. This PR is APPROVED but has merge conflicts. Steps: 1) git fetch origin master 2) git checkout story-007/story-007 3) git rebase origin/master 4) Resolve ALL conflicts keeping ALL story rows from BOTH sides 5) git push --force-with-lease origin story-007/story-007 (use --force if needed) 6) Verify push succeeded. Rebase-only task. Do NOT create new deliverables or branch. Cross-reference: STORY-007"},
    {"story_id": "STORY-932", "repo": "tech-dev-agents", "scope": "small",
     "cross_story_reference": True, "enqueued_by": "morris",
     "title": "Rebase PR #257 (STORY-803 needs_info loop)",
     "prompt": "Rebase PR #257 (STORY-803 needs_info loop fix) onto main and push. This PR is APPROVED but has merge conflicts. Steps: 1) git fetch origin main 2) git checkout story-803/story-803 3) git rebase origin/main 4) Resolve ALL conflicts keeping ALL content from BOTH sides 5) git push --force-with-lease origin story-803/story-803 (use --force if needed) 6) Verify push succeeded. Rebase-only task. Do NOT create new deliverables or branch. Cross-reference: STORY-803"},
]

for s in stories:
    data = json.dumps(s).encode()
    req = urllib.request.Request(base, data=data, headers={"X-API-Key": key, "Content-Type": "application/json"})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=10))
        print("OK", s["story_id"], "depth=" + str(d["queue_depth"]))
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        print("FAIL", s["story_id"], e.code, body)
