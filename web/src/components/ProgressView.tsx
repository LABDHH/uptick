import type { Progress } from "../types";

import { PHASES, activePhase, percentDone } from "../lib/phases";

export default function ProgressView({ p }: { p: Progress | null }) {
  const done = p?.done ?? 0;
  const pctDone = percentDone(done, p?.total ?? 13);
  const active = activePhase(done);

  return (
    <div className="card animate-rise overflow-hidden p-0">
      <div className="h-1 bg-surf-raised dark:bg-white/10">
        <div className="h-full bg-gradient-to-r from-grape-500 to-coral-500 transition-all duration-700 ease-out"
             style={{ width: `${pctDone}%` }} />
      </div>

      <div className="p-6">
        <div className="flex items-center gap-3">
          <span className="relative flex h-2.5 w-2.5 shrink-0">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-grape-500 opacity-60" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-grape-500" />
          </span>
          <h3 className="font-display text-[1.1rem] font-bold">
            {PHASES[active].label}…
          </h3>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1.5 text-[13px]">
          {PHASES.map((ph, i) => (
            <span key={ph.label} className="flex items-center gap-2">
              <span className={
                i < active ? "font-medium text-mint-600 dark:text-mint-500"
                : i === active ? "font-semibold text-ink dark:text-slate-100"
                : "text-ink-faint/60"
              }>
                {i < active ? "✓ " : ""}{ph.label}
              </span>
              {i < PHASES.length - 1 && (
                <span className="text-line-strong dark:text-white/20">→</span>
              )}
            </span>
          ))}
        </div>

        <p className="mt-4 text-[12.5px] leading-relaxed muted">
          This usually takes about a minute. You can leave the tab open.
        </p>
      </div>
    </div>
  );
}
