"""Scoring math. No agent produces the final number.

LLM numeric scores drift between runs and cannot be audited. Agents emit
bounded judgments (rubric levels with quoted evidence); Python does the
arithmetic. Every rank is explainable by pointing at the tables below.

The score has two halves, and relevance comes first:

    fit = 100 * gate(relevance) * (0.55 * relevance + 0.45 * quality) + bonuses

  * relevance — how well this creator fits THIS brand and product: who watches
    them (audience_match), whether the product has a natural moment in their
    content (product_fit), and whether tone, price tier and values line up
    (brand_fit). See RELEVANCE_PARTS.
  * quality — measured channel strength: engagement for its size, reach,
    sponsorship track record, consistency, momentum. See WEIGHTS.
  * gate — relevance multiplies the whole score, so no amount of engagement or
    fame can carry a creator who is wrong for the brand. Below
    OFF_BRIEF_BELOW a creator is excluded outright.
"""
from __future__ import annotations

import bisect
import math
import os
import re
import statistics
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

# Videos shorter than this are Shorts: a 40s Short cannot host a 90s host-read,
# and Shorts' inflated views corrupt every ratio, so the two formats are never
# measured together (see pick_sample).
SHORTS_MAX_S = 180
# Views are still climbing in the first week — excluded from the median.
MIN_AGE_DAYS = 7
# Shorts settle faster than long videos.
SHORTS_MIN_AGE_DAYS = 3
# A creator with at least this many settled long videos is measured on them;
# below it, a Shorts-first creator is measured on their Shorts instead.
MIN_LONG_SAMPLE = 4
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
# The list a brand expects to see. When fewer than this many creators clear
# the bar, the graph widens the search one level up (broader, more popular
# kinds of creator whose viewers still include the buyer), and the shortlist
# fills toward this number with honestly labelled picks.
SHORTLIST_TARGET = int(os.environ.get("UPTICK_SHORTLIST_TARGET", "10"))
# A fill pick must still clear this. Off-brief creators never reach scoring
# (relevance gate), so everything above it fits the brand to some degree.
FILL_MIN_SCORE = float(os.environ.get("UPTICK_FILL_MIN_SCORE", "35"))


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
    def pick(rows: list[dict], tier: str) -> list[dict]:
        return [{**r, "tier": r.get("tier") or tier} for r in rows]

    strong = [r for r in ranked
              if r["fit_score"] >= RECOMMEND_MIN_SCORE and r["confidence"] != "low"]
    shown = pick(strong, "top")

    # Fill toward the target, best evidence first: strong scores on thinner
    # data, then creators who fit the brand less directly (often the
    # "one level up" picks from a widened search). Each is labelled.
    if len(shown) < SHORTLIST_TARGET:
        ids = {r["channel_id"] for r in shown}
        thin = [r for r in ranked if r["channel_id"] not in ids
                and r["fit_score"] >= RECOMMEND_MIN_SCORE]
        shown += pick(thin[:SHORTLIST_TARGET - len(shown)], "limited_data")
    if len(shown) < SHORTLIST_TARGET:
        ids = {r["channel_id"] for r in shown}
        broader = [r for r in ranked if r["channel_id"] not in ids
                   and FILL_MIN_SCORE <= r["fit_score"] < RECOMMEND_MIN_SCORE]
        shown += pick(broader[:SHORTLIST_TARGET - len(shown)], "broader")
    if not shown:
        shown = pick(ranked[:3], "broader")

    shown.sort(key=lambda r: r["fit_score"], reverse=True)
    shown = diversify(shown[:SHORTLIST_MAX])
    ids = {r["channel_id"] for r in shown}
    rest = [r for r in ranked if r["channel_id"] not in ids]
    held = len(rest)

    reason = {"held_back": held, "note": ""}
    if held > 0:
        weak = sum(1 for r in rest if r["fit_score"] < RECOMMEND_MIN_SCORE)
        thin = sum(1 for r in rest if r["fit_score"] >= RECOMMEND_MIN_SCORE
                   and r["confidence"] == "low")
        extra = held - weak - thin
        bits = []
        if weak:
            bits.append(f"{weak} scored too low for this brief")
        if thin:
            bits.append(f"{thin} had too few recent videos to judge fairly")
        if extra:
            bits.append(f"{extra} fell beyond the {SHORTLIST_MAX}-creator limit")
        reason["note"] = (
            f"We also looked at {held} other creator{'s' if held != 1 else ''} "
            f"and left {'it' if held == 1 else 'them'} out: "
            + " and ".join(bits) + "."
        )
    return shown, reason


def recommendable(ranked: list[dict]) -> int:
    """How many creators clear the recommendation bar, whatever their data."""
    return sum(1 for r in ranked if r["fit_score"] >= RECOMMEND_MIN_SCORE)

# Variety: among creators whose scores are within this many points, prefer one
# from a kind of creator not yet shown. Never promotes a weaker creator past a
# clearly stronger one; it only breaks near-ties toward a varied list. Kept
# small on purpose: a visibly lower score ranked above a higher one reads as a
# bug to the person reading the list.
DIVERSITY_WINDOW = 1.5
DIVERSITY_PENALTY = 0.08


def diversify(rows: list[dict]) -> list[dict]:
    """Reorder (never filter) a score-sorted list for variety."""
    def key(r: dict) -> str:
        return r.get("archetype") or (r.get("agent") or {}).get("match_type") or ""

    if len({key(r) for r in rows}) <= 1:
        return list(rows)
    pool = sorted(rows, key=lambda r: r["fit_score"], reverse=True)
    seen: Counter = Counter()
    out: list[dict] = []
    while pool:
        top = pool[0]["fit_score"]
        window = [r for r in pool if top - r["fit_score"] <= DIVERSITY_WINDOW]
        pick = max(window, key=lambda r: r["fit_score"]
                   * (1 - DIVERSITY_PENALTY * seen[key(r)]))
        out.append(pick)
        pool.remove(pick)
        seen[key(pick)] += 1
    return out


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

# ---------------------------------------------------------------- relevance
#
# Three parts, each from an agent that must quote its evidence. The weights say
# what matters most for a brand: the RIGHT PEOPLE watching comes first, then a
# natural place for the product, then a voice the brand can stand beside.
RELEVANCE_PARTS = {
    "audience_match": 0.45,   # Agent 4 profile vs the target buyer, compared in code
    "product_fit": 0.30,      # Agent 3: is there a natural moment for the product
    "brand_fit": 0.25,        # Agent 3: tone, price tier, values
}
# Share of the base score carried by relevance; quality carries the rest.
RELEVANCE_SHARE = 0.55
# Judged relevance below this is off-brief: excluded, whatever the numbers.
OFF_BRIEF_BELOW = float(os.environ.get("UPTICK_MIN_RELEVANCE", "0.30"))

# Rubric levels -> numbers. Ordered best to worst: a level given WITHOUT
# evidence is moved one step down, because an unsupported claim of fit is
# worth less than a supported one.
PRODUCT_FIT_LEVELS = {"natural": 1.0, "plausible": 0.7, "forced": 0.3, "none": 0.05}
BRAND_FIT_LEVELS = {"match": 1.0, "neutral": 0.6, "clash": 0.1}
AUDIENCE_LEVELS = {"strong": 1.0, "good": 0.75, "partial": 0.45, "weak": 0.15}


def level_value(table: dict[str, float], level: Any, evidence: Any) -> Optional[float]:
    """A rubric level as a number, one step lower when no evidence backs it."""
    if level not in table:
        return None
    order = list(table)
    has_evidence = bool(evidence) and (
        not isinstance(evidence, (list, tuple)) or any(str(e).strip() for e in evidence)
    ) and (not isinstance(evidence, str) or evidence.strip())
    i = order.index(level)
    if not has_evidence and i < len(order) - 1:
        i += 1
    return table[order[i]]


def gate(relevance: float) -> float:
    """How much of the score a creator may keep, by how relevant they are.

    0.80+ keeps everything; 0.50 keeps half; 0.20 keeps nothing. This is what
    stops a famous, highly engaged, wrong-audience creator from ranking.
    """
    return max(0.0, min(1.0, (relevance - 0.2) / 0.6))


# ---------------------------------------------------------------- audience match
#
# The target buyer (from the persona agent) and each creator's inferred
# audience (from Agent 4) use the SAME schema, so the comparison is done here,
# field by field, instead of asking a model for one opaque number.
AUDIENCE_FIELDS = {
    "age_bands": 0.25,
    "life_stages": 0.20,
    "interests": 0.25,
    "languages": 0.15,
    "region": 0.10,
    "spending_power": 0.05,
}
_SPEND_ORDER = ["low", "mid", "high"]
_CONF_SHRINK = {"high": 1.0, "medium": 0.75, "low": 0.4}


def _as_weights(v: Any) -> dict[str, float]:
    """A list of {key, weight} or of plain strings, as normalized weights."""
    out: dict[str, float] = {}
    for item in v or []:
        if isinstance(item, dict):
            k = str(item.get("band") or item.get("key") or "").strip().lower()
            try:
                w = float(item.get("weight", 1.0))
            except (TypeError, ValueError):
                w = 1.0
        else:
            k, w = str(item).strip().lower(), 1.0
        if k and w > 0:
            out[k] = out.get(k, 0.0) + w
    total = sum(out.values())
    return {k: w / total for k, w in out.items()} if total > 0 else {}


def _overlap(a: dict[str, float], b: dict[str, float]) -> Optional[float]:
    """Shared mass of two weighted sets: 1.0 identical, 0.0 disjoint."""
    if not a or not b:
        return None
    return sum(min(a.get(k, 0.0), b.get(k, 0.0)) for k in set(a) | set(b))


def _jaccard(a: Any, b: Any) -> Optional[float]:
    sa = {str(x).strip().lower() for x in a or [] if str(x).strip()}
    sb = {str(x).strip().lower() for x in b or [] if str(x).strip()}
    if not sa or not sb:
        return None
    # How much of what the brand wants this audience covers, softened by the
    # plain Jaccard so a creator with fifty interests does not win by breadth.
    cover = len(sa & sb) / len(sa)
    jac = len(sa & sb) / len(sa | sb)
    return 0.7 * cover + 0.3 * jac


def audience_match(target: dict, creator: dict) -> tuple[Optional[float], dict]:
    """Field-by-field fit of a creator's inferred audience to the target buyer.

    Each field's similarity is pulled toward 0.5 by the LOWER of the two
    confidences, and a field either side left blank is dropped and the rest
    renormalized. Returns (score or None, per-field detail for the UI).
    """
    if not isinstance(target, dict) or not isinstance(creator, dict):
        return None, {}
    sims: dict[str, Optional[float]] = {}
    sims["age_bands"] = _overlap(_as_weights(target.get("age_bands")),
                                 _as_weights(creator.get("age_bands")))
    sims["life_stages"] = _jaccard(target.get("life_stages"), creator.get("life_stages"))
    sims["interests"] = _jaccard(target.get("interests"), creator.get("interests"))
    tl = {str(x).lower()[:2] for x in target.get("languages") or [] if x}
    cl = {str(x).lower()[:2] for x in creator.get("languages") or [] if x}
    sims["languages"] = (1.0 if tl & cl else 0.0) if tl and cl else None
    tr, cr = (target.get("region") or "").upper(), (creator.get("region") or "").upper()
    sims["region"] = (1.0 if tr == cr else 0.0) if tr and cr else None
    ts_, cs_ = target.get("spending_power"), creator.get("spending_power")
    if ts_ in _SPEND_ORDER and cs_ in _SPEND_ORDER:
        sims["spending_power"] = 1.0 - abs(_SPEND_ORDER.index(ts_) - _SPEND_ORDER.index(cs_)) / 2
    else:
        sims["spending_power"] = None

    tconf = target.get("confidence") or {}
    cconf = creator.get("confidence") or {}
    detail: dict[str, dict] = {}
    num = den = 0.0
    for f, w in AUDIENCE_FIELDS.items():
        v = sims.get(f)
        if v is None:
            continue
        shrink = min(_CONF_SHRINK.get(tconf.get(f) if isinstance(tconf, dict) else None, 0.75),
                     _CONF_SHRINK.get(cconf.get(f) if isinstance(cconf, dict) else None, 0.75))
        adj = 0.5 + (v - 0.5) * shrink
        detail[f] = {"similarity": round(v, 3), "adjusted": round(adj, 3)}
        num += w * adj
        den += w
    if den <= 0:
        return None, detail
    return num / den, detail


def relevance_parts(
    rel: dict | None, aud: dict | None, target: dict | None, failed: set[str],
) -> tuple[dict[str, Optional[float]], dict]:
    """The three relevance parts for one creator, or None where unjudged.

    Accepts the old single-number rows too (relevance, audience_fit), so a
    cached or legacy judgment still scores rather than vanishing.
    """
    parts: dict[str, Optional[float]] = {k: None for k in RELEVANCE_PARTS}
    detail: dict = {}

    if "audience_analyst" not in failed and aud:
        prof = aud.get("profile")
        if target and isinstance(prof, dict):
            parts["audience_match"], detail["audience_fields"] = audience_match(target, prof)
        if parts["audience_match"] is None:
            lvl = aud.get("buyer_overlap")
            if lvl in AUDIENCE_LEVELS:
                parts["audience_match"] = level_value(AUDIENCE_LEVELS, lvl, aud.get("evidence"))
            elif aud.get("audience_fit") is not None:
                parts["audience_match"] = _clamp(aud.get("audience_fit"))

    if "relevance_judge" not in failed and rel:
        lvl = rel.get("product_fit")
        if lvl in PRODUCT_FIT_LEVELS:
            parts["product_fit"] = level_value(
                PRODUCT_FIT_LEVELS, lvl, rel.get("product_moments"))
        elif rel.get("relevance") is not None:
            parts["product_fit"] = _clamp(rel.get("relevance"))
        bf = rel.get("brand_fit")
        if isinstance(bf, dict):
            vals = [level_value(BRAND_FIT_LEVELS, bf.get(k), rel.get("brand_evidence"))
                    for k in ("tone", "price_tier", "values")]
            vals = [v for v in vals if v is not None]
            if vals:
                parts["brand_fit"] = sum(vals) / len(vals)
    return parts, detail


def _clamp(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return max(0.0, min(1.0, f))


# ---------------------------------------------------------------- quality

# Measured channel strength. Relevance is NOT in here: it gates and leads the
# score separately, so these weights only decide between creators who fit.
WEIGHTS = {
    "engagement_rate": 0.20,    # size-adjusted percentile within the set
    "reach": 0.14,              # median views, percentile within the set
    "view_per_sub": 0.12,       # percentile within the set
    "size_fit": 0.12,           # the brief asked for a size, honour it
    "sponsor_ratio": 0.10,      # share of their own uploads that are sponsored
    "sponsor_confidence": 0.08, # Agent 6
    "consistency": 0.08,        # spread of log views
    "momentum": 0.07,           # recent uploads vs the ones before
    "geo_fit": 0.06,            # the brief asked for a market
    "activity": 0.03,
}

# Popularity: on the chart today, and how many of the last 14 days. A bonus,
# scaled by the gate, so it lifts creators who fit and never rescues one who
# does not.
POPULARITY_BONUS_MAX = 6.0


def popularity_bonus(trending_days: int, trending_today: bool) -> float:
    days = max(int(trending_days or 0), 1 if trending_today else 0)
    if days <= 0:
        return 0.0
    return min(POPULARITY_BONUS_MAX, 2.0 + (days - 1) * 1.0)



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


def eligible_videos(videos: list[dict], fmt: str = "long") -> list[dict]:
    """The videos a creator is measured on, for one format.

    "long" drops Shorts, live/premieres and videos too young to have settled.
    "shorts" keeps only Shorts, settled for SHORTS_MIN_AGE_DAYS (Shorts views
    plateau within days, not weeks). The two are never mixed in one sample:
    Shorts views run far higher, so mixing would corrupt every median.
    """
    out = []
    for v in videos:
        snip = v.get("snippet", {})
        if snip.get("liveBroadcastContent", "none") != "none":
            continue
        dur = v.get("_duration_s") or 0
        a = age_days(snip.get("publishedAt"))
        if fmt == "shorts":
            if not (0 < dur < SHORTS_MAX_S):
                continue
            if a is None or a < SHORTS_MIN_AGE_DAYS:
                continue
        else:
            if dur < SHORTS_MAX_S:
                continue
            if a is None or a < MIN_AGE_DAYS:
                continue
        out.append(v)
    return out


def pick_sample(videos: list[dict]) -> tuple[str, list[dict]]:
    """Measure a creator on the format they actually make.

    Long-form when there is enough of it (a host-read fits there best), else
    Shorts when the creator is mainly a Shorts creator. Much of a market's
    attention, India's especially, lives in Shorts; dropping every Shorts-first
    creator as "insufficient data" threw most of the trending chart away.
    """
    long = eligible_videos(videos, "long")
    if len(long) >= MIN_LONG_SAMPLE:
        return "long", long
    shorts = eligible_videos(videos, "shorts")
    if len(shorts) > len(long):
        return "shorts", shorts
    return "long", long


# ---------------------------------------------------------------- sponsorship

# Phrases creators use to disclose a deal in a description. Deterministic, so
# the sponsorship term never depends on a model reading the text.
_SPONSOR_RE = re.compile(
    r"(sponsored by|this video is sponsored|thanks to [^.\n]{1,40} for sponsoring"
    r"|paid promotion|paid partnership|in partnership with|partnered with"
    r"|use (?:my |our )?code|promo code|coupon code|discount code"
    r"|#ad\b|#sponsored|#paidpartnership)",
    re.I,
)


def sponsor_signals(videos: list[dict], paid_ids: set[str] | None = None) -> dict:
    """How often this creator runs sponsored content, from their OWN uploads.

    Counts a video as sponsored when the creator ticked YouTube's paid
    promotion box on it, it came back from the paid-placement search, or its
    description discloses a deal. The ratio is None when nothing sponsored was
    seen: disclosure is self-declared and under-reported, so absence is
    missing evidence, never a zero.
    """
    paid_ids = paid_ids or set()
    _, elig = pick_sample(videos)
    declared = described = sponsored = 0
    last: Optional[float] = None
    for v in elig:
        d = bool((v.get("paidProductPlacementDetails") or {}).get("hasPaidProductPlacement")) \
            or v.get("id") in paid_ids
        t = bool(_SPONSOR_RE.search((v.get("snippet") or {}).get("description") or ""))
        declared += d
        described += t
        if d or t:
            sponsored += 1
            age = age_days((v.get("snippet") or {}).get("publishedAt"))
            if age is not None and (last is None or age < last):
                last = age
    return {
        "sponsored_videos": sponsored,
        "declared_paid_videos": declared,
        "described_sponsor_videos": described,
        "ratio": (sponsored / len(elig)) if (elig and sponsored) else None,
        "last_sponsored_days": last,
    }


def sponsor_term(ratio: Optional[float]) -> Optional[float]:
    """Sponsorship ratio as a score: proof they take deals, without ad fatigue.

    A creator sponsoring roughly one video in four is the sweet spot. Nearly
    every video sponsored means viewers have learned to skip the read.
    """
    if ratio is None or ratio <= 0:
        return None
    if ratio < 0.15:
        return 0.6 + 0.4 * ratio / 0.15
    if ratio <= 0.35:
        return 1.0
    if ratio <= 0.7:
        return 1.0 - 0.5 * (ratio - 0.35) / 0.35
    return 0.5


def channel_metrics(
    channel: dict, videos: list[dict], paid_hits: int, videos_surfaced: int,
    paid_ids: set[str] | None = None,
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

    fmt, elig = pick_sample(videos)
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

    # Spread of LOG views: views are heavy-tailed, so the coefficient of
    # variation swings wildly on one viral upload. A standard deviation of 0.6
    # in log10 (one video routinely 4x another) scores zero.
    consistency = None
    logs = [math.log10(x) for x in views if x > 0]
    if len(logs) >= 3:
        consistency = max(0.0, 1.0 - statistics.stdev(logs) / 0.6)

    # Momentum: the last five uploads against the ones before them. 0.5 is
    # flat, 1.0 is doubling, 0.0 is halving.
    momentum = None
    dated = sorted(
        (
            (age_days(v.get("snippet", {}).get("publishedAt")) or 0.0,
             _int(v.get("statistics", {}), "viewCount"))
            for v in elig
        ),
        key=lambda t: t[0],
    )
    dated = [vc for _, vc in dated if vc is not None]
    if len(dated) >= 8:
        recent = statistics.median(dated[:5])
        older = statistics.median(dated[5:20])
        if recent > 0 and older > 0:
            momentum = max(0.0, min(1.0, 0.5 + math.log2(recent / older) / 2))

    sponsor = sponsor_signals(videos, paid_ids)
    if not paid_ids and paid_hits and sponsor["ratio"] is None and videos_surfaced:
        # Older callers pass only a count of paid-search hits.
        sponsor["ratio"] = max(0.0, min(1.0, paid_hits / videos_surfaced))

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
        "momentum": momentum,
        "sponsor_ratio": sponsor["ratio"],
        "sponsored_videos": sponsor["sponsored_videos"],
        "declared_paid_videos": sponsor["declared_paid_videos"],
        "last_sponsored_days": sponsor["last_sponsored_days"],
        "activity": activity,
        "paid_placement_hits": paid_hits,
        "videos_surfaced": videos_surfaced,
        "eligible_video_count": len(elig),
        # Which format the numbers above describe: "long" or "shorts".
        "format": fmt,
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


def percentile(values: dict[str, Optional[float]]) -> dict[str, Optional[float]]:
    """Rank within the CURRENT candidate set, as 0..1 (ties share a rank).

    Replaces min-max for the code terms: with min-max one viral outlier
    squashes everyone else toward zero, while a rank only cares about order.
    A single candidate, or an all-equal set, scores 0.5.
    """
    present = sorted(v for v in values.values() if v is not None)
    n = len(present)
    out: dict[str, Optional[float]] = {}
    for k, v in values.items():
        if v is None:
            out[k] = None
        elif n == 1:
            out[k] = 0.5
        else:
            lo = bisect.bisect_left(present, v)
            hi = bisect.bisect_right(present, v) - 1
            out[k] = ((lo + hi) / 2) / (n - 1)
    return out


def size_adjusted(
    er: dict[str, Optional[float]], subs: dict[str, Optional[int]], min_points: int = 6,
) -> dict[str, Optional[float]]:
    """Engagement judged against what is normal FOR THE CHANNEL'S SIZE.

    Engagement rate falls as channels grow, so a raw comparison always favours
    the smallest channel in the set. Fit log(ER) against log(subs) across the
    set and rank the residuals: "more engaged than a channel this size usually
    is". Falls back to a plain rank when there are too few points to fit.
    """
    pts = {k: (math.log(subs[k]), math.log(v)) for k, v in er.items()
           if v and v > 0 and subs.get(k)}
    if len(pts) < min_points:
        return percentile(er)
    xs = [x for x, _ in pts.values()]
    ys = [y for _, y in pts.values()]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    var = sum((x - mx) ** 2 for x in xs)
    if var <= 1e-12:
        return percentile(er)
    slope = sum((x - mx) * (y - my) for x, y in pts.values()) / var
    resid: dict[str, Optional[float]] = {}
    for k, v in er.items():
        if not v or v <= 0:
            resid[k] = None
        elif k in pts:
            x, y = pts[k]
            resid[k] = y - (my + slope * (x - mx))
        else:
            # Hidden subscriber count: compare with the set's typical level.
            resid[k] = math.log(v) - my
    return percentile(resid)


def prefilter(channel: dict, size_band: str, region_code: str | None) -> tuple[str, str]:
    """Screen a channel on its stats alone, before any video is fetched.

    Returns (verdict, reason):
      "drop"     — excluded on principle (made for kids, too new)
      "overflow" — below the reach floor, outside the size band, or based in
                   another market. Kept in reserve so a thin search can still
                   show its closest near-misses instead of a blank page.
      "keep"     — worth spending enrichment on.

    Runs on every channel discovery finds, so the expensive per-creator steps
    are spent only on creators who could actually be recommended.
    """
    if (channel.get("status") or {}).get("madeForKids"):
        return "drop", "made for kids (COPPA ad restrictions)"
    age = age_days((channel.get("snippet") or {}).get("publishedAt"))
    if age is not None and age < MIN_CHANNEL_AGE_DAYS:
        return "drop", "channel less than 90 days old"
    title = (channel.get("snippet") or {}).get("title") or ""
    if title.endswith(" - Topic") or "vevo" in title.lower():
        return "drop", "auto-generated music or label channel"
    stats = channel.get("statistics") or {}
    subs = None if stats.get("hiddenSubscriberCount") else _int(stats, "subscriberCount")
    if subs is not None and 0 < subs < MIN_SUBSCRIBERS:
        return "overflow", f"under {MIN_SUBSCRIBERS:,} subscribers ({subs:,})"
    if band_distance(subs, size_band) > MAX_BAND_DISTANCE:
        return "overflow", f"far outside the requested {size_band}-tier size band"
    country = (channel.get("snippet") or {}).get("country")
    if geo_fit(country, region_code) == 0.0:
        return "overflow", f"based in {country}, the brief asked for {region_code}"
    return "keep", ""


def hard_exclusion(c: dict, size_band: str) -> Optional[str]:
    """Why this enriched creator can never be recommended, or None.

    Code-only (channel stats and measured metrics), so the graph can apply it
    BEFORE any model judges the creator: a creator who will be excluded anyway
    is not worth paying four judges to read.
    """
    ch, m = c["channel"], c["metrics"]
    if ch.get("status", {}).get("madeForKids"):
        return "made for kids (COPPA ad restrictions)"
    if m["channel_age_days"] is not None and m["channel_age_days"] < MIN_CHANNEL_AGE_DAYS:
        return "channel less than 90 days old"
    if m["last_upload_days"] is None or m["last_upload_days"] > MAX_STALE_DAYS:
        return "no upload in the last 180 days"
    if confidence(m) == "insufficient":
        return "insufficient data (under 2 usable videos)"

    # Reach floors, applied after the data-quality checks so the reason given
    # is the most specific one.
    subs = m["subscriber_count"]
    if subs is not None and subs < MIN_SUBSCRIBERS:
        return f"under {MIN_SUBSCRIBERS:,} subscribers ({subs:,})"
    mv = m["median_views"]
    if mv is None or mv < MIN_MEDIAN_VIEWS:
        return (f"median views below {MIN_MEDIAN_VIEWS:,}"
                + (f" ({int(mv):,})" if mv is not None else ""))

    # Size band, as a hard rule rather than a scoring nudge.
    #
    # A brief that says "mid-tier" is usually saying "this is the budget".
    # A 20M-subscriber channel is not an imperfect answer to that, it is an
    # unaffordable one, and letting a strong engagement score outweigh it is
    # how a shortlist ends up recommending creators nobody can book.
    if band_distance(subs, size_band) > MAX_BAND_DISTANCE:
        subs_txt = f"{subs:,}" if subs is not None else "unknown"
        return (f"far outside the requested {size_band}-tier size band "
                f"({subs_txt} subscribers)")
    return None


SIZE_EXCLUSIONS = ("subscribers", "median views", "size band")


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
    target_audience: dict | None = None,
    trending_days: dict[str, int] | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Returns (ranked, review_manually, excluded).

    Any missing term — a failed agent or an unmeasurable metric — has its
    weight dropped and the remainder renormalized. The query never fails; it
    degrades and is marked partial.
    """
    excluded: list[dict] = []
    live: list[dict] = []

    # ---- hard exclusions, before scoring
    for c in candidates:
        why = hard_exclusion(c, size_band)
        if why:
            excluded.append({**c, "exclusion": why})
        else:
            live.append(c)

    if not live:
        return [], [], excluded

    # ---- relative terms across the surviving set, within each format: a
    # Shorts creator's views are compared with other Shorts creators', never
    # with a long-form creator's.
    norm_er: dict[str, Optional[float]] = {}
    norm_vps: dict[str, Optional[float]] = {}
    norm_reach: dict[str, Optional[float]] = {}
    by_fmt: dict[str, list[dict]] = {}
    for c in live:
        by_fmt.setdefault(c["metrics"].get("format") or "long", []).append(c)
    for grp in by_fmt.values():
        norm_er.update(size_adjusted(
            {c["channel_id"]: c["metrics"]["engagement_rate"] for c in grp},
            {c["channel_id"]: c["metrics"]["subscriber_count"] for c in grp},
        ))
        norm_vps.update(percentile(
            {c["channel_id"]: c["metrics"]["view_per_sub"] for c in grp}))
        norm_reach.update(percentile(
            {c["channel_id"]: c["metrics"]["median_views"] for c in grp}))

    ranked: list[dict] = []
    review: list[dict] = []
    tdays = trending_days or {}

    for c in live:
        cid, m = c["channel_id"], c["metrics"]
        rel = relevance.get(cid) or {}
        aud = audience.get(cid) or {}
        saf = safety.get(cid) or {}
        spo = sponsorship.get(cid) or {}

        # ---- relevance: the three parts, the gate, the off-brief rule
        parts, rel_detail = relevance_parts(rel, aud, target_audience, failed_agents)
        present = {k: v for k, v in parts.items() if v is not None}
        if present:
            rw = sum(RELEVANCE_PARTS[k] for k in present)
            r = sum(RELEVANCE_PARTS[k] * v for k, v in present.items()) / rw
            g = gate(r)
            if r < OFF_BRIEF_BELOW:
                excluded.append({
                    **c,
                    "exclusion": f"off-brief: relevance {r:.2f} is below "
                                 f"{OFF_BRIEF_BELOW:.2f} for this brand and product",
                })
                continue
        else:
            # Nobody could judge this creator. Neutral, not negative, and the
            # gate is not applied to a judgment that was never made.
            rw, r, g = 0.0, 0.5, 1.0

        # ---- quality
        terms: dict[str, Optional[float]] = {
            "engagement_rate": norm_er[cid],
            "reach": norm_reach[cid],
            "view_per_sub": norm_vps[cid],
            "size_fit": size_fit(m["subscriber_count"], size_band),
            "sponsor_ratio": sponsor_term(m.get("sponsor_ratio")),
            "sponsor_confidence": None if "sponsorship_analyst" in failed_agents
                                  else (_clamp(spo.get("sponsor_confidence")) if spo else None),
            "consistency": m["consistency"],
            "momentum": m.get("momentum"),
            "geo_fit": geo_fit(
                c["channel"].get("snippet", {}).get("country"), region_code
            ),
            "activity": m["activity"],
        }
        usable = {k: v for k, v in terms.items() if v is not None}
        total_w = sum(WEIGHTS[k] for k in usable)
        q = (sum(WEIGHTS[k] * usable[k] for k in usable) / total_w) if total_w > 0 else 0.5

        base = g * (RELEVANCE_SHARE * r + (1 - RELEVANCE_SHARE) * q)

        is_trending = cid in trending
        cul_row = (cultural or {}).get(cid)
        cul_bonus, cul_reason = cultural_bonus(cul_row, target_generation)
        pop_bonus = popularity_bonus(tdays.get(cid, 0), is_trending)
        # Bonuses are scaled by the gate too: fame lifts a creator who fits,
        # and does nothing for one who does not.
        fit = round(min(100.0, 100.0 * base + g * (cul_bonus + pop_bonus)), 1)

        breakdown: list[dict] = []
        if present:
            for k in sorted(present, key=lambda x: -RELEVANCE_PARTS[x]):
                w = g * RELEVANCE_SHARE * RELEVANCE_PARTS[k] / rw
                breakdown.append({"term": k, "group": "relevance",
                                  "value": round(present[k], 4), "weight": round(w, 4),
                                  "contribution": round(100.0 * w * present[k], 2)})
        else:
            w = g * RELEVANCE_SHARE
            breakdown.append({"term": "relevance", "group": "relevance",
                              "value": 0.5, "weight": round(w, 4),
                              "contribution": round(100.0 * w * 0.5, 2)})
        for k in sorted(usable, key=lambda x: -WEIGHTS[x]):
            w = g * (1 - RELEVANCE_SHARE) * WEIGHTS[k] / total_w
            breakdown.append({"term": k, "group": "quality",
                              "value": round(usable[k], 4), "weight": round(w, 4),
                              "contribution": round(100.0 * w * usable[k], 2)})

        dropped = [k for k, v in parts.items() if v is None] + \
                  [k for k, v in terms.items() if v is None]

        row = {
            "channel_id": cid,
            "title": c["channel"].get("snippet", {}).get("title", ""),
            "description": c["channel"].get("snippet", {}).get("description", ""),
            "country": c["channel"].get("snippet", {}).get("country"),
            "url": f"https://www.youtube.com/channel/{cid}",
            "fit_score": fit,
            "size_band": size_band,
            "confidence": confidence(m),
            "partial": bool(failed_agents) or bool(dropped),
            "trending": is_trending,
            "trending_days": tdays.get(cid, 0),
            "popularity_bonus": round(g * pop_bonus, 2),
            "cultural_bonus": round(g * cul_bonus, 2),
            "cultural_reason": cul_reason,
            "cultural": cul_row or {},
            "found_via": c.get("found_via", []),
            # The judge read the content; the search label only says which
            # query happened to surface the creator.
            "archetype": (rel.get("archetype")
                          if rel.get("archetype") not in (None, "", "other")
                          else c.get("archetype") or ""),
            "relevance_score": round(r, 4) if present else None,
            "relevance_gate": round(g, 4),
            "relevance_detail": {
                **rel_detail,
                "audience_level": aud.get("buyer_overlap"),
                "audience_evidence": aud.get("evidence") or [],
                "product_fit": rel.get("product_fit"),
                "product_moments": rel.get("product_moments") or [],
                "brand_fit": rel.get("brand_fit") or {},
                "brand_evidence": rel.get("brand_evidence") or "",
            },
            "metrics": m,
            "breakdown": breakdown,
            "dropped_terms": dropped,
            "top_videos": c.get("top_videos", []),
            "agent": {
                "relevance": round(r, 4) if present else None,
                "relevance_reason": rel.get("reason"),
                "match_type": rel.get("match_type"),
                "audience_fit": parts.get("audience_match"),
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
