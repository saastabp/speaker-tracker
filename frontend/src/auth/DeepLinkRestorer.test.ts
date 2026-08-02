import { describe, expect, it } from 'vitest';
import { safeReturnTo } from './DeepLinkRestorer';

describe('safeReturnTo', () => {
  it('keeps an ordinary in-app deep link', () => {
    expect(safeReturnTo('/contacts/42')).toBe('/contacts/42');
    expect(safeReturnTo('/pipeline?reached=booked&closed=all')).toBe(
      '/pipeline?reached=booked&closed=all',
    );
  });

  it('rejects protocol-relative paths that leave the origin', () => {
    // The attack: a link to `https://app/\/evil.com` gives a pathname the router resolves as
    // protocol-relative, so the victim lands on evil.com the instant they finish signing in.
    // These are the exact `window.location.pathname` values the URL parser produces for
    // `https://app/\evil.com`, `https://app//evil.com` and `https://app/\/evil.com` — the browser
    // rewrites the backslash itself, so the poisoned value is already stored looking harmless.
    // Each resolves to https://evil.com/ when handed to the router.
    expect(safeReturnTo('//evil.com')).toBeNull();
    expect(safeReturnTo('///evil.com')).toBeNull();
    expect(safeReturnTo('/\\evil.com')).toBeNull();
    expect(safeReturnTo('//evil.com/contacts')).toBeNull();
  });

  it('rejects anything that is not an absolute path', () => {
    expect(safeReturnTo('https://evil.com')).toBeNull();
    expect(safeReturnTo('javascript:alert(1)')).toBeNull();
    expect(safeReturnTo('contacts/42')).toBeNull();
    expect(safeReturnTo('')).toBeNull();
    expect(safeReturnTo(null)).toBeNull();
  });
});