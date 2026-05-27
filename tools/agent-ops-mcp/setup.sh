#!/usr/bin/env bash
# setup.sh — Idempotent setup for agent-ops-mcp
# Installs dependencies, validates registry, checks API health, prints Claude Code config.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY="${AGENT_REGISTRY_PATH:-deployment/vm/agent-registry.json}"
OPS_URL="${OPS_CONSOLE_URL:-http://localhost:8002}"

echo "=== agent-ops-mcp setup ==="

# 1. Install Node.js dependencies
echo "[1/4] Installing npm dependencies..."
cd "$SCRIPT_DIR"
npm install --silent 2>/dev/null || npm install

# 2. Build TypeScript
echo "[2/4] Building TypeScript..."
npm run build 2>/dev/null && echo "  Build OK" || echo "  Build failed (non-fatal during dev)"

# 3. Validate agent registry
echo "[3/4] Validating agent registry..."
if [ -f "$REGISTRY" ]; then
    AGENT_COUNT=$(python3 -c "import json; print(len(json.load(open('$REGISTRY'))))" 2>/dev/null || echo "0")
    echo "  Registry: $REGISTRY ($AGENT_COUNT agents)"
else
    echo "  WARNING: Registry not found at $REGISTRY"
    echo "  Set AGENT_REGISTRY_PATH to the correct path"
fi

# 4. Check API health
echo "[4/4] Checking ops console API health..."
if curl -sf "${OPS_URL}/api/health" >/dev/null 2>&1; then
    echo "  API health: OK ($OPS_URL)"
else
    echo "  WARNING: API not reachable at $OPS_URL"
    echo "  Start the ops console or set OPS_CONSOLE_URL"
fi

# Print Claude Code MCP config
echo ""
echo "=== Claude Code config (add to .claude/settings.json) ==="
cat <<EOF
{
  "mcpServers": {
    "agent-ops": {
      "command": "node",
      "args": ["${SCRIPT_DIR}/dist/index.js"],
      "env": {
        "OPS_CONSOLE_URL": "${OPS_URL}",
        "OPS_API_KEY": "\${OPS_API_KEY}",
        "AGENT_REGISTRY_PATH": "${REGISTRY}"
      }
    }
  }
}
EOF

echo ""
echo "Setup complete."
