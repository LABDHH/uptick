"""Vercel entrypoint.

Vercel's Python runtime looks for a top-level `app` in one of a fixed set of
filenames at the project root (app.py, index.py, main.py, server.py, ...). It
does not look in api/, so this re-exports the real application.

Everything else stays where it is: api/main.py remains the module you run
locally with uvicorn.
"""
from api.main import app  # noqa: F401
