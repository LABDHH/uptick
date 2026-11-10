"""Why each creator was chosen, in words and numbers a marketer can read.

The score is computed in code (metrics.py). This module turns a scored row
into three things, all derived from that same row so nothing here can drift
from the numbers:

  * key_numbers(row): the handful of supporting numbers shown on the card,
    rounded for people ("1.4M", "~600K per video", "1.3%"), each with a one
    line meaning ("more than most on this list").
  * facts(row, rank, total): everything the rationale writer may cite, already
    in that human form. The output auditor checks the prose against the SAME
    facts, so a true sentence about the audience or a sponsor is never
    rejected just because the checker could not see where it came from.
  * summary(row): a plain-language headline, reason and caveat written in
    code. Shown whenever the writer's prose is missing or did not pass the
    check, so every creator on the list always says why it is there.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import metrics

# ---------------------------------------------------------------- formatting


def compact(n: Optional[float]) -> Optional[str]:
    """1350000 -> "1.4M", 597029.5 -> "597K", 8421 -> "8.4K"."""
    if n is None:
        return None
    n = float(n)
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= div:
            v = n / div
            s = f"{v:.0f}" if v >= 10 else f"{v:.1f}"
            return (s[:-2] if s.endswith(".0") else s) + suf
    return f"{n:.0f}"


def pct(x: Optional[float]) -> Optional[str]:
    """0.012944 -> "1.3%"."""
    if x is None:
        return None
    v = x * 100
    return f"{v:.2f}%" if v < 0.1 else f"{v:.1f}%"


def ago(days: Optional[float]) -> Optional[str]:
    if days is None:
        return None
    d = int(round(days))
    if d <= 1:
        return "in the last day"
    if d < 14:
        return f"{d} days ago"
    if d < 60:
        return f"{d // 7} weeks ago"
    return f"{d // 30} months ago"


def short_title(t: Optional[str], limit: int = 64) -> str:
    """A video title as a quote: no hashtags, no "| Channel Name" tail."""
    t = re.sub(r"#\S+", "", t or "")
    t = re.split(r"\s*\|\|\s*", t)[0]
    t = re.sub(r"\s+", " ", t).strip(" -–—:")
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def _profile(p: Optional[str]) -> str:
    """'Likely Gen Z comedy fans.' -> 'Gen Z comedy fans'."""
    p = re.sub(r"\s*\((?:inferred|based|read)[^)]*\)", "", p or "")
    p = p.strip().rstrip(".")
    p = re.sub(r"^(likely|probably|mostly|mainly)\s+", "", p, flags=re.I)
    return p


def _relative(v: Optional[float], hi: str, mid: str, lo: str) -> Optional[str]:
    """A within-list percentile (0..1) as words."""
    if v is None:
        return None
    if v >= 0.66:
        return hi
    if v >= 0.34:
        return mid
    return lo


def _term(row: dict, name: str) -> Optional[float]:
    for b in row.get("breakdown") or []:
        if b.get("term") == name:
            return b.get("value")
    return None


BAND_RANGE = {
    "nano": "under 10K", "micro": "10K–100K", "mid": "100K–1M",
    "macro": "1M–10M", "mega": "10M+",
}


def _size_note(subs: Optional[int], band: Optional[str]) -> Optional[str]:
    if subs is None or band not in metrics.SIZE_BANDS:
        return None
    lo, hi = metrics.SIZE_BANDS[band]
    rng = BAND_RANGE.get(band, band)
    if lo <= subs < hi:
        return f"in the {band} range you asked for ({rng})"
    return (f"larger than the {band} range you asked for ({rng})" if subs >= hi
            else f"smaller than the {band} range you asked for ({rng})")


# ---------------------------------------------------------------- key numbers

AUDIENCE_WORDS = {"strong": "Strong match", "good": "Good match",
                  "partial": "Partial match", "weak": "Weak match"}
PRODUCT_WORDS = {"natural": "Natural fit", "plausible": "Can fit",
                 "forced": "Would feel forced", "none": "No natural place"}


def key_numbers(row: dict) -> list[dict]:
    """The supporting numbers for one creator, each with what it means.

    Ordered so the evidence for relevance comes first, then reach and
    engagement, then sponsorship and popularity. Missing data is left out
    rather than shown as zero.
    """
    m = row.get("metrics") or {}
    rd = row.get("relevance_detail") or {}
    agent = row.get("agent") or {}
    shorts = m.get("format") == "shorts"
    out: list[dict] = []

    lvl = rd.get("audience_level")
    if lvl in AUDIENCE_WORDS:
        prof = _profile(agent.get("inferred_viewer_profile"))
        out.append({"label": "Audience", "value": AUDIENCE_WORDS[lvl],
                    "note": (f"Likely viewers: {prof}, inferred from comments and titles"
                             if prof else "inferred from comments and titles")})

    pf = rd.get("product_fit")
    if pf in PRODUCT_WORDS:
        moment = short_title((rd.get("product_moments") or [None])[0])
        out.append({"label": "Product fit", "value": PRODUCT_WORDS[pf],
                    "note": f"e.g. in “{moment}”" if moment else None})

    bf = rd.get("brand_fit") or {}
    judged = {k: v for k, v in bf.items() if v in metrics.BRAND_FIT_LEVELS}
    if judged:
        ok = sum(1 for v in judged.values() if v == "match")
        names = {"tone": "Tone", "price_tier": "Price", "values": "Values"}
        mark = {"match": "✓", "neutral": "neutral", "clash": "✗ clashes"}
        parts = [f"{names.get(k, k)} {mark[v]}" for k, v in judged.items()]
        out.append({"label": "Brand fit", "value": f"{ok} of {len(judged)} match",
                    "note": " · ".join(parts)})

    subs = m.get("subscriber_count")
    if subs:
        out.append({"label": "Subscribers", "value": compact(subs),
                    "note": _size_note(subs, row.get("size_band"))})

    mv = m.get("median_views")
    if mv:
        out.append({
            "label": "Typical views",
            "value": f"~{compact(mv)} per {'Short' if shorts else 'video'}",
            "note": _relative(_term(row, "reach"),
                              "more than most on this list",
                              "around the middle of this list",
                              "fewer than most on this list"),
        })

    er = m.get("engagement_rate")
    if er is not None:
        rel = _relative(_term(row, "engagement_rate"),
                        "above usual for a channel this size",
                        "about usual for a channel this size",
                        "below usual for a channel this size")
        out.append({"label": "Engagement", "value": pct(er),
                    "note": "of viewers like or comment" + (f", {rel}" if rel else "")})

    n = m.get("eligible_video_count") or 0
    sp = m.get("sponsored_videos")
    if n and sp is not None:
        last = ago(m.get("last_sponsored_days"))
        out.append({
            "label": "Sponsored videos",
            "value": f"{sp} of last {n}",
            "note": (f"most recent {last}" if sp and last
                     else "none spotted, so brand deals may be new to them"),
        })

    days = row.get("trending_days") or 0
    if days > 0:
        out.append({"label": "Trending", "value": f"{days} of last 14 days",
                    "note": "on YouTube's trending chart in your market"})

    cons = m.get("consistency")
    if cons is not None:
        out.append({"label": "Steadiness",
                    "value": ("Steady" if cons >= 0.6 else
                              "Mixed" if cons >= 0.3 else "Swings a lot"),
                    "note": "how similar views are from one video to the next"})

    mom = m.get("momentum")
    if mom is not None:
        out.append({"label": "Momentum",
                    "value": ("Growing" if mom >= 0.6 else
                              "Flat" if mom >= 0.4 else "Slowing"),
                    "note": "latest 5 videos against the 15 before them"})
    return out


# ---------------------------------------------------------------- strengths

STRENGTH = {
    "audience_match": "their viewers look like your buyers",
    "product_fit": "the product fits naturally into their videos",
    "brand_fit": "their tone and price level suit the brand",
    "engagement_rate": "viewers engage more than usual for a channel this size",
    "reach": "their videos reach more people than most on this list",
    "view_per_sub": "a large share of their subscribers watch each video",
    "size_fit": "they are the size of creator you asked for",
    "sponsor_ratio": "they already run sponsorships, without overdoing it",
    "sponsor_confidence": "they are comfortable doing brand deals",
    "consistency": "their views are steady from video to video",
    "momentum": "their recent videos are growing",
    "geo_fit": "they are based in your target market",
}
WEAKNESS = {
    "audience_match": "their audience only partly overlaps your buyers",
    "product_fit": "the product would not sit naturally in their videos",
    "brand_fit": "their tone or price level may not suit the brand",
    "engagement_rate": "viewers engage less than usual for a channel this size",
    "reach": "typical views are lower than most on this list",
    "view_per_sub": "only a small share of subscribers watch each video",
    "size_fit": "they are outside the size you asked for",
    "sponsor_confidence": "there is little sign of past brand deals",
    "consistency": "views swing a lot from video to video",
    "momentum": "recent videos are getting fewer views than before",
}


def _strength_terms(row: dict) -> list[str]:
    rows = sorted((b for b in row.get("breakdown") or []
                   if b.get("term") in STRENGTH and (b.get("value") or 0) >= 0.7),
                  key=lambda b: -(b.get("contribution") or 0))
    return [b["term"] for b in rows]


def strengths(row: dict, n: int = 3) -> list[str]:
    out = [STRENGTH[t] for t in _strength_terms(row)]
    if (row.get("trending_days") or 0) >= 2:
        out.insert(min(2, len(out)),
                   f"they have trended on {row['trending_days']} of the last 14 days")
    return out[:n]


def _headline(row: dict) -> str:
    """The single strongest reason, made specific to this creator."""
    m = row.get("metrics") or {}
    rd = row.get("relevance_detail") or {}
    agent = row.get("agent") or {}
    unit = "Short" if m.get("format") == "shorts" else "video"
    # The most specific evidence first: a video, the viewers, the numbers.
    # Brand fit is the least distinctive, so it leads only when nothing else does.
    prefer = ["product_fit", "audience_match", "engagement_rate", "reach", "brand_fit"]
    have = _strength_terms(row)
    for term in [t for t in prefer if t in have] + [t for t in have if t not in prefer]:
        if term == "product_fit":
            moment = short_title((rd.get("product_moments") or [None])[0], 48)
            if moment:
                return f"Natural fit in videos like “{moment}”"
        if term == "audience_match":
            prof = _profile(agent.get("inferred_viewer_profile"))
            if prof:
                prof = prof if len(prof) <= 70 else prof[:69].rstrip(" ,") + "…"
                return f"Viewers look like your buyers: {prof[0].lower() + prof[1:]}"
        if term == "reach" and m.get("median_views"):
            return f"Big reach: about {compact(m['median_views'])} views per {unit}"
        if term == "engagement_rate" and m.get("engagement_rate") is not None:
            return f"Unusually engaged viewers: {pct(m['engagement_rate'])} like or comment"
        if term == "brand_fit" and rd.get("brand_evidence"):
            ev = str(rd["brand_evidence"]).rstrip(".")
            ev = ev if len(ev) <= 90 else ev[:89].rstrip(" ,") + "…"
            return f"On-brand: {ev[0].lower() + ev[1:]}"
        if term in STRENGTH:
            t = STRENGTH[term]
            return t[0].upper() + t[1:]
    return row.get("archetype") or "A reasonable fit for this brief"


def weaknesses(row: dict, n: int = 2) -> list[str]:
    rows = sorted((b for b in row.get("breakdown") or []
                   if b.get("term") in WEAKNESS and b.get("value") is not None
                   and b["value"] <= 0.3 and (b.get("weight") or 0) >= 0.03),
                  key=lambda b: -(b.get("weight") or 0))
    return [WEAKNESS[b["term"]] for b in rows][:n]


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


TIER_NOTE = {
    "limited_data": "We could only see a few of their recent videos, so treat "
                    "these numbers as early signals.",
    "broader": "A broader pick: not a direct match for the product, but a "
               "popular creator whose viewers overlap your buyers.",
}


# ---------------------------------------------------------------- facts

def facts(row: dict, rank: Optional[int] = None, total: Optional[int] = None) -> dict:
    """Everything the rationale writer may cite, in human form."""
    agent = row.get("agent") or {}
    cul = row.get("cultural") or {}
    rd = row.get("relevance_detail") or {}
    f: dict[str, Any] = {
        "channel_id": row["channel_id"],
        "name": row.get("title"),
        "kind_of_creator": row.get("archetype") or None,
        "rank": f"{rank} of {total}" if rank and total else None,
        "fit_score": round(row.get("fit_score") or 0),
        "list_tier": row.get("tier") or "top",
        "format": "Shorts" if (row.get("metrics") or {}).get("format") == "shorts"
                  else "long videos",
        "key_numbers": [{k: v for k, v in kn.items() if v} for kn in key_numbers(row)],
        "strengths": strengths(row, 4),
        "weaknesses": weaknesses(row),
        "why_relevant": agent.get("relevance_reason"),
        "likely_viewers_inferred": agent.get("inferred_viewer_profile"),
        "audience_evidence": (rd.get("audience_evidence") or [])[:3],
        "product_moments": [short_title(t) for t in (rd.get("product_moments") or [])[:3]],
        "brand_evidence": rd.get("brand_evidence") or None,
        "sponsor_evidence": agent.get("sponsor_evidence") or None,
        "known_sponsor_categories": agent.get("known_sponsor_categories") or None,
        "off_youtube": cul.get("notable_context") if cul.get("evidence_found") else None,
        "data_note": TIER_NOTE.get(row.get("tier") or ""),
        "safety_note": agent.get("safety_reason") if agent.get("safety_flag") else None,
    }
    return {k: v for k, v in f.items() if v not in (None, [], "")}


# ---------------------------------------------------------------- summary

def summary(row: dict) -> dict:
    """A plain headline, reason and caveat, written in code from the row."""
    s = strengths(row, 3)
    w = weaknesses(row, 1)
    agent = row.get("agent") or {}
    rd = row.get("relevance_detail") or {}
    m = row.get("metrics") or {}

    # The kind of creator is already a pill on the card; the headline says
    # the single strongest reason, in this creator's own evidence.
    headline = _headline(row)

    parts: list[str] = []
    if s:
        parts.append(f"Picked because {_join(s)}.")
    prof = _profile(agent.get("inferred_viewer_profile"))
    if prof:
        parts.append(f"Their viewers appear to be {prof[0].lower() + prof[1:]}.")
    moment = short_title((rd.get("product_moments") or [None])[0])
    if moment and rd.get("product_fit") in ("natural", "plausible"):
        parts.append(f"The product fits videos like “{moment}”.")
    mv = m.get("median_views")
    subs = m.get("subscriber_count")
    if mv:
        unit = "Short" if m.get("format") == "shorts" else "video"
        bit = f"About {compact(mv)} views per {unit}"
        if subs:
            bit += f" on {compact(subs)} subscribers"
        parts.append(bit + ".")

    caveat = TIER_NOTE.get(row.get("tier") or "")
    if not caveat and w:
        caveat = w[0][0].upper() + w[0][1:] + "."
    if not caveat and row.get("confidence") == "low":
        caveat = TIER_NOTE["limited_data"]
    return {"headline": headline[:120], "rationale": " ".join(parts),
            "caveat": caveat}
