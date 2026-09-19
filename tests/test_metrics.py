"""Scoring math, including the §12 sanity check."""
from datetime import datetime, timedelta, timezone

import metrics


def ts(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def vid(i, views, likes=None, comments=None, dur=600, days=30, live="none"):
    st = {}
    if views is not None:
        st["viewCount"] = str(views)
    if likes is not None:
        st["likeCount"] = str(likes)
    if comments is not None:
        st["commentCount"] = str(comments)
    return {
        "id": i,
        "snippet": {"publishedAt": ts(days), "liveBroadcastContent": live, "title": f"t{i}"},
        "statistics": st,
        "_duration_s": dur,
    }


def chan(cid, subs, hidden=False, age=1000, kids=False):
    st = {"videoCount": "100", "viewCount": "999"}
    if hidden:
        st["hiddenSubscriberCount"] = True
    else:
        st["subscriberCount"] = str(subs)
    return {
        "id": cid,
        "snippet": {"title": cid, "publishedAt": ts(age), "description": "d"},
        "statistics": st,
        "status": {"madeForKids": kids},
    }


def test_median_not_mean():
    """10k,12k,11k,9k,2.4M -> mean ~488k, median 11k. The median is honest."""
    vs = [vid(f"v{i}", x) for i, x in enumerate([10000, 12000, 11000, 9000, 2400000])]
    assert metrics.channel_metrics(chan("A", 100000), vs, 0, 5)["median_views"] == 11000.0


def test_shorts_live_and_young_videos_excluded():
    mix = [
        vid("short", 99999, dur=40),      # Shorts corrupt every ratio
        vid("live", 5000, live="live"),
        vid("young", 5000, days=2),       # views still climbing
        vid("ok", 5000, dur=600, days=30),
    ]
    assert len(metrics.eligible_videos(mix)) == 1


def test_disabled_likes_yield_no_signal_not_zero():
    vs = [vid("a", 1000), vid("b", 1100)]
    assert metrics.channel_metrics(chan("N", 5000), vs, 0, 2)["engagement_rate"] is None


def test_ratios_beat_raw_counts():
    """§12: if the list is just the biggest channels, the scoring is broken.

    Both channels clear the reach floors, so this isolates the ratio logic.
    """
    small, big = chan("SMALL", 400_000), chan("BIG", 10_000_000)
    sv = [vid(f"s{i}", 300000, likes=15000, comments=1500, days=10 + i * 5) for i in range(8)]
    bv = [vid(f"b{i}", 200000, likes=1000, comments=50, days=10 + i * 5) for i in range(8)]
    cands = [
        {"channel_id": "SMALL", "channel": small,
         "metrics": metrics.channel_metrics(small, sv, 2, 8), "top_videos": []},
        {"channel_id": "BIG", "channel": big,
         "metrics": metrics.channel_metrics(big, bv, 0, 8), "top_videos": []},
    ]
    rel = {"SMALL": {"relevance": 0.8}, "BIG": {"relevance": 0.8}}
    aud = {"SMALL": {"audience_fit": 0.7}, "BIG": {"audience_fit": 0.7}}
    ranked, _, _ = metrics.score_candidates(cands, rel, aud, {}, {}, set(), "mid", set())
    assert ranked[0]["title"] == "SMALL"


def test_all_stage_b_agents_down_still_ranks():
    ch = chan("C", 400_000)
    vs = [vid(f"v{i}", 300000, likes=15000, comments=1000, days=10 + i * 5) for i in range(8)]
    cands = [{"channel_id": "C", "channel": ch,
              "metrics": metrics.channel_metrics(ch, vs, 1, 8), "top_videos": []}]
    ranked, _, _ = metrics.score_candidates(
        cands, {}, {}, {}, {}, set(), "mid",
        {"relevance_judge", "audience_analyst", "safety_auditor", "sponsorship_analyst"},
    )
    assert len(ranked) == 1 and ranked[0]["partial"] is True


def test_hard_exclusions():
    sv = [vid(f"s{i}", 40000, likes=100, days=10 + i * 5) for i in range(8)]
    stale = [vid(f"z{i}", 5000, days=300) for i in range(6)]
    cands = [
        {"channel_id": "KID", "channel": chan("KID", 50000, kids=True),
         "metrics": metrics.channel_metrics(chan("KID", 50000, kids=True), sv, 0, 8)},
        {"channel_id": "NEW", "channel": chan("NEW", 50000, age=10),
         "metrics": metrics.channel_metrics(chan("NEW", 50000, age=10), sv, 0, 8)},
        {"channel_id": "STALE", "channel": chan("STALE", 50000),
         "metrics": metrics.channel_metrics(chan("STALE", 50000), stale, 0, 6)},
    ]
    _, _, excluded = metrics.score_candidates(cands, {}, {}, {}, {}, set(), "micro", set())
    reasons = {e["channel_id"]: e["exclusion"] for e in excluded}
    assert "kids" in reasons["KID"]
    assert "90 days" in reasons["NEW"]
    assert "180 days" in reasons["STALE"]


def test_normalize_is_relative_and_handles_singletons():
    assert metrics.normalize({"a": 5.0})["a"] == 0.5      # single candidate -> neutral
    assert metrics.normalize({"a": 1.0, "b": 1.0})["a"] == 0.5  # all-equal -> neutral
    n = metrics.normalize({"a": 0.0, "b": 10.0, "c": None})
    assert n["a"] == 0.0 and n["b"] == 1.0 and n["c"] is None


def _cand(cid, subs, views, hidden=False):
    ch = chan(cid, subs, hidden=hidden)
    vs = [vid(f"{cid}{i}", views, likes=int(views * 0.04), comments=100,
              days=10 + i * 5) for i in range(8)]
    return {"channel_id": cid, "channel": ch,
            "metrics": metrics.channel_metrics(ch, vs, 1, 8), "top_videos": []}


def test_reach_floors_exclude_small_channels():
    """A creator below the floors cannot deliver campaign reach, so they are
    excluded outright rather than ranked low."""
    cands = [
        _cand("BIGENOUGH", 500_000, 250_000),
        _cand("FEWSUBS", 50_000, 250_000),        # subs below the floor
        _cand("FEWVIEWS", 500_000, 20_000),       # views below the floor
    ]
    ranked, _, excluded = metrics.score_candidates(
        cands, {}, {}, {}, {}, set(), "mid", set())

    assert [r["channel_id"] for r in ranked] == ["BIGENOUGH"]
    reasons = {e["channel_id"]: e["exclusion"] for e in excluded}
    assert "subscribers" in reasons["FEWSUBS"]
    assert "median views" in reasons["FEWVIEWS"]


def test_hidden_subscribers_are_judged_on_views_alone():
    """Hiding the subscriber count is a setting, not a signal. Such a channel
    must not be dropped for a number it simply did not publish."""
    cands = [_cand("HIDDEN", 0, 250_000, hidden=True)]
    ranked, _, excluded = metrics.score_candidates(
        cands, {}, {}, {}, {}, set(), "mid", set())
    assert [r["channel_id"] for r in ranked] == ["HIDDEN"], \
        f"hidden-subscriber channel was wrongly excluded: {excluded}"


def _row(cid, score, conf="high"):
    return {"channel_id": cid, "fit_score": score, "confidence": conf}


def test_shortlist_is_a_ceiling_not_a_target():
    """Fifteen is the maximum, never something to pad toward."""
    rows = [_row(f"c{i}", 90 - i) for i in range(30)]
    shown, held = metrics.shortlist(rows)
    assert len(shown) <= metrics.SHORTLIST_MAX
    assert held["held_back"] == len(rows) - len(shown)


def test_weak_creators_are_held_back_with_a_reason():
    rows = [_row("a", 88), _row("b", 72), _row("c", 61),
            _row("d", 30), _row("e", 22)]
    shown, held = metrics.shortlist(rows)
    assert [r["channel_id"] for r in shown] == ["a", "b", "c"]
    assert held["held_back"] == 2
    assert "too low" in held["note"]


def test_a_genuinely_strong_set_is_all_shown():
    rows = [_row(f"c{i}", 80 - i) for i in range(10)]
    shown, held = metrics.shortlist(rows)
    assert len(shown) == 10
    assert held["held_back"] == 0
    assert held["note"] == ""


def test_never_returns_empty_when_candidates_exist():
    """A thin result must still show the best of what was found."""
    rows = [_row("a", 33, "low"), _row("b", 28, "low")]
    shown, _ = metrics.shortlist(rows)
    assert len(shown) > 0, "returned nothing despite having candidates"


def _small(cid, subs, views, kids=False, age=1000):
    ch = chan(cid, subs, kids=kids, age=age)
    vs = [vid(f"{cid}{i}", views, likes=int(views * 0.05), comments=200,
              days=10 + i * 5) for i in range(8)]
    return {"channel_id": cid, "channel": ch,
            "metrics": metrics.channel_metrics(ch, vs, 1, 8), "top_videos": []}


def test_empty_result_falls_back_to_the_closest_candidates():
    """A blank page teaches the user nothing. Show the near-misses instead."""
    cands = [_small("A", 60_000, 40_000), _small("B", 45_000, 30_000),
             _small("C", 80_000, 55_000), _small("D", 20_000, 9_000)]
    ranked, _, _, note = metrics.score_with_fallback(
        cands, {}, {}, {}, {}, set(), "mid", set())

    assert 0 < len(ranked) <= 3
    assert all(r.get("below_floor") for r in ranked)
    assert note.get("below_floor") is True
    assert "closest" in note["note"]
    # Fully scored rows, not stubs: the cards need these to render.
    assert all("breakdown" in r and "metrics" in r for r in ranked)


def test_fallback_never_resurrects_principled_exclusions():
    """Made-for-kids and dormant channels stay out. Relaxing those would be
    dishonest rather than merely lenient."""
    cands = [_small("KIDS", 50_000, 40_000, kids=True),
             _small("NEW", 50_000, 40_000, age=10)]
    ranked, _, _, note = metrics.score_with_fallback(
        cands, {}, {}, {}, {}, set(), "mid", set())
    assert ranked == []
    assert note == {}


def test_fallback_does_not_fire_when_real_results_exist():
    cands = [_small("BIG", 500_000, 250_000), _small("TINY", 10_000, 5_000)]
    ranked, _, _, note = metrics.score_with_fallback(
        cands, {}, {}, {}, {}, set(), "mid", set())
    assert [r["channel_id"] for r in ranked] == ["BIG"]
    assert note == {}
    assert not ranked[0].get("below_floor")


def test_reach_floors_are_restored_after_a_fallback():
    """The relaxed pass must not leak its thresholds into later searches."""
    before = (metrics.MIN_SUBSCRIBERS, metrics.MIN_MEDIAN_VIEWS)
    metrics.score_with_fallback([_small("A", 60_000, 40_000)],
                                {}, {}, {}, {}, set(), "mid", set())
    assert (metrics.MIN_SUBSCRIBERS, metrics.MIN_MEDIAN_VIEWS) == before


# ---------------------------------------------------------------- brief constraints
# The shortlist that prompted these: a "mid-tier, India" brief returned 9.8M and
# 20.8M-subscriber channels, and creators outside the market, because size_fit
# carried 0.02 of the score and geography carried none at all.

def test_size_fit_punishes_a_far_miss():
    """A macro channel against a mid brief is a wrong answer, not a near one."""
    assert metrics.size_fit(300_000, "mid") == 1.0
    assert metrics.size_fit(20_800_000, "mid") < 0.2
    # Two bands out must score clearly below one band out.
    assert metrics.size_fit(20_000_000, "micro") < metrics.size_fit(500_000, "micro")


def test_band_distance_counts_bands():
    assert metrics.band_distance(300_000, "mid") == 0
    assert metrics.band_distance(2_000_000, "mid") == 1      # adjacent, allowed
    assert metrics.band_distance(20_000_000, "mid") == 2     # far, excluded
    assert metrics.band_distance(20_000_000, "micro") == 3   # further still


def test_geo_fit_treats_unknown_country_as_no_signal():
    """Absence of a country is missing evidence, never a wrong answer (§4)."""
    assert metrics.geo_fit("IN", "IN") == 1.0
    assert metrics.geo_fit("US", "IN") == 0.0
    assert metrics.geo_fit(None, "IN") is None    # dropped term, not a penalty
    assert metrics.geo_fit("IN", None) is None


def _live_candidate(cid, subs, country=None):
    """A candidate that clears every floor, so only the band rule can drop it."""
    ch = chan(cid, subs)
    ch["snippet"]["country"] = country
    vids = [vid(f"{cid}-{i}", 500_000, likes=20_000, comments=500, days=30 + i)
            for i in range(8)]
    return {
        "channel_id": cid,
        "channel": ch,
        "metrics": metrics.channel_metrics(ch, vids, 0, 8),
        "videos": vids,
        "paid_video_ids": [],
        "top_videos": [],
    }


def test_far_out_of_band_creator_is_excluded_not_ranked():
    """The 20.8M channel on a mid-tier brief must not reach the shortlist."""
    cands = [_live_candidate("in_band", 400_000), _live_candidate("mega", 20_800_000)]
    ranked, _, excluded = metrics.score_candidates(
        cands, {}, {}, {}, {}, set(), "mid", set(),
    )
    assert [r["channel_id"] for r in ranked] == ["in_band"]
    assert any(e["channel_id"] == "mega" and "size band" in e["exclusion"]
               for e in excluded)


def test_adjacent_band_is_kept_but_ranked_below():
    """One band out is a judgment call, so it is scored down, not dropped."""
    cands = [_live_candidate("mid", 400_000), _live_candidate("macro", 2_000_000)]
    ranked, _, _ = metrics.score_candidates(
        cands, {}, {}, {}, {}, set(), "mid", set(),
    )
    assert {r["channel_id"] for r in ranked} == {"mid", "macro"}
    assert ranked[0]["channel_id"] == "mid"


def test_wrong_market_ranks_below_right_market():
    cands = [_live_candidate("local", 400_000, country="IN"),
             _live_candidate("foreign", 400_000, country="US")]
    ranked, _, _ = metrics.score_candidates(
        cands, {}, {}, {}, {}, set(), "mid", set(), region_code="IN",
    )
    assert ranked[0]["channel_id"] == "local"


def test_unstated_country_is_not_penalised_against_a_match():
    """A blank country must not be scored as if it were the wrong country."""
    cands = [_live_candidate("blank", 400_000, country=None),
             _live_candidate("foreign", 400_000, country="US")]
    ranked, _, _ = metrics.score_candidates(
        cands, {}, {}, {}, {}, set(), "mid", set(), region_code="IN",
    )
    by = {r["channel_id"]: r for r in ranked}
    assert by["blank"]["fit_score"] > by["foreign"]["fit_score"]
    assert "geo_fit" in by["blank"]["dropped_terms"]


def test_band_exclusion_never_produces_a_blank_page():
    """The band rule must not turn a thin result into an empty one, and the
    explanation must name the bar that was actually missed."""
    cands = [_live_candidate("mega1", 15_000_000), _live_candidate("mega2", 30_000_000)]
    rows, _, _, note = metrics.score_with_fallback(
        cands, {}, {}, {}, {}, set(), "mid", set(),
    )
    assert len(rows) == 2                      # shown, not silently dropped
    assert all(r.get("below_floor") for r in rows)
    assert "size band" in note["note"]         # the real reason, not the floors
    assert "median views" not in note["note"]
