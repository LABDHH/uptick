"""Fixture results for demo mode.

Lets the full UI render, ranked rows, breakdowns, evidence, banners, the
withheld-rationale and safety paths, without an API key, a network call, or a
single unit of YouTube quota. The numbers are synthetic but internally
consistent: they are run through the real metrics.score_candidates(), so the
scores and renormalization on screen are genuinely computed, not hard-coded.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import graph as graph_mod
import metrics

# Deliberately mixed: the top of this list is NOT the fitness channels, because
# a protein bar's buyers are young people who watch comedy and campus content.
SEED = [
    # title, subs, median views, engagement, paid hits, relevance, audience, safety
    ("Aditi Does Standup",    880_000, 940_000, 0.094, 2, 0.71, 0.91, None),
    ("Hostel Diaries",      1_240_000, 780_000, 0.082, 1, 0.68, 0.89, None),
    ("Lift Lab India",      1_420_000, 610_000, 0.058, 3, 0.92, 0.88, None),
    ("Desi Gains",            780_000, 440_000, 0.071, 2, 0.88, 0.84, None),
    ("The Nutrition Desk",  3_100_000, 520_000, 0.033, 2, 0.86, 0.79, None),
    ("Home Workout Hindi",  1_960_000, 380_000, 0.044, 1, 0.79, 0.72, None),
    ("Strength & Science",    540_000, 290_000, 0.081, 1, 0.83, 0.81, None),
    ("Mumbai Runs",         1_210_000, 220_000, 0.039, 0, 0.68, 0.64, None),
    ("Budget Fitness Bharat", 460_000, 190_000, 0.066, 1, 0.74, 0.77, None),
    ("Calisthenics Clinic", 2_330_000, 310_000, 0.029, 0, 0.71, 0.58, None),
    ("The Macro Method",      920_000, 170_000, 0.052, 1, 0.77, 0.73, None),
    ("Gym Kit Reviews",     1_670_000, 250_000, 0.031, 2, 0.64, 0.51, None),
    ("Yoga Every Morning",  2_880_000, 400_000, 0.036, 0, 0.48, 0.42, None),
    ("Supplement Truth",      610_000, 330_000, 0.062, 2, 0.81, 0.76,
     ("high", "misinformation", "Repeatedly claims specific supplements cure "
      "metabolic disease, which a regulated brand cannot sit beside.")),
    ("Bulk Season",           380_000, 120_000, 0.058, 0, 0.69, 0.66,
     ("medium", "category_conflict", "Recent video is a paid promotion for a "
      "competing whey brand.")),
]

# Standing outside YouTube, what subscriber counts and trending cannot see.
CULTURAL = {
    "Aditi Does Standup": {
        "cultural_relevance": 0.92, "fame_tier": "household_name",
        "persona": "Deadpan observational comic whose crowd work circulates widely",
        "audience_generation": "gen_z", "sustained_or_spike": "sustained",
        "notable_context": "Two national tours and a streaming special in three years",
        "brand_fit_note": "Reaches the exact age band that buys snack bars, with a "
                          "persona that carries a light product mention naturally",
        "evidence_found": True,
    },
    "Hostel Diaries": {
        "cultural_relevance": 0.74, "fame_tier": "scene_famous",
        "persona": "Campus life vlogger with a large student following",
        "audience_generation": "gen_z", "sustained_or_spike": "rising",
        "notable_context": "Regularly covered in student media; large campus events",
        "brand_fit_note": "Viewers are students who snack between classes",
        "evidence_found": True,
    },
    "Supplement Truth": {
        "cultural_relevance": 0.55, "fame_tier": "scene_famous",
        "persona": "Combative myth-busting reviewer",
        "audience_generation": "millennial", "sustained_or_spike": "spike",
        "notable_context": "A recent callout video drove a short traffic surge",
        "brand_fit_note": "Audience is sceptical and already brand-loyal",
        "evidence_found": True,
    },
    "The Nutrition Desk": {
        "cultural_relevance": 0.0, "fame_tier": "unknown", "persona": "",
        "audience_generation": "unclear", "sustained_or_spike": "unknown",
        "notable_context": "", "brand_fit_note": "",
        "evidence_found": False,   # no coverage is NOT a penalty
    },
}

# Keyed by creator name, NOT by rank position: the ranking shifts whenever
# scoring changes, and index keys silently attach prose to the wrong creator.
RATIONALE = {
    "Aditi Does Standup": (
        "Reaches the target age band outside the category",
        "Aditi posts the highest engagement rate in this set at 9.4%, on an 88.0K "
        "subscriber base, and two recent videos carry YouTube's creator-declared "
        "paid-promotion flag. She does not make fitness content at all, which is "
        "the point: her audience is the age band that buys snack bars.",
        "Comedy audiences expect a light touch, so a hard product claim would "
        "land badly here.",
    ),
    "Lift Lab India": (
        "Strongest category fit with proven sponsor history",
        "Lift Lab India pairs 61.0K median views with a 5.8% engagement rate, and "
        "three recent videos carry the creator-declared paid-promotion flag. The "
        "audience is already in market for nutrition products.",
        "Category-expert viewers are often loyal to an existing brand, so expect "
        "a harder conversion than the reach suggests.",
    ),
    "Hostel Diaries": (
        "Student audience, rising cultural profile",
        "Hostel Diaries returns 78.0K median views at an 8.2% engagement rate. The "
        "content is campus life rather than nutrition, so viewers are students who "
        "snack between classes.",
        "Its prominence is rising rather than established, so reach may move either "
        "way over a campaign.",
    ),
    "Desi Gains": (
        "Best engagement per subscriber in the set",
        "Desi Gains posts a 7.1% engagement rate on a 78.0K subscriber base, the "
        "strongest ratio here, with two declared paid promotions.",
        "Smaller absolute reach than others on this list.",
    ),
    "Strength & Science": (
        "Small channel, strongest view-per-subscriber ratio",
        "Strength & Science converts 54.0K subscribers into 29.0K median views, the "
        "best views-per-subscriber figure in this set, at an 8.1% engagement rate.",
        "Low confidence: fewer usable videos than the creators above it.",
    ),
}


def _ts(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat().replace("+00:00", "Z")


def _video(i: int, views: int, er: float, days: int, paid: bool) -> dict:
    likes = int(views * er * 0.9)
    comments = int(views * er * 0.1)
    titles = [
        "I tested 7 whey proteins for 30 days, here's the winner",
        "The truth about protein timing (with the research)",
        "₹2000 vs ₹6000 protein: blind taste and lab test",
        "Full day of eating for lean muscle | Indian diet",
        "Beginner mistakes that stall your gains",
    ]
    return {
        "id": f"vid{i}",
        "snippet": {
            "title": titles[i % len(titles)],
            "description": ("Sponsored by a nutrition brand, use code SAVE10. "
                            if paid else "Links and sources below. "),
            "publishedAt": _ts(days),
            "liveBroadcastContent": "none",
        },
        "statistics": {"viewCount": str(views), "likeCount": str(likes),
                       "commentCount": str(comments)},
        "contentDetails": {"duration": "PT12M30S"},
        "_duration_s": 750,
    }


def build() -> dict:
    """Assemble a result dict shaped exactly like a real graph run."""
    candidates, relevance, audience, safety, sponsorship = [], {}, {}, {}, {}

    for idx, (title, subs, mv, er, paid, rel, aud, flag) in enumerate(SEED):
        cid = f"UCdemo{idx:018d}"
        # Spread views around the median so consistency and the median are real.
        spread = [1.0, 0.82, 1.18, 0.91, 1.09, 0.88, 1.12, 0.97]
        vids = [
            _video(i, int(mv * spread[i]), er, 12 + i * 9, paid=(i < paid))
            for i in range(8)
        ]
        channel = {
            "id": cid,
            "snippet": {
                "title": title,
                "description": "Fitness and nutrition content for Indian lifters.",
                "country": "IN",
                "publishedAt": _ts(1200),
            },
            "statistics": {"subscriberCount": str(subs), "videoCount": "180",
                           "viewCount": str(subs * 40)},
            "contentDetails": {"relatedPlaylists": {"uploads": f"UUdemo{idx}"}},
            "status": {"madeForKids": False},
        }
        m = metrics.channel_metrics(channel, vids, paid, 8)
        candidates.append({
            "channel_id": cid, "channel": channel, "metrics": m, "videos": vids,
            "paid_video_ids": [f"vid{i}" for i in range(paid)],
            "top_videos": [
                {"title": v["snippet"]["title"], "video_id": v["id"],
                 "views": v["statistics"]["viewCount"],
                 "url": f"https://www.youtube.com/watch?v={v['id']}",
                 "paid_placement": i < paid}
                for i, v in enumerate(vids[:4])
            ],
        })

        relevance[cid] = {"channel_id": cid, "relevance": rel,
                          "match_type": "direct" if rel > 0.75 else "adjacent",
                          "reason": f'Reviews supplements directly in "{vids[0]["snippet"]["title"]}".'}
        audience[cid] = {"channel_id": cid, "audience_fit": aud,
                         "inferred_viewer_profile":
                             "Lifters buying their own supplements, mostly Hindi and English speakers",
                         "purchase_intent_signal": "high" if aud > 0.75 else "medium",
                         "reasoning": "Content is purchase-adjacent: comparisons and reviews."}
        sponsorship[cid] = {
            "channel_id": cid,
            "sponsor_confidence": min(1.0, 0.25 + paid * 0.24),
            "observed_format": "dedicated_segment" if paid >= 2 else (
                "integrated_mention" if paid == 1 else "none_observed"),
            "cadence": "frequent" if paid >= 3 else ("occasional" if paid else "none_observed"),
            "known_sponsor_categories": ["supplements"] if paid else [],
            "evidence": "Sponsored by a nutrition brand, use code SAVE10" if paid else "",
        }
        if flag:
            sev, cat, reason = flag
            safety[cid] = {"channel_id": cid, "flag": True, "severity": sev,
                           "category": cat, "reason": reason,
                           "evidence": "claims specific supplements cure disease"}
        else:
            safety[cid] = {"channel_id": cid, "flag": False, "severity": "low",
                           "category": "none", "reason": "", "evidence": ""}

    cultural = {}
    for idx, (title, *_rest) in enumerate(SEED):
        row = CULTURAL.get(title)
        if row:
            cultural[f"UCdemo{idx:018d}"] = dict(row, channel_id=f"UCdemo{idx:018d}")

    trending = {candidates[2]["channel_id"]}
    ranked, review, excluded = metrics.score_candidates(
        candidates, relevance, audience, safety, sponsorship, trending, "mid",
        set(), cultural=cultural, target_generation="gen_z",
    )

    # Rationales for the top few; one deliberately fails the audit so the
    # withheld-prose path is visible.
    rationales, audit = {}, {}
    for row in ranked[:6]:
        entry = RATIONALE.get(row["title"])
        if not entry:
            continue
        cid = row["channel_id"]
        h, body, cav = entry
        rationales[cid] = {"channel_id": cid, "headline": h,
                           "rationale": body, "caveat": cav}
        audit[cid] = {"channel_id": cid, "verdict": "pass", "unsupported_claims": []}

    # One deliberately unverifiable rationale, so the withheld-prose path is
    # visible in the demo: it claims demographics, which no data can support.
    for row in ranked:
        if row["title"] == "Gym Kit Reviews":
            cid = row["channel_id"]
            rationales[cid] = {
                "channel_id": cid,
                "headline": "Reaches 2.4 million monthly viewers",
                "rationale": "This channel reaches 2.4 million viewers a month and "
                             "its audience is 70% men aged 18-24.",
                "caveat": None,
            }
            audit[cid] = {"channel_id": cid, "verdict": "revise",
                          "unsupported_claims": ["2.4 million monthly viewers",
                                                 "audience is 70% men aged 18-24"]}
            break

    return {
        "intent": {
            "brand": "Demo Nutrition Co", "product": "protein bars",
            "product_category": "sports nutrition",
            "target_audience": "students and early-career workers who snack "
                               "between classes or shifts and try new brands",
            "target_generation": "gen_z",
            "cultural_angle": "campus life, gym culture and online comedy",
            "size_band": "mid", "geo_raw": "India", "geo_granularity": "country",
            "region_code": "IN",
            "brand_safety_sensitivities": ["unproven health claims", "steroids"],
            "ambiguities": ["Budget per placement was not stated.",
                            "No preference given between Hindi and English channels."],
        },
        "query_plan": {"youtube_category_id": "17",
                       "search_keywords": ["protein powder review india",
                                           "best whey protein", "protein for muscle gain"],
                       "relevance_language": "hi", "region_code": "IN",
                       "excluded_terms": ["recipe"]},
        "scored": ranked, "review_manually": review, "excluded": excluded,
        "cultural": cultural,
        "cultural_sources": ["scroll.in", "thehindu.com", "filmcompanion.in",
                             "youtube.com/@aditidoesstandup"],
        "market_sources": ["economictimes.indiatimes.com", "inc42.com",
                           "statista.com"],
        "market": {
            "brand_known": False,
            "brand_profile": "",
            "brand_positioning": "",
            "known_competitors": ["Yoga Bar", "RiteBite", "Max Protein"],
            "category_landscape": "Crowded and price-driven, with shelf space "
                                  "dominated by three incumbents.",
            "audience_watch_habits": ["campus vlogs", "standup comedy",
                                      "gaming streams", "gym routines"],
            "confidence": "medium",
        },
        "review": {
            "verdict": "ship_with_caveat",
            "headline": "A usable shortlist, but weighted toward fitness channels.",
            "what_we_found": "Fifteen creators cleared the bar. The strongest is "
                             "a comedian rather than a fitness channel, which is "
                             "the point: her audience is the age band that buys "
                             "snack bars, and she already runs declared paid "
                             "promotions.",
            "how_to_use_this": "Approach the top three first. Aditi and Hostel "
                               "Diaries reach the target age band outside the "
                               "category, so pitch them on the snacking occasion "
                               "rather than on nutrition claims.",
            "gaps": [
                "Only two creators have verified paid-promotion history.",
                "No Tamil or Telugu language creators surfaced.",
            ],
            "suggested_refinements": [
                "Name a price point, so creators can pitch value or premium.",
                "Say whether regional-language creators are in scope.",
            ],
            "flagged_channel_ids": [],
            "diversity_note": "Nine of fifteen are fitness or nutrition channels. "
                              "The audience-space searches found the more "
                              "interesting candidates, so a broader brief would "
                              "likely surface more of them.",
        },
        "rationales": rationales, "audit": audit,
        "agent_failures": [], "notices": [], "demo": True,
    }
