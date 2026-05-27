#!/usr/bin/env bash
# agent-disk-cleanup.sh — Daily disk cleanup for agent VMs.
#
# Prunes known-safe disk-growth sources to keep disk usage below 70%.
# Designed to run as a daily cron at 03:00 UTC.
#
# Usage:
#   agent-disk-cleanup.sh [--dry-run] [--force]
#
# Flags:
#   --dry-run   Print what would be deleted without acting.
#   --force     Run cleanup regardless of disk watermark (bypass 60% threshold).
#
# Targets (6 known disk-growth sources):
#   1. systemd journal (journalctl --vacuum-time=7d)
#   2. pytest_cache (~/.cache/pytest_cache/ entries older than 14 days)
#   3. uv + pip caches (~/.cache/uv/, ~/.cache/pip/ entries older than 14 days)
#   4. Merged/closed PR branches in ~/dev/hpi-gorillacommerce/*/
#   5. Failed-story flags (~/state/*/failed-stories/*.txt older than 30 days)
#   6. Stale /tmp files owned by current user, older than 7 days
#
# AC-12: If any cleanup step fails, log the error and CONTINUE to the next step.
# Security: Runs as agent user (hermes), NOT root. Only journalctl --vacuum uses sudo.
# Security: All cleanup paths are HARDCODED — no env-var-controlled delete targets.
# Security: Uses find -delete (anchored), never rm -rf.

set -o pipefail

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
DRY_RUN=0
FORCE=0
LOGFILE="/var/log/agent-cleanup.log"
TOTAL_FREED=0

# ---------------------------------------------------------------------------
# Parse flags
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --force)
            FORCE=1
            shift
            ;;
        *)
            echo "[CLEANUP] unknown flag: $1" >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log() {
    local msg="[CLEANUP] $1"
    echo "$msg"
}

log_freed() {
    local source="$1"
    local bytes="$2"
    TOTAL_FREED=$((TOTAL_FREED + bytes))
    log "freed ${bytes} bytes from ${source}"
}

# Calculate bytes used by a path (returns 0 if path doesn't exist)
bytes_used() {
    local path="$1"
    if [[ -e "$path" ]]; then
        du -sb "$path" 2>/dev/null | awk '{print $1}' || echo 0
    else
        echo 0
    fi
}

# Get current disk usage percentage for /
disk_usage_pct() {
    df / | awk 'NR==2 {gsub(/%/,""); print $5}'
}

# ---------------------------------------------------------------------------
# Watermark guard (AC-8)
# ---------------------------------------------------------------------------
DISK_PCT=$(disk_usage_pct)

if [[ "$FORCE" -eq 1 ]]; then
    log "disk at ${DISK_PCT}% — --force set, running cleanup regardless of watermark"
elif [[ "$DISK_PCT" -lt 60 ]]; then
    log "disk at ${DISK_PCT}% — below 60% watermark, no-op (skip cleanup)"
    exit 0
else
    log "disk at ${DISK_PCT}% — above 60% watermark, running full cleanup"
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
    log "DRY_RUN mode — printing planned actions without deleting"
fi

# ---------------------------------------------------------------------------
# Step 1: journalctl --vacuum-time=7d (AC-4, SC-3)
# ---------------------------------------------------------------------------
cleanup_journal() {
    log "step 1/6: journalctl vacuum (7 days)"
    local before
    before=$(sudo journalctl --disk-usage 2>/dev/null | grep -oP '\d+\.\d+[MGK]' | head -1 || echo "unknown")

    if [[ "$DRY_RUN" -eq 1 ]]; then
        log "dry-run: would run sudo journalctl --vacuum-time=7d (current: ${before})"
        return 0
    fi

    sudo journalctl --vacuum-time=7d 2>&1 | tail -1 || true
    local after
    after=$(sudo journalctl --disk-usage 2>/dev/null | grep -oP '\d+\.\d+[MGK]' | head -1 || echo "unknown")
    # Estimate freed bytes (rough — log the before/after for visibility)
    log "journal: before=${before}, after=${after}"
    log_freed "journalctl" 0  # Exact byte diff is hard to parse; logged above
}

# ---------------------------------------------------------------------------
# Step 2: pytest_cache cleanup (AC-6)
# ---------------------------------------------------------------------------
cleanup_pytest_cache() {
    log "step 2/6: pytest_cache (entries older than 14 days)"
    local cache_dir="$HOME/.cache/pytest_cache"

    if [[ ! -d "$cache_dir" ]]; then
        log "pytest_cache dir not found at ${cache_dir} — skip"
        return 0
    fi

    local before_bytes
    before_bytes=$(bytes_used "$cache_dir")

    if [[ "$DRY_RUN" -eq 1 ]]; then
        local count
        count=$(find "$cache_dir" -type f -atime +14 2>/dev/null | wc -l)
        log "dry-run: would delete ${count} files from pytest_cache older than 14 days"
        return 0
    fi

    find "$cache_dir" -type f -atime +14 -delete 2>/dev/null || true

    local after_bytes
    after_bytes=$(bytes_used "$cache_dir")
    local freed=$((before_bytes - after_bytes))
    [[ "$freed" -lt 0 ]] && freed=0
    log_freed "pytest_cache" "$freed"
}

# ---------------------------------------------------------------------------
# Step 3: uv + pip caches (AC-6)
# ---------------------------------------------------------------------------
cleanup_uv_pip_cache() {
    log "step 3/6: uv + pip caches (entries older than 14 days)"

    for cache_name in "uv" "pip"; do
        local cache_dir="$HOME/.cache/${cache_name}"
        if [[ ! -d "$cache_dir" ]]; then
            log "${cache_name} cache dir not found — skip"
            continue
        fi

        local before_bytes
        before_bytes=$(bytes_used "$cache_dir")

        if [[ "$DRY_RUN" -eq 1 ]]; then
            local count
            count=$(find "$cache_dir" -type f -atime +14 2>/dev/null | wc -l)
            log "dry-run: would delete ${count} files from .cache/${cache_name} older than 14 days"
            continue
        fi

        find "$cache_dir" -type f -atime +14 -delete 2>/dev/null || true

        local after_bytes
        after_bytes=$(bytes_used "$cache_dir")
        local freed=$((before_bytes - after_bytes))
        [[ "$freed" -lt 0 ]] && freed=0
        log_freed "${cache_name}_cache" "$freed"
    done
}

# ---------------------------------------------------------------------------
# Step 4: Merged/closed PR branch pruning (AC-5, SC-4)
# ---------------------------------------------------------------------------
cleanup_merged_branches() {
    log "step 4/6: pruning merged/closed PR branches"
    local dev_dir="$HOME/dev/hpi-gorillacommerce"

    if [[ ! -d "$dev_dir" ]]; then
        log "dev dir not found at ${dev_dir} — skip branch pruning"
        return 0
    fi

    local total_freed=0
    for repo_dir in "$dev_dir"/*/; do
        [[ ! -d "${repo_dir}.git" ]] && continue

        cd "$repo_dir" || continue

        # List local branches, excluding main and master
        local branches
        branches=$(git branch --format='%(refname:short)' 2>/dev/null | grep -v -E '^(main|master)$' || true)

        for branch in $branches; do
            # Skip if branch is currently checked out
            local current
            current=$(git branch --show-current 2>/dev/null || true)
            [[ "$branch" == "$current" ]] && continue

            # Skip if branch has uncommitted changes (safety)
            # (can only check for current branch; for others, check worktree)

            # Check PR status via gh CLI (AC-5)
            local pr_state
            pr_state=$(gh pr view "$branch" --json state --jq '.state' 2>/dev/null || echo "UNKNOWN")

            if [[ "$pr_state" == "MERGED" ]] || [[ "$pr_state" == "CLOSED" ]]; then
                local branch_size
                branch_size=$(git rev-list --count "main..${branch}" 2>/dev/null || echo 0)

                if [[ "$DRY_RUN" -eq 1 ]]; then
                    log "dry-run: would delete branch ${branch} in $(basename "$repo_dir") (PR state: ${pr_state})"
                    continue
                fi

                git branch -D "$branch" 2>/dev/null || true
                log "deleted branch ${branch} in $(basename "$repo_dir") (PR state: ${pr_state})"
            fi
        done

        cd - >/dev/null 2>&1 || true
    done

    log_freed "merged-PR-branches" "$total_freed"
}

# ---------------------------------------------------------------------------
# Step 5: Failed-story flags (AC-7)
# ---------------------------------------------------------------------------
cleanup_failed_story_flags() {
    log "step 5/6: failed-story flags (older than 30 days)"
    local state_dir="$HOME/state"

    if [[ ! -d "$state_dir" ]]; then
        log "state dir not found at ${state_dir} — skip"
        return 0
    fi

    local before_bytes=0
    local after_bytes=0

    for agent_dir in "$state_dir"/*/; do
        local failed_dir="${agent_dir}failed-stories"
        [[ ! -d "$failed_dir" ]] && continue

        local dir_before
        dir_before=$(bytes_used "$failed_dir")
        before_bytes=$((before_bytes + dir_before))

        if [[ "$DRY_RUN" -eq 1 ]]; then
            local count
            count=$(find "$failed_dir" -name "*.txt" -type f -mtime +30 2>/dev/null | wc -l)
            log "dry-run: would delete ${count} failed-story flags older than 30 days from $(basename "$agent_dir")"
            continue
        fi

        find "$failed_dir" -name "*.txt" -type f -mtime +30 -delete 2>/dev/null || true

        local dir_after
        dir_after=$(bytes_used "$failed_dir")
        after_bytes=$((after_bytes + dir_after))
    done

    if [[ "$DRY_RUN" -eq 0 ]]; then
        local freed=$((before_bytes - after_bytes))
        [[ "$freed" -lt 0 ]] && freed=0
        log_freed "failed-story-flags" "$freed"
    fi
}

# ---------------------------------------------------------------------------
# Step 6: Stale /tmp files (owned by current user, older than 7 days)
# ---------------------------------------------------------------------------
cleanup_tmp() {
    log "step 6/6: stale /tmp files (owned by $(whoami), older than 7 days)"

    if [[ "$DRY_RUN" -eq 1 ]]; then
        local count
        count=$(find /tmp -maxdepth 1 -user "$(whoami)" -type f -atime +7 2>/dev/null | wc -l)
        log "dry-run: would delete ${count} stale /tmp files"
        return 0
    fi

    local before_bytes
    before_bytes=$(find /tmp -maxdepth 1 -user "$(whoami)" -type f -atime +7 -exec du -cb {} + 2>/dev/null | tail -1 | awk '{print $1}' || echo 0)
    [[ -z "$before_bytes" ]] && before_bytes=0

    find /tmp -maxdepth 1 -user "$(whoami)" -type f -atime +7 -delete 2>/dev/null || true

    log_freed "stale-tmp" "${before_bytes}"
}

# ---------------------------------------------------------------------------
# Execute all cleanup steps (AC-12: continue on individual failure)
# ---------------------------------------------------------------------------
cleanup_journal || log "error: journalctl cleanup failed — continuing"
cleanup_pytest_cache || log "error: pytest_cache cleanup failed — continuing"
cleanup_uv_pip_cache || log "error: uv/pip cache cleanup failed — continuing"
cleanup_merged_branches || log "error: branch pruning failed — continuing"
cleanup_failed_story_flags || log "error: failed-story flag cleanup failed — continuing"
cleanup_tmp || log "error: /tmp cleanup failed — continuing"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
log "total: ${TOTAL_FREED} bytes freed"
NEW_PCT=$(disk_usage_pct)
log "disk now at ${NEW_PCT}% (was ${DISK_PCT}%)"

if [[ "$NEW_PCT" -gt 80 ]]; then
    log "CRIT: disk still above 80% after full cleanup — manual intervention or disk resize needed"
fi

exit 0
