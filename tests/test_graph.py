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
                            paid_placement_only=False, max_results=50, **kw):
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

    async def trending_pool(self, rc, cat=None, pages=2):
        return [{"video_id": "trend1", "channel_id": CH[1],
                 "channel_title": "Creator 1", "title": "Trending sketch",
                 "category_id": cat or "", "rank": 1}]

    async def resolve_refs(self, refs):
        # Only real handles resolve; an invented one must be dropped.
        known = {"@creator3": CH[3], "@creator4": CH[4]}
        return {r: known[r] for r in refs if r in known}

    async def top_comments(self, video_id, n=20):
        return ["exam tomorrow and still watching", "watching from Delhi bhai"]

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
                "topicDetails": {"topicCategories": [
                    "https://en.wikipedia.org/wiki/Lifestyle_(sociology)"]},
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

    async def search_grounded(self, system, payload, temp, **kw):
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
            # Creator 5 is genuinely off-brief: no product moment, wrong tone,
            # wrong audience. Everyone else fits to some degree.
            return {"results": [{"channel_id": c,
                                 "product_fit": "none" if i == 5 else
                                 ("natural" if i % 2 else "plausible"),
                                 "product_moments": [] if i == 5 else [f"Video up{i}_1"],
                                 "brand_fit": ({"tone": "clash", "price_tier": "clash",
                                                "values": "clash"} if i == 5 else
                                               {"tone": "match", "price_tier": "neutral",
                                                "values": "match"}),
                                 "brand_evidence": "" if i == 5 else "upbeat campus humour",
                                 "archetype": "College comedians" if i % 2 else "Fitness",
                                 "reason": "cites a title", "match_type": "lifestyle"}
                                for i, c in enumerate(CH)]
                    + [{"channel_id": "UC_HALLUCINATED", "product_fit": "natural",
                        "product_moments": ["fake"], "brand_fit": {},
                        "brand_evidence": "", "archetype": "", "reason": "fake",
                        "match_type": "direct"}]}
        if name == "audience_analyst":
            assert "exam tomorrow" in payload, "comments never reached the analyst"
            return {"results": [{"channel_id": c,
                                 "profile": {
                                     "age_bands": [{"band": "45_plus" if i == 5 else "18_24",
                                                    "weight": 1.0}],
                                     "life_stages": ["retired" if i == 5 else "college"],
                                     "interests": ["gardening"] if i == 5 else
                                                  ["comedy", "fitness_gym"],
                                     "languages": ["hi"], "region": "IN",
                                     "spending_power": "mid",
                                     "confidence": {"age_bands": "medium",
                                                    "life_stages": "medium",
                                                    "interests": "high",
                                                    "languages": "high",
                                                    "region": "medium",
                                                    "spending_power": "low"}},
                                 "buyer_overlap": "weak" if i == 5 else "good",
                                 "evidence": ["exam tomorrow and still watching"],
                                 "inferred_viewer_profile": "p",
                                 "purchase_intent_signal": "high", "reasoning": "r"}
                                for i, c in enumerate(CH)]}
        if name == "persona_builder":
            return {
                "brand_profile": {"tone": "playful", "price_tier": "mass",
                                  "values": ["honest"], "avoid": ["steroids"],
                                  "languages": ["hi", "en"]},
                "product_moments": ["between lectures", "post-workout"],
                "target_audience": {
                    "age_bands": [{"band": "18_24", "weight": 0.7},
                                  {"band": "25_34", "weight": 0.3}],
                    "life_stages": ["college", "early_career"],
                    "interests": ["comedy", "fitness_gym", "college_campus"],
                    "languages": ["hi", "en"], "region": "IN",
                    "spending_power": "mid",
                    "confidence": {"age_bands": "high", "life_stages": "high",
                                   "interests": "medium", "languages": "high",
                                   "region": "high", "spending_power": "medium"}},
                "archetypes": [
                    {"name": "College comedians",
                     "why_audience_matches": "students watch them",
                     "search_phrases": ["college comedy sketch india"],
                     "category_ids": ["23", "999"], "uses_product_words": False,
                     "known_creators": [{"name": "Creator 4", "youtube_ref": "@creator4"}]},
                    {"name": "Fitness",
                     "why_audience_matches": "gym-goers buy protein",
                     "search_phrases": ["protein powder review india"],
                     "category_ids": ["26"], "uses_product_words": True,
                     "known_creators": []},
                ],
            }
        if name == "scout_structurer":
            return {"creators": [
                {"name": "Creator 3", "youtube_ref": "@creator3",
                 "archetype": "College comedians", "evidence": "campus favourite"},
                {"name": "Ghost Creator", "youtube_ref": "@ghost_not_real",
                 "archetype": "College comedians", "evidence": "invented"},
            ]}
        if name == "prescreener":
            import re
            ids = list(dict.fromkeys(re.findall(r'"channel_id":"(UC[^"]+)"', payload)))
            return {"results": [{"channel_id": c, "keep": True, "archetype": "Fitness",
                                 "reason": "ok"} for c in ids]}
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
async def test_unverified_rationales_are_replaced_by_a_code_summary():
    """A 'revise' verdict never ships the writer's prose, but the creator still
    says why it is on the list: a summary built in code from its own numbers."""
    st = await run()
    rows = graph.apply_audit(st["scored"], st["rationales"], st["audit"])
    revised = [r for r in rows if (st["audit"].get(r["channel_id"]) or {})
               .get("verdict") == "revise"]
    assert revised
    for r in revised:
        assert r["rationale_status"] == "summary"
        assert r["rationale"] != st["rationales"][r["channel_id"]]["rationale"]
        assert r["rationale"] and r["headline"]
    assert all(r.get("rationale") for r in rows)
    assert all(r.get("key_numbers") is not None for r in rows)


@pytest.mark.parametrize("victim", [
    "persona_builder", "scout_structurer",
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
    # The scout's two calls (grounded search, then structuring) fail as one.
    reported = {"scout_structurer": "creator_scout"}.get(victim, victim)
    assert reported in st["agent_failures"]


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


@pytest.mark.asyncio
async def test_relevance_first_flow_end_to_end():
    """Persona -> trending + scout + search -> screen -> relevance gate."""
    st = await run()
    ids = {r["channel_id"] for r in st["scored"]}
    # Creator 5 was judged off-brief: excluded with the reason, never ranked.
    assert CH[5] not in ids
    assert any(e["channel_id"] == CH[5] and "off-brief" in e["exclusion"]
               for e in st["excluded"])
    # The scout's real handle resolved; its invented one was dropped and said so.
    by = {r["channel_id"]: r for r in st["scored"]}
    assert "scout" in by[CH[3]]["found_via"]
    assert any("could not be verified" in n for n in st["notices"])
    # Trending is a source, not just a flag.
    assert "trending" in by[CH[1]]["found_via"]
    # Every row explains its relevance in parts.
    top = st["scored"][0]
    assert {"audience_match", "product_fit", "brand_fit"} <= {
        b["term"] for b in top["breakdown"]}


@pytest.mark.asyncio
async def test_searches_per_brief_stay_within_budget():
    calls = []
    orig = FakeYT.search_videos

    async def counting(self, *a, **k):
        calls.append(k.get("paid_placement_only"))
        return await orig(self, *a, **k)

    FakeYT.search_videos = counting
    try:
        await run()
    finally:
        FakeYT.search_videos = orig
    assert len(calls) <= graph.MAX_SEARCHES_PER_RUN
