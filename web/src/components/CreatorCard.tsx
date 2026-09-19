import { useState } from "react";
import Avatar from "./Avatar";
import ScoreRing from "./ScoreRing";
import type { Creator } from "../types";
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

function Metric({ label, value, hint, dim }: { label: string; value: string; hint?: string; dim?: boolean }) {
  return (
    <div className="rounded-xl bg-surf-raised px-3 py-2.5 dark:bg-white/[.05]" title={hint}>
      <div className="label mb-1">{label}</div>
      <div className={`font-display text-lg font-bold tabular-nums ${dim ? "text-ink-faint" : ""}`}>{value}</div>
    </div>
  );
}

export default function CreatorCard({ rank, c }: { rank: number; c: Creator }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const m = c.metrics, a = c.agent, cul = c.cultural;

  const bonus = (c.cultural_bonus ?? 0) + (c.trending ? 5 : 0);
  const base = Math.max(0, Math.min(100, c.fit_score) - bonus);

  // The single strongest reason to look at this creator, surfaced up front.
  const hook =
    cul?.evidence_found && cul.fame_tier && cul.fame_tier !== "unknown" ? FAME_LABEL[cul.fame_tier]
    : m.paid_placement_hits > 0 ? `${m.paid_placement_hits} declared paid promo${m.paid_placement_hits > 1 ? "s" : ""}`
    : m.engagement_rate && m.engagement_rate > 0.05 ? "High engagement"
    : null;

  async function copyPitch() {
    const lines = [
      c.title,
      c.url,
      `Fit score ${c.fit_score.toFixed(0)}/100 (${c.confidence} confidence)`,
      `${m.subscribers_hidden ? "Subscribers hidden" : compact(m.subscriber_count) + " subscribers"} · ${compact(m.median_views)} median views · ${pct(m.engagement_rate)} engagement`,
      c.rationale_status === "verified" && c.rationale ? `\n${c.rationale}` : "",
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
                  <span>{compact(m.median_views)} median views</span>
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
              {c.trending && <Pill tone="coral">🔥 Trending</Pill>}
              {c.confidence === "low" && <Pill tone="sun">Low confidence</Pill>}
              {a.safety_flag && (
                <Pill tone={a.safety_severity === "high" ? "coral" : "sun"}>
                  ⚠ Safety: {a.safety_severity}
                </Pill>
              )}
              {c.partial && <Pill tone="grey">Partial data</Pill>}
              {c.below_floor && <Pill tone="sun">Below reach bar</Pill>}
            </div>

            {/* the pitch */}
            {c.rationale_status === "verified" && c.rationale && (
              <p className="mt-3.5 text-[14px] leading-relaxed muted">
                <span className="font-semibold text-ink dark:text-slate-100">{c.headline}. </span>
                {c.rationale}
              </p>
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
          {c.rationale_status === "withheld_unverified" && (
            <div className="mb-4 rounded-xl border border-dashed border-sun-500/50 bg-sun-50 p-3.5
                            text-[13px] leading-relaxed dark:bg-sun-500/10">
              <b>Rationale withheld.</b> The auditor could not verify every claim in it, so it was
              dropped rather than shown. The numbers below are unaffected.
            </div>
          )}

          <div className="mb-5 grid grid-cols-2 gap-2.5 sm:grid-cols-4">
            <Metric label="Median views" value={compact(m.median_views)} hint="Median of recent long-form videos, never the mean." />
            <Metric label="Engagement" value={pct(m.engagement_rate)} dim={m.engagement_rate === null} hint="Median (likes + comments) / views." />
            <Metric label="Views / sub" value={m.view_per_sub === null ? "-" : m.view_per_sub.toFixed(2)} dim={m.view_per_sub === null} hint="Dropped when the subscriber count is hidden." />
            <Metric label="Consistency" value={m.consistency === null ? "-" : `${(m.consistency * 100).toFixed(0)}%`} dim={m.consistency === null} />
          </div>

          <div className="grid gap-6 md:grid-cols-2">
            <div>
              <div className="label mb-3">How the {c.fit_score.toFixed(0)} was earned</div>
              <div className="flex flex-col gap-2">
                {c.breakdown.map((b) => (
                  <div key={b.term} className="grid grid-cols-[92px_1fr_34px] items-center gap-2.5">
                    <span className="truncate text-right text-[12px] muted">{b.term.replace(/_/g, " ")}</span>
                    <span className="h-2 overflow-hidden rounded-full bg-surf-raised dark:bg-white/[.07]">
                      <i className="block h-full rounded-full bg-gradient-to-r from-grape-500 to-grape-600"
                         style={{ width: `${Math.min(100, (b.contribution / Math.max(1e-9, c.fit_score)) * 100)}%` }} />
                    </span>
                    <span className="text-right text-[12px] font-semibold tabular-nums text-ink-faint">
                      {b.contribution.toFixed(1)}
                    </span>
                  </div>
                ))}
              </div>
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
                      <em className="not-italic text-ink-faint">(inferred from content, not measured)</em>
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
