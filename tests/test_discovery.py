"""Discovery: trending as a source, named creators verified, pool screened.

A creator who is never surfaced can never be recommended, so discovery reads
the trending charts, scouts named creators and searches, then keeps the
creators with the most independent evidence. A named creator that does not
resolve to a real channel was invented, and must never reach the output.
"""
import cache
import discovery
import graph
import youtube


def test_refs_parse_into_channels_handles_and_videos():
    assert youtube.parse_ref("https://www.youtube.com/@BeerBiceps") == ("handle", "BeerBiceps")
    assert youtube.parse_ref("@ashishchanchlani") == ("handle", "ashishchanchlani")
    assert youtube.parse_ref("https://youtu.be/dQw4w9WgXcQ") == ("video", "dQw4w9WgXcQ")
    assert youtube.parse_ref("youtube.com/watch?v=dQw4w9WgXcQ&t=3") == ("video", "dQw4w9WgXcQ")
    assert youtube.parse_ref("youtube.com/channel/UCX6OQ3DkcsbYNE6H8uQQuVA") == \
        ("channel", "UCX6OQ3DkcsbYNE6H8uQQuVA")
    # A bare name or an email-like string is not a reference.
    assert youtube.parse_ref("Bhuvan Bam") is None
    assert youtube.parse_ref("team@brand.com") is None


def test_name_matching_is_exact_not_fuzzy():
    titles = {"UC1": "Ashish Chanchlani Vines", "UC2": "BB Ki Vines"}
    got = discovery.match_names(["bb ki vines!", "Ashish", "BB"], titles)
    assert got == {"bb ki vines!": "UC2"}


def test_archetype_queries_interleave_product_and_audience():
    persona = {"archetypes": [
        {"name": "Fitness", "search_phrases": ["protein review"], "uses_product_words": True},
        {"name": "Gym food", "search_phrases": ["gym meal prep"], "uses_product_words": True},
        {"name": "Campus comedy", "search_phrases": ["college sketch"], "uses_product_words": False},
    ]}
    qs = graph.plan_queries({"search_keywords": []}, {}, persona, {})
    assert len(qs) == max(1, graph.MAX_SEARCHES_PER_RUN - 1)
    assert {q["intent"] for q in qs} == {"category", "audience_space"}
    assert all(q.get("archetype") for q in qs)


def test_creator_name_queries_never_spend_a_search():
    market = {"search_queries": [
        {"query": "Bhuvan Bam", "intent": "creator_name"},
        {"query": "protein review india", "intent": "category"},
    ]}
    qs = graph.plan_queries({"search_keywords": []}, market, {}, {"geo_raw": "India"})
    assert all(q["intent"] != "creator_name" for q in qs)


def test_trending_videos_stay_out_of_the_metrics_sample():
    """A creator's single most popular upload would inflate their median."""
    pool = [
        {"video_id": "s1", "channel_id": "UC1", "channel_title": "A", "title": "x",
         "found_via": "category"},
        {"video_id": "t1", "channel_id": "UC1", "channel_title": "A", "title": "hit",
         "found_via": "trending"},
    ]
    e = graph.pool_by_channel(pool)["UC1"]
    assert e["search_video_ids"] == ["s1"]
    assert e["found_via"] == {"category", "trending"}


def test_independent_sources_rank_first():
    one = {"channel_id": "A", "found_via": ["category", "audience_space", "paid_placement"],
           "query_hits": 9, "paid_video_ids": ["p"]}
    two = {"channel_id": "B", "found_via": ["scout", "trending"], "query_hits": 0,
           "paid_video_ids": []}
    order = sorted([one, two], key=lambda e: discovery.rank_key(e, {}), reverse=True)
    assert order[0]["channel_id"] == "B"


def test_persona_only_keeps_real_category_ids():
    out = discovery.clean_persona({
        "brand_profile": {"tone": "playful", "price_tier": "nonsense"},
        "target_audience": {"age_bands": [{"band": "18_24", "weight": 2},
                                          {"band": "999", "weight": 1}],
                            "interests": ["comedy", "made_up_interest"]},
        "archetypes": [{"name": "Campus comedy", "category_ids": ["23", "4242"],
                        "search_phrases": ["college sketch"]}],
    }, {"23", "24"})
    assert out["archetypes"][0]["category_ids"] == ["23"]
    assert out["brand_profile"]["price_tier"] == ""
    assert out["target_audience"]["interests"] == ["comedy"]
    assert [b["band"] for b in out["target_audience"]["age_bands"]] == ["18_24"]


def test_trending_days_counts_distinct_days(monkeypatch, tmp_path):
    monkeypatch.setattr(cache, "DB_PATH", str(tmp_path / "t.db"))
    cache._local.__dict__.pop("conn", None)
    cache.init_db()
    days = iter(["2026-11-01", "2026-11-02", "2026-11-02", "2026-11-05"])
    monkeypatch.setattr(cache, "pacific_date", lambda: next(days))
    cache.record_trending("IN", None, ["UC1", "UC2"])
    cache.record_trending("IN", "24", ["UC1"])
    cache.record_trending("IN", None, ["UC1"])       # same day, other chart
    cache.record_trending("US", None, ["UC1"])       # other market
    import datetime as _dt

    class _Fixed(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime(2026, 11, 6, tzinfo=tz)
    monkeypatch.setattr(cache, "datetime", _Fixed)
    got = cache.trending_days("IN", ["UC1", "UC2", "UC3"])
    assert got == {"UC1": 2, "UC2": 1}
    cache._local.__dict__.pop("conn", None)
