"""Vercel serverless entrypoint.

Vercel routes /api/* to this file, but it does not deliver the path
consistently: depending on how the rewrite resolves, the function can receive
either the full "/api/health" or just "/health" with the prefix stripped.

The FastAPI routes are declared as /api/..., so a stripped path produced
FastAPI's own 404 ({"detail":"Not Found"}) and, for POST /api/search, a 405
when the path happened to match a GET-only route.

Rather than guess which form Vercel will send, this wrapper accepts both: if a
request arrives without the /api prefix, it is added back before the app sees
it. api/main.py is untouched and is still what uvicorn runs locally.
"""
from api.main import app as _app

# Paths the API actually serves, without the /api prefix.
_KNOWN = ("/health", "/search", "/demo")


async def app(scope, receive, send):
    """ASGI wrapper that normalises the path to the /api/... form."""
    if scope["type"] == "http":
        path = scope.get("path", "")
        if not path.startswith("/api"):
            base = path.rstrip("/") or "/"
            if base in _KNOWN:
                scope = dict(scope)
                scope["path"] = "/api" + base
                if scope.get("raw_path"):
                    scope["raw_path"] = scope["path"].encode()
    await _app(scope, receive, send)
