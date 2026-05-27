# Ops Console — containerized FastAPI application
# STORY-032: Containerize ops console for reliable deployment
#
# Build:  docker build -t ops-console .
# Run:    docker run -p 8005:8005 --env-file .env ops-console

FROM python:3.12-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies for asyncpg (libpq) and cryptography
RUN apt-get update && \
    apt-get install -y --no-install-recommends libpq5 && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY tech_dev_agents/ tech_dev_agents/
COPY deployment/vm/agent-registry.json deployment/vm/agent-registry.json

# Create directory for JSON dispatch queue fallback
RUN mkdir -p /var/lib/ops-console && chown 1000:1000 /var/lib/ops-console

# Non-root user
RUN useradd --uid 1000 --create-home appuser
USER appuser

EXPOSE 8005

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8005/api/health')" || exit 1

# Run with uvicorn (--factory flag for create_app pattern)
# Set Docker-appropriate fallback queue path
ENV OPS_DISPATCH_QUEUE_PATH=/var/lib/ops-console/dispatch-queue.json

CMD ["uvicorn", "tech_dev_agents.ops_console.main:create_app", \
     "--factory", "--host", "0.0.0.0", "--port", "8005"]
