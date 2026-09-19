"""response_schema definitions for all 8 agents.

Plain dicts — google-genai accepts dict schemas directly on
GenerateContentConfig.response_schema (verified against SDK 2.x).

Every per-candidate schema carries channel_id because results are reconciled
by ID, never by position (§4 cross-cutting rule 6).
"""
from __future__ import annotations

STR = {"type": "string"}
NUM = {"type": "number"}
BOOL = {"type": "boolean"}


def _arr(item: dict) -> dict:
    return {"type": "array", "items": item}


def _results(props: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "results": _arr(
                {"type": "object", "properties": props, "required": required}
            )
        },
        "required": ["results"],
    }


# ---------------------------------------------------------------- Agent 1

INTENT = {
    "type": "object",
    "properties": {
        "brand": STR,
        "product": STR,
        "product_category": STR,
        "target_audience": STR,
        "size_band": {"type": "string",
                      "enum": ["nano", "micro", "mid", "macro", "mega"]},
        "target_generation": {
            "type": "string",
            "enum": ["gen_z", "millennial", "mixed", "older", "unclear"],
        },
        "cultural_angle": STR,
        "geo_raw": STR,
        "geo_granularity": {
            "type": "string",
            "enum": ["country", "sub_country", "none"],
        },
        "region_code": {"type": "string", "nullable": True},
        "brand_safety_sensitivities": _arr(STR),
        "ambiguities": _arr(STR),
    },
    "required": [
        "brand",
        "product",
        "product_category",
        "target_audience",
        "size_band",
        "target_generation",
        "cultural_angle",
        "geo_raw",
        "geo_granularity",
        "brand_safety_sensitivities",
        "ambiguities",
    ],
}

# ---------------------------------------------------------------- Agent 2

QUERY_PLAN = {
    "type": "object",
    "properties": {
        "youtube_category_id": STR,
        "search_keywords": _arr(STR),
        "relevance_language": STR,
        "region_code": STR,
        "excluded_terms": _arr(STR),
    },
    "required": ["youtube_category_id", "search_keywords", "relevance_language",
                 "region_code", "excluded_terms"],
}

# ---------------------------------------------------------------- Agent 3

RELEVANCE = _results(
    {
        "channel_id": STR,
        "relevance": NUM,
        "reason": STR,
        "match_type": {
            "type": "string",
            "enum": ["direct", "adjacent", "lifestyle", "weak"],
        },
    },
    ["channel_id", "relevance", "reason", "match_type"],
)

# ---------------------------------------------------------------- Agent 4

AUDIENCE = _results(
    {
        "channel_id": STR,
        "audience_fit": NUM,
        "inferred_viewer_profile": STR,
        "purchase_intent_signal": {
            "type": "string",
            "enum": ["high", "medium", "low"],
        },
        "reasoning": STR,
    },
    ["channel_id", "audience_fit", "inferred_viewer_profile",
     "purchase_intent_signal", "reasoning"],
)

# ---------------------------------------------------------------- Agent 5

SAFETY = _results(
    {
        "channel_id": STR,
        "flag": BOOL,
        "severity": {"type": "string", "enum": ["low", "medium", "high"]},
        "category": {
            "type": "string",
            "enum": [
                "controversy",
                "explicit",
                "political",
                "misinformation",
                "category_conflict",
                "none",
            ],
        },
        "reason": STR,
        "evidence": STR,
    },
    ["channel_id", "flag", "severity", "category", "reason", "evidence"],
)

# ---------------------------------------------------------------- Agent 6

SPONSORSHIP = _results(
    {
        "channel_id": STR,
        "sponsor_confidence": NUM,
        "observed_format": {
            "type": "string",
            "enum": [
                "dedicated_segment",
                "integrated_mention",
                "product_review",
                "affiliate_only",
                "none_observed",
            ],
        },
        "cadence": {
            "type": "string",
            "enum": ["frequent", "occasional", "rare", "none_observed"],
        },
        "known_sponsor_categories": _arr(STR),
        "evidence": STR,
    },
    ["channel_id", "sponsor_confidence", "observed_format", "cadence",
     "known_sponsor_categories", "evidence"],
)

# ---------------------------------------------------------------- Agent 7

RATIONALE = _results(
    {
        "channel_id": STR,
        "headline": STR,
        "rationale": STR,
        "caveat": {"type": "string", "nullable": True},
    },
    ["channel_id", "headline", "rationale"],
)

# ---------------------------------------------------------------- Agent 8

AUDIT = _results(
    {
        "channel_id": STR,
        "verdict": {"type": "string", "enum": ["pass", "revise"]},
        "unsupported_claims": _arr(STR),
    },
    ["channel_id", "verdict", "unsupported_claims"],
)


# ---------------------------------------------------------------- Agent 9

CULTURAL = _results(
    {
        "channel_id": STR,
        "cultural_relevance": NUM,
        "fame_tier": {
            "type": "string",
            "enum": ["household_name", "scene_famous", "niche_known", "unknown"],
        },
        "persona": STR,
        "audience_generation": {
            "type": "string",
            "enum": ["gen_z", "millennial", "mixed", "older", "unclear"],
        },
        "sustained_or_spike": {
            "type": "string",
            "enum": ["sustained", "rising", "spike", "fading", "unknown"],
        },
        "notable_context": STR,
        "brand_fit_note": STR,
        "evidence_found": BOOL,
    },
    ["channel_id", "cultural_relevance", "fame_tier", "persona",
     "audience_generation", "sustained_or_spike", "notable_context",
     "brand_fit_note", "evidence_found"],
)


# ---------------------------------------------------------------- Agent 10

MARKET_RESEARCH = {
    "type": "object",
    "properties": {
        "brand_known": BOOL,
        "brand_profile": STR,
        "brand_positioning": STR,
        "brand_story_angle": STR,
        "known_competitors": _arr(STR),
        "competitor_creator_tactics": STR,
        "category_landscape": STR,
        "audience_watch_habits": _arr(STR),
        "named_creators": _arr({
            "type": "object",
            "properties": {
                "name": STR,
                "why": STR,
                "channel_hint": STR,
            },
            "required": ["name", "why", "channel_hint"],
        }),
        "search_queries": _arr({
            "type": "object",
            "properties": {
                "query": STR,
                "intent": {
                    "type": "string",
                    "enum": ["category", "audience_space", "competitor", "creator_name"],
                },
                "rationale": STR,
            },
            "required": ["query", "intent", "rationale"],
        }),
        "red_flags": _arr(STR),
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    },
    "required": [
        "brand_known", "brand_profile", "brand_positioning", "brand_story_angle",
        "known_competitors", "competitor_creator_tactics", "category_landscape",
        "audience_watch_habits", "named_creators", "search_queries",
        "red_flags", "confidence",
    ],
}


# ---------------------------------------------------------------- Agent 11

SHORTLIST_REVIEW = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["ship", "ship_with_caveat", "weak"],
        },
        "headline": STR,
        "what_we_found": STR,
        "how_to_use_this": STR,
        "gaps": _arr(STR),
        "suggested_refinements": _arr(STR),
        "flagged_channel_ids": _arr(STR),
        "diversity_note": STR,
    },
    "required": ["verdict", "headline", "what_we_found", "how_to_use_this",
                 "gaps", "suggested_refinements", "flagged_channel_ids",
                 "diversity_note"],
}
