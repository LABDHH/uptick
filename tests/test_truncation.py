"""Regression tests for the Stage B truncation failures.

Observed in production: safety_auditor and sponsorship_analyst failed on 3 of 4
runs at 0ms latency while the other six agents passed. Those two have the
largest per-candidate output schemas (6 fields), so at 25 candidates they hit
the output-token ceiling, came back as truncated half-JSON, and the repair
retry re-sent the identical oversized request.
"""
import json

import pytest

import agents
import prompts
import schemas


@pytest.fixture(autouse=True)
def fast_throttle(monkeypatch, tmp_path):
    import cache
    monkeypatch.setattr(agents, "THROTTLE_SECONDS", 0.0)
    monkeypatch.setattr(cache, "DB_PATH", str(tmp_path / "t.db"))
    cache._local.__dict__.pop("conn", None)
    cache.init_db()
    yield
    cache._local.__dict__.pop("conn", None)


def dossier(n):
    return {f"UC{i:020d}": {"channel_id": f"UC{i:020d}", "title": f"C{i}",
                            "videos": []} for i in range(n)}


class Client(agents.GeminiClient):
    """Truncates whenever a request carries more than `limit` candidates."""
    def __init__(self, limit):
        self.run_id = ""
        self.client = None
        self.limit = limit
        self.calls = []

    async def _generate(self, system, payload, schema, temp):
        n = payload.count('"channel_id"')
        self.calls.append(n)
        if n > self.limit:
            raise agents.OutputTruncated("hit the output ceiling")
        return {"results": [{"channel_id": c, "sponsor_confidence": 0.5,
                             "observed_format": "integrated_mention",
                             "cadence": "occasional",
                             "known_sponsor_categories": [], "evidence": "e"}
                            for c in json.loads(
                                payload.split("<candidate_data>")[1]
                                       .split("</candidate_data>")[0]
                            )[0:] and
                            [d["channel_id"] for d in json.loads(
                                payload.split("<candidate_data>")[1]
                                       .split("</candidate_data>")[0])]]}


@pytest.mark.asyncio
async def test_oversized_set_is_split_not_failed():
    """The original bug: 25 candidates truncated and the whole agent failed."""
    g = Client(limit=agents.STAGE_B_BATCH)
    out = await agents.agent6_sponsorship(g, dossier(25), {"brand": "B"}, "")
    assert len(out) == 25                          # every candidate still judged
    assert max(g.calls) <= agents.STAGE_B_BATCH    # each request fit the ceiling


@pytest.mark.asyncio
async def test_small_set_still_goes_in_one_call():
    """Comparability matters: don't split when the set already fits."""
    g = Client(limit=100)
    n = agents.STAGE_B_BATCH
    out = await agents.agent6_sponsorship(g, dossier(n), {"brand": "B"}, "")
    assert len(out) == n
    assert len(g.calls) == 1


@pytest.mark.asyncio
async def test_truncation_is_never_retried_verbatim():
    """Re-sending an identical oversized request cannot help and wastes RPD."""
    class AlwaysTruncates(agents.GeminiClient):
        def __init__(self):
            self.run_id = ""
            self.client = None
            self.n = 0

        async def _generate(self, system, payload, schema, temp):
            self.n += 1
            raise agents.OutputTruncated("ceiling")

    g = AlwaysTruncates()
    with pytest.raises(agents.AgentFailure):
        await agents.agent6_sponsorship(g, dossier(2), {"brand": "B"}, "")
    # 2 candidates -> split to 1+1, each tried once. No repair retries.
    assert g.n == 3, f"expected 3 attempts (1 whole + 2 halves), got {g.n}"


@pytest.mark.asyncio
async def test_a_transient_failure_is_repaired_not_lost():
    """A single bad response is repaired by the one retry, losing nothing."""
    class OneBadResponse(agents.GeminiClient):
        def __init__(self):
            self.run_id = ""
            self.client = None
            self.seen = 0

        async def _generate(self, system, payload, schema, temp):
            self.seen += 1
            if self.seen == 1:
                raise json.JSONDecodeError("bad", "", 0)
            ids = [d["channel_id"] for d in json.loads(
                payload.split("<candidate_data>")[1].split("</candidate_data>")[0])]
            return {"results": [{"channel_id": c, "relevance": 0.7,
                                 "reason": "r", "match_type": "direct"} for c in ids]}

    g = OneBadResponse()
    n = agents.STAGE_B_BATCH * 2
    out = await agents.agent3_relevance(g, dossier(n), {"brand": "B"}, "")
    assert len(out) == n       # repair retry recovered the batch
    assert g.seen == 3         # two batches, the first retried once


@pytest.mark.asyncio
async def test_one_dead_batch_still_returns_the_others():
    """Partial success is success: surviving batches must still count."""
    class FirstBatchDead(agents.GeminiClient):
        def __init__(self):
            self.run_id = ""
            self.client = None
            self.seen = 0

        async def _generate(self, system, payload, schema, temp):
            self.seen += 1
            ids = [d["channel_id"] for d in json.loads(
                payload.split("<candidate_data>")[1].split("</candidate_data>")[0])]
            # Both the first attempt and its repair retry fail.
            if self.seen <= 2:
                raise json.JSONDecodeError("bad", "", 0)
            return {"results": [{"channel_id": c, "relevance": 0.7,
                                 "reason": "r", "match_type": "direct"} for c in ids]}

    g = FirstBatchDead()
    n = agents.STAGE_B_BATCH
    out = await agents.agent3_relevance(g, dossier(n * 2), {"brand": "B"}, "")
    assert len(out) == n       # first batch lost entirely, second recovered


def test_output_ceiling_is_large_enough_for_a_full_set():
    """25 candidates x ~6 fields must fit well inside the ceiling."""
    assert agents.MAX_OUTPUT_TOKENS >= 32768
    assert agents.STAGE_B_BATCH <= 15
    # Still large enough that candidates are judged against each other.
    assert agents.STAGE_B_BATCH >= 5


def test_throttle_works_across_event_loops():
    """Streamlit calls asyncio.run() per query, creating a new loop each time.

    A module-level asyncio.Lock binds to the first loop that awaits it, so
    every query after the first died with 'bound to a different event loop'.
    Run in a worker thread because pytest-asyncio already owns a loop here.
    """
    import asyncio as aio
    import concurrent.futures as cf

    agents.THROTTLE_SECONDS = 0.0

    async def work():
        await agents.throttle()
        await agents.throttle()
        return True

    def one_query():
        return aio.run(work())

    with cf.ThreadPoolExecutor(max_workers=1) as ex:
        # Three sequential queries, three separate event loops.
        for _ in range(3):
            assert ex.submit(one_query).result() is True

    assert len(agents._locks) <= 1, "locks accumulated across loops"
