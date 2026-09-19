"""Scoring math. No agent produces the final number.

LLM numeric scores drift between runs and cannot be audited. Agents emit
bounded judgments; Python does the arithmetic. Every rank is explainable by
pointing at WEIGHTS below.

Agent terms sum to 0.42, code terms to 0.58 — deliberate. Most of the score
rests on measured numbers.
"""
from __future__ import annotations

import os
import statistics
from datetime import datetime, timezone
from typing import Any, Optional

# Videos shorter than this are Shorts: a 40s Short cannot host a 90s host-read,
# and Shorts' inflated views corrupt every ratio.
SHORTS_MAX_S = 180
# Views are still climbing in the first week — excluded from the median.
MIN_AGE_DAYS = 7
# Hard exclusions
MAX_STALE_DAYS = 180
MIN_CHANNEL_AGE_DAYS = 90

# Reach floors. A creator below these cannot deliver meaningful campaign reach,
# so they are excluded outright rather than ranked low: a shortlist padded with
# tiny channels wastes the reader's attention.
#
# Median views is the stricter test and the more honest one. Subscriber counts
# are vanity and often stale, while median views is what a placement actually
# reaches. A channel with a hidden subscriber count is judged on views alone
# rather than dropped, since hiding subscribers is a setting, not a signal.
MIN_SUBSCRIBERS = int(os.environ.get("UPTICK_MIN_SUBS", "100000"))
MIN_MEDIAN_VIEWS = int(os.environ.get("UPTICK_MIN_VIEWS", "100000"))

# 15 is a CEILING, never a target. A shortlist is only worth the reader's time
# if every name on it is defensible, so a creator is shown only when the
# evidence supports recommending them. Returning four good names and saying so
# is more useful than fifteen names where eleven are padding.
SHORTLIST_MAX = 15
# Below this the score is not a recommendation, it is a guess.
RECOMMEND_MIN_SCORE = float(os.environ.get("UPTICK_MIN_SCORE", "45"))


def _relax(excluded: list[dict], limit: int = 3) -> list[dict]:
    """The closest near-misses, for when nothing cleared the reach floors.

    Only creators held back for SIZE are eligible. A channel excluded for being
    made-for-kids, dormant, or too new was excluded on principle, and relaxing
    those would be dishonest rather than merely lenient.
    """
    SIZE_REASONS = ("subscribers", "median views", "size band")
    near = [
        e for e in excluded
        if any(r in e.get("exclusion", "") for r in SIZE_REASONS)
        and e.get("metrics", {}).get("median_views") is not None
    ]
    near.sort(key=lambda e: e["metrics"]["median_views"] or 0, reverse=True)
    return near[:limit]


def shortlist(ranked: list[dict]) -> tuple[list[dict], dict]:
    """Cut the ranked list where the evidence stops supporting a recommendation.

    Returns (shown, reason) where reason explains any creators held back, so
    the interface can say why the list is short instead of looking broken.
    """
    strong = [r for r in ranked
              if r["fit_score"] >= RECOMMEND_MIN_SCORE
              and r["confidence"] != "low"]

    # Keep a low-confidence creator only when it scores well and we would
    # otherwise have almost nothing to show.
    if len(strong) < 3:
        extra = [r for r in ranked
                 if r not in strong and r["fit_score"] >= RECOMMEND_MIN_SCORE]
        strong = (strong + extra)[:3] or ranked[:3]

    strong.sort(key=lambda r: r["fit_score"], reverse=True)
    shown = strong[:SHORTLIST_MAX]
    held = len(ranked) - len(shown)

    reason = {"held_back": held, "note": ""}
    if held > 0:
        weak = sum(1 for r in ranked
                   if r not in shown and r["fit_score"] < RECOMMEND_MIN_SCORE)
        thin = held - weak
        bits = []
        if weak:
            bits.append(f"{weak} scored too low to recommend")
        if thin:
            bits.append(f"{thin} had too little evidence to judge confidently")
        reason["note"] = (
            f"{held} other creator{'s' if held != 1 else ''} were found but not "
            f"shown: " + " and ".join(bits) + "."
        )
    return shown, reason

SIZE_BANDS = {
    "nano": (0, 10_000),
    "micro": (10_000, 100_000),
    "mid": (100_000, 1_000_000),
    "macro": (1_000_000, 10_000_000),
    # Split out of macro deliberately. With macro running to infinity, a 20M
    # channel and a 1.5M channel were the same distance from a mid-tier brief,
    # so the ten-million-subscriber outliers that prompted this split were
    # treated as a near miss and ranked.
    "mega": (10_000_000, float("inf")),
}
_BAND_ORDER = ["nano", "micro", "mid", "macro", "mega"]

WEIGHTS = {
    "relevance": 0.22,          # Agent 3
    "audience_fit": 0.09,       # Agent 4
    "engagement_rate": 0.15,    # code
    "view_per_sub": 0.11,       # code
    "sponsor_ratio": 0.10,      # code, from the paid-placement search
    "sponsor_confidence": 0.06, # Agent 6
    "consistency": 0.08,        # code
    "activity": 0.03,           # code
    "size_fit": 0.10,           # code — the brief asked for a size, honour it
    "geo_fit": 0.06,            # code — the brief asked for a market
}
TRENDING_BONUS = 5.0

# Cultural standing is a BONUS, never a weighted term.
#
# Most creators have no press coverage, and that says nothing bad about them —
# it usually just means they are a working YouTuber rather than a public
# figure. As a weighted term, "no evidence found" would score 0 and actively
# push them down the ranking, which would be a false negative at scale. As a
# bonus it can only lift the genuinely culturally prominent, which is exactly
# what it is for. Trending (a single day) is worth 5; sustained fame is worth
# more.
CULTURAL_BONUS_MAX = 12.0
FAME_MULTIPLIER = {
    "household_name": 1.0,
    "scene_famous": 0.8,
    "niche_known": 0.4,
    "unknown": 0.0,
}
# A one-week viral spike is not standing. Discount it heavily.
DURABILITY = {
    "sustained": 1.0,
    "rising": 0.8,
    "spike": 0.35,
    "fading": 0.4,
    "unknown": 0.5,
}


def cultural_bonus(row: dict | None, target_gen: str | None = None) -> tuple[float, str]:
    """Points to add for prominence outside YouTube, plus a short reason.

    Returns (0.0, "") when nothing was found — absence of press coverage is
    absence of evidence, never evidence against the creator.
    """
    if not row or not row.get("evidence_found"):
        return 0.0, ""
    rel = row.get("cultural_relevance")
    try:
        rel = float(rel)
    except (TypeError, ValueError):
        return 0.0, ""
    rel = max(0.0, min(1.0, rel))

    fame = FAME_MULTIPLIER.get(row.get("fame_tier"), 0.0)
    dur = DURABILITY.get(row.get("sustained_or_spike"), 0.5)
    bonus = CULTURAL_BONUS_MAX * rel * fame * dur

    # Extra credit when the creator's audience generation matches the brief.
    gen = row.get("audience_generation")
    if target_gen and gen and gen == target_gen:
        bonus *= 1.15

    bonus = round(min(CULTURAL_BONUS_MAX, bonus), 2)
    if bonus <= 0:
        return 0.0, ""
    tier = str(row.get("fame_tier", "")).replace("_", " ")
    dur_word = str(row.get("sustained_or_spike", "")).replace("_", " ")
    return bonus, f"{tier}, {dur_word}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(s: str | None) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_days(s: str | None) -> Optional[float]:
    t = parse_ts(s)
    return None if t is None else (_now() - t).total_seconds() / 86400


def _int(d: dict, key: str) -> Optional[int]:
    """Absent stat fields mean 'disabled/hidden', never zero."""
    v = d.get(key)
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def eligible_videos(videos: list[dict]) -> list[dict]:
    """Drop Shorts, live/premieres, and videos too young to have settled."""
    out = []
    for v in videos:
        snip = v.get("snippet", {})
        if snip.get("liveBroadcastContent", "none") != "none":
            continue
        if (v.get("_duration_s") or 0) < SHORTS_MAX_S:
            continue
        a = age_days(snip.get("publishedAt"))
        if a is None or a < MIN_AGE_DAYS:
            continue
        out.append(v)
    return out


def channel_metrics(
    channel: dict, videos: list[dict], paid_hits: int, videos_surfaced: int
) -> dict:
    """Per-channel raw metrics. Median, never mean.

    Videos at 10k, 12k, 11k, 9k, 2.4M give a mean of ~488k and a median of 11k.
    The median is the honest expected reach.
    """
    stats = channel.get("statistics", {})
    hidden = bool(stats.get("hiddenSubscriberCount"))
    subs = None if hidden else _int(stats, "subscriberCount")
    # A hidden sub count must never become 0 — the division blows up and the
    # channel silently ranks first.
    if subs is not None and subs <= 0:
        subs = None

    elig = eligible_videos(videos)
    views = [_int(v.get("statistics", {}), "viewCount") for v in elig]
    views = [x for x in views if x is not None]

    median_views = float(statistics.median(views)) if views else None

    rates = []
    for v in elig:
        st = v.get("statistics", {})
        vc = _int(st, "viewCount")
        if not vc:
            continue
        likes = _int(st, "likeCount")
        comments = _int(st, "commentCount")
        if likes is None and comments is None:
            continue  # both disabled — no signal, not a zero
        rates.append(((likes or 0) + (comments or 0)) / vc)
    engagement_rate = float(statistics.median(rates)) if rates else None

    view_per_sub = (median_views / subs) if (median_views is not None and subs) else None

    consistency = None
    if len(views) >= 3:
        mean = statistics.mean(views)
        if mean > 0:
            consistency = 1.0 - min(1.0, statistics.stdev(views) / mean)

    sponsor_ratio = (paid_hits / videos_surfaced) if videos_surfaced else 0.0
    sponsor_ratio = max(0.0, min(1.0, sponsor_ratio))

    last_upload_days = None
    pub_dates = [age_days(v.get("snippet", {}).get("publishedAt")) for v in videos]
    pub_dates = [d for d in pub_dates if d is not None]
    if pub_dates:
        last_upload_days = min(pub_dates)

    if last_upload_days is None:
        activity = 0.0
    elif last_upload_days <= 30:
        activity = 1.0
    elif last_upload_days <= 90:
        activity = 0.5
    else:
        activity = 0.0

    return {
        "subscriber_count": subs,
        "subscribers_hidden": hidden,
        "median_views": median_views,
        "engagement_rate": engagement_rate,
        "view_per_sub": view_per_sub,
        "consistency": consistency,
        "sponsor_ratio": sponsor_ratio,
        "activity": activity,
        "paid_placement_hits": paid_hits,
        "videos_surfaced": videos_surfaced,
        "eligible_video_count": len(elig),
        "last_upload_days": last_upload_days,
        "channel_age_days": age_days(channel.get("snippet", {}).get("publishedAt")),
        "likes_visible": any(
            _int(v.get("statistics", {}), "likeCount") is not None for v in elig
        ),
    }


def size_fit(subs: Optional[int], band: str) -> float:
    """1.0 in-band, falling off by how many bands away the creator sits.

    The brief names a size for a budget reason: a mid-tier campaign cannot
    afford a 20M-subscriber channel, so surfacing one is not a near miss, it
    is the wrong answer. Distance is measured in BANDS, and the far end of the
    scale is near zero rather than the old 0.2, which a strong engagement score
    could trivially outrun.
    """
    if subs is None or band not in SIZE_BANDS:
        return 0.5
    lo, hi = SIZE_BANDS[band]
    if lo <= subs < hi:
        return 1.0
    actual = next(
        (b for b in _BAND_ORDER if SIZE_BANDS[b][0] <= subs < SIZE_BANDS[b][1]),
        None,
    )
    if actual is None:
        return 0.05
    return {1: 0.45, 2: 0.12}.get(
        abs(_BAND_ORDER.index(actual) - _BAND_ORDER.index(band)), 0.05
    )


# How far outside the requested band a creator may sit before they are dropped
# from the ranking outright rather than merely scored down. One band of slack
# is deliberate: band edges are round numbers, and a 1.1M-subscriber channel
# against a "mid" brief is a judgment call, not an error. Two bands out is not.
MAX_BAND_DISTANCE = int(os.environ.get("UPTICK_MAX_BAND_DISTANCE", "1"))


def band_distance(subs: Optional[int], band: str) -> int:
    """How many size bands separate this creator from the requested one."""
    if subs is None or band not in SIZE_BANDS:
        return 0
    lo, hi = SIZE_BANDS[band]
    if lo <= subs < hi:
        return 0
    actual = next(
        (b for b in _BAND_ORDER if SIZE_BANDS[b][0] <= subs < SIZE_BANDS[b][1]),
        None,
    )
    if actual is None:
        return 0
    return abs(_BAND_ORDER.index(actual) - _BAND_ORDER.index(band))


def geo_fit(country: Optional[str], region_code: Optional[str]) -> Optional[float]:
    """Does this creator sit in the market the brief asked for?

    Returns None — a dropped term, not a penalty — when either side is unknown.
    A channel's country field is optional on YouTube and plenty of legitimate
    creators leave it blank, so absence must not read as a wrong answer
    (AGENT_GUIDELINES §4). A stated mismatch, though, is real evidence: the
    brief asked for one market and this creator publishes from another.
    """
    if not region_code or not country:
        return None
    return 1.0 if country.upper() == region_code.upper() else 0.0


def confidence(m: dict) -> str:
    """high >=6 videos + subs visible + likes visible; medium >=4; low >=2."""
    n = m["eligible_video_count"]
    if n >= 6 and not m["subscribers_hidden"] and m["likes_visible"]:
        return "high"
    if n >= 4:
        return "medium"
    if n >= 2:
        return "low"
    return "insufficient"


def normalize(values: dict[str, Optional[float]]) -> dict[str, Optional[float]]:
    """Min-max within the CURRENT candidate set, not absolute thresholds.

    Engagement norms differ wildly between gaming and finance; only relative
    comparison is meaningful. A single candidate, or an all-equal set, scores
    0.5 — neutral rather than an arbitrary 0 or 1.
    """
    present = {k: v for k, v in values.items() if v is not None}
    if not present:
        return {k: None for k in values}
    lo, hi = min(present.values()), max(present.values())
    if hi - lo < 1e-12:
        return {k: (0.5 if v is not None else None) for k, v in values.items()}
    return {
        k: ((v - lo) / (hi - lo) if v is not None else None)
        for k, v in values.items()
    }


def score_candidates(
    candidates: list[dict],
    relevance: dict[str, dict],
    audience: dict[str, dict],
    safety: dict[str, dict],
    sponsorship: dict[str, dict],
    trending: set[str],
    size_band: str,
    failed_agents: set[str],
    cultural: dict[str, dict] | None = None,
    target_generation: str | None = None,
    region_code: str | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (ranked, review_manually, excluded).

    Any missing term — a failed agent or an unmeasurable metric — has its
    weight dropped and the remainder renormalized to 1.0. The query never
    fails; it degrades and is marked partial.
    """
    excluded: list[dict] = []
    live: list[dict] = []

    # ---- hard exclusions, before scoring
    for c in candidates:
        ch, m = c["channel"], c["metrics"]
        if ch.get("status", {}).get("madeForKids"):
            excluded.append({**c, "exclusion": "made for kids (COPPA ad restrictions)"})
            continue
        if m["channel_age_days"] is not None and m["channel_age_days"] < MIN_CHANNEL_AGE_DAYS:
            excluded.append({**c, "exclusion": "channel less than 90 days old"})
            continue
        if m["last_upload_days"] is None or m["last_upload_days"] > MAX_STALE_DAYS:
            excluded.append({**c, "exclusion": "no upload in the last 180 days"})
            continue
        if confidence(m) == "insufficient":
            excluded.append({**c, "exclusion": "insufficient data (under 2 usable videos)"})
            continue

        # Reach floors, applied after the data-quality checks so the reason
        # given is the most specific one.
        subs = m["subscriber_count"]
        if subs is not None and subs < MIN_SUBSCRIBERS:
            excluded.append({
                **c,
                "exclusion": f"under {MIN_SUBSCRIBERS:,} subscribers "
                             f"({subs:,})",
            })
            continue
        mv = m["median_views"]
        if mv is None or mv < MIN_MEDIAN_VIEWS:
            excluded.append({
                **c,
                "exclusion": (f"median views below {MIN_MEDIAN_VIEWS:,}"
                              + (f" ({int(mv):,})" if mv is not None else "")),
            })
            continue

        # Size band, as a hard rule rather than a scoring nudge.
        #
        # A brief that says "mid-tier" is usually saying "this is the budget".
        # A 20M-subscriber channel is not an imperfect answer to that, it is
        # an unaffordable one, and letting a strong engagement score outweigh
        # it is how a shortlist ends up recommending creators nobody can book.
        dist = band_distance(m["subscriber_count"], size_band)
        if dist > MAX_BAND_DISTANCE:
            subs_txt = (f"{m['subscriber_count']:,}"
                        if m["subscriber_count"] is not None else "unknown")
            excluded.append({
                **c,
                "exclusion": f"far outside the requested {size_band}-tier size "
                             f"band ({subs_txt} subscribers)",
            })
            continue
        live.append(c)

    if not live:
        return [], [], excluded

    # ---- min-max normalization across the surviving set
    norm_er = normalize({c["channel_id"]: c["metrics"]["engagement_rate"] for c in live})
    norm_vps = normalize({c["channel_id"]: c["metrics"]["view_per_sub"] for c in live})

    ranked: list[dict] = []
    review: list[dict] = []

    for c in live:
        cid, m = c["channel_id"], c["metrics"]
        rel = relevance.get(cid) or {}
        aud = audience.get(cid) or {}
        saf = safety.get(cid) or {}
        spo = sponsorship.get(cid) or {}

        # A missing row from a live agent is neutral 0.5, not zero — absence of
        # a judgment is not a negative judgment.
        terms: dict[str, Optional[float]] = {
            "relevance": None if "relevance_judge" in failed_agents
                         else (rel.get("relevance") if rel else 0.5),
            "audience_fit": None if "audience_analyst" in failed_agents
                            else (aud.get("audience_fit") if aud else 0.5),
            "engagement_rate": norm_er[cid],
            "view_per_sub": norm_vps[cid],
            "sponsor_ratio": m["sponsor_ratio"],
            "sponsor_confidence": None if "sponsorship_analyst" in failed_agents
                                  else (spo.get("sponsor_confidence") if spo else 0.0),
            "consistency": m["consistency"],
            "activity": m["activity"],
            "size_fit": size_fit(m["subscriber_count"], size_band),
            "geo_fit": geo_fit(
                c["channel"].get("snippet", {}).get("country"), region_code
            ),
        }

        # Drop unusable terms, renormalize the rest to 1.0.
        usable = {k: v for k, v in terms.items() if v is not None}
        total_w = sum(WEIGHTS[k] for k in usable)
        if total_w <= 0:
            continue
        raw = sum(WEIGHTS[k] * usable[k] for k in usable) / total_w

        is_trending = cid in trending
        cul_row = (cultural or {}).get(cid)
        cul_bonus, cul_reason = cultural_bonus(cul_row, target_generation)
        fit = round(
            100.0 * raw
            + (TRENDING_BONUS if is_trending else 0.0)
            + cul_bonus,
            1,
        )
        fit = min(100.0, fit)

        breakdown = [
            {
                "term": k,
                "value": round(usable[k], 4),
                "weight": round(WEIGHTS[k] / total_w, 4),
                "contribution": round(100.0 * WEIGHTS[k] / total_w * usable[k], 2),
            }
            for k in sorted(usable, key=lambda x: -WEIGHTS[x])
        ]

        row = {
            "channel_id": cid,
            "title": c["channel"].get("snippet", {}).get("title", ""),
            "description": c["channel"].get("snippet", {}).get("description", ""),
            "country": c["channel"].get("snippet", {}).get("country"),
            "url": f"https://www.youtube.com/channel/{cid}",
            "fit_score": fit,
            "confidence": confidence(m),
            "partial": bool(failed_agents) or len(usable) < len(WEIGHTS),
            "trending": is_trending,
            "cultural_bonus": cul_bonus,
            "cultural_reason": cul_reason,
            "cultural": cul_row or {},
            "metrics": m,
            "breakdown": breakdown,
            "dropped_terms": [k for k, v in terms.items() if v is None],
            "top_videos": c.get("top_videos", []),
            "agent": {
                "relevance": rel.get("relevance"),
                "relevance_reason": rel.get("reason"),
                "match_type": rel.get("match_type"),
                "audience_fit": aud.get("audience_fit"),
                "inferred_viewer_profile": aud.get("inferred_viewer_profile"),
                "purchase_intent_signal": aud.get("purchase_intent_signal"),
                "audience_reasoning": aud.get("reasoning"),
                "safety_flag": saf.get("flag"),
                "safety_severity": saf.get("severity"),
                "safety_category": saf.get("category"),
                "safety_reason": saf.get("reason"),
                "safety_evidence": saf.get("evidence"),
                "sponsor_confidence": spo.get("sponsor_confidence"),
                "observed_format": spo.get("observed_format"),
                "cadence": spo.get("cadence"),
                "known_sponsor_categories": spo.get("known_sponsor_categories"),
                "sponsor_evidence": spo.get("evidence"),
            },
        }

        # severity high → review manually, never silently dropped
        if saf.get("flag") and saf.get("severity") == "high":
            review.append(row)
        else:
            ranked.append(row)

    ranked.sort(key=lambda r: r["fit_score"], reverse=True)
    review.sort(key=lambda r: r["fit_score"], reverse=True)

    return ranked, review, excluded


def score_with_fallback(*args, **kwargs):
    """score_candidates, but never a blank page.

    If nothing clears the reach floors, the same scoring runs again with the
    floors lifted and the best few near-misses are returned, flagged so the
    interface can say plainly that they are below the bar. A short honest list
    beats an empty one: the user still learns who exists in this space.

    Only the SIZE floors are relaxed. Made-for-kids, dormant and too-new
    channels stay excluded, because those were principled exclusions rather
    than a judgement about reach.
    """
    ranked, review, excluded = score_candidates(*args, **kwargs)
    if ranked or review:
        return ranked, review, excluded, {}

    near = _relax(excluded)
    if not near:
        return ranked, review, excluded, {}

    global MIN_SUBSCRIBERS, MIN_MEDIAN_VIEWS, MAX_BAND_DISTANCE
    keep_s, keep_v = MIN_SUBSCRIBERS, MIN_MEDIAN_VIEWS
    keep_b = MAX_BAND_DISTANCE
    # Which bar actually emptied the list, so the note can say so honestly.
    band_only = all("size band" in e.get("exclusion", "") for e in near)
    MIN_SUBSCRIBERS, MIN_MEDIAN_VIEWS = 0, 0
    MAX_BAND_DISTANCE = len(_BAND_ORDER)
    try:
        relaxed, rev2, _ = score_candidates(*args, **kwargs)
    finally:
        MIN_SUBSCRIBERS, MIN_MEDIAN_VIEWS = keep_s, keep_v
        MAX_BAND_DISTANCE = keep_b

    ids = {n["channel_id"] for n in near}
    rows = [r for r in relaxed if r["channel_id"] in ids][:3]
    for r in rows:
        r["below_floor"] = True

    band = (args[6] if len(args) > 6 else kwargs.get("size_band")) or "requested"
    if band_only:
        detail = (
            f"No creator in this space sits inside the {band}-tier size band. "
            f"These are the closest {len(rows)} we found, and they are larger "
            f"or smaller than you asked for — check the subscriber count before "
            f"budgeting. Widening the size band in your brief would give a "
            f"stronger list."
        )
    else:
        detail = (
            f"No creator in this space cleared the bar of {keep_s:,} "
            f"subscribers and {keep_v:,} median views. These are the closest "
            f"{len(rows)} we found, shown so the search is not a dead end. "
            f"Treat them as leads rather than a shortlist: either this niche is "
            f"small on YouTube, or the brief is narrow enough that few "
            f"creators fit."
        )
    note = {"below_floor": True, "note": detail}
    return rows, rev2, excluded, note
