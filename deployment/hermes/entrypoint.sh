#!/usr/bin/env bash
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-/home/hermes/.hermes}"

# Ensure Claude Code uses the direct Anthropic API (not Bedrock)
export CLAUDE_CODE_USE_BEDROCK=0

# ---------------------------------------------------------------------------
# Derive AZURE_OPENAI_ENDPOINT from OPENAI_BASE_URL when not explicitly set.
# Azure AI Foundry sets OPENAI_BASE_URL to the root host (e.g.
# https://eastus.api.cognitive.microsoft.com/).  Hermes custom provider needs
# the full deployment path WITHOUT /v1 — it appends /chat/completions itself.
# ---------------------------------------------------------------------------
# Azure native endpoint: {host}/openai/deployments/{model}
# The OpenAI SDK appends /chat/completions; our Azure shim adds ?api-version= and api-key header.
_model_name="${AZURE_OPENAI_MODEL:-gpt5chat}"
_base="${OPENAI_BASE_URL:-}"
if [[ -n "$_base" && ( -z "${AZURE_OPENAI_ENDPOINT:-}" || "${AZURE_OPENAI_ENDPOINT}" == */v1 ) ]]; then
  _base="${_base%/}"  # strip trailing slash
  export AZURE_OPENAI_ENDPOINT="${_base}/openai/deployments/${_model_name}"
  echo "[entrypoint] Derived AZURE_OPENAI_ENDPOINT=${AZURE_OPENAI_ENDPOINT}"
fi

# ---------------------------------------------------------------------------
# CLI verification checks
# ---------------------------------------------------------------------------
echo "[entrypoint] Checking CLI tools..."
claude --version || echo "WARNING: Claude Code CLI not found"
gh --version    || echo "WARNING: gh CLI not found"

# ---------------------------------------------------------------------------
# Copy Hermes config files to ~/.hermes/ if they don't already exist
# (Allows runtime secrets/overrides to take precedence over baked-in files.)
# ---------------------------------------------------------------------------
mkdir -p "${HERMES_HOME}"

for src_file in /usr/local/share/hermes-defaults/config.yaml \
                /usr/local/share/hermes-defaults/SOUL.md; do
  dest_file="${HERMES_HOME}/$(basename "${src_file}")"
  if [[ ! -f "${dest_file}" && -f "${src_file}" ]]; then
    cp "${src_file}" "${dest_file}"
    echo "[entrypoint] Copied $(basename "${src_file}") to ${HERMES_HOME}/"
  fi
done

# .env needs variable substitution (Container App env/secrets → Hermes .env)
# Always re-render on boot so persisted volumes don't retain stale values.
if [[ -f /usr/local/share/hermes-defaults/.env ]]; then
  envsubst < /usr/local/share/hermes-defaults/.env > "${HERMES_HOME}/.env"
  echo "[entrypoint] Rendered .env to ${HERMES_HOME}/ (with env var substitution)"
fi

# Also ensure the full directory tree is in place
mkdir -p \
  "${HERMES_HOME}/cron" \
  "${HERMES_HOME}/sessions" \
  "${HERMES_HOME}/logs" \
  "${HERMES_HOME}/memories" \
  "${HERMES_HOME}/skills" \
  "${HERMES_HOME}/hooks" \
  "${HERMES_HOME}/image_cache" \
  "${HERMES_HOME}/audio_cache" \
  "${HERMES_HOME}/whatsapp/session"

# ---------------------------------------------------------------------------
# Persist pairing data across container restarts.
# /mnt/persist is an Azure Files share; SQLite can't run on it (no locking),
# so we only symlink directories that store plain JSON/text files.
# ---------------------------------------------------------------------------
PERSIST_ROOT="/mnt/persist"
if [[ -d "${PERSIST_ROOT}" ]]; then
  for subdir in pairing memories; do
    persist_dir="${PERSIST_ROOT}/${subdir}"
    local_dir="${HERMES_HOME}/${subdir}"
    mkdir -p "${persist_dir}"
    if [[ -d "${local_dir}" && ! -L "${local_dir}" ]]; then
      # Move any existing files into persistent storage, then replace with symlink
      cp -a "${local_dir}/." "${persist_dir}/" 2>/dev/null || true
      rm -rf "${local_dir}"
    fi
    if [[ ! -e "${local_dir}" ]]; then
      ln -s "${persist_dir}" "${local_dir}"
      echo "[entrypoint] Symlinked ${local_dir} → ${persist_dir}"
    fi
  done
else
  echo "[entrypoint] No persistent storage at ${PERSIST_ROOT} — pairing data will not survive restarts"
  mkdir -p "${HERMES_HOME}/pairing" "${HERMES_HOME}/memories"
fi

if [[ "${HERMES_RUN_DOCTOR:-0}" == "1" ]]; then
  hermes doctor || true
fi

# ---------------------------------------------------------------------------
# Env-var audit — log which critical vars are set (values redacted)
# ---------------------------------------------------------------------------
echo "[entrypoint] === Environment variable audit ==="
for var in ANTHROPIC_API_KEY OPENAI_API_KEY GITHUB_TOKEN GH_TOKEN \
           TEAMS_ENABLED TEAMS_CLIENT_ID TEAMS_CLIENT_SECRET \
           TEAMS_TENANT_ID TEAMS_BOT_USER_ID TEAMS_NOTIFICATION_HOST \
           TEAMS_WEBHOOK_PORT MicrosoftAppPassword \
           LOKI_URL LOKI_PROJECT LOKI_ENV LOKI_SOURCE_SYSTEM \
           AZURE_OPENAI_ENDPOINT API_SERVER_ENABLED API_SERVER_PORT; do
  val="$(printenv "$var" 2>/dev/null || true)"
  if [[ -z "$val" ]]; then
    echo "[entrypoint]   $var = (NOT SET)"
  else
    echo "[entrypoint]   $var = ${val:0:4}****  (set, ${#val} chars)"
  fi
done
echo "[entrypoint] === End audit ==="

# ---------------------------------------------------------------------------
# Source the rendered .env into the process environment.
# The .env contains TEAMS_* vars derived from container secrets via envsubst.
# Without this, the gateway process won't see them.
# ---------------------------------------------------------------------------
if [[ -f "${HERMES_HOME}/.env" ]]; then
  echo "[entrypoint] Sourcing ${HERMES_HOME}/.env into process environment..."
  set -a  # auto-export all variables
  source "${HERMES_HOME}/.env"
  set +a
  echo "[entrypoint] .env sourced — dumping rendered values (secrets redacted):"
  while IFS= read -r line; do
    # skip comments and blanks
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ -z "$line" ]] && continue
    key="${line%%=*}"
    val="${line#*=}"
    if [[ ${#val} -gt 4 ]]; then
      echo "[entrypoint]   $key = ${val:0:4}****  (${#val} chars)"
    else
      echo "[entrypoint]   $key = $val"
    fi
  done < "${HERMES_HOME}/.env"
else
  echo "[entrypoint] WARNING: ${HERMES_HOME}/.env not found — TEAMS_* vars may be missing"
fi

# ---------------------------------------------------------------------------
# Write key env vars to ~/.bashrc so Hermes terminal subshells inherit them.
# The gateway process has them (via set -a/source above), but Hermes's terminal
# tool may spawn shells that don't inherit the full parent environment.
# ---------------------------------------------------------------------------
{
  echo "# Auto-injected by entrypoint — env vars for Hermes terminal tool"
  echo "export GITHUB_TOKEN=\"${GITHUB_TOKEN:-}\""
  echo "export GH_TOKEN=\"${GH_TOKEN:-}\""
} >> /home/hermes/.bashrc
echo "[entrypoint] Wrote env exports to ~/.bashrc for terminal subshells"

# ---------------------------------------------------------------------------
# SDLC framework — copy skills and link into ~/.claude and ~/.sdlc
# Azure Files (SMB) doesn't support symlinks, so we copy instead.
# ---------------------------------------------------------------------------
SDLC_SRC="/opt/sdlc-framework"
if [[ -d "${SDLC_SRC}" ]]; then
  # Make ~/.sdlc available (local filesystem copy for symlink-friendly access)
  rm -rf /home/hermes/.sdlc
  cp -r "${SDLC_SRC}" /home/hermes/.sdlc

  # Copy skills into ~/.claude/skills (persistent storage, no symlinks)
  rm -rf /home/hermes/.claude/skills
  cp -r "${SDLC_SRC}/skills" /home/hermes/.claude/skills
  echo "[entrypoint] SDLC skills copied to ~/.claude/skills and ~/.sdlc"
fi

# ---------------------------------------------------------------------------
# Default / bot / gateway mode: exec hermes gateway in the foreground.
# The native Teams adapter (gateway/platforms/teams.py) is loaded by the
# Hermes gateway directly — no separate bot_server.py process is needed.
# ---------------------------------------------------------------------------
if [[ $# -eq 0 || "$1" == "bot" || "$1" == "gateway" ]]; then
  # Force debug logging and unbuffered output for gateway startup diagnostics
  export LOG_LEVEL="${LOG_LEVEL:-DEBUG}"
  export HERMES_LOG_LEVEL="${HERMES_LOG_LEVEL:-DEBUG}"
  export PYTHONUNBUFFERED=1

  # Health server on port 8080 handles probes + proxies /api/notifications to Teams adapter
  echo "[entrypoint] Starting health/proxy server on port 8080..."
  python3 /usr/local/bin/hermes-health-server.py &

  # Pre-flight: verify TeamsAdapter is importable
  echo "[entrypoint] Running pre-flight check..."
  HERMES_REPO="${HERMES_REPO:-/opt/hermes-agent}"
  python3 -u - "$HERMES_REPO" <<'PYCHECK'
import sys, os
repo = sys.argv[1]
sys.path.insert(0, repo)
venv_lib = os.path.join(repo, "venv/lib")
for d in (os.listdir(venv_lib) if os.path.isdir(venv_lib) else []):
    sp = os.path.join(venv_lib, d, "site-packages")
    if os.path.isdir(sp):
        sys.path.insert(0, sp)
try:
    from gateway.platforms.teams import TeamsAdapter, check_teams_requirements
    missing = check_teams_requirements()
    print(f"[preflight] TeamsAdapter OK — unmet: {missing or 'none'}")
except Exception as e:
    print(f"[preflight] ERROR: {e}")
PYCHECK

  echo "[entrypoint] Starting hermes gateway (LOG_LEVEL=${LOG_LEVEL})..."
  exec hermes gateway 2>&1
fi

# ---------------------------------------------------------------------------
# CLI passthrough mode
# ---------------------------------------------------------------------------
if [[ "$1" == "cli" ]]; then
  shift
  exec hermes "$@"
fi

exec "$@"
