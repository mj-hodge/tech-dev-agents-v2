## Answer: Option A — Allowlist line 2944 with documented reason

**Directive:** Choose Option A. Add the allowlist entry for line 2944.

**Rationale:** The git fetch origin main at line 2944 in sdlc_phase_runner.py is a known contract violation, but fixing it requires changes to the SDLC runner itself which is out of scope for this story (seed says do not modify sdlc_phase_runner.py). STORY-761 is already filed to address SDLC runner hardcoded branch references. Allowlisting with a clear reason maintains contract enforcement while acknowledging the known exception.

**What to do next:**
1. Add line 2944 of sdlc_phase_runner.py to the allowlist with reason: "Known violation — STORY-761 tracks the fix in sdlc_phase_runner.py. Allowlisted to avoid blocking unrelated work."
2. Ensure the allowlist format matches other entries in the codebase
3. Add a code comment near the allowlist entry referencing STORY-761
4. Run the contract checker to verify it passes with the allowlist in place

— Morris
