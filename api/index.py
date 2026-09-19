"""Vercel serverless entrypoint.

Vercel's Python runtime imports this file and looks for a module-level ASGI
application named `app`. It must be the application object itself: a coroutine
function named `app` is not detected as ASGI, which is what previously left the
function answering FastAPI's own 404 ({"detail":"Not Found"}) for every path.

Two things have to happen before `app` can be bound:

  1. The repo root must be on sys.path. The pipeline modules (cache, graph,
     agents, metrics, youtube, prompts, schemas, demo_data) live there, and
     inside the function bundle neither the root nor `api/` is a package. The
     insert below runs before the import of api.main, because api.main's own
     sys.path fix cannot help an import that fails while loading it.

  2. `api` must be importable as a package. There is no __init__.py, so
     main.py is loaded by file location rather than by package name.

api/main.py is untouched and is still what uvicorn runs locally.
"""
import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

# Repo root first: the pipeline modules are imported as top-level names.
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Load api/main.py by path, so this works whether or not `api` is a package.
_spec = importlib.util.spec_from_file_location("uptick_api_main", _HERE / "main.py")
_main = importlib.util.module_from_spec(_spec)
sys.modules["uptick_api_main"] = _main
_spec.loader.exec_module(_main)

# The ASGI application Vercel serves. Routes are declared with their /api
# prefix in main.py and Vercel forwards the full request path, so no path
# rewriting is needed or wanted here.
app = _main.app
