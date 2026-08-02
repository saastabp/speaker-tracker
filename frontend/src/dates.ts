// Date parsing and formatting for the whole SPA. Extracted after `parseDateLocal` / `startOfToday`
// had been copied verbatim into three modules, the overdue comparison into four, and three
// near-identical `formatDate`s had grown in the pages.
//
// **Parsing and formatting are deliberately separate functions.** The one real bug this file
// replaces came from a single `formatDate(iso)` that guessed how to parse its argument: given a
// bare `YYYY-MM-DD` it used `new Date()`, read the value as UTC midnight, and rendered the
// previous day in Hawaiʻi. Splitting them forces every call site to say which kind of value it
// holds — a bare calendar date or a timestamp — which is the decision that was being made by
// accident.
//
// No date library: the app has none installed, and these are the only operations it needs.
// Formatting stays on `Intl`, which follows the viewer's locale.

/**
 * Parse a bare `YYYY-MM-DD` as a **local** date.
 *
 * `new Date(iso)` reads that form as UTC midnight, which renders as the *previous day* in a
 * negative-offset zone like Kauaʻi — and files a Jan 1 event under the previous year.
 */
export function parseDateLocal(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number);
  return new Date(y, m - 1, d);
}

/** Parse a **timestamp** (an instant, not a calendar day), or null if it is unparseable. */
export function parseTimestamp(iso: string): Date | null {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Midnight today, for comparing a calendar date without a time component getting in the way. */
export function startOfToday(): Date {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

/** Whether a bare `YYYY-MM-DD` falls before today — the shared definition of "overdue". */
export function isOverdue(iso: string): boolean {
  return parseDateLocal(iso) < startOfToday();
}

/** Whole days between a bare `YYYY-MM-DD` and today, for age chips. Never negative. */
export function daysSince(iso: string): number {
  const then = parseDateLocal(iso);
  return Math.max(0, Math.round((startOfToday().getTime() - then.getTime()) / 86_400_000));
}

/** "Jul 9" — the compact form the list columns use. */
export function shortDate(d: Date): string {
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

/** "Jul 9, 2026" — a full calendar date. */
export function longDate(d: Date): string {
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

/** Locale date *and* time, for an instant rather than a day. */
export function dateTime(d: Date): string {
  return d.toLocaleString();
}

// The `timestamp*` wrappers below take an ISO **timestamp** string and fall back to showing the
// raw value when it will not parse — a malformed value should look wrong rather than vanish. The
// name carries the safety property: reach for one of these only when the field is an instant. A
// bare `YYYY-MM-DD` must go through `parseDateLocal` instead, or it shifts a day.

/** "Jul 9, 2026" from a timestamp, or the raw value if unparseable. */
export function timestampDate(iso: string): string {
  const d = parseTimestamp(iso);
  return d ? longDate(d) : iso;
}

/** Locale date and time from a timestamp, or the raw value if unparseable. */
export function timestampDateTime(iso: string): string {
  const d = parseTimestamp(iso);
  return d ? dateTime(d) : iso;
}

/** "Jul 9" from a nullable timestamp; an em dash when absent or unparseable. */
export function timestampShortDate(iso: string | null): string {
  const d = iso ? parseTimestamp(iso) : null;
  return d ? shortDate(d) : '—';
}