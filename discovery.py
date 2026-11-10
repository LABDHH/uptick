"""Discovery: decide who to look for, then find them in three places at once.

Discovery decides everything downstream: a creator who is never surfaced can
never be recommended, however good the scoring. So instead of a handful of
keyword guesses, discovery now works the way a media planner does:

  1. persona  — who buys this, and which KINDS of creator do those people
                watch (Agent 12, from the brief and the market research)
  2. sources  — three, run side by side:
                  * trending charts for the market and the archetypes'
                    categories (1 unit a page, no search call)
                  * a grounded scout that names real creators per archetype
                  * a few YouTube searches built from the archetypes
  3. screen   — resolve every named creator to a REAL channel (anything that
                does not resolve was invented, and is dropped), check channel
                stats, pre-screen a large pool for audience fit, and keep the
                creators with the most independent evidence

Every model call goes through agents.GeminiClient, so it is routed, priced and
counted against the monthly budget like every other agent.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

import agents
import cache
import prompts
import schemas
from agents import clean_text, reconcile, wrap

MAX_ARCHETYPES = 6
# One-level-up kinds of creator, used only when the archetypes run thin.
MAX_WIDER = 3
MAX_SCOUTED = 60
# Channels per pre-screen request. Each row is ~100 tokens, so forty fit
# comfortably and a typical pool of ~120 is three parallel calls.
PRESCREEN_BATCH = 40


def _h(*parts: Any) -> str:
    return hashlib.sha256(
        json.dumps(parts, sort_keys=True, default=str).encode()
    ).hexdigest()[:32]


# ---------------------------------------------------------------- persona

def _clean_profile(p: Any) -> dict:
    """Sanitize a model-written audience profile; the schema enforces enums."""
    if not isinstance(p, dict):
        return {}
    bands = []
    for b in (p.get("age_bands") or [])[:5]:
        if isinstance(b, dict) and b.get("band") in prompts.AGE_BANDS:
            w = agents.clamp01(b.get("weight"), 0.0)
            if w > 0:
                bands.append({"band": b["band"], "weight": w})
    conf = p.get("confidence") if isinstance(p.get("confidence"), dict) else {}
    return {
        "age_bands": bands,
        "life_stages": [x for x in (p.get("life_stages") or [])
                        if x in prompts.LIFE_STAGES][:4],
        "interests": [x for x in (p.get("interests") or [])
                      if x in prompts.INTEREST_TAXONOMY][:8],
        "languages": [clean_text(x, 5).lower() for x in (p.get("languages") or [])
                      if x][:4],
        "region": clean_text(p.get("region"), 2).upper(),
        "spending_power": p.get("spending_power")
        if p.get("spending_power") in ("low", "mid", "high") else "unknown",
        "confidence": {k: v for k, v in conf.items()
                       if v in ("high", "medium", "low")},
    }


def clean_persona(out: dict, valid_categories: set[str]) -> dict:
    """Everything in the persona feeds searches and four judges: sanitize all."""
    bp = out.get("brand_profile") if isinstance(out.get("brand_profile"), dict) else {}
    arcs = _clean_archetypes(out.get("archetypes"), valid_categories, MAX_ARCHETYPES)
    names = {a["name"] for a in arcs}
    wider = [a for a in _clean_archetypes(out.get("wider_archetypes"), valid_categories,
                                          MAX_WIDER) if a["name"] not in names]
    return {
        "brand_profile": {
            "tone": clean_text(bp.get("tone"), 60),
            "price_tier": bp.get("price_tier")
            if bp.get("price_tier") in ("budget", "mass", "premium", "luxury") else "",
            "values": [clean_text(x, 60) for x in (bp.get("values") or [])[:5] if x],
            "avoid": [clean_text(x, 60) for x in (bp.get("avoid") or [])[:6] if x],
            "languages": [clean_text(x, 5).lower() for x in
                          (bp.get("languages") or [])[:4] if x],
        },
        "product_moments": [clean_text(x, 100) for x in
                            (out.get("product_moments") or [])[:6] if x],
        "target_audience": _clean_profile(out.get("target_audience")),
        "archetypes": arcs,
        "wider_archetypes": wider,
    }


def _clean_archetypes(raw: Any, valid_categories: set[str], limit: int) -> list[dict]:
    arcs = []
    for a in (raw or [])[:limit]:
        if not isinstance(a, dict):
            continue
        name = clean_text(a.get("name"), 80)
        if not name:
            continue
        arcs.append({
            "name": name,
            "why_audience_matches": clean_text(a.get("why_audience_matches"), 200),
            "search_phrases": [clean_text(q, 80) for q in
                               (a.get("search_phrases") or [])[:2] if q],
            # Only real category ids reach the trending chart call.
            "category_ids": [str(c) for c in (a.get("category_ids") or [])
                             if str(c) in valid_categories][:2],
            "uses_product_words": bool(a.get("uses_product_words")),
            "known_creators": [
                {"name": clean_text(k.get("name"), 80),
                 "youtube_ref": clean_text(k.get("youtube_ref"), 120)}
                for k in (a.get("known_creators") or [])[:6]
                if isinstance(k, dict) and k.get("name")
            ],
        })
    return arcs


async def agent12_persona(
    g: "agents.GeminiClient", intent: dict, brief: str,
    market: dict | None, categories: list[dict],
) -> dict:
    """Who buys this, and which kinds of creator those buyers watch."""
    cat_lines = "\n".join(f"  {c['id']}: {c['title']}" for c in categories) or "  (none)"
    named = (market or {}).get("named_creators") or []
    payload = (
        f"Brief, as the advertiser wrote it:\n{wrap(clean_text(brief, 1200))}\n\n"
        f"Structured intent:\n{json.dumps(intent, indent=1)}\n\n"
        f"{agents.market_context(market)}"
        + (("Creators the research named:\n" + "\n".join(
            f"  - {c['name']} ({c.get('channel_hint') or 'no link'}): {c.get('why', '')}"
            for c in named) + "\n\n") if named else "")
        + f"VALID YOUTUBE CATEGORIES (use only these ids):\n{cat_lines}\n\n"
        + prompts._PROFILE_RULES
        + "\n\nBuild the brand profile, the buyer and the creator archetypes."
    )
    out = await g.call(
        "persona_builder", prompts.PERSONA_BUILDER, payload, schemas.PERSONA, 0.4,
        cache_key=_h("persona", brief, intent, market),
    )
    return clean_persona(out, {str(c["id"]) for c in categories})


# ---------------------------------------------------------------- scout

async def agent_scout(
    g: "agents.GeminiClient", intent: dict, persona: dict, wider: bool = False,
) -> tuple[list[dict], list[str]]:
    """Real creators per archetype, from the open web. One grounded call for
    all archetypes, then a cheap pass that copies names and links to JSON.

    wider=True scouts the one-level-up archetypes instead: popular mainstream
    creators whose viewers include the buyer, for when the specific
    archetypes turned up too few creators.
    """
    arcs = persona.get("wider_archetypes" if wider else "archetypes") or []
    if not arcs:
        return [], []
    key = _h("scout", intent, [a["name"] for a in arcs], wider)
    cached = cache.get_agent(key, "creator_scout")
    if cached is not None:
        cache.log_agent(g.run_id, "creator_scout", 0, "cache_hit")
        return cached.get("creators", []), cached.get("sources", [])

    band = intent.get("size_band") or "mid"
    lo, hi = {"nano": (1e3, 1e4), "micro": (1e4, 1e5), "mid": (1e5, 1e6),
              "macro": (1e6, 1e7), "mega": (1e7, 1e8)}.get(band, (1e5, 1e6))
    arc_lines = "\n".join(
        f"- {a['name']}: {a['why_audience_matches']}"
        + (f" (e.g. videos titled like: {'; '.join(a['search_phrases'])})"
           if a.get("search_phrases") else "")
        for a in arcs
    )
    payload = (
        f"Market: {clean_text(intent.get('geo_raw') or intent.get('region_code'), 60)}\n"
        f"Brand: {clean_text(intent.get('brand'), 80)} — "
        f"{clean_text(intent.get('product'), 120)}\n"
        f"Requested size: {band} tier, roughly {int(lo):,} to {int(hi):,} subscribers.\n\n"
        f"Archetypes:\n{arc_lines}\n\n"
        + ("These are BROADER, more popular kinds of creator whose viewers include "
           "the buyer. Favour well-known, consistently popular creators in this "
           "market over niche ones.\n\n" if wider else "")
        + "Find real creators for each archetype."
    )
    briefing, sources = await g.search_grounded(
        prompts.CREATOR_SCOUT, payload, 0.4, agent="creator_scout")

    names = "\n".join(f"- {a['name']}" for a in arcs)
    out = await g.call(
        "scout_structurer", prompts.SCOUT_STRUCTURER,
        f"Archetype names:\n{names}\n\nScout briefing:\n{wrap(briefing)}",
        schemas.SCOUT, 0.1,
    )
    valid_arcs = {a["name"] for a in arcs}
    creators = []
    for c in (out.get("creators") or [])[:MAX_SCOUTED]:
        if not isinstance(c, dict):
            continue
        name = clean_text(c.get("name"), 80)
        if not name:
            continue
        arc = clean_text(c.get("archetype"), 80)
        creators.append({
            "name": name,
            "youtube_ref": clean_text(c.get("youtube_ref"), 160),
            "archetype": arc if arc in valid_arcs else "",
            "evidence": clean_text(c.get("evidence"), 200),
        })
    cache.put_agent(key, "creator_scout", {"creators": creators, "sources": sources})
    return creators, sources


# ---------------------------------------------------------------- pre-screen

async def agent_prescreen(
    g: "agents.GeminiClient", rows: list[dict], persona: dict | None, intent: dict,
) -> dict[str, dict]:
    """Keep or drop each channel in a large pool, on cheap fields only.

    rows: [{channel_id, title, description, topics, keywords, recent_titles,
    subscribers}]. Returns {channel_id: {keep, archetype, reason}}; a channel
    missing from the answer is treated as kept by the caller.
    """
    if not rows:
        return {}
    from relevance import persona_context  # local: relevance imports agents only

    ctx = persona_context(persona)
    intent_line = json.dumps(
        {k: intent.get(k) for k in ("brand", "product", "target_audience",
                                    "geo_raw", "size_band")},
        separators=(",", ":"),
    )
    batches = [rows[i:i + PRESCREEN_BATCH] for i in range(0, len(rows), PRESCREEN_BATCH)]

    async def one(batch: list[dict]) -> dict[str, dict]:
        payload = (
            f"Advertiser: {intent_line}\n\n{ctx}"
            f"Channels ({len(batch)}):\n"
            + wrap(json.dumps(batch, separators=(",", ":")))
            + "\n\nReturn one result for every channel_id above."
        )
        out = await g.call(
            "prescreener", prompts.PRESCREENER, payload, schemas.PRESCREEN, 0.1,
            cache_key=_h("prescreen", ctx, intent_line, [r["channel_id"] for r in batch]),
        )
        res = reconcile(out.get("results"), {r["channel_id"] for r in batch})
        for r in res.values():
            r["keep"] = r.get("keep") is not False
            r["archetype"] = clean_text(r.get("archetype"), 80)
            r["reason"] = clean_text(r.get("reason"), 120)
        return res

    merged: dict[str, dict] = {}
    errors: list[str] = []
    for res in await asyncio.gather(*[one(b) for b in batches], return_exceptions=True):
        if isinstance(res, Exception):
            errors.append(f"{type(res).__name__}: {res}")
        else:
            merged.update(res)
    if not merged and errors:
        raise agents.AgentFailure(f"prescreener: {errors[0]}")
    return merged


# ---------------------------------------------------------------- pure helpers

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _norm(s: str) -> str:
    return _NON_ALNUM.sub("", (s or "").lower())


def match_names(names: list[str], titles: dict[str, str]) -> dict[str, str]:
    """Bare creator names -> channel ids already in the pool, conservatively.

    Only an exact match after normalizing case and punctuation counts. A loose
    match would attach a scouted name to the wrong channel, which is worse
    than not attaching it at all.
    """
    by_norm: dict[str, str] = {}
    for cid, t in titles.items():
        n = _norm(t)
        if n and n not in by_norm:
            by_norm[n] = cid
    out: dict[str, str] = {}
    for name in names:
        n = _norm(name)
        if len(n) >= 3 and n in by_norm:
            out[name] = by_norm[n]
    return out


# Which evidence groups count as independent. A creator surfaced by the scout
# AND the trending chart is better corroborated than one matched by three
# phrasings of the same search.
SOURCE_GROUPS = {
    "paid_placement": "search",
    "category": "search",
    "audience_space": "search",
    "competitor": "search",
    "creator_name": "search",
    "trending": "trending",
    "scout": "scout",
    "research": "scout",
}


def source_groups(found_via: list[str]) -> set[str]:
    return {SOURCE_GROUPS.get(f.split(":", 1)[0], "search") for f in found_via}


def rank_key(entry: dict, trending_days: dict[str, int]) -> tuple:
    """Order the screened pool before the expensive per-creator steps."""
    groups = source_groups(entry.get("found_via") or [])
    return (
        len(groups),                               # independent corroboration
        "scout" in groups,                         # named for this audience
        len(entry.get("paid_video_ids") or []),    # proven sponsorship
        trending_days.get(entry["channel_id"], 0), # sustained popularity
        entry.get("query_hits", 0),
    )
