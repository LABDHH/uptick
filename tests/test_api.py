"""The HTTP layer: SSE framing, demo endpoint, and the session cap.

The graph itself is covered by test_graph.py; these tests only check that
transport does not lose or mangle what the graph produces.
"""
import importlib
import json
import os
import tempfile

import pytest

import agents
import cache
import youtube


@pytest.fixture(autouse=True)
def stub(monkeypatch, tmp_path):
    tg = importlib.import_module("test_graph")
    monkeypatch.setattr(youtube, "YouTubeClient", tg.FakeYT)
    monkeypatch.setattr(agents, "GeminiClient", tg.FakeGemini)
    monkeypatch.setattr(agents, "THROTTLE_SECONDS", 0.0)
    monkeypatch.setattr(cache, "DB_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    tg.FakeGemini.failing = set()
    cache._local.__dict__.pop("conn", None)
    cache.init_db()
    yield
    cache._local.__dict__.pop("conn", None)


def _frames(raw_chunks):
    """Parse SSE text into (event, payload) pairs."""
    out = []
    for raw in raw_chunks:
        for part in raw.strip().split("\n\n"):
            ev, data = None, []
            for line in part.split("\n"):
                if line.startswith("event:"):
                    ev = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data.append(line.split(":", 1)[1].strip())
            if ev and data:
                out.append((ev, json.loads("\n".join(data))))
    return out


@pytest.mark.asyncio
async def test_stream_emits_progress_then_one_result(monkeypatch, tmp_path):
    import api.main as m
    monkeypatch.setattr(m, "CHECKPOINT_DB", str(tmp_path / "ck.db"))

    chunks = [c async for c in m._run("protein bar india gen z")]
    frames = _frames(chunks)
    kinds = [e for e, _ in frames]

    assert kinds.count("result") == 1
    assert kinds.count("error") == 0
    assert kinds.count("progress") >= 10      # one per graph node
    assert kinds[-1] == "result"              # result always last

    payload = frames[-1][1]
    assert payload["ranked"], "no creators returned"
    assert "intent" in payload and "notices" in payload


@pytest.mark.asyncio
async def test_progress_counts_advance_monotonically(monkeypatch, tmp_path):
    import api.main as m
    monkeypatch.setattr(m, "CHECKPOINT_DB", str(tmp_path / "ck2.db"))

    seen = [p["done"] for e, p in _frames(
        [c async for c in m._run("x")]) if e == "progress"]
    assert seen == sorted(seen), "progress went backwards"
    assert all(p <= len(m.NODE_LABELS) for p in seen)


@pytest.mark.asyncio
async def test_missing_keys_yield_an_error_frame(monkeypatch):
    import api.main as m
    monkeypatch.setenv("YOUTUBE_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    frames = _frames([c async for c in m._run("x")])
    assert frames and frames[0][0] == "error"


def test_demo_endpoint_shape():
    from fastapi.testclient import TestClient
    import api.main as m

    with TestClient(m.app) as client:
        r = client.get("/api/demo")
        assert r.status_code == 200
        d = r.json()
        assert d["demo"] is True
        assert len(d["ranked"]) > 0
        # The demo must exercise the real scoring engine, not canned scores.
        assert all("breakdown" in c and c["breakdown"] for c in d["ranked"])


def test_session_cap_is_enforced_server_side(monkeypatch):
    """The browser cannot be trusted with the cap, so the server counts."""
    from fastapi.testclient import TestClient
    import api.main as m

    m._session_counts.clear()
    monkeypatch.setattr(m, "SESSION_QUERY_CAP", 2)
    with TestClient(m.app) as client:
        for _ in range(2):
            m._session_counts["s1"] = m._session_counts.get("s1", 0) + 1
        r = client.post("/api/search", json={"brief": "test brief", "session_id": "s1"})
        assert r.status_code == 429


def test_keys_load_from_dotenv_and_streamlit_secrets(tmp_path, monkeypatch):
    """Keys were in .streamlit/secrets.toml from the Streamlit era.

    Ignoring a file the user has already filled in produced a "missing key"
    error that was impossible to act on, so both sources are read.
    """
    import api.main as m

    (tmp_path / ".streamlit").mkdir()
    (tmp_path / ".env").write_text('YOUTUBE_API_KEY="from-env"\n')
    (tmp_path / ".streamlit" / "secrets.toml").write_text(
        '# comment\nGEMINI_API_KEY = "from-secrets"\nAPP_PASSWORD = "ignored"\n'
    )

    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)

    m._load_keys(tmp_path)

    assert os.environ["YOUTUBE_API_KEY"] == "from-env"
    assert os.environ["GEMINI_API_KEY"] == "from-secrets"
    # Only the two API keys are read from secrets.toml, nothing else.
    assert "APP_PASSWORD" not in os.environ


def test_environment_wins_over_files(tmp_path, monkeypatch):
    """A real deployment sets env vars; files must never override them."""
    import api.main as m

    (tmp_path / ".env").write_text("YOUTUBE_API_KEY=from-file\n")
    monkeypatch.setenv("YOUTUBE_API_KEY", "from-environment")
    m._load_keys(tmp_path)
    assert os.environ["YOUTUBE_API_KEY"] == "from-environment"


def test_missing_key_error_names_what_to_do(monkeypatch):
    """A setup error must say which key and where to put it."""
    import asyncio
    import api.main as m

    monkeypatch.setenv("YOUTUBE_API_KEY", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")

    async def collect():
        return [c async for c in m._run("x")]

    frames = _frames(asyncio.run(collect()))
    assert frames[0][0] == "error"
    msg = frames[0][1]["message"]
    assert "YOUTUBE_API_KEY" in msg and "GEMINI_API_KEY" in msg
    assert ".env" in msg


def test_vercel_entrypoint_exposes_the_app():
    """Vercel's Python runtime looks for a top-level `app` at the project
    root, not in api/. The re-export in app.py is what makes deploys work."""
    from app import app as exported
    import api.main as m
    assert exported is m.app


def test_vercel_config_is_consistent_with_the_layout():
    import json
    from pathlib import Path

    cfg = json.loads((Path(__file__).resolve().parent.parent / "vercel.json").read_text())
    # The function key must name a real entrypoint file Vercel recognises.
    assert "app.py" in cfg["functions"]
    assert (Path(__file__).resolve().parent.parent / "app.py").is_file()
    # The frontend is gitignored, so Vercel must build it rather than expect it.
    assert "npm run build" in cfg["buildCommand"]
    assert cfg["outputDirectory"] == "web/dist"
    # A 47s run needs far more than the old default.
    assert cfg["functions"]["app.py"]["maxDuration"] >= 120
