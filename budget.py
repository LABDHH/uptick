"""Monthly Gemini spend: measured per call, turned into a search limit.

The API key is on a paid tier, so there is no per-minute ceiling worth pacing
against. What does bind is the monthly spend cap. This module replaces "how
many calls per minute" with "how many searches can this month still afford".

How it works:
  * Every Gemini response reports its own token usage. cost_of_response()
    prices it (thinking tokens bill as output) and record() adds it to this
    month's total, along with the Google Search queries a grounded call ran.
    Those are free up to FREE_GROUNDED_QUERIES a month, and only queries past
    the allowance are charged.
  * Every admitted search bumps a run counter, so spend / runs is the real
    average cost of a search, cache hits included.
  * status() blocks new searches once the remaining budget cannot cover one
    more average search.
  * Within a search, once it has spent max_run_usd(), every remaining call
    is routed to Flash-Lite (see agents.route), so no single search can run
    away. The budget used is the cap times HEADROOM, so runs
    already in flight, and any pricing drift, land below the real cap.

Google's own spend cap stays the hard backstop. This guard exists so users see
"paused" from us instead of every agent failing at once when Google cuts off.

Where the ledger lives matters. On Vercel the SQLite file sits in /tmp, which
is per instance and wiped on cold start, so a monthly total kept there resets
constantly. Set KV_REST_API_URL / KV_REST_API_TOKEN (the Upstash Redis
integration on Vercel sets these) and the ledger moves to Redis, shared across
instances and durable. Locally, SQLite is fine.
"""
from __future__ import annotations

import contextvars
import json
import logging
import os
from datetime import datetime
from typing import Any

import httpx

import cache

log = logging.getLogger("uptick.budget")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# USD per 1M tokens, (input, output). Thinking tokens bill as output.
#
# Third-party-reported Google list prices, Nov 2026; check them against
# ai.google.dev/gemini-api/docs/pricing. 3.8 Flash is priced at its post-intro
# list rate (the intro discount ends 2027-01-01) so the guard errs high, not
# low. Override with UPTICK_PRICES='{"model-id": [in, out], ...}'.
_DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    "gemini-3.8-flash": (1.50, 7.50),
    "gemini-3.5-flash-lite": (0.30, 2.50),
}
# A model missing from the table is priced like the dearest one, never free.
_UNKNOWN_PRICE = (1.50, 7.50)


def _prices() -> dict[str, tuple[float, float]]:
    prices = dict(_DEFAULT_PRICES)
    raw = os.environ.get("UPTICK_PRICES")
    if raw:
        try:
            for k, v in json.loads(raw).items():
                prices[k] = (float(v[0]), float(v[1]))
        except Exception as e:  # a typo in an env var must not stop searches
            log.warning("UPTICK_PRICES ignored: %s", e)
    return prices


def grounded_query_usd() -> float:
    """Grounding with Google Search on Gemini 3+ bills per search query the
    model runs: $14 per 1,000, after the free monthly allowance."""
    return _env_float("UPTICK_PRICE_GROUNDED_QUERY", 0.014)


def free_grounded_queries() -> int:
    """Google's free grounded queries per month, shared by every Gemini 3+
    model on the project. Lower it if other apps share the project."""
    return int(_env_float("UPTICK_FREE_GROUNDED_QUERIES", 5000))


def monthly_cap_usd() -> float | None:
    """The spend this app may make per month, or None for no app-side limit."""
    cap = _env_float("UPTICK_MONTHLY_BUDGET_USD", 0.0)
    return cap if cap > 0 else None


def headroom() -> float:
    """Share of the cap searches may use. The rest absorbs in-flight runs."""
    return min(1.0, max(0.1, _env_float("UPTICK_BUDGET_HEADROOM", 0.9)))


def default_run_usd() -> float:
    """What one search is assumed to cost until enough have been measured."""
    return _env_float("UPTICK_EST_RUN_USD", 0.20)


def max_run_usd() -> float:
    """Spend after which the rest of a single search runs on Flash-Lite."""
    return _env_float("UPTICK_MAX_RUN_USD", 0.25)


# Below this many searches in the month, the measured average is too noisy
# to trust and the configured estimate is used instead.
MIN_RUNS_FOR_AVERAGE = 3


# ---------------------------------------------------------------- pricing

def _n(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def cost_of_usage(
    model: str,
    prompt_tokens: int,
    output_tokens: int,
    thinking_tokens: int = 0,
) -> float:
    """Token cost only. Grounded queries are counted separately because the
    monthly free allowance decides whether they cost anything."""
    p_in, p_out = _prices().get(model, _UNKNOWN_PRICE)
    return (
        prompt_tokens * p_in / 1e6
        + (output_tokens + thinking_tokens) * p_out / 1e6
    )


def grounded_queries(resp: Any) -> int:
    queries = 0
    for cand in getattr(resp, "candidates", None) or []:
        gm = getattr(cand, "grounding_metadata", None)
        queries += len(getattr(gm, "web_search_queries", None) or [])
    return queries


def grounding_cost(queries_this_month: int) -> float:
    return max(0, queries_this_month - free_grounded_queries()) * grounded_query_usd()


def cost_of_response(model: str, resp: Any) -> float:
    """Token cost of one generate_content response, from its usage report."""
    um = getattr(resp, "usage_metadata", None)
    prompt = _n(getattr(um, "prompt_token_count", 0))
    # Search results the grounding tool feeds back in bill as input.
    prompt += _n(getattr(um, "tool_use_prompt_token_count", 0))
    output = _n(getattr(um, "candidates_token_count", 0))
    thinking = _n(getattr(um, "thoughts_token_count", 0))
    return cost_of_usage(model, prompt, output, thinking)


# ---------------------------------------------------------------- ledger

def month_key() -> str:
    """Billing months are counted in Pacific time, as Google bills."""
    return datetime.now(cache._PACIFIC).strftime("%Y-%m")


def _redis_creds() -> tuple[str, str] | None:
    url = os.environ.get("KV_REST_API_URL") or os.environ.get("UPSTASH_REDIS_REST_URL")
    tok = os.environ.get("KV_REST_API_TOKEN") or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
    return (url.rstrip("/"), tok) if url and tok else None


_KEY_TTL = 45 * 86400  # a month's keys outlive the month, then expire


async def _redis(commands: list[list]) -> list:
    url, tok = _redis_creds()  # type: ignore[misc]
    async with httpx.AsyncClient(timeout=4.0) as client:
        r = await client.post(
            f"{url}/pipeline",
            headers={"Authorization": f"Bearer {tok}"},
            json=commands,
        )
        r.raise_for_status()
        out = r.json()
    for item in out:
        if isinstance(item, dict) and item.get("error"):
            raise RuntimeError(item["error"])
    return [item.get("result") if isinstance(item, dict) else item for item in out]


def _keys(month: str) -> tuple[str, str, str]:
    base = f"uptick:spend:{month}"
    return f"{base}:micro_usd", f"{base}:runs", f"{base}:grounded_queries"


async def _add(micro_usd: int = 0, runs: int = 0, queries: int = 0) -> None:
    month = month_key()
    if _redis_creds():
        cmds: list[list] = []
        for key, n in zip(_keys(month), (micro_usd, runs, queries)):
            if n:
                cmds += [["INCRBY", key, n], ["EXPIRE", key, _KEY_TTL]]
        if cmds:
            await _redis(cmds)
        return
    c = cache._conn()
    c.execute("INSERT OR IGNORE INTO spend_ledger (month) VALUES (?)", (month,))
    c.execute(
        "UPDATE spend_ledger SET micro_usd = micro_usd + ?, runs = runs + ?, "
        "grounded_queries = grounded_queries + ? WHERE month = ?",
        (micro_usd, runs, queries, month),
    )
    c.commit()


async def _read() -> tuple[int, int, int]:
    """(token spend in micro-USD, searches admitted, grounded queries)."""
    month = month_key()
    if _redis_creds():
        (vals,) = await _redis([["MGET", *_keys(month)]])
        return _n(vals[0]), _n(vals[1]), _n(vals[2])
    row = cache._conn().execute(
        "SELECT micro_usd, runs, grounded_queries FROM spend_ledger WHERE month = ?",
        (month,),
    ).fetchone()
    return (row[0], row[1], row[2]) if row else (0, 0, 0)


def ledger_is_reliable() -> bool:
    """False when the monthly total would reset with the serverless instance."""
    return bool(_redis_creds()) or not cache.EPHEMERAL_LEDGER


# ---------------------------------------------------------------- public API

class RunMeter:
    """Token spend of the search in progress."""
    __slots__ = ("usd",)

    def __init__(self) -> None:
        self.usd = 0.0


# Set at the start of each search. The graph's node tasks inherit the context,
# so every agent call in one search adds to the same meter.
_run_meter: contextvars.ContextVar[RunMeter | None] = contextvars.ContextVar(
    "uptick_run_meter", default=None
)


def begin_run() -> RunMeter:
    meter = RunMeter()
    _run_meter.set(meter)
    return meter


def over_run_ceiling() -> bool:
    meter = _run_meter.get()
    return meter is not None and meter.usd >= max_run_usd()


async def record(usd: float, queries: int = 0) -> None:
    """Add one call's cost to this month and to the current search. Never
    raises: a ledger hiccup must not fail an agent whose answer has already
    been paid for."""
    meter = _run_meter.get()
    if meter is not None:
        meter.usd += max(0.0, usd)
    micro = max(0, int(round(usd * 1e6)))
    if micro <= 0 and queries <= 0:
        return
    try:
        await _add(micro_usd=micro, queries=max(0, queries))
    except Exception as e:
        log.warning("spend not recorded ($%.5f, %d queries): %s", usd, queries, e)


async def start_run() -> None:
    try:
        await _add(runs=1)
    except Exception as e:
        log.warning("run not counted: %s", e)


async def status() -> dict:
    """Spend so far this month, and whether another search is affordable.

    Fails closed: if the ledger cannot be read, the spend is unknown, and an
    unknown spend is not one to keep adding to.
    """
    cap = monthly_cap_usd()
    try:
        micro, runs, queries = await _read()
    except Exception as e:
        log.warning("spend ledger unreadable: %s", e)
        return {"configured": cap is not None, "blocked": cap is not None,
                "error": str(e)}

    spent = micro / 1e6 + grounding_cost(queries)
    measured = runs >= MIN_RUNS_FOR_AVERAGE and spent > 0
    per_run = spent / runs if measured else default_run_usd()
    out: dict = {
        "configured": cap is not None,
        "month": month_key(),
        "spent_usd": round(spent, 4),
        "runs": runs,
        "grounded_queries": queries,
        "free_grounded_queries": free_grounded_queries(),
        "avg_run_usd": round(per_run, 4),
        "avg_is_measured": measured,
        "blocked": False,
    }
    if cap is None:
        return out

    usable = cap * headroom()
    left = usable - spent
    out.update({
        "cap_usd": cap,
        "usable_usd": round(usable, 4),
        "left_usd": round(max(0.0, left), 4),
        "searches_left": max(0, int(left // per_run)) if per_run > 0 else None,
        # Not enough left for one more typical search.
        "blocked": left < per_run,
    })
    return out


if __name__ == "__main__":
    # `python budget.py` prints this month's spend and searches left.
    import asyncio
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from api.main import _load_keys

    _load_keys()
    cache.init_db()
    print(json.dumps(asyncio.run(status()), indent=2))
