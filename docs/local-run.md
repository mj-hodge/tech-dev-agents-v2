# Local Run and Test Guide

This project now includes a Hermes Agent container runtime at `deployment/hermes/`.

## 1. Prerequisites

- Docker
- Python 3.12+ (for repository tests)
- Git

Optional (for full predeploy checks):

- `trivy` or `grype`
- `gitleaks` or `trufflehog`
- `pip-audit`
- `alembic`
- Azure CLI (`az`)

## 2. Build Hermes Runtime Image

From repo root:

```bash
docker build -f deployment/hermes/Dockerfile -t hermes-agent:local .
```

## 3. Run Hermes Locally

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

Inside the container, Hermes is installed at:

- `/opt/hermes-agent` (source)
- `/opt/hermes-agent/venv` (python env)
- `/usr/local/bin/hermes` (CLI)
- `/home/hermes/.hermes` (runtime state)

## 4. Run Repository Tests

```bash
PYTHONDONTWRITEBYTECODE=1 pytest -q
```

## 5. Run Predeploy Checks

```bash
bash tests/predeploy/run_all.sh
```

Required env vars for predeploy checks:

```bash
export IMAGE_TAG="<registry>/<image>:<tag>"
export BASE_URL="https://<service-host>"
export DATABASE_URL="postgresql+psycopg://<user>:<pass>@<host>:5432/<db>"
export REDIS_URL="redis://:<password>@<host>:6379/0"
```

## 6. Result Policy

- `PASS`: gate passed
- `FAIL`: actionable issue found
- `BLOCKED`: missing env/tooling/infrastructure; treat as not deployable
