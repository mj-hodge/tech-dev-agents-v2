---
name: research
description: Dispatch a research question to an agent via the ops-console queue. The agent writes findings to a feature branch — no PR, no seed.md, just research output.
---

# /research — Dispatch Research to an Agent

Send a research question to your agent fleet. An available agent (daisy or devon) claims it, investigates, and writes findings to a feature branch. No PR is created — research is lightweight.

## Usage

```
/research "<question>"                              # Auto-derives next STORY-ID
/research --story-id=STORY-N "<question>"           # Use a specific story ID
/research --target-agent=daisy "<question>"          # Target a specific agent
/research --output=features/story-N-slug/research.md "<question>"  # Custom output path
```

### Examples

```
/research "How does our completion gate handle deliverables at non-standard paths?"
/research --story-id=STORY-550 "Survey rate-limit reset parsing across the codebase"
/research "What are the tradeoffs between polling and webhooks for Grafana alerts?"
```

## Steps

Follow these steps in order. Execute shell commands via Bash tool.

### 1. Derive the next story ID

If the user did NOT provide `--story-id`, derive the next available ID:

```bash
HIGHEST=$(ls features/ | grep -E '^story-[0-9]+' | sort -V | tail -1 | grep -oE '[0-9]+')
NEXT_ID=$((HIGHEST + 1))
echo "STORY-${NEXT_ID}"
```

If the user provided `--story-id=STORY-N`, use that instead.

### 2. Derive the story folder slug from the question

Take the first 5–7 words of the question, lowercase them, replace spaces with hyphens, strip punctuation, and trim to 40 characters.

**Example:**
- Question: `"How does our completion gate handle deliverables at non-standard paths?"`
- Slug: `how-does-our-completion-gate-handle`
- Folder: `features/story-${NEXT_ID}-how-does-our-completion-gate-handle/`

### 3. Construct the POST body

Build a JSON payload with `scope=research`. The user's question goes verbatim into `prompt`. Do NOT create a `seed.md` — research scope skips seed creation (per STORY-539).

```json
{
  "story_id": "STORY-${NEXT_ID}",
  "repo": "tech-dev-agents",
  "scope": "research",
  "prompt": "<the user's question verbatim>",
  "enqueued_by": "mark",
  "title": "Research: <short summary of the question>"
}
```

Optional fields:
- `"target_agent": "daisy"` — if the user specified `--target-agent`
- `"output_path": "features/story-N-slug/research.md"` — if the user specified `--output`

### 4. POST to the dispatch API

Send the request using `curl`. Use `$OPS_CONSOLE_URL` and `$OPS_CONSOLE_API_KEY` from environment — never hardcode these values.

```bash
curl -s -X POST "${OPS_CONSOLE_URL}/api/dispatch" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: ${OPS_CONSOLE_API_KEY}" \
  -d '{
    "story_id": "STORY-'"${NEXT_ID}"'",
    "repo": "tech-dev-agents",
    "scope": "research",
    "prompt": "'"${QUESTION}"'",
    "enqueued_by": "mark",
    "title": "Research: '"${SHORT_TITLE}"'"
  }'
```

### 5. Display the result

Show the user:
- The HTTP status (expect **201 Created**)
- The `story_id` assigned
- The `queue_depth` from the response
- A note: "An agent will claim this on the next dispatch tick (~60s). Research output will appear on branch `story-N/story-N`. No PR will be created."

**Example output:**
```
✅ Research dispatched as STORY-541
   Queue depth: 2
   An agent will claim this on the next tick (~60s).
   Output branch: story-541/story-541
   No PR will be created — check the branch directly.
```

## Override Defaults

| Flag | Default | Description |
|------|---------|-------------|
| `--story-id=STORY-N` | Auto-derived (highest + 1) | Use a specific story ID instead of auto-deriving |
| `--target-agent=NAME` | Any available agent | Route to a specific agent (e.g., `daisy`, `devon`) |
| `--output=PATH` | Agent decides | Specify where the agent should write its findings |

## Notes

- Research dispatches use `scope=research` which runs a single-phase SDLC (research only, no tests, no PR).
- The agent writes findings to the feature branch and notifies via Teams when done.
- This skill is read-only on your local machine — all work happens on the agent VM.
- If the dispatch returns 409 (conflict), the story ID is already in use. Increment and retry.
