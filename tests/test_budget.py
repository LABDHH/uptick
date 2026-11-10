"""Model routing, 3.8 -> Lite fallback, and the monthly spend gate."""
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

import agents
import budget
import cache


@pytest.fixture(autouse=True)
def db(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "DB_PATH", str(tmp_path / "b.db"))
    for k in ("KV_REST_API_URL", "KV_REST_API_TOKEN", "UPSTASH_REDIS_REST_URL",
              "UPSTASH_REDIS_REST_TOKEN", "UPTICK_MONTHLY_BUDGET_USD",
              "UPTICK_PRICES", "UPTICK_EST_RUN_USD", "UPTICK_BUDGET_HEADROOM",
              "UPTICK_MAX_RUN_USD", "UPTICK_FREE_GROUNDED_QUERIES"):
        monkeypatch.delenv(k, raising=False)
    cache._local.__dict__.pop("conn", None)
    cache.init_db()
    yield
    cache._local.__dict__.pop("conn", None)


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- routing

def test_every_agent_in_the_code_has_a_route():
    """A new agent silently landing on the default route is a cost surprise."""
    root = Path(agents.__file__).parent
    src = "\n".join((root / f).read_text()
                    for f in ("agents.py", "discovery.py", "graph.py"))
    called = set(re.findall(r'g\.call\(\s*"(\w+)"', src))
    called |= set(re.findall(r'call_batched\(\s*g, "(\w+)"', src))
    called |= set(re.findall(r'agent="(\w+)"', src))
    assert called, "regex no longer finds the agent names"
    assert called <= set(agents.AGENT_MODELS), called - set(agents.AGENT_MODELS)


def test_routes_use_known_models_and_levels():
    for name, (model, level) in agents.AGENT_MODELS.items():
        assert model in (agents.FLASH, agents.LITE), name
        agents._thinking(level)  # raises on an invalid level


def test_flash_goes_where_judgement_is_hard():
    for name in ("market_search", "shortlist_reviewer", "rationale_writer"):
        assert agents.route(name)[0] == agents.FLASH
    for name in ("market_researcher", "cultural_analyst", "sponsorship_analyst"):
        assert agents.route(name) == (agents.LITE, "minimal")


def test_only_single_call_agents_use_flash():
    """The batched Stage B judges read ~18k tokens each; on 3.8 they would
    double the cost of a search."""
    flash = {n for n, (m, _) in agents.AGENT_MODELS.items() if m == agents.FLASH}
    assert flash == {"market_search", "rationale_writer", "shortlist_reviewer"}


def test_search_past_its_ceiling_drops_to_lite(monkeypatch):
    monkeypatch.setenv("UPTICK_MAX_RUN_USD", "0.10")

    async def one_search():
        budget.begin_run()
        before = agents.route("shortlist_reviewer")
        await budget.record(0.12)
        return before, agents.route("shortlist_reviewer")

    before, after = run(one_search())
    assert before == (agents.FLASH, "medium")
    assert after == (agents.LITE, "medium")
    # A new search starts with a fresh meter.
    async def next_search():
        budget.begin_run()
        return agents.route("shortlist_reviewer")
    assert run(next_search())[0] == agents.FLASH


# ---------------------------------------------------------------- fallback

class Overloaded(Exception):
    code = 503


def _client(fail_on: set[str], errors: list):
    g = agents.GeminiClient.__new__(agents.GeminiClient)
    g.run_id = ""
    seen: list[str] = []

    async def once(model, level, system, payload, schema, temp):
        seen.append(model)
        if model in fail_on:
            raise errors[0]
        return {"ok": model}

    g._generate_once = once
    return g, seen


def test_overloaded_flash_falls_back_to_lite(monkeypatch):
    async def no_sleep(_):
        return None
    monkeypatch.setattr(agents.asyncio, "sleep", no_sleep)
    g, seen = _client({agents.FLASH}, [Overloaded("503 UNAVAILABLE")])
    out = run(g._generate("s", "p", {}, 0.2, agent="shortlist_reviewer"))
    assert out == {"ok": agents.LITE}
    assert seen == [agents.FLASH] * 3 + [agents.LITE]


def test_bad_request_does_not_fall_back():
    g, seen = _client({agents.FLASH}, [RuntimeError("400 INVALID_ARGUMENT")])
    with pytest.raises(RuntimeError):
        run(g._generate("s", "p", {}, 0.2, agent="shortlist_reviewer"))
    assert seen == [agents.FLASH]


def test_truncation_is_never_retried_as_transient():
    assert not agents._transient(agents.OutputTruncated("hit the ceiling"))
    assert not agents._transient(ValueError("Expecting value: char 5003"))
    assert agents._transient(RuntimeError("429 RESOURCE_EXHAUSTED"))


def test_generate_records_cost_and_routes_thinking(monkeypatch):
    sent = {}

    async def generate_content(model, contents, config):
        sent.update(model=model, config=config)
        return NS(
            text='{"results": []}',
            candidates=[NS(finish_reason="STOP", grounding_metadata=None)],
            prompt_feedback=None,
            usage_metadata=NS(prompt_token_count=1_000_000,
                              candidates_token_count=0,
                              thoughts_token_count=0),
        )

    g = agents.GeminiClient.__new__(agents.GeminiClient)
    g.run_id = ""
    g.client = NS(aio=NS(models=NS(generate_content=generate_content)))
    run(g._generate("s", "p", {}, 0.2, agent="shortlist_reviewer"))

    assert sent["model"] == agents.FLASH
    assert sent["config"].thinking_config.thinking_level.value == "MEDIUM"
    assert sent["config"].temperature is None  # 3.8 runs at its default
    spent, _, _ = run(budget._read())
    assert spent == 1_500_000  # 1M input tokens of 3.8 at $1.50


# ---------------------------------------------------------------- pricing

def test_thinking_bills_as_output():
    resp = NS(
        usage_metadata=NS(prompt_token_count=1000, tool_use_prompt_token_count=1000,
                          candidates_token_count=500, thoughts_token_count=500),
        candidates=[NS(grounding_metadata=NS(web_search_queries=["a", "b"]))],
    )
    cost = budget.cost_of_response("gemini-3.5-flash-lite", resp)
    assert cost == pytest.approx(2000 * 0.30 / 1e6 + 1000 * 2.50 / 1e6)
    assert budget.grounded_queries(resp) == 2


def test_grounding_is_free_until_the_monthly_allowance_is_used(monkeypatch):
    monkeypatch.setenv("UPTICK_FREE_GROUNDED_QUERIES", "100")
    run(budget.record(0.0, queries=60))
    assert run(budget.status())["spent_usd"] == 0
    run(budget.record(0.0, queries=60))   # 120 queries, 20 past the allowance
    assert run(budget.status())["spent_usd"] == pytest.approx(20 * 0.014)


def test_unknown_model_is_never_free():
    assert budget.cost_of_usage("some-future-model", 1_000_000, 0) > 0


# ---------------------------------------------------------------- the gate

def test_no_cap_never_blocks():
    s = run(budget.status())
    assert s["configured"] is False and s["blocked"] is False


def test_blocks_when_one_more_search_does_not_fit(monkeypatch):
    monkeypatch.setenv("UPTICK_MONTHLY_BUDGET_USD", "10")
    monkeypatch.setenv("UPTICK_EST_RUN_USD", "1")
    run(budget.record(8.5))          # usable is 9.00, 0.50 left < 1.00
    s = run(budget.status())
    assert s["blocked"] is True and s["searches_left"] == 0


def test_measured_average_replaces_the_estimate(monkeypatch):
    monkeypatch.setenv("UPTICK_MONTHLY_BUDGET_USD", "10")
    for _ in range(4):
        run(budget.start_run())
    run(budget.record(1.0))          # 4 searches cost $1 -> $0.25 each
    s = run(budget.status())
    assert s["avg_is_measured"] and s["avg_run_usd"] == pytest.approx(0.25)
    assert s["searches_left"] == 32  # (9.00 - 1.00) / 0.25
    assert s["blocked"] is False


def test_unreadable_ledger_fails_closed(monkeypatch):
    monkeypatch.setenv("UPTICK_MONTHLY_BUDGET_USD", "10")

    async def broken():
        raise RuntimeError("redis down")

    monkeypatch.setattr(budget, "_read", broken)
    assert run(budget.status())["blocked"] is True


def test_search_endpoint_refuses_when_budget_is_spent(monkeypatch):
    from fastapi.testclient import TestClient
    import api.main as m

    monkeypatch.setenv("UPTICK_MONTHLY_BUDGET_USD", "1")
    run(budget.record(5.0))
    m._session_counts.clear()
    with TestClient(m.app) as client:
        r = client.post("/api/search", json={"brief": "test brief", "session_id": "b1"})
    assert r.status_code == 503
    assert "quota" not in r.text.lower() and "$" not in r.text


def test_meter_reaches_agents_running_inside_the_graph(monkeypatch):
    """The ceiling only works if agent calls made inside LangGraph's node
    tasks see the meter that /api/search started for that search."""
    import importlib
    from fastapi.testclient import TestClient
    import api.main as m
    import youtube

    tg = importlib.import_module("test_graph")
    seen: list[bool] = []

    class MeteredGemini(tg.FakeGemini):
        async def call(self, name, *a, **k):
            seen.append(budget.over_run_ceiling())
            await budget.record(1.0)   # each call blows past the ceiling
            return await super().call(name, *a, **k)

    monkeypatch.setattr(youtube, "YouTubeClient", tg.FakeYT)
    monkeypatch.setattr(agents, "GeminiClient", MeteredGemini)
    monkeypatch.setenv("YOUTUBE_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("UPTICK_MAX_RUN_USD", "0.5")
    monkeypatch.setattr(m, "CHECKPOINT_DB", str(Path(cache.DB_PATH).with_name("ck.db")))
    tg.FakeGemini.failing = set()
    m._session_counts.clear()

    with TestClient(m.app) as client:
        r = client.post("/api/search", json={"brief": "protein for students in India",
                                             "session_id": "meter"})
        assert r.status_code == 200
        r.read()

    assert len(seen) > 2
    assert seen[0] is False            # fresh meter at the start of the search
    assert all(seen[1:])               # every later call sees the spend
