// Shared display helpers for pipeline stage + payment chips and fee formatting — used by the board
// (Pipeline) and the opportunity detail header so their colours/format stay in lockstep.

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

/** Format a decimal fee string as currency; null when there is no fee. */
export function formatMoney(fee: string | null | undefined, currency?: string): string | null {
  if (!fee) return null;
  const amount = Number(fee);
  const cur = currency || 'USD';
  if (Number.isNaN(amount)) return `${cur} ${fee}`;
  try {
    return new Intl.NumberFormat(undefined, { style: 'currency', currency: cur }).format(amount);
  } catch {
    return `${cur} ${amount.toFixed(2)}`;
  }
}