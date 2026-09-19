"""Uptick HTTP API.

Wraps the existing LangGraph pipeline. Everything in graph.py, agents.py,
metrics.py, youtube.py and cache.py is reused untouched: this module only
handles transport.

Progress streams over Server-Sent Events because a cold query takes roughly
50 to 60 seconds, and a blank spinner for a minute is not acceptable. The
graph already emits per-node updates via astream, so SSE is a thin adapter
over something that already works.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cache          # noqa: E402
import demo_data      # noqa: E402
import graph as graph_mod  # noqa: E402

CHECKPOINT_DB = os.environ.get("UPTICK_CHECKPOINTS", "checkpoints.db")


def _load_keys(root: Path | None = None) -> None:
    """Populate the API keys from .env, then from .streamlit/secrets.toml.

    The secrets.toml fallback exists because that is where the keys lived
    while this was a Streamlit app, and silently ignoring a file the user has
    already filled in is a worse failure than reading it. Environment
    variables always win, so a real deployment is unaffected.
    """
    root = root or Path(__file__).resolve().parent.parent

    env_file = root / ".env"
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    secrets = root / ".streamlit" / "secrets.toml"
    if secrets.is_file():
        for line in secrets.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k in ("YOUTUBE_API_KEY", "GEMINI_API_KEY") and v:
                os.environ.setdefault(k, v)


_load_keys()

# Per-session query cap, enforced server side. The browser cannot be trusted
# with this, so the session id is issued here and counted here.
SESSION_QUERY_CAP = 5
_session_counts: dict[str, int] = {}

app = FastAPI(title="Uptick API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        *(os.environ.get("UPTICK_ORIGINS", "").split(",") if os.environ.get("UPTICK_ORIGINS") else []),
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    cache.init_db()
    try:
        cache.prune()
    except Exception:
        pass  # housekeeping must never block startup


class SearchRequest(BaseModel):
    brief: str = Field(min_length=3, max_length=2000)
    session_id: str | None = None


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


NODE_LABELS = {
    "interpret": "Reading the brief",
    "research": "Researching the brand and market",
    "strategize": "Planning the search",
    "discover": "Searching YouTube",
    "enrich": "Fetching channels and recent uploads",
    "dossier": "Compressing candidate dossiers",
    "relevance": "Judging content relevance",
    "audience": "Estimating purchase intent",
    "safety": "Auditing brand safety",
    "sponsorship": "Analysing sponsorship signals",
    "cultural": "Researching cultural standing",
    "score": "Scoring and ranking",
    "narrate": "Writing rationales",
    "audit": "Verifying every claim",
    "review": "Reviewing the shortlist",
}
TOTAL_STEPS = len(NODE_LABELS)


async def _run(brief: str) -> AsyncIterator[str]:
    """Drive the graph, emitting one SSE frame per completed node."""
    yt_key = os.environ.get("YOUTUBE_API_KEY", "")
    gem_key = os.environ.get("GEMINI_API_KEY", "")
    if not (yt_key and gem_key):
        missing = [n for n in ("YOUTUBE_API_KEY", "GEMINI_API_KEY")
                   if not os.environ.get(n)]
        yield _sse("error", {
            "message": (
                f"Live search needs {' and '.join(missing)}. Add "
                f"{'them' if len(missing) > 1 else 'it'} to a .env file in the "
                f"project folder, then restart the server."
            ),
            "setup": True,
        })
        return

    run_id = uuid.uuid4().hex[:12]
    final: dict = {"agent_failures": [], "notices": []}
    done = 0

    try:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        async with AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB) as saver:
            g = graph_mod.build_graph(yt_key, gem_key, checkpointer=saver)
            async for chunk in g.astream(
                {"brief_raw": brief, "run_id": run_id,
                 "agent_failures": [], "notices": []},
                config={"configurable": {"thread_id": run_id}},
            ):
                for node, update in chunk.items():
                    if node in NODE_LABELS:
                        done += 1
                        yield _sse("progress", {
                            "node": node,
                            "label": NODE_LABELS[node],
                            "done": done,
                            "total": TOTAL_STEPS,
                        })
                    if isinstance(update, dict):
                        for k, v in update.items():
                            if k in ("agent_failures", "notices"):
                                final[k] = final.get(k, []) + list(v or [])
                            else:
                                final[k] = v
                await asyncio.sleep(0)  # let the event loop flush the frame
    except Exception as e:
        yield _sse("error", {"message": f"{type(e).__name__}: {e}"})
        return

    if final.get("fatal"):
        yield _sse("error", {
            "message": final["fatal"],
            "keywords": (final.get("query_plan") or {}).get("search_keywords", []),
        })
        return

    yield _sse("result", _shape(final))


def _shape(state: dict) -> dict:
    """Flatten graph state into exactly what the UI needs."""
    import metrics
    rows = graph_mod.apply_audit(
        state.get("scored") or [], state.get("rationales"), state.get("audit")
    )
    shown, held = metrics.shortlist(rows)
    return {
        "intent": state.get("intent") or {},
        "ranked": shown,
        "held_back": held,
        "floor_note": state.get("floor_note") or {},
        "review_manually": state.get("review_manually") or [],
        "excluded": [
            {
                "channel_id": e.get("channel_id"),
                "title": e.get("channel", {}).get("snippet", {}).get("title", ""),
                "exclusion": e.get("exclusion", ""),
            }
            for e in (state.get("excluded") or [])
        ],
        "cultural_sources": state.get("cultural_sources") or [],
        "market": state.get("market") or {},
        "market_sources": state.get("market_sources") or [],
        "review": state.get("review") or {},
        "agent_failures": sorted(set(state.get("agent_failures") or [])),
        "notices": list(dict.fromkeys(state.get("notices") or [])),
        "demo": False,
    }


@app.post("/api/search")
async def search(req: SearchRequest):
    sid = req.session_id or "anon"
    if _session_counts.get(sid, 0) >= SESSION_QUERY_CAP:
        raise HTTPException(429, "That is enough searches for this session.")

    q = cache.quota_status()
    if q["blocked"]:
        raise HTTPException(
            503,
            "New searches are paused for now. Briefs you have already run "
            "still load instantly.",
        )

    _session_counts[sid] = _session_counts.get(sid, 0) + 1
    return StreamingResponse(
        _run(req.brief),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # stop nginx buffering the stream
        },
    )


@app.get("/api/demo")
async def demo():
    """The full interface with no API key, no network call, no quota."""
    d = demo_data.build()
    import metrics
    rows = graph_mod.apply_audit(
        d.get("scored") or [], d.get("rationales"), d.get("audit")
    )
    shown, held = metrics.shortlist(rows)
    return {
        "intent": d.get("intent") or {},
        "ranked": shown,
        "held_back": held,
        "review_manually": d.get("review_manually") or [],
        "excluded": [
            {
                "channel_id": e.get("channel_id"),
                "title": e.get("channel", {}).get("snippet", {}).get("title", ""),
                "exclusion": e.get("exclusion", ""),
            }
            for e in (d.get("excluded") or [])
        ],
        "cultural_sources": d.get("cultural_sources") or [],
        "market": d.get("market") or {},
        "market_sources": d.get("market_sources") or [],
        "review": d.get("review") or {},
        "agent_failures": [],
        "notices": [],
        "demo": True,
    }


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        # False on serverless: each instance keeps its own quota ledger, so
        # the daily search stop under-counts across instances.
        "quota_ledger_reliable": cache.ledger_is_reliable(),
        "keys_configured": bool(
            os.environ.get("YOUTUBE_API_KEY") and os.environ.get("GEMINI_API_KEY")
        ),
    }


# Serve the built frontend when it exists, so production is ONE service.
#
# This mount must run on Vercel too. Vercel detects FastAPI from requirements
# and deploys the whole app as a single function, and a framework preset takes
# precedence over file-based /api functions: every request reaches this app,
# including "/". Nothing else serves web/dist there, so skipping the mount in
# production left "/" answering FastAPI's own 404.
#
# Mounting at "/" does not shadow the API, because Starlette matches routes in
# declaration order and every /api route above is declared before this line.
_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="web")
