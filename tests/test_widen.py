"""Ten good creators, each with a reason a marketer can read.

What these tests guard:
  * Shorts-first creators are measured on their Shorts, not thrown away as
    "insufficient data" (most of a market's trending chart is Shorts).
  * A studied creator the data rules out is replaced from the pool before any
    judge is paid to read them.
  * Too few strong matches widens the search one level up, with no extra
    search.list call, and the widened creators are scored together with the
    first pass's (whose judgments are kept, not overwritten).
  * The shortlist fills toward ten with honestly labelled picks.
  * Every creator shows a plain reason and rounded supporting numbers, never
    a raw float like 0.7629912063709234.
"""
import re

import pytest

import explain
import graph
import metrics
from tests.test_graph import CH, FakeGemini, FakeYT, run, stub  # noqa: F401
from tests.test_metrics import chan, vid


# ---------------------------------------------------------------- Shorts

def test_shorts_first_creator_is_measured_on_shorts():
    shorts = [vid(f"s{i}", 900_000 + i, likes=40_000, dur=45, days=5 + i) for i in range(12)]
    one_long = [vid("l1", 200_000, likes=5_000, dur=900, days=20)]
    m = metrics.channel_metrics(chan("S", 2_000_000), shorts + one_long, 0, 0)
    assert m["format"] == "shorts"
    assert m["eligible_video_count"] == 12
    assert metrics.confidence(m) != "insufficient"


def test_long_videos_win_when_there_are_enough():
    longs = [vid(f"l{i}", 300_000, likes=9_000, dur=900, days=10 + i) for i in range(5)]
    shorts = [vid(f"s{i}", 2_000_000, likes=90_000, dur=40, days=5) for i in range(20)]
    m = metrics.channel_metrics(chan("L", 2_000_000), longs + shorts, 0, 0)
    assert m["format"] == "long"
    assert m["median_views"] == 300_000          # Shorts never leak into the median


def test_shorts_creators_are_compared_with_shorts_creators():
    """A Shorts creator's views are ranked among Shorts, never against long-form."""
    def cand(cid, views, dur):
        vids = [vid(f"{cid}{i}", views, likes=views // 30, dur=dur, days=10 + i)
                for i in range(8)]
        return {"channel_id": cid, "channel": chan(cid, 1_000_000),
                "metrics": metrics.channel_metrics(chan(cid, 1_000_000), vids, 0, 0),
                "top_videos": []}
    cands = [cand("LONG_A", 200_000, 900), cand("LONG_B", 400_000, 900),
             cand("SHORT_A", 3_000_000, 40), cand("SHORT_B", 6_000_000, 40)]
    ranked, _, _ = metrics.score_candidates(cands, {}, {}, {}, {}, set(), "macro", set())
    reach = {r["channel_id"]: next(b["value"] for b in r["breakdown"] if b["term"] == "reach")
             for r in ranked}
    assert reach["LONG_B"] == reach["SHORT_B"] == 1.0
    assert reach["LONG_A"] == reach["SHORT_A"] == 0.0


# ---------------------------------------------------------------- shortlist

def _row(cid, score, conf="high"):
    return {"channel_id": cid, "fit_score": score, "confidence": conf}


def test_shortlist_fills_toward_ten_with_labelled_picks():
    rows = ([_row(f"top{i}", 80 - i) for i in range(5)]
            + [_row(f"thin{i}", 60 - i, "low") for i in range(2)]
            + [_row(f"wide{i}", 42 - i) for i in range(6)]
            + [_row("weak", 20)])
    shown, held = metrics.shortlist(rows)
    tiers = {r["channel_id"]: r["tier"] for r in shown}
    assert len(shown) == metrics.SHORTLIST_TARGET
    assert sum(t == "top" for t in tiers.values()) == 5
    assert sum(t == "limited_data" for t in tiers.values()) == 2
    assert sum(t == "broader" for t in tiers.values()) == 3
    assert "weak" not in tiers
    assert "too low" in held["note"] and "auditor" not in held["note"]


def test_low_confidence_is_held_back_when_the_list_is_already_full():
    rows = [_row(f"c{i}", 90 - i) for i in range(12)] + [_row("thin", 70, "low")]
    shown, held = metrics.shortlist(rows)
    assert "thin" not in {r["channel_id"] for r in shown}
    assert "too few recent videos" in held["note"]


# ---------------------------------------------------------------- explanations

def test_numbers_are_rounded_for_people():
    assert explain.compact(1_350_000) == "1.4M"
    assert explain.compact(597_029.5) == "597K"
    assert explain.compact(8_421) == "8.4K"
    assert explain.compact(41_400_000) == "41M"
    assert explain.pct(0.012944379387982645) == "1.3%"


LONG_FLOAT = re.compile(r"\d\.\d{4,}")


def _scored_row():
    vids = [vid(f"v{i}", 500_000 + 10_000 * i, likes=9_000, comments=300, dur=900,
                days=10 + 5 * i) for i in range(10)]
    cands = [{"channel_id": cid, "channel": chan(cid, subs),
              "metrics": metrics.channel_metrics(chan(cid, subs), vids, 0, 0),
              "top_videos": []}
             for cid, subs in (("A", 1_350_000), ("B", 900_000), ("C", 2_500_000))]
    rel = {"A": {"product_fit": "natural",
                 "product_moments": ["A College Week In My Life"],
                 "brand_fit": {"tone": "match", "price_tier": "match", "values": "match"},
                 "brand_evidence": "campus routines", "reason": "college vlogs"}}
    aud = {"A": {"buyer_overlap": "good", "evidence": ["exam tomorrow"],
                 "inferred_viewer_profile": "Urban college students"}}
    ranked, _, _ = metrics.score_candidates(cands, rel, aud, {}, {}, set(), "macro", set())
    return next(r for r in ranked if r["channel_id"] == "A")


def test_key_numbers_are_readable_and_explained():
    row = _scored_row()
    kn = explain.key_numbers(row)
    labels = [k["label"] for k in kn]
    assert labels[:3] == ["Audience", "Product fit", "Brand fit"]
    assert {"Subscribers", "Typical views", "Engagement"} <= set(labels)
    for k in kn:
        assert not LONG_FLOAT.search(k["value"]), k
    subs = next(k for k in kn if k["label"] == "Subscribers")
    assert subs["value"] == "1.4M" and "macro" in subs["note"]


def test_summary_always_says_why_without_raw_floats():
    row = _scored_row()
    s = explain.summary(row)
    assert s["headline"] and s["rationale"].startswith("Picked because")
    assert "College Week" in s["rationale"]
    assert not LONG_FLOAT.search(s["rationale"])
    facts = explain.facts(row, 2, 10)
    assert facts["rank"] == "2 of 10"
    assert not LONG_FLOAT.search(repr(facts["key_numbers"]))


def test_broader_picks_say_so():
    row = {**_scored_row(), "tier": "broader"}
    assert "broader pick" in explain.summary(row)["caveat"].lower()


# ---------------------------------------------------------------- graph

class CountingYT(FakeYT):
    searches = 0
    wide_charts = 0

    async def search_videos(self, *a, **kw):
        CountingYT.searches += 1
        return await super().search_videos(*a, **kw)

    async def trending_pool(self, rc, cat=None, pages=2):
        if pages == graph.WIDEN_TRENDING_PAGES:
            CountingYT.wide_charts += 1
        return await super().trending_pool(rc, cat, pages)


@pytest.mark.asyncio
async def test_too_few_matches_widens_once_without_extra_searches(monkeypatch):
    import youtube
    monkeypatch.setattr(youtube, "YouTubeClient", CountingYT)
    CountingYT.searches = CountingYT.wide_charts = 0
    # Study only 5 on the first pass so the rest wait in the reserve.
    monkeypatch.setattr(graph, "MAX_CANDIDATES", 5)
    monkeypatch.setattr(graph, "MIN_SCREENED", 1)
    st = await run()
    assert st.get("fatal") is None
    assert st.get("widen_round") == 1
    assert any("widened one level up" in n for n in st["notices"])
    assert CountingYT.wide_charts >= 1
    assert CountingYT.searches <= graph.MAX_SEARCHES_PER_RUN   # widening spends none
    studied = {c["channel_id"] for c in st["studied"]}
    assert len(studied) > 5                                     # the reserve was studied
    # First-pass judgments survive the second pass.
    first = [c for c in CH[:12] if c in studied]
    assert all(c in st["relevance"] for c in first if c != CH[11])


@pytest.mark.asyncio
async def test_enough_matches_skips_widening(monkeypatch):
    monkeypatch.setattr(metrics, "SHORTLIST_TARGET", 1)
    st = await run()
    assert not st.get("widen_round")
    assert not any("widened" in n for n in st["notices"])


class SparseYT(FakeYT):
    """Creator 0 is found by search but has no uploads: only the one video
    search surfaced, too few to measure."""
    async def recent_uploads(self, pl, mr=20):
        return [] if pl == "UU" + "0" * 22 else await super().recent_uploads(pl, mr)

    async def search_videos(self, *a, **kw):
        out = await super().search_videos(*a, **kw)
        # Keep creator 0 in the pool, but with a single video to its name.
        return [o for o in out if o["video_id"] != "vid0_r"]


@pytest.mark.asyncio
async def test_unmeasurable_creator_is_replaced_before_judging(monkeypatch):
    import youtube
    monkeypatch.setattr(youtube, "YouTubeClient", SparseYT)
    monkeypatch.setattr(graph, "MAX_CANDIDATES", 6)
    monkeypatch.setattr(graph, "WIDEN_ENABLED", False)
    seen_by_judges: set[str] = set()
    real_call = FakeGemini.call

    async def spy(self, name, system, payload, schema, temp, cache_key=None):
        if name == "relevance_judge":
            seen_by_judges.update(re.findall(r'"channel_id":\s*"(UC\d{22})"', payload))
        return await real_call(self, name, system, payload, schema, temp, cache_key)
    monkeypatch.setattr(FakeGemini, "call", spy)

    st = await run()
    ruled_out = {e["channel_id"] for e in st["excluded"]
                 if "insufficient data" in e.get("exclusion", "")}
    assert CH[0] in ruled_out, "creator 0 should be ruled out on data"
    assert not (ruled_out & seen_by_judges), "judges were paid to read a ruled-out creator"
    assert any("took" in n and "place" in n for n in st["notices"])


def test_research_sources_drop_youtube_and_search_plumbing():
    from api.main import _clean_sources
    got = _clean_sources(["blinkit.com", "youtube.com", "https://www.afaqs.com/x",
                          "vertexaisearch.cloud.google.com", "blinkit.com"])
    assert got == ["blinkit.com", "afaqs.com"]
