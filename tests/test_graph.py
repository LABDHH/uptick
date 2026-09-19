"""End-to-end graph behavior with stubbed APIs.

The guarantee under test (§12): killing any one agent still returns ranked
results.
"""
from datetime import datetime, timedelta, timezone

import pytest

import agents
import cache
import graph
import youtube

NC = 12
CH = [f"UC{i:022d}" for i in range(NC)]


def ts(d):
    return (datetime.now(timezone.utc) - timedelta(days=d)).isoformat().replace("+00:00", "Z")


class FakeYT:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def video_categories(self, rc):
        return [{"id": "20", "title": "Gaming"}, {"id": "26", "title": "Howto & Style"}]

    async def search_videos(self, q, rc, cat=None, lang=None,
                            paid_placement_only=False, max_results=50):
        out = []
        for i, c in enumerate(CH):
            if paid_placement_only and i % 3:
                continue
            out.append({
                "video_id": f"vid{i}_{'p' if paid_placement_only else 'r'}",
                "channel_id": c, "channel_title": f"Creator {i}",
                "title": f"Best protein powder review {i}",
                "paid_placement_hit": paid_placement_only,
            })
        return out

    async def trending_channels(self, rc, cat=None):
        return {CH[1]}

    async def channels(self, ids):
        out = {}
        for c in ids:
            idx = CH.index(c)
            out[c] = {
                "id": c,
                "snippet": {"title": f"Creator {idx}", "description": "Fitness",
                            "country": "IN", "publishedAt": ts(900)},
                "statistics": {"subscriberCount": str(250_000 * (idx + 1)),
                               "videoCount": "200", "viewCount": "5000000"},
                "contentDetails": {"relatedPlaylists": {"uploads": f"UU{idx:022d}"}},
                "status": {"madeForKids": idx == 11},
            }
        return out

    async def recent_uploads(self, pl, mr=20):
        idx = int(pl[2:])
        return [f"up{idx}_{j}" for j in range(10)]

    async def videos(self, ids):
        out = {}
        for v in ids:
            seed = hash(v) % 1000
            out[v] = {
                "id": v,
                "snippet": {"channelId": CH[0], "title": f"Video {v}",
                            "description": "Sponsored by X, use code SAVE10",
                            "publishedAt": ts(20 + seed % 100),
                            "liveBroadcastContent": "none"},
                "statistics": {"viewCount": str(200_000 + seed * 900),
                               "likeCount": str(9000 + seed * 20),
                               "commentCount": str(50 + seed // 10)},
                "contentDetails": {"duration": "PT10M"}, "_duration_s": 600,
            }
        return out


class FakeGemini:
    """Returns schema-shaped output, plus one hallucinated channel_id."""
    failing: set = set()

    def __init__(self, *a, **k):
        self.run_id = ""

    async def search_grounded(self, system, payload, temp):
        if "cultural_analyst" in self.failing:
            raise RuntimeError("search down")
        return "Briefing: creators tour nationally.", ["example.com"]

    async def call(self, name, system, payload, schema, temp, cache_key=None):
        if name in self.failing:
            raise RuntimeError(f"{name} is down")
        if name == "brief_interpreter":
            return {"brand": "ProteinCo", "product": "whey", "product_category": "nutrition",
                    "target_audience": "students who snack between classes",
                    "size_band": "mid", "geo_raw": "India",
                    "target_generation": "gen_z", "cultural_angle": "campus life",
                    "geo_granularity": "country", "region_code": "IN",
                    "brand_safety_sensitivities": ["steroids"], "ambiguities": []}
        if name == "query_strategist":
            return {"youtube_category_id": "26",
                    "search_keywords": ["protein powder review", "best whey india"],
                    "relevance_language": "hi", "region_code": "IN", "excluded_terms": []}
        if name == "relevance_judge":
            return {"results": [{"channel_id": c, "relevance": 0.5 + 0.03 * i,
                                 "reason": "cites a title", "match_type": "direct"}
                                for i, c in enumerate(CH)]
                    + [{"channel_id": "UC_HALLUCINATED", "relevance": 1.0,
                        "reason": "fake", "match_type": "direct"}]}
        if name == "audience_analyst":
            return {"results": [{"channel_id": c, "audience_fit": 0.4 + 0.04 * i,
                                 "inferred_viewer_profile": "p",
                                 "purchase_intent_signal": "high", "reasoning": "r"}
                                for i, c in enumerate(CH)]}
        if name == "safety_auditor":
            return {"results": [{"channel_id": c, "flag": i == 2,
                                 "severity": "high" if i == 2 else "low",
                                 "category": "controversy" if i == 2 else "none",
                                 "reason": "r", "evidence": "e"}
                                for i, c in enumerate(CH)]}
        if name == "sponsorship_analyst":
            return {"results": [{"channel_id": c, "sponsor_confidence": 0.3 + 0.05 * i,
                                 "observed_format": "integrated_mention",
                                 "cadence": "occasional",
                                 "known_sponsor_categories": ["supplements"],
                                 "evidence": "use code SAVE10"}
                                for i, c in enumerate(CH)]}
        if name == "market_researcher":
            return {
                "brand_known": True,
                "brand_profile": "A challenger nutrition brand.",
                "brand_positioning": "Affordable, no-nonsense.",
                "brand_story_angle": "Built by lifters, not marketers.",
                "known_competitors": ["RivalWhey", "BigNutrition"],
                "competitor_creator_tactics": "Rivals run discount codes.",
                "category_landscape": "Crowded and price-driven.",
                "audience_watch_habits": ["campus vlogs", "standup comedy"],
                "named_creators": [
                    {"name": "Creator 3", "why": "big with students",
                     "channel_hint": "creator3"},
                ],
                "search_queries": [
                    {"query": "protein powder review india", "intent": "category",
                     "rationale": "obvious"},
                    {"query": "college day in my life india", "intent": "audience_space",
                     "rationale": "where buyers are"},
                    {"query": "rivalwhey sponsored", "intent": "competitor",
                     "rationale": "find rival deals"},
                ],
                "red_flags": [],
                "confidence": "medium",
            }
        if name == "shortlist_reviewer":
            return {
                "verdict": "ship_with_caveat",
                "headline": "Usable shortlist, thin on verified sponsors",
                "what_we_found": "Mostly fitness creators.",
                "how_to_use_this": "Start with the top three.",
                "gaps": ["few declared paid promotions"],
                "suggested_refinements": ["name a price point"],
                "flagged_channel_ids": [],
                "diversity_note": "Concentrated in one niche.",
            }
        if name == "cultural_analyst":
            import re
            ids = list(dict.fromkeys(re.findall(r'(UC\d{22})', payload)))
            return {"results": [{"channel_id": c, "cultural_relevance": 0.7,
                                 "fame_tier": "scene_famous", "persona": "p",
                                 "audience_generation": "gen_z",
                                 "sustained_or_spike": "sustained",
                                 "notable_context": "tours", "brand_fit_note": "fit",
                                 "evidence_found": True} for c in ids]}
        import re
        cids = list(dict.fromkeys(re.findall(r'"channel_id":\s*"(UC[^"]+)"', payload)))
        if name == "rationale_writer":
            return {"results": [{"channel_id": c, "headline": "Strong ratio fit",
                                 "rationale": "Good engagement.", "caveat": None}
                                for c in cids]}
        if name == "output_auditor":
            return {"results": [{"channel_id": c,
                                 "verdict": "pass" if i % 4 else "revise",
                                 "unsupported_claims": [] if i % 4 else ["made-up stat"]}
                                for i, c in enumerate(cids)]}
        raise AssertionError(name)


@pytest.fixture(autouse=True)
def stub(monkeypatch, tmp_path):
    monkeypatch.setattr(youtube, "YouTubeClient", FakeYT)
    monkeypatch.setattr(agents, "GeminiClient", FakeGemini)
    monkeypatch.setattr(agents, "THROTTLE_SECONDS", 0.0)
    monkeypatch.setattr(cache, "DB_PATH", str(tmp_path / "t.db"))
    cache._local.__dict__.pop("conn", None)
    cache.init_db()
    FakeGemini.failing = set()
    yield
    cache._local.__dict__.pop("conn", None)


async def run(brief="protein powder brand, India, mid-size creators"):
    g = graph.build_graph("k", "k")
    return await g.ainvoke(
        {"brief_raw": brief, "run_id": "t", "agent_failures": [], "notices": []}
    )


@pytest.mark.asyncio
async def test_happy_path():
    st = await run()
    assert st.get("fatal") is None
    assert len(st["scored"]) > 0
    assert len(st["review_manually"]) == 1          # high severity routed, not dropped
    assert any("kids" in e["exclusion"] for e in st["excluded"])  # COPPA


@pytest.mark.asyncio
async def test_hallucinated_channel_id_never_reaches_output():
    st = await run()
    assert "UC_HALLUCINATED" not in {r["channel_id"] for r in st["scored"]}


@pytest.mark.asyncio
async def test_unverified_rationales_are_withheld():
    st = await run()
    rows = graph.apply_audit(st["scored"], st["rationales"], st["audit"])
    statuses = {r["rationale_status"] for r in rows}
    assert "withheld_unverified" in statuses  # a 'revise' verdict drops the prose


@pytest.mark.parametrize("victim", [
    "relevance_judge", "audience_analyst", "safety_auditor",
    "sponsorship_analyst", "rationale_writer", "output_auditor", "query_strategist",
    "cultural_analyst", "market_researcher", "shortlist_reviewer",
])
@pytest.mark.asyncio
async def test_killing_any_one_agent_still_returns_results(victim):
    FakeGemini.failing = {victim}
    st = await run()
    assert st.get("fatal") is None
    assert len(st["scored"]) > 0
    assert victim in st["agent_failures"]


@pytest.mark.asyncio
async def test_all_stage_b_agents_down_degrades_to_metrics_only():
    FakeGemini.failing = {"relevance_judge", "audience_analyst",
                          "safety_auditor", "sponsorship_analyst"}
    st = await run()
    assert st.get("fatal") is None
    assert len(st["scored"]) > 0
    assert st["scored"][0]["partial"] is True


@pytest.mark.asyncio
async def test_output_auditor_failure_suppresses_all_prose():
    """Never ship unaudited claims."""
    FakeGemini.failing = {"output_auditor"}
    st = await run()
    rows = graph.apply_audit(st["scored"], st.get("rationales"), st.get("audit"))
    assert all(r.get("rationale_status") != "verified" for r in rows)
