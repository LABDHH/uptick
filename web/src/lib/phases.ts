/**
 * The backend runs up to nineteen graph nodes, but node names like "dossier" and
 * "audit" are engineering internals. A marketer waiting for a shortlist does
 * not need them, and listing them all makes the wait read like a system status
 * page. They collapse into five plain phases.
 *
 * Kept separate from the component so the mapping can be tested directly.
 */
export const PHASES = [
  { label: "Understanding your brief", upto: 2 },
  { label: "Researching the market", upto: 4 },
  { label: "Finding creators", upto: 8 },
  { label: "Studying each creator", upto: 14 },
  { label: "Ranking the shortlist", upto: 19 },
] as const;

export function activePhase(done: number): number {
  const i = PHASES.findIndex((ph) => done < ph.upto);
  return i === -1 ? PHASES.length - 1 : i;
}

export function percentDone(done: number, total = 19): number {
  return Math.max(5, Math.min(100, Math.round((done / Math.max(1, total)) * 100)));
}
