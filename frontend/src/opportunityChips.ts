// Shared display helpers for pipeline stage + payment chips — used by the board (Pipeline) and the
// opportunity detail header so their colours stay in lockstep. Fee formatting lives in `format.ts`
// (`formatMoney`): it is a value formatter rather than a chip, and filing it here is what let a
// second copy grow on the Dashboard unnoticed.

/** Stage marker dot colour — the mockup's cool→warm→good progression across the funnel. */
export const STAGE_DOT: Record<string, string> = {
  researching: 'var(--mantine-color-gray-5)',
  outreach_sent: 'var(--mantine-color-terracotta-6)',
  in_conversation: 'var(--mantine-color-terracotta-6)',
  pitched: 'var(--mantine-color-gold-6)',
  booked: 'var(--mantine-color-gold-6)',
  delivered: 'var(--mantine-color-good-6)',
};

/** Mantine Badge colour for a pipeline stage (same progression as the board dots). */
const STAGE_BADGE: Record<string, string> = {
  researching: 'gray',
  outreach_sent: 'terracotta',
  in_conversation: 'terracotta',
  pitched: 'gold',
  booked: 'gold',
  delivered: 'good',
};

/** Mantine Badge colour token for a stage short_name (defaults muted). */
export function stageColor(shortName: string): string {
  return STAGE_BADGE[shortName] ?? 'gray';
}

/** Payment-status chip colour: settled → green, billed-unpaid → amber, otherwise muted. */
export function paymentColor(shortName: string, settled: boolean): string {
  if (settled) return 'good';
  if (shortName === 'invoiced' || shortName === 'partial') return 'warn';
  return 'gray';
}