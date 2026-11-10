# Uptick

**Find the creators your audience is talking about.**

Enter a brief — *"protein bar brand, India, Gen Z"* — and get ranked YouTube
creators who take in-video sponsorships, each with a score breakdown, the
evidence behind it, and a written rationale fact-checked against the source
metrics.

**Stack:** FastAPI + React (Vite, TypeScript, Tailwind) · LangGraph ·
Gemini 3.8 Flash + 3.5 Flash-Lite · YouTube Data API v3 · SQLite

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

Run the tests with `pytest`: 148 tests, no API keys or network needed.

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
brief → interpret ─┬→ research ⌕ ─┐
                   └→ strategize ─┴→ persona ─┬→ discover (trending + search) ─┐
                                              └→ scout ⌕ ─────────────────────┴→ screen
      → enrich (+ comments, + refill) → dossier ├→ relevance   ┐
                                                ├→ audience    │
                                                ├→ safety      ├→ score ─┬→ narrate → audit
                                                ├→ sponsorship │         └→ review
                                                └→ cultural ⌕  ┘
                     score ─(fewer than 10 clear the bar)→ widen → discover + scout → … → score
```

⌕ = grounded web search.

### Ten good creators, every time it is honest to

* **Shorts creators count.** Each creator is measured on the format they
  actually make: long videos when there are at least 4, otherwise their
  Shorts. The two are never mixed in one median, and a Shorts creator's reach
  is compared only with other Shorts creators'. Much of a market's trending
  chart (India's especially) is Shorts-first; before this, those creators were
  all dropped as "insufficient data".
* **50 recent uploads per creator** (one playlist page, still 1 unit), so a
  creator who mixes Shorts and long videos has enough of each to judge.
* **Refill before judging.** A studied creator the data rules out (too few
  videos, under the reach floors, far outside the size band) is replaced from
  the screened pool *before* the Gemini judges read anyone, so no judging is
  paid for on a creator who cannot be recommended.
* **Widen one level up.** If fewer than `UPTICK_SHORTLIST_TARGET` (10)
  creators clear the bar, the graph loops once: the persona's
  `wider_archetypes` (broader, more popular kinds of creator whose viewers
  still include the buyer: mainstream comedy, big lifestyle vloggers, cricket
  commentary and so on) drive deeper trending charts and a second scout. No
  extra `search.list` call. New finds go through the same enrich, judge and
  score path and are ranked together with the first pass. The relevance gate
  still applies: popularity never rescues an off-brief creator.
* **Labelled fill.** The shortlist fills toward 10: clear recommendations
  first, then strong scores on thin data ("Limited data"), then creators
  scoring 35 to 45 ("Broader fit"). Each label is shown on the card.

### Why each creator is there, in plain words

`explain.py` turns each scored row into readable reasons and numbers: a
summary ("Picked because their viewers look like your buyers, the product fits
naturally into their videos…"), a caveat, and **key numbers** rounded for
people (`1.4M` subscribers, `~600K per video`, `1.3% of viewers like or
comment, above usual for a channel this size`, `2 of last 20 videos
sponsored`). The rationale writer and the output auditor read the same facts,
so true claims about the audience or a sponsor pass. When the writer's note
cannot be confirmed, the card shows the code-written summary instead of
nothing.

A cold brief spends **3 of the 100 daily search calls** (one paid-placement
search and two archetype searches), so about 30 briefs a day are available.
Trending charts, handle lookups, uploads and comments cost 1 unit each and no
search calls.

### Relevance first: who buys this, and who do they watch

The tool's job is the most relevant top creators for a brand, so discovery and
scoring both start from the buyer rather than the product category:

1. **Persona (Agent 12).** From the brief and the market research it writes the
   brand profile (tone, price tier, values, what to avoid), the moments the
   product is used in, the target buyer as a structured profile, and 4–6
   **creator archetypes** whose audience *is* that buyer. At least two must use
   no product words: buyers of most products are watching comedy, campus,
   gaming or lifestyle content, not reviews of the product.
2. **Three sources, side by side.** The trending charts for the market and for
   each archetype's YouTube category (a real source now, not just a +5 flag); a
   grounded **scout** that names real creators per archetype; and three
   YouTube searches.
3. **Screen.** Every named creator is resolved to a real channel (`@handle`,
   channel URL or video link). One that does not resolve was invented and is
   dropped. The whole pool is checked on channel stats (1 unit per 50) before
   any per-creator spend; a large pool is pre-screened for audience fit on
   cheap fields, which clears out the studios, labels and news networks that
   dominate trending. Creators found by **independent sources** (scout and
   trending, say) are studied first.
4. **Audience evidence.** Each studied creator gets a sample of viewer
   comments, their tags, topics and audio language. Comments are the best
   public signal of who watches: the language viewers write in, where they
   are, what stage of life they mention.
5. **Three-part relevance**, each needing quoted evidence (see Scoring).

Trending history is stored daily, so a creator's **days on the chart in the
last 14** counts toward popularity, rather than one day's spike. On Vercel this
needs a persistent database to accumulate across deploys.
Expect **under a minute cold, <3s warm**. Gemini calls are not paced per
minute (paid tier); the monthly spend gate in `budget.py` is the limit.

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

No agent produces the final number. Agents emit **rubric levels with quoted
evidence**; Python turns them into numbers and does the arithmetic.

```
fit = 100 · gate(relevance) · (0.55 · relevance + 0.45 · quality) + gate · bonuses
gate(r) = clamp((r − 0.2) / 0.6, 0, 1)        relevance < 0.30 → excluded as off-brief
```

**Relevance** (`metrics.RELEVANCE_PARTS`):

| Part | Weight | How it is judged |
|---|---|---|
| audience_match | 0.45 | Agent 4 infers the creator's audience (age bands, life stage, interests, language, country, spending) from content and comments; code compares it field by field with the target buyer |
| product_fit | 0.30 | Agent 3: `natural / plausible / forced / none`, quoting the videos where it fits |
| brand_fit | 0.25 | Agent 3: tone, price tier and values each `match / neutral / clash` |

A level given without evidence is moved down one step in code. Low-confidence
audience fields are pulled toward neutral.

**Quality** (`metrics.WEIGHTS`): engagement judged *for the channel's size*
(0.20), reach (0.14), views per subscriber (0.12), size fit (0.12), the share
of their own uploads that are sponsored (0.10), Agent 6's sponsor confidence
(0.08), consistency (0.08), momentum (0.07), market fit (0.06), activity (0.03).

**Bonuses**, scaled by the gate: popularity from trending (up to +6) and
cultural standing (up to +12). Fame lifts a creator who fits and does nothing
for one who does not. Agent 5 (safety) carries no weight; it gates and
annotates, and a human decides.

Three choices do most of the work:
- **Median, never mean.** Views of 10k/12k/11k/9k/2.4M give a mean of ~488k and
  a median of 11k. The median is the honest expected reach.
- **Ratios, never raw counts.** A 50k-sub channel at 40k median views beats a
  10M-sub channel at 200k. Ranking by raw count just ranks by size, which the
  user can do for free.
- **Percentile ranks within the current candidate set**, not min-max, so one
  viral outlier cannot squash everyone else. Engagement is ranked on the
  residual of log engagement against log subscribers, because raw engagement
  always favours the smallest channel.
- **Sponsorship from the creator's own uploads**: YouTube's paid-promotion
  flag on every recent video plus disclosure phrases in descriptions. When
  none is seen the term is dropped, never scored zero.

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
- **No audience demographics exist.** No measured viewer age, gender, or
  income data is publicly available. Audience profiles are **inferred** from
  videos and viewer comments, labelled as inferred in the UI, and backed by
  quoted evidence. Agent 8 still treats any stated demographic statistic as
  fabricated by definition.
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
| `discovery.py` | Persona, creator scout, pre-screen, pool ranking |
| `relevance.py` | Brand/buyer context and audience evidence for the judges |
| `agents.py` | Agents, per-agent model + thinking routing, injection defenses, ID validation |
| `budget.py` | Measured Gemini spend per month; pauses new searches near the cap |
| `prompts.py` | System instructions |
| `schemas.py` | `response_schema` definitions |
| `youtube.py` | httpx client, batching, `fields`, error taxonomy |
| `metrics.py` | Scoring math, normalization, confidence, shortlist tiers |
| `explain.py` | Plain-language reasons and rounded key numbers per creator |
| `cache.py` | SQLite cache, quota ledger, agent log |
| `theme.py` | CSS design system and HTML component builders |
| `demo_data.py` | Fixtures for demo mode (no API key or quota needed) |
| `AGENT_GUIDELINES.md` | How the agents should reason (edit this to change behaviour) |
| `tests/` | 148 tests, no network required |
