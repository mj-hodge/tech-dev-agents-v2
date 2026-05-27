#!/usr/bin/env bash
# cleanup-fake-story-ids.sh
#
# After Daisy finishes STORY-491 (which is really STORY-480), rename the
# artifacts to the correct story ID. Run this AFTER the PR is merged.
#
# Background: the dispatch queue's duplicate check forced creating new story
# IDs for every re-enqueue attempt. STORY-480 (Dashboard Overhaul) was
# re-enqueued as 481, 486, 487, 488, 489, 490, 491, 492. Only 491 has
# actual deliverables (Daisy completed phases 1-8 under that ID).
#
# The fix (dispatch_db_service.py) now allows re-enqueue of failed stories,
# so this won't happen again.

set -euo pipefail

REPO_DIR="${1:?Usage: $0 <repo-dir>}"
cd "$REPO_DIR"

echo "=== Cleaning up fake story IDs ==="

# STORY-491 → STORY-480 (dashboard overhaul)
if [ -d "features/story-491" ]; then
    echo "Moving features/story-491/ → features/story-480-dashboard-overhaul/"
    mkdir -p features/story-480-dashboard-overhaul
    cp -n features/story-491/*.md features/story-480-dashboard-overhaul/ 2>/dev/null || true
    echo "  Copied $(ls features/story-491/*.md 2>/dev/null | wc -l) deliverables"
fi

# Remove empty orphan folders from other fake IDs
for num in 481 486 487 488 489 490 492 493 466 468 470 472 482 484 469 471 476 474 483 485; do
    DIR="features/story-${num}"
    if [ -d "$DIR" ]; then
        FILES=$(ls "$DIR"/*.md 2>/dev/null | wc -l)
        if [ "$FILES" -eq 0 ]; then
            echo "Removing empty orphan: $DIR"
            rm -rf "$DIR"
        else
            echo "WARNING: $DIR has $FILES deliverables — needs manual review"
        fi
    fi
done

echo "=== Done ==="
echo "Next steps:"
echo "  1. git add features/ && git commit -m 'cleanup: rename story-491 → story-480 (fake ID from re-enqueue bug)'"
echo "  2. Update Monday.com task for STORY-480 with completion status"
echo "  3. Close any Monday.com tasks created under fake IDs (481-492)"
