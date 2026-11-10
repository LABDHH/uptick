"""LangGraph state, node wiring, checkpointer.

Two structural rules (§6):
  * Separate state keys per parallel node. Two nodes writing the same key in
    one superstep raises InvalidUpdateError.
  * Every agent node catches its own exceptions and returns
    {"agent_failures": [name]}. An uncaught exception kills the whole graph —
    exactly the failure mode to avoid.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
import os
import operator
from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

import agents
import cache
import discovery
import explain
import metrics
import relevance as relevance_mod
import youtube
from agents import clean_text

# Enrichment and Stage B both scale with this. Candidates now arrive already
# screened on channel stats and audience fit, so every one of these slots goes
# to a creator who could actually be recommended.
MAX_CANDIDATES = int(os.environ.get("UPTICK_MAX_CANDIDATES", "24"))
# Search calls are the binding constraint: 100 per day, project wide.
# Three per brief: the paid-placement pool plus the two strongest archetype
# phrasings. The trending charts and the scout find creators without spending
# any, so 90/3 leaves 30 briefs a day.
MAX_SEARCHES_PER_RUN = int(os.environ.get("UPTICK_MAX_SEARCHES", "3"))
# Trending chart pages (50 videos, 1 unit each) per chart, for the market's
# general chart and each archetype category (at most TRENDING_MAX_CHARTS).
TRENDING_PAGES = int(os.environ.get("UPTICK_TRENDING_PAGES", "2"))
TRENDING_MAX_CHARTS = 6
# Viewer comments are the best public audience evidence: fetched for this
# many recent videos per creator, 1 unit each.
COMMENT_VIDEOS = int(os.environ.get("UPTICK_COMMENT_VIDEOS", "2"))
COMMENTS_PER_VIDEO = 12
# Below this many screened creators, the closest near-misses are enriched too,
# so a thin search still has something honest to show.
MIN_SCREENED = 8
# Searches only look back this far: older videos come from channels the
# dormancy rule would drop anyway.
SEARCH_LOOKBACK_DAYS = 180
# One playlist page (1 unit) holds 50, so reading 50 costs the same as 20.
# Creators who mix Shorts with long videos need the deeper sample: twenty
# recent uploads often held only one or two long videos to measure.
UPLOADS_PER_CHANNEL = 50
# A studied creator whose data rules them out (too few videos, below the reach
# floors, far outside the size band) is replaced from the screened pool before
# any judge reads them, up to this many times.
REFILL_ROUNDS = 2
# Widening: when fewer than metrics.SHORTLIST_TARGET creators clear the bar,
# search once more, one level up (broader, more popular kinds of creator whose
# viewers include the buyer). No extra search.list call: trending charts, the
# scout and the leftover screened pool only.
WIDEN_ENABLED = os.environ.get("UPTICK_WIDEN", "1") != "0"
WIDEN_CANDIDATES = int(os.environ.get("UPTICK_WIDEN_CANDIDATES", "16"))
WIDEN_TRENDING_PAGES = 4
# Mainstream categories read when the persona offers no wider archetypes:
# Entertainment, Comedy, People & Blogs.
WIDEN_FALLBACK_CATEGORIES = ["24", "23", "22"]
RESERVE_MAX = 80
# Concurrent playlist fetches. Generous because these cost 1 unit each and no
# search quota, but bounded so we never open forty sockets at once.
UPLOAD_CONCURRENCY = 12
DOSSIER_VIDEOS = 8


def _merge(a: Optional[dict], b: Optional[dict]) -> dict:
    """Reducer for per-creator results: a widened second pass ADDS creators."""
    return {**(a or {}), **(b or {})}


class State(TypedDict, total=False):
    brief_raw: str
    run_id: str
    region_hint: Optional[str]

    intent: Optional[dict]
    query_plan: Optional[dict]
    categories: list[dict]
    market: Optional[dict]
    market_sources: list[str]

    persona: Optional[dict]
    pool: list[dict]
    scouted: list[dict]
    scout_sources: Annotated[list[str], operator.add]
    trending_days: Annotated[dict, _merge]
    screened_out: Annotated[list[dict], operator.add]
    discover_error: Optional[str]

    # candidates: the batch the judges read in THIS pass.
    # studied: every enriched creator across passes, which score ranks.
    # reserve: screened creators not yet studied, best first.
    candidates: list[dict]
    studied: Annotated[list[dict], operator.add]
    reserve: list[dict]
    widen_round: int
    dossier: dict
    dossier_hash: str
    trending: list[str]

    # One key per parallel node — never share. Merged, not replaced, so the
    # widened pass adds its judgments to the first pass's.
    relevance: Annotated[dict, _merge]
    audience: Annotated[dict, _merge]
    safety: Annotated[dict, _merge]
    sponsorship: Annotated[dict, _merge]
    cultural: Annotated[dict, _merge]
    cultural_sources: Annotated[list[str], operator.add]

    agent_failures: Annotated[list[str], operator.add]

    scored: list[dict]
    review_manually: list[dict]
    excluded: list[dict]
    floor_note: Optional[dict]
    rationales: Optional[dict]
    audit: Optional[dict]
    review: Optional[dict]

    fatal: Optional[str]
    notices: Annotated[list[str], operator.add]


def _ensure_ladder(queries: list[dict], intent: dict, market: dict) -> list[dict]:
    """Make sure the query set reaches the audience, not only the product.

    Keeps every researched query, then appends broad audience-space searches if
    the set does not already contain them. Ordered so the narrow, highest
    precision queries run first and the broad ones fill the remaining budget.
    """
    broad = [q for q in queries if q.get("intent") == "audience_space"]
    if len(broad) >= 2:
        return queries

    geo = (intent.get("geo_raw") or "").strip()
    gen = intent.get("target_generation")
    angle = (market or {}).get("cultural_angle") or intent.get("cultural_angle") or ""

    # Generic lifestyle and attention spaces: where buyers of almost any
    # consumer product actually spend their watch time.
    seeds: list[str] = []
    if angle:
        seeds.append(angle)
    if geo:
        seeds += [f"day in my life {geo}", f"vlog {geo}"]
        if gen == "gen_z":
            seeds.append(f"college life {geo}")
        seeds.append(f"standup comedy {geo}")
    else:
        seeds += ["day in my life vlog", "standup comedy"]

    have = {q["query"].lower().strip() for q in queries}
    for s in seeds:
        key = s.lower().strip()
        if key and key not in have:
            have.add(key)
            queries.append({
                "query": s,
                "intent": "audience_space",
                "rationale": "broad audience space, added to reach buyers who "
                             "do not watch content about this product",
            })
            broad.append(queries[-1])
        if len(broad) >= 2:
            break
    return queries


def plan_queries(plan: dict, market: dict, persona: dict, intent: dict) -> list[dict]:
    """The keyword searches to spend, at most MAX_SEARCHES_PER_RUN - 1 (the
    paid-placement search takes one slot).

    With a persona, the searches are its archetypes' own phrasings, product
    and audience-space interleaved so a budget of two covers one of each.
    Without one, the market research queries and the mechanical plan are used,
    with the audience-space ladder guaranteed in code.
    """
    n = max(1, MAX_SEARCHES_PER_RUN - 1)
    arcs = (persona or {}).get("archetypes") or []
    if arcs:
        product, audience = [], []
        for a in arcs:
            for q in a.get("search_phrases") or []:
                row = {"query": q,
                       "intent": "category" if a.get("uses_product_words") else "audience_space",
                       "archetype": a["name"], "rationale": a.get("why_audience_matches", "")}
                (product if a.get("uses_product_words") else audience).append(row)
                break  # one phrasing per archetype; breadth beats depth here
        ordered: list[dict] = []
        for pair in zip(product, audience):
            ordered += list(pair)
        longer = product if len(product) > len(audience) else audience
        ordered += longer[min(len(product), len(audience)):]
        seen: set[str] = set()
        out = []
        for q in ordered:
            k = q["query"].lower().strip()
            if k and k not in seen:
                seen.add(k)
                out.append(q)
        if out:
            return out[:n]

    queries: list[dict] = []
    seen_q: set[str] = set()
    for q in (market.get("search_queries") or []):
        key = q["query"].lower().strip()
        # Named creators are resolved directly; a keyword search for a name
        # would spend a search call on what costs 1 unit as a handle lookup.
        if q.get("intent") == "creator_name":
            continue
        if key and key not in seen_q:
            seen_q.add(key)
            queries.append(q)
    for kw in plan.get("search_keywords") or []:
        key = kw.lower().strip()
        if key and key not in seen_q:
            seen_q.add(key)
            queries.append({"query": kw, "intent": "category",
                            "rationale": "from the query plan"})
    queries = _ensure_ladder(queries, intent, market)
    narrow = [q for q in queries if q.get("intent") != "audience_space"]
    broad = [q for q in queries if q.get("intent") == "audience_space"]
    ordered = []
    for pair in zip(narrow, broad):
        ordered += list(pair)
    longer = narrow if len(narrow) > len(broad) else broad
    ordered += longer[min(len(narrow), len(broad)):]
    return ordered[:n]


def _entry(cid: str, title: str) -> dict:
    return {
        "channel_id": cid, "channel_title": title,
        "search_video_ids": [], "paid_video_ids": [], "titles": [],
        "found_via": set(), "query_hits": 0, "archetype": "",
    }


def pool_by_channel(pool: list[dict]) -> dict[str, dict]:
    """Dedupe discovery hits by channel, BEFORE any per-channel spend.

    Trending videos are kept out of the metrics sample: a creator's single
    most popular upload would inflate their median views.
    """
    by_channel: dict[str, dict] = {}
    for item in pool:
        cid = item.get("channel_id")
        if not cid:
            continue
        e = by_channel.setdefault(cid, _entry(cid, item.get("channel_title", "")))
        via = item.get("found_via", "category")
        e["found_via"].add(via)
        if item.get("title") and len(e["titles"]) < 5:
            e["titles"].append(item["title"])
        if item.get("archetype") and not e["archetype"]:
            e["archetype"] = item["archetype"]
        if via == "trending":
            continue
        if item["video_id"] not in e["search_video_ids"]:
            e["search_video_ids"].append(item["video_id"])
        if item.get("paid_placement_hit") and item["video_id"] not in e["paid_video_ids"]:
            e["paid_video_ids"].append(item["video_id"])
        e["query_hits"] += 1
    return by_channel


STAGE_B_KEYS = {
    "relevance_judge": "relevance", "audience_analyst": "audience",
    "safety_auditor": "safety", "sponsorship_analyst": "sponsorship",
    "cultural_analyst": "cultural",
}


def _candidate(e: dict) -> dict:
    """A screened pool entry as the enrich step takes it (JSON-safe)."""
    return {
        "channel_id": e["channel_id"],
        "channel_title": e["channel_title"],
        "search_video_ids": list(e["search_video_ids"]),
        "paid_video_ids": list(e["paid_video_ids"]),
        "found_via": sorted(e["found_via"]),
        "query_hits": e["query_hits"],
        "archetype": e.get("archetype", ""),
    }


def _prescreen_row(e: dict) -> dict:
    """What the pre-screen sees: cheap fields only, ~100 tokens a channel."""
    ch = e["channel"]
    sn = ch.get("snippet") or {}
    stats = ch.get("statistics") or {}
    topics = [str(u).rstrip("/").rsplit("/", 1)[-1].replace("_", " ")
              for u in ((ch.get("topicDetails") or {}).get("topicCategories") or [])[:4]]
    try:
        subs = None if stats.get("hiddenSubscriberCount") else int(stats.get("subscriberCount"))
    except (TypeError, ValueError):
        subs = None
    return {
        "channel_id": e["channel_id"],
        "title": clean_text(sn.get("title"), 80),
        "description": clean_text(sn.get("description"), 160),
        "country": sn.get("country"),
        "subscribers": subs,
        "topics": topics,
        "keywords": clean_text(((ch.get("brandingSettings") or {}).get("channel") or {})
                               .get("keywords"), 100),
        "recent_titles": [clean_text(t, 90) for t in e.get("titles", [])[:3]],
    }


def build_graph(yt_key: str, gemini_key: str, checkpointer=None):
    g = agents.GeminiClient(gemini_key)

    # ---------------------------------------------------------- Stage A

    async def interpret(state: State) -> dict:
        g.run_id = state.get("run_id", "")
        try:
            intent = await agents.agent1_interpret(g, state["brief_raw"])
            return {"intent": intent}
        except Exception as e:
            # Agent 1 is structural: without intent there is nothing to search.
            return {"fatal": f"Could not interpret the brief: {e}"}

    async def research(state: State) -> dict:
        """Agent 10: study the brand and market before searching for anyone.

        Discovery was the weakest link: two keyword searches decided the whole
        result, and a creator they missed could never be recovered downstream.
        """
        if state.get("fatal"):
            return {}
        try:
            data, sources = await agents.agent10_market_research(
                g, state["intent"], state["brief_raw"]
            )
            return {"market": data, "market_sources": sources}
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            cache.log_agent(state.get("run_id", ""), "market_researcher", 0,
                            f"error: {detail[:300]}")
            logging.warning("market_researcher failed: %s", detail)
            return {
                "market": {}, "market_sources": [],
                "agent_failures": ["market_researcher"],
                "notices": ["Market research is unavailable, so the search used "
                            "the brief alone."],
            }

    async def strategize(state: State) -> dict:
        if state.get("fatal"):
            return {}
        intent = state["intent"]
        region = intent.get("region_code") or state.get("region_hint") or "US"
        try:
            async with youtube.YouTubeClient(yt_key) as yt:
                cats = await yt.video_categories(region)
        except (youtube.QuotaExceeded, youtube.SearchCapReached) as e:
            return {"fatal": str(e)}
        except Exception:
            cats = []

        try:
            plan = await agents.agent2_strategize(g, intent, cats)
        except Exception as e:
            # Fall back to a mechanical plan rather than failing the query.
            plan = {
                "youtube_category_id": "",
                "search_keywords": [
                    clean_text(intent.get("product_category") or intent.get("product") or "review", 80)
                ],
                "relevance_language": "en",
                "region_code": region,
                "excluded_terms": [],
            }
            return {"query_plan": plan, "categories": cats,
                    "agent_failures": ["query_strategist"],
                    "notices": ["Query strategist failed; used a fallback search plan."]}
        return {"query_plan": plan, "categories": cats}

    # ---------------------------------------------------------- persona

    async def persona(state: State) -> dict:
        """Agent 12: who buys this, and which kinds of creator they watch.

        Runs after the market research, because a buyer described without
        knowing the brand and its rivals is a guess.
        """
        if state.get("fatal"):
            return {}
        try:
            p = await discovery.agent12_persona(
                g, state["intent"], state["brief_raw"], state.get("market"),
                state.get("categories") or [],
            )
            if not p.get("archetypes"):
                raise agents.AgentFailure("persona_builder returned no archetypes")
            return {"persona": p}
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            cache.log_agent(state.get("run_id", ""), "persona_builder", 0,
                            f"error: {detail[:300]}")
            logging.warning("persona_builder failed: %s", detail)
            return {
                "persona": {},
                "agent_failures": ["persona_builder"],
                "notices": ["Buyer profiling is unavailable, so discovery used the "
                            "market research searches instead."],
            }

    # ---------------------------------------------------------- scout

    async def scout(state: State) -> dict:
        """Real creators per archetype, named from the open web."""
        if state.get("fatal"):
            return {}
        p = state.get("persona") or {}
        wide = bool(state.get("widen_round"))
        if not p.get("wider_archetypes" if wide else "archetypes"):
            return {"scouted": [], "scout_sources": []}
        try:
            creators, sources = await discovery.agent_scout(
                g, state["intent"], p, wider=wide)
            return {"scouted": creators, "scout_sources": sources}
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            cache.log_agent(state.get("run_id", ""), "creator_scout", 0,
                            f"error: {detail[:300]}")
            logging.warning("creator_scout failed: %s", detail)
            return {
                "scouted": [], "scout_sources": [],
                "agent_failures": ["creator_scout"],
                "notices": ["The creator scout is unavailable, so this run used "
                            "trending charts and YouTube search only."],
            }

    # ---------------------------------------------------------- discovery

    async def discover(state: State) -> dict:
        """Trending charts plus a few YouTube searches, side by side.

        The trending charts cost 1 unit a page and no search call, so the
        market's general chart and the chart for every archetype's category
        are all read. Searches are spent only on the paid-placement pool and
        the strongest archetype phrasings. Every hit keeps the intent it was
        found by, which the screen uses as independent evidence.
        """
        if state.get("fatal"):
            return {}
        plan = state["query_plan"]
        market = state.get("market") or {}
        p = state.get("persona") or {}
        region = plan["region_code"]
        cat = plan["youtube_category_id"] or None
        lang = plan["relevance_language"]
        # Widened pass: no search.list at all. Deeper and broader trending
        # charts (the one-level-up archetypes' categories) find the popular
        # creators; the scout names more.
        wide = bool(state.get("widen_round"))

        queries = [] if wide else plan_queries(plan, market, p, state.get("intent") or {})
        after = (datetime.now(timezone.utc) - timedelta(days=SEARCH_LOOKBACK_DAYS)) \
            .strftime("%Y-%m-%dT%H:%M:%SZ")

        notices: list[str] = []
        pooled: list[dict] = []
        trending: set[str] = set()
        ran = 0
        error: Optional[str] = None
        charted: list = []

        try:
            async with youtube.YouTubeClient(yt_key) as yt:
                async def _paid():
                    # A creator-declared paid promotion is the highest
                    # precision signal available, so it gets a search of its
                    # own, on the product query when there is one.
                    if not queries:
                        return []
                    q = next((q for q in queries if q["intent"] == "category"), queries[0])
                    try:
                        hits = await yt.search_videos(
                            q["query"], region, cat, lang,
                            paid_placement_only=True, published_after=after,
                        )
                    except youtube.SearchCapReached:
                        raise
                    except Exception:
                        return []
                    for it in hits:
                        it["found_via"] = "paid_placement"
                    return hits

                async def _one(q: dict):
                    # audience_space drops the category filter on purpose: the
                    # point is creators YouTube does not file under this product.
                    use_cat = None if q["intent"] == "audience_space" else cat
                    if not cache.can_spend(search_calls=1, units=100):
                        return None
                    try:
                        hits = await yt.search_videos(
                            q["query"], region, use_cat, lang,
                            paid_placement_only=False, published_after=after,
                        )
                    except Exception:
                        return []
                    for it in hits:
                        it["found_via"] = q["intent"]
                        if q.get("archetype"):
                            it["archetype"] = q["archetype"]
                    return hits

                if wide:
                    valid = {str(c.get("id")) for c in state.get("categories") or []}
                    wider_cats = [c for a in (p.get("wider_archetypes") or [])
                                  for c in a.get("category_ids") or []]
                    if not wider_cats:
                        wider_cats = [c for c in WIDEN_FALLBACK_CATEGORIES
                                      if not valid or c in valid]
                    charts = [None] + wider_cats
                    pages = WIDEN_TRENDING_PAGES
                else:
                    charts = [None] + [c for a in (p.get("archetypes") or [])
                                       for c in a.get("category_ids") or []]
                    if cat:
                        charts.append(cat)
                    pages = TRENDING_PAGES
                charts = list(dict.fromkeys(charts))[:TRENDING_MAX_CHARTS]

                async def _chart(cid: Optional[str]):
                    try:
                        return cid, await yt.trending_pool(region, cid, pages)
                    except youtube.QuotaExceeded:
                        raise
                    except Exception:
                        return cid, []

                paid, searched, charted = await asyncio.gather(
                    _paid(),
                    asyncio.gather(*[_one(q) for q in queries]),
                    asyncio.gather(*[_chart(c) for c in charts]),
                )
                if paid:
                    ran += 1
                    pooled += paid
                elif queries:
                    notices.append(
                        "No creator-declared paid-promotion videos matched this "
                        "brief, so sponsorship evidence comes from each "
                        "creator's own uploads and descriptions."
                    )
                if any(h is None for h in searched):
                    notices.append("The daily search budget limited how many "
                                   "angles this brief could cover.")
                for hits in searched:
                    if hits:
                        ran += 1
                        pooled += hits
                for cid, items in charted:
                    try:
                        cache.record_trending(region, cid, [i["channel_id"] for i in items])
                    except Exception:
                        pass
                    for it in items:
                        trending.add(it["channel_id"])
                        pooled.append({**it, "found_via": "trending"})
        except (youtube.SearchCapReached, youtube.QuotaExceeded) as e:
            error = str(e)
        except Exception as e:
            error = f"YouTube search failed: {e}"

        read = sum(1 for _, items in (charted if not error else []) if items)
        if not wide:
            notices.append(
                f"Ran {ran} YouTube search{'es' if ran != 1 else ''} and read {read} "
                f"trending chart{'s' if read != 1 else ''}."
            )
        trending |= set(state.get("trending") or [])
        out = {"pool": pooled, "trending": sorted(trending), "notices": notices}
        if error and not pooled and not wide:
            out["discover_error"] = error
        return out

    # ---------------------------------------------------------- screen

    async def screen(state: State) -> dict:
        """Merge every source, resolve named creators, and screen the pool.

        Runs on channel stats alone (1 unit per 50 channels), before any
        per-creator spend, so the expensive steps are only paid for creators
        who could actually be recommended.
        """
        if state.get("fatal"):
            return {}
        intent = state.get("intent") or {}
        plan = state["query_plan"]
        region = plan["region_code"]
        band = intent.get("size_band") or "mid"
        p = state.get("persona") or {}
        market = state.get("market") or {}
        notices: list[str] = []
        failures: list[str] = []
        wide = bool(state.get("widen_round"))
        limit = WIDEN_CANDIDATES if wide else MAX_CANDIDATES

        by_channel = pool_by_channel(state.get("pool") or [])
        seen: set[str] = set()
        if wide:
            # Everyone already studied or screened out stays decided; the
            # first pass's leftovers rejoin the pool beside the new finds.
            seen = {c["channel_id"] for c in state.get("studied") or []} | \
                   {e["channel_id"] for e in state.get("screened_out") or []}
            for r in state.get("reserve") or []:
                e = by_channel.setdefault(r["channel_id"],
                                          _entry(r["channel_id"], r.get("channel_title", "")))
                e["found_via"] |= set(r.get("found_via") or [])
                e["search_video_ids"] = list(dict.fromkeys(
                    e["search_video_ids"] + list(r.get("search_video_ids") or [])))
                e["paid_video_ids"] = list(dict.fromkeys(
                    e["paid_video_ids"] + list(r.get("paid_video_ids") or [])))
                e["query_hits"] += r.get("query_hits", 0)
                e["archetype"] = e["archetype"] or r.get("archetype", "")
            for cid in seen:
                by_channel.pop(cid, None)

        # Every named creator, from the scout and from the research.
        named: list[tuple[str, str, str, str]] = []
        for c in state.get("scouted") or []:
            named.append((c["name"], c.get("youtube_ref", ""), c.get("archetype", ""), "scout"))
        for a in p.get("wider_archetypes" if wide else "archetypes") or []:
            for k in a.get("known_creators") or []:
                named.append((k["name"], k.get("youtube_ref", ""), a["name"], "research"))
        if not wide:
            for c in market.get("named_creators") or []:
                named.append((c["name"], c.get("channel_hint", ""), "", "research"))

        try:
            async with youtube.YouTubeClient(yt_key) as yt:
                refs = [r for _, r, _, _ in named if r and youtube.parse_ref(r)]
                resolved = await yt.resolve_refs(refs) if refs else {}
                titles = {cid: e["channel_title"] for cid, e in by_channel.items()}
                by_name = discovery.match_names(
                    [n for n, r, _, _ in named if not resolved.get(r)], titles)
                unresolved = 0
                for name, ref, arc, via in named:
                    cid = resolved.get(ref) or by_name.get(name)
                    if not cid:
                        unresolved += 1
                        continue
                    if cid in seen:
                        continue
                    e = by_channel.setdefault(cid, _entry(cid, name))
                    e["found_via"].add(via)
                    if arc and not e.get("archetype"):
                        e["archetype"] = arc
                if named and not wide:
                    found = len(named) - unresolved
                    notices.append(
                        f"The scout and research named {len(named)} creators; "
                        f"{found} were confirmed as real YouTube channels"
                        + (f" and {unresolved} could not be verified, so they "
                           f"were left out." if unresolved else ".")
                    )
                channels = await yt.channels(list(by_channel)) if by_channel else {}
        except youtube.QuotaExceeded as e:
            return {"candidates": [], "reserve": []} if wide else {"fatal": str(e)}
        except Exception as e:
            if wide:
                logging.warning("widened screen failed: %s", e)
                return {"candidates": [], "reserve": []}
            return {"fatal": f"YouTube channel lookup failed: {e}"}

        if not by_channel:
            if wide:
                return {"candidates": [], "reserve": [], "notices": notices}
            return {"fatal": state.get("discover_error") or "No creators matched this brief.",
                    "notices": notices}

        keep: list[dict] = []
        overflow: list[tuple[dict, str]] = []
        screened_out: list[dict] = []
        for cid, e in by_channel.items():
            ch = channels.get(cid)
            if not ch:
                continue  # deleted, terminated, or never real
            e["channel"] = ch
            verdict, reason = metrics.prefilter(ch, band, intent.get("region_code"))
            if verdict == "drop":
                screened_out.append({"channel_id": cid, "channel": ch, "exclusion": reason})
            elif verdict == "overflow":
                overflow.append((e, reason))
            else:
                keep.append(e)

        try:
            tdays = cache.trending_days(region, list(by_channel))
        except Exception:
            tdays = {}

        def rank(e: dict) -> tuple:
            return discovery.rank_key(e, tdays)

        # A large pool is mostly trending noise for any one brand: pre-screen
        # it on cheap fields before spending anything per creator.
        if len(keep) > limit:
            screen_persona = p
            if wide and p.get("wider_archetypes"):
                screen_persona = {**p, "archetypes": (p.get("archetypes") or [])
                                  + p["wider_archetypes"]}
            try:
                verdicts = await discovery.agent_prescreen(
                    g, [_prescreen_row(e) for e in keep], screen_persona, intent)
            except Exception as ex:
                logging.warning("prescreener failed: %s", ex)
                verdicts = {}
                failures.append("prescreener")
                notices.append("The audience pre-screen was unavailable, so creators "
                               "were kept in order of how much evidence surfaced them.")
            kept, cut = [], []
            for e in keep:
                v = verdicts.get(e["channel_id"])
                if v and v.get("archetype") not in (None, "", "other") \
                        and not e.get("archetype"):
                    e["archetype"] = v["archetype"]
                (cut if v and not v["keep"] else kept).append((e, v))
            cut.sort(key=lambda ev: rank(ev[0]), reverse=True)
            # Never let the pre-screen starve the shortlist.
            if len(kept) < limit:
                need = limit - len(kept)
                kept += cut[:need]
                cut = cut[need:]
            keep = [e for e, _ in kept]
            for e, v in cut:
                why = (v or {}).get("reason") or "audience unlikely to be the buyer"
                screened_out.append({"channel_id": e["channel_id"], "channel": e["channel"],
                                     "exclusion": f"not this audience ({why})"})

        keep.sort(key=rank, reverse=True)
        chosen = keep[:limit]
        reserve = keep[limit:limit + RESERVE_MAX]
        overflow.sort(key=lambda er: rank(er[0]), reverse=True)
        if len(chosen) < MIN_SCREENED and not wide:
            # Too few clear the bar: carry the closest near-misses into
            # scoring, where the fallback can show them honestly labelled.
            extra = overflow[: MIN_SCREENED - len(chosen)]
            chosen += [e for e, _ in extra]
            overflow = overflow[len(extra):]
        for e, reason in overflow:
            screened_out.append({"channel_id": e["channel_id"], "channel": e["channel"],
                                 "exclusion": reason})

        if not chosen:
            if wide:
                return {"candidates": [], "reserve": [], "trending_days": tdays,
                        "screened_out": screened_out[:80], "notices": notices,
                        "agent_failures": failures}
            return {"fatal": "No creator in this space passed the screen. "
                             + (screened_out[0]["exclusion"].capitalize() + " was the "
                                "most common reason." if screened_out else ""),
                    "notices": notices}

        if wide:
            notices.append(
                f"The wider search turned up {len(by_channel)} more creators; "
                f"studied the {len(chosen)} most promising."
            )
        else:
            sources = {g_ for e in by_channel.values()
                       for g_ in discovery.source_groups(sorted(e["found_via"]))}
            notices.append(
                f"Found {len(by_channel)} distinct creators across "
                f"{len(sources)} source{'s' if len(sources) != 1 else ''} "
                f"({', '.join(sorted(sources))}); studied the {len(chosen)} best "
                f"in depth."
            )
        return {"candidates": [_candidate(e) for e in chosen],
                "reserve": [_candidate(e) for e in reserve],
                "trending_days": tdays,
                "screened_out": screened_out[:80], "notices": notices,
                "agent_failures": failures}

    # ---------------------------------------------------------- enrichment

    async def _enrich_batch(yt, cands: list[dict]) -> list[dict]:
        """Channels, recent uploads and their videos for one batch."""
        ids = [c["channel_id"] for c in cands]
        channels = await yt.channels(ids)

        # Rule 1: uploads playlist, never search.list per channel. One
        # playlist call per channel, run concurrently: 1 unit each and no
        # search quota.
        wanted = [
            (cid, ch.get("contentDetails", {})
                    .get("relatedPlaylists", {}).get("uploads"))
            for cid in ids
            if (ch := channels.get(cid))
        ]
        sem = asyncio.Semaphore(UPLOAD_CONCURRENCY)

        async def _uploads(cid: str, pl: str | None):
            async with sem:
                try:
                    return cid, await yt.recent_uploads(pl, UPLOADS_PER_CHANNEL)
                except Exception:
                    return cid, []

        upload_ids: dict[str, list[str]] = dict(
            await asyncio.gather(*[_uploads(c, p_) for c, p_ in wanted])
        )
        all_video_ids: list[str] = []
        for c in cands:
            all_video_ids += c["search_video_ids"]
        for v in upload_ids.values():
            all_video_ids += v
        videos = await yt.videos(list(dict.fromkeys(all_video_ids)))

        out = []
        for c in cands:
            cid = c["channel_id"]
            ch = channels.get(cid)
            if not ch:
                continue  # deleted/terminated between search and lookup
            # Reconcile by ID: playlistItems lists deleted videos that
            # videos.list omits entirely.
            vids = [videos[v] for v in upload_ids.get(cid, []) if v in videos]
            for v in c["search_video_ids"]:
                if v in videos and all(x["id"] != v for x in vids):
                    vids.append(videos[v])

            paid_set = set(c["paid_video_ids"])
            surfaced = len(set(c["search_video_ids"]))
            m = metrics.channel_metrics(ch, vids, len(paid_set), surfaced, paid_ids=paid_set)

            # The same sample the metrics describe: long videos, or Shorts
            # for a Shorts-first creator.
            _, sample = metrics.pick_sample(vids)
            sample.sort(key=lambda v: v.get("snippet", {}).get("publishedAt", ""), reverse=True)
            out.append({
                "channel_id": cid,
                "found_via": c.get("found_via", []),
                "query_hits": c.get("query_hits", 0),
                "archetype": c.get("archetype", ""),
                "channel": ch,
                "metrics": m,
                "videos": sample,
                "paid_video_ids": list(paid_set),
                "top_videos": [
                    {
                        "title": v.get("snippet", {}).get("title", ""),
                        "video_id": v["id"],
                        "views": v.get("statistics", {}).get("viewCount"),
                        "url": f"https://www.youtube.com/watch?v={v['id']}",
                        "paid_placement": v["id"] in paid_set or bool(
                            (v.get("paidProductPlacementDetails") or {})
                            .get("hasPaidProductPlacement")),
                    }
                    for v in sample[:5]
                ],
            })
        return out

    async def enrich(state: State) -> dict:
        """Measure the batch, and replace anyone the data rules out.

        A creator who will be excluded anyway (too few videos, below the reach
        floors, far outside the size band) is swapped for the next best from
        the screened pool BEFORE the judges read anyone, so every judged slot
        goes to a creator who could actually be recommended.
        """
        if state.get("fatal"):
            return {}
        wide = bool(state.get("widen_round"))
        batch = list(state.get("candidates") or [])
        reserve = list(state.get("reserve") or [])
        if not batch:
            return {"candidates": []} if wide else \
                {"fatal": "No candidate channels could be enriched."}
        band = (state.get("intent") or {}).get("size_band") or "mid"
        target = len(batch)
        viable: list[dict] = []
        ruled_out: list[dict] = []
        refilled = 0
        try:
            async with youtube.YouTubeClient(yt_key) as yt:
                for rnd in range(REFILL_ROUNDS + 1):
                    for e in await _enrich_batch(yt, batch):
                        (ruled_out if metrics.hard_exclusion(e, band) else viable).append(e)
                    need = target - len(viable)
                    if need <= 0 or not reserve or rnd == REFILL_ROUNDS:
                        break
                    take = need + 4  # a little over: some refills fail too
                    batch, reserve = reserve[:take], reserve[take:]
                    refilled += len(batch)
        except youtube.QuotaExceeded as e:
            if wide or viable or ruled_out:
                logging.warning("enrich stopped early: %s", e)
            else:
                return {"fatal": str(e)}
        except Exception as e:
            if not wide and not (viable or ruled_out):
                return {"fatal": f"YouTube enrichment failed: {e}"}
            logging.warning("enrich stopped early: %s", e)

        if not viable and not ruled_out:
            return {"candidates": [], "reserve": reserve} if wide else \
                {"fatal": "No candidate channels could be enriched."}

        # Judge the viable creators. When none are viable, judge the closest
        # size near-misses instead, so the below-the-bar fallback still has
        # real judgments behind it.
        judge = viable or [
            e for e in ruled_out
            if any(k in (metrics.hard_exclusion(e, band) or "")
                   for k in metrics.SIZE_EXCLUSIONS)
        ][:MIN_SCREENED]

        notices: list[str] = []
        if refilled:
            notices.append(
                f"{len(ruled_out)} studied creator{'s' if len(ruled_out) != 1 else ''} "
                f"had too little recent data or reach to recommend, so the next "
                f"best from the pool took {'their' if len(ruled_out) != 1 else 'the'} "
                f"place{'s' if len(ruled_out) != 1 else ''}."
            )

        # Viewer comments: who actually watches, in their own words. 1 unit per
        # video; a creator with comments off simply has none.
        if COMMENT_VIDEOS > 0 and judge:
            try:
                async with youtube.YouTubeClient(yt_key) as yt:
                    sem = asyncio.Semaphore(UPLOAD_CONCURRENCY)

                    async def _comments(e: dict):
                        out: list[str] = []
                        for v in e["videos"][:COMMENT_VIDEOS]:
                            async with sem:
                                try:
                                    got = await yt.top_comments(v["id"], COMMENTS_PER_VIDEO)
                                except youtube.QuotaExceeded:
                                    raise
                                except Exception:
                                    got = None
                            out += got or []
                        e["comments"] = out

                    await asyncio.gather(*[_comments(e) for e in judge])
            except youtube.QuotaExceeded:
                pass  # comments are evidence, not a requirement
            except Exception as ex:
                logging.warning("comment fetch failed: %s", ex)

        return {"candidates": judge, "studied": viable + ruled_out,
                "reserve": reserve, "notices": notices}

    # ---------------------------------------------------------- dossier

    def _num(v):
        """Keep numbers as numbers: quoted strings cost extra tokens."""
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    async def dossier(state: State) -> dict:
        """Compressed dossier, built in code before Stage B.

        Four agents receive this same payload, so raw API JSON would cost 4x
        input tokens. Target <=400 tokens per candidate.
        """
        if state.get("fatal"):
            return {}
        if not state.get("candidates"):
            # A widened pass that found nobody new: nothing for the judges.
            return {"dossier": {}, "dossier_hash": ""}
        d: dict[str, dict] = {}
        for c in state["candidates"]:
            ch = c["channel"]
            snip = ch.get("snippet", {})
            m = c["metrics"]
            paid = set(c["paid_video_ids"])
            vids = c["videos"][:DOSSIER_VIDEOS]
            d[c["channel_id"]] = {
                "channel_id": c["channel_id"],
                "title": clean_text(snip.get("title"), 100),
                "description": clean_text(snip.get("description"), 180),
                "country": snip.get("country"),
                "subscriber_count": m["subscriber_count"],
                "median_views": m["median_views"],
                "engagement_rate": (
                    round(m["engagement_rate"], 5) if m["engagement_rate"] is not None else None
                ),
                "paid_placement_hits": m["paid_placement_hits"],
                # "shorts": the videos below are Shorts, so a sponsor slot is
                # a short integration, not a host-read.
                "format": m.get("format", "long"),
                # Compact keys and shorter fields: four agents receive this
                # same payload, so every character is paid for four times.
                # §5 targets <=400 tokens per candidate.
                "videos": [
                    {
                        "t": clean_text(v.get("snippet", {}).get("title"), 110),
                        "d": clean_text(v.get("snippet", {}).get("description"), 160),
                        "views": _num(v.get("statistics", {}).get("viewCount")),
                        "likes": _num(v.get("statistics", {}).get("likeCount")),
                        "age_d": round(metrics.age_days(
                            v.get("snippet", {}).get("publishedAt")) or 0),
                        "paid": v["id"] in paid,
                    }
                    for v in vids
                ],
            }
        return {"dossier": d, "dossier_hash": agents.dossier_hash(d)}

    # ---------------------------------------------------------- Stage B
    # Four nodes, four separate state keys. Each swallows its own exception.

    def _stage_b(name: str, fn, key: str, extra=lambda state: {}):
        async def node(state: State) -> dict:
            if state.get("fatal") or not state.get("dossier"):
                return {}
            try:
                res = await fn(g, state["dossier"], state["intent"],
                               state["dossier_hash"], state.get("market"),
                               **extra(state))
                return {key: res}
            except Exception as e:
                # Record the real reason: a silent failure is undiagnosable.
                detail = f"{type(e).__name__}: {e}"
                cache.log_agent(state.get("run_id", ""), name, 0, f"error: {detail[:300]}")
                logging.warning("Stage B agent %s failed: %s", name, detail)
                return {
                    key: {},
                    "agent_failures": [name],
                    "notices": [f"{name} failed ({detail[:160]}); "
                                f"its weight was redistributed."],
                }
        return node

    # The two relevance judges read the brand and buyer; the audience analyst
    # also reads each creator's viewer comments, tags and topics.
    relevance_node = _stage_b(
        "relevance_judge", agents.agent3_relevance, "relevance",
        lambda st: {"persona_ctx": relevance_mod.persona_context(st.get("persona"))},
    )
    audience_node = _stage_b(
        "audience_analyst", agents.agent4_audience, "audience",
        lambda st: {
            "persona_ctx": relevance_mod.persona_context(st.get("persona")),
            "evidence": relevance_mod.audience_evidence(st.get("candidates") or []),
        },
    )
    safety_node = _stage_b("safety_auditor", agents.agent5_safety, "safety")
    sponsorship_node = _stage_b("sponsorship_analyst", agents.agent6_sponsorship, "sponsorship")

    async def cultural_node(state: State) -> dict:
        """Agent 9 — cultural standing from the open web.

        Runs on the enriched candidates rather than the dossier, because it
        needs names to search for, not engagement statistics.
        """
        if state.get("fatal") or not state.get("candidates"):
            return {}
        rows = [
            {
                "channel_id": c["channel_id"],
                "title": c["channel"].get("snippet", {}).get("title", ""),
                "description": c["channel"].get("snippet", {}).get("description", ""),
            }
            for c in state["candidates"]
        ]
        try:
            res, sources = await agents.agent9_cultural(
                g, rows, state["intent"], state.get("dossier_hash", "")
            )
            return {"cultural": res, "cultural_sources": sources}
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            cache.log_agent(state.get("run_id", ""), "cultural_analyst", 0,
                            f"error: {detail[:300]}")
            logging.warning("cultural_analyst failed: %s", detail)
            return {
                "cultural": {}, "cultural_sources": [],
                "agent_failures": ["cultural_analyst"],
                "notices": ["Cultural research is unavailable for this run, so "
                            "ranking used YouTube signals only."],
            }

    # ---------------------------------------------------------- scoring

    async def score(state: State) -> dict:
        if state.get("fatal"):
            return {}
        # A judge that failed in one pass but answered in the other still
        # counts for the creators it did judge.
        failed = {a for a in (state.get("agent_failures") or [])
                  if a not in STAGE_B_KEYS or not state.get(STAGE_B_KEYS[a])}
        band = (state.get("intent") or {}).get("size_band", "mid")
        intent = state.get("intent") or {}
        # Every creator studied in any pass, once each (latest wins).
        studied = list({c["channel_id"]: c for c in
                        (state.get("studied") or state.get("candidates") or [])}.values())
        ranked, review, excluded, floor_note = metrics.score_with_fallback(
            studied,
            state.get("relevance") or {},
            state.get("audience") or {},
            state.get("safety") or {},
            state.get("sponsorship") or {},
            set(state.get("trending") or []),
            band,
            failed,
            cultural=state.get("cultural") or {},
            target_generation=intent.get("target_generation"),
            region_code=intent.get("region_code"),
            target_audience=(state.get("persona") or {}).get("target_audience") or None,
            trending_days=state.get("trending_days") or {},
        )
        excluded = list(excluded) + list(state.get("screened_out") or [])
        notices: list[str] = []
        total = len(ranked) + len(review)
        if total and len(review) / total > 0.6:
            notices.append(
                "The safety auditor flagged more than 60% of candidates at high "
                "severity, which usually means it is over-triggering. Review the "
                "flagged section rather than treating the list as empty."
            )
        return {"scored": ranked, "review_manually": review,
                "excluded": excluded, "floor_note": floor_note,
                "notices": notices}

    # ---------------------------------------------------------- widen

    def after_score(state: State):
        """Widen once when too few creators clear the bar; else write up."""
        if (WIDEN_ENABLED and not state.get("fatal") and not state.get("widen_round")
                and metrics.recommendable(state.get("scored") or [])
                < metrics.SHORTLIST_TARGET):
            return "widen"
        return ["narrate", "review"]

    async def widen(state: State) -> dict:
        """Go one level up: broader, more popular kinds of creator.

        A niche product rarely has ten creators who make content about it in
        one country. Its buyers still watch something, so the second pass
        looks at the more mainstream creators those buyers also watch, and
        scores them with the same relevance gate: a popular creator only
        makes the list if their audience genuinely overlaps the buyer.
        """
        n = metrics.recommendable(state.get("scored") or [])
        names = [a["name"] for a in (state.get("persona") or {}).get("wider_archetypes") or []]
        where = ", ".join(names) if names else "popular creators in this market"
        return {
            "widen_round": 1,
            "notices": [f"Only {n} creator{'s' if n != 1 else ''} cleared the bar on the "
                        f"first pass, so the search widened one level up to: {where}."],
        }

    # ---------------------------------------------------------- Stage D/E

    async def narrate(state: State) -> dict:
        if state.get("fatal") or not state.get("scored"):
            return {}
        top, _ = metrics.shortlist(state["scored"])
        try:
            return {"rationales": await agents.agent7_narrate(g, top, state["intent"])}
        except Exception as e:
            return {"rationales": {}, "agent_failures": ["rationale_writer"],
                    "notices": [f"Rationale writer failed ({type(e).__name__}); "
                                f"showing metric breakdowns only."]}

    async def audit(state: State) -> dict:
        if state.get("fatal") or not state.get("rationales"):
            return {}
        top, _ = metrics.shortlist(state["scored"])
        try:
            return {"audit": await agents.agent8_audit(g, top, state["rationales"])}
        except Exception as e:
            # Never ship unaudited claims: suppress all prose instead.
            return {"audit": {}, "rationales": {}, "agent_failures": ["output_auditor"],
                    "notices": [f"Output auditor failed ({type(e).__name__}); "
                                f"rationales suppressed so no unverified claim ships."]}

    async def review(state: State) -> dict:
        """Agent 11: the last check, on the shortlist as a whole."""
        if state.get("fatal") or not state.get("scored"):
            return {}
        # Scored rows only: this now runs beside narrate/audit, so the
        # rationales are deliberately not awaited.
        rows = list(state.get("scored") or [])
        try:
            return {"review": await agents.agent11_review_shortlist(
                g, rows, state["intent"], state.get("market"))}
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            cache.log_agent(state.get("run_id", ""), "shortlist_reviewer", 0,
                            f"error: {detail[:300]}")
            logging.warning("shortlist_reviewer failed: %s", detail)
            return {"review": {}, "agent_failures": ["shortlist_reviewer"]}

    # ---------------------------------------------------------- wiring

    b = StateGraph(State)
    for name, fn in [
        ("interpret", interpret), ("research", research),
        ("strategize", strategize), ("persona", persona),
        ("discover", discover), ("scout", scout), ("screen", screen),
        ("enrich", enrich), ("dossier", dossier),
        ("relevance", relevance_node), ("audience", audience_node),
        ("safety", safety_node), ("sponsorship", sponsorship_node),
        ("cultural", cultural_node),
        ("score", score), ("widen", widen), ("narrate", narrate),
        ("audit", audit), ("review", review),
    ]:
        b.add_node(name, fn)

    b.add_edge(START, "interpret")
    # research and strategize both depend only on the intent, so they run
    # side by side. The persona needs both (the research, and the real
    # category list strategize fetched). Then the three sources run side by
    # side (trending + search in discover, the scout on its own) and the
    # screen merges them.
    b.add_edge("interpret", "research")
    b.add_edge("interpret", "strategize")
    b.add_edge("research", "persona")
    b.add_edge("strategize", "persona")
    b.add_edge("persona", "discover")
    b.add_edge("persona", "scout")
    b.add_edge("discover", "screen")
    b.add_edge("scout", "screen")
    b.add_edge("screen", "enrich")
    b.add_edge("enrich", "dossier")

    for n in ("relevance", "audience", "safety", "sponsorship", "cultural"):
        b.add_edge("dossier", n)   # fan-out
        b.add_edge(n, "score")     # fan-in — score waits for all five

    # narrate -> audit is a real dependency: the auditor checks the prose.
    # The reviewer only reads the scored rows, so it runs alongside them
    # instead of waiting, which takes its cost off the critical path.
    #
    # Too few creators cleared the bar? Widen once, one level up, and run the
    # same enrich -> judge -> score path on the new finds (scored together
    # with the first pass's creators), then write up.
    b.add_conditional_edges("score", after_score, ["widen", "narrate", "review"])
    b.add_edge("widen", "discover")
    b.add_edge("widen", "scout")
    b.add_edge("narrate", "audit")
    b.add_edge("audit", END)
    b.add_edge("review", END)

    return b.compile(checkpointer=checkpointer)


def graph_apply_audit_preview(state) -> list[dict]:
    """Rows as the user will see them, for the reviewer to judge."""
    return apply_audit(
        list(state.get("scored") or []),
        state.get("rationales") or {},
        state.get("audit") or {},
    )


def apply_audit(scored: list[dict], rationales: dict, audit: dict) -> list[dict]:
    """Every row gets a reason and its supporting numbers.

    The writer's prose is used only where the auditor passed every claim in
    it. Anywhere else (a claim it could not confirm, the writer or auditor
    down) the row gets explain.summary instead: a plain reason written in code
    from the row's own numbers, so it cannot be wrong and never leaves a
    creator on the list without saying why.
    """
    shown, _ = metrics.shortlist(scored)
    tiers = {r["channel_id"]: r.get("tier") for r in shown}
    for row in scored:
        cid = row["channel_id"]
        if tiers.get(cid):
            row["tier"] = tiers[cid]
        row["key_numbers"] = explain.key_numbers(row)
        r = (rationales or {}).get(cid)
        a = (audit or {}).get(cid)
        if r and a and a.get("verdict") == "pass" and r.get("rationale"):
            row["headline"] = r.get("headline")
            row["rationale"] = r.get("rationale")
            row["caveat"] = r.get("caveat")
            row["rationale_status"] = "verified"
        else:
            row.update(explain.summary(row))
            row["rationale_status"] = "summary"
            if r and a and a.get("verdict") == "revise":
                # Kept for diagnosis; the interface does not show it.
                row["unsupported_claims"] = a.get("unsupported_claims") or []
    return scored
