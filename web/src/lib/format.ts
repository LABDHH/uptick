export function compact(n: number | string | null | undefined): string {
  if (n === null || n === undefined || n === "") return "-";
  const v = typeof n === "string" ? Number(n) : n;
  if (!Number.isFinite(v)) return "-";
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(1)}K`;
  return String(Math.round(v));
}

export function pct(x: number | null | undefined, digits = 2): string {
  return x === null || x === undefined ? "-" : `${(x * 100).toFixed(digits)}%`;
}

export function titleCase(s: string): string {
  return s.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export const FAME_LABEL: Record<string, string> = {
  household_name: "Household name",
  scene_famous: "Famous in their scene",
  niche_known: "Known in their niche",
};

export const GEN_LABEL: Record<string, string> = {
  gen_z: "Gen Z audience",
  millennial: "Millennial audience",
  mixed: "Mixed ages",
  older: "Older audience",
};

export const MATCH_LABEL: Record<string, string> = {
  direct: "Direct match",
  adjacent: "Adjacent niche",
  lifestyle: "Lifestyle overlap",
  weak: "Weak fit",
};

export const CONF_LABEL: Record<string, string> = {
  high: "High confidence",
  medium: "Medium confidence",
  low: "Low confidence",
};
