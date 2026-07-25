/** Colour-code venue type chips with the brand palette (podcasts warm, networks/resorts green). */
export function orgTypeColor(shortName: string): string {
  if (shortName === 'podcast') return 'terracotta';
  if (shortName === 'resort' || shortName === 'network') return 'good';
  return 'gray';
}