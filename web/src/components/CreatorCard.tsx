import { useState } from "react";
import Avatar from "./Avatar";
import ScoreRing from "./ScoreRing";
import type { BreakdownTerm, Creator, FitLevel, KeyNumber } from "../types";
import { compact, pct, titleCase, FAME_LABEL, GEN_LABEL, MATCH_LABEL } from "../lib/format";

/** Research text arrives without terminal punctuation; join it into prose. */
function dot(s?: string): string {
  const t = (s ?? "").trim();
  if (!t) return "";
  return /[.!?]$/.test(t) ? t : t + ".";
}

const TONES: Record<string, string> = {
  mint:  "bg-mint-50 text-mint-700 dark:bg-mint-500/15 dark:text-mint-500",
  grape: "bg-grape-50 text-grape-700 dark:bg-grape-500/15 dark:text-grape-300",
  coral: "bg-coral-50 text-coral-600 dark:bg-coral-500/15 dark:text-coral-500",
  sun:   "bg-sun-50 text-sun-600 dark:bg-sun-500/15 dark:text-sun-500",
  sky:   "bg-sky-50 text-sky-600 dark:bg-sky-500/15 dark:text-sky-500",
  grey:  "bg-surf-raised text-ink-soft dark:bg-white/10 dark:text-slate-300",
};

function Pill({ tone = "grey", children }: { tone?: string; children: React.ReactNode }) {
  return <span className={`pill ${TONES[tone]}`}>{children}</span>;
}

/** One supporting number: the value, what it measures, and what it means. */
function Fact({ k }: { k: KeyNumber }) {
  return (
    <div className="rounded-xl bg-surf-raised px-3 py-2 dark:bg-white/[.05]">
      <div className="label !text-[10.5px]">{k.label}</div>
      <div className="font-display text-[14.5px] font-bold leading-snug tabular-nums">{k.value}</div>
      {k.note && <div className="mt-0.5 text-[11.5px] leading-snug text-ink-faint">{k.note}</div>}
    </div>
  );
}

/** How many supporting numbers show before "Why this score" is opened. */
const FACTS_UP_FRONT = 6;

const TERM_LABEL: Record<string, string> = {
  audience_match: "Audience match", product_fit: "Product fit", brand_fit: "Brand fit",
  relevance: "Relevance", engagement_rate: "Engagement", reach: "Typical views",
  view_per_sub: "Views per sub", size_fit: "Right size", sponsor_ratio: "Sponsor history",
  sponsor_confidence: "Open to deals", consistency: "Steady views", momentum: "Momentum",
  geo_fit: "In your market", activity: "Posting lately",
};

const FIT_TONE: Record<FitLevel, string> = { match: "mint", neutral: "grey", clash: "coral" };
const FIT_WORD: Record<FitLevel, string> = { match: "fits", neutral: "neutral", clash: "clashes" };
const PRODUCT_WORD: Record<string, string> = {
  natural: "Natural fit for the product",
  plausible: "Product could fit in",
  forced: "Product would feel forced",
  none: "No natural place for the product",
};
const FIELD_WORD: Record<string, string> = {
  age_bands: "Age", life_stages: "Life stage", interests: "Interests",
  languages: "Language", region: "Country", spending_power: "Spending",
};
const GROUP_LABEL: Record<string, string> = {
  relevance: "Fit with this brand",
  quality: "Channel strength",
};

function Bars({ rows, total }: { rows: BreakdownTerm[]; total: number }) {
  return (
    <div className="flex flex-col gap-2">
      {rows.map((b) => (
        <div key={b.term} className="grid grid-cols-[112px_1fr_34px] items-center gap-2.5">
          <span className="truncate text-right text-[12px] muted">{TERM_LABEL[b.term] ?? b.term.replace(/_/g, " ")}</span>
          <span className="h-2 overflow-hidden rounded-full bg-surf-raised dark:bg-white/[.07]">
            <i className="block h-full rounded-full bg-gradient-to-r from-grape-500 to-grape-600"
               style={{ width: `${Math.min(100, (b.contribution / Math.max(1e-9, total)) * 100)}%` }} />
          </span>
          <span className="text-right text-[12px] font-semibold tabular-nums text-ink-faint">
            {b.contribution.toFixed(1)}
          </span>
        </div>
      ))}
    </div>
  );
}

export default function CreatorCard({ rank, c }: { rank: number; c: Creator }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const m = c.metrics, a = c.agent, cul = c.cultural;

  const rd = c.relevance_detail ?? {};
  const days = c.trending_days ?? 0;
  const facts = c.key_numbers ?? [];
  const shorts = m.format === "shorts";
  const bonus = (c.cultural_bonus ?? 0) + (c.popularity_bonus ?? (c.trending ? 5 : 0));
  const base = Math.max(0, Math.min(100, c.fit_score) - bonus);

  // The single strongest reason to look at this creator, surfaced up front.
  const hook =
    rd.audience_level === "strong" ? "Their audience is your buyer"
    : rd.product_fit === "natural" ? PRODUCT_WORD.natural
    : cul?.evidence_found && cul.fame_tier && cul.fame_tier !== "unknown" ? FAME_LABEL[cul.fame_tier]
    : m.paid_placement_hits > 0 ? `${m.paid_placement_hits} declared paid promo${m.paid_placement_hits > 1 ? "s" : ""}`
    : m.engagement_rate && m.engagement_rate > 0.05 ? "High engagement"
    : null;

  async function copyPitch() {
    const lines = [
      c.title,
      c.url,
      `Fit score ${c.fit_score.toFixed(0)}/100 (${c.confidence} confidence)`,
      `${m.subscribers_hidden ? "Subscribers hidden" : compact(m.subscriber_count) + " subscribers"} · ${compact(m.median_views)} median views · ${pct(m.engagement_rate)} engagement`,
      c.headline ? `\n${c.headline}.` : "",
      c.rationale ?? "",
      c.caveat ? `Watch out: ${c.caveat}` : "",
      facts.length ? "\n" + facts.map((k) => `${k.label}: ${k.value}${k.note ? ` (${k.note})` : ""}`).join("\n") : "",
    ].filter(Boolean);
    try {
      await navigator.clipboard.writeText(lines.join("\n"));
      setCopied(true); setTimeout(() => setCopied(false), 1800);
    } catch { /* clipboard blocked */ }
  }

  return (
    <div className="animate-rise">
      <div className={`card overflow-hidden ${open ? "rounded-b-none" : ""} hover:shadow-lift`}>
        {/* rank stripe */}
        <div className="flex items-stretch">
          <div className={`w-1.5 shrink-0 ${
            rank === 1 ? "bg-gradient-to-b from-sun-500 to-coral-500"
            : rank <= 3 ? "bg-gradient-to-b from-grape-500 to-grape-700"
            : "bg-line dark:bg-white/10"}`} />

          <div className="min-w-0 flex-1 p-4 sm:p-5">
            <div className="flex items-start gap-3.5">
              <Avatar name={c.title} id={c.channel_id} />

              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="label shrink-0">#{rank}</span>
                  <h3 className="truncate font-display text-[1.12rem] font-bold tracking-tight">{c.title}</h3>
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[13px] muted tabular-nums">
                  <span>{m.subscribers_hidden ? "Subs hidden" : `${compact(m.subscriber_count)} subs`}</span>
                  <span className="text-line-strong dark:text-white/20">•</span>
                  <span>{compact(m.median_views)} views per {shorts ? "Short" : "video"}</span>
                  {m.engagement_rate !== null && (
                    <>
                      <span className="text-line-strong dark:text-white/20">•</span>
                      <span className="font-semibold text-mint-600 dark:text-mint-500">{pct(m.engagement_rate)} engagement</span>
                    </>
                  )}
                </div>
              </div>

              <ScoreRing value={c.fit_score} />
            </div>

            {/* score composition */}
            <div className="mt-3.5 flex h-[6px] gap-[3px] overflow-hidden rounded-full">
              <i style={{ flex: base }} className="block rounded-l-full bg-gradient-to-r from-grape-500 to-grape-600" />
              {bonus > 0 && <i style={{ flex: bonus }} className="block bg-mint-500" title={`+${bonus.toFixed(1)} bonus`} />}
              <i style={{ flex: Math.max(0.01, 100 - c.fit_score) }} className="block rounded-r-full bg-line dark:bg-white/10" />
            </div>

            <div className="mt-3 flex flex-wrap gap-1.5">
              {hook && <Pill tone="mint">★ {hook}</Pill>}
              {cul?.evidence_found && cul.audience_generation && GEN_LABEL[cul.audience_generation] && (
                <Pill tone="grape">{GEN_LABEL[cul.audience_generation]}</Pill>
              )}
              {c.archetype && <Pill tone="sky">{c.archetype}</Pill>}
              {(c.trending || days > 0) && (
                <Pill tone="coral">🔥 Trending{days > 1 ? ` · ${days} of the last 14 days` : ""}</Pill>
              )}
              {shorts && <Pill tone="grape">Shorts creator</Pill>}
              {c.tier === "broader" && (
                <span title="Not a direct match for the product, but a popular creator whose viewers overlap your buyers.">
                  <Pill tone="sky">Broader fit</Pill>
                </span>
              )}
              {(c.tier === "limited_data" || (c.confidence === "low" && c.tier !== "broader")) && (
                <span title="Judged on only a few recent videos, so treat the numbers as early signals.">
                  <Pill tone="sun">Limited data</Pill>
                </span>
              )}
              {a.safety_flag && (
                <Pill tone={a.safety_severity === "high" ? "coral" : "sun"}>
                  ⚠ Safety: {a.safety_severity}
                </Pill>
              )}
              {c.partial && <Pill tone="grey">Partial data</Pill>}
              {c.below_floor && <Pill tone="sun">Below reach bar</Pill>}
            </div>

            {/* why this creator, and the numbers behind it */}
            {c.rationale && (
              <div className="mt-3.5">
                <p className="text-[14px] leading-relaxed muted">
                  {c.headline && (
                    <span className="font-semibold text-ink dark:text-slate-100">{dot(c.headline)} </span>
                  )}
                  {c.rationale}
                </p>
                {c.caveat && (
                  <p className="mt-1.5 text-[13px] leading-relaxed text-sun-600 dark:text-sun-500">
                    Watch out: {c.caveat}
                  </p>
                )}
              </div>
            )}
            {!!facts.length && (
              <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
                {facts.slice(0, FACTS_UP_FRONT).map((k) => <Fact key={k.label} k={k} />)}
              </div>
            )}

            {cul?.evidence_found && (
              <div className="mt-3.5 rounded-xl bg-gradient-to-br from-grape-50 to-transparent p-3.5
                              dark:from-grape-500/10 dark:to-transparent">
                <div className="mb-1.5 flex items-center justify-between gap-2">
                  <span className="label !text-grape-600 dark:!text-grape-300">
                    Known beyond YouTube · {titleCase(cul.sustained_or_spike ?? "")}
                  </span>
                  <span className="pill bg-white/70 text-grape-700 dark:bg-white/10 dark:text-grape-300">
                    +{(c.cultural_bonus ?? 0).toFixed(1)} pts
                  </span>
                </div>
                <p className="text-[13.5px] leading-relaxed muted">
                  {cul.persona && <span className="font-semibold text-ink dark:text-slate-100">{dot(cul.persona)} </span>}
                  {dot(cul.notable_context)} {dot(cul.brand_fit_note)}
                </p>
              </div>
            )}

            {/* actions: what a marketer does next */}
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <a href={c.url} target="_blank" rel="noreferrer"
                 className="inline-flex items-center gap-1.5 rounded-lg bg-ink px-3 py-1.5 text-[13px]
                            font-semibold text-white transition hover:bg-grape-600
                            dark:bg-white/10 dark:hover:bg-grape-600">
                Open channel ↗
              </a>
              <button onClick={copyPitch}
                      className="rounded-lg border border-line px-3 py-1.5 text-[13px] font-semibold
                                 muted transition hover:border-grape-500 hover:text-grape-600
                                 dark:border-white/10">
                {copied ? "✓ Copied" : "Copy for deck"}
              </button>
              <button onClick={() => setOpen(!open)}
                      className="ml-auto rounded-lg px-2.5 py-1.5 text-[13px] font-semibold text-ink-faint
                                 transition hover:text-grape-600">
                {open ? "Hide" : "Why this score"} <span className={`inline-block transition ${open ? "rotate-90" : ""}`}>›</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      {open && (
        <div className="card animate-pop rounded-t-none border-t-0 p-5">
          {facts.length > FACTS_UP_FRONT && (
            <div className="mb-5 grid grid-cols-2 gap-2 sm:grid-cols-4">
              {facts.slice(FACTS_UP_FRONT).map((k) => <Fact key={k.label} k={k} />)}
            </div>
          )}

          <div className="grid gap-6 md:grid-cols-2">
            <div>
              <div className="label mb-3">How the {c.fit_score.toFixed(0)} was earned</div>
              {(["relevance", "quality"] as const).map((grp) => {
                const rows = c.breakdown.filter((b) => (b.group ?? "quality") === grp);
                if (!rows.length) return null;
                const sum = rows.reduce((t, b) => t + b.contribution, 0);
                return (
                  <div key={grp} className="mb-4">
                    <div className="mb-2 flex items-baseline justify-between text-[12px]">
                      <span className="font-semibold">{GROUP_LABEL[grp]}</span>
                      <span className="tabular-nums text-ink-faint">{sum.toFixed(1)} pts</span>
                    </div>
                    <Bars rows={rows} total={c.fit_score} />
                  </div>
                );
              })}
              {bonus > 0 && (
                <p className="text-[12px] text-ink-faint">
                  Plus {bonus.toFixed(1)} bonus for popularity and standing outside YouTube.
                </p>
              )}
              {c.relevance_gate !== undefined && c.relevance_gate < 0.999 && (
                <p className="mt-1 text-[12px] leading-relaxed text-ink-faint">
                  Fit with the brand scaled this score to {(c.relevance_gate * 100).toFixed(0)}%:
                  reach and engagement count fully only for creators who fit.
                </p>
              )}
              {!!c.dropped_terms?.length && (
                <p className="mt-3 text-[12px] leading-relaxed text-ink-faint">
                  Could not be measured here, so the weight was redistributed:{" "}
                  {c.dropped_terms.map((t) => t.replace(/_/g, " ")).join(", ")}.
                </p>
              )}
            </div>

            <div>
              <div className="label mb-3">Evidence</div>
              <div className="flex flex-col gap-3.5 text-[13px] leading-relaxed">
                {rd.product_fit && (
                  <div>
                    <span className="label block mb-1">{PRODUCT_WORD[rd.product_fit] ?? rd.product_fit}</span>
                    {!!rd.product_moments?.length && (
                      <span className="muted">
                        Where it fits: {rd.product_moments.map((t) => `“${t}”`).join(", ")}
                      </span>
                    )}
                  </div>
                )}
                {!!rd.brand_fit && Object.keys(rd.brand_fit).length > 0 && (
                  <div>
                    <span className="label block mb-1.5">Brand fit</span>
                    <div className="flex flex-wrap gap-1.5">
                      {(["tone", "price_tier", "values"] as const).map((k) => {
                        const lv = rd.brand_fit?.[k];
                        return lv ? (
                          <Pill key={k} tone={FIT_TONE[lv]}>{k.replace("_", " ")} {FIT_WORD[lv]}</Pill>
                        ) : null;
                      })}
                    </div>
                    {rd.brand_evidence && <span className="mt-1.5 block muted">{rd.brand_evidence}</span>}
                  </div>
                )}
                {!!rd.audience_fields && Object.keys(rd.audience_fields).length > 0 && (
                  <div>
                    <span className="label block mb-1.5">Audience vs your buyer</span>
                    <div className="flex flex-wrap gap-1.5">
                      {Object.entries(rd.audience_fields).map(([k, f]) => (
                        <Pill key={k} tone={f.similarity >= 0.66 ? "mint" : f.similarity >= 0.33 ? "sun" : "coral"}>
                          {FIELD_WORD[k] ?? k} {f.similarity >= 0.66 ? "✓" : f.similarity >= 0.33 ? "~" : "✗"}
                        </Pill>
                      ))}
                    </div>
                    {!!rd.audience_evidence?.length && (
                      <span className="mt-1.5 block muted">
                        {rd.audience_evidence.slice(0, 2).map((t) => `“${t}”`).join(" · ")}
                      </span>
                    )}
                  </div>
                )}
                {a.relevance_reason && (
                  <div>
                    <span className="label block mb-1">{MATCH_LABEL[a.match_type ?? ""] ?? a.match_type}</span>
                    <span className="muted">{a.relevance_reason}</span>
                  </div>
                )}
                {a.inferred_viewer_profile && (
                  <div>
                    <span className="label block mb-1">Likely viewers · {a.purchase_intent_signal} intent</span>
                    <span className="muted">
                      {a.inferred_viewer_profile}{" "}
                      <em className="not-italic text-ink-faint">(inferred from content and comments, not measured)</em>
                    </span>
                  </div>
                )}
                <div>
                  <span className="label block mb-1">Sponsorships</span>
                  <span className="muted">
                    {a.observed_format && a.observed_format !== "none_observed"
                      ? <>{a.observed_format.replace(/_/g," ")}, {a.cadence}. “{a.sponsor_evidence}”</>
                      : "None observed in the sampled videos. Disclosure is self-declared, so this is not evidence they decline deals."}
                  </span>
                </div>
                {a.safety_flag && (
                  <div>
                    <span className="label block mb-1">Safety · {a.safety_severity} · {a.safety_category}</span>
                    <span className="muted">{a.safety_reason}</span>
                  </div>
                )}
              </div>
            </div>
          </div>

          {!!c.top_videos?.length && (
            <div className="mt-6">
              <div className="label mb-2">Recent long-form videos</div>
              <div className="divide-y divide-line dark:divide-white/10">
                {c.top_videos.map((v) => (
                  <a key={v.video_id} href={v.url} target="_blank" rel="noreferrer"
                     className="flex items-baseline gap-3 py-2 text-[13px] transition hover:text-grape-600">
                    <span className="min-w-0 flex-1 truncate">{v.title}</span>
                    {v.paid_placement && <Pill tone="mint">paid</Pill>}
                    <span className="shrink-0 text-[12px] tabular-nums text-ink-faint">{compact(v.views)} views</span>
                  </a>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
