/** A circular score gauge: the number a marketer scans first. */
export default function ScoreRing({ value, size = 58 }: { value: number; size?: number }) {
  const r = (size - 7) / 2;
  const circ = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, value)) / 100;

  const tone =
    value >= 75 ? { from: "#10b981", to: "#059669", text: "text-mint-600" }
    : value >= 55 ? { from: "#7c5cff", to: "#6838f5", text: "text-grape-600" }
    : value >= 40 ? { from: "#f5a524", to: "#d98a10", text: "text-sun-600" }
    : { from: "#8b93a9", to: "#565e78", text: "text-ink-faint" };

  const id = `g${Math.round(value * 100)}`;
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <defs>
          <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" stopColor={tone.from} />
            <stop offset="100%" stopColor={tone.to} />
          </linearGradient>
        </defs>
        <circle cx={size/2} cy={size/2} r={r} fill="none" strokeWidth="5"
                className="stroke-line dark:stroke-white/10" />
        <circle cx={size/2} cy={size/2} r={r} fill="none" strokeWidth="5"
                stroke={`url(#${id})`} strokeLinecap="round"
                strokeDasharray={circ} strokeDashoffset={circ * (1 - pct)}
                style={{ transition: "stroke-dashoffset .8s cubic-bezier(.22,.7,.3,1)" }} />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">
        <span className={`font-display text-[1.15rem] font-bold tabular-nums ${tone.text}`}>
          {Math.round(value)}
        </span>
      </div>
    </div>
  );
}
