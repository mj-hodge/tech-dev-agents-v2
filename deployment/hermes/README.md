# Hermes Agent Container Runtime

This folder defines a container image that installs and runs [Nous Hermes Agent](https://hermes-agent.nousresearch.com/).

## What Is Installed In The Image

The Docker image installs Hermes into these locations:

- Hermes source repo: `/opt/hermes-agent`
- Python virtual environment: `/opt/hermes-agent/venv`
- Hermes CLI binary: `/usr/local/bin/hermes`
- Runtime user: `hermes`
- Agent data home: `/home/hermes/.hermes`

Image contents include:

- Python 3.11 runtime
- Node.js 22 + npm (required by Hermes integrations)
- Hermes dependencies from `.[all]`
- Runtime tools used by Hermes (`git`, `ripgrep`, `ffmpeg`, `jq`, `curl`)

## Build Locally

From repo root:

```bash
docker build -f deployment/hermes/Dockerfile -t hermes-agent:local .
```

## Run Locally

Gateway mode (default):

```bash
docker run --rm -it \
  -e ANTHROPIC_API_KEY="<your-key>" \
  -v "$PWD/.local/hermes-home:/home/hermes/.hermes" \
  hermes-agent:local
```

CLI mode:

```bash
docker run --rm -it \
  -e ANTHROPIC_API_KEY="<your-key>" \
  -v "$PWD/.local/hermes-home:/home/hermes/.hermes" \
  hermes-agent:local cli
```

## Container Entrypoint Behavior

The container entrypoint:

- creates required Hermes state directories under `$HERMES_HOME`
- optionally runs diagnostics if `HERMES_RUN_DOCTOR=1`
- starts `hermes gateway` by default
- supports explicit CLI mode with `cli`

## Azure Container Apps Deployment (High-Level)

Use `docs/azure-predeploy-setup.md` for the complete predeploy and deployment sequence.
