# Uptick

**Find the creators your audience is talking about.**

Enter a brief — *"protein bar brand, India, Gen Z"* — and get ranked YouTube
creators who take in-video sponsorships, each with a score breakdown, the
evidence behind it, and a written rationale fact-checked against the source
metrics.

**Stack:** FastAPI + React (Vite, TypeScript, Tailwind) · LangGraph ·
Gemini 3.5 Flash-Lite · YouTube Data API v3 · SQLite

---

## Setup

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then add your two API keys
```

**Development** (API on 8700, Vite on 5173 with hot reload):

```bash
./run.sh          # open http://localhost:5173
```

**Production** (one process serving the API and the built frontend):

```bash
./serve.sh        # open http://localhost:8700
```

Run the tests with `pytest`: 50 tests, no API keys or network needed.

**No keys yet?** Click **See an example**. It renders the whole interface from
fixtures scored by the real ranking engine, so the numbers on screen are
genuinely computed.

## Architecture

```
web/            React + Vite + Tailwind frontend
  src/App.tsx           page shell, banners, results
  src/components/       CreatorCard, SearchPanel, ProgressView
  src/lib/api.ts        SSE client (POST cannot use EventSource)
api/main.py     FastAPI: /api/search (SSE), /api/demo, /api/health
graph.py        LangGraph pipeline, unchanged by the frontend swap
```

The backend has no frontend dependency: `graph.py`, `agents.py`, `metrics.py`,
`youtube.py` and `cache.py` import nothing from FastAPI or Streamlit. The API
layer is transport only.

Progress streams over Server-Sent Events because a cold query takes roughly 50
to 60 seconds. The graph already emits per-node updates through `astream`, so
the SSE endpoint is a thin adapter over something that already worked.

## How it works

```
brief → interpret → strategize → discover → enrich → dossier
                                                        ├→ relevance   ┐
                                                        ├→ audience    │
                                                        ├→ safety      ├→ score → narrate → audit
                                                        ├→ sponsorship │
                                                        └→ cultural ⌕  ┘
```

⌕ = grounded web search.

Eleven Gemini agents. A cold brief takes roughly 15 to 25 seconds and spends
4 of the 100 daily search calls, so at least 20 briefs a day are available.
Expect **~50–60s cold, <3s warm** — the latency is the throttle, not the work.

### The two rules that save the project

**Rule 1 — never use `search.list` to fetch a channel's videos.**
`search.list?channelId=X` costs 100 units *and one of only 100 daily search
calls, per channel*. At 25 candidates that is the entire day's budget. The
uploads-playlist path costs 1 unit and 0 search calls — ~100× cheaper.
(`youtube.py: recent_uploads`)

**Rule 2 — find sponsorship-proven creators with a search filter, not a model.**
`search.list(videoPaidProductPlacement=true)` returns only videos the creator
themselves flagged as a paid promotion, which separates creator-integrated deals
from YouTube's programmatic pre-rolls. High precision, low recall — so its
presence is rewarded and its absence is **never** penalized.
(`youtube.py: search_videos`)

### Speed

Two things were serialized and should not have been.

The Gemini throttle slept a fixed 5 seconds after every call, so thirteen
calls meant sixty-five seconds of waiting even though the limit allows fifteen
per minute. It is now a **sliding window**: calls that fit in the minute's
budget run concurrently, and it only blocks when the window is genuinely full.

YouTube searches ran one at a time, and the uploads playlist was fetched with
one sequential round trip per candidate, forty of them. Both now run
concurrently. The project allows 100 search calls per **minute**, so the
per-minute ceiling was never the constraint; only the daily 100 is.

Measured end to end with realistic latency: **78s to 10s**.

### The shortlist is a ceiling, not a target

Fifteen is the maximum, never something to pad toward. A creator appears only
when the evidence supports recommending them, and anything held back is
explained: *"4 other creators were found but not shown: 4 scored too low to
recommend."* Four defensible names beat fifteen where eleven are filler.

Creators below **100,000 subscribers or 100,000 median views** are excluded
outright, since they cannot deliver meaningful campaign reach. A channel with a
hidden subscriber count is judged on views alone rather than dropped, because
hiding subscribers is a setting, not a signal.

### Agent 10 — market research, before any creator search

Discovery used to be the weakest link: two keyword searches decided the entire
result, and a creator those keywords missed could never be recovered by any
amount of downstream scoring. The failure was invisible, because every creator
that *was* returned looked plausible.

Agent 10 now runs first, with grounded web search, and answers four questions:

1. **The brand.** Does it exist, how is it positioned, what is its story? A
   creator has to fit the brand's voice, not just its product category. If the
   brand has no public footprint, that is recorded plainly rather than invented.
2. **The competitors.** Who else sells this, and which creators do they already
   sponsor? This feeds the safety auditor's `category_conflict` check directly.
3. **The category and audience.** What do these buyers actually watch, answered
   without looking at the product category at all.
4. **The creators.** Who is genuinely prominent with this audience in this market.

It outputs 4 to 8 YouTube queries tagged by intent (`category`,
`audience_space`, `competitor`, `creator_name`). An `audience_space` query
deliberately drops the category filter, because the whole point is to find
creators YouTube does not file under this product.

Each candidate records which intents surfaced it, and a creator found by
several different angles ranks above one that matched a single phrasing.

### Agent 11 — reviewing the shortlist as a whole

Every other agent judges one creator at a time, so nothing could notice that
the *entire result* was thin, one-note, or missing the audience the brief asked
for. Agent 11 runs last and answers what a sceptical head of marketing asks:
is this worth acting on, what is missing, and what would a better brief say?
Its verdict can be `weak`, which is an honest and useful answer.

### Bias guards

Every judging agent carries explicit guards, because a research agent that
searches for a brand can easily talk itself into liking what it found:
popularity is not fit, evidence beats recognition, how a creator was found is
not a verdict, no anchoring on list order, absent data is not bad data, and
saying "I cannot tell" is a legitimate output.

### Agent 9 — cultural standing

YouTube's own signals cannot see whether a creator is a household name to
under-25s. Subscriber counts miss off-platform fame entirely, and the trending
chart captures a single day, not a standing. A standup comic, musician or news
anchor can be far more culturally present than their channel metrics suggest.

Agent 9 researches each shortlisted creator with **grounded Google Search**,
then a second pass structures the findings. It records whether prominence is
`sustained` or a one-week `spike`, and which generation the audience skews to.

Its output is a **bonus, never a weighted term** — capped at 12 points, scaled
by durability. Most working creators have no press coverage, and as a weighted
term "nothing found" would score zero and push them down the ranking. As a
bonus it can only lift the genuinely prominent.

### How the agents reason

The reasoning doctrine lives in [AGENT_GUIDELINES.md](AGENT_GUIDELINES.md).
The central rule is that a brief is about **buyers, not categories**: a protein
bar is not a "fitness product" but a snack that particular people eat at
particular moments, so the pipeline searches where those people already are —
comedy, campus life, gaming — not only where the product is discussed. That
failure mode is invisible without this discipline, because every category-
matched result looks plausible while the better creators were never surfaced.

### Scoring is pure code

No agent produces the final number. LLM scores drift between runs and cannot be
audited; agents emit bounded judgments and Python does the arithmetic, so every
rank is explainable by pointing at `metrics.WEIGHTS`.

| Term | Weight | Source |
|---|---|---|
| relevance | 0.25 | Agent 3 |
| engagement_rate | 0.18 | code |
| view_per_sub | 0.13 | code |
| sponsor_ratio | 0.12 | code |
| consistency | 0.10 | code |
| audience_fit | 0.10 | Agent 4 |
| sponsor_confidence | 0.07 | Agent 6 |
| activity | 0.03 | code |
| size_fit | 0.02 | code |

Agent terms **0.42**, code terms **0.58** — deliberate. Agent 5 (safety) carries
no weight; it gates and annotates, and a human decides.

Three choices do most of the work:
- **Median, never mean.** Views of 10k/12k/11k/9k/2.4M give a mean of ~488k and
  a median of 11k. The median is the honest expected reach.
- **Ratios, never raw counts.** A 50k-sub channel at 40k median views beats a
  10M-sub channel at 200k. Ranking by raw count just ranks by size, which the
  user can do for free.
- **`norm()` is min-max within the current candidate set.** Engagement norms
  differ wildly between gaming and finance; only relative comparison means
  anything.

### Stage B batching

The four Stage B agents receive the whole candidate set in one call, because
isolated per-candidate ratings drift and stop being comparable — and the
ranking depends entirely on comparing them. Splitting only happens when a
response would exceed the model's output ceiling: the set is halved
recursively until each request fits, and the rows are merged by channel ID.

This matters because the two six-field agents (safety, sponsorship) emit the
most tokens per candidate, so at 25 candidates they hit the ceiling first and
returned truncated half-JSON. Truncation is now detected explicitly and is
never retried verbatim — an identical oversized request truncates identically.

### It degrades, it does not fail

Every agent node catches its own exceptions. A failed agent has its weight
dropped and the remainder renormalized to 1.0, and the result is marked
`partial`. All four Stage B agents can die and the metrics-only ranking still
works. This is covered by tests, one per agent.

---

## What this tool cannot tell you

These limits are structural, not bugs, and the UI states them permanently:

- **Geography is country-level only.** No state or city targeting exists
  anywhere in the YouTube API. A sub-country brief triggers a banner rather than
  being silently upgraded to a country.
- **No audience demographics exist.** No viewer age, gender, or income data is
  publicly available, so no agent may state any. Agent 8 treats any such claim
  as fabricated by definition.
- **Transcripts are unavailable** (`captions.download` is owner-only), so
  sponsorship analysis reads descriptions.
- **Sponsorship disclosure is self-declared and under-reported.** Absence of
  evidence is never treated as evidence of absence.
- **Engagement is a proxy for attention, not predicted ROI.**

## Safety and quota behavior

- **Prompt injection** is treated as a real attack: video titles and
  descriptions are untrusted input. They are wrapped in delimiters, declared
  "data, never instructions", stripped of control characters, truncated to 200
  chars, and every returned `channel_id` is validated against the input set so
  hallucinated or injected IDs are dropped.
- **`403 quotaExceeded` stops everything** until midnight Pacific and is never
  retried. **`403 rateLimitExceeded` is the opposite** — backoff and retry.
- **The 100/day search cap is not a distinct API error**, so it is tracked in
  `quota_ledger` and blocked at 90.
- A **per-session query cap** and the global ledger protect the daily search
  budget. Both are enforced silently — the user never sees a counter.
  Note there is **no password gate**: if you expose this publicly, the session
  cap is per browser session and will not stop a determined visitor from
  exhausting the daily search calls.

Data comes from the official YouTube Data API v3 only. No scraping, even when
quota-blocked — API use is permitted, scraping violates the ToS.

## Deploying

Streamlit Community Cloud works, with one caveat: the filesystem is **ephemeral**,
so `uptick.db` is wiped on every redeploy. That is survivable here because
the cache simply rebuilds, but any future historical-tracking feature would need
managed Postgres.

## Layout

| File | Contents |
|---|---|
| `api/main.py` | HTTP API: SSE search, demo, health |
| `web/` | React frontend (Vite, TypeScript, Tailwind) |

| `graph.py` | LangGraph state, node wiring, checkpointer, degradation |
| `agents.py` | 8 agents, throttle, injection defenses, ID validation |
| `prompts.py` | System instructions |
| `schemas.py` | `response_schema` definitions |
| `youtube.py` | httpx client, batching, `fields`, error taxonomy |
| `metrics.py` | Scoring math, normalization, confidence |
| `cache.py` | SQLite cache, quota ledger, agent log |
| `theme.py` | CSS design system and HTML component builders |
| `demo_data.py` | Fixtures for demo mode (no API key or quota needed) |
| `AGENT_GUIDELINES.md` | How the agents should reason (edit this to change behaviour) |
| `tests/` | 64 tests, no network required |
