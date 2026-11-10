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

# ---------------------------------------------------------------- audience profile
#
# One shape for both sides of the audience match: the target buyer (persona
# agent) and each creator's inferred audience (Agent 4). metrics.audience_match
# compares them field by field, so the shapes must stay identical.

def _enum(values: list[str]) -> dict:
    return {"type": "string", "enum": list(values)}


_CONF = _enum(["high", "medium", "low"])


def _profile() -> dict:
    from prompts import AGE_BANDS, INTEREST_TAXONOMY, LIFE_STAGES
    return {
        "type": "object",
        "properties": {
            "age_bands": _arr({
                "type": "object",
                "properties": {"band": _enum(AGE_BANDS), "weight": NUM},
                "required": ["band", "weight"],
            }),
            "life_stages": _arr(_enum(LIFE_STAGES)),
            "interests": _arr(_enum(INTEREST_TAXONOMY)),
            "languages": _arr(STR),
            "region": STR,
            "spending_power": _enum(["low", "mid", "high", "unknown"]),
            "confidence": {
                "type": "object",
                "properties": {k: _CONF for k in (
                    "age_bands", "life_stages", "interests", "languages",
                    "region", "spending_power")},
                "required": ["age_bands", "life_stages", "interests",
                             "languages", "region", "spending_power"],
            },
        },
        "required": ["age_bands", "life_stages", "interests", "languages",
                     "region", "spending_power", "confidence"],
    }


AUDIENCE_PROFILE = _profile()

# ---------------------------------------------------------------- Agent 3

RELEVANCE = _results(
    {
        "channel_id": STR,
        "product_fit": _enum(["natural", "plausible", "forced", "none"]),
        "product_moments": _arr(STR),
        "brand_fit": {
            "type": "object",
            "properties": {
                "tone": _enum(["match", "neutral", "clash"]),
                "price_tier": _enum(["match", "neutral", "clash"]),
                "values": _enum(["match", "neutral", "clash"]),
            },
            "required": ["tone", "price_tier", "values"],
        },
        "brand_evidence": STR,
        "archetype": STR,
        "reason": STR,
        "match_type": _enum(["direct", "adjacent", "lifestyle", "weak"]),
    },
    ["channel_id", "product_fit", "product_moments", "brand_fit",
     "brand_evidence", "archetype", "reason", "match_type"],
)

# ---------------------------------------------------------------- Agent 4

AUDIENCE = _results(
    {
        "channel_id": STR,
        "profile": AUDIENCE_PROFILE,
        "buyer_overlap": _enum(["strong", "good", "partial", "weak"]),
        "evidence": _arr(STR),
        "inferred_viewer_profile": STR,
        "purchase_intent_signal": _enum(["high", "medium", "low"]),
        "reasoning": STR,
    },
    ["channel_id", "profile", "buyer_overlap", "evidence",
     "inferred_viewer_profile", "purchase_intent_signal", "reasoning"],
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


# ---------------------------------------------------------------- Agent 12

_ARCHETYPE = {
    "type": "object",
    "properties": {
        "name": STR,
        "why_audience_matches": STR,
        "search_phrases": _arr(STR),
        "category_ids": _arr(STR),
        "uses_product_words": BOOL,
        "known_creators": _arr({
            "type": "object",
            "properties": {"name": STR, "youtube_ref": STR},
            "required": ["name", "youtube_ref"],
        }),
    },
    "required": ["name", "why_audience_matches", "search_phrases",
                 "category_ids", "uses_product_words", "known_creators"],
}

PERSONA = {
    "type": "object",
    "properties": {
        "brand_profile": {
            "type": "object",
            "properties": {
                "tone": STR,
                "price_tier": _enum(["budget", "mass", "premium", "luxury"]),
                "values": _arr(STR),
                "avoid": _arr(STR),
                "languages": _arr(STR),
            },
            "required": ["tone", "price_tier", "values", "avoid", "languages"],
        },
        "product_moments": _arr(STR),
        "target_audience": AUDIENCE_PROFILE,
        "archetypes": _arr(_ARCHETYPE),
        # One level up: broader, more popular kinds of creator whose viewers
        # still include the buyer. Used only when the archetypes run thin.
        "wider_archetypes": _arr(_ARCHETYPE),
    },
    "required": ["brand_profile", "product_moments", "target_audience", "archetypes",
                 "wider_archetypes"],
}

# ---------------------------------------------------------------- scout

SCOUT = {
    "type": "object",
    "properties": {
        "creators": _arr({
            "type": "object",
            "properties": {
                "name": STR,
                "youtube_ref": STR,
                "archetype": STR,
                "evidence": STR,
            },
            "required": ["name", "youtube_ref", "archetype", "evidence"],
        }),
    },
    "required": ["creators"],
}

# ---------------------------------------------------------------- pre-screen

PRESCREEN = _results(
    {
        "channel_id": STR,
        "keep": BOOL,
        "archetype": STR,
        "reason": STR,
    },
    ["channel_id", "keep", "archetype", "reason"],
)
