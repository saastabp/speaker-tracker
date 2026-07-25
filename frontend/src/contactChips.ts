/** Colour-code warmth chips: warm → terracotta, lukewarm → gold, cold → muted grey. */
export function warmthColor(shortName: string | null): string {
  if (shortName === 'warm') return 'terracotta';
  if (shortName === 'lukewarm') return 'gold';
  return 'gray';
}