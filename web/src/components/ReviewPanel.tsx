import type { Results } from "../types";

/**
 * Verdict labels point FORWARD.
 *
 * "This brief needs rethinking" reads as a dead end and is usually wrong:
 * even a thin list contains the best creators that exist for this brief in
 * this market. The label names the next move instead of grading the attempt.
 */
const VERDICT = {
  ship: { label: "Strong shortlist", tone: "mint", icon: "✓" },
  ship_with_caveat: { label: "Worth a look, with caveats", tone: "sun", icon: "!" },
  weak: { label: "Early leads, worth widening the brief", tone: "grape", icon: "→" },
} as const;

const TONES: Record<string, string> = {
  mint:  "border-l-mint-500 bg-mint-50/60 dark:bg-mint-500/10",
  sun:   "border-l-sun-500 bg-sun-50/70 dark:bg-sun-500/10",
  grape: "border-l-grape-500 bg-grape-50/60 dark:bg-grape-500/10",
  coral: "border-l-coral-500 bg-coral-50/70 dark:bg-coral-500/10",
};
const BADGE: Record<string, string> = {
  mint:  "bg-mint-500 text-white",
  sun:   "bg-sun-500 text-white",
  grape: "bg-grape-500 text-white",
  coral: "bg-coral-500 text-white",
};

/**
 * The reviewer judges the shortlist as a whole. Every other agent looks at one
 * creator, so nothing else in the system can notice that the entire result is
 * thin or one-note.
 */
export default function ReviewPanel({ r }: { r: Results }) {
  const rev = r.review;
  if (!rev?.verdict) return null;
  const v = VERDICT[rev.verdict] ?? VERDICT.ship_with_caveat;

  return (
    <div className={`rounded-xl2 border border-line border-l-[3px] p-5 dark:border-white/10 ${TONES[v.tone]}`}>
      <div className="mb-3 flex flex-wrap items-center gap-2.5">
        <span className={`flex h-6 w-6 items-center justify-center rounded-full text-[12px] font-bold ${BADGE[v.tone]}`}>
          {v.icon}
        </span>
        <span className="font-display text-[1.05rem] font-bold">{v.label}</span>
      </div>

      {rev.headline && (
        <p className="mb-2 text-[14.5px] font-semibold leading-snug">{rev.headline}</p>
      )}
      {rev.what_we_found && (
        <p className="text-[13.5px] leading-relaxed muted">{rev.what_we_found}</p>
      )}
      {rev.how_to_use_this && (
        <p className="mt-2 text-[13.5px] leading-relaxed muted">
          <b className="text-ink dark:text-slate-100">How to use this: </b>
          {rev.how_to_use_this}
        </p>
      )}

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        {!!rev.gaps?.length && (
          <div>
            <div className="label mb-1.5">What would strengthen this</div>
            <ul className="space-y-1 text-[13px] leading-relaxed muted">
              {rev.gaps.map((g) => <li key={g}>• {g}</li>)}
            </ul>
          </div>
        )}
        {!!rev.suggested_refinements?.length && (
          <div>
            <div className="label mb-1.5">Try next</div>
            <ul className="space-y-1 text-[13px] leading-relaxed muted">
              {rev.suggested_refinements.map((x) => <li key={x}>• {x}</li>)}
            </ul>
          </div>
        )}
      </div>

      {rev.diversity_note && (
        <p className="mt-3 border-t border-line pt-3 text-[12.5px] leading-relaxed text-ink-faint dark:border-white/10">
          {rev.diversity_note}
        </p>
      )}
    </div>
  );
}
