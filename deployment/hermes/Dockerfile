# ==============================================================================
# Standard tools for all dev agent images:
#   - Claude Code CLI (@anthropic-ai/claude-code) for autonomous SDLC execution
#   - GitHub CLI (gh) for PR/issue management from within the agent
#   - tech_dev_agents Python package + personas for agent persona loading
# Keep all existing Hermes tooling — it remains available for message routing
# and WhatsApp/Teams bridge functionality.
# ==============================================================================

FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV HERMES_HOME=/home/hermes/.hermes
ENV HERMES_REPO=/opt/hermes-agent
ENV PATH="/root/.local/bin:/home/hermes/.local/bin:/opt/hermes-agent/venv/bin:${PATH}"
ENV BOT_NAME=dan
ENV BOT_EMAIL=tech-agent-dan@gorillacommerce.co
ENV AZURE_OPENAI_ENDPOINT=https://eastus.api.cognitive.microsoft.com/openai/deployments/gpt5chat

# System dependencies aligned with Hermes docs:
# - git prerequisite
# - nodejs/npm for browser automation and WhatsApp bridge
# - ripgrep and ffmpeg used by Hermes tooling
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      bash \
      ca-certificates \
      curl \
      ffmpeg \
      gettext-base \
      git \
      gnupg \
      jq \
      ripgrep \
      sudo && \
    rm -rf /var/lib/apt/lists/*

# Install Node.js 22.x
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get update && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install Claude Code CLI + Codex CLI (standard dev agent tools)
RUN npm install -g @anthropic-ai/claude-code@latest @openai/codex@latest

# Install GitHub CLI (standard dev agent tool)
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends gh && \
    rm -rf /var/lib/apt/lists/*

# Install uv (recommended by Hermes install docs)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone Hermes Agent source with submodules
RUN git clone --recurse-submodules https://github.com/NousResearch/hermes-agent.git "${HERMES_REPO}"

WORKDIR ${HERMES_REPO}

# Create venv and install Hermes with all extras
RUN uv venv venv --python 3.11 && \
    VIRTUAL_ENV="${HERMES_REPO}/venv" uv pip install -e ".[all]" && \
    npm install && \
    ln -sf "${HERMES_REPO}/venv/bin/hermes" /usr/local/bin/hermes

# Create runtime user and home (with passwordless sudo)
RUN useradd -m -s /bin/bash hermes && \
    echo "hermes ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/hermes && \
    mkdir -p "${HERMES_HOME}" "${HERMES_HOME}/"{cron,sessions,logs,memories,skills,pairing,hooks,image_cache,audio_cache,whatsapp/session} && \
    chown -R hermes:hermes /home/hermes "${HERMES_REPO}"

# Copy Hermes config files into a staging directory; entrypoint copies them to
# ~/.hermes/ at runtime so that volume mounts / runtime secrets take precedence.
RUN mkdir -p /usr/local/share/hermes-defaults
COPY deployment/hermes/hermes-config.yaml /usr/local/share/hermes-defaults/config.yaml
COPY deployment/hermes/hermes-env         /usr/local/share/hermes-defaults/.env
COPY deployment/hermes/SOUL.md            /usr/local/share/hermes-defaults/SOUL.md
RUN chown -R hermes:hermes /usr/local/share/hermes-defaults

COPY tech_dev_agents/ /opt/tech-dev-agents/tech_dev_agents/
COPY personas/ /opt/tech-dev-agents/personas/

# Clone SDLC framework (skills, templates, agent guidance)
RUN git clone https://github.com/hpi-gorillacommerce/sdlc-framework.git /opt/sdlc-framework

# Install tech_dev_agents package dependencies into the Hermes venv (using uv, not pip)
RUN VIRTUAL_ENV="${HERMES_REPO}/venv" uv pip install --no-cache \
      requests \
      pyyaml \
      aiohttp \
      msal

COPY deployment/hermes/entrypoint.sh /usr/local/bin/hermes-entrypoint.sh
COPY deployment/hermes/health_server.py /usr/local/bin/hermes-health-server.py
COPY deployment/hermes/bot_server.py /usr/local/bin/bot_server.py
COPY deployment/hermes/teams.py /opt/hermes-agent/gateway/platforms/teams.py
COPY deployment/hermes/patch_gateway_config.py /tmp/patch_gateway_config.py
COPY deployment/hermes/patch_tools_config.py /tmp/patch_tools_config.py
COPY deployment/hermes/patch_azure_openai.py /tmp/patch_azure_openai.py
RUN python3 /tmp/patch_gateway_config.py && rm /tmp/patch_gateway_config.py
RUN python3 /tmp/patch_tools_config.py && rm /tmp/patch_tools_config.py
RUN python3 /tmp/patch_azure_openai.py && rm /tmp/patch_azure_openai.py
RUN chmod +x /usr/local/bin/hermes-entrypoint.sh

USER hermes
WORKDIR /home/hermes

# Default to Hermes gateway (Teams adapter runs natively)
ENTRYPOINT ["/usr/local/bin/hermes-entrypoint.sh"]
CMD ["gateway"]
