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
import os
import operator
from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

import agents
import cache
import metrics
import youtube
from agents import clean_text

# Enrichment and Stage B both scale with this. Twenty-four still gives the
# judges a wide field to compare within (the ranking depends on relative
# comparison) while keeping each model call small enough to return quickly.
MAX_CANDIDATES = int(os.environ.get("UPTICK_MAX_CANDIDATES", "24"))
# Search calls are the binding constraint: 100 per day, project wide.
# Four per brief keeps at least 20 briefs a day available while still covering
# the paid-placement pool plus the category, audience-space and competitor
# angles. Raise UPTICK_MAX_SEARCHES for a one-off deeper run.
# Five covers the ladder: paid-placement, two category rungs, and two
# audience-space rungs. 90/5 still leaves 18 briefs a day.
MAX_SEARCHES_PER_RUN = int(os.environ.get("UPTICK_MAX_SEARCHES", "5"))
UPLOADS_PER_CHANNEL = 20
# Concurrent playlist fetches. Generous because these cost 1 unit each and no
# search quota, but bounded so we never open forty sockets at once.
UPLOAD_CONCURRENCY = 12
DOSSIER_VIDEOS = 8


class State(TypedDict, total=False):
    brief_raw: str
    run_id: str
    region_hint: Optional[str]

    intent: Optional[dict]
    query_plan: Optional[dict]
    categories: list[dict]
    market: Optional[dict]
    market_sources: list[str]

    candidates: list[dict]
    dossier: dict
    dossier_hash: str
    trending: list[str]

    # One key per parallel node — never share.
    relevance: Optional[dict]
    audience: Optional[dict]
    safety: Optional[dict]
    sponsorship: Optional[dict]
    cultural: Optional[dict]
    cultural_sources: list[str]

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

    # ---------------------------------------------------------- discovery

    async def discover(state: State) -> dict:
        """Run every researched query, not two guesses.

        Each query carries the intent it was written for, and that intent is
        kept on the candidate: a creator found in the audience_space (comedy,
        campus, gaming) is exactly the non-obvious fit this tool exists to
        surface, and the scorer should know it was found that way.
        """
        if state.get("fatal"):
            return {}
        plan = state["query_plan"]
        market = state.get("market") or {}
        region = plan["region_code"]
        cat = plan["youtube_category_id"] or None
        lang = plan["relevance_language"]

        # Researched queries first; the mechanical plan is the fallback.
        queries: list[dict] = []
        seen_q: set[str] = set()
        for q in (market.get("search_queries") or []):
            key = q["query"].lower().strip()
            if key and key not in seen_q:
                seen_q.add(key)
                queries.append(q)
        for kw in plan["search_keywords"]:
            key = kw.lower().strip()
            if key and key not in seen_q:
                seen_q.add(key)
                queries.append({"query": kw, "intent": "category",
                                "rationale": "from the query plan"})

        # Guarantee the ladder in CODE, not just in the prompt.
        #
        # A brief for a narrow product (gelato) whose researcher only proposed
        # narrow queries will return almost nothing, and no amount of
        # downstream scoring can recover a creator that was never surfaced.
        # The buyers of most products are watching lifestyle and entertainment
        # content, not content about the product, so at least one broad search
        # always runs.
        queries = _ensure_ladder(queries, state.get("intent") or {}, market)

        # Interleave rather than truncate. A flat cut keeps whatever came first,
        # which is always the narrow category queries, so the broad searches
        # that reach the actual buyers get dropped exactly when they matter
        # most. Guarantee at least two audience-space slots.
        narrow = [q for q in queries if q.get("intent") != "audience_space"]
        broad = [q for q in queries if q.get("intent") == "audience_space"]
        keep_broad = min(len(broad), max(2, MAX_SEARCHES_PER_RUN // 2))
        keep_narrow = max(0, MAX_SEARCHES_PER_RUN - keep_broad)
        queries = narrow[:keep_narrow] + broad[:keep_broad]
        if not queries:
            return {"fatal": "No searchable terms could be derived."}

        notices: list[str] = []
        pooled: list[dict] = []
        trending: set[str] = set()
        ran = 0

        try:
            async with youtube.YouTubeClient(yt_key) as yt:
                # The paid-placement pool runs first: a creator-declared paid
                # promotion is the highest precision signal available.
                try:
                    paid = await yt.search_videos(
                        queries[0]["query"], region, cat, lang,
                        paid_placement_only=True,
                    )
                    ran += 1
                    for it in paid:
                        it["found_via"] = "paid_placement"
                    pooled += paid
                    if not paid:
                        notices.append(
                            "No creator-declared paid-promotion videos matched "
                            "this brief, so sponsorship evidence below comes "
                            "from video descriptions only."
                        )
                except youtube.SearchCapReached:
                    raise
                except Exception:
                    pass

                # Your project allows 100 search calls per MINUTE, so the
                # per-minute ceiling is not the constraint here; only the
                # daily 100 is. Running these concurrently turns eight
                # sequential round trips into roughly one.
                runnable = [
                    q for q in queries
                    if cache.can_spend(search_calls=1, units=100)
                ]
                if len(runnable) < len(queries):
                    notices.append(
                        "The daily search budget limited how many angles this "
                        "brief could cover."
                    )

                async def _one(q: dict):
                    # audience_space drops the category filter on purpose: the
                    # point is creators YouTube does not file under this product.
                    use_cat = None if q["intent"] == "audience_space" else cat
                    try:
                        hits = await yt.search_videos(
                            q["query"], region, use_cat, lang,
                            paid_placement_only=False,
                        )
                        for it in hits:
                            it["found_via"] = q["intent"]
                        return hits
                    except Exception:
                        return []

                results = await asyncio.gather(*[_one(q) for q in runnable])
                for hits in results:
                    if hits:
                        ran += 1
                        pooled += hits

                trending = await yt.trending_channels(region, cat)
        except youtube.SearchCapReached as e:
            if not pooled:
                return {"fatal": str(e)}
        except youtube.QuotaExceeded as e:
            if not pooled:
                return {"fatal": str(e)}
        except Exception as e:
            if not pooled:
                return {"fatal": f"YouTube search failed: {e}"}

        # Dedupe by channelId BEFORE enrichment, not after.
        by_channel: dict[str, dict] = {}
        for item in pooled:
            cid = item["channel_id"]
            e = by_channel.setdefault(cid, {
                "channel_id": cid,
                "channel_title": item.get("channel_title", ""),
                "search_video_ids": [], "paid_video_ids": [],
                "found_via": set(), "query_hits": 0,
            })
            if item["video_id"] not in e["search_video_ids"]:
                e["search_video_ids"].append(item["video_id"])
            if item.get("paid_placement_hit") and item["video_id"] not in e["paid_video_ids"]:
                e["paid_video_ids"].append(item["video_id"])
            e["found_via"].add(item.get("found_via", "category"))
            e["query_hits"] += 1

        for e in by_channel.values():
            e["found_via"] = sorted(e["found_via"])

        # A creator surfaced by SEVERAL different query intents is stronger
        # evidence than one that matched a single phrasing, so rank by breadth
        # before spending enrichment budget.
        ordered = sorted(
            by_channel.values(),
            key=lambda c: (len(c["paid_video_ids"]), len(c["found_via"]), c["query_hits"]),
            reverse=True,
        )[:MAX_CANDIDATES]

        if not ordered:
            return {"fatal": "No creators matched this brief.", "notices": notices}

        notices.append(
            f"Ran {ran} YouTube searches across "
            f"{len({q['intent'] for q in queries})} angles and found "
            f"{len(by_channel)} distinct creators."
        )
        return {"candidates": ordered, "trending": sorted(trending), "notices": notices}

    # ---------------------------------------------------------- enrichment

    async def enrich(state: State) -> dict:
        if state.get("fatal"):
            return {}
        cands = state["candidates"]
        ids = [c["channel_id"] for c in cands]
        try:
            async with youtube.YouTubeClient(yt_key) as yt:
                channels = await yt.channels(ids)

                # Rule 1: uploads playlist, never search.list per channel.
                # One playlist call per channel, run concurrently. These are
                # 1 unit each and do not touch the search quota at all, so the
                # only reason they were slow was that they queued.
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
                    await asyncio.gather(*[_uploads(c, p) for c, p in wanted])
                )

                all_video_ids: list[str] = []
                for c in cands:
                    all_video_ids += c["search_video_ids"]
                for v in upload_ids.values():
                    all_video_ids += v
                videos = await yt.videos(list(dict.fromkeys(all_video_ids)))
        except youtube.QuotaExceeded as e:
            return {"fatal": str(e)}
        except Exception as e:
            return {"fatal": f"YouTube enrichment failed: {e}"}

        enriched = []
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
            m = metrics.channel_metrics(ch, vids, len(paid_set), surfaced)

            elig = metrics.eligible_videos(vids)
            elig.sort(key=lambda v: v.get("snippet", {}).get("publishedAt", ""), reverse=True)
            enriched.append(
                {
                    "channel_id": cid,
                    "found_via": c.get("found_via", []),
                    "query_hits": c.get("query_hits", 0),
                    "channel": ch,
                    "metrics": m,
                    "videos": elig,
                    "paid_video_ids": list(paid_set),
                    "top_videos": [
                        {
                            "title": v.get("snippet", {}).get("title", ""),
                            "video_id": v["id"],
                            "views": v.get("statistics", {}).get("viewCount"),
                            "url": f"https://www.youtube.com/watch?v={v['id']}",
                            "paid_placement": v["id"] in paid_set,
                        }
                        for v in elig[:5]
                    ],
                }
            )

        if not enriched:
            return {"fatal": "No candidate channels could be enriched."}
        return {"candidates": enriched}

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

    def _stage_b(name: str, fn, key: str):
        async def node(state: State) -> dict:
            if state.get("fatal") or not state.get("dossier"):
                return {}
            try:
                res = await fn(g, state["dossier"], state["intent"],
                               state["dossier_hash"], state.get("market"))
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

    relevance_node = _stage_b("relevance_judge", agents.agent3_relevance, "relevance")
    audience_node = _stage_b("audience_analyst", agents.agent4_audience, "audience")
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
        failed = set(state.get("agent_failures") or [])
        band = (state.get("intent") or {}).get("size_band", "mid")
        intent = state.get("intent") or {}
        ranked, review, excluded, floor_note = metrics.score_with_fallback(
            state["candidates"],
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
        )
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
        ("strategize", strategize),
        ("discover", discover), ("enrich", enrich), ("dossier", dossier),
        ("relevance", relevance_node), ("audience", audience_node),
        ("safety", safety_node), ("sponsorship", sponsorship_node),
        ("cultural", cultural_node),
        ("score", score), ("narrate", narrate), ("audit", audit),
        ("review", review),
    ]:
        b.add_node(name, fn)

    b.add_edge(START, "interpret")
    # research and strategize both depend only on the intent, so they run
    # side by side. discover waits for both.
    b.add_edge("interpret", "research")
    b.add_edge("interpret", "strategize")
    b.add_edge("strategize", "discover")
    b.add_edge("research", "discover")
    b.add_edge("discover", "enrich")
    b.add_edge("enrich", "dossier")

    for n in ("relevance", "audience", "safety", "sponsorship", "cultural"):
        b.add_edge("dossier", n)   # fan-out
        b.add_edge(n, "score")     # fan-in — score waits for all five

    # narrate -> audit is a real dependency: the auditor checks the prose.
    # The reviewer only reads the scored rows, so it runs alongside them
    # instead of waiting, which takes its cost off the critical path.
    b.add_edge("score", "narrate")
    b.add_edge("narrate", "audit")
    b.add_edge("score", "review")
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
    """Attach prose only where the auditor passed it.

    A 'revise' verdict drops that rationale and the row renders its metric
    breakdown alone. A missing sentence is cheaper than a false one.
    """
    for row in scored:
        cid = row["channel_id"]
        r = (rationales or {}).get(cid)
        a = (audit or {}).get(cid)
        if r and a and a.get("verdict") == "pass":
            row["headline"] = r.get("headline")
            row["rationale"] = r.get("rationale")
            row["caveat"] = r.get("caveat")
            row["rationale_status"] = "verified"
        elif r and a and a.get("verdict") == "revise":
            row["rationale_status"] = "withheld_unverified"
            row["unsupported_claims"] = a.get("unsupported_claims") or []
        elif r and not audit:
            row["rationale_status"] = "withheld_unaudited"
        else:
            row["rationale_status"] = "none"
    return scored
