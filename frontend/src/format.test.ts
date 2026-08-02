import { describe, expect, it } from 'vitest';
import { formatBytes, formatMoney } from './format';

describe('formatMoney', () => {
  it('returns null only for an absent amount, not for zero', () => {
    expect(formatMoney(null)).toBeNull();
    expect(formatMoney(undefined)).toBeNull();
    expect(formatMoney('')).toBeNull();
    // "0" is a real total and must format as currency; the Dashboard renders it directly.
    expect(formatMoney('0', 'USD')).toContain('0');
  });

  it('formats a decimal string without losing the cents', () => {
    expect(formatMoney('1234.50', 'USD')).toContain('1,234.5');
  });

  it('defaults to USD when no currency is given', () => {
    expect(formatMoney('10')).toBe(formatMoney('10', 'USD'));
  });

  it('shows an unparseable amount with its currency rather than swallowing it', () => {
    expect(formatMoney('abc', 'USD')).toBe('USD abc');
  });

  it('falls back instead of throwing on an unknown currency code', () => {
    // Intl raises a RangeError on a code it does not know; the Dashboard's old private copy had
    // no guard, so one bad code would have blanked the whole revenue panel.
    expect(() => formatMoney('10', 'NOTACURRENCY')).not.toThrow();
    expect(formatMoney('10', 'NOTACURRENCY')).toBe('NOTACURRENCY 10.00');
  });
});

describe('formatBytes', () => {
  it('is empty for absent or zero', () => {
    expect(formatBytes(null)).toBe('');
    expect(formatBytes(undefined)).toBe('');
    expect(formatBytes(0)).toBe('');
  });

  it('scales across the unit boundaries', () => {
    expect(formatBytes(512)).toBe('512 B');
    expect(formatBytes(1024)).toBe('1 KB');
    expect(formatBytes(1024 * 1024 - 1)).toBe('1024 KB');
    expect(formatBytes(1024 * 1024)).toBe('1.0 MB');
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB');
  });
});