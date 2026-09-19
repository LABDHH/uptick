"""Vercel serverless entrypoint.

Vercel maps files in /api to routes, so this file answers every request under
/api/*. It re-exports the FastAPI app unchanged; api/main.py is still what
uvicorn runs locally.

The app defines its routes as /api/health, /api/search and /api/demo, and
Vercel passes the full original path through, so the prefixes line up without
any rewriting.
"""
from api.main import app  # noqa: F401
