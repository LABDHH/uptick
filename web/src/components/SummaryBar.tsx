import type { Results } from "../types";

/** What a marketer wants to know at a glance before reading any card. */
export default function SummaryBar({ r }: { r: Results }) {
  const ranked = r.ranked;
  if (!ranked.length) return null;

  const avg = ranked.reduce((s, c) => s + c.fit_score, 0) / ranked.length;
  const withPromo = ranked.filter((c) => c.metrics.paid_placement_hits > 0).length;
  const known = ranked.filter((c) => c.cultural?.evidence_found).length;
  const strong = ranked.filter((c) => c.fit_score >= 70).length;

  const tiles = [
    { label: "Creators", value: String(ranked.length), tone: "text-ink dark:text-slate-100" },
    { label: "Strong fits", value: String(strong), tone: "text-mint-600" },
    { label: "Proven sponsors", value: String(withPromo), tone: "text-grape-600 dark:text-grape-300" },
    { label: "Known offline", value: String(known), tone: "text-coral-500" },
    { label: "Avg score", value: avg.toFixed(0), tone: "text-ink dark:text-slate-100" },
  ];

  return (
    <div className="card grid grid-cols-2 gap-px overflow-hidden bg-line p-0 sm:grid-cols-5 dark:bg-white/10">
      {tiles.map((t) => (
        <div key={t.label} className="bg-surf px-4 py-3.5 dark:bg-[#12172a]">
          <div className="label mb-1">{t.label}</div>
          <div className={`font-display text-[1.45rem] font-bold tabular-nums leading-none ${t.tone}`}>
            {t.value}
          </div>
        </div>
      ))}
    </div>
  );
}
