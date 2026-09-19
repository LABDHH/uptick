"""Agent 9 — cultural standing beyond YouTube's own signals."""
import pytest

import agents
import metrics


@pytest.fixture(autouse=True)
def fast(monkeypatch, tmp_path):
    import cache
    monkeypatch.setattr(agents, "THROTTLE_SECONDS", 0.0)
    monkeypatch.setattr(cache, "DB_PATH", str(tmp_path / "c.db"))
    cache._local.__dict__.pop("conn", None)
    cache.init_db()
    yield
    cache._local.__dict__.pop("conn", None)


class FakeCultural(agents.GeminiClient):
    def __init__(self, briefing="", rows=None, fail_search=False):
        self.run_id = ""
        self.client = None
        self.briefing = briefing
        self.rows = rows or []
        self.fail_search = fail_search
        self.searched = 0

    async def search_grounded(self, system, payload, temp):
        self.searched += 1
        if self.fail_search:
            raise RuntimeError("search unavailable")
        return self.briefing, ["thehindu.com", "scroll.in"]

    async def call(self, name, system, payload, schema, temp, cache_key=None):
        return {"results": self.rows}


CANDS = [
    {"channel_id": "UCcomic", "title": "Standup Comic", "description": "comedy"},
    {"channel_id": "UCplain", "title": "Plain Creator", "description": "vlogs"},
]


@pytest.mark.asyncio
async def test_finds_fame_youtube_signals_would_miss():
    g = FakeCultural(
        briefing="UCcomic tours nationally and headlines comedy festivals.",
        rows=[
            {"channel_id": "UCcomic", "cultural_relevance": 0.9,
             "fame_tier": "household_name", "persona": "Sharp observational comic",
             "audience_generation": "gen_z", "sustained_or_spike": "sustained",
             "notable_context": "National tour", "brand_fit_note": "Strong fit",
             "evidence_found": True},
            {"channel_id": "UCplain", "cultural_relevance": 0.0,
             "fame_tier": "unknown", "persona": "", "audience_generation": "unclear",
             "sustained_or_spike": "unknown", "notable_context": "",
             "brand_fit_note": "", "evidence_found": False},
        ],
    )
    rows, sources = await agents.agent9_cultural(g, CANDS, {"brand": "B"}, "")
    assert rows["UCcomic"]["fame_tier"] == "household_name"
    assert sources                      # sources surfaced for auditing
    # No coverage must never become a negative score.
    assert rows["UCplain"]["cultural_relevance"] == 0.0


@pytest.mark.asyncio
async def test_hallucinated_ids_are_dropped_here_too():
    g = FakeCultural(briefing="x", rows=[
        {"channel_id": "UCnot_a_candidate", "cultural_relevance": 1.0,
         "fame_tier": "household_name", "persona": "p",
         "audience_generation": "gen_z", "sustained_or_spike": "sustained",
         "notable_context": "", "brand_fit_note": "", "evidence_found": True},
    ])
    rows, _ = await agents.agent9_cultural(g, CANDS, {"brand": "B"}, "")
    assert rows == {}


@pytest.mark.asyncio
async def test_search_failure_raises_so_the_graph_can_degrade():
    g = FakeCultural(fail_search=True)
    with pytest.raises(agents.AgentFailure):
        await agents.agent9_cultural(g, CANDS, {"brand": "B"}, "")


def test_no_evidence_is_never_a_penalty():
    """Most creators have no press coverage; that must not push them down."""
    assert metrics.cultural_bonus(None) == (0.0, "")
    assert metrics.cultural_bonus({"evidence_found": False}) == (0.0, "")


def test_sustained_fame_outranks_a_viral_spike():
    sustained, _ = metrics.cultural_bonus(
        {"evidence_found": True, "cultural_relevance": 0.9,
         "fame_tier": "household_name", "sustained_or_spike": "sustained"})
    spike, _ = metrics.cultural_bonus(
        {"evidence_found": True, "cultural_relevance": 0.9,
         "fame_tier": "household_name", "sustained_or_spike": "spike"})
    assert sustained > spike * 2


def test_bonus_is_capped_and_cannot_dominate_the_score():
    bonus, _ = metrics.cultural_bonus(
        {"evidence_found": True, "cultural_relevance": 1.0,
         "fame_tier": "household_name", "sustained_or_spike": "sustained"},
        "gen_z")
    assert bonus <= metrics.CULTURAL_BONUS_MAX


def test_generation_match_gives_a_lift():
    row = {"evidence_found": True, "cultural_relevance": 0.8,
           "fame_tier": "scene_famous", "sustained_or_spike": "sustained",
           "audience_generation": "gen_z"}
    matched, _ = metrics.cultural_bonus(row, "gen_z")
    unmatched, _ = metrics.cultural_bonus(row, "older")
    assert matched > unmatched
