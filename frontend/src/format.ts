// Value formatting that is not date formatting (that lives in `dates.ts`) and not domain colour
// mapping (that lives in the `*Chips.ts` modules).
//
// `formatMoney` moved here from `opportunityChips.ts`: it is a plain value formatter rather than a
// chip, and the Dashboard had grown a second, subtly different copy because the shared one was
// filed under a name that did not suggest it.

/**
 * Format a decimal-string amount as currency, or null when there is no amount.
 *
 * Money crosses the wire as a string so no precision is lost to a float; `Intl` needs a number, so
 * the conversion happens here in one place. A value that will not parse is shown with its currency
 * rather than swallowed, and an unknown currency code falls back instead of throwing — `Intl`
 * raises a RangeError on a code it does not recognise, which would otherwise blank a whole panel.
 */
export function formatMoney(
  fee: string | null | undefined,
  currency?: string,
): string | null {
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

/** Format a byte count as "512 B" / "34 KB" / "1.2 MB"; empty string when absent or zero. */
export function formatBytes(bytes: number | null | undefined): string {
  if (!bytes) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}