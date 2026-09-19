import { useEffect, useRef, useState } from "react";

/**
 * One sentence with fill-in slots, not a form.
 *
 * A list of labelled fields reads like paperwork and invites terse one-word
 * answers. A sentence invites a sentence, and the agents reason far better
 * from prose that explains WHO the buyer is than from "Audience: Gen Z".
 */
const TEMPLATE =
  "[Brand name] sells [product] in [country]. " +
  "We want to reach [who the buyer is and when they use it]. " +
  "Looking for [creator size or style], avoiding [what to rule out].";

const EXAMPLES = [
  {
    emoji: "🍦",
    title: "Gelato, India",
    text: "Frost & Co sells artisanal gelato in India. " +
      "We want to reach students and young professionals who go out for " +
      "dessert with friends on weekends. Looking for creators with a young " +
      "urban following, avoiding channels aimed at families with kids.",
  },
  {
    emoji: "🍫",
    title: "Protein bars, Gen Z",
    text: "A new protein bar brand sells a 60 rupee single serve across " +
      "metro India. We want to reach students and early-career workers who " +
      "snack between classes or shifts. Looking for mid to large creators, " +
      "avoiding hardcore bodybuilding channels.",
  },
  {
    emoji: "🎧",
    title: "Earbuds, US tech",
    text: "An audio brand sells wireless earbuds under 80 dollars in the " +
      "United States. We want to reach commuters and students who research " +
      "before they buy. Looking for reviewers with engaged comment sections, " +
      "avoiding channels that only do unboxings.",
  },
];

export default function SearchPanel({
  brief, setBrief, onSearch, onDemo, busy, compact: isCompact,
}: {
  brief: string; setBrief: (s: string) => void;
  onSearch: () => void; onDemo: () => void; busy: boolean; compact?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [copied, setCopied] = useState(false);
  useEffect(() => { if (!isCompact) ref.current?.focus(); }, [isCompact]);

  function useTemplate() {
    setBrief(TEMPLATE);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
    requestAnimationFrame(() => {
      const el = ref.current;
      if (!el) return;
      el.focus();
      // Select the first [slot] so typing replaces it immediately.
      const i = TEMPLATE.indexOf("[");
      const j = TEMPLATE.indexOf("]");
      if (i >= 0 && j > i) el.setSelectionRange(i, j + 1);
    });
  }

  if (isCompact) {
    return (
      <div>
        <textarea
          ref={ref} value={brief} onChange={(e) => setBrief(e.target.value)}
          onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") onSearch(); }}
          placeholder="Search another brief…" rows={2} disabled={busy}
          className="w-full resize-none rounded-xl border border-line bg-surf px-4 py-2.5
                     text-[15px] leading-relaxed outline-none placeholder:text-ink-faint
                     disabled:opacity-60 dark:border-white/10 dark:bg-white/[.05]"
        />
        <button onClick={onSearch} disabled={busy || brief.trim().length < 3}
                className="btn btn-primary mt-2">
          {busy ? "Searching…" : "Find creators"}
        </button>
      </div>
    );
  }

  return (
    <div>
      <div className="card p-2 shadow-lift">
        <textarea
          ref={ref} value={brief} onChange={(e) => setBrief(e.target.value)}
          onKeyDown={(e) => { if ((e.metaKey || e.ctrlKey) && e.key === "Enter") onSearch(); }}
          placeholder={TEMPLATE}
          rows={7} disabled={busy}
          className="w-full resize-none bg-transparent px-4 py-3.5 font-sans text-[15px]
                     leading-relaxed outline-none placeholder:text-ink-faint
                     disabled:opacity-60"
        />
        <div className="flex flex-wrap items-center gap-2 px-2 pb-1.5">
          <button onClick={onSearch} disabled={busy || brief.trim().length < 3}
                  className="btn btn-primary">
            {busy ? "Searching…" : "Find creators"}
          </button>
          <button onClick={useTemplate} disabled={busy} className="btn">
            {copied ? "✓ Template added" : "Use the template"}
          </button>
          <span className="ml-auto hidden pr-1 text-[12px] text-ink-faint sm:inline">
            ⌘↵ to search
          </span>
        </div>
      </div>

      <div className="mt-5">
        <div className="mb-2.5 flex items-center gap-2">
          <span className="label">Or start from an example</span>
          <span className="h-px flex-1 bg-line dark:bg-white/10" />
          <button onClick={onDemo} disabled={busy}
                  className="text-[12.5px] font-semibold text-grape-600 transition
                             hover:underline disabled:opacity-50 dark:text-grape-300">
            Preview sample results →
          </button>
        </div>

        <div className="grid gap-2 sm:grid-cols-3">
          {EXAMPLES.map((ex) => (
            <button key={ex.title} onClick={() => setBrief(ex.text)} disabled={busy}
                    className="group rounded-xl border border-line bg-surf p-3 text-left
                               transition-all hover:-translate-y-0.5 hover:border-grape-500
                               hover:shadow-card disabled:opacity-50
                               dark:border-white/10 dark:bg-white/[.04]">
              <div className="mb-1 text-lg leading-none">{ex.emoji}</div>
              <div className="text-[13px] font-semibold leading-snug
                              group-hover:text-grape-600 dark:group-hover:text-grape-300">
                {ex.title}
              </div>
              <div className="mt-1 line-clamp-2 text-[12px] leading-snug muted">
                {ex.text.split(". ")[0]}.
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
