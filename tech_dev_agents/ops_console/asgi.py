"""ASGI entrypoint — creates the FastAPI app for uvicorn."""
from tech_dev_agents.ops_console.main import create_app

app = create_app()
