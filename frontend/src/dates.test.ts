import { describe, expect, it } from 'vitest';
import {
  daysSince,
  dateTime,
  isOverdue,
  longDate,
  parseDateLocal,
  parseTimestamp,
  shortDate,
  timestampDate,
  timestampDateTime,
  timestampShortDate,
} from './dates';

// Assertions are written to hold in **any** timezone, because that is the actual contract — the
// bug these guard against was a bare date parsed as UTC, which only misbehaves in some zones and
// so would pass a test pinned to the machine's own.

/** A bare date's calendar parts, which must survive parsing unchanged. */
function parts(d: Date) {
  return { year: d.getFullYear(), month: d.getMonth(), day: d.getDate() };
}

describe('parseDateLocal', () => {
  it('keeps the calendar day it was given, in every timezone', () => {
    expect(parts(parseDateLocal('2026-07-24'))).toEqual({ year: 2026, month: 6, day: 24 });
  });

  it('does not roll a January 1st back into the previous year', () => {
    // The regression that shipped: `new Date('2026-01-01')` is UTC midnight, which is Dec 31 2025
    // in Hawaiʻi — so History filed the gig under the wrong year and its year pill hid it.
    expect(parseDateLocal('2026-01-01').getFullYear()).toBe(2026);
  });

  it('lands at local midnight, not at some offset into the day', () => {
    const d = parseDateLocal('2026-07-24');
    expect([d.getHours(), d.getMinutes(), d.getSeconds()]).toEqual([0, 0, 0]);
  });
});

describe('parseTimestamp', () => {
  it('parses an instant', () => {
    expect(parseTimestamp('2026-07-24T18:30:00Z')?.getTime()).toBe(
      Date.UTC(2026, 6, 24, 18, 30, 0),
    );
  });

  it('returns null rather than an Invalid Date', () => {
    expect(parseTimestamp('not a date')).toBeNull();
    expect(parseTimestamp('')).toBeNull();
  });
});

describe('isOverdue', () => {
  const iso = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

  it('is false for today — due today is not yet overdue', () => {
    expect(isOverdue(iso(new Date()))).toBe(false);
  });

  it('is true for yesterday and false for tomorrow', () => {
    const shift = (days: number) => {
      const d = new Date();
      d.setDate(d.getDate() + days);
      return iso(d);
    };
    expect(isOverdue(shift(-1))).toBe(true);
    expect(isOverdue(shift(1))).toBe(false);
  });
});

describe('daysSince', () => {
  it('counts whole days back', () => {
    const d = new Date();
    d.setDate(d.getDate() - 5);
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    expect(daysSince(iso)).toBe(5);
  });

  it('never goes negative for a future date', () => {
    const d = new Date();
    d.setDate(d.getDate() + 10);
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
    expect(daysSince(iso)).toBe(0);
  });
});

describe('formatters', () => {
  const day = new Date(2026, 6, 24, 13, 45);

  it('longDate carries the year, shortDate does not', () => {
    expect(longDate(day)).toContain('2026');
    expect(shortDate(day)).not.toContain('2026');
    expect(shortDate(day)).toContain('24');
  });

  it('dateTime includes a time component', () => {
    expect(dateTime(day).length).toBeGreaterThan(longDate(day).length - 4);
  });
});

describe('timestamp wrappers', () => {
  it('fall back to the raw value so a malformed date looks wrong rather than vanishing', () => {
    expect(timestampDate('nonsense')).toBe('nonsense');
    expect(timestampDateTime('nonsense')).toBe('nonsense');
  });

  it('render an em dash for an absent timestamp', () => {
    expect(timestampShortDate(null)).toBe('—');
    expect(timestampShortDate('nonsense')).toBe('—');
  });

  it('format a real instant', () => {
    expect(timestampDate('2026-07-24T18:30:00Z')).toContain('2026');
  });
});