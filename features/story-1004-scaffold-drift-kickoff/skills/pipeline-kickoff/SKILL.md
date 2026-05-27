---
name: pipeline-kickoff
description: >
  Kick off a new data pipeline story. Validates that the pipeline's data source is
  registered in the shared catalog (tech-project-mapping/catalog/data-sources.yaml)
  before starting. If the source is missing, shows the expected YAML block and prompts
  to register it first. Once validated, loads pipeline-kickoff-prompt.md and runs
  Phase 1 for the pipeline story.
triggers:
  - pipeline kickoff
  - new pipeline
  - kick off pipeline
  - start pipeline story
  - pipeline story
---

# pipeline-kickoff — New Data Pipeline Story Gate

Before kicking off a new pipeline story, confirm the data source is registered in the
shared catalog. An unregistered source will block integration testing later — validate
it now.

## Usage

```
/pipeline-kickoff "<pipeline-name>" "<source-system>"
```

Example:

```
/pipeline-kickoff "shopify-orders-daily" "shopify"
/pipeline-kickoff "netsuite-gl-sync" "netsuite-production"
```

## Steps

1. **Locate the catalog file.**

   Look for the shared data-source catalog at one of these paths (relative to the
   workspace root):

   ```
   tech-project-mapping/catalog/data-sources.yaml
   ../tech-project-mapping/catalog/data-sources.yaml
   ```

   If neither path exists, note it and proceed to Step 3 with a warning.

2. **Check if the source is registered.**

   The catalog file (`sources.yaml` / `data-sources.yaml`) contains entries like:

   ```yaml
   sources:
     - name: shopify
       type: ecommerce
       owner: data-team
       ...
     - name: netsuite-production
       type: erp
       owner: finance-team
       ...
   ```

   Run:

   ```bash
   # Check if the named source appears in the catalog
   SOURCE="<source-system-name>"
   CATALOG_FILE="tech-project-mapping/catalog/data-sources.yaml"

   if [ ! -f "$CATALOG_FILE" ]; then
     CATALOG_FILE="../tech-project-mapping/catalog/data-sources.yaml"
   fi

   if grep -q "name: $SOURCE" "$CATALOG_FILE" 2>/dev/null; then
     echo "PASS: source '$SOURCE' is registered in the catalog"
   else
     echo "FAIL: source '$SOURCE' not found in $CATALOG_FILE"
   fi
   ```

3. **If source is NOT registered:**

   Show the user the expected YAML block to add and stop:

   ```
   Source '<source-system>' is not registered in the shared catalog.

   Add this block to tech-project-mapping/catalog/data-sources.yaml:

   sources:
     - name: <source-system>
       type: <ecommerce|erp|analytics|crm|custom>
       owner: <team-name>
       description: "<one-line description>"
       connection_tested: false
       added_by: "<your name>"
       added_date: "<YYYY-MM-DD>"

   Once added, commit the catalog change, then re-run /pipeline-kickoff.
   ```

   **Do not proceed with Phase 1 until the source is registered.**

4. **If source IS registered (or user confirms it is):**

   Load the Phase 1 template from `pipeline-kickoff-prompt.md` (in the same feature
   folder, or at `.sdlc/templates/pipeline-kickoff-prompt.md` after Mark copies it).

   Use the template to structure the Phase 1 seed for the new pipeline story. Substitute:
   - `{{pipeline_name}}` → the pipeline name the user provided
   - `{{source_system}}` → the registered source name
   - `{{today}}` → today's date (YYYY-MM-DD)

5. **Run Phase 1 using the populated template.**

   Write the seed to:

   ```
   features/story-<STORY-N>-<pipeline-slug>/seed.md
   ```

   Where `<pipeline-slug>` is derived from the pipeline name (lowercase, hyphenated,
   max 40 chars).

   Example: `pipeline-kickoff "shopify-orders-daily"` → folder
   `features/story-1005-shopify-orders-daily/`

6. **Confirm and summarize:**

   After the seed is written, output:

   ```
   Pipeline story kicked off.
   Source: <source-system> — REGISTERED
   Seed: features/story-<N>-<pipeline-slug>/seed.md
   Next step: dispatch to an agent via /dispatch, or continue with /phase-7.
   ```

## Notes

- The catalog path uses `sources.yaml` or `data-sources.yaml` as the filename depending
  on the repo convention. Both are checked.
- If the `tech-project-mapping` repo is not checked out at the expected relative path,
  warn the user and offer to proceed without catalog validation.
- This skill does NOT modify the catalog — it only reads it. The developer must add the
  source entry manually before re-running the skill.
- The pipeline-kickoff-prompt.md template is the authoritative structure for pipeline
  Phase 1 seeds. Do not invent a different structure.
