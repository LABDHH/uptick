"""The Gemini agents: model routing, injection defenses, ID validation.

Two things in this module are non-negotiable (§11 "never cut"):
  * channel-ID validation — every agent response is reconciled against the
    input candidate set, and unknown IDs are dropped. This catches
    hallucination generally, not just malice.
  * input sanitization and delimiting of everything YouTube-derived.

The key is on a paid tier, so calls are not paced per minute. Spend is the
limit instead: every response's cost is recorded in budget.py, and the API
stops admitting searches when the month's budget runs low.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import unicodedata
from typing import Any, Callable, Optional

from google import genai
from google.genai import types

import budget
import cache
import explain
import metrics
import prompts
import schemas

# ---------------------------------------------------------------- models
#
# Two models. 3.8 Flash reasons better and costs ~5x more per token; Flash-Lite
# is cheap and fast. 3.8 goes only where judgement is hard AND the agent makes
# ONE call per search (so its cost stays small and fixed) AND the result shapes
# everything downstream or is what the user reads. The batched Stage B judges
# read ~18k tokens each, so on 3.8 they would double the cost of a search; they
# stay on Lite. If the budget grows, relevance_judge and audience_analyst are
# the first to move up.
FLASH = os.environ.get("UPTICK_MODEL_FLASH", "gemini-3.8-flash")
LITE = os.environ.get("UPTICK_MODEL_LITE", "gemini-3.5-flash-lite")

# agent name -> (model, thinking level). Thinking tokens bill as output, so
# the level is set per agent rather than left at the model default.
#
# Temperature: Google advises leaving Gemini 3 thinking models at their
# default (1.0); lower values can cause looping. 3.8 calls therefore ignore the
# per-agent temp. Lite keeps the low temps it has been tuned and tested with.
AGENT_MODELS: dict[str, tuple[str, str]] = {
    # Stage A: one call each, but everything downstream inherits them.
    "brief_interpreter": (LITE, "low"),       # extraction; fields re-validated
    "market_search": (FLASH, "medium"),       # grounded: discovery's weak link
    "market_researcher": (LITE, "minimal"),   # structures the briefing to JSON
    "query_strategist": (LITE, "low"),        # category id re-validated
    # Stage B: batched over the candidate set.
    # Discovery: who to look for and where. Per-search cost matters at a $1
    # monthly budget, so these are one call each (the scout is one grounded
    # call for every archetype together) and stay on Lite.
    "persona_builder": (LITE, "medium"),      # buyer + archetypes; shapes all discovery
    "creator_scout": (LITE, "low"),           # grounded: finds creators per archetype
    "scout_structurer": (LITE, "minimal"),    # copies names and links to JSON
    "prescreener": (LITE, "low"),             # fast keep/drop over a big pool
    "relevance_judge": (LITE, "low"),         # product + brand fit, quoted evidence
    "audience_analyst": (LITE, "low"),        # audience profile from comments
    "safety_auditor": (LITE, "low"),
    "sponsorship_analyst": (LITE, "minimal"), # mostly reads paid_placement_hits
    "cultural_search": (LITE, "low"),         # grounded; 12 creators, context only
    "cultural_analyst": (LITE, "minimal"),    # structures the briefing to JSON
    # Stage C: what the user reads.
    "rationale_writer": (FLASH, "low"),       # prose; auditor checks the numbers
    "output_auditor": (LITE, "medium"),       # mechanical, but minimal waves errors through
    "shortlist_reviewer": (FLASH, "medium"),  # judges the whole list
}
_DEFAULT_ROUTE = (LITE, "low")


def route(agent_name: str) -> tuple[str, str]:
    model, level = AGENT_MODELS.get(agent_name, _DEFAULT_ROUTE)
    # Per-search ceiling: past it, the rest of this search runs on Lite.
    if model != LITE and budget.over_run_ceiling():
        return LITE, level
    return model, level


def _thinking(level: str) -> types.ThinkingConfig:
    return types.ThinkingConfig(thinking_level=types.ThinkingLevel(level.upper()))


def _transient(e: Exception) -> bool:
    """Overload, rate limit or a 5xx: worth retrying, and worth falling back
    from 3.8 to Lite for. A bad reply or a 4xx about the request is not."""
    if isinstance(e, (AgentFailure, ValueError)):  # incl. truncation, bad JSON
        return False
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    if isinstance(code, int):
        return code == 429 or code >= 500
    return bool(_TRANSIENT_RE.search(str(e)))


_TRANSIENT_RE = re.compile(
    r"\b(429|50[0234])\b|RESOURCE_EXHAUSTED|UNAVAILABLE|DEADLINE_EXCEEDED|overloaded"
)


# The six-field agents (safety, sponsorship) emit the most tokens per
# candidate. At 25 candidates an 8k ceiling truncates them mid-JSON, which is
# what made those two agents fail while the others passed.
MAX_OUTPUT_TOKENS = 32768

# Candidates per Stage B request. Splitting a large set costs an extra call
# but keeps every response well inside the ceiling.
# Measured: a full dossier runs ~770 tokens/candidate of INPUT, and the
# six-field agents emit ~120 tokens/candidate of OUTPUT. Twelve keeps both
# sides comfortable while still judging enough candidates together for the
# relative comparison to mean something.
STAGE_B_BATCH = int(os.environ.get("UPTICK_BATCH", "12"))

# Rate limiting: a sliding window, not a fixed sleep between calls.
#
# The old throttle slept 5s after EVERY call, which serialized the whole
# pipeline: thirteen calls meant sixty-five seconds of waiting even though the
# limit allows fifteen per minute. A window lets calls that fit in the budget
# run concurrently and only blocks when the minute is genuinely full, which is
# what the limit actually says.
#
# OFF by default: the key is on a paid tier, where the per-minute limit is far
# above what one search sends. Set UPTICK_GEMINI_RPM to turn it back on (e.g.
# 15 for a free-tier key).
GEMINI_RPM = int(os.environ.get("UPTICK_GEMINI_RPM", "0"))
# Leave one slot spare so a repair retry never tips us over the limit.
_WINDOW = 60.0
_SAFETY = 1

_call_times: "dict[int, list[float]]" = {}
_locks: "dict[int, asyncio.Lock]" = {}


def _loop_key() -> int:
    """Streamlit and uvicorn both create loops per request in some modes, and
    an asyncio.Lock binds to the loop that first awaits it."""
    return id(asyncio.get_running_loop())


def _loop_lock() -> asyncio.Lock:
    key = _loop_key()
    lock = _locks.get(key)
    if lock is None:
        for k in [k for k in _locks if k != key]:
            _locks.pop(k, None)
            _call_times.pop(k, None)
        lock = _locks[key] = asyncio.Lock()
    return lock


async def throttle() -> None:
    """Admit a call if the last minute has room, else wait only as long as needed.

    Concurrent callers each take a slot, so agents that fan out in the graph
    genuinely overlap instead of queueing behind a fixed sleep.
    """
    if GEMINI_RPM <= 0:
        return
    while True:
        async with _loop_lock():
            key = _loop_key()
            now = time.monotonic()
            times = [t for t in _call_times.get(key, []) if now - t < _WINDOW]
            _call_times[key] = times

            budget = max(1, GEMINI_RPM - _SAFETY)
            if len(times) < budget:
                times.append(now)
                return

            # The window is full: wait until the oldest call ages out.
            wait = _WINDOW - (now - times[0]) + 0.05
        await asyncio.sleep(max(0.05, wait))


# ---------------------------------------------------------------- sanitization

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WS = re.compile(r"\s+")
# Delimiter-forging attempt: a creator writing our own closing tag in a
# description to escape the data block.
_FENCE = re.compile(r"</?candidate_data>", re.I)


def clean_text(s: Any, limit: int = 200) -> str:
    """Strip control chars, normalize whitespace, truncate.

    Truncation is itself a defense: most injection payloads sit further down a
    long description than the first 200 characters.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = _CTRL.sub(" ", s)
    s = _FENCE.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    if len(s) > limit:
        s = s[:limit].rstrip() + "…"
    return s


def wrap(payload: str) -> str:
    """Every YouTube-derived payload goes inside explicit delimiters."""
    return f"<candidate_data>\n{payload}\n</candidate_data>"


# ---------------------------------------------------------------- validation

def clamp01(v: Any, default: float = 0.5) -> float:
    """Clamp to [0,1]; reject non-numeric. Never let a bad float into scoring."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf
        return default
    return max(0.0, min(1.0, f))


def reconcile(
    rows: Any,
    valid_ids: set[str],
    float_fields: tuple[str, ...] = (),
) -> dict[str, dict]:
    """Cross-cutting rules 4, 5 and 6, in one place.

    Validates every channel_id against the input set and DROPS unknowns; clamps
    floats; keys by ID so response length never has to equal input length.
    """
    out: dict[str, dict] = {}
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        cid = row.get("channel_id")
        if not isinstance(cid, str) or cid not in valid_ids:
            continue  # hallucinated or echoed-back id
        for f in float_fields:
            row[f] = clamp01(row.get(f))
        out[cid] = row
    return out


# ---------------------------------------------------------------- client

class GeminiClient:
    def __init__(self, api_key: str, run_id: str = ""):
        self.client = genai.Client(api_key=api_key)
        self.run_id = run_id

    async def call(
        self,
        agent_name: str,
        system: str,
        payload: str,
        schema: dict,
        temp: float,
        cache_key: Optional[str] = None,
    ) -> dict:
        """One agent call: cache → throttle → generate → parse → repair once."""
        if cache_key:
            hit = cache.get_agent(cache_key, agent_name)
            if hit is not None:
                cache.log_agent(self.run_id, agent_name, 0, "cache_hit")
                return hit

        started = time.monotonic()
        try:
            result = await self._generate(
                system, payload, schema, temp, agent=agent_name
            )
        except OutputTruncated:
            # Never retry this verbatim — the same request truncates again.
            # The caller (call_batched) splits the candidate set instead.
            cache.log_agent(
                self.run_id, agent_name,
                int((time.monotonic() - started) * 1000), "truncated",
            )
            raise
        except (json.JSONDecodeError, AgentFailure) as first:
            # One repair retry. It costs money, so exactly one.
            try:
                result = await self._generate(
                    system,
                    payload
                    + "\n\nYour previous reply could not be parsed. Reply with "
                      "valid JSON matching the schema, and nothing else — no "
                      "markdown fence, no commentary.",
                    schema,
                    temp,
                    agent=agent_name,
                )
            except Exception as e:
                cache.log_agent(
                    self.run_id, agent_name,
                    int((time.monotonic() - started) * 1000),
                    f"json_error: {str(first)[:120]}",
                )
                raise AgentFailure(f"{agent_name}: unparseable after repair ({e})") from e
        except Exception as e:
            cache.log_agent(
                self.run_id, agent_name,
                int((time.monotonic() - started) * 1000),
                f"error: {type(e).__name__}: {str(e)[:200]}",
            )
            raise AgentFailure(f"{agent_name}: {type(e).__name__}: {e}") from e

        cache.log_agent(
            self.run_id, agent_name,
            int((time.monotonic() - started) * 1000), "ok",
        )
        if cache_key:
            cache.put_agent(cache_key, agent_name, result)
        return result

    async def _generate(
        self, system: str, payload: str, schema: dict, temp: float,
        agent: str = "",
    ) -> dict:
        """Route to the agent's model; retry transient errors; and if 3.8 is
        still overloaded or out of quota, answer on Lite rather than fail."""
        model, level = route(agent)
        tried: list[str] = []
        for m in ([model, LITE] if model != LITE else [model]):
            try:
                return await self._generate_on(m, level, system, payload, schema, temp)
            except Exception as e:
                tried.append(f"{m}: {type(e).__name__}: {str(e)[:120]}")
                if not _transient(e) or m == LITE:
                    raise
        raise AgentFailure("; ".join(tried))  # unreachable, kept for type checkers

    async def _generate_on(
        self, model: str, level: str, system: str, payload: str,
        schema: dict, temp: float, attempts: int = 3,
    ) -> dict:
        """One model, with backoff on transient errors (1s, 2s)."""
        for attempt in range(attempts):
            try:
                return await self._generate_once(
                    model, level, system, payload, schema, temp
                )
            except Exception as e:
                if not _transient(e) or attempt == attempts - 1:
                    raise
                await asyncio.sleep(2 ** attempt)
        raise AssertionError("unreachable")

    async def _generate_once(
        self, model: str, level: str, system: str, payload: str,
        schema: dict, temp: float,
    ) -> dict:
        await throttle()
        resp = await self.client.aio.models.generate_content(
            model=model,
            contents=payload,
            config=types.GenerateContentConfig(
                system_instruction=system,
                # 3.8 runs at its default temperature; see AGENT_MODELS.
                temperature=None if model == FLASH else temp,
                thinking_config=_thinking(level),
                response_mime_type="application/json",
                response_schema=schema,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                # These agents use no tools; AFC only adds request overhead.
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        )
        # Paid for whether or not it parses, so record it first.
        await budget.record(budget.cost_of_response(model, resp))

        # A truncated response is the failure mode that matters here: the
        # six-field agents emit the most tokens per candidate, so they hit the
        # output ceiling first and come back as unparseable half-JSON. Detect
        # it explicitly — json.loads would otherwise report a confusing syntax
        # error at some arbitrary offset.
        finish = None
        blocked = None
        try:
            cand = (resp.candidates or [None])[0]
            finish = getattr(cand, "finish_reason", None)
            blocked = getattr(
                getattr(resp, "prompt_feedback", None), "block_reason", None
            )
        except Exception:
            pass

        if blocked:
            raise AgentFailure(f"prompt blocked by safety filter ({blocked})")

        text = (resp.text or "").strip()

        if finish is not None and str(finish).endswith("MAX_TOKENS"):
            raise OutputTruncated(
                f"response hit the {MAX_OUTPUT_TOKENS}-token output ceiling"
            )
        if not text:
            raise AgentFailure(f"empty response from model (finish={finish})")
        return json.loads(text)


    async def search_grounded(
        self, system: str, payload: str, temp: float,
        attempts: int = 3, agent: str = "",
    ) -> tuple[str, list[str]]:
        """A grounded call: Google Search enabled, free-text out.

        Search grounding and response_schema cannot be relied on together, so
        this returns prose and a separate structuring pass turns it into JSON.
        Returns (text, source_urls).

        Retried with backoff. Grounded calls reach out to Search and are
        markedly flakier than a plain generate: a single 503 used to take out
        the market researcher and the cultural analyst together, which is the
        pair whose absence the user actually sees ("these agents failed").
        The last attempt runs on Lite when the agent is routed to 3.8, so an
        overloaded 3.8 degrades the research instead of losing it.
        """
        model, level = route(agent)
        last: Exception | None = None
        for attempt in range(attempts):
            m = LITE if (attempt == attempts - 1 and attempt > 0) else model
            try:
                return await self._search_grounded_once(
                    system, payload, temp, model=m, level=level
                )
            except Exception as e:
                last = e
                if attempt < attempts - 1:
                    # 1s, 2s: long enough to clear a transient upstream blip,
                    # short enough that the user is not left waiting.
                    await asyncio.sleep(2 ** attempt)
        raise AgentFailure(
            f"grounded search failed after {attempts} attempts: "
            f"{type(last).__name__}: {last}"
        ) from last

    async def _search_grounded_once(
        self, system: str, payload: str, temp: float,
        model: str = "", level: str = "low",
    ) -> tuple[str, list[str]]:
        model = model or LITE
        await throttle()
        resp = await self.client.aio.models.generate_content(
            model=model,
            contents=payload,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=None if model == FLASH else temp,
                thinking_config=_thinking(level),
                max_output_tokens=MAX_OUTPUT_TOKENS,
                tools=[types.Tool(google_search=types.GoogleSearch())],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        )
        await budget.record(
            budget.cost_of_response(model, resp), budget.grounded_queries(resp)
        )
        text = (resp.text or "").strip()
        if not text:
            raise AgentFailure("grounded search returned nothing")

        # Surface the sources so claims are auditable in the UI.
        urls: list[str] = []
        try:
            for cand in resp.candidates or []:
                gm = getattr(cand, "grounding_metadata", None)
                for chunk in (getattr(gm, "grounding_chunks", None) or []):
                    web = getattr(chunk, "web", None)
                    uri = getattr(web, "uri", None)
                    title = getattr(web, "title", None)
                    if uri:
                        urls.append(title or uri)
        except Exception:
            pass
        return text, list(dict.fromkeys(urls))[:12]


class AgentFailure(Exception):
    """An agent failed. The graph catches this and degrades — never crashes."""


class OutputTruncated(AgentFailure):
    """The model hit the output ceiling. Retrying verbatim cannot help: the
    caller must split the candidate set instead."""


def dossier_hash(dossier: dict) -> str:
    """Key agent cache on the candidate set, not the brief.

    Two differently worded briefs that surface the same creators should hit
    cache — every hit is a search the monthly budget does not pay for.
    """
    return hashlib.sha256(
        json.dumps(dossier, sort_keys=True, default=str).encode()
    ).hexdigest()[:32]


async def call_batched(
    g: "GeminiClient",
    agent_name: str,
    system: str,
    schema: dict,
    temp: float,
    dossier: dict,
    build_payload,
    dhash: str,
    float_fields: tuple = (),
) -> dict[str, dict]:
    """Run one Stage B agent over the candidate set, splitting if needed.

    All candidates go in ONE call when they fit, because isolated per-candidate
    ratings drift and become non-comparable — the ranking depends on them being
    judged against each other. Splitting is the fallback for oversized sets
    only, and the batch stays large enough (STAGE_B_BATCH) that comparison
    within it is still meaningful.
    """
    ids = list(dossier)
    batches = [ids] if len(ids) <= STAGE_B_BATCH else [
        ids[i : i + STAGE_B_BATCH] for i in range(0, len(ids), STAGE_B_BATCH)
    ]

    merged: dict[str, dict] = {}
    errors: list[str] = []

    async def run_batch(batch_ids: list[str]) -> None:
        """One batch, splitting itself in half if the response truncates.

        Recursive rather than iterative so that a split runs its two halves
        concurrently too: the old loop awaited every batch in turn, which made
        an agent with five batches take five times as long as it needed to.
        """
        sub = {k: dossier[k] for k in batch_ids}
        key = f"{dhash}:{len(batch_ids)}:{batch_ids[0][-6:]}" if dhash else None
        try:
            out = await g.call(agent_name, system, build_payload(sub), schema, temp, key)
            merged.update(reconcile(out.get("results"), set(sub), float_fields))
        except OutputTruncated:
            if len(batch_ids) > 1:
                mid = len(batch_ids) // 2
                await asyncio.gather(
                    run_batch(batch_ids[:mid]), run_batch(batch_ids[mid:])
                )
            else:
                errors.append(f"{batch_ids[0]}: response truncated even alone")
        except AgentFailure as e:
            errors.append(str(e))

    # All batches at once. The rate limiter is the only thing that should
    # serialize these, and it admits everything that fits inside the minute.
    await asyncio.gather(*[run_batch(b) for b in batches])

    # Partial success is still success: a batch that failed simply contributes
    # no rows, and score_candidates treats a missing row as neutral.
    if not merged and errors:
        raise AgentFailure(f"{agent_name}: all batches failed — {errors[0]}")
    return merged


# ================================================================ the 8 agents
#
# Every agent receives the WHOLE candidate set in one call. Never one call per
# candidate: isolated ratings drift and become non-comparable, which silently
# breaks the ranking — the one thing this product does.


async def agent1_interpret(g: GeminiClient, brief: str) -> dict:
    payload = (
        "Advertiser brief:\n"
        + wrap(clean_text(brief, limit=2000))
        + "\n\nExtract the advertising intent."
    )
    out = await g.call("brief_interpreter", prompts.BRIEF_INTERPRETER,
                       payload, schemas.INTENT, 0.2)
    gran = out.get("geo_granularity")
    if gran not in ("country", "sub_country", "none"):
        out["geo_granularity"] = "none"
    if out.get("size_band") not in ("nano", "micro", "mid", "macro", "mega"):
        out["size_band"] = "mid"
    if out.get("target_generation") not in (
        "gen_z", "millennial", "mixed", "older", "unclear"
    ):
        out["target_generation"] = "unclear"
    out["cultural_angle"] = clean_text(out.get("cultural_angle"), 120)
    rc = out.get("region_code")
    out["region_code"] = rc.upper() if isinstance(rc, str) and len(rc) == 2 else None
    return out


async def agent2_strategize(
    g: GeminiClient, intent: dict, categories: list[dict]
) -> dict:
    """Input must include the real videoCategories response so the agent
    cannot invent an id. We validate the answer against it regardless."""
    cat_lines = "\n".join(f"  {c['id']}: {c['title']}" for c in categories)
    payload = (
        f"Advertising intent:\n{json.dumps(intent, indent=2)}\n\n"
        f"Valid YouTube video categories for this region "
        f"(you MUST pick one of these ids, or \"\"):\n{cat_lines}\n\n"
        "Produce the search parameters."
    )
    out = await g.call("query_strategist", prompts.QUERY_STRATEGIST,
                       payload, schemas.QUERY_PLAN, 0.3)

    valid = {str(c["id"]) for c in categories}
    cid = str(out.get("youtube_category_id", "")).strip()
    # Fall back to unfiltered search on mismatch rather than sending a bad id.
    out["youtube_category_id"] = cid if cid in valid else ""

    kws = [clean_text(k, 80) for k in (out.get("search_keywords") or []) if k]
    out["search_keywords"] = [k for k in kws if k][:5]
    if not out["search_keywords"]:
        out["search_keywords"] = [
            clean_text(intent.get("product_category") or intent.get("product") or "review", 80)
        ]

    rl = out.get("relevance_language")
    out["relevance_language"] = rl if isinstance(rl, str) and 2 <= len(rl) <= 5 else "en"
    rc = out.get("region_code")
    if not (isinstance(rc, str) and len(rc) == 2):
        rc = intent.get("region_code") or "US"
    out["region_code"] = rc.upper()
    out["excluded_terms"] = [clean_text(t, 40) for t in (out.get("excluded_terms") or [])][:10]
    return out


# Compact field names keep the shared Stage B payload small; four agents each
# pay for it, so the legend is far cheaper than verbose keys repeated 200x.
FIELD_LEGEND = (
    "Each candidate: title, description, country, subscriber_count, "
    "median_views, engagement_rate, paid_placement_hits (videos YouTube "
    "confirmed as creator-declared paid promotions), and videos[] where "
    "t=title, d=description, views, likes, age_d=days since upload, "
    "paid=came from the paid-promotion search. When present: comments "
    "(a sample of viewer comments), tags, topics, keywords (the creator's "
    "own) and audio_lang."
)


def market_context(market: dict | None) -> str:
    """The brand and market findings, rendered once for every Stage B agent.

    Without this the judges only ever see the product category, so they score
    category fit. With it they can score fit with THIS brand: its positioning,
    its story, and who its rivals already sponsor.
    """
    if not market:
        return ""
    bits: list[str] = []
    if market.get("brand_known"):
        for k, label in (
            ("brand_profile", "The brand"),
            ("brand_positioning", "Positioning"),
            ("brand_story_angle", "Story angle"),
        ):
            if market.get(k):
                bits.append(f"{label}: {market[k]}")
    else:
        bits.append(
            "This brand has no public footprint we could find, which is normal "
            "for a new or private label. Reason from the category instead."
        )
    if market.get("known_competitors"):
        bits.append("Competitors: " + ", ".join(market["known_competitors"]))
    if market.get("competitor_creator_tactics"):
        bits.append("What rivals do with creators: " + market["competitor_creator_tactics"])
    if market.get("category_landscape"):
        bits.append("Category now: " + market["category_landscape"])
    if market.get("audience_watch_habits"):
        bits.append("These buyers watch: " + "; ".join(market["audience_watch_habits"]))
    if not bits:
        return ""
    return "BRAND AND MARKET RESEARCH\n" + "\n".join(f"- {b}" for b in bits) + "\n\n"


def _stage_b_payload(dossier: dict, intent: dict, extra: str = "") -> str:
    return (
        f"Advertiser intent:\n{json.dumps(intent, separators=(',', ':'))}\n\n"
        f"{extra}{FIELD_LEGEND}\n\n"
        f"Candidates ({len(dossier)}):\n"
        # separators= strips the whitespace that indent= would add to every
        # one of ~200 nested video objects.
        + wrap(json.dumps(list(dossier.values()), separators=(",", ":")))
        + "\n\nReturn one result object for every channel_id above."
    )


_LEVELS = {
    "product_fit": ("natural", "plausible", "forced", "none"),
    "brand": ("match", "neutral", "clash"),
    "buyer_overlap": ("strong", "good", "partial", "weak"),
    "match_type": ("direct", "adjacent", "lifestyle", "weak"),
}


def _keyed(dhash: str, *parts: str) -> str:
    """Relevance depends on the brand as well as the creators, so its cache key
    must too: the same creators judged for two brands are two judgments."""
    if not dhash:
        return ""
    return hashlib.sha256("|".join((dhash, *parts)).encode()).hexdigest()[:32]


async def agent3_relevance(
    g: GeminiClient, dossier: dict, intent: dict, dhash: str,
    market: dict | None = None, persona_ctx: str = "",
) -> dict[str, dict]:
    """Product fit and brand fit, as rubric levels with quoted evidence.

    persona_ctx is the brand profile, product moments and archetypes rendered
    by relevance.persona_context(). Code turns the levels into numbers.
    """
    ctx = market_context(market) + persona_ctx
    rows = await call_batched(
        g, "relevance_judge", prompts.RELEVANCE_JUDGE, schemas.RELEVANCE, 0.2,
        dossier, lambda sub: _stage_b_payload(sub, intent, ctx),
        _keyed(dhash, persona_ctx, json.dumps(intent, sort_keys=True, default=str)),
    )
    for r in rows.values():
        if r.get("product_fit") not in _LEVELS["product_fit"]:
            r.pop("product_fit", None)
        r["product_moments"] = [clean_text(t, 120) for t in
                                (r.get("product_moments") or [])[:3] if t]
        bf = r.get("brand_fit") if isinstance(r.get("brand_fit"), dict) else {}
        r["brand_fit"] = {k: bf.get(k) for k in ("tone", "price_tier", "values")
                          if bf.get(k) in _LEVELS["brand"]}
        r["brand_evidence"] = clean_text(r.get("brand_evidence"), 240)
        r["archetype"] = clean_text(r.get("archetype"), 80)
        r["reason"] = clean_text(r.get("reason"), 400)
        if r.get("match_type") not in _LEVELS["match_type"]:
            r["match_type"] = "adjacent"
    return rows


async def agent4_audience(
    g: GeminiClient, dossier: dict, intent: dict, dhash: str,
    market: dict | None = None, persona_ctx: str = "",
    evidence: dict[str, dict] | None = None,
) -> dict[str, dict]:
    """Who watches each creator, in the same profile shape as the target buyer.

    evidence adds what only this agent needs (viewer comments, tags, topics,
    audio language), so the other Stage B agents do not pay for reading it.
    """
    evidence = evidence or {}
    ctx = market_context(market) + persona_ctx + prompts._PROFILE_RULES + "\n\n"

    def build(sub: dict) -> str:
        merged = {k: {**v, **evidence.get(k, {})} for k, v in sub.items()}
        return _stage_b_payload(merged, intent, ctx)

    rows = await call_batched(
        g, "audience_analyst", prompts.AUDIENCE_ANALYST, schemas.AUDIENCE, 0.3,
        dossier, build,
        _keyed(dhash, persona_ctx, json.dumps(evidence, sort_keys=True, default=str)),
    )
    for r in rows.values():
        if r.get("buyer_overlap") not in _LEVELS["buyer_overlap"]:
            r.pop("buyer_overlap", None)
        if not isinstance(r.get("profile"), dict):
            r.pop("profile", None)
        r["evidence"] = [clean_text(e, 160) for e in (r.get("evidence") or [])[:4] if e]
        r["inferred_viewer_profile"] = clean_text(r.get("inferred_viewer_profile"), 240)
        r["reasoning"] = clean_text(r.get("reasoning"), 300)
        if r.get("purchase_intent_signal") not in ("high", "medium", "low"):
            r["purchase_intent_signal"] = "medium"
    return rows


async def agent5_safety(
    g: GeminiClient, dossier: dict, intent: dict, dhash: str,
    market: dict | None = None,
) -> dict[str, dict]:
    sens = intent.get("brand_safety_sensitivities") or []
    rivals = (market or {}).get("known_competitors") or []
    extra = (
        market_context(market)
        + f"This brand is specifically sensitive to: {', '.join(sens)}\n"
        + (f"Known competitors, whose sponsorship of a creator is a "
           f"category_conflict: {', '.join(rivals)}.\n" if rivals else
           f"A direct competitor of {intent.get('brand') or 'the advertiser'} "
           f"sponsoring the creator counts as category_conflict.\n")
        + "\n"
    )
    rows = await call_batched(
        g, "safety_auditor", prompts.SAFETY_AUDITOR, schemas.SAFETY, 0.0,
        dossier, lambda sub: _stage_b_payload(sub, intent, extra), dhash,
    )
    for r in rows.values():
        if r.get("severity") not in ("low", "medium", "high"):
            r["severity"] = "low"
        if not r.get("flag"):
            r["severity"] = "low"
            r["category"] = "none"
    return rows


async def agent6_sponsorship(
    g: GeminiClient, dossier: dict, intent: dict, dhash: str,
    market: dict | None = None,
) -> dict[str, dict]:
    extra = (
        market_context(market)
        + "Each candidate carries paid_placement_hits: the number of its videos "
          "that YouTube's verified paid-product-placement search returned. "
          "Presence is strong evidence; absence means nothing.\n\n"
    )
    return await call_batched(
        g, "sponsorship_analyst", prompts.SPONSORSHIP_ANALYST,
        schemas.SPONSORSHIP, 0.2, dossier,
        lambda sub: _stage_b_payload(sub, intent, extra), dhash,
        ("sponsor_confidence",),
    )


async def agent7_narrate(
    g: GeminiClient, top: list[dict], intent: dict
) -> dict[str, dict]:
    """The shortlist only: no prose spent on candidates that got cut.

    The writer reads explain.facts, where every number is already in the form
    a marketer reads ("1.4M", "~600K per video", "1.3%"), never a raw float.
    """
    rows = [explain.facts(c, i + 1, len(top)) for i, c in enumerate(top)]
    brief = {k: intent.get(k) for k in ("brand", "product", "target_audience",
                                        "geo_raw", "size_band") if intent.get(k)}
    payload = (
        f"The brand's brief:\n{json.dumps(brief, indent=1)}\n\n"
        f"The shortlist, best first, with the facts you may use:\n"
        + wrap(json.dumps(rows, indent=1, ensure_ascii=False))
        + "\n\nWrite one entry per channel_id."
    )
    out = await g.call("rationale_writer", prompts.RATIONALE_WRITER,
                       payload, schemas.RATIONALE, 0.5)
    rows_out = reconcile(out.get("results"), {c["channel_id"] for c in top})
    for r in rows_out.values():
        r["headline"] = clean_text(r.get("headline"), 120)
        r["rationale"] = clean_text(r.get("rationale"), 700)
        cav = r.get("caveat")
        r["caveat"] = clean_text(cav, 300) if cav else None
    return rows_out


async def agent8_audit(
    g: GeminiClient, top: list[dict], rationales: dict[str, dict]
) -> dict[str, dict]:
    """Check each rationale against the SAME facts the writer was given."""
    rows = []
    for i, c in enumerate(top):
        r = rationales.get(c["channel_id"])
        if not r:
            continue
        rows.append({
            "channel_id": c["channel_id"],
            "facts": explain.facts(c, i + 1, len(top)),
            "written_rationale": {
                "headline": r.get("headline"),
                "rationale": r.get("rationale"),
                "caveat": r.get("caveat"),
            },
        })
    if not rows:
        return {}
    payload = (
        "Verify each written rationale against its own facts:\n"
        + wrap(json.dumps(rows, indent=1, ensure_ascii=False))
        + "\n\nReturn a verdict for every channel_id."
    )
    out = await g.call("output_auditor", prompts.OUTPUT_AUDITOR,
                       payload, schemas.AUDIT, 0.0)
    res = reconcile(out.get("results"), {r["channel_id"] for r in rows})
    for r in res.values():
        if r.get("verdict") not in ("pass", "revise"):
            r["verdict"] = "revise"  # doubt resolves against shipping the claim
    return res


# ---------------------------------------------------------------- Agent 9

# Researching every candidate costs one grounded call each, which is slow and
# costs per Search query. Only the candidates that could actually make the shortlist are
# worth researching.
CULTURAL_SHORTLIST = 12


async def agent9_cultural(
    g: GeminiClient,
    candidates: list[dict],
    intent: dict,
    dhash: str,
) -> tuple[dict[str, dict], list[str]]:
    """Cultural standing from the open web, not from YouTube's own signals.

    YouTube tells you nothing about whether a creator is a household name to
    under-25s. A standup comic or a news anchor can be far more culturally
    present than their subscriber count suggests, and the trending chart only
    reflects a single day. This agent asks the web instead.

    Two steps, because search grounding and response_schema cannot be relied on
    in one call: a grounded research pass, then a cheap structuring pass.
    """
    shortlist = candidates[:CULTURAL_SHORTLIST]
    if not shortlist:
        return {}, []

    roster = "\n".join(
        f'- {clean_text(c.get("title"), 80)} (channel_id: {c["channel_id"]}) — '
        f'{clean_text(c.get("description"), 120)}'
        for c in shortlist
    )
    region = intent.get("geo_raw") or intent.get("region_code") or ""
    audience = intent.get("target_audience") or ""

    research_payload = (
        f"Advertiser: {clean_text(intent.get('brand'), 80)} — "
        f"{clean_text(intent.get('product'), 120)}\n"
        f"Target audience: {clean_text(audience, 160)}\n"
        f"Market: {clean_text(region, 80)}\n\n"
        f"Research these creators:\n{wrap(roster)}\n\n"
        "Write a short briefing for each, and include each channel_id verbatim."
    )

    cache_key = f"{dhash}:cultural" if dhash else None
    cached = cache.get_agent(cache_key, "cultural_analyst") if cache_key else None
    if cached is not None:
        cache.log_agent(g.run_id, "cultural_analyst", 0, "cache_hit")
        return cached.get("rows", {}), cached.get("sources", [])

    started = time.monotonic()
    try:
        briefing, sources = await g.search_grounded(
            prompts.CULTURAL_RESEARCHER, research_payload, 0.4,
            agent="cultural_search",
        )
    except Exception as e:
        cache.log_agent(
            g.run_id, "cultural_analyst",
            int((time.monotonic() - started) * 1000),
            f"error: search: {type(e).__name__}: {str(e)[:150]}",
        )
        raise AgentFailure(f"cultural_analyst: grounded search failed: {e}") from e

    valid = {c["channel_id"] for c in shortlist}
    structure_payload = (
        f"Advertiser: {clean_text(intent.get('brand'), 80)} — "
        f"{clean_text(intent.get('product'), 120)}, targeting "
        f"{clean_text(audience, 160)}.\n\n"
        f"Channel ids to return, one record each:\n"
        + "\n".join(f"- {cid}" for cid in valid)
        + f"\n\nResearch briefing:\n{wrap(briefing)}"
    )
    out = await g.call(
        "cultural_analyst", prompts.CULTURAL_STRUCTURER,
        structure_payload, schemas.CULTURAL, 0.1,
    )
    rows = reconcile(out.get("results"), valid, ("cultural_relevance",))
    for r in rows.values():
        r["persona"] = clean_text(r.get("persona"), 240)
        r["notable_context"] = clean_text(r.get("notable_context"), 240)
        r["brand_fit_note"] = clean_text(r.get("brand_fit_note"), 240)
        # No evidence means no score, never a penalty.
        if not r.get("evidence_found"):
            r["cultural_relevance"] = 0.0
            r["fame_tier"] = "unknown"

    if cache_key:
        cache.put_agent(cache_key, "cultural_analyst",
                        {"rows": rows, "sources": sources})
    return rows, sources


# ---------------------------------------------------------------- Agent 10

async def agent10_market_research(
    g: GeminiClient, intent: dict, brief: str
) -> tuple[dict, list[str]]:
    """Study the brand, its rivals, the category and the audience, before
    any creator search runs.

    This exists because discovery was the weakest link in the system. Two
    keyword searches decided everything: a creator those keywords missed could
    never be recovered by any amount of downstream scoring, and the failure was
    invisible, since every creator that WAS returned looked plausible.

    Two steps, because search grounding and response_schema cannot be relied on
    together: a grounded research pass, then a structuring pass.
    """
    payload = (
        f"Brief, as the advertiser wrote it:\n{wrap(clean_text(brief, 1200))}\n\n"
        f"Structured intent:\n{json.dumps(intent, indent=2)}\n\n"
        "Research this brand and market, then propose the YouTube searches."
    )

    cache_key = _hash_text(f"{brief}|{intent.get('region_code')}|{intent.get('product')}")
    cached = cache.get_agent(cache_key, "market_researcher")
    if cached is not None:
        cache.log_agent(g.run_id, "market_researcher", 0, "cache_hit")
        return cached.get("data", {}), cached.get("sources", [])

    started = time.monotonic()
    try:
        briefing, sources = await g.search_grounded(
            prompts.MARKET_RESEARCHER, payload, 0.35,
            agent="market_search",
        )
    except Exception as e:
        cache.log_agent(
            g.run_id, "market_researcher",
            int((time.monotonic() - started) * 1000),
            f"error: search: {type(e).__name__}: {str(e)[:150]}",
        )
        raise AgentFailure(f"market_researcher: grounded search failed: {e}") from e

    out = await g.call(
        "market_researcher", prompts.MARKET_STRUCTURER,
        f"Structured intent:\n{json.dumps(intent, indent=2)}\n\n"
        f"Research briefing:\n{wrap(briefing)}",
        schemas.MARKET_RESEARCH, 0.1,
    )

    data = _clean_market(out)
    cache.put_agent(cache_key, "market_researcher", {"data": data, "sources": sources})
    return data, sources


def _hash_text(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:32]


def _clean_market(out: dict) -> dict:
    """Sanitize every field: this feeds both search and four other agents."""
    def s(key: str, limit: int = 400) -> str:
        return clean_text(out.get(key), limit)

    queries = []
    for q in (out.get("search_queries") or [])[:8]:
        if not isinstance(q, dict):
            continue
        text = clean_text(q.get("query"), 90)
        if not text:
            continue
        intent_kind = q.get("intent")
        queries.append({
            "query": text,
            "intent": intent_kind if intent_kind in (
                "category", "audience_space", "competitor", "creator_name"
            ) else "category",
            "rationale": clean_text(q.get("rationale"), 200),
        })

    creators = []
    for c in (out.get("named_creators") or [])[:15]:
        if not isinstance(c, dict):
            continue
        name = clean_text(c.get("name"), 80)
        if name:
            creators.append({
                "name": name,
                "why": clean_text(c.get("why"), 200),
                "channel_hint": clean_text(c.get("channel_hint"), 80),
            })

    return {
        "brand_known": bool(out.get("brand_known")),
        "brand_profile": s("brand_profile"),
        "brand_positioning": s("brand_positioning"),
        "brand_story_angle": s("brand_story_angle"),
        "known_competitors": [clean_text(x, 60) for x in
                              (out.get("known_competitors") or [])[:12] if x],
        "competitor_creator_tactics": s("competitor_creator_tactics"),
        "category_landscape": s("category_landscape"),
        "audience_watch_habits": [clean_text(x, 120) for x in
                                  (out.get("audience_watch_habits") or [])[:10] if x],
        "named_creators": creators,
        "search_queries": queries,
        "red_flags": [clean_text(x, 200) for x in (out.get("red_flags") or [])[:8] if x],
        "confidence": out.get("confidence") if out.get("confidence") in
                      ("high", "medium", "low") else "low",
    }


# ---------------------------------------------------------------- Agent 11

async def agent11_review_shortlist(
    g: GeminiClient, ranked: list[dict], intent: dict, market: dict | None
) -> dict:
    """Judge the shortlist as a whole, after scoring.

    Every other agent evaluates one creator at a time, so nothing in the system
    could previously notice that the WHOLE RESULT was thin, one-note, or missing
    the audience the brief asked for. This agent answers the question a head of
    marketing asks first: is this list actually worth acting on?
    """
    if not ranked:
        return {}

    rows = [
        {
            "channel_id": c["channel_id"],
            "title": c["title"],
            "fit_score": c["fit_score"],
            "confidence": c["confidence"],
            "found_via": c.get("found_via", []),
            "subscribers": c["metrics"].get("subscriber_count"),
            "median_views": c["metrics"].get("median_views"),
            "engagement_rate": c["metrics"].get("engagement_rate"),
            "paid_placement_hits": c["metrics"].get("paid_placement_hits"),
            "match_type": c["agent"].get("match_type"),
            "purchase_intent": c["agent"].get("purchase_intent_signal"),
            "fame_tier": (c.get("cultural") or {}).get("fame_tier"),
            "safety": c["agent"].get("safety_category"),
            # State plainly whether each row already satisfies the two brief
            # constraints the reviewer can check itself. Without these it
            # re-derives them from raw subscriber counts and reports the
            # pipeline's own filters back to the user as failures.
            "in_requested_size_band": metrics.band_distance(
                c["metrics"].get("subscriber_count"),
                intent.get("size_band") or "mid",
            ) == 0,
            "country": c.get("country"),
            "in_requested_market": metrics.geo_fit(
                c.get("country"), intent.get("region_code")
            ),
        }
        for c in ranked[:15]
    ]

    payload = (
        f"What the advertiser asked for:\n{json.dumps(intent, indent=2)}\n\n"
        f"{market_context(market)}"
        f"The shortlist as scored ({len(rows)} creators):\n"
        + wrap(json.dumps(rows, indent=1))
        + "\n\nJudge this result as a whole."
    )

    out = await g.call("shortlist_reviewer", prompts.SHORTLIST_REVIEWER,
                       payload, schemas.SHORTLIST_REVIEW, 0.3)

    valid = {c["channel_id"] for c in ranked}
    return {
        "verdict": out.get("verdict") if out.get("verdict") in
                   ("ship", "ship_with_caveat", "weak") else "ship_with_caveat",
        "headline": clean_text(out.get("headline"), 160),
        "what_we_found": clean_text(out.get("what_we_found"), 600),
        "how_to_use_this": clean_text(out.get("how_to_use_this"), 600),
        "gaps": [clean_text(x, 200) for x in (out.get("gaps") or [])[:6] if x],
        "suggested_refinements": [clean_text(x, 200) for x in
                                  (out.get("suggested_refinements") or [])[:5] if x],
        # Same ID validation as everywhere else: unknown ids are dropped.
        "flagged_channel_ids": [x for x in (out.get("flagged_channel_ids") or [])
                                if isinstance(x, str) and x in valid][:10],
        "diversity_note": clean_text(out.get("diversity_note"), 300),
    }
