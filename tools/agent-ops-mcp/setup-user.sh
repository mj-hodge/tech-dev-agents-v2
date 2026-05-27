#!/usr/bin/env bash
# setup-user.sh — Set up agent-ops MCP tools for a new team member
# Run from anywhere. Requires: Node.js 18+, Claude Code CLI, git access to this repo.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

echo "=== Agent Ops MCP — New User Setup ==="
echo ""

# --- Step 1: Check prerequisites ---
echo "[1/6] Checking prerequisites..."

if ! command -v node &>/dev/null; then
  echo "  ❌ Node.js not found. Install Node.js 18+ first."
  exit 1
fi
NODE_VER=$(node -v | sed 's/v//' | cut -d. -f1)
if [ "$NODE_VER" -lt 18 ]; then
  echo "  ❌ Node.js $NODE_VER found, need 18+."
  exit 1
fi
echo "  ✅ Node.js $(node -v)"

if ! command -v claude &>/dev/null; then
  echo "  ❌ Claude Code CLI not found. Install from https://claude.ai/code"
  exit 1
fi
echo "  ✅ Claude Code $(claude --version 2>&1 | head -1)"

if [ ! -f "${REPO_DIR}/.env" ]; then
  echo "  ⚠️  No .env file found at ${REPO_DIR}/.env"
  echo "     Ask Mark for the Loki API key and ops console API key."
  echo "     Create ${REPO_DIR}/.env with:"
  echo "       OPS_LOKI_API_KEY=<loki-key>"
  echo "       OPS_API_KEY=<ops-console-key>"
  echo ""
  read -p "  Press Enter after creating .env, or Ctrl+C to abort..."
fi
echo "  ✅ .env found"

# --- Step 2: Install MCP server dependencies ---
echo ""
echo "[2/6] Installing MCP server..."
cd "${SCRIPT_DIR}"
npm install --silent 2>/dev/null || npm install
if [ -f tsconfig.json ]; then
  npm run build 2>/dev/null && echo "  ✅ TypeScript built" || echo "  ⚠️  Build failed (may need tsc)"
fi

# --- Step 3: Graph API authentication ---
echo ""
echo "[3/6] Authenticating with Microsoft Graph API..."
echo "  This lets you send Teams messages to agents as yourself."
echo "  A browser window will open — sign in with your Gorilla Commerce account."
echo ""

mkdir -p ~/.agent-ops

if [ -f ~/.agent-ops/graph-token.json ]; then
  EXPIRED=$(python3 -c "
import json, time
d = json.load(open('$HOME/.agent-ops/graph-token.json'))
print('yes' if d.get('expires_at',0) < time.time() else 'no')
" 2>/dev/null || echo "yes")
  if [ "$EXPIRED" = "no" ]; then
    echo "  ✅ Graph token already valid"
  else
    echo "  Token expired, refreshing..."
    bash "${SCRIPT_DIR}/auth/graph-token.sh"
  fi
else
  bash "${SCRIPT_DIR}/auth/graph-token.sh"
fi

# --- Step 4: Verify Teams chats exist ---
echo ""
echo "[4/6] Checking Teams chats with agents..."

TOKEN=$(python3 -c "import json; print(json.load(open('$HOME/.agent-ops/graph-token.json'))['access_token'])" 2>/dev/null || echo "")

if [ -n "$TOKEN" ]; then
  AGENTS_WITH_CHATS=$(curl -s -H "Authorization: Bearer $TOKEN" \
    'https://graph.microsoft.com/v1.0/me/chats?$top=50&$expand=members' 2>/dev/null | \
    python3 -c "
import sys,json
d=json.load(sys.stdin)
found=[]
for c in d.get('value',[]):
    for m in c.get('members',[]):
        e=m.get('email','').lower()
        if 'tech-agent-dan' in e: found.append('dan')
        if 'tech-agent-derrick' in e: found.append('derrick')
print(' '.join(set(found)))
" 2>/dev/null || echo "")

  if echo "$AGENTS_WITH_CHATS" | grep -q "dan"; then
    echo "  ✅ 1:1 chat with Dan exists"
  else
    echo "  ⚠️  No 1:1 chat with Dan. Open Teams and message tech-agent-dan@gorillacommerce.co"
  fi

  if echo "$AGENTS_WITH_CHATS" | grep -q "derrick"; then
    echo "  ✅ 1:1 chat with Derrick exists"
  else
    echo "  ⚠️  No 1:1 chat with Derrick. Open Teams and message tech-agent-derrick@gorillacommerce.co"
  fi
else
  echo "  ⚠️  Could not verify chats (no token). Message both agents in Teams to create 1:1 chats:"
  echo "     - tech-agent-dan@gorillacommerce.co"
  echo "     - tech-agent-derrick@gorillacommerce.co"
fi

# --- Step 5: Configure Claude Code MCP ---
echo ""
echo "[5/6] Configuring Claude Code MCP server..."

NODE_PATH=$(which node)
DIST_PATH="${SCRIPT_DIR}/dist/index.js"
OPS_URL="https://tech-dev-agents.gorillacommerce.ai"
OPS_KEY=$(grep OPS_API_KEY "${REPO_DIR}/.env" 2>/dev/null | head -1 | cut -d= -f2 || echo "")
REGISTRY_PATH="${REPO_DIR}/deployment/vm/agent-registry.json"

SETTINGS_FILE="$HOME/.claude/settings.json"

if [ -f "$SETTINGS_FILE" ]; then
  # Check if agent-ops already configured
  if grep -q "agent-ops" "$SETTINGS_FILE" 2>/dev/null; then
    echo "  ✅ agent-ops already in ${SETTINGS_FILE}"
  else
    echo ""
    echo "  Add this to the mcpServers section of ${SETTINGS_FILE}:"
    echo ""
    cat <<MCPEOF
    "agent-ops": {
      "command": "${NODE_PATH}",
      "args": ["${DIST_PATH}"],
      "env": {
        "OPS_CONSOLE_URL": "${OPS_URL}",
        "OPS_API_KEY": "${OPS_KEY}",
        "AGENT_REGISTRY_PATH": "${REGISTRY_PATH}"
      }
    }
MCPEOF
    echo ""
    echo "  Or copy .mcp.json to the project root (already done if you cloned the repo)."
  fi
else
  echo "  ⚠️  No ${SETTINGS_FILE} found. Create it with the MCP config above."
fi

# --- Step 6: Verify ---
echo ""
echo "[6/6] Verification..."
echo ""

if [ -f "${DIST_PATH}" ]; then
  echo "  ✅ MCP server built at ${DIST_PATH}"
else
  echo "  ❌ MCP server not built. Run: cd ${SCRIPT_DIR} && npm run build"
fi

if [ -f ~/.agent-ops/graph-token.json ]; then
  echo "  ✅ Graph token at ~/.agent-ops/graph-token.json"
else
  echo "  ❌ No Graph token. Run: bash ${SCRIPT_DIR}/auth/graph-token.sh"
fi

if [ -f "${REPO_DIR}/.env" ]; then
  echo "  ✅ .env with API keys"
else
  echo "  ❌ No .env — ask Mark for keys"
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Restart Claude Code to load the MCP server"
echo "  2. Try: /fleet (check agent status)"
echo "  3. Try: /dispatch dan STORY-XXX (send work to an agent)"
echo ""
echo "Available skills:"
echo "  /fleet        — agent status, costs, availability"
echo "  /dispatch     — send work to agents with review"
echo "  /whats-next   — see what needs your attention"
echo ""
