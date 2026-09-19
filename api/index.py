"""Vercel serverless entrypoint.

Vercel routes /api/* here, but does not guarantee which path form the function
receives: it can be the full "/api/health" or a prefix-stripped "/health",
and it can also arrive via root_path. Guessing wrong returns FastAPI's own
404 ({"detail":"Not Found"}), which is exactly what production was doing.

Rather than guess, this wrapper:
  1. reconstructs the path from root_path + path when Vercel splits them,
  2. adds the /api prefix back when it is missing,
  3. exposes a diagnostic at /api/__whoami so the real scope can be inspected
     from the deployed function instead of inferred.

api/main.py is untouched and is still what uvicorn runs locally.
"""
import json

from api.main import app as _app

# The API surface, without the /api prefix.
_KNOWN = ("/health", "/search", "/demo")


def _normalise(scope: dict) -> dict:
    """Return a scope whose path is the /api/... form the routes expect."""
    path = scope.get("path", "") or "/"
    root = scope.get("root_path", "") or ""

    # Vercel sometimes puts the mount point in root_path and the remainder in
    # path; joining them recovers the original request path.
    joined = (root + path) if root and not path.startswith(root) else path

    candidate = joined
    if not candidate.startswith("/api"):
        base = candidate.rstrip("/") or "/"
        if base in _KNOWN:
            candidate = "/api" + base

    if candidate != scope.get("path"):
        scope = dict(scope)
        scope["path"] = candidate
        scope["root_path"] = ""
        if scope.get("raw_path"):
            scope["raw_path"] = candidate.encode()
    return scope


async def app(scope, receive, send):
    if scope["type"] != "http":
        await _app(scope, receive, send)
        return

    # Diagnostic: report exactly what the platform handed us. Answers the
    # "which path form does Vercel actually send" question with evidence.
    if (scope.get("path") or "").rstrip("/").endswith("__whoami"):
        body = json.dumps({
            "path": scope.get("path"),
            "root_path": scope.get("root_path"),
            "raw_path": (scope.get("raw_path") or b"").decode(errors="replace"),
            "method": scope.get("method"),
            "routes": sorted(
                r.path for r in _app.routes if getattr(r, "path", "").startswith("/api")
            ),
        }).encode()
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": body})
        return

    await _app(_normalise(scope), receive, send)
