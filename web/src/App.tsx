import { useEffect, useMemo, useState } from "react";
import CreatorCard from "./components/CreatorCard";
import ProgressView from "./components/ProgressView";
import SearchPanel from "./components/SearchPanel";
import ReviewPanel from "./components/ReviewPanel";
import SummaryBar from "./components/SummaryBar";
import { runSearch } from "./lib/api";
import type { Progress, Results } from "./types";

function sessionId(): string {
  const k = "uptick_sid";
  let v = localStorage.getItem(k);
  if (!v) { v = crypto.randomUUID(); localStorage.setItem(k, v); }
  return v;
}

type Sort = "score" | "reach" | "engagement";

function Note({ tone = "grape", title, children }: {
  tone?: "grape" | "sun" | "coral"; title?: string; children: React.ReactNode;
}) {
  const tones = {
    grape: "border-l-grape-500 bg-grape-50/60 dark:bg-grape-500/10",
    sun:   "border-l-sun-500 bg-sun-50/70 dark:bg-sun-500/10",
    coral: "border-l-coral-500 bg-coral-50/70 dark:bg-coral-500/10",
  };
  return (
    <div className={`rounded-xl border border-line border-l-[3px] p-3.5 text-[13px] leading-relaxed
                     muted dark:border-white/10 ${tones[tone]}`}>
      {title && <b className="text-ink dark:text-slate-100">{title} </b>}
      {children}
    </div>
  );
}

export default function App() {
  const [brief, setBrief] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [results, setResults] = useState<Results | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sort, setSort] = useState<Sort>("score");
  // Dark only for now. The light palette still exists in the stylesheet, so
  // restoring the toggle later is a one line change.
  useEffect(() => {
    document.documentElement.classList.add("dark");
  }, []);

  async function search() {
    setBusy(true); setError(null); setResults(null); setProgress(null);
    try { setResults(await runSearch(brief, sessionId(), setProgress)); }
    catch (e) { setError(e instanceof Error ? e.message : "Something went wrong."); }
    finally { setBusy(false); setProgress(null); }
  }

  const ranked = useMemo(() => {
    const rows = [...(results?.ranked ?? [])];
    if (sort === "reach") rows.sort((a, b) => (b.metrics.median_views ?? 0) - (a.metrics.median_views ?? 0));
    else if (sort === "engagement") rows.sort((a, b) => (b.metrics.engagement_rate ?? 0) - (a.metrics.engagement_rate ?? 0));
    else rows.sort((a, b) => b.fit_score - a.fit_score);
    return rows;
  }, [results, sort]);

  const hasResults = !!results && !busy;

  // What the user actually lost, not which internal agent raised the error.
  // "cultural_analyst failed and its weight was redistributed" reads as a
  // broken product; neither of these is a weighted scoring term, and saying so
  // is both more honest and less alarming.
  const DEGRADED_COPY: Record<string, string> = {
    cultural_analyst:
      "Fame and press coverage outside YouTube could not be checked, so creators are ranked on their YouTube signals alone.",
    market_researcher:
      "Background research on the brand and market was unavailable, so the search worked from your brief alone.",
    rationale_writer:
      "The written explanations could not be generated, so each creator shows a plain summary built from their numbers instead.",
    output_auditor:
      "The written explanations could not be double-checked, so each creator shows a plain summary built from their numbers instead.",
    shortlist_reviewer: "The overall review of this shortlist could not be generated.",
    persona_builder:
      "The buyer profile could not be built, so creators were found from the market research searches and judged against your brief directly.",
    creator_scout:
      "The web scout for named creators was unavailable, so creators came from trending charts and YouTube search.",
    prescreener:
      "The quick audience pre-screen was unavailable, so creators were studied in order of how much evidence surfaced them.",
    relevance_judge:
      "Product and brand fit could not be judged, so the ranking leans on audience fit and channel strength.",
    audience_analyst:
      "Audience inference was unavailable, so the ranking leans on product fit, brand fit and channel strength.",
  };

  const degraded = useMemo(
    () =>
      (results?.agent_failures ?? [])
        .map((f) => DEGRADED_COPY[f])
        .filter((x): x is string => !!x),
    [results],
  );

  return (
    <div className="min-h-screen">
      {/* header */}
      <header className="sticky top-0 z-20 border-b border-line/70 bg-surf-page/80 backdrop-blur-md
                         dark:border-white/10 dark:bg-[#0a0c16]/80">
        <div className="mx-auto flex max-w-5xl items-center gap-3 px-5 py-3">
          <button onClick={() => { setResults(null); setError(null); }}
                  className="flex items-center gap-2 font-display text-[1.2rem] font-extrabold tracking-tight">
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-gradient-to-br
                             from-grape-500 to-grape-700 text-white shadow-glow">
              <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor"
                   strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 16l5-6 4 4 6-8" />
              </svg>
            </span>
            Uptick
          </button>

          {hasResults && (
            <span className="hidden truncate text-[13px] muted sm:inline">
              · {results!.intent?.product || brief}
            </span>
          )}

          <span className="ml-auto" />
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-5 pb-28">
        {/* hero, only before results */}
        {!hasResults && !busy && (
          <section className="pt-14 text-center sm:pt-20">
            <span className="pill mb-5 bg-grape-50 text-grape-700 dark:bg-grape-500/15 dark:text-grape-300">
              ✦ Nine agents · live YouTube data
            </span>
            <h1 className="mx-auto max-w-[19ch] font-display text-[2.7rem] font-extrabold leading-[1.05]
                           tracking-tight sm:text-[3.4rem]">
              Find the creators your{" "}
              <span className="bg-gradient-to-br from-grape-500 via-grape-600 to-coral-500 bg-clip-text text-transparent">
                audience
              </span>{" "}
              is talking about.
            </h1>
            <p className="mx-auto mt-4 max-w-[54ch] text-[15px] leading-relaxed muted">
              Describe your campaign in a sentence. Get a ranked shortlist of YouTube
              creators worth approaching, each with the sponsorship signals we could
              actually observe and a rationale checked against the real numbers.
            </p>
            <div className="mx-auto mt-8 max-w-2xl text-left">
              <SearchPanel brief={brief} setBrief={setBrief} onSearch={search} busy={busy} />
            </div>
          </section>
        )}

        {/* compact search once results exist */}
        {(hasResults || busy) && (
          <div className="pt-6">
            <button
              onClick={() => { setResults(null); setError(null); setProgress(null); }}
              disabled={busy}
              className="mb-3 inline-flex items-center gap-1.5 text-[13px] font-semibold
                         text-ink-faint transition hover:text-grape-600 disabled:opacity-40">
              <span aria-hidden>&larr;</span> New search
            </button>
            <SearchPanel brief={brief} setBrief={setBrief} onSearch={search}
                         busy={busy} compact />
          </div>
        )}

        <div className="mt-5 space-y-3">
          {busy && <ProgressView p={progress} />}

          {error && (
            <Note tone="coral" title="Search failed.">{error}</Note>
          )}

          {hasResults && results!.intent?.geo_granularity === "sub_country" && (
            <Note tone="sun" title="Sub-country targeting is not available.">
              You asked for <b>{results!.intent.geo_raw}</b>. The search ran at country
              level ({results!.intent.region_code}) using language and content as a proxy.
            </Note>
          )}

          {hasResults && !!degraded.length && (
            <Note tone="sun" title="Some enrichment was unavailable.">
              {degraded.join(" ")} Creators are still ranked: anything that could
              not be judged counts as neutral, never against them.
            </Note>
          )}
        </div>

        {hasResults && (
          <section className="mt-6">
            <SummaryBar r={results!} />

            {results!.review?.verdict && (
              <div className="mt-4">
                <ReviewPanel r={results!} />
              </div>
            )}

            {!!results!.persona?.archetypes?.length && (
              <details className="card mt-4 p-4 text-[13.5px]">
                <summary className="cursor-pointer font-semibold">
                  Who we looked for
                  {results!.persona.brand_profile?.tone && (
                    <span className="font-normal muted">
                      {" "}· {results!.persona.brand_profile.tone}
                      {results!.persona.brand_profile.price_tier
                        ? `, ${results!.persona.brand_profile.price_tier}` : ""} brand
                    </span>
                  )}
                </summary>
                <ul className="mt-3 space-y-1.5">
                  {results!.persona.archetypes.map((a) => (
                    <li key={a.name} className="leading-relaxed">
                      <b>{a.name}.</b> <span className="muted">{a.why_audience_matches}</span>
                    </li>
                  ))}
                </ul>
                {!!results!.persona.product_moments?.length && (
                  <p className="mt-3 muted">
                    <b className="text-ink dark:text-slate-100">Where the product fits: </b>
                    {results!.persona.product_moments.join("; ")}.
                  </p>
                )}
              </details>
            )}

            {ranked.length === 0 ? (
              <div className="card mt-6 p-12 text-center">
                <div className="mb-3 text-4xl">🔍</div>
                <h3 className="font-display text-lg font-bold">
                  Let us widen the net
                </h3>
                <p className="mx-auto mt-2 max-w-[48ch] text-[14px] leading-relaxed muted">
                  Nothing cleared the bar on this phrasing, which usually means the
                  product is described more narrowly than people search. Try naming
                  the occasion rather than the product: who buys it, where, and when
                  they use it.
                </p>
              </div>
            ) : (
              <>
                {!!results!.floor_note?.note && (
                  <div className="mt-6 rounded-xl2 border border-line border-l-[3px]
                                  border-l-sun-500 bg-sun-50/70 p-4 text-[13.5px]
                                  leading-relaxed muted dark:border-white/10 dark:bg-sun-500/10">
                    <b className="text-ink dark:text-slate-100">Early leads. </b>
                    {results!.floor_note.note}
                  </div>
                )}

                <div className="mb-3 mt-7 flex flex-wrap items-center gap-3">
                  <h2 className="font-display text-[1.25rem] font-bold">
                    {ranked.length} creator{ranked.length === 1 ? "" : "s"}{" "}
                    {results!.floor_note?.note ? "to consider" : "worth a look"}
                  </h2>
                  <div className="ml-auto flex items-center gap-1 rounded-xl border border-line p-1
                                  dark:border-white/10">
                    {([["score","Best fit"],["reach","Reach"],["engagement","Engagement"]] as const).map(([k, l]) => (
                      <button key={k} onClick={() => setSort(k)}
                              className={`rounded-lg px-2.5 py-1 text-[12.5px] font-semibold transition
                                ${sort === k ? "bg-grape-500 text-white shadow-sm"
                                  : "text-ink-faint hover:text-grape-600"}`}>
                        {l}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="space-y-3">
                  {ranked.map((c, i) => <CreatorCard key={c.channel_id} rank={i + 1} c={c} />)}
                </div>

                {!!results!.held_back?.note && (
                  <p className="mt-4 rounded-xl border border-dashed border-line px-4 py-3
                                text-[13px] leading-relaxed muted dark:border-white/10">
                    {results!.held_back.note}
                  </p>
                )}
              </>
            )}

            {!!results!.review_manually.length && (
              <div className="mt-10">
                <h2 className="font-display text-[1.15rem] font-bold">
                  ⚠ Review manually · {results!.review_manually.length}
                </h2>
                <p className="mb-3 mt-1 text-[13.5px] muted">
                  Flagged at high severity by the safety auditor. They are never silently
                  dropped, a human decides.
                </p>
                <div className="space-y-3">
                  {results!.review_manually.map((c, i) => (
                    <CreatorCard key={c.channel_id} rank={i + 1} c={c} />
                  ))}
                </div>
              </div>
            )}

            {(!!results!.cultural_sources.length || !!results!.market_sources?.length) && (
              <details className="mt-8 text-[12.5px] leading-relaxed text-ink-faint">
                <summary className="cursor-pointer font-semibold transition hover:text-grape-600">
                  Websites we read to research your brand and these creators
                </summary>
                <p className="mt-2">
                  Before searching YouTube, we read about your brand, its competitors and
                  what your buyers watch, and checked each creator's reputation outside
                  YouTube. These are the sites that research drew on:
                </p>
                <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
                  {[...(results!.market_sources ?? []), ...results!.cultural_sources]
                    .filter((v, i, arr) => arr.indexOf(v) === i)
                    .slice(0, 16)
                    .map((d) => (
                      <li key={d}>
                        <a href={`https://${d}`} target="_blank" rel="noreferrer"
                           className="underline decoration-dotted underline-offset-2 hover:text-grape-600">
                          {d}
                        </a>
                      </li>
                    ))}
                </ul>
              </details>
            )}
          </section>
        )}

        <details className="mt-10 text-[13px]">
          <summary className="cursor-pointer font-semibold text-ink-faint transition hover:text-grape-600">
            What this data can and cannot tell you
          </summary>
          <p className="mt-2.5 max-w-[70ch] leading-relaxed muted">
            Geography is <b>country-level only</b>, YouTube exposes no state or city data.
            <b> No audience demographics exist</b> publicly. Where a creator's audience is
            described (age, life stage, language), it is <b>inferred from their videos and
            viewer comments</b>, labelled as such, and quoted from the evidence; it is never
            measured data. Engagement is a <b>proxy for attention, not predicted ROI</b>.
            Sponsorship disclosure is <b>self-declared and under-reported</b>, so absence of
            evidence is not evidence a creator takes no deals.
          </p>
        </details>
      </main>
    </div>
  );
}
