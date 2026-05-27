# STORY-1015 — Retire data-pipelines-runbooks → merge into gc-data-v2/platform/runbooks/ + cross-repo index

**Story ID:** STORY-1015
**Scope:** Small
**Epic:** STORY-1000 (Cross-Repo Canon Alignment)
**Work-Stream:** D — Knowledgebase curation
**Repos touched (cross-repo):**
- `data-pipelines-runbooks` (move content out; archive repo — **IRREVERSIBLE**)
- `gc-data-v2` (new `platform/runbooks/` directory + content)
- `tech-gc-knowledgebase` (cross-repo index + stale-reference fix at `wiki/processes/data-pipeline-operations.md:95`)
- `tech-dev-agents` (SDLC artifacts only)
**Date:** 2026-05-18
**Status:** Phase 1 — seed draft, gated awaiting approval
**Frontend:** false

---

## Problem

The org has **three places** runbook content might live, and the reader has no signpost:

1. `data-pipelines-runbooks/` — public repo, 4 paths: `runbooks/rb_template.md` (a 121-line skeleton, never instantiated), `diagrams/amazon-api-pipelines.{png,puml}`, `scripts/`, `templates/`. `README.md` (lines 1-3) says nothing more than the repo name. **Last meaningful push 2025-11-19 — stale 6 months.**

2. `gc-data-v2/platform/` — has `failure-modes.md`, `observability.md`, `appinsights-query-guide.md`, `pipeline-integration.md`, but **no `runbooks/` subdirectory**. Operational content is scattered across `platform/*.md`.

3. `tech-gc-knowledgebase/wiki/processes/data-pipeline-operations.md:95-96` — points readers AT `data-pipelines-runbooks` with the disclaimer "last pushed 2025-11-19, marked Stale." Reader is left without a current source.

The 5-repo discovery surface promised by epic SC-7 needs to collapse these three options into one. Also, `tech-gc-knowledgebase/wiki/systems/` has 30+ system pages but no **cross-repo data-platform index** — no single page where a new engineer can find "for source X: which spec, which platform doc, which failure-mode."

## Goal

1. **Move** the live artifacts from `data-pipelines-runbooks` into `gc-data-v2/platform/runbooks/`:
   - `runbooks/rb_template.md` → `gc-data-v2/platform/runbooks/RUNBOOK_TEMPLATE.md`
   - `diagrams/amazon-api-pipelines.{png,puml}` → `gc-data-v2/platform/runbooks/diagrams/amazon-api-pipelines.{png,puml}`
   - `scripts/` and `templates/` — Phase 6 audits contents; move only if currently referenced anywhere; otherwise document as discarded.
2. **Fix the stale reference** at `tech-gc-knowledgebase/wiki/processes/data-pipeline-operations.md:95-96` to point to the new `gc-data-v2/platform/runbooks/` location.
3. **Create** `tech-gc-knowledgebase/wiki/systems/data-platform-specs.md` — a cross-repo index. One row per source linking to: (a) `gc-data-v2/sources/{src}/README.md`, (b) the most-relevant `gc-data-v2/platform/*.md` doc, (c) the matching `gc-data-v2/platform/failure-modes.md` section.
4. **Archive** `hpi-gorillacommerce/data-pipelines-runbooks` via `gh repo edit --archived` — IRREVERSIBLE; gated explicitly on Mark approval as the final commit action.

## Scope

**In:**
- File move (PR against `gc-data-v2`).
- Stale-ref fix + cross-repo index (PR against `tech-gc-knowledgebase`).
- Repo archive (final operation, after both PRs merge).
- Verification that no other repo references `data-pipelines-runbooks` URLs (grep across `gc-data-v2`, `tech-gc-knowledgebase`, `tech-dev-agents`).

**Out:**
- Filling in runbook content for actual pipelines (the template is moved as a template; instantiation per pipeline is a follow-up — typically owned by the pipeline-repo's own runbook task).
- Updating `gc-data-v2/AGENTS.md` to reference `platform/runbooks/` (canonical platform doc list change — defer to a future canon update).
- New `failure-modes.md` sections (the index links to existing sections, doesn't create new ones).
- Anything in the 8 source spec READMEs themselves (STORY-1013 territory).

## Out of scope (explicit Do-Not-Do)

- Do NOT instantiate a real runbook (e.g. "spapi-runbook.md") in this story. Template only; instantiation is per-pipeline owner's responsibility.
- Do NOT mass-rewrite anything else in `tech-gc-knowledgebase/wiki/` — STORY-1016 handles the freshness frontmatter rollout.
- Do NOT delete files from `data-pipelines-runbooks` before archive — let the archive freeze them as historical record. The move is a copy-then-archive, not a `git rm`.
- Do NOT archive `data-pipelines-runbooks` without explicit "yes, archive" from Mark via `needs_info` or directive.

## Success criteria

- **SC-1** — `ls /mnt/c/Projects/gc-data-v2/platform/runbooks/RUNBOOK_TEMPLATE.md` exists, file identical (line-for-line) to original `data-pipelines-runbooks/runbooks/rb_template.md` modulo path-relative link rewrites.
- **SC-2** — `ls /mnt/c/Projects/gc-data-v2/platform/runbooks/diagrams/amazon-api-pipelines.png` and `.puml` exist; image is byte-identical to source; `.puml` is byte-identical or has only path-link updates.
- **SC-3** — `grep -n "data-pipelines-runbooks" /mnt/c/Projects/tech-gc-knowledgebase/wiki/processes/data-pipeline-operations.md` returns 0 results (or only matches an explicit "previously lived in data-pipelines-runbooks (archived)" historical note).
- **SC-4** — `ls /mnt/c/Projects/tech-gc-knowledgebase/wiki/systems/data-platform-specs.md` exists. Page contains one row per source currently in `gc-data-v2/sources/` — at minimum: `amazon-sp-api`, `amazon-ads-api`, `walmart-supplier`, `walmart-ad-connect`, `netsuite`, `shopify`, `toolio`, `other-platforms` (8 rows).
- **SC-5** — Each index row has three working markdown links: source-spec, platform-doc, failure-modes anchor. `find` + `markdown-link-check` (or equivalent) returns 0 broken links from `data-platform-specs.md`.
- **SC-6** — Cross-repo grep for stale `data-pipelines-runbooks` references returns 0 (excluding the archive-historical note): `for r in gc-data-v2 tech-gc-knowledgebase tech-dev-agents; do grep -rln "data-pipelines-runbooks" /mnt/c/Projects/$r ; done` → empty (or only intentional archive-note).
- **SC-7** — `gh repo view hpi-gorillacommerce/data-pipelines-runbooks --json isArchived` returns `{"isArchived": true}` (LAST step, after Mark approval).
- **SC-8** — Pre-archive backup: a final commit-pinned tag exists on `data-pipelines-runbooks` (e.g. `pre-archive-2026-XX-XX`) so the archive freezes a known SHA.

## Files to modify (grouped by repo)

### `gc-data-v2` (NEW files)
- `platform/runbooks/RUNBOOK_TEMPLATE.md` — copy of `data-pipelines-runbooks/runbooks/rb_template.md`.
- `platform/runbooks/diagrams/amazon-api-pipelines.png` — binary copy.
- `platform/runbooks/diagrams/amazon-api-pipelines.puml` — text copy (relative-path updates if needed).
- `platform/runbooks/README.md` — new index for `platform/runbooks/`; states the template's purpose, when to instantiate, and where instantiated runbooks live (per-pipeline-repo `runbooks/` subdir, e.g. `walmart-supplier-v2/runbooks/walmart-supplier-runbook.md`).

### `gc-data-v2` (NO edit to existing platform docs)
- `platform/failure-modes.md`, `observability.md`, etc. — referenced by the new index, not modified.

### `tech-gc-knowledgebase`
- `wiki/processes/data-pipeline-operations.md` — edit lines 95-96 (current text: "Historical runbook documentation lives in `data-pipelines-runbooks` (last pushed 2025-11-19, marked Stale). Current runbooks may be..."). Replace with a pointer to `gc-data-v2/platform/runbooks/` and the cross-repo index at `wiki/systems/data-platform-specs.md`.
- `wiki/systems/data-platform-specs.md` — NEW cross-repo index page.
- `wiki/systems/data-pipelines-runbooks.md` — EXISTING page (already in `wiki/systems/`). Update to add an "Archived 2026-XX-XX" banner and redirect to `gc-data-v2/platform/runbooks/` and the new index.

### `data-pipelines-runbooks`
- No file edits (move is copy-out; archive freezes).
- Repo-level action: `gh repo edit hpi-gorillacommerce/data-pipelines-runbooks --archived`.
- Tag: `git -C data-pipelines-runbooks tag pre-archive-2026-XX-XX && git -C data-pipelines-runbooks push origin pre-archive-2026-XX-XX`.

### `tech-dev-agents` (SDLC artifacts)
- `features/story-1015-retire-runbooks-and-index/seed.md` (this file)
- `features/story-1015-retire-runbooks-and-index/test-design.md` (Phase 7)

## Files to NOT modify

- `gc-data-v2/AGENTS.md`, `README.md`, `sources.yaml` — out of scope.
- `gc-data-v2/sources/*` — STORY-1013 territory.
- Any `platform/*.md` (modify only by adding `platform/runbooks/` subdir).
- `tech-gc-knowledgebase/wiki/**` files other than the two named above.
- Any `*-v2` pipeline repo's `runbooks/` (those are owned by each pipeline; this story moves the canonical template only).

## Verification plan

| Check | Command | Expected output |
|---|---|---|
| Template moved | `diff /mnt/c/Projects/data-pipelines-runbooks/runbooks/rb_template.md /mnt/c/Projects/gc-data-v2/platform/runbooks/RUNBOOK_TEMPLATE.md` | Empty or only known link-rewrites |
| Diagrams moved (binary) | `sha256sum /mnt/c/Projects/data-pipelines-runbooks/diagrams/amazon-api-pipelines.png /mnt/c/Projects/gc-data-v2/platform/runbooks/diagrams/amazon-api-pipelines.png` | Identical hashes |
| Stale ref removed | `grep -c "data-pipelines-runbooks" /mnt/c/Projects/tech-gc-knowledgebase/wiki/processes/data-pipeline-operations.md` | `0` (or `1` if intentional archive note) |
| New index exists | `ls /mnt/c/Projects/tech-gc-knowledgebase/wiki/systems/data-platform-specs.md` | File listed |
| Index has all sources | `grep -cE '\\| (amazon-sp-api\|amazon-ads-api\|walmart-supplier\|walmart-ad-connect\|netsuite\|shopify\|toolio\|other-platforms) \\|' /mnt/c/Projects/tech-gc-knowledgebase/wiki/systems/data-platform-specs.md` | `8` |
| No broken links from index | `npx markdown-link-check /mnt/c/Projects/tech-gc-knowledgebase/wiki/systems/data-platform-specs.md --config link-check-config.json` (config allows file:// to gc-data-v2 paths) | 0 dead links |
| Cross-repo grep clean | `for r in gc-data-v2 tech-gc-knowledgebase tech-dev-agents ; do grep -rln "data-pipelines-runbooks" "/mnt/c/Projects/$r" ; done \| grep -v "archived\|historical"` | empty |
| Pre-archive tag pushed | `git -C /mnt/c/Projects/data-pipelines-runbooks tag --list 'pre-archive-*'` | One row |
| Repo archived | `gh repo view hpi-gorillacommerce/data-pipelines-runbooks --json isArchived --jq .isArchived` | `true` |
| Replay (epic SC-7) | Same `gh repo view` + `ls ... data-platform-specs.md` | Both succeed |

## Boundaries

| Always Do | Ask First | Never Do |
|---|---|---|
| Copy files preserving byte-content (binary-safe for PNG/PUML) | **Archive `data-pipelines-runbooks` on GitHub** — IRREVERSIBLE; explicit Mark approval required before `gh repo edit --archived` is run | Run `gh repo delete` — archive is the destination, never delete |
| Tag `pre-archive-2026-XX-XX` on `data-pipelines-runbooks` HEAD before archive | Move `scripts/` or `templates/` from data-pipelines-runbooks — Phase 6 audits whether they're still referenced | Force-push to any of the three repos |
| Update the cross-repo index when a new source lands (codify this in the new index's own header) | Update `gc-data-v2/AGENTS.md` to list `platform/runbooks/` (defer) | Archive without Mark's explicit "yes" — see SC-7 |
| Add an "Archived 2026-XX-XX → see gc-data-v2/platform/runbooks/" banner to `wiki/systems/data-pipelines-runbooks.md` | Rewrite the moved template's content (it's a template, not specific) | Instantiate per-pipeline runbooks here (per-pipeline-repo concern) |

## Done looks like

```
$ ls /mnt/c/Projects/gc-data-v2/platform/runbooks/
README.md
RUNBOOK_TEMPLATE.md
diagrams/

$ grep -c "data-pipelines-runbooks" /mnt/c/Projects/tech-gc-knowledgebase/wiki/processes/data-pipeline-operations.md
0

$ ls /mnt/c/Projects/tech-gc-knowledgebase/wiki/systems/data-platform-specs.md
.../data-platform-specs.md

$ gh repo view hpi-gorillacommerce/data-pipelines-runbooks --json isArchived
{"isArchived": true}

$ git -C /mnt/c/Projects/data-pipelines-runbooks tag --list 'pre-archive-*'
pre-archive-2026-XX-XX
```

## Escalation contract

- **Archive is irreversible.** Phase 8 MUST NOT call `gh repo edit --archived` until:
  1. Both PRs (gc-data-v2 + tech-gc-knowledgebase) are MERGED to `main`.
  2. The `pre-archive-2026-XX-XX` tag is pushed to origin on `data-pipelines-runbooks`.
  3. Mark gives explicit text approval ("archive it" / "yes, archive") via `needs_info` response or direct directive.
  4. SC-6 cross-repo grep returns clean.
- If any of `scripts/` or `templates/` in `data-pipelines-runbooks` is currently referenced by a CI workflow or another repo, STOP, file `needs_info` — moving it may break the referencer; we may need to preserve it elsewhere first.
- If `wiki/systems/data-platform-specs.md` already exists at PR-open time (someone else got there first), STOP and reconcile via `needs_info`.


## Test Criteria

| Test | Validates |
|------|-----------|
| RED tests from Phase 7 (see `## Verification plan`) | Every SC has a literal test or shell command |
| Full pytest suite GREEN, zero regressions | No unrelated breakage |
| Incident-replay tests (where applicable) | The 2026-05-04..05-18 failure classes are catchable by the new gate |

See `## Success criteria` and `## Verification plan` above for SC-keyed predicates.


## Validation

After merge:

1. **Tests:** all RED tests turned GREEN; `pytest <story test paths>` exits 0.
2. **No regressions:** full suite passes; CI green on the PR.
3. **Per-SC verification:** every command in `## Verification plan` runs and produces the expected output.
4. **Tracking updated:** `.project` and `backlog.md` reflect Phase 8 completion; Monday.com task updated.

## Phase path

**Small scope → `1 → 7 → 8 → Done`.**

No Phase 6 — the design is dictated by the spec (3 file moves, 1 ref fix, 1 new index page, 1 archive). Phase 7 writes verification: link-check the new index, byte-compare the moved files, grep-clean assertion. Phase 8 (a) opens PR against `gc-data-v2`, (b) opens PR against `tech-gc-knowledgebase`, (c) once both merge + Mark approves, runs the archive command.

## Cross-PR coordination

PRs are independent at the diff level but coordinated at the merge level:
- PR1 (`gc-data-v2/feat/platform-runbooks`) → review by Morris (`review-prs`), merge to main.
- PR2 (`tech-gc-knowledgebase/feat/data-platform-specs-index`) → review by Morris, merge to main. Must reference PR1 in description (links into `gc-data-v2/platform/runbooks/` only work after PR1 merges).
- ARCHIVE (`gh repo edit --archived`) → after PR1 + PR2 merged, after Mark approves.

Morris's `merge` skill MUST NOT auto-merge PR2 before PR1 is on main — codify this in PR descriptions with `Depends-On: gc-data-v2#<PR1>`.

## Reference — the stale text being replaced

`tech-gc-knowledgebase/wiki/processes/data-pipeline-operations.md` lines 95-96 (current state, anchor for the fix):

```
Historical runbook documentation lives in `data-pipelines-runbooks`
(last pushed 2025-11-19, marked Stale). Current runbooks may be
```

Replacement (Phase 8): redirect to `gc-data-v2/platform/runbooks/` and the cross-repo index `wiki/systems/data-platform-specs.md`. Keep an explicit "Archive note: `data-pipelines-runbooks` was retired YYYY-MM-DD; historical content frozen at tag `pre-archive-YYYY-MM-DD`" line for traceability.

## Reference — current `data-pipelines-runbooks` inventory

```
/mnt/c/Projects/data-pipelines-runbooks/
├── README.md          (3 lines; states only "Runbooks, documentation, and scripts for Azure/Fabric data pipelines")
├── diagrams/
│   ├── amazon-api-pipelines.png    (binary; the only diagram)
│   └── amazon-api-pipelines.puml   (PlantUML source for the above)
├── runbooks/
│   └── rb_template.md  (121 lines; 11-section runbook skeleton, never instantiated against any real pipeline)
├── scripts/            (Phase 6 audits contents — preserve or discard per use)
└── templates/          (Phase 6 audits contents — preserve or discard per use)
```

`rb_template.md`'s 11 sections (lines 1-119): Overview · Architecture Diagram · Data Sources · Pipeline Steps / Logic · Inputs & Outputs · Monitoring & Alerts · Troubleshooting · Recovery & Backfill · Configuration & Secrets · Change Management · References. This is the canonical template moved to `gc-data-v2/platform/runbooks/RUNBOOK_TEMPLATE.md`.

## Cross-repo index page — required shape

`tech-gc-knowledgebase/wiki/systems/data-platform-specs.md` must include:

```markdown
---
last-reviewed: 2026-05-18
owner: data-platform
freshness-sla: 90
---

# Data Platform Specs — Cross-Repo Index

> Authoritative index across `gc-data-v2/` (canon + sources), this wiki, and per-pipeline `*-v2` repos.

## Sources

| Source | Spec README | Platform doc(s) | Failure modes |
|--------|-------------|-----------------|---------------|
| amazon-sp-api | [gc-data-v2/sources/amazon-sp-api/README.md](...) | [platform/ingestion-patterns.md](...) | [platform/failure-modes.md#sp-api](...) |
| amazon-ads-api | ... | ... | ... |
| walmart-supplier | ... | ... | ... |
| walmart-ad-connect | ... | ... | ... |
| netsuite | ... | ... | ... |
| shopify | ... | ... | ... |
| toolio | ... | ... | ... |
| other-platforms | ... | ... | ... |

## Runbooks

- Template: [gc-data-v2/platform/runbooks/RUNBOOK_TEMPLATE.md](...)
- Per-pipeline instances live in each `*-v2` repo's `runbooks/` directory.

## Retired

- `data-pipelines-runbooks` (archived YYYY-MM-DD, tag `pre-archive-YYYY-MM-DD`)
```

Frontmatter is added even though STORY-1016's broader backfill may run later — the new file ships ready for STORY-1016's gate.
