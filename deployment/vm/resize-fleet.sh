#!/usr/bin/env bash
set -euo pipefail
# ============================================================================
# Resize agent VMs (deallocate -> resize -> start, per VM).
#
# Usage:
#   ./resize-fleet.sh                                    # all 6 fleet VMs to default Standard_D2as_v4
#   ./resize-fleet.sh --dry-run                          # show plan, change nothing
#   ./resize-fleet.sh --size Standard_D4as_v4            # different target size
#   ./resize-fleet.sh vm-derrick-agent-dev               # subset by name
#   ./resize-fleet.sh derrick morris                     # short name (auto-mapped to vm-NAME-agent-dev / vm-NAME-dev)
#   ./resize-fleet.sh --yes                              # skip confirmation prompt
#
# Behavior per VM:
#   1. Skip if already at target size
#   2. Deallocate, resize, start
#   3. Poll SSH on port 443 until reachable
#   4. Continue with next VM on failure (do not abort the run)
#
# Disk + static public IP are preserved by deallocate/start. Running sessions
# on the VM are killed when it deallocates — ensure no in-flight work first.
#
# Quota note: Azure tracks vCPU quota per family. B-series and D-series are
# separate quotas. Quota as of 2026-04-26: 10 vCPU Dasv4 in eastus + 10 in
# eastus2. Check before resizing across families:
#   az vm list-usage -l eastus -o table | grep -iE 'BS Family|DASv4'
# ============================================================================

RESOURCE_GROUP="RG-TECH-DEV-AGENTS-DEV"
DEFAULT_SIZE="Standard_D2as_v4"

# Canonical fleet (alphabetical). Update when adding new VMs.
FLEET=(
    vm-dan-agent-dev
    vm-daisy-dev
    vm-derrick-agent-dev
    vm-devon-dev
    vm-morris-agent-dev
    vm-ops-console-dev
)

# ---- Parse args ----
DRY_RUN=false
ASSUME_YES=false
TARGET_SIZE="$DEFAULT_SIZE"
SELECTED=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)  DRY_RUN=true; shift ;;
        --yes|-y)   ASSUME_YES=true; shift ;;
        --size)     TARGET_SIZE="$2"; shift 2 ;;
        --size=*)   TARGET_SIZE="${1#--size=}"; shift ;;
        -h|--help)
            sed -n '4,28p' "$0"
            exit 0
            ;;
        -*)
            echo "ERROR: unknown flag '$1'. See --help." >&2
            exit 2
            ;;
        *)
            SELECTED+=("$1"); shift
            ;;
    esac
done

# ---- Resolve VM names: short -> long ----
resolve_vm_name() {
    local name="$1"
    # Already fully qualified
    [[ "$name" == vm-* ]] && { echo "$name"; return; }
    # Try the two known patterns and pick whichever exists
    for candidate in "vm-${name}-agent-dev" "vm-${name}-dev"; do
        if az vm show -g "$RESOURCE_GROUP" -n "$candidate" --query name -o tsv >/dev/null 2>&1; then
            echo "$candidate"; return
        fi
    done
    echo "ERROR: cannot resolve '$name' to a VM in $RESOURCE_GROUP" >&2
    return 1
}

if [[ ${#SELECTED[@]} -eq 0 ]]; then
    TARGETS=("${FLEET[@]}")
else
    TARGETS=()
    for arg in "${SELECTED[@]}"; do
        TARGETS+=("$(resolve_vm_name "$arg")")
    done
fi

# ---- Show plan ----
echo "=== Fleet resize plan ==="
echo "Resource group: $RESOURCE_GROUP"
echo "Target size:    $TARGET_SIZE"
echo "VMs:"
for vm in "${TARGETS[@]}"; do
    current=$(az vm show -g "$RESOURCE_GROUP" -n "$vm" --query "hardwareProfile.vmSize" -o tsv 2>/dev/null || echo "MISSING")
    if [[ "$current" == "$TARGET_SIZE" ]]; then
        echo "  - $vm: $current  (already at target, will skip)"
    elif [[ "$current" == "MISSING" ]]; then
        echo "  - $vm: NOT FOUND  (will skip)"
    else
        echo "  - $vm: $current  -> $TARGET_SIZE"
    fi
done

if $DRY_RUN; then
    echo
    echo "*** DRY RUN — no changes made ***"
    exit 0
fi

# ---- Confirm ----
if ! $ASSUME_YES; then
    echo
    read -r -p "Proceed? Each VM will deallocate (~30s), resize, then restart. Active SSH sessions will drop. [y/N] " ans
    [[ "$ans" =~ ^[Yy] ]] || { echo "Aborted."; exit 0; }
fi

# ---- Resize each VM ----
SUCCESS=()
SKIPPED=()
FAILED=()

resize_one() {
    local vm="$1"
    local current ip ts
    ts=$(date -u +%H:%M:%SZ)
    echo
    echo "[$ts] === $vm ==="

    current=$(az vm show -g "$RESOURCE_GROUP" -n "$vm" --query "hardwareProfile.vmSize" -o tsv 2>/dev/null || echo "")
    if [[ -z "$current" ]]; then
        echo "  $vm not found — skipping"
        SKIPPED+=("$vm (not found)")
        return
    fi
    if [[ "$current" == "$TARGET_SIZE" ]]; then
        echo "  Already at $TARGET_SIZE — skipping"
        SKIPPED+=("$vm (already $TARGET_SIZE)")
        return
    fi

    ip=$(az vm show -d -g "$RESOURCE_GROUP" -n "$vm" --query publicIps -o tsv 2>/dev/null || echo "")
    echo "  Current: $current   Target: $TARGET_SIZE   IP: ${ip:-unknown}"

    echo "  [1/4] Deallocating..."
    if ! az vm deallocate -g "$RESOURCE_GROUP" -n "$vm" >/dev/null 2>&1; then
        echo "  ERROR: deallocate failed"
        FAILED+=("$vm (deallocate)")
        return
    fi

    echo "  [2/4] Resizing to $TARGET_SIZE..."
    if ! az vm resize -g "$RESOURCE_GROUP" -n "$vm" --size "$TARGET_SIZE" >/dev/null 2>&1; then
        echo "  ERROR: resize failed — attempting to restart at original size"
        az vm start -g "$RESOURCE_GROUP" -n "$vm" >/dev/null 2>&1 || true
        FAILED+=("$vm (resize — original size restored)")
        return
    fi

    echo "  [3/4] Starting..."
    if ! az vm start -g "$RESOURCE_GROUP" -n "$vm" >/dev/null 2>&1; then
        echo "  ERROR: start failed"
        FAILED+=("$vm (start)")
        return
    fi

    if [[ -n "$ip" ]]; then
        echo "  [4/4] Waiting for SSH on $ip:443..."
        local waited=0
        while ! timeout 5 ssh -p 443 -o ConnectTimeout=4 -o StrictHostKeyChecking=no -o BatchMode=yes "azureagent@$ip" "echo OK" >/dev/null 2>&1; do
            sleep 10
            waited=$((waited + 10))
            if [[ $waited -ge 600 ]]; then
                echo "  WARN: SSH not back after 10 min — VM is up at hypervisor level but not responsive yet"
                FAILED+=("$vm (ssh-timeout)")
                return
            fi
        done
        echo "  SSH back after ${waited}s"
    else
        echo "  [4/4] No public IP — skipping SSH probe"
    fi

    SUCCESS+=("$vm")
}

for vm in "${TARGETS[@]}"; do
    resize_one "$vm"
done

# ---- Summary ----
echo
echo "=== Summary ==="
echo "Succeeded (${#SUCCESS[@]}):"; for v in "${SUCCESS[@]}"; do echo "  - $v"; done
echo "Skipped   (${#SKIPPED[@]}):"; for v in "${SKIPPED[@]}"; do echo "  - $v"; done
echo "Failed    (${#FAILED[@]}):"; for v in "${FAILED[@]}"; do echo "  - $v"; done

[[ ${#FAILED[@]} -eq 0 ]]
