"""Inputs for the relevance judges: the brand and buyer, and audience evidence.

Relevance is judged in three parts (see metrics.RELEVANCE_PARTS). Two agents
do the judging (agents.agent3_relevance and agents.agent4_audience); this
module prepares what they read:

  * persona_context() renders the brand profile, the moments the product is
    used in, the target buyer and the creator archetypes, so the judges score
    fit with THIS brand rather than with a product category.
  * audience_evidence() gathers, per creator, the evidence that says who
    watches: a sample of viewer comments, the creator's tags, topics and
    keywords, and the audio language. Only the audience analyst receives it,
    so the other judges do not pay to read it.

Everything YouTube-derived is cleaned with agents.clean_text and later wrapped
in the <candidate_data> delimiters by the payload builder.
"""
from __future__ import annotations

import json
from collections import Counter

from agents import clean_text

# Viewer comments per creator, after cleaning. Enough to see language, place
# and life stage; few enough that a 30-creator batch stays cheap.
COMMENTS_PER_CREATOR = 16
COMMENT_CHARS = 140


def persona_context(persona: dict | None) -> str:
    """The brand and its buyer, as one block every relevance judge reads."""
    if not persona:
        return ""
    lines: list[str] = []
    bp = persona.get("brand_profile") or {}
    if bp:
        bits = []
        if bp.get("tone"):
            bits.append(f"tone {bp['tone']}")
        if bp.get("price_tier"):
            bits.append(f"price tier {bp['price_tier']}")
        if bp.get("values"):
            bits.append("values: " + ", ".join(bp["values"]))
        if bp.get("avoid"):
            bits.append("must not sit beside: " + ", ".join(bp["avoid"]))
        if bp.get("languages"):
            bits.append("campaign languages: " + ", ".join(bp["languages"]))
        lines.append("BRAND PROFILE: " + "; ".join(bits))
    if persona.get("product_moments"):
        lines.append("PRODUCT MOMENTS (where a mention feels natural): "
                     + "; ".join(persona["product_moments"]))
    if persona.get("target_audience"):
        lines.append("TARGET BUYER PROFILE: "
                     + json.dumps(persona["target_audience"], separators=(",", ":")))
    arcs = persona.get("archetypes") or []
    if arcs:
        lines.append("CREATOR ARCHETYPES:\n" + "\n".join(
            f"  - {a['name']}: {a.get('why_audience_matches', '')}" for a in arcs))
    if not lines:
        return ""
    return "BRAND AND BUYER\n" + "\n".join(lines) + "\n\n"


def _topic(url: str) -> str:
    """Wikipedia topic URL -> readable label ("Association_football" -> ...)."""
    return clean_text(str(url).rstrip("/").rsplit("/", 1)[-1].replace("_", " "), 40)


def audience_evidence(
    candidates: list[dict], comments: dict[str, list[str]] | None = None,
) -> dict[str, dict]:
    """Per-creator audience evidence, keyed by channel_id. Empty fields are
    left out, so a creator with comments switched off simply has none."""
    comments = comments or {}
    out: dict[str, dict] = {}
    for c in candidates:
        cid = c["channel_id"]
        ch = c.get("channel") or {}
        vids = c.get("videos") or []
        ev: dict = {}

        com = [clean_text(t, COMMENT_CHARS) for t in (comments.get(cid)
                                                       or c.get("comments") or [])]
        com = [t for t in com if t][:COMMENTS_PER_CREATOR]
        if com:
            ev["comments"] = com

        tags: Counter = Counter()
        langs: Counter = Counter()
        for v in vids[:10]:
            sn = v.get("snippet") or {}
            for t in (sn.get("tags") or [])[:10]:
                t = clean_text(t, 30).lower()
                if t:
                    tags[t] += 1
            lang = sn.get("defaultAudioLanguage")
            if lang:
                langs[str(lang)[:5]] += 1
        if tags:
            ev["tags"] = [t for t, _ in tags.most_common(12)]
        if langs:
            ev["audio_lang"] = langs.most_common(1)[0][0]

        topics = [_topic(u) for u in
                  ((ch.get("topicDetails") or {}).get("topicCategories") or [])[:6]]
        topics = [t for t in topics if t]
        if topics:
            ev["topics"] = topics
        kw = clean_text(((ch.get("brandingSettings") or {}).get("channel") or {})
                        .get("keywords"), 160)
        if kw:
            ev["keywords"] = kw
        if ev:
            out[cid] = ev
    return out
