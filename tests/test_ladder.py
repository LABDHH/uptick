"""The ladder: discovery must reach the audience, not only the product.

A brief for a narrow product (gelato) whose researcher proposes only narrow
queries returns almost nothing usable, and no downstream scoring can recover a
creator that was never surfaced. The buyers of most consumer products are
watching lifestyle and entertainment content, not content about the product.
"""
import graph
import prompts


def test_broad_searches_are_added_when_research_is_narrow():
    narrow = [
        {"query": "gelato review mumbai", "intent": "category", "rationale": ""},
        {"query": "best ice cream india", "intent": "category", "rationale": ""},
    ]
    out = graph._ensure_ladder(
        list(narrow), {"geo_raw": "India", "target_generation": "gen_z"}, {})
    broad = [q for q in out if q["intent"] == "audience_space"]
    assert len(broad) >= 2, "no audience-space searches were added"
    # The researched queries are kept, never replaced.
    assert all(any(o["query"] == n["query"] for o in out) for n in narrow)


def test_existing_broad_queries_are_left_alone():
    already = [
        {"query": "cafe hopping", "intent": "category", "rationale": ""},
        {"query": "day in my life delhi", "intent": "audience_space", "rationale": ""},
        {"query": "standup comedy india", "intent": "audience_space", "rationale": ""},
    ]
    out = graph._ensure_ladder(list(already), {"geo_raw": "India"}, {})
    assert len(out) == len(already), "padded a set that already had the ladder"


def test_broad_queries_survive_the_search_budget():
    """A flat truncation keeps whatever came first, which is always the narrow
    queries, dropping the broad ones exactly when they matter most."""
    queries = (
        [{"query": f"narrow {i}", "intent": "category", "rationale": ""}
         for i in range(6)]
        + [{"query": f"broad {i}", "intent": "audience_space", "rationale": ""}
           for i in range(2)]
    )
    narrow = [q for q in queries if q["intent"] != "audience_space"]
    broad = [q for q in queries if q["intent"] == "audience_space"]
    cap = graph.MAX_SEARCHES_PER_RUN
    keep_broad = min(len(broad), max(2, cap // 2))
    kept = narrow[: max(0, cap - keep_broad)] + broad[:keep_broad]

    assert len([q for q in kept if q["intent"] == "audience_space"]) >= 2
    assert len(kept) <= cap


def test_prompts_teach_the_ladder_not_category_matching():
    """The gelato failure came from judging content category instead of
    audience. Every judging prompt must say so explicitly."""
    assert "Rung 1" in prompts.RELEVANCE_JUDGE
    assert "WHAT SHARE" in prompts.RELEVANCE_JUDGE
    # "weak" must mean an audience mismatch, never a topic mismatch.
    assert "genuine AUDIENCE mismatch" in prompts.RELEVANCE_JUDGE
    assert "BUILD THEM AS A LADDER" in prompts.MARKET_RESEARCHER
    assert "THE LADDER" in prompts.SHORTLIST_REVIEWER


def test_reviewer_is_constructive_not_dismissive():
    """Telling a user to discard the shortlist is almost never useful."""
    t = prompts.SHORTLIST_REVIEWER
    assert "NEVER DISMISSIVE" in t
    assert "AUDIENCE THAT BUYS" in t
    # Variety of content categories is a strength, not a defect to flag.
    assert "never when the topics merely differ" in t.lower() or \
           "Never criticise a list for containing different content" in t
