const PALETTE = [
  "from-grape-500 to-grape-700",
  "from-coral-500 to-coral-600",
  "from-mint-500 to-mint-700",
  "from-sky-500 to-sky-600",
  "from-sun-500 to-sun-600",
];

/** Deterministic colour per channel, so the same creator always looks the same. */
export default function Avatar({ name, id, size = 42 }: { name: string; id: string; size?: number }) {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  const grad = PALETTE[h % PALETTE.length];
  const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();

  return (
    <div style={{ width: size, height: size }}
         className={`flex shrink-0 items-center justify-center rounded-xl bg-gradient-to-br ${grad}
                     font-display text-sm font-bold text-white shadow-sm`}>
      {initials || "?"}
    </div>
  );
}
