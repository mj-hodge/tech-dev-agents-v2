#!/usr/bin/env bash
# graph-token.sh — Device code flow for Microsoft Graph API token.
#
# First run:  Initiates device code login, stores tokens.
# Subsequent: Refreshes the existing refresh token.
#
# Works on Linux and WSL.
set -euo pipefail

TENANT_ID="${GRAPH_TENANT_ID:-1060148b-e4f2-4e64-880e-b8b05958e6fe}"
CLIENT_ID="${GRAPH_APP_ID:-dc0cba0b-f12d-40da-88f0-adcda94075be}"
SCOPE="https://graph.microsoft.com/.default offline_access"
TOKEN_DIR="${HOME}/.agent-ops"
TOKEN_FILE="${TOKEN_DIR}/graph-token.json"

mkdir -p "$TOKEN_DIR"

# --- Helper: save token response ---
save_token() {
    local response="$1"
    local access_token refresh_token expires_in expires_at

    access_token=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
    refresh_token=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('refresh_token',''))")
    expires_in=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('expires_in',3600))")
    expires_at=$(python3 -c "import time; print(int(time.time()) + $expires_in)")

    python3 -c "
import json
store = {
    'access_token': '$access_token',
    'refresh_token': '$refresh_token',
    'expires_at': $expires_at
}
with open('$TOKEN_FILE', 'w') as f:
    json.dump(store, f, indent=2)
"
    echo "Token saved to $TOKEN_FILE (expires in ${expires_in}s)"
}

# --- Refresh existing token ---
try_refresh() {
    if [ ! -f "$TOKEN_FILE" ]; then
        return 1
    fi

    local refresh_token
    refresh_token=$(python3 -c "import json; print(json.load(open('$TOKEN_FILE')).get('refresh_token',''))" 2>/dev/null)
    if [ -z "$refresh_token" ]; then
        return 1
    fi

    echo "Refreshing existing token..."
    local response
    response=$(curl -sf -X POST \
        "https://login.microsoftonline.com/${TENANT_ID}/oauth2/v2.0/token" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "client_id=${CLIENT_ID}&grant_type=refresh_token&refresh_token=${refresh_token}&scope=${SCOPE}" \
        2>/dev/null) || return 1

    if echo "$response" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'access_token' in d" 2>/dev/null; then
        save_token "$response"
        return 0
    fi
    return 1
}

# --- Device code flow ---
device_code_flow() {
    echo "Starting device code flow..."
    echo ""

    local device_response
    device_response=$(curl -sf -X POST \
        "https://login.microsoftonline.com/${TENANT_ID}/oauth2/v2.0/devicecode" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        -d "client_id=${CLIENT_ID}&scope=${SCOPE}")

    local user_code device_code message interval
    user_code=$(echo "$device_response" | python3 -c "import sys,json; print(json.load(sys.stdin)['user_code'])")
    device_code=$(echo "$device_response" | python3 -c "import sys,json; print(json.load(sys.stdin)['device_code'])")
    message=$(echo "$device_response" | python3 -c "import sys,json; print(json.load(sys.stdin)['message'])")
    interval=$(echo "$device_response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('interval',5))")

    echo "$message"
    echo ""
    echo "Code: $user_code"
    echo ""

    # Try to open browser (WSL and Linux)
    if command -v xdg-open &>/dev/null; then
        xdg-open "https://microsoft.com/devicelogin" 2>/dev/null || true
    elif command -v wslview &>/dev/null; then
        wslview "https://microsoft.com/devicelogin" 2>/dev/null || true
    fi

    echo "Waiting for authorization..."
    local max_attempts=60
    local attempt=0

    while [ $attempt -lt $max_attempts ]; do
        sleep "$interval"
        attempt=$((attempt + 1))

        local token_response
        token_response=$(curl -sf -X POST \
            "https://login.microsoftonline.com/${TENANT_ID}/oauth2/v2.0/token" \
            -H "Content-Type: application/x-www-form-urlencoded" \
            -d "client_id=${CLIENT_ID}&grant_type=urn:ietf:params:oauth:grant-type:device_code&device_code=${device_code}" \
            2>/dev/null) || continue

        if echo "$token_response" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'access_token' in d" 2>/dev/null; then
            echo ""
            echo "Authorization successful!"
            save_token "$token_response"
            return 0
        fi

        local error
        error=$(echo "$token_response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('error',''))" 2>/dev/null)
        if [ "$error" = "authorization_pending" ]; then
            printf "."
            continue
        elif [ "$error" = "authorization_declined" ] || [ "$error" = "expired_token" ]; then
            echo ""
            echo "Error: $error"
            return 1
        fi
    done

    echo ""
    echo "Timeout waiting for authorization."
    return 1
}

# --- Main ---
echo "=== Microsoft Graph API Token Manager ==="
echo "Tenant: $TENANT_ID"
echo "App ID: $CLIENT_ID"
echo ""

if try_refresh; then
    echo "Token refresh successful."
else
    echo "No valid refresh token found. Starting device code flow..."
    echo ""
    device_code_flow
fi
